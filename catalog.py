"""Materialize a catalog from successful inventories. Does not contact SFR."""
import json
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote
from sync import encode,sha,write

def dependency_set(schemas,entry):
    files={s["path"]:s for s in schemas}
    visited={}
    def visit(path):
        if path in visited:return
        s=files[path];visited[path]=s["sha256"]
        for loc in s["dependencies"]:
            if not loc: raise ValueError("Импорт без пути в "+path)
            visit(posixpath.normpath(posixpath.join(posixpath.dirname(path),loc)))
    visit(entry)
    # Paths relative to the entry directory survive archive packaging changes.
    return sorted((posixpath.relpath(p,posixpath.dirname(entry)),h) for p,h in visited.items())

def derive(snapshot):
    rows=snapshot["entries"];catalog=[]; registry=[]; issues=[]
    for r in rows:
        for table in r.get("registry_tables",[]):
            if table[0][:2]!=["Код типа сообщения","Наименование"]:continue
            group=""
            for cells in table[1:]:
                if len(cells)==1:group=cells[0]
                elif len(cells)>=4 and cells[0].isdigit():
                    registry.append(dict(environment=r["environment"],number=cells[0],title=cells[1],description=cells[2],appendix=cells[3],module=group,source=r["url"]))
    for r in rows:
        fname=unquote(r["url"].split("/")[-1]);number=re.search(r"тип[ _]+сообщения[ _]+(\d+)",fname,re.I)
        if not number or r["kind"]!="docx":continue
        number=number[1];e=r["environment"];struct=dict(r.get("structure",[]))
        root=next((v for k,v in struct.items() if "корневой элемент" in k.lower()),None)
        ns=next((v for k,v in struct.items() if "пространство имен" in k.lower()),None)
        candidates=[(a,s) for a in rows if a["environment"]==e and a["kind"]=="zip" for s in a.get("schemas",[]) if ns==s["namespace"] and any(x["name"]==root for x in s["roots"])]
        title=next((x["title"] for x in registry if x["number"]==number and x["environment"]==e),r["label"])
        version=re.search(r"[vв](\d+(?:[._]\d+)+)",r["label"],re.I)
        date=re.search(r"(\d{2}\.\d{2}\.(?:\d{4}|\d{2}))(?!\d)",r["label"])
        d=dict(number=number,environment=e,title=title,document_version=version[1].replace("_",".") if version else None,label_date=date[1] if date else None,document_url=r["url"],document_sha256=r["sha256"],root=root,namespace=ns,version_tables=r.get("version_tables",[]),status="review")
        if len(candidates)==1:
            a,s=candidates[0]
            try:
                deps=dependency_set(a["schemas"],s["path"])
                root_text=next(x["description"] for x in s["roots"] if x["name"]==root)
                if d["title"]==r["label"] and root_text:
                    d["title"]=re.split(r"\s*Тип сообщения СЭДО",root_text,flags=re.I)[0].rstrip(". ") or r["label"]
                d["module"]="ВНиМ, ССВ" if "ВНиМ" in fname else "Возмещения" if "Возмещения" in fname else None
                d.update(status="matched",archive_url=a["url"],archive_sha256=a["sha256"],schema_path=s["path"],dependency_digest=sha(encode(deps)),dependencies=deps)
            except (ValueError,KeyError) as ex:issues.append(str(ex))
        if d["status"]!="matched":issues.append([e,number,"Требуется проверка сопоставления",len(candidates)])
        d["revision_id"]=sha(encode([e,number,r["sha256"],d.get("dependency_digest")]))
        catalog.append(d)
    return dict(revisions=catalog,registry=registry,review=issues)

def export(state,target):
    ptr=json.loads((state/"current.json").read_bytes())["snapshot"]
    history=json.loads((state/"history.json").read_bytes()) if (state/"history.json").exists() else [ptr]
    versions={};current_ids=set()
    for key in dict.fromkeys([*history,ptr]):
        d=derive(json.loads((state/"snapshots"/(key+".json")).read_bytes()))
        for row in d["revisions"]:
            versions[row["revision_id"]]=row
            if key==ptr:current_ids.add(row["revision_id"])
        if key==ptr:latest=d
    latest["revisions"]=[dict(r,listed_now=i in current_ids) for i,r in versions.items()]
    write(target,encode(latest))
    print(len(versions),"revisions;",len(latest["registry"]),"registry rows;",len(latest["review"]),"review items")

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state",type=Path,default=Path("state"));p.add_argument("--output",type=Path,default=Path("catalog.json"))
    a=p.parse_args();export(a.state,a.output)

