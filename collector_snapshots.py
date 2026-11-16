"""Validate Russian collector inputs by replaying their complete WSDL closure offline."""
import copy
import json
import os
import re
from datetime import datetime
from pathlib import Path

from sync import encode, sha
from wsdl_sources import collect, MAX_FILE, MAX_TOTAL, MAX_FILES

DIGEST = re.compile(r"[0-9a-f]{64}")


def make_manifest(library, observed_at):
    library = json.loads(encode(library))
    return dict(format=1, environment=library["environment"], observed_at=observed_at,
                content_id=sha(encode(library)), library=library)


def validate_manifest(manifest, read_object):
    if set(manifest) != {"format", "environment", "observed_at", "content_id", "library"}:
        raise ValueError("Unsupported collector manifest fields")
    if manifest["format"] != 1 or manifest["environment"] not in ("prod", "test"):
        raise ValueError("Unsupported collector format or contour")
    if not datetime.fromisoformat(manifest["observed_at"]).tzinfo:
        raise ValueError("Collector timestamp requires timezone")
    row = manifest["library"]
    if (row["environment"] != manifest["environment"] or
            manifest["content_id"] != sha(encode(row))):
        raise ValueError("Collector metadata or contour mismatch")
    sources = row["source_files"]
    if not 0 < len(sources) <= MAX_FILES or sum(s["bytes"] for s in sources) > MAX_TOTAL:
        raise ValueError("Collector closure exceeds limits")
    resources = {}
    def checked(digest, limit):
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("Invalid object digest")
        raw = read_object(digest)
        if len(raw) > limit or sha(raw) != digest:
            raise ValueError("Collector object digest or size mismatch")
        return raw
    for source in sources:
        raw = checked(source["sha256"], MAX_FILE)
        if len(raw) != source["bytes"] or source["url"] in resources:
            raise ValueError("Duplicate source URL or incorrect size")
        resources[source["url"]] = raw
    archive = checked(row["sha256"], MAX_TOTAL)
    if len(archive) != row["bytes"]:
        raise ValueError("Incorrect library size")
    replay = collect(row["source_url"], manifest["environment"], resources.__getitem__, sha)
    # Older manifests predate explicit import/include metadata. Compare those
    # envelopes in their original format without changing their content ID.
    for expected, actual in zip(row.get("schemas", []), replay.get("schemas", [])):
        for old, new in zip(expected.get("imports", []), actual.get("imports", [])):
            if "kind" not in old:
                new.pop("kind", None)
    if json.loads(encode(replay)) != row:
        raise ValueError("Collector library does not match offline replay")
    return row


def collector_source():
    source = os.environ.get("SEDO_COLLECTOR_SOURCE") or "sourcecraft"
    if source not in ("sourcecraft", "home"):
        raise ValueError("SEDO_COLLECTOR_SOURCE must be sourcecraft or home")
    return source


def _load_pointer(base, environment, pointer):
    from collector_feed import manifest_path
    if not DIGEST.fullmatch(pointer):
        raise ValueError("Invalid current manifest identity")
    manifest = json.loads((base / manifest_path(environment, pointer)).read_bytes())
    if manifest["content_id"] != pointer or manifest["environment"] != environment:
        raise ValueError("Current manifest identity mismatch")
    return validate_manifest(manifest, lambda h: (base / "objects" / h).read_bytes())


def load_library(base, environment):
    base = Path(base)
    if collector_source() == "home":
        current = base / "collector" / environment / "current"
        if not current.is_file():
            raise ValueError("Home collector has no confirmed pointer for " + environment)
        return _load_pointer(base, environment, current.read_text("ascii").strip())
    feed = base / "collector/feed.json"
    if feed.exists():
        from collector_feed import validate_index
        pointer = validate_index(json.loads(feed.read_bytes()))["latest"].get(environment)
        return _load_pointer(base, environment, pointer) if pointer else None
    # Legacy installations predate both the SourceCraft feed and home pointers.
    manifests = []
    for path in (base / "collector" / environment).glob("*.json"):
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Collector manifest exceeds limit")
        manifest = json.loads(path.read_bytes())
        if path.stem != manifest["content_id"] or manifest["environment"] != environment:
            raise ValueError("Collector manifest path mismatch")
        manifests.append(manifest)
    if not manifests:
        return None
    latest = max(manifests, key=lambda m: datetime.fromisoformat(m["observed_at"]))
    return validate_manifest(latest, lambda digest: (base / "objects" / digest).read_bytes())


def pending(root):
    root = Path(root)
    pointer = json.loads((root / "state/current.json").read_bytes())
    snapshot = json.loads((root / "state/snapshots" / (pointer["snapshot"] + ".json")).read_bytes())
    processed = processed_manifests(root / "state")
    if any(manifest["content_id"] not in processed["manifests"].get(manifest["environment"], {})
           for manifest in feed_manifests(root / "state")):
        return True
    for environment in ("prod", "test"):
        row = load_library(root / "state", environment)
        if row and not any(entry == row for entry in snapshot["entries"]):
            return True
    return False


