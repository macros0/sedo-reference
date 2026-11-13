"""Stage, validate and promote a complete SFR update. Publication is done by Actions."""
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import build_site
from catalog import export
from fields import build
from sync import encode, sync, write


def validate_history(previous, current, directory):
    old_ids = {r["revision_id"] for r in previous["revisions"]}
    new_ids = {r["revision_id"] for r in current["revisions"]}
    if not old_ids <= new_ids:
        raise ValueError("Обновление потеряло сохраненные редакции")
    events={e["revision_id"]:e for e in current["updates"]}
    if len(events)!=len(current["updates"]) or set(events)!=new_ids:
        raise ValueError("Журнал не соответствует сохранённым редакциям")
    for event in previous.get("updates", []):
        if events.get(event["revision_id"])!=event:
            raise ValueError("Изменилось ранее сохранённое событие журнала")
    revisions={r["revision_id"]:r for r in current["revisions"]}
    for rid,event in events.items():
        before=event["previous_revision_id"]
        if before:
            a,b=revisions.get(before),revisions[rid]
            if not a or before==rid or (a["number"],a["environment"])!=(b["number"],b["environment"]):
                raise ValueError("Некорректная ссылка сравнения в журнале")
    if current["review"]:
        raise ValueError("Новые схемы требуют проверки сопоставления")
    status = json.loads((directory / "data/fields/index.json").read_bytes())
    ready = {r["revision_id"] for r in status if r["status"] == "ready"}
    documents = {r["revision_id"] for r in current["revisions"] if r["status"]=="document_only" and r.get("source_type")=="manual"}
    if ready != new_ids-documents or {r["revision_id"] for r in status if r["status"]=="document_only"}!=documents:
        raise ValueError("Не все редакции удалось построить")
    for revision in current["revisions"]:
        rid = revision["revision_id"]
        if rid in documents:
            continue
        fields = json.loads((directory / "data/fields" / (rid + ".json")).read_bytes())["fields"]
        if not fields or fields[0]["name"] != revision["root"]:
            raise ValueError("Некорректный корень " + rid)
        for idx, field in enumerate(fields):
            if any(fields[child]["parent"] != idx for child in field["children"]):
                raise ValueError("Повреждена связь полей " + rid)
        if not (directory / "site/editions" / (rid + ".html")).is_file():
            raise ValueError("Не создана страница " + rid)
    for rid in old_ids:
        old_field = directory / "previous-fields" / (rid + ".json")
        new_field = directory / "data/fields" / (rid + ".json")
        old_revision=next(r for r in previous["revisions"] if r["revision_id"]==rid)
        if old_revision["status"]=="document_only" and old_revision.get("source_type")=="manual":
            continue
        if old_field.read_bytes() != new_field.read_bytes():
            raise ValueError("Изменилось дерево существующей редакции " + rid)
    for env in ("prod", "test"):
        old_current = [r for r in previous["revisions"] if r["environment"] == env and r.get("listed_now", True)]
        new_current = [r for r in current["revisions"] if r["environment"] == env and r.get("listed_now", True)]
        # A disappearing document is retained in history; an empty contour needs investigation.
        if old_current and not new_current:
            raise ValueError("Исчезли все актуальные тома контура " + env)


def recover_promotion(root):
    recovery = root / ".update-recovery"
    if not recovery.exists():
        return
    journal = recovery / "journal.json"
    if journal.exists() and not (recovery / "committed").exists():
        existed = json.loads(journal.read_bytes())
        for folder in ("state", "data", "site"):
            backup, target = recovery / "previous" / folder, root / folder
            if backup.exists():
                if target.exists():
                    shutil.rmtree(target)
                backup.replace(target)
            elif not existed[folder] and target.exists():
                shutil.rmtree(target)
    shutil.rmtree(recovery)


def promote(root, stage):
    """Keep a recoverable previous generation until every directory is installed."""
    recover_promotion(root)
    recovery = root / ".update-recovery"
    recovery.mkdir()
    try:
        for folder in ("state", "data", "site"):
            shutil.copytree(stage / folder, recovery / "candidate" / folder)
        (recovery / "previous").mkdir()
        existed = {folder: (root / folder).exists() for folder in ("state", "data", "site")}
        temporary = recovery / "journal.tmp"
        temporary.write_bytes(encode(existed))
        temporary.replace(recovery / "journal.json")
        for folder in existed:
            if existed[folder]:
                (root / folder).replace(recovery / "previous" / folder)
            (recovery / "candidate" / folder).replace(root / folder)
        (recovery / "committed").touch()
    except Exception:
        recover_promotion(root)
        raise
    recover_promotion(root)


