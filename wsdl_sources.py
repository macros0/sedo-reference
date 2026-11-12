"""Archive published WSDL/XSD dependencies without mixing SFR contours."""
import copy
import io
import json
import posixpath
import re
import zipfile
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from xml.dom import minidom
from xml.etree import ElementTree as ET

XS = "{http://www.w3.org/2001/XMLSchema}"
WSDL = "{http://schemas.xmlsoap.org/wsdl/}"
HOSTS = {"prod": ("ecp.sfr.gov.ru", "sedo.fss.ru"),
         "test": ("ecp-test.sfr.gov.ru", "ecp-test-sedo.sfr.gov.ru", "docs-test.fss.ru")}
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_FILES = 48


def discover(entries, environment):
    hosts = HOSTS[environment]
    seeds = set()
    for row in entries:
        u = urlsplit(row["url"])
        if (row["environment"] == environment and u.scheme == "https" and
                u.hostname in hosts and not u.username and not u.password and
                u.port in (None, 443) and "wsdl" in parse_qs(u.query, keep_blank_values=True)):
            seeds.add(row["url"])
    return sorted(seeds, key=lambda url: (hosts.index(urlsplit(url).hostname),
                                         "Simple" not in urlsplit(url).path, url))


def _allowed(url, seed):
    u, origin = urlsplit(url), urlsplit(seed)
    folder = posixpath.dirname(origin.path) + "/"
    path = posixpath.normpath(unquote(u.path))
    if (u.scheme != "https" or u.hostname != origin.hostname or
            u.port != origin.port or u.username or u.password or u.fragment or
            not path.startswith(folder)):
        raise ValueError("WSDL dependency outside approved HTTPS endpoint: " + url)
    query = parse_qs(u.query, keep_blank_values=True)
    if not (set(query) <= {"wsdl", "xsd"} and
            (query or path.lower().endswith((".xsd", ".wsdl")))):
        raise ValueError("Unsupported WSDL dependency URL: " + url)


def _inline_schemas(raw):
    """Retain in-scope QName prefixes when a schema is embedded in WSDL."""
    doc = minidom.parseString(raw)
    result = []
    try:
        for schema in doc.getElementsByTagNameNS(XS[1:-1], "schema"):
            if not schema.getAttribute("targetNamespace"):
                continue
            clone = schema.cloneNode(deep=True)
            namespaces = {}
            parent = schema
            while parent is not None:
                if parent.nodeType == parent.ELEMENT_NODE:
                    for key in parent.attributes.keys():
                        if key == "xmlns" or key.startswith("xmlns:"):
                            namespaces.setdefault(key, parent.getAttribute(key))
                parent = parent.parentNode
            for key, value in namespaces.items():
                clone.setAttribute(key, value)
            result.append(clone.toxml(encoding="utf8"))
    finally:
        doc.unlink()
    return result


def _archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as z:
        for name, content in sorted(files.items()):
            z.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), content)
    return output.getvalue()


def collect(seed, environment, fetch, blob):
    from sync import inspect_file
    if seed not in discover([dict(environment=environment, url=seed)], environment):
        raise ValueError("Unapproved WSDL source")
    pending = [(seed, None)]
    visited = {}
    files = {}
    sources = []
    total = 0
    while pending:
        url, expected = pending.pop(0)
        _allowed(url, seed)
        if url in visited:
            if expected and visited[url] != expected:
                raise ValueError("Imported namespace mismatch: " + url)
            continue
        if len(visited) >= MAX_FILES:
            raise ValueError("WSDL dependency file limit exceeded")
        raw = fetch(url)
        total += len(raw)
        if len(raw) > MAX_FILE or total > MAX_TOTAL:
            raise ValueError("WSDL dependency size limit exceeded")
        if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
            raise ValueError("DTD/entity is not supported in WSDL/XSD")
        root = ET.fromstring(raw)
        kind = "xsd" if root.tag == XS + "schema" else "wsdl" if root.tag == WSDL + "definitions" else None
        if not kind:
            raise ValueError("Expected WSDL definitions or XSD schema: " + url)
        namespace = root.get("targetNamespace")
        if expected and expected != namespace:
            raise ValueError("Imported namespace mismatch: " + url)
        visited[url] = namespace
        source = dict(url=url, sha256=blob(raw), bytes=len(raw), kind=kind, namespace=namespace)
        sources.append(source)
        if kind == "xsd":
            query = parse_qs(urlsplit(url).query)
            name = posixpath.basename(query.get("xsd", [urlsplit(url).path])[0])
            if not re.fullmatch(r"[\w. -]+\.xsd", name, re.I):
                raise ValueError("Unsupported schema filename: " + name)
            files[f"schemas/{len(sources):03d}/" + name] = raw
        else:
            for i, content in enumerate(_inline_schemas(raw)):
                files[f"schemas/{len(sources):03d}/inline-{i}.xsd"] = content
        for element in root.iter():
            if element.tag not in (XS + "import", XS + "include", WSDL + "import"):
                continue
            location = element.get("schemaLocation") or element.get("location")
            if not location:
                raise ValueError("WSDL/XSD import without location: " + url)
            target = urljoin(url, location)
            _allowed(target, seed)
            pending.append((target, element.get("namespace") if element.tag.endswith("import") else None))
    if not files:
        raise ValueError("WSDL contains no XSD schemas")
    content = _archive(files)
    return dict(environment=environment, url=seed + "#xsd-bundle", kind="zip",
                label="XSD по WSDL шлюза СФР", archive_name="wsdl-schemas.zip",
                source_type="wsdl", source_url=seed, source_files=sources,
                sha256=blob(content), bytes=len(content), **inspect_file(content, "zip"))


