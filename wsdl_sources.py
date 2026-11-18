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
        if row["environment"] != environment:
            continue
        try:
            u = urlsplit(row["url"])
            port = u.port
        except ValueError:
            continue
        if (u.scheme == "https" and
                u.hostname in hosts and not u.username and not u.password and
                port in (None, 443) and "wsdl" in parse_qs(u.query, keep_blank_values=True)):
            seeds.add(u._replace(fragment="").geturl())
    return sorted(seeds, key=lambda url: (hosts.index(urlsplit(url).hostname),
                                         "Simple" not in urlsplit(url).path, url))


def _allowed(url, seed):
    u, origin = urlsplit(url), urlsplit(seed)
    origin_path = posixpath.normpath(unquote(origin.path))
    folder = (origin_path if origin.path.endswith("/") else posixpath.dirname(origin_path)).rstrip("/") + "/"
    path = posixpath.normpath(unquote(u.path))
    query = parse_qs(u.query, keep_blank_values=True)
    own_query = path == origin_path and query and set(query) <= {"wsdl", "xsd"}
    if (u.scheme != "https" or u.hostname != origin.hostname or
            (u.port or 443) != (origin.port or 443) or u.username or u.password or u.fragment or
            not (path.startswith(folder) or own_query)):
        raise ValueError("WSDL dependency outside approved HTTPS endpoint: " + url)
    if not (set(query) <= {"wsdl", "xsd"} and
            (query or path.lower().endswith((".xsd", ".wsdl")))):
        raise ValueError("Unsupported WSDL dependency URL: " + url)


def _inline_schemas(raw, legacy, *, legacy_inline_includes=False):
    """Retain in-scope QName prefixes when a schema is embedded in WSDL."""
    doc = minidom.parseString(raw)
    result = []
    try:
        for schema in doc.getElementsByTagNameNS(XS[1:-1], "schema"):
            parent = schema.parentNode
            if not legacy and not (parent.namespaceURI == WSDL[1:-1] and parent.localName == "types"
                                   and parent.parentNode is doc.documentElement):
                continue
            declarations = {"element", "attribute", "simpleType", "complexType", "group", "attributeGroup", "notation"}
            if not legacy_inline_includes:
                declarations.add("include")
            # Import-only wrappers need no archive member. Unqualified
            # declarations and includes can define an entry point.
            if not schema.getAttribute("targetNamespace") and not any(
                    child.nodeType == child.ELEMENT_NODE and child.namespaceURI == XS[1:-1]
                    and child.localName in declarations for child in schema.childNodes):
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
        from schema import write_archive_targets
        write_archive_targets(z,getattr(files,'import_targets',{}))
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            # Freeze the ZIP creator OS too: Python defaults to the host OS.
            # Match the already archived Windows bundles on Linux collectors.
            info.create_system = 0
            info.external_attr = 0o600 << 16
            z.writestr(info, content)
    return output.getvalue()


def _references(root, url, seed, legacy):
    from schema import check_dependency_bases
    if legacy:
        schemas = list(root.iter(XS + "schema"))
        edges = None
    else:
        schemas = [root] if root.tag == XS + "schema" else root.findall(WSDL + "types/" + XS + "schema")
        edges = set(root.findall(WSDL + "import")) if root.tag == WSDL + "definitions" else set()
        for schema in schemas:
            edges.update(child for child in schema if child.tag in
                         (XS + "include", XS + "import", XS + "redefine", XS + "override"))
    check_dependency_bases(root, url, edges=edges)
    includes = {edge: schema.get("targetNamespace") for schema in schemas
                for edge in schema.findall(XS + "include")}
    for element in root.iter():
        if edges is not None and element not in edges:
            continue
        if element.tag in (XS + "redefine", XS + "override"):
            raise ValueError("Unsupported xs:" + element.tag.rsplit('}', 1)[-1] + " in " + url)
        if element.tag not in (XS + "import", XS + "include", WSDL + "import"):
            continue
        location = element.get("schemaLocation") or element.get("location")
        if not location:
            raise ValueError("WSDL/XSD import without location: " + url)
        target = urljoin(url, location)
        _allowed(target, seed)
        if element.tag == XS + "include":
            yield target, "include", includes.get(element)
        else:
            yield target, "wsdl_import" if element.tag == WSDL + "import" else "import", element.get("namespace")


