"""Stage, validate and promote a complete SFR update. Publication is done by Actions."""
import json
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


def run(root):
    root = Path(root).resolve()
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
            # Keep originals for all historical editions as well as their XSD archives.
            objects = {r["archive_sha256"] for r in current["revisions"] if r.get("archive_sha256")}
            objects.update(r["document_sha256"] for r in current["revisions"])
            objects.update(r["document_sha256"] for r in current["registry"] if r.get("document_sha256"))
            for name in ("current.json", "history.json", "last-run.json", "observations.json"):
                shutil.copy2(stage / "state" / name, root / "state" / name)
            shutil.copytree(stage / "state/snapshots", root / "state/snapshots", dirs_exist_ok=True)
            for digest in objects:
                source = stage / "state/objects" / digest
                shutil.copy2(source, root / "state/objects" / digest)
            for folder in ("data", "site"):
                shutil.copytree(stage / folder, root / folder, dirs_exist_ok=True)
            old_ids = {r["revision_id"] for r in previous["revisions"]}
            added = [dict(number=r["number"], environment=r["environment"], version=r["document_version"], revision_id=r["revision_id"])
                     for r in current["revisions"] if r["revision_id"] not in old_ids]
            known = {(r["environment"], r["number"]) for r in previous["registry"]+previous["revisions"]}
            new_messages = sorted({(r["environment"], r["number"]) for r in current["registry"]+current["revisions"]}-known)
            snapshot=json.loads((stage/"state/snapshots"/(json.loads((stage/"state/current.json").read_bytes())["snapshot"]+".json")).read_bytes())
            schema_diagnostics=[dict(environment=r["environment"],source=r["url"],issues=r["schema_diagnostics"]) for r in snapshot["entries"] if r.get("schema_diagnostics")]
            ready_pairs={(r["environment"],r["number"]) for r in current["revisions"] if r["status"]=="matched"}
            registry_pairs={(r["environment"],r["number"]):r for r in current["registry"]}
            missing=[]
            for (env,number),row in sorted(registry_pairs.items(),key=lambda i:(i[0][0],int(i[0][1]))):
                if (env,number) in ready_pairs:continue
                reasons=list(dict.fromkeys(issue["reason"] for record in schema_diagnostics if record["environment"]==env for issue in record["issues"] if issue.get("number")==number))
                missing.append(dict(environment=env,number=number,title=row["title"],reasons=reasons or ["В опубликованных источниках не найдена полная сопоставленная XSD."]))
            write(root/"data/schema-coverage.json",encode(dict(checked_at=current["checked_at"],numbers_with_trees=len({n for e,n in ready_pairs}),missing=missing)))
            result = dict(status="ok", checked_at=current["checked_at"], new_revisions=added, schema_diagnostics=schema_diagnostics, missing_trees=missing,
                          new_messages=[dict(environment=e, number=n) for e,n in new_messages],
                          total_revisions=len(current["revisions"]))
            write(report, encode(result))
            print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        write(report, encode(dict(status="failed", error=str(exc))))
        raise


if __name__ == "__main__":
    run(Path(__file__).resolve().parent)

