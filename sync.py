"""Download a versioned SEDO source inventory. Python 3.10+, standard library."""
import argparse
import collections
import concurrent.futures
import hashlib
import io
from itertools import islice
import json
import posixpath
import re
import sys
import ssl
import time
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit, quote, unquote
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from xml.etree import ElementTree as ET

SITES = {"prod":"https://lk.sfr.gov.ru/sedo.html","test":"https://lk-test.fss.ru/sedo.html"}
CATALOG_HOSTS = {"prod": {"lk.sfr.gov.ru", "lk.fss.ru"}, "test": {"lk-test.fss.ru"}}
MAX_BYTES = 96 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 4000
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XS = "{http://www.w3.org/2001/XMLSchema}"
def sha(b): return hashlib.sha256(b).hexdigest()
def norm(s): return " ".join(s.split())
def encode(obj): return json.dumps(obj,ensure_ascii=False,indent=2).encode("utf8")
def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".partial")
    tmp.write_bytes(data)
    tmp.replace(path)
def record_observation(base, digest, observed_at):
    """Retain the first successful observation of content, even if it returns later."""
    path = base / "observations.json"
    observations = json.loads(path.read_bytes()) if path.exists() else dict(format=1, snapshots={})
    observations["snapshots"].setdefault(digest, observed_at)
    write(path, encode(observations))
class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.items=[]; self.anchor=None; self.base_href=None
    def handle_starttag(self,tag,attrs):
        if tag == "base" and self.base_href is None and dict(attrs).get("href") is not None:
            self.base_href = dict(attrs)["href"]
        if tag=="a" and dict(attrs).get("href"):
            self.anchor=[dict(attrs)["href"],[]]
    def handle_data(self,d):
        if self.anchor: self.anchor[1].append(d)
    def handle_endtag(self,t):
        if t=="a" and self.anchor:
            url,words=self.anchor
            self.items.append((url,norm("".join(words))))
            self.anchor=None
    def resolved(self, base, *, html_base=True):
        if html_base and self.base_href is not None:
            try:
                base = urljoin(base, self.base_href.strip())
            except ValueError:
                pass
        for href, label in self.items:
            try:
                url = urljoin(base, href.strip())
            except ValueError:
                continue
            yield url, label

def get(url, timeout=30, attempts=3, *, wsdl_seed=None):
    import os
    url = resource_url(url)
    context = {"wsdl_seed": wsdl_seed} if wsdl_seed is not None else {}
    if os.environ.get("SEDO_SFR_POLITE") == "1":
        return _polite_fetch(url, **context)
    return _get(url, timeout=timeout, attempts=attempts, **context)


def source_kind(url):
    return PurePosixPath(unquote(urlsplit(url).path)).suffix.lower().lstrip('.')


def resource_url(url):
    """A browser fragment identifies a source link, never an HTTP resource."""
    return urlsplit(url)._replace(fragment="").geturl()