def _relative_archive(files, origins, requested, expanded):
    """Keep the original layout when its offline edges select the fetched schemas."""
    from schema import import_metadata, resolve_import
    from sync import sha
    roots = {name: ET.fromstring(raw) for name, raw in files.items()}
    metadata = {name: import_metadata(root, sha(files[name]))
                for name, root in roots.items()}
    targets = {url: name for name, url in requested.items()}
    contexts = {name: {metadata[name]["namespace"]} if metadata[name]["namespace"] else
                {namespace for url, namespace in expanded if url == requested[name]}
                for name in origins}

    def check_layout(paths, exact=None):
        inventory = {paths[name]: data for name, data in metadata.items()}
        if exact:
            inventory = {path: data | {'import_targets': exact.get(path,{})} for path,data in inventory.items()}
        pending = [(name, paths[name], ns) for name, namespaces in contexts.items() for ns in namespaces]
        seen = set()
        while pending:
            source, path, effective = pending.pop()
            if (source, path, effective) in seen:
                continue
            seen.add((source, path, effective))
            for edge in roots[source]:
                if edge.tag not in (XS + "import", XS + "include"):
                    continue
                location = edge.attrib["schemaLocation"]
                kind = edge.tag.rsplit("}", 1)[-1]
                namespace = effective if kind == "include" else edge.get("namespace")
                resolved = resolve_import(inventory, path, location, namespace, kind)
                target = targets[urljoin(origins[source], location)]
                if inventory[resolved]["sha256"] != metadata[target]["sha256"]:
                    raise ValueError("WSDL archive selected a different dependency: " + location)
                # Equal bytes at different paths can have different child dependencies.
                pending.append((target, resolved, metadata[target]["namespace"] or namespace))

    paths = {name: name for name in files}
    try:
        check_layout(paths)
    except ValueError:
        pass
    else:
        return files
    result = {}
    # Reserve real filenames before synthetic inline/query schema names.
    for name in sorted(files, key=lambda n: bool(urlsplit(origins[n]).query)):
        raw = files[name]
        origin = urlsplit(origins[name])
        directory = posixpath.dirname(posixpath.normpath(unquote(origin.path))).lstrip("/")
        path = posixpath.join("schemas", directory, posixpath.basename(name))
        if path in result and result[path] != raw:
            if not origin.query:
                raise ValueError("Conflicting WSDL archive paths: " + path)
            # Preserve the base directory for relative includes in this WSDL.
            path = posixpath.join("schemas", directory, name.replace("/", "-"))
            if path in result:
                raise ValueError("Conflicting WSDL archive paths: " + path)
        result[path] = raw
        paths[name] = path
    try:
        check_layout(paths)
    except ValueError:
        from schema import SchemaFiles
        exact={paths[name]: {edge.get('schemaLocation'): paths[targets[urljoin(origins[name],edge.get('schemaLocation'))]]
                            for edge in roots[name] if edge.tag in (XS+'include',XS+'import')}
               for name in files}
        exact={path: edges for path,edges in exact.items() if edges}
        check_layout(paths,exact)
        return SchemaFiles(result,import_targets=exact)
    return result


def _index_inline_entry_members(content, schemas, sources):
    """Record candidate paths that expose the same declaration via an inline entry."""
    from schema import Schema
    originals = {source["sha256"] for source in sources}
    entries = [schema for schema in schemas if not schema["namespace"]
               and schema["sha256"] not in originals and schema["roots"]]
    if not entries:
        return
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for entry in entries:
            path = entry["path"]
            root = ET.fromstring(archive.read(path))
            if (root.find(XS + "include") is None
                    or any(edge.tag not in (XS + "include", XS + "import", XS + "annotation") for edge in root)):
                continue
            members = {}
            for name in sorted({node["name"] for node in entry["roots"]}):
                try:
                    tree = Schema(archive, path, name)
                except (ValueError, KeyError):
                    continue
                declaration = tree.owner[tree.root]
                matching = []
                for schema in schemas:
                    member = schema["path"]
                    if (member == path or member not in tree.files or schema["namespace"] != entry["namespace"]
                            or not any(node["name"] == name for node in schema["roots"])):
                        continue
                    try:
                        standalone = Schema(archive, member, name)
                    except (ValueError, KeyError):
                        continue
                    if standalone.owner[standalone.root] == declaration:
                        matching.append(member)
                if matching:
                    members[name] = matching
            if members:
                entry["wsdl_entry_members"] = members


