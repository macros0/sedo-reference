"""Stage, validate and promote a complete SFR update. Publication is done by Actions."""
import json
import shutil
import tempfile
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch

import build_site
from catalog import export
from collector_snapshots import collector_source
from fields import build
from edition_groups import display_updates, group_editions, source_updates
from state_lock import catalog_lock_root, file_lock
from sync import encode, resource_url, sync, write


def validate_publications(previous, current):
    for env in ('prod', 'test'):
        for category in ('revisions', 'registry'):
            def listed(catalog):
                return {row['number'] for row in catalog.get(category, [])
                        if row['environment'] == env and row.get('listed_now', True)
                        and row.get('source_type') != 'manual'}
            old, new = listed(previous), listed(current)
            removed = old - new
            # A single retirement is normal. Losing over 20% of a contour's
            # publications requires investigation before promoting the snapshot.
            if old and ((category == 'revisions' and not new) or len(removed) > max(1, len(old) * 0.2)):
                raise ValueError(f'Массовое исчезновение публикаций {env}/{category}: '
                                 f'{len(old)} -> {len(new)}, исчезло {len(removed)}; '
                                 + ', '.join(sorted(removed)[:10]))


def validate_inventory(previous, current):
    """Compare the links published by each contour, including guides and navigation."""
    def resources(snapshot, env):
        return {resource_url(row['url']) for row in snapshot['entries']
                if row['environment'] == env and row.get('source_type') != 'wsdl'
                and row['kind'] != 'wsdl_status' and row['url'].startswith(('http://', 'https://'))}
    for env in ('prod', 'test'):
        old, new = resources(previous, env), resources(current, env)
        removed = old - new
        if len(removed) > max(1, len(old) * 0.2):
            raise ValueError(f'Массовое исчезновение ссылок {env}: {len(old)} -> {len(new)}, '
                             f'исчезло {len(removed)}; ' + ', '.join(sorted(removed)[:10]))


def active_inventory(state):
    key = json.loads((state / 'current.json').read_bytes())['snapshot']
    return json.loads((state / 'snapshots' / (key + '.json')).read_bytes())


def validate_saved_history(previous, current, archived_ids=()):
    """Retain confirmed editions/events, allowing explicitly archived failed reviews."""
    archived = set(archived_ids)
    old_ids = {r["revision_id"] for r in previous.get("revisions", [])} - archived
    new_ids = {r["revision_id"] for r in current.get("revisions", [])}
    if not old_ids <= new_ids:
        raise ValueError("Обновление потеряло сохраненные редакции")
    events = {e["revision_id"]: e for e in current.get("updates", [])}
    if len(events) != len(current.get("updates", [])) or set(events) != new_ids:
        raise ValueError("Журнал не соответствует сохранённым редакциям")
    old_events = {e["revision_id"]: e for e in previous.get("updates", [])}
    for rid, event in old_events.items():
        if rid in archived:
            continue
        expected = dict(event)
        before = expected["previous_revision_id"]
        visited = set()
        while before in archived and before not in visited:
            visited.add(before)
            before = old_events.get(before, {}).get("previous_revision_id")
        expected["previous_revision_id"] = before
        if events.get(rid) != expected:
            raise ValueError("Изменилось ранее сохранённое событие журнала")
    def enriched(old, new):
        # Word history is restored from the original bytes without changing the
        # evidence already recorded by earlier catalog formats.
        return old == {k: v for k, v in new.items() if k != "document_history" or k in old}
    for rid, source in previous.get("update_sources", {}).items():
        if rid not in archived and not enriched(source, current.get("update_sources", {}).get(rid, {})):
            raise ValueError("Изменился ранее сохранённый источник события журнала")
    observations = [row for row in current.get("source_observations", []) if row["revision_id"] not in archived]
    old_observations = [row for row in previous.get("source_observations", []) if row["revision_id"] not in archived]
    for old, new in zip(old_observations, observations):
        if ({k: v for k, v in old.items() if k != "source"} != {k: v for k, v in new.items() if k != "source"}
                or not enriched(old["source"], new["source"])):
            raise ValueError("Изменились ранее сохранённые наблюдения источников")
    if len(observations) < len(old_observations):
        raise ValueError("Изменились ранее сохранённые наблюдения источников")


