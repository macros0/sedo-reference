"""Download a versioned SEDO source inventory. Python 3.10+, standard library."""
import argparse
import collections
import concurrent.futures
import hashlib
import io
import json
import re
import sys
import ssl
import time
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, quote, unquote
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

SITES = {"prod":"https://lk.sfr.gov.ru/sedo.html","test":"https://lk-test.fss.ru/sedo.html"}
MAX_BYTES = 96 * 1024 * 1024
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
        super().__init__(); self.items=[]; self.anchor=None
    def handle_starttag(self,tag,attrs):
        if tag=="a" and dict(attrs).get("href"):
            self.anchor=[dict(attrs)["href"],[]]
    def handle_data(self,d):
        if self.anchor: self.anchor[1].append(d)
    def handle_endtag(self,t):
        if t=="a" and self.anchor:
            url,words=self.anchor
            self.items.append((url,norm("".join(words))))
            self.anchor=None

def get(url, timeout=30, attempts=3):
    u=urlsplit(url)
    safe=urlunsplit((u.scheme,u.netloc,quote(unquote(u.path),safe="/"),u.query,""))
    context = ssl.create_default_context()
    if u.hostname in {urlsplit(site).hostname for site in SITES.values()}:
        bundle = Path(__file__).resolve().parent / "certs" / (u.hostname + ".pem")
        if bundle.exists():
            context.load_verify_locations(cafile=str(bundle))
    for attempt in range(attempts):
        try:
            with urlopen(Request(safe,headers={"User-Agent":"SEDO-reference/1.0"}),timeout=timeout,context=context) as r:
                b=r.read(MAX_BYTES+1)
                if len(b)>MAX_BYTES: raise ValueError("file exceeds limit")
                return b
        except OSError:
            if attempt==attempts-1: raise
            time.sleep(1+attempt)

def inspect_file(b,kind):
    if kind not in ("docx","zip"): return {}
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        def xml(n):
            if z.getinfo(n).file_size>MAX_BYTES: raise ValueError("XML exceeds limit")
            return ET.fromstring(z.read(n))
        if kind=="zip":
            result=[]
            for n in sorted(z.namelist()):
                if n.lower().endswith(".xsd"):
                    r=xml(n)
                    result.append(dict(path=n,namespace=r.get("targetNamespace"),sha256=sha(z.read(n)),
                         dependencies=[e.get("schemaLocation") for e in r if e.tag in (XS+"import",XS+"include")],
                         imports=[dict(location=e.get("schemaLocation"),namespace=e.get("namespace") if e.tag==XS+"import" else r.get("targetNamespace")) for e in r if e.tag in (XS+"import",XS+"include")],
                         roots=[dict(name=e.get("name"),description=norm(" ".join(t for a in e.findall(XS+"annotation/"+XS+"documentation") for t in a.itertext()))) for e in r.findall(XS+"element")]))
            return {"schemas":result}
        doc=xml("word/document.xml")
        def cell(n): return norm("".join(t.text or "" for t in n.iter(WORD+"t")))
        tables=[]
        for tb in doc.findall(".//"+WORD+"body/"+WORD+"tbl"):
            rows=[[cell(c) for c in row.findall(WORD+"tc")] for row in tb.findall(WORD+"tr")]
            if rows: tables.append(rows)
        info={}
        for table in tables:
            for row in table:
                if len(row)==2 and any(k in row[0].lower() for k in ("корневой элемент","расположение файла xml-схемы","пространство имен файла xml-схемы")):
                    info.setdefault("structure",[]).append(row)
            if any("номер версии xml-схемы" in s.lower() for s in table[0]):
                info.setdefault("version_tables",[]).append(table)
            if any(re.search(r"(код|номер|тип).*сообщени|тип документа",s,re.I) for s in table[0]) and len(table)>2 and len(table[0])>=2:
                info.setdefault("registry_tables",[]).append(table)
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
            bindings = document_bindings(doc)
            if bindings: info["module_bindings"] = bindings
        return info

