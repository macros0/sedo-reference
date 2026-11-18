"""Collect and verify a candidate before installing its inventory and field trees."""
import argparse
import filecmp
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from auto_update import active_inventory, catalog_locks, validate_inventory, validate_publications, validate_saved_history
from catalog import derive, export
from fields import build
from sync import CONFIRMATION_FILES, confirmation_lock, encode, recover_confirmation, sync, write


STATE_INPUTS = ("objects", "snapshots", "collector", "index.json", "inbox.json", "last-run.json", "archived-reviews.json",
                *(name for name in CONFIRMATION_FILES if "/" not in name))


def copy_inputs(source, target, names):
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = source / name
        if path.is_dir():
            shutil.copytree(path, target / name)
        elif path.is_file():
            shutil.copy2(path, target / name)


def recover_catalog(state, out):
    recovery = state / ".catalog-recovery"
    if not recovery.is_dir():
        return
    if recovery.is_symlink() or getattr(recovery, "is_junction", lambda: False)():
        raise ValueError("Ссылка на каталог восстановления " + str(recovery))
    from build_site import cleanup_site_recovery
    if (recovery / "cleanup.json").is_file():
        cleanup_site_recovery(recovery)
        return
    allowed = {"journal.json", "journal.partial", "cleanup.partial", "committed", "preparing", "state", "data"}
    for entry in recovery.iterdir():
        if entry.name not in allowed or entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            raise ValueError("Неизвестное содержимое каталога восстановления " + str(recovery))
    if any(recovery.iterdir()) and not any((recovery / name).is_file() for name in ("journal.json", "committed", "preparing")):
        raise ValueError("Отсутствуют признаки транзакции восстановления " + str(recovery))
    for role, names in (("state", STATE_INPUTS), ("data", ("catalog.json", "fields"))):
        directory = recovery / role
        if directory.exists():
            for entry in directory.iterdir():
                if entry.name not in names:
                    raise ValueError("Неизвестное содержимое каталога восстановления " + str(directory))
    for entry in recovery.rglob("*"):
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            raise ValueError("Ссылка внутри каталога восстановления " + str(recovery))
    journal = recovery / "journal.json"
    if journal.exists() and not (recovery / "committed").exists():
        saved = json.loads(journal.read_bytes())
        if saved["output"] != str(out.resolve()):
            raise ValueError("Повторите update_catalog.py с исходными --state и --out для восстановления")
        for role, relative, existed in saved["files"]:
            name = PurePosixPath(relative)
            if (role not in ("state", "data") or name.is_absolute() or not name.parts or
                    any(p in ("", ".", "..") or ":" in p or "\\" in p for p in name.parts) or
                    name.parts[0] not in (STATE_INPUTS if role == "state" else ("catalog.json", "fields"))):
                raise ValueError("Некорректный путь в журнале восстановления каталога")
            target = (state if role == "state" else out) / name
            if existed:
                write(target, (recovery / role / name).read_bytes())
            else:
                target.unlink(missing_ok=True)
    # The durable manifest survives removal of journal/preparing/committed and
    # prevents a resumed cleanup from rolling back an installed generation.
    cleanup_site_recovery(recovery)