def validate_history(previous, current, directory):
    validate_publications(previous, current)
    validate_saved_history(previous, current)
    old_ids = {r["revision_id"] for r in previous["revisions"]}
    new_ids = {r["revision_id"] for r in current["revisions"]}
    events = {e["revision_id"]: e for e in current["updates"]}
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


def build_history_baseline(stage):
    """Recompute the confirmed inputs with the generator used for the candidate."""
    build(stage / "data/catalog.json", stage / "state", stage / "previous-fields")


def recover_promotion(root):
    recovery = root / ".update-recovery"
    if not recovery.exists():
        return
    if recovery.is_symlink() or getattr(recovery, "is_junction", lambda: False)():
        raise ValueError("Ссылка на каталог восстановления " + str(recovery))
    if (recovery / "cleanup.json").is_file():
        build_site.cleanup_site_recovery(recovery)
        return
    allowed = {"journal.json", "journal.tmp", "cleanup.partial", "committed", "preparing", "previous", "candidate"}
    for entry in recovery.iterdir():
        if entry.name not in allowed or entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            raise ValueError("Неизвестное содержимое каталога восстановления " + str(recovery))
    if any(recovery.iterdir()) and not any((recovery / name).is_file() for name in ("journal.json", "committed", "preparing")):
        raise ValueError("Отсутствуют признаки транзакции восстановления " + str(recovery))
    for directory in ("previous", "candidate"):
        path = recovery / directory
        if path.exists():
            for entry in path.iterdir():
                if entry.name not in ("state", "data", "site") or not entry.is_dir():
                    raise ValueError("Неизвестное содержимое каталога восстановления " + str(path))
    for entry in recovery.rglob("*"):
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            raise ValueError("Ссылка внутри каталога восстановления " + str(recovery))
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
    # Atomically record ownership before deleting any transaction evidence.
    # A retry resumes this manifest and never replays rollback after cleanup began.
    build_site.cleanup_site_recovery(recovery)


def promote(root, stage):
    """Keep a recoverable previous generation until every directory is installed."""
    recover_promotion(root)
    build_site.preserve_publication_files(root / "site", stage / "site")
    recovery = root / ".update-recovery"
    recovery.mkdir()
    (recovery / "preparing").touch()
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


@contextmanager
def update_lock(root, *, filename=".update.lock", recover=True, catalog_pair=None):
    with file_lock(root, filename=filename) as root:
        if recover:
            recover_promotion(root)
            build_site.recover_site(root)
            from sync import confirmation_lock, recover_confirmation
            state = root / "state"
            if (root / ".catalog-recovery").is_dir() and not (catalog_pair and catalog_pair[0] == root):
                raise ValueError("Завершите восстановление update_catalog.py с исходными --state и --out")
            if (state / ".catalog-recovery").is_dir() and not (catalog_pair and catalog_pair[0] == state):
                from update_catalog import recover_catalog
                recover_catalog(state, root / "data")
            if (state / ".sync-recovery.json").exists():
                with confirmation_lock(state):
                    recover_confirmation(state)
        yield root


@contextmanager
def catalog_locks(*directories, catalog_pair=None):
    """CLI writers sharing a managed state, data or site directory share its project lock."""
    roots = {catalog_lock_root(directory) for directory in directories}
    with ExitStack() as locks:
        for root in sorted(roots, key=str):
            root.mkdir(parents=True, exist_ok=True)
            locks.enter_context(update_lock(root, catalog_pair=catalog_pair))
        yield


def run(root):
    with update_lock(root) as locked_root:
        _run(locked_root)


