"""Import incoming/prod and incoming/test transactionally; --check validates a PR."""
import argparse
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from auto_update import validate_history
from catalog import dependency_set
from manual_import import fingerprint, inspect_upload, prepare_import, promote_import
from sync import MAX_BYTES, encode, sha, write


def detect_metadata(filename, environment, override=None):
    match=re.search(r"тип[ _]+сообщения[ _]+(\d+)",filename,re.I) or re.match(r"(\d{1,8})(?=[_ .-]|$)",filename)
    date_match=re.search(r"(?<![\dvвVВ])(\d{2})[._](\d{2})[._](\d{4}|\d{2})(?!\d)",filename)
    version_name=filename[:date_match.start()] if date_match else filename
    version=re.search(r"[vв](\d+(?:[._]\d+)+)",version_name,re.I)
    date=""
    if date_match:
        year=("20" if len(date_match[3])==2 else "")+date_match[3]
        date=year+"-"+date_match[2]+"-"+date_match[1]
        try:datetime.strptime(date,"%Y-%m-%d")
        except ValueError as exc:raise ValueError("Некорректная дата в имени "+filename) from exc
    data=dict(number=match[1] if match else "",version=version[1].replace("_",".") if version else "",date=date,source="Ручная загрузка из GitHub")
    if override is not None:
        if not isinstance(override,dict) or set(override)-{"number","version","date","title","source"}:
            raise ValueError("Проверьте поля metadata.json для "+filename)
        data.update({key:str(value) for key,value in override.items()})
    data["environment"]=environment
    if not re.fullmatch(r"\d{1,8}",data["number"]):
        raise ValueError("Не определён номер сообщения: "+filename+". Используйте 10110_v3.1.docx или metadata.json.")
    return data


def collect(root):
    incoming=root/"incoming";documents=[];archives={};manifests={};inputs={}
    if incoming.is_symlink() or (hasattr(incoming,"is_junction") and incoming.is_junction()):
        raise ValueError("Ссылки в incoming не поддерживаются")
    if not incoming.exists():return [],{}
    for path in sorted(incoming.rglob("*")):
        if path.is_symlink() or (hasattr(path,"is_junction") and path.is_junction()):
            raise ValueError("Ссылки в incoming не поддерживаются: "+str(path.relative_to(root)))
        if not path.is_file():continue
        if path.name.lower().endswith(".md") or path.name==".gitkeep":continue
        relative=path.relative_to(incoming)
        if len(relative.parts)<2 or relative.parts[0] not in ("prod","test"):
            raise ValueError("Положите файл в incoming/prod или incoming/test: "+str(relative))
        if path.stat().st_size>(1024*1024 if path.name=="metadata.json" else MAX_BYTES):
            raise ValueError("Слишком большой файл: "+str(relative))
        content=path.read_bytes();inputs[path]=sha(content)
        if path.suffix.lower()==".docx":documents.append((path,content,relative.parts[0]))
        elif path.suffix.lower()==".zip":archives.setdefault(path.parent,[]).append((path,content,inspect_upload(content,"zip")))
        elif path.name=="metadata.json":
            value=json.loads(content.decode("utf-8-sig"))
            if not isinstance(value,dict):raise ValueError("metadata.json должен содержать объект по именам DOCX")
            manifests[path.parent]=value
        else:raise ValueError("Поддерживаются DOCX, ZIP и metadata.json: "+str(relative))
    tasks=[]
    for folder in archives:
        if not any(path.parent==folder for path,_,_ in documents):
            raise ValueError(str(folder.relative_to(root))+": положите DOCX рядом с ZIP, чтобы определить сообщение и версию.")
    for path,content,env in documents:
        meta=detect_metadata(path.name,env,manifests.get(path.parent,{}).get(path.name))
        try:
            structure=dict(inspect_upload(content,"docx").get("structure",[]))
            root_name=next((v for k,v in structure.items() if "корневой элемент" in k.lower()),None)
            namespace=next((v for k,v in structure.items() if "пространство имен" in k.lower()),None)
            candidates=[]
            for archive,body,info in archives.get(path.parent,[]):
                for schema in info.get("schemas",[]):
                    if schema["namespace"]==namespace and any(r["name"]==root_name for r in schema["roots"]):
                        digest=sha(encode(dependency_set(info["schemas"],schema["path"])))
                        candidates.append((digest,archive,body))
            if archives.get(path.parent) and not candidates:raise ValueError("В ZIP рядом с DOCX не найдена подходящая XSD")
            if len({x[0] for x in candidates})>1:raise ValueError("Подходят несколько разных XSD. Разнесите версии по отдельным папкам.")
            chosen=candidates[0] if candidates else None
            signature=sha(encode([sha(content),sha(chosen[2]) if chosen else None,meta]))
            tasks.append(dict(path=path,document=content,metadata=meta,archive=chosen[2] if chosen else None,
                              archive_name=chosen[1].name if chosen else "schemas.zip",signature=signature))
        except (ValueError,KeyError) as exc:raise ValueError(str(path.relative_to(root))+": "+str(exc)) from exc
    def order(task):
        m=task["metadata"];version=tuple(int(x) for x in m["version"].split(".")) if re.fullmatch(r"\d+(?:\.\d+)*",m["version"]) else ()
        return m["environment"],int(m["number"]),m["date"],version,str(task["path"])
    return sorted(tasks,key=order),inputs