def catalog_resource(url, environment):
    """Resolve a published HTTPS alias inside its own catalog contour."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme == "https" and parts.hostname in CATALOG_HOSTS[environment]
            and port in (None, 443) and not parts.username and not parts.password):
        return parts._replace(netloc=urlsplit(SITES[environment]).netloc, fragment="").geturl()

def request_path(path):
    # Preserve existing URI octets. Encode literal '%' and text from HTML IRIs.
    path = re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", path)
    return quote(path, safe="/%:@!$&'()*+,;=-._~")

def download_key(row):
    parts = urlsplit(row.get("download_url") or resource_url(row["url"]))
    path = request_path(parts.path)
    path = re.sub(r"%[0-9a-fA-F]{2}", lambda match: match[0].upper(), path)
    query = request_query(parts.query)
    query = re.sub(r"%[0-9a-fA-F]{2}", lambda match: match[0].upper(), query)
    return parts._replace(path=path, query=query).geturl()


def request_query(query):
    query = re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", query)
    return quote(query, safe="/?:@!$&'()*+,;=%-._~")


class RedirectedBytes(bytes):
    """Downloaded bytes with their effective base URI after a checked redirect."""
    def __new__(cls, raw, final_url):
        result = super().__new__(cls, raw)
        result.final_url = final_url
        return result


def source_url(url, seed):
    """Validate the actual next request before any network operation."""
    from wsdl_sources import HOSTS, _allowed
    u, origin = urlsplit(url), urlsplit(seed)
    if (u.scheme != 'https' or u.hostname != origin.hostname or
            u.port not in (None, 443) or u.username or u.password or u.fragment):
        raise ValueError('Source outside approved HTTPS endpoint: ' + url)
    if origin.hostname in {host for hosts in HOSTS.values() for host in hosts}:
        _allowed(url, seed)
        return
    path = posixpath.normpath(unquote(u.path))
    pages = {'/sedo.html', '/prab.html', '/mchd.html', '/eln.html'}
    if (origin.hostname not in {urlsplit(site).hostname for site in SITES.values()} or
            '\\' in path or not (path in pages or path.startswith(('/specs_sedo/', '/prab/', '/mchd/', '/eln/')))):
        raise ValueError('Source outside approved catalog path: ' + url)


class SourceRedirect(HTTPRedirectHandler):
    def __init__(self, seed):
        self.seed = seed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = resource_url(newurl)
        source_url(newurl, self.seed)
        if fp is not None:
            if len(fp.read(MAX_BYTES + 1)) > MAX_BYTES:
                raise ValueError("redirect response exceeds limit")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SourceHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        from collector_transport import before_http_request
        before_http_request()
        return super().https_open(request)


def _get(url, timeout=30, attempts=3, *, wsdl_seed=None):
    url = resource_url(url)
    seed = wsdl_seed if wsdl_seed is not None else url
    source_url(url, seed)
    u=urlsplit(url)
    safe=urlunsplit((u.scheme,u.netloc,request_path(u.path),request_query(u.query),""))
    context = ssl.create_default_context()
    if u.hostname in {urlsplit(site).hostname for site in SITES.values()} | {"ecp-test.sfr.gov.ru"}:
        bundle = Path(__file__).resolve().parent / "certs" / (u.hostname + ".pem")
        if bundle.exists():
            context.load_verify_locations(cafile=str(bundle))
    for attempt in range(attempts):
        try:
            opener = build_opener(SourceRedirect(seed), SourceHTTPSHandler(context=context))
            with opener.open(Request(safe,headers={"User-Agent":"SEDO-reference/1.0"}),timeout=timeout) as r:
                b=r.read(MAX_BYTES+1)
                if len(b)>MAX_BYTES: raise ValueError("file exceeds limit")
                headers = getattr(r, 'headers', {})
                length = headers.get('Content-Length')
                if length is not None and headers.get('Transfer-Encoding', '').lower() != 'chunked':
                    if len(b) != int(length):
                        raise OSError(f'Incomplete HTTP body: Content-Length {length}, received {len(b)}')
                if r.geturl() != safe:
                    source_url(r.geturl(), seed)
                    return RedirectedBytes(b, r.geturl())
                return b
        except OSError:
            if attempt==attempts-1: raise
            time.sleep(1+attempt)


from collector_transport import PoliteFetch
_polite_fetch = PoliteFetch(_get, max_requests=1000)

def inspect_file(b,kind,*,legacy_module_bindings=False,binding_changes=None,legacy_schema_roots=False,legacy_docx=False):
    if kind not in ("docx","zip"): return {}
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        members = z.infolist()
        if len({item.filename for item in members}) != len(members):
            raise ValueError('Duplicate archive member names')
        if len(members) > MAX_ARCHIVE_FILES or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
            raise ValueError('Expanded archive exceeds limit')
        def xml(n):
            if z.getinfo(n).file_size>MAX_BYTES: raise ValueError("XML exceeds limit")
            return ET.fromstring(z.read(n))
        if kind=="zip":
            from schema import check_dependency_bases
            result=[]
            for n in sorted(z.namelist()):
                if n.lower().endswith(".xsd"):
                    if z.getinfo(n).file_size > MAX_BYTES:
                        raise ValueError('XML exceeds limit')
                    raw = z.read(n)
                    r=ET.fromstring(raw)
                    if r.find(XS+"redefine") is not None:
                        raise ValueError("Unsupported xs:redefine in " + n)
                    if r.find(XS+"override") is not None:
                        raise ValueError("Unsupported xs:override in " + n)
                    check_dependency_bases(r, n)
                    result.append(dict(path=n,namespace=r.get("targetNamespace"),sha256=sha(raw),
                         dependencies=[e.get("schemaLocation") for e in r if e.tag in (XS+"import",XS+"include")],
                         imports=[dict(location=e.get("schemaLocation"),namespace=e.get("namespace") if e.tag==XS+"import" else r.get("targetNamespace"),**({"kind":"include"} if e.tag==XS+"include" else {})) for e in r if e.tag in (XS+"import",XS+"include")],
                         roots=[dict(name=e.get("name"),description=norm(" ".join(t for a in e.findall(XS+"annotation/"+XS+"documentation") for t in a.itertext()))) for e in r.findall(XS+"element")]))
            from schema import included_roots, _snapshot_match, archive_targets
            for item in result:
                if item['path'] in archive_targets(z): item['import_targets']=archive_targets(z)[item['path']]
            legacy, changes = _snapshot_match.get()
            if not legacy and not legacy_schema_roots:
                inventory = {item['path']: item for item in result}
                expanded = {}
                for item in result:
                    try: expanded[item['path']] = included_roots(inventory,item['path'])
                    except ValueError: continue  # Matching reports an incomplete closure.
                for item in result:
                    roots = expanded.get(item['path'],item['roots'])
                    if roots != item['roots']:
                        if changes is not None: changes.add('included_roots')
                        if binding_changes is not None: binding_changes.add('included_roots')
                        item['roots'] = roots
            return {"schemas":result}
        from document_metadata import normalized_heading, cell_text
        doc=xml("word/document.xml")
        def cell(n):
            old = norm("".join(t.text or "" for t in n.iter(WORD+"t")))
            value = old if legacy_docx else cell_text(n)
            if value != old and binding_changes is not None: binding_changes.add('docx_text')
            return value
        tables=[]
        for tb in doc.findall(".//"+WORD+"body/"+WORD+"tbl"):
            rows=[[cell(c) for c in row.findall(WORD+"tc")] for row in tb.findall(WORD+"tr")]
            if rows: tables.append(rows)
        info={}
        from document_metadata import document_history
        changes = document_history(tables)
        if changes:
            info["document_history"] = changes
        for table in tables:
            for row in table:
                if len(row)==2 and any(k in normalized_heading(row[0]) for k in ("корневой элемент","расположение файла xml-схемы","пространство имен файла xml-схемы")):
                    info.setdefault("structure",[]).append(row)
            if any("номер версии xml-схемы" in s.lower() for s in table[0]):
                info.setdefault("version_tables",[]).append(table)
            if any(re.search(r"(код|номер|тип).*сообщени|тип документа",s,re.I) for s in table[0]) and len(table[0])>=2:
                valid = len(table)>2 or (not legacy_docx and any(len(row)>=2 and row[0].isdigit() for row in table[1:]))
                if valid:
                    info.setdefault("registry_tables",[]).append(table)
                    if len(table)==2 and binding_changes is not None: binding_changes.add('docx_text')
        # Only declarations in the document body count. Tables contain XML examples
        # and TOC paragraphs can repeat headings without describing a message.
        messages = {}
        for paragraph in doc.findall(WORD+"body/"+WORD+"p"):
            style = paragraph.find(WORD+"pPr/"+WORD+"pStyle")
            if style is not None and re.search(r"toc|contents|оглав", style.get(WORD+"val", ""), re.I):
                continue
            declaration = re.fullmatch(r"Тип сообщения\s+(?:СЭДО\s*)?(?:№\s*)?(\d{1,5})\s*[:—–-]\s*(.+)", cell(paragraph), re.I)
            if declaration:
                number, title = declaration.groups()
                messages.setdefault(number, dict(number=number, title=title.strip()))
        if messages:
            info["message_types"] = list(messages.values())
            from module_xsd import document_bindings
            bindings = document_bindings(doc, legacy=legacy_module_bindings, changes=binding_changes)
            if bindings: info["module_bindings"] = bindings
        return info

def match_modules(base, entries, supports, blob, *, legacy=False, changes=None, combined_bridge=True, legacy_schema_roots=False):
    """Match documents with one explicitly selected set of contour libraries."""
    from module_xsd import match_document
    from wsdl_sources import bridge
    for r in entries:
        if not r.get("module_bindings"):continue
        read_object=lambda h:(base/"objects"/h).read_bytes()
        matches,issues=match_document(r,entries,read_object,legacy=legacy,changes=changes)
        if {b["number"] for b in r["module_bindings"]}-{m["number"] for m in matches}:
            archives=[]
            for support in supports:
                for archive in bridge(r,support,read_object,include_combined=combined_bridge,legacy_schema_roots=legacy_schema_roots):
                    if changes is not None and archive.get("document_container", "").endswith("#combined"):
                        changes.add("combined_bridge")
                    blob(archive.pop("content"));archives.append(archive)
            if archives:
                r["wsdl_archives"]=archives
                matches,issues=match_document(r,entries,read_object,legacy=legacy,changes=changes)
        if matches:r["module_matches"]=matches
        if issues:r.setdefault("schema_diagnostics",[]).extend(issues)


CONFIRMATION_FILES = ("observations.json", "history.json", "current.json", "collector/processed.json", "last-success.json")


def confirmed_time(base, digest):
    success = base / "last-success.json"
    if success.exists():
        record = json.loads(success.read_bytes())
        return record["observed_at"] if record["snapshot"] == digest else None
    # Older stores used the latest attempt as their only success marker.
    report = base / "last-run.json"
    if report.exists():
        record = json.loads(report.read_bytes())
        if record.get("snapshot") == digest and not record.get("errors") and record.get("status", "ok") == "ok":
            return record.get("observed_at")


def confirmation_lock(base):
    """Serialize library collection and export, including crash recovery."""
    from auto_update import update_lock
    # A separate lock domain avoids nesting the caller's project lock.
    return update_lock(base, filename=".sync.lock", recover=False)


def recover_confirmation(base):
    """Restore the previous confirmation after an interrupted local collection."""
    journal = base / ".sync-recovery.json"
    if not journal.exists():
        return
    saved = json.loads(journal.read_bytes())
    for name in CONFIRMATION_FILES:
        if name not in saved:  # recovery journal from a previous version
            continue
        content = saved[name]
        target = base / name
        if content is None:
            target.unlink(missing_ok=True)
        else:
            write(target, bytes.fromhex(content))
    if "attempt_started_at" in saved:
        started_at = saved["attempt_started_at"]
        path = base / "last-run.json"
        attempt = json.loads(path.read_bytes()) if path.exists() else {}
        if attempt.get("started_at") != started_at:
            attempt = dict(started_at=started_at, observed_at=started_at)
        attempt.update(status="failed", errors=attempt.get("errors") or [["collection", "Сбор прерван до подтверждения снимка"]])
        write(path, encode(attempt))
    journal.unlink()


def sync(base, *, wsdl_entries=None, snapshot_format=None, individual_wsdl=False):
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    with confirmation_lock(base):
        _collect_confirmed(base, wsdl_entries=wsdl_entries, snapshot_format=snapshot_format,
                           individual_wsdl=individual_wsdl)


def _collect_confirmed(base, *, wsdl_entries=None, snapshot_format=None, individual_wsdl=False):
    recover_confirmation(base)
    success, pointer = base / "last-success.json", base / "current.json"
    if not success.exists() and pointer.exists():
        previous = json.loads(pointer.read_bytes())["snapshot"]
        stamp = confirmed_time(base, previous)
        if stamp:
            write(success, encode(dict(snapshot=previous, observed_at=stamp)))
    saved = {name: (base/name).read_bytes().hex() if (base/name).exists() else None
             for name in CONFIRMATION_FILES}
    started_at = datetime.now(timezone.utc).isoformat()
    saved["attempt_started_at"] = started_at
    journal = base / ".sync-recovery.json"
    write(journal, encode(saved))
    report = dict(status="processing", started_at=started_at, observed_at=started_at, errors=[])
    try:
        write(base / "last-run.json", encode(report))
        _sync(base, report, wsdl_entries=wsdl_entries, snapshot_format=snapshot_format, individual_wsdl=individual_wsdl)
        write(success, encode(dict(snapshot=report["snapshot"], observed_at=report["observed_at"])))
        report["status"] = "ok"
        write(base / "last-run.json", encode(report))
        journal.unlink()
    except Exception as exc:
        recover_confirmation(base)
        report["status"] = "failed"
        if not report["errors"]:
            report["errors"].append(["collection", str(exc)])
        write(base / "last-run.json", encode(report))
        raise


def _sync(base, report, *, wsdl_entries=None, snapshot_format=None, individual_wsdl=False):
    """Collect sources, optionally replaying the recorded WSDL observations offline."""
    entries=[]; pages={}; errors=[]; tasks=[]
    # Keep the original snapshot identity when none of the new inputs is needed.
    # Explicit formats are used by verify.py to replay the original collector.
    output_format = snapshot_format or 2
    resolve_page_urls = snapshot_format is None or snapshot_format >= 3
    page_redirects = {}
    source_rules = snapshot_format is None or snapshot_format >= 6
    def page_base(url, content):
        final = getattr(content, "final_url", url) if resolve_page_urls else url
        if final != url:
            source_url(final, url)
            page_redirects[url] = final
        return final
    def blob(b):
        h=sha(b); p=base/"objects"/h
        if not p.exists() or p.stat().st_size != len(b) or p.read_bytes() != b:
            write(p,b)
        return h
    for env,url in SITES.items():
        b=get(url); pages[env]=blob(b)
        main_base = page_base(url, b)
        parser=Links();parser.feed(b.decode("utf-8-sig"))
        lookup={}
        page_links = list(parser.resolved(main_base, html_base=source_rules))
        if source_rules and page_links != list(parser.resolved(main_base, html_base=False)):
            output_format = max(output_format, 6)
        for u,label in page_links:
            parts=urlsplit(u)
            if u in lookup:
                if source_rules and label and not lookup[u]["label"]:
                    lookup[u]["label"] = label
                    output_format = max(output_format, 6)
                continue
            ext=source_kind(u)
            r=dict(environment=env,url=u,label=label,kind=ext or "link");lookup[u]=r;entries.append(r)
            download = catalog_resource(u, env)
            if download and parts.path.startswith("/specs_sedo/") and ext in ("docx","zip","pdf","crt","xsd","doc","xlsx"):
                if download != resource_url(u):
                    r["download_url"] = download
                tasks.append(r)
        # Old official aliases may be normalized onto the contour's current
        # endpoint. Never discard an unrelated or another contour's hostname.
        linked=set()
        for u,label in page_links:
            link = catalog_resource(u, env)
            if link:
                path = re.sub(r"/+", "/", urlsplit(link).path)
                if path in ("/prab.html", "/mchd.html", "/eln.html"):
                    companion = urlsplit(link)._replace(path=path, fragment="").geturl() if source_rules else urljoin(url, path)
                    linked.add((path, companion))
                    if source_rules and companion != urljoin(url, path):
                        output_format = max(output_format, 6)
        for path, companion in sorted(linked, key=lambda item: (("/prab.html", "/mchd.html", "/eln.html").index(item[0]), item[1])):
            content=get(companion)
            companion_base = page_base(companion, content)
            page_row = lookup.get(companion) if output_format >= 2 else None
            if page_row is None:
                page_row = dict(environment=env, url=companion, label=path[1:], kind="html")
                entries.append(page_row)
            page_row.update(sha256=blob(content), bytes=len(content))
            extra=Links();extra.feed(content.decode("utf-8-sig"))
            extra_links = list(extra.resolved(companion_base, html_base=source_rules))
            if source_rules and extra_links != list(extra.resolved(companion_base, html_base=False)):
                output_format = max(output_format, 6)
            for resource,label in extra_links:
                parts=urlsplit(resource)
                if not parts.path.lower().endswith(".zip"):continue
                is_schema=(path=="/prab.html" and parts.path.startswith("/prab/")) or (re.search(r"xsd|схем",label+" "+unquote(parts.path),re.I) and parts.path.startswith(("/prab/","/mchd/","/eln/")))
                if not is_schema:continue
                legacy = resource_url(resource) if parts.hostname == urlsplit(url).hostname else None
                download = catalog_resource(resource, env) if resolve_page_urls else legacy
                if download != legacy:
                    output_format = max(output_format, 3)
                if not download:continue
                row=lookup.get(resource)
                if row is None:
                    row=dict(environment=env,url=resource,label=label,kind="zip")
                    lookup[resource]=row;entries.append(row)
                if source_rules and label and not row["label"]:
                    row["label"] = label
                    output_format = max(output_format, 6)
                if "source_page" not in row:
                    row["source_page"]=companion
                    if download != resource_url(resource):
                        row["download_url"] = download
                    tasks.append(row)
        if not any(r["environment"]==env and r["kind"]=="zip" for r in tasks): raise ValueError(env+": отсутствуют ожидаемые архивы")
    # Keep every published source link, but fetch and inspect its resource once.
    task_groups = {}
    for row in tasks:
        group = task_groups.setdefault(download_key(row), [])
        if row not in group:
            group.append(row)
    tasks = [group[0] for group in task_groups.values()]
    legacy_match = snapshot_format is not None and snapshot_format < 4
    match_changes = set()
    def fetch(r):
        changes = set()
        b=get(r.get("download_url") or resource_url(r["url"]));meta=inspect_file(b,r["kind"],legacy_module_bindings=legacy_match,binding_changes=changes,legacy_schema_roots=snapshot_format is not None and snapshot_format < 7,legacy_docx=snapshot_format is not None and snapshot_format < 7);packages=[]
        if r["kind"]=="docx":
            from module_xsd import extract_archives
            packages,issues=extract_archives(b)
            if issues:meta["schema_diagnostics"]=issues
            archives=[]
            for package in packages:
                content=package.pop("content")
                package.update(sha256=sha(content),**inspect_file(content,"zip",legacy_schema_roots=snapshot_format is not None and snapshot_format < 7,binding_changes=changes))
                archives.append(package)
                package["content"]=content
            if archives:meta["embedded_archives"]=[{k:v for k,v in a.items() if k!="content"} for a in archives]
        if not source_rules:
            meta.pop("document_history", None)
        elif meta.get("document_history"):
            changes.add("document_history")
        return b,meta,packages,changes
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        pending = iter(tasks)
        futures = {pool.submit(fetch, r): r for r in islice(pending, 3)}
        while futures:
            completed, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in completed:
                r = futures.pop(fut)
                try:
                    b,meta,packages,changes=fut.result()
                    match_changes.update(changes)
                    digest = blob(b)
                    for alias in task_groups[download_key(r)]:
                        alias.update(sha256=digest,bytes=len(b),**meta)
                        if alias['kind']=='docx' and (snapshot_format is None or snapshot_format>=7) and (urlsplit(alias['url']).query or urlsplit(alias['url']).fragment):
                            match_changes.add('docx_url')
                    for package in packages:blob(package["content"])
                    if packages:
                        del package
                    del b, packages
                except ImportError:
                    raise
                except Exception as e:
                    for alias in task_groups[download_key(r)]:
                        alias["error"]=str(e)
                    errors.append([r["url"],str(e)])
            completed.clear()
            del fut
            for r in islice(pending, 3 - len(futures)):
                futures[pool.submit(fetch, r)] = r
    # Only published, explicitly classified service hosts can provide a contour's
    # transport library. A test URL on the production page remains a test URL.
    from wsdl_sources import discover, collect
    previous={}
    pointer=base/"current.json"
    if pointer.exists():
        key=json.loads(pointer.read_bytes())["snapshot"]
        previous=json.loads((base/"snapshots"/(key+".json")).read_bytes())
    supports=[]
    for env in SITES:
        if wsdl_entries is not None:
            recorded = [dict(row) for row in wsdl_entries if row["environment"] == env]
            entries.extend(recorded)
            supports.extend(row for row in recorded if row.get("source_type") == "wsdl")
            continue
        from collector_snapshots import load_library
        captured = load_library(base, env)
        if captured:
            entries.append(captured); supports.append(captured)
            continue
        for seed in discover(entries,env):
            try:
                support=collect(seed,env,lambda url:get(url,timeout=10,attempts=1,wsdl_seed=seed),blob)
                entries.append(support);supports.append(support)
                break
            except Exception as exc:
                entries.append(dict(environment=env,url=seed+"#wsdl-check",kind="wsdl_status",status="unavailable",error=str(exc)))
        else:
            # A transient service outage must not remove an earlier verified tree.
            cached=next((r for r in previous.get("entries",[]) if r.get("source_type")=="wsdl" and r["environment"]==env),None)
            if cached and (base/"objects"/cached["sha256"]).exists():
                support=dict(cached,retrieval_status="cached")
                entries.append(support);supports.append(support)
    from schema import snapshot_matching
    with snapshot_matching(legacy_match, match_changes):
        match_modules(base, entries, supports, blob, legacy=legacy_match, changes=match_changes,
                      combined_bridge=snapshot_format is None or snapshot_format >= 5,
                      legacy_schema_roots=snapshot_format is not None and snapshot_format < 7)
    if not legacy_match and match_changes:
        output_format = max(output_format, 4)
    if "combined_bridge" in match_changes:
        output_format = max(output_format, 5)
    if "document_history" in match_changes:
        output_format = max(output_format, 6)
    if {"included_roots", "docx_text", "docx_url"} & match_changes:
        output_format = max(output_format, 7)
    if snapshot_format is None or individual_wsdl:
        from catalog import record_individual_wsdl_matches
        evidence_version = {'version': individual_wsdl} if type(individual_wsdl) is int else {}
        record_individual_wsdl_matches(entries, lambda digest: (base / 'objects' / digest).read_bytes(),
                                       legacy_filenames=snapshot_format is not None and snapshot_format < 7,
                                       **evidence_version)
    result=dict(format=max(output_format, 3) if page_redirects else output_format,pages=pages,entries=entries)
    if page_redirects:
        result["page_redirects"] = page_redirects
    payload=encode(result);digest=sha(payload);write(base/"snapshots"/(digest+".json"),payload)
    pointer=base/"current.json"
    previous=json.loads(pointer.read_bytes()) if pointer.exists() else {}
    old={}
    if previous:
        prior=json.loads((base/"snapshots"/(previous["snapshot"]+".json")).read_bytes())
        old={(r["environment"],r["url"]):r for r in prior["entries"]}
    new={(r["environment"],r["url"]):r for r in json.loads(payload)["entries"]}
    diff={key:[] for key in ("added","removed","changed")}
    for key in sorted(old.keys()|new.keys()):
        if old.get(key)!=new.get(key): diff["removed" if key not in new else "added" if key not in old else "changed"].append(list(key))
    report.update(observed_at=datetime.now(timezone.utc).isoformat(),snapshot=digest,previous=previous.get("snapshot"),files=len(tasks),errors=errors,diff=diff)
    if errors: raise ValueError(f"{len(errors)} file errors, see last-run.json")
    from collector_snapshots import replay_history, selected_records
    replayed, processed = replay_history(base, result, blob) if wsdl_entries is None else ([], None)
    if processed is not None:
        report["collector_replay"] = [dict(environment=env, content_id=key, **record)
                                      for env, records in selected_records(processed).items() for key, record in records.items()]
    record_observation(base, digest, report["observed_at"])
    history_file=base/"history.json"
    history=json.loads(history_file.read_bytes()) if history_file.exists() else []
    for key in (previous.get("snapshot"), *replayed, digest):
        if key and key not in history: history.append(key)
    write(history_file,encode(history))
    # Install the active snapshot and its transition together. Synthetic WSDL
    # observations and manual submissions never move the real catalog pointer.
    transitions = previous.get("transitions", [])
    if not transitions or previous.get("snapshot") != digest:
        transitions.append(dict(snapshot=digest, observed_at=report["observed_at"]))
    write(pointer,encode(dict(snapshot=digest, transitions=transitions)))
    if processed is not None:
        write(base/"collector/processed.json",encode(processed))
    print(f"{len(entries)} references, {len(tasks)} downloaded files; {digest}")
def main():
    from auto_update import catalog_locks
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--state",type=Path,default=Path("state"))
    state=p.parse_args().state.resolve()
    with catalog_locks(state):
        sync(state)


if __name__=="__main__":
    try:main()
    except Exception as e:sys.exit(str(e))

