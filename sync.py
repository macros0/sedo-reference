"""Download a versioned SEDO source inventory. Python 3.10+, standard library."""
import argparse
import collections
import concurrent.futures
import hashlib
import io
import json
import re
import sys
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

def get(url):
    u=urlsplit(url)
    safe=urlunsplit((u.scheme,u.netloc,quote(unquote(u.path),safe="/"),u.query,""))
    for attempt in range(3):
        try:
            with urlopen(Request(safe,headers={"User-Agent":"SEDO-reference/1.0"}),timeout=30) as r:
                b=r.read(MAX_BYTES+1)
                if len(b)>MAX_BYTES: raise ValueError("file exceeds limit")
                return b
        except OSError:
            if attempt==2: raise
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
        if not any(r["environment"]==env and r["kind"]=="zip" for r in tasks): raise ValueError(env+": отсутствуют ожидаемые архивы")
    def fetch(r):
        b=get(r["url"]); return b,inspect_file(b,r["kind"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(fetch,r):r for r in tasks}
        for fut in concurrent.futures.as_completed(futures):
            r=futures[fut]
            try:
                b,meta=fut.result();r.update(sha256=blob(b),bytes=len(b),**meta)
            except Exception as e:
                r["error"]=str(e);errors.append([r["url"],str(e)])
    result=dict(format=1,pages=pages,entries=entries)
    payload=encode(result);digest=sha(payload);write(base/"snapshots"/(digest+".json"),payload)
    pointer=base/"current.json"
    previous=json.loads(pointer.read_bytes()) if pointer.exists() else {}
    old={}
    if previous:
        prior=json.loads((base/"snapshots"/(previous["snapshot"]+".json")).read_bytes())
        old={(r["environment"],r["url"]):r for r in prior["entries"]}
    new={(r["environment"],r["url"]):r for r in entries}
    diff={key:[] for key in ("added","removed","changed")}
    for key in sorted(old.keys()|new.keys()):
        if old.get(key)!=new.get(key): diff["removed" if key not in new else "added" if key not in old else "changed"].append(list(key))
    report=dict(observed_at=datetime.now(timezone.utc).isoformat(),snapshot=digest,previous=previous.get("snapshot"),files=len(tasks),errors=errors,diff=diff)
    write(base/"last-run.json",encode(report))
    if errors: raise ValueError(f"{len(errors)} file errors, see last-run.json")
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