def edition_changes(previous, current, fields_directory):
    """Use the browser journal's grouping while preserving legacy source rows."""
    statuses = json.loads((fields_directory / "index.json").read_bytes())
    ready = {row["revision_id"] for row in statuses if row["status"] == "ready"}
    fields = {}
    def load(rid):
        if rid not in fields:
            fields[rid] = json.loads((fields_directory / (rid + ".json")).read_bytes())["fields"]
        return fields[rid]
    def grouped(catalog):
        return group_editions(dict(catalog, revisions=[dict(row, ready=row["revision_id"] in ready)
                                                       for row in catalog["revisions"]]), load)
    old, new = grouped(previous), grouped(current)
    old_schemas = {event["revision_id"] for event in display_updates(old)}
    old_sources = {event["event_id"] for event in source_updates(old)}
    return ([event for event in display_updates(new) if event["revision_id"] not in old_schemas],
            [event for event in source_updates(new) if event["event_id"] not in old_sources])


def _run(root, *, history_only=False):
    report = root / "update-report.json"
    previous = json.loads((root / "data/catalog.json").read_bytes())
    try:
        with tempfile.TemporaryDirectory(prefix="sedo-update-") as tmp:
            stage = Path(tmp)
            for folder in ("state", "data", "web"):
                shutil.copytree(root / folder, stage / folder)
            build_history_baseline(stage)
            shutil.copy2(root / "viewer-template.html", stage / "viewer-template.html")
            try:
                if history_only:
                    from collector_snapshots import replay_archived_inputs
                    collector_replay = replay_archived_inputs(stage / "state")
                else:
                    sync(stage / "state")
            except Exception as exc:
                failure = stage / "state/last-run.json"
                detail = json.loads(failure.read_bytes()).get("errors", []) if failure.exists() else []
                raise ValueError(f"{exc}; source errors: {detail}") from exc
            snapshot = active_inventory(stage / "state")
            validate_inventory(active_inventory(root / "state"), snapshot)
            export(stage / "state", stage / "data/catalog.json")
            current = json.loads((stage / "data/catalog.json").read_bytes())
            validate_publications(previous, current)
            if not history_only:
                current["checked_at"] = json.loads((stage / "state/last-run.json").read_bytes())["observed_at"]
            else:
                current["checked_at"] = previous.get("checked_at") or current.get("checked_at")
            write(stage / "data/catalog.json", encode(current))
            build(stage / "data/catalog.json", stage / "state", stage / "data/fields")
            with patch.object(build_site, "HERE", stage):
                build_site.main()
            validate_history(previous, current, stage)
            old_ids = {r["revision_id"] for r in previous["revisions"]}
            added = [dict(number=r["number"], environment=r["environment"], version=r["document_version"], revision_id=r["revision_id"])
                     for r in current["revisions"] if r["revision_id"] not in old_ids]
            schema_events, source_events = edition_changes(previous, current, stage / "data/fields")
            known = {(r["environment"], r["number"]) for r in previous["registry"]+previous["revisions"]}
            new_messages = sorted({(r["environment"], r["number"]) for r in current["registry"]+current["revisions"]}-known)
            if not history_only:
                collector_replay=json.loads((stage/"state/last-run.json").read_bytes()).get("collector_replay", [])
            schema_diagnostics=[dict(environment=r["environment"],source=r["url"],issues=r["schema_diagnostics"]) for r in snapshot["entries"] if r.get("schema_diagnostics")]
            wsdl_status=[dict(environment=r["environment"],source=r["url"],status=r.get("status",r.get("retrieval_status","ok")),error=r.get("error")) for r in snapshot["entries"] if r.get("source_type")=="wsdl" or r["kind"]=="wsdl_status"]
            ready_pairs={(r["environment"],r["number"]) for r in current["revisions"] if r["status"]=="matched" and r.get("listed_now", True)}
            registry_pairs={(r["environment"],r["number"]):r for r in current["registry"] if r.get("listed_now", True)}
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
            result = dict(status="ok", collector_source=collector_source(), checked_at=current["checked_at"], new_revisions=added, schema_diagnostics=schema_diagnostics, wsdl_status=wsdl_status, missing_trees=missing,
                          collector_replay=collector_replay,
                          new_schema_revisions=schema_events, new_source_events=source_events,
                          new_messages=[dict(environment=e, number=n) for e,n in new_messages],
                          total_revisions=len(current["revisions"]))
            write(report, encode(result))
            print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        write(report, encode(dict(status="failed", error=str(exc))))
        raise


if __name__ == "__main__":
    run(Path(__file__).resolve().parent)