def bridge(document, support, read_object):
    """Combine an unchanged document schema with one same-contour WSDL closure."""
    if document["environment"] != support["environment"]:
        return []
    from sync import inspect_file, sha
    result = []
    with zipfile.ZipFile(io.BytesIO(read_object(support["sha256"]))) as z:
        dependencies = {"wsdl/" + name: z.read(name) for name in z.namelist()}
    seen = set()
    for archive in document.get("embedded_archives", []):
        if archive.get("container", "").endswith("#combined"):
            continue
        with zipfile.ZipFile(io.BytesIO(read_object(archive["sha256"]))) as z:
            own = {"document/" + name: z.read(name) for name in z.namelist()}
        # A namespace supplied by the document must not be shadowed by a library.
        own_namespaces = {ET.fromstring(v).get("targetNamespace") for v in own.values()}
        files = {n: v for n, v in dependencies.items() if ET.fromstring(v).get("targetNamespace") not in own_namespaces}
        files.update(own)
        content = _archive(files)
        digest = sha(content)
        if digest in seen:
            continue
        seen.add(digest)
        result.append(dict(environment=document["environment"], kind="zip", source_type="wsdl_bridge",
                           url=document["url"] + "#wsdl=" + support["sha256"] + "&schema=" + archive["sha256"],
                           label="Схема DOCX и зависимости WSDL", archive_name="module-wsdl-schemas.zip",
                           sha256=digest, bytes=len(content), content=content,
                           source_url=support["source_url"], source_files=copy.deepcopy(support["source_files"]),
                           document_container=archive.get("container",""), document_archive_sha256=archive["sha256"], **inspect_file(content, "zip")))
    return result


def probe():
    """Read-only live check, also run from GitHub before Pages deployment."""
    from sync import SITES, Links, get, sha
    checks=[]
    for environment,page in SITES.items():
        attempts=[]
        try:
            parser=Links();parser.feed(get(page,timeout=12,attempts=1).decode("utf-8-sig"))
            entries=[dict(environment=environment,url=urljoin(page,href)) for href,label in parser.items]
            seeds=discover(entries,environment)
            for seed in seeds:
                try:
                    row=collect(seed,environment,lambda url:get(url,timeout=10,attempts=1),sha)
                    checks.append(dict(environment=environment,status="ok",source=seed,files=len(row["source_files"]),attempts=attempts))
                    break
                except Exception as exc:attempts.append(dict(source=seed,error=str(exc)))
            else:checks.append(dict(environment=environment,status="unavailable",attempts=attempts))
        except Exception as exc:checks.append(dict(environment=environment,status="unavailable",error=str(exc),attempts=attempts))
    return checks


if __name__=="__main__":
    import argparse,os
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe",action="store_true",help="Check published endpoints without editing the reference")
    args=parser.parse_args()
    if args.probe:
        report=json.dumps(probe(),ensure_ascii=False,indent=2)
        print(report)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"],"a",encoding="utf8") as stream:
                stream.write("\n## WSDL source checks\n\n```json\n"+report+"\n```\n")
    else:parser.print_help()
