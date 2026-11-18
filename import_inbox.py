"""Import incoming/prod and incoming/test transactionally; --check validates a PR."""
import argparse
import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import build_site
from auto_update import update_lock, validate_history, build_history_baseline
from catalog import dependency_set
from schema import equivalent_closure
from document_metadata import root_metadata, root_matches, prefer_schema_location
from fields import build
from manual_import import filename_metadata, fingerprint, inspect_upload, stage_import, promote_import, import_structure
from sync import MAX_BYTES, encode, sha, write


def detect_metadata(filename, environment, override=None):
    match=re.search(r"тип[ _]+сообщения[ _]+(\d+)",filename,re.I) or re.match(r"(\d{1,8})(?=[_ .-]|$)",filename)
    data=dict(number=match[1] if match else "", **filename_metadata(filename), source="Ручная загрузка из GitHub")
    if override is not None:
        if not isinstance(override,dict) or set(override)-{"number","version","date","title","source"}:
            raise ValueError("Проверьте поля metadata.json для "+filename)
        data.update({key:str(value) for key,value in override.items()})
    data["environment"]=environment
    if data["date"]:
        try:datetime.strptime(data["date"],"%Y-%m-%d")
        except ValueError as exc:raise ValueError("Некорректная дата для "+filename) from exc
    if not re.fullmatch(r"\d{1,8}",data["number"]):
        raise ValueError("Не определён номер сообщения: "+filename+". Используйте 10110_v3.1.docx или metadata.json.")
    return data


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_checked(path, expected):
    content = path.read_bytes()
    if sha(content) != expected:
        raise ValueError("incoming изменились. Повторите импорт: " + str(path))
    return content


def inbox_inputs(root):
    incoming=root/"incoming";inputs={}
    if incoming.is_symlink() or (hasattr(incoming,"is_junction") and incoming.is_junction()):
        raise ValueError("Ссылки в incoming не поддерживаются")
    if not incoming.exists():return {}
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
        if path.suffix.lower() not in (".docx", ".zip") and path.name != "metadata.json":
            raise ValueError("Поддерживаются DOCX, ZIP и metadata.json: "+str(relative))
        inputs[path]=file_digest(path)
    return inputs


def collect(root, ledger=None):
    incoming=root/"incoming";documents=[];archives={};manifests={}
    inputs=inbox_inputs(root)
    for path in inputs:
        relative=path.relative_to(incoming)
        if path.suffix.lower()==".docx":documents.append((path,relative.parts[0]))
        elif path.suffix.lower()==".zip":archives.setdefault(path.parent,[]).append(path)
        elif path.name=="metadata.json":
            value=json.loads(read_checked(path, inputs[path]).decode("utf-8-sig"))
            if not isinstance(value,dict):raise ValueError("metadata.json должен содержать объект по именам DOCX")
            manifests[path.parent]=value
    for folder, manifest in manifests.items():
        names = {path.name for path, _ in documents if path.parent == folder}
        unknown = set(manifest) - names
        if unknown:
            raise ValueError(str(folder.relative_to(root)) + ": неизвестные DOCX в metadata.json: " + ", ".join(sorted(unknown)))
    catalog_path = root / 'data/catalog.json'
    known_revisions = {row['revision_id'] for row in json.loads(catalog_path.read_bytes()).get('revisions', [])} if catalog_path.is_file() else set()
    known_revisions.update(saved['revision_id'] for saved in (ledger or {}).get('files', {}).values() if saved.get('revision_id'))
    tasks=[]
    archive_info={}
    archive_folder=None
    for folder in archives:
        if not any(path.parent==folder for path,_ in documents):
            raise ValueError(str(folder.relative_to(root))+": положите DOCX рядом с ZIP, чтобы определить сообщение и версию.")
    for path,env in documents:
        override=manifests.get(path.parent,{}).get(path.name)
        meta=detect_metadata(path.name,env,override)
        explicit_fields=sorted(set(override or {}) & {"version","date","title","source"})
        input_signature=sha(encode([inputs[path], [(a.name,inputs[a]) for a in archives.get(path.parent,[])], meta, explicit_fields]))
        saved=(ledger or {}).get("files",{}).get(path.relative_to(root).as_posix(),{})
        if saved.get("input_signature")==input_signature and saved.get("explicit_fields",[])==explicit_fields:
            continue
        if path.parent != archive_folder:
            archive_info={}
            archive_folder=path.parent
        try:
            structure=dict(import_structure(inspect_upload(read_checked(path, inputs[path]),"docx"),meta['number'])[0])
            root_name, namespace, namespace_declared = root_metadata(structure.items())
            candidates=[]
            matches=[]
            for archive in archives.get(path.parent,[]):
                if archive not in archive_info:
                    archive_info[archive]=inspect_upload(read_checked(archive, inputs[archive]),"zip")
                info=archive_info[archive]
                for schema in info.get("schemas",[]):
                    if root_matches(schema, root_name, namespace, namespace_declared):
                        matches.append((archive,schema))
            for archive,schema in prefer_schema_location(matches,structure.items()):
                closure = dependency_set(archive_info[archive]["schemas"], schema["path"])
                digest = equivalent_closure(closure, schema["path"])
                rid = sha(encode([env, meta["number"], inputs[path], sha(encode(closure))]))
                candidates.append((digest,archive,rid))
            if archives.get(path.parent) and not candidates:raise ValueError("В ZIP рядом с DOCX не найдена подходящая XSD")
            if len({x[0] for x in candidates})>1:raise ValueError("Подходят несколько разных XSD. Разнесите версии по отдельным папкам.")
            chosen=next((c for c in candidates if c[2] == saved.get("revision_id")),
                        next((c for c in candidates if c[2] in known_revisions), candidates[0] if candidates else None))
            signature=sha(encode([inputs[path],inputs[chosen[1]] if chosen else None,meta]))
            tasks.append(dict(path=path,metadata=meta,explicit_fields=explicit_fields,archive=chosen[1] if chosen else None,
                              input_signature=input_signature, preferred_revision_id=chosen[2] if chosen else saved.get("revision_id"),
                              archive_name=chosen[1].name if chosen else "schemas.zip",signature=signature))
        except (ValueError,KeyError) as exc:raise ValueError(str(path.relative_to(root))+": "+str(exc)) from exc
    def order(task):
        m=task["metadata"];version=tuple(int(x) for x in m["version"].split(".")) if re.fullmatch(r"\d+(?:\.\d+)*",m["version"]) else ()
        return m["environment"],int(m["number"]),m["date"],version,str(task["path"])
    return sorted(tasks,key=order),inputs