def prefer_wsdl_inline_entries(candidates, root):
    """Remove duplicate entry paths only inside their own confirmed WSDL archive."""
    covered = {(archive["url"], archive["sha256"], member)
               for archive, schema, *_ in candidates if archive.get("source_type") == "wsdl"
               for member in schema.get("wsdl_entry_members", {}).get(root, ())}
    return [candidate for candidate in candidates
            if (candidate[0]["url"], candidate[0]["sha256"], candidate[1]["path"]) not in covered]


def collect(seed, environment, fetch, blob, *, legacy_annotations=False, legacy_schema_roots=False,
            legacy_inline_includes=False):
    from schema import import_basename
    from sync import inspect_file
    if seed not in discover([dict(environment=environment, url=seed)], environment):
        raise ValueError("Unapproved WSDL source")
    pending = [(seed, None, None)]
    visited = {}
    expanded = set()
    files = {}
    origins = {}
    requested = {}
    sources = []
    total = 0
    while pending:
        url, relation, expected = pending.pop(0)
        _allowed(url, seed)
        if url not in visited:
            if len(visited) >= MAX_FILES:
                raise ValueError("WSDL dependency file limit exceeded")
            raw = fetch(url)
            final_url = getattr(raw, "final_url", url)
            _allowed(final_url, seed)
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
            visited[url] = kind, namespace, list(_references(root, final_url, seed, legacy_annotations))
            source = dict(url=url, sha256=blob(raw), bytes=len(raw), kind=kind, namespace=namespace)
            if final_url != url:
                source["final_url"] = final_url
            sources.append(source)
            if kind == "xsd":
                query = parse_qs(urlsplit(url).query)
                name = posixpath.basename(query.get("xsd", [unquote(urlsplit(url).path)])[0])
                if "xsd" in query and not name.lower().endswith(".xsd"):
                    name = import_basename(url)
                if not re.fullmatch(r"[\w. -]+\.xsd", name, re.I):
                    raise ValueError("Unsupported schema filename: " + name)
                path = f"schemas/{len(sources):03d}/" + name
                files[path] = raw
                origins[path] = final_url
                requested[path] = url
            else:
                for i, content in enumerate(_inline_schemas(raw, legacy_annotations,
                                                            legacy_inline_includes=legacy_inline_includes)):
                    path = f"schemas/{len(sources):03d}/inline-{i}.xsd"
                    files[path] = content
                    origins[path] = final_url
                    requested[path] = url
        kind, namespace, references = visited[url]
        if relation == "include" and (kind != "xsd" or namespace not in (None, expected)):
            raise ValueError("Included namespace mismatch: " + url)
        if relation == "import" and (kind != "xsd" or expected != namespace):
            raise ValueError("Imported namespace mismatch: " + url)
        if relation == "wsdl_import" and (kind != "wsdl" or expected != namespace):
            raise ValueError("Imported namespace mismatch: " + url)
        effective = expected if relation == "include" and namespace is None else namespace
        if (url, effective) in expanded:
            continue
        expanded.add((url, effective))
        for target, edge, declared in references:
            # A chameleon propagates its inherited namespace to nested includes.
            required = effective if edge == "include" and kind == "xsd" and declared is None else declared
            pending.append((target, edge, required))
    if not files:
        raise ValueError("WSDL contains no XSD schemas")
    content = _archive(_relative_archive(files, origins, requested, expanded))
    metadata = inspect_file(content, "zip", legacy_schema_roots=legacy_schema_roots)
    if not legacy_inline_includes:
        _index_inline_entry_members(content, metadata["schemas"], sources)
    return dict(environment=environment, url=seed + "#xsd-bundle", kind="zip",
                label="XSD по WSDL шлюза СФР", archive_name="wsdl-schemas.zip",
                source_type="wsdl", source_url=seed, source_files=sources,
                sha256=blob(content), bytes=len(content), **metadata)


