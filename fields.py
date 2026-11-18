"""Export tag trees for all verified revisions from the local archive store."""
import argparse
import collections
import json
import zipfile
from pathlib import Path
from schema import Schema
from sync import sha,encode,write
def build(index,state,target):
    catalog=json.loads(index.read_bytes())
    statuses=[]
    for r in catalog["revisions"]:
        key=r["revision_id"]; report=dict(revision_id=key,number=r["number"],environment=r["environment"])
        if r["status"]=="document_only" and r.get("source_type")=="manual":
            statuses.append(dict(report,status="document_only"));continue
        if r["status"]!="matched":
            statuses.append(dict(report,status="review"));continue
        source=state/"objects"/r["archive_sha256"]
        if sha(source.read_bytes())!=r["archive_sha256"]:raise ValueError("Поврежден локальный архив")
        try:
            with zipfile.ZipFile(source) as z:
                schema=Schema(z,r["schema_path"],r["root"]);schema.field(schema.root)
            fields=schema.fields
            paths=set()
            for field in fields:
                if field['path'] in paths:raise ValueError('Неоднозначный XML-путь в схеме: '+field['path'])
                paths.add(field['path'])
            write(target/(key+".json"),encode(dict(revision_id=key,fields=fields)))
            statuses.append(dict(report,status="ready",count=len(fields)))
        except (ValueError,KeyError) as exc:
            statuses.append(dict(report,status="review",error=str(exc)))
    write(target/"index.json",encode(statuses))
    print(collections.Counter(r["status"] for r in statuses))
if __name__=="__main__":
    from auto_update import catalog_locks
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--index",type=Path,default=Path("catalog.json"));p.add_argument("--state",type=Path,default=Path("state"));p.add_argument("--output",type=Path,default=Path("fields"))
    a=p.parse_args()
    with catalog_locks(a.index.parent, a.state, a.output):
        build(a.index,a.state,a.output)