def process_inbox(root,check=False):
    with update_lock(root) as locked_root:
        return _process_inbox(locked_root, check)


def _process_inbox(root, check):
    ledger_path=root/"state/inbox.json"
    ledger=json.loads(ledger_path.read_bytes()) if ledger_path.exists() else dict(format=1,files={})
    tasks,inputs=collect(root, ledger=ledger)
    # collect filters only inputs confirmed by the current index format. Older
    # ledgers are revalidated once, then receive the complete input signature.
    pending=tasks
    report=dict(status="ok",check_only=check,processed=len(pending),new_revisions=0,files=[])
    if not pending:return report
    original_fingerprint=fingerprint(root)
    previous=json.loads((root/"data/catalog.json").read_bytes())
    with tempfile.TemporaryDirectory(prefix="sedo-inbox-") as tmp:
        stage=Path(tmp)
        for folder in ("state","data","web"):shutil.copytree(root/folder,stage/folder)
        shutil.copy2(root/"viewer-template.html",stage/"viewer-template.html")
        build_history_baseline(stage)
        for task in pending:
            relative=task["path"].relative_to(root).as_posix()
            try:
                old_fields=ledger["files"].get(relative,{}).get("explicit_fields",[])
                result=stage_import(stage,read_checked(task["path"], inputs[task["path"]]),task["path"].name,task["metadata"],
                                    read_checked(task["archive"], inputs[task["archive"]]) if task["archive"] else None,task["archive_name"],
                                    correction_fields=set(task["explicit_fields"]), reset_fields=old_fields,
                                    previous_revision_id=ledger['files'].get(relative,{}).get('revision_id'),
                                    preferred_revision_id=task["preferred_revision_id"])
                revision=result["revision"];rid=revision["revision_id"]
                report["new_revisions"]+=int(result["new_revision"])
                ledger["files"][relative]=dict(signature=task["signature"],input_signature=task["input_signature"],explicit_fields=task["explicit_fields"],
                                               revision_id=rid,processed_at=datetime.now(timezone.utc).isoformat())
                report["files"].append(dict(path=relative,number=revision["number"],environment=revision["environment"],
                                            version=revision["document_version"],revision_id=rid,
                                            new_revision=result["new_revision"],status=revision["status"]))
            except (ValueError,KeyError) as exc:raise ValueError(relative+": "+str(exc)) from exc
        build(stage/"data/catalog.json",stage/"state",stage/"data/fields")
        with patch.object(build_site, "HERE", stage):
            build_site.main()
        published=json.loads((stage/"site/catalog-data.js").read_text("utf8").removeprefix("window.SEDO_CATALOG=").removesuffix(";"))
        events={e["revision_id"]:e for e in published["updates"]}
        for file in report["files"]:
            file["comparison"]=events[file["revision_id"]].get("comparison")
        write(stage/"state/inbox.json",encode(ledger))
        validate_history(previous,json.loads((stage/"data/catalog.json").read_bytes()),stage)
        if not check:
            if fingerprint(root)!=original_fingerprint or inbox_inputs(root)!=inputs:
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