def processed_manifests(base):
    path = base / "collector/processed.json"
    return json.loads(path.read_bytes()) if path.exists() else dict(format=1, manifests={})


def feed_manifests(base):
    """Only the selected, confirmed feed can establish replayable observations."""
    feed = base / "collector/feed.json"
    if collector_source() != "sourcecraft" or not feed.exists():
        return []
    from collector_feed import validate_index, manifest_path
    index = validate_index(json.loads(feed.read_bytes()))
    result = []
    for entry in index["manifests"]:
        env, digest = entry["environment"], entry["content_id"]
        manifest = json.loads((base / manifest_path(env, digest)).read_bytes())
        if manifest["environment"] != env or manifest["content_id"] != digest:
            raise ValueError("Feed manifest identity mismatch")
        validate_manifest(manifest, lambda h: (base / "objects" / h).read_bytes())
        result.append(manifest)
    return sorted(result, key=lambda m: (datetime.fromisoformat(m["observed_at"]), m["environment"], m["content_id"]))


def replay_history(base, current, blob):
    """Reconstruct unprocessed feed observations against confirmed DOCX bytes.

    The caller publishes history, current and this ledger only after all replay
    work succeeds. Orphaned content files from an interruption are harmless:
    confirmation always comes from history/current, and replay is deterministic.
    """
    from sync import encode, match_modules, record_observation, write
    manifests = feed_manifests(base)
    if not manifests:
        return [], None
    processed = processed_manifests(base)
    history_file = base / "history.json"
    history = json.loads(history_file.read_bytes()) if history_file.exists() else []
    pointer = base / "current.json"
    if pointer.exists():
        history.append(json.loads(pointer.read_bytes())["snapshot"])
    history = list(dict.fromkeys(history))
    confirmed = [(key, json.loads((base / "snapshots" / (key + ".json")).read_bytes())) for key in history]
    observed_file = base / "observations.json"
    observations = json.loads(observed_file.read_bytes())["snapshots"] if observed_file.exists() else {}
    replayed = []
    for manifest in manifests:
        env, identity = manifest["environment"], manifest["content_id"]
        records = processed["manifests"].setdefault(env, {})
        if identity in records:
            continue
        row = manifest["library"]
        existing = next((key for key, snapshot in confirmed if row in snapshot.get("entries", [])), None)
        if existing:
            records[identity] = dict(status="recorded", snapshot=existing, observed_at=manifest["observed_at"])
            continue
        stamp = datetime.fromisoformat(manifest["observed_at"])
        candidates = [(datetime.fromisoformat(observations[key]), order, key, snapshot)
                      for order, (key, snapshot) in enumerate(confirmed)
                      if observations.get(key) and datetime.fromisoformat(observations[key]) <= stamp
                      and not snapshot.get("source_type") == "manual"]
        candidate = max(candidates, default=None, key=lambda item: (item[0], item[1]))
        docs = ([r for r in candidate[3].get("entries", [])
                 if r.get("environment") == env and r.get("module_bindings") and r.get("kind") == "docx"]
                if candidate else [])
        if not docs:
            # Today's collection can still establish the active edition, but it
            # cannot retroactively confirm which DOCX was present at observed_at.
            records[identity] = dict(status="unavailable", observed_at=manifest["observed_at"],
                reason="Не найден подтверждённый DOCX на момент наблюдения WSDL; историческую пару восстановить нельзя.")
            if row in current["entries"]:
                records[identity]["current_snapshot"] = sha(encode(current))
            continue
        for document in docs:
            raw = (base / "objects" / document["sha256"]).read_bytes()
            if sha(raw) != document["sha256"]:
                raise ValueError("Historical DOCX digest mismatch")
        source_key, baseline = candidate[2:]
        historical = copy.deepcopy(baseline)
        historical["entries"] = [r for r in historical["entries"]
                                 if not (r.get("environment") == env and
                                         (r.get("source_type") == "wsdl" or r.get("kind") == "wsdl_status"))]
        historical["entries"].append(copy.deepcopy(row))
        for document in historical["entries"]:
            if document.get("environment") == env and document.get("module_bindings"):
                for field in ("module_matches", "wsdl_archives", "schema_diagnostics"):
                    document.pop(field, None)
        # Rebuild only this contour's module matches. Other contour rows already
        # contain their confirmed matches and must not be mutated by the replay.
        selected = [r for r in historical["entries"] if r.get("environment") == env]
        match_modules(base, selected, [row], blob)
        historical["collector_observation"] = dict(environment=env, content_id=identity,
            observed_at=manifest["observed_at"], document_snapshot=source_key)
        payload = encode(historical)
        digest = sha(payload)
        write(base / "snapshots" / (digest + ".json"), payload)
        record_observation(base, digest, manifest["observed_at"])
        replayed.append(digest)
        records[identity] = dict(status="replayed", snapshot=digest, observed_at=manifest["observed_at"], document_snapshot=source_key)
    return replayed, processed


if __name__ == "__main__":
    from auto_update import _run, update_lock
    with update_lock(Path(__file__).resolve().parent) as root:
        if pending(root):
            _run(root)
        else:
            print("No pending collector inputs; validated current snapshots.")