def build_candidate(state, data):
    index = data / "catalog.json"
    field_index = data / "fields/index.json"
    prior = json.loads(index.read_bytes()) if index.exists() else {}
    old_fields = json.loads(field_index.read_bytes()) if field_index.exists() else []
    prior_ids = {row["revision_id"] for row in prior.get("revisions", [])}
    previously_rejected = {row["revision_id"] for row in old_fields if row["status"] == "review"
                           and not (data / "fields" / (row["revision_id"] + ".json")).exists()} & prior_ids
    export(state, index)
    build(index, state, data / "fields")
    statuses = json.loads(field_index.read_bytes())
    if previously_rejected and index.exists():
        current = json.loads(index.read_bytes())
        active = {r["revision_id"] for r in current["revisions"] if r.get("listed_now")}
        rejected = {r["revision_id"]: r.get("error") or "Полная XSD не была сопоставлена."
                    for r in statuses if r["status"] == "review" and r["revision_id"] in previously_rejected - active}
        current_ids = {r["revision_id"] for r in current["revisions"]}
        # Catalog export already converts historical unmatched rows to document
        # sources. Record that intentional archival before validating retention.
        rejected.update({r["revision_id"]: "Полная XSD не была сопоставлена."
                         for r in prior.get("revisions", []) if r.get("status") == "review"
                         and r["revision_id"] in previously_rejected - active - current_ids})
        if rejected:
            archive = state / "archived-reviews.json"
            evidence = json.loads(archive.read_bytes()) if archive.exists() else {}
            for key in json.loads((state / "history.json").read_bytes()):
                snapshot = json.loads((state / "snapshots" / (key + ".json")).read_bytes())
                for row in derive(snapshot)["revisions"]:
                    rid = row["revision_id"]
                    if rid in rejected:
                        evidence.setdefault(key, {})[rid] = rejected[rid]
            write(archive, encode(evidence))
            export(state, index)
            build(index, state, data / "fields")
            statuses = json.loads(field_index.read_bytes())
    failed = [row for row in statuses if row["status"] not in ("ready", "document_only")]
    if failed:
        raise SystemExit("Нужна проверка новых схем: " + ", ".join(
            f"{row['number']}/{row['environment']}: {row.get('error', row['status'])}" for row in failed))


def install(state, out, stage):
    """Back up changed files before the first replacement, including across volumes."""
    changed = []
    for role, target in (("state", state), ("data", out)):
        for path in sorted((stage / role).rglob("*")):
            if not path.is_file() or path.name.endswith(".lock"):
                continue
            relative = path.relative_to(stage / role)
            dest = target / relative
            if not dest.is_file() or not filecmp.cmp(path, dest, shallow=False):
                changed.append((role, relative.as_posix(), dest.is_file()))
    if not changed:
        return
    state.mkdir(parents=True, exist_ok=True)
    recovery = state / ".catalog-recovery"
    recovery.mkdir()
    (recovery / "preparing").touch()
    try:
        for role, relative, existed in changed:
            if existed:
                backup = recovery / role / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile((state if role == "state" else out) / relative, backup)
        write(recovery / "journal.json", encode(dict(output=str(out.resolve()), files=changed)))
        for role, relative, _ in changed:
            target = (state if role == "state" else out) / relative
            write(target, (stage / role / relative).read_bytes())
        (recovery / "committed").touch()
    finally:
        recover_catalog(state, out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("state"))
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    state, out = args.state.resolve(), args.out.resolve()
    with catalog_locks(state, out, catalog_pair=(state, out)):
        state.mkdir(parents=True, exist_ok=True)
        with confirmation_lock(state), tempfile.TemporaryDirectory(prefix="sedo-catalog-") as tmp:
            recover_catalog(state, out)
            recover_confirmation(state)
            stage = Path(tmp)
            candidate, data = stage / "state", stage / "data"
            copy_inputs(state, candidate, STATE_INPUTS)
            copy_inputs(out, data, ("catalog.json", "fields"))
            if not args.offline:
                sync(candidate)
                if (state / 'current.json').is_file():
                    validate_inventory(active_inventory(state), active_inventory(candidate))
            prior_index = out / "catalog.json"
            previous = json.loads(prior_index.read_bytes()) if prior_index.is_file() else None
            old_status_path = out / "fields/index.json"
            rejected = {r["revision_id"] for r in json.loads(old_status_path.read_bytes())
                        if r["status"] == "review" and not (out / "fields" / (r["revision_id"] + ".json")).exists()} if old_status_path.is_file() else set()
            build_candidate(candidate, data)
            if previous is not None:
                archive = candidate / "archived-reviews.json"
                evidence = json.loads(archive.read_bytes()) if archive.is_file() else {}
                allowed = {rid for rows in evidence.values() for rid in rows} & rejected
                validate_saved_history(previous, json.loads((data / "catalog.json").read_bytes()), allowed)
            if previous is not None:
                validate_publications(json.loads((out / 'catalog.json').read_bytes()),
                                      json.loads((data / 'catalog.json').read_bytes()))
            install(state, out, stage)


if __name__ == "__main__":
    main()