def process_inbox(root,check=False):
    root=Path(root).resolve()
    original_fingerprint=fingerprint(root)
    tasks,inputs=collect(root)
    ledger_path=root/"state/inbox.json"
    ledger=json.loads(ledger_path.read_bytes()) if ledger_path.exists() else dict(format=1,files={})
    pending=[t for t in tasks if ledger["files"].get(t["path"].relative_to(root).as_posix(),{}).get("signature")!=t["signature"]]
    report=dict(status="ok",check_only=check,processed=len(pending),new_revisions=0,files=[])
    if not pending:return report
    previous=json.loads((root/"data/catalog.json").read_bytes())
    with tempfile.TemporaryDirectory(prefix="sedo-inbox-") as tmp:
        stage=Path(tmp)
        for folder in ("state","data","web"):shutil.copytree(root/folder,stage/folder)
        shutil.copy2(root/"viewer-template.html",stage/"viewer-template.html")
        shutil.copytree(root/"data/fields",stage/"previous-fields")
        for task in pending:
            relative=task["path"].relative_to(root).as_posix()
            try:
                preview=prepare_import(stage,task["document"],task["path"].name,task["metadata"],task["archive"],task["archive_name"])
                try:
                    preview.save()
                    if not preview.changed:
                        # Duplicate-only batches still need all validated generated pages.
                        shutil.copytree(preview.directory/"site",stage/"site",dirs_exist_ok=True)
                    report["new_revisions"]+=int(preview.changed)
                    rid=preview.revision["revision_id"]
                    ledger["files"][relative]=dict(signature=task["signature"],revision_id=rid,processed_at=datetime.now(timezone.utc).isoformat())
                    published=json.loads((preview.directory/"site/catalog-data.js").read_text("utf8").removeprefix("window.SEDO_CATALOG=").removesuffix(";"))
                    event=next(e for e in published["updates"] if e["revision_id"]==rid)
                    report["files"].append(dict(path=relative,number=preview.revision["number"],environment=preview.revision["environment"],version=preview.revision["document_version"],revision_id=rid,new_revision=preview.changed,status=preview.revision["status"],comparison=event.get("comparison")))
                finally:preview.close()
            except (ValueError,KeyError) as exc:raise ValueError(relative+": "+str(exc)) from exc
        write(stage/"state/inbox.json",encode(ledger))
        validate_history(previous,json.loads((stage/"data/catalog.json").read_bytes()),stage)
        if not check:
            if fingerprint(root)!=original_fingerprint or any(not path.is_file() or sha(path.read_bytes())!=digest for path,digest in inputs.items()):
                raise ValueError("Проект или incoming изменились. Повторите импорт.")
            promote_import(root,stage)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--report",type=Path,default=Path("inbox-report.json"))
    args=parser.parse_args()
    try:report=process_inbox(args.root,args.check)
    except Exception as exc:
        report=dict(status="failed",check_only=args.check,error=str(exc))
        write(args.report,encode(report));print(json.dumps(report,ensure_ascii=False));raise SystemExit(1)
    write(args.report,encode(report));print(json.dumps(report,ensure_ascii=False))


if __name__=="__main__":main()