def sync(base):
    entries=[]; pages={}; errors=[]; tasks=[]
    def blob(b):
        h=sha(b); p=base/"objects"/h
        if not p.exists(): write(p,b)
        return h
    for env,url in SITES.items():
        b=get(url); pages[env]=blob(b)
        parser=Links();parser.feed(b.decode("utf-8-sig"))
        lookup={}
        for href,label in parser.items:
            u=urljoin(url,href.strip()); parts=urlsplit(u)
            if u in lookup: continue
            ext=Path(unquote(parts.path)).suffix.lower().lstrip(".")
            r=dict(environment=env,url=u,label=label,kind=ext or "link");lookup[u]=r;entries.append(r)
            if parts.hostname==urlsplit(url).hostname and parts.path.startswith("/specs_sedo/") and ext in ("docx","zip","pdf","crt","xsd","doc","xlsx"):
                tasks.append(r)
        # Follow published schema pages, keeping their original contour host.
        linked={re.sub(r"/+","/",urlsplit(urljoin(url,href)).path) for href,label in parser.items}
        for path in ("/prab.html","/mchd.html","/eln.html"):
            if path not in linked:continue
            companion=urljoin(url,path);content=get(companion)
            entries.append(dict(environment=env,url=companion,label=path[1:],kind="html",sha256=blob(content),bytes=len(content)))
            extra=Links();extra.feed(content.decode("utf-8-sig"))
            for href,label in extra.items:
                resource=urljoin(companion,href.strip());parts=urlsplit(resource)
                if parts.hostname!=urlsplit(url).hostname or not parts.path.lower().endswith(".zip"):continue
                is_schema=(path=="/prab.html" and parts.path.startswith("/prab/")) or (re.search(r"xsd|схем",label+" "+parts.path,re.I) and parts.path.startswith(("/prab/","/mchd/","/eln/")))
                if not is_schema or resource in lookup:continue
                row=dict(environment=env,url=resource,label=label,kind="zip",source_page=companion)
                lookup[resource]=row;entries.append(row);tasks.append(row)
        if not any(r["environment"]==env and r["kind"]=="zip" for r in tasks): raise ValueError(env+": отсутствуют ожидаемые архивы")
    def fetch(r):
        b=get(r["url"]);meta=inspect_file(b,r["kind"]);packages=[]
        if r["kind"]=="docx":
            from module_xsd import extract_archives
            packages,issues=extract_archives(b)
            if issues:meta["schema_diagnostics"]=issues
            archives=[]
            for package in packages:
                content=package.pop("content")
                package.update(sha256=sha(content),**inspect_file(content,"zip"))
                archives.append(package)
                package["content"]=content
            if archives:meta["embedded_archives"]=[{k:v for k,v in a.items() if k!="content"} for a in archives]
        return b,meta,packages
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(fetch,r):r for r in tasks}
        for fut in concurrent.futures.as_completed(futures):
            r=futures[fut]
            try:
                b,meta,packages=fut.result();r.update(sha256=blob(b),bytes=len(b),**meta)
                for package in packages:blob(package["content"])
            except Exception as e:
                r["error"]=str(e);errors.append([r["url"],str(e)])
    # Only published, explicitly classified service hosts can provide a contour's
    # transport library. A test URL on the production page remains a test URL.
    from wsdl_sources import discover, collect, bridge
    previous={}
    pointer=base/"current.json"
    if pointer.exists():
        key=json.loads(pointer.read_bytes())["snapshot"]
        previous=json.loads((base/"snapshots"/(key+".json")).read_bytes())
    supports=[]
    for env in SITES:
        for seed in discover(entries,env):
            try:
                support=collect(seed,env,lambda url:get(url,timeout=10,attempts=1),blob)
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
    from module_xsd import match_document
    for r in entries:
        if not r.get("module_bindings"):continue
        read_object=lambda h:(base/"objects"/h).read_bytes()
        matches,issues=match_document(r,entries,read_object)
        if {b["number"] for b in r["module_bindings"]}-{m["number"] for m in matches}:
            archives=[]
            for support in supports:
                for archive in bridge(r,support,read_object):
                    blob(archive.pop("content"));archives.append(archive)
            if archives:
                r["wsdl_archives"]=archives
                matches,issues=match_document(r,entries,read_object)
        if matches:r["module_matches"]=matches
        if issues:r.setdefault("schema_diagnostics",[]).extend(issues)
    result=dict(format=1,pages=pages,entries=entries)
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
    report=dict(observed_at=datetime.now(timezone.utc).isoformat(),snapshot=digest,previous=previous.get("snapshot"),files=len(tasks),errors=errors,diff=diff)
    write(base/"last-run.json",encode(report))
    if errors: raise ValueError(f"{len(errors)} file errors, see last-run.json")
    record_observation(base, digest, report["observed_at"])
    history_file=base/"history.json"
    history=json.loads(history_file.read_bytes()) if history_file.exists() else []
    for key in (previous.get("snapshot"),digest):
        if key and key not in history: history.append(key)
    write(history_file,encode(history))
    write(pointer,encode({"snapshot":digest}))
    print(f"{len(entries)} references, {len(tasks)} downloaded files; {digest}")
if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--state",type=Path,default=Path("state"))
    try:sync(p.parse_args().state)
    except Exception as e:sys.exit(str(e))