def run(root):
    root = Path(root).resolve()
    # OS releases the lock even when the process terminates unexpectedly.
    with (root / ".update.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _run(root)


def _run(root):
    root = Path(root).resolve()
    recover_promotion(root)
    report = root / "update-report.json"
    previous = json.loads((root / "data/catalog.json").read_bytes())
    try:
        with tempfile.TemporaryDirectory(prefix="sedo-update-") as tmp:
            stage = Path(tmp)
            for folder in ("state", "data", "web"):
                shutil.copytree(root / folder, stage / folder)
            shutil.copytree(root / "data/fields", stage / "previous-fields")
            shutil.copy2(root / "viewer-template.html", stage / "viewer-template.html")
            try:
                sync(stage / "state")
            except Exception as exc:
                failure = stage / "state/last-run.json"
                detail = json.loads(failure.read_bytes()).get("errors", []) if failure.exists() else []
                raise ValueError(f"{exc}; source errors: {detail}") from exc
            export(stage / "state", stage / "data/catalog.json")
            current = json.loads((stage / "data/catalog.json").read_bytes())
            current["checked_at"] = json.loads((stage / "state/last-run.json").read_bytes())["observed_at"]
            write(stage / "data/catalog.json", encode(current))
            build(stage / "data/catalog.json", stage / "state", stage / "data/fields")
            with patch.object(build_site, "HERE", stage):
                build_site.main()
            validate_history(previous, current, stage)
            old_ids = {r["revision_id"] for r in previous["revisions"]}
            added = [dict(number=r["number"], environment=r["environment"], version=r["document_version"], revision_id=r["revision_id"])
                     for r in current["revisions"] if r["revision_id"] not in old_ids]
            known = {(r["environment"], r["number"]) for r in previous["registry"]+previous["revisions"]}
            new_messages = sorted({(r["environment"], r["number"]) for r in current["registry"]+current["revisions"]}-known)
            snapshot=json.loads((stage/"state/snapshots"/(json.loads((stage/"state/current.json").read_bytes())["snapshot"]+".json")).read_bytes())
            schema_diagnostics=[dict(environment=r["environment"],source=r["url"],issues=r["schema_diagnostics"]) for r in snapshot["entries"] if r.get("schema_diagnostics")]
            wsdl_status=[dict(environment=r["environment"],source=r["url"],status=r.get("status",r.get("retrieval_status","ok")),error=r.get("error")) for r in snapshot["entries"] if r.get("source_type")=="wsdl" or r["kind"]=="wsdl_status"]
            ready_pairs={(r["environment"],r["number"]) for r in current["revisions"] if r["status"]=="matched"}
            registry_pairs={(r["environment"],r["number"]):r for r in current["registry"]}
            missing=[]
            for (env,number),row in sorted(registry_pairs.items(),key=lambda i:(i[0][0],int(i[0][1]))):
                if (env,number) in ready_pairs:continue
                reasons=list(dict.fromkeys(issue["reason"] for record in schema_diagnostics if record["environment"]==env for issue in record["issues"] if issue.get("number")==number))
                for source in wsdl_status:
                    if source["environment"]==env and source["status"]=="unavailable":
                        reasons.append("WSDL недоступен: "+source["source"].split("#")[0]+" — "+source["error"])
                missing.append(dict(environment=env,number=number,title=row["title"],reasons=reasons or ["В опубликованных источниках не найдена полная сопоставленная XSD."]))
            write(stage/"data/schema-coverage.json",encode(dict(checked_at=current["checked_at"],numbers_with_trees=len({n for e,n in ready_pairs}),source_checks=wsdl_status,missing=missing)))
            promote(root, stage)
            result = dict(status="ok", checked_at=current["checked_at"], new_revisions=added, schema_diagnostics=schema_diagnostics, wsdl_status=wsdl_status, missing_trees=missing,
                          new_messages=[dict(environment=e, number=n) for e,n in new_messages],
                          total_revisions=len(current["revisions"]))
            write(report, encode(result))
            print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        write(report, encode(dict(status="failed", error=str(exc))))
        raise


if __name__ == "__main__":
    run(Path(__file__).resolve().parent)