def bridge(document, support, read_object, *, include_combined=True, legacy_schema_roots=False):
    """Combine an unchanged document schema with one same-contour WSDL closure."""
    if document["environment"] != support["environment"]:
        return []
    from sync import inspect_file, sha
    from schema import archive_targets, SchemaFiles
    def prefixed_targets(archive,prefix):
        return {prefix+source:{location:prefix+target for location,target in edges.items()}
                for source,edges in archive_targets(archive).items()}
    result = []
    with zipfile.ZipFile(io.BytesIO(read_object(support["sha256"]))) as z:
        dependencies = {"wsdl/" + name: z.read(name) for name in z.namelist()}
        support_targets=prefixed_targets(z,'wsdl/')
    seen = set()
    archives = document.get("embedded_archives", [])
    combined = [archive for archive in archives if archive.get("container", "").endswith("#combined")]
    # All document-owned dependencies take precedence over library substitutes.
    # Historical snapshots predate this combined source and must replay unchanged.
    archives = combined if include_combined and combined else [archive for archive in archives if archive not in combined]
    for archive in archives:
        with zipfile.ZipFile(io.BytesIO(read_object(archive["sha256"]))) as z:
            own = {"document/" + name: z.read(name) for name in z.namelist()}
            targets=support_targets | prefixed_targets(z,'document/')
        # A namespace supplied by the document must not be shadowed by a library.
        own_namespaces = {ET.fromstring(v).get("targetNamespace") for v in own.values()}
        files = {n: v for n, v in dependencies.items() if ET.fromstring(v).get("targetNamespace") not in own_namespaces}
        files.update(own)
        # One namespace can span several files. Restore only missing concrete
        # dependencies, keeping all declarations supplied by the document first.
        from schema import resolve_import, import_basename, import_metadata
        inventory = {name: import_metadata(ET.fromstring(raw), sha(raw))
                     for name, raw in {**dependencies, **own}.items()}
        for name,edges in targets.items(): inventory[name]['import_targets']=edges
        pending = [(name, inventory[name]["namespace"]) for name in files]
        expanded = set()
        for name, inherited_namespace in pending:
            root = ET.fromstring(files[name])
            effective_namespace = root.get("targetNamespace") or inherited_namespace
            if (name, effective_namespace) in expanded:
                continue
            expanded.add((name, effective_namespace))
            for reference in root:
                if reference.tag not in (XS + "include", XS + "import"):
                    continue
                location = reference.get("schemaLocation")
                if not location:
                    continue
                kind = reference.tag.rsplit('}', 1)[-1]
                namespace = reference.get("namespace") if kind == "import" else effective_namespace
                try:
                    target = resolve_import({n: inventory[n] for n in files}, name, location, namespace, kind)
                    pending.append((target, namespace if kind == "include" else None))
                    continue
                except ValueError:
                    pass
                try:
                    target = resolve_import(inventory, name, location, namespace, kind)
                except ValueError:
                    # Preserve conflicting candidates so the matching pass can
                    # distinguish ambiguity from a genuinely missing dependency.
                    for candidate, metadata in inventory.items():
                        if (candidate in dependencies and candidate not in files
                                and posixpath.basename(candidate) == import_basename(location)
                                and metadata["namespace"] in ({namespace, None} if kind == "include" else {namespace})):
                            files[candidate] = dependencies[candidate]
                            pending.append((candidate, namespace if kind == "include" else None))
                    continue
                if target not in files:
                    files[target] = dependencies[target]
                pending.append((target, namespace if kind == "include" else None))
        content = _archive(SchemaFiles(files,import_targets={name:edges for name,edges in targets.items() if name in files}))
        digest = sha(content)
        if digest in seen:
            continue
        seen.add(digest)
        result.append(dict(environment=document["environment"], kind="zip", source_type="wsdl_bridge",
                           url=document["url"] + "#wsdl=" + support["sha256"] + "&schema=" + archive["sha256"],
                           label="Схема DOCX и зависимости WSDL", archive_name="module-wsdl-schemas.zip",
                           sha256=digest, bytes=len(content), content=content,
                           source_url=support["source_url"], source_files=copy.deepcopy(support["source_files"]),
                           document_container=archive.get("container",""), document_archive_sha256=archive["sha256"], **inspect_file(content, "zip",legacy_schema_roots=legacy_schema_roots)))
    return result


def probe():
    """Read-only live check, also run from GitHub before Pages deployment."""
    from sync import SITES, Links, get, sha
    checks=[]
    for environment,page in SITES.items():
        attempts=[]
        try:
            content = get(page,timeout=12,attempts=1)
            parser=Links();parser.feed(content.decode("utf-8-sig"))
            base = getattr(content, "final_url", page)
            entries=[dict(environment=environment,url=url) for url,_ in parser.resolved(base)]
            seeds=discover(entries,environment)
            for seed in seeds:
                try:
                    row=collect(seed,environment,lambda url:get(url,timeout=10,attempts=1,wsdl_seed=seed),sha)
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
