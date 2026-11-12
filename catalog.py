"""Materialize a catalog from successful inventories. Does not contact SFR."""
import json
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote
from sync import encode,sha,write

from schema import dependency_set

def derive(snapshot):
    if snapshot.get("source_type")=="manual":
        return dict(revisions=[dict(snapshot["revision"])],registry=[],review=[])
    rows=snapshot["entries"];catalog=[]; registry=[]; issues=[]
    for r in rows:
        for table in r.get("registry_tables",[]):
            if table[0][:2]!=["Код типа сообщения","Наименование"]:continue
            group=""
            for cells in table[1:]:
                if len(cells)==1:group=cells[0]
                elif len(cells)>=4 and cells[0].isdigit():
                    registry.append(dict(environment=r["environment"],number=cells[0],title=cells[1],description=cells[2],appendix=cells[3],module=group,source=r["url"]))
    # Module declarations supplement, rather than overwrite, the general list.
    known = {(r["environment"], r["number"]) for r in registry}
    for r in rows:
        if r["kind"] != "docx": continue
        filename = unquote(r["url"].split("/")[-1])
        module = re.search(r"Пр[ _]+(\d+)[ _]+(.+?)(?:\s*\(|\.docx$)", filename, re.I)
        if not module: continue
        appendix, name = module.groups()
        version = re.search(r"[vв](\d+(?:[._]\d+)+)", r["label"], re.I)
        date = re.search(r"(\d{2}\.\d{2}\.(?:\d{4}|\d{2}))(?!\d)", r["label"])
        for declaration in r.get("message_types", []):
            identity = (r["environment"], declaration["number"])
            if identity in known: continue
            registry.append(dict(environment=r["environment"], number=declaration["number"],
                                 title=declaration["title"], description="Тип описан в отдельной спецификации модуля.",
                                 appendix=str(int(appendix)), module=name.replace("_", " ").strip(), source=r["url"],
                                 source_type="module", document_sha256=r["sha256"],
                                 document_version=version[1].replace("_", ".") if version else None,
                                 label_date=date[1] if date else None))
            known.add(identity)
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
    for r in rows:
        for match in r.get("module_matches", []):
            version=re.search(r"[vв](\d+(?:[._]\d+)+)",r["label"],re.I)
            date=re.search(r"(\d{2}\.\d{2}\.(?:\d{4}|\d{2}))(?!\d)",r["label"])
            row=dict(match,environment=r["environment"],document_url=r["url"],document_sha256=r["sha256"],
                     document_version=version[1].replace("_",".") if version else None,label_date=date[1] if date else None,
                     module=next((x["module"] for x in registry if x["environment"]==r["environment"] and x["number"]==match["number"]),r["label"]),
                     source_type="module",status="matched",version_tables=r.get("version_tables",[]))
            row["revision_id"]=sha(encode([r["environment"],row["number"],r["sha256"],row["dependency_digest"]]))
            catalog.append(row)
    return dict(revisions=catalog,registry=registry,review=issues)

def export(state,target):
    ptr=json.loads((state/"current.json").read_bytes())["snapshot"]
    history=json.loads((state/"history.json").read_bytes()) if (state/"history.json").exists() else [ptr]
    observation_file=state/"observations.json"
    observations=json.loads(observation_file.read_bytes())["snapshots"] if observation_file.exists() else {}
    updates=[];previous_by_message={}
    versions={};current_ids=set();registry={};current_registry=set()
    for batch,key in enumerate(dict.fromkeys([*history,ptr])):
        d=derive(json.loads((state/"snapshots"/(key+".json")).read_bytes()))
        added={}
        for row in d["revisions"]:
            identity=(row["number"],row["environment"])
            if row["revision_id"] not in versions:
                updates.append(dict(revision_id=row["revision_id"],snapshot=key,
                                    observed_at=observations.get(key),initial_import=batch==0,
                                    previous_revision_id=previous_by_message.get(identity)))
                added[identity]=row["revision_id"]
            old=versions.get(row["revision_id"])
            if not old or row.get("source_type")!="manual" or old.get("source_type")=="manual":
                if old and old.get("manual_source") and not row.get("manual_source"):
                    row=dict(row,manual_source=old["manual_source"])
                versions[row["revision_id"]]=row
            if key==ptr:current_ids.add(row["revision_id"])
        # Editions discovered in the same batch share a previous historical edition.
        previous_by_message.update(added)
        for row in d["registry"]:
            identity=sha(encode(row))
            registry[identity]=row
            if key==ptr:current_registry.add(identity)
        if key==ptr:latest=d
    latest["revisions"]=[dict(r,listed_now=i in current_ids) for i,r in versions.items()]
    latest["registry"]=[dict(row,listed_now=identity in current_registry) for identity,row in registry.items()]
    latest["updates"]=updates
    report_file=state/"last-run.json"
    if report_file.exists():
        report=json.loads(report_file.read_bytes())
        if report.get("snapshot")==ptr and not report.get("errors"):
            latest["checked_at"]=report["observed_at"]
    write(target,encode(latest))
    print(len(versions),"revisions;",len(latest["registry"]),"registry rows;",len(latest["review"]),"review items")

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state",type=Path,default=Path("state"));p.add_argument("--output",type=Path,default=Path("catalog.json"))
    a=p.parse_args();export(a.state,a.output)

