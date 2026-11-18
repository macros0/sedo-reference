"""Validate Russian collector inputs by replaying their complete WSDL closure offline."""
import copy
import json
import os
import re
from datetime import datetime
from pathlib import Path
from xml.etree.ElementTree import ParseError

from sync import RedirectedBytes, encode, sha
from wsdl_sources import collect, MAX_FILE, MAX_TOTAL, MAX_FILES

DIGEST = re.compile(r"[0-9a-f]{64}")
HOME_POINTER = "current.json"
HOME_POINTER_LIMIT = 256


def parse_home_pointer(raw):
    try:
        row = json.loads(raw)
        if (not isinstance(row, dict) or set(row) != {"format", "content_id", "capture_started_at"}
                or type(row["format"]) is not int or row["format"] != 1
                or not isinstance(row["content_id"], str) or not DIGEST.fullmatch(row["content_id"])):
            raise ValueError("Unsupported pointer fields")
        started = datetime.fromisoformat(row["capture_started_at"])
        if started.tzinfo is None:
            raise ValueError("Pointer timestamp requires timezone")
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid Home current.json") from exc
    return row["content_id"], started


def legacy_home_identity(raw):
    if raw is None:
        return None
    identity = raw.decode("ascii").strip()
    if not DIGEST.fullmatch(identity):
        raise ValueError("Invalid legacy Home current pointer")
    return identity


def read_home_pointer_file(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(HOME_POINTER_LIMIT + 1)
    except FileNotFoundError:
        return None
    if len(raw) > HOME_POINTER_LIMIT:
        raise ValueError("Home pointer exceeds size limit: " + str(path))
    return raw


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
        resources[source["url"]] = RedirectedBytes(raw, source["final_url"]) if "final_url" in source else raw
    archive = checked(row["sha256"], MAX_TOTAL)
    if len(archive) != row["bytes"]:
        raise ValueError("Incorrect library size")
    # Replay requires the exact old archive and schema metadata.
    # Annotation traversal, root indexing and inline include entry points
    # changed independently.
    def matches(annotations, roots, inline_includes):
        replay = collect(row["source_url"], manifest["environment"], resources.__getitem__, sha,
                         legacy_annotations=annotations, legacy_schema_roots=roots,
                         legacy_inline_includes=inline_includes)
        for expected, actual in zip(row.get("schemas", []), replay.get("schemas", [])):
            for old, new in zip(expected.get("imports", []), actual.get("imports", [])):
                if "kind" not in old:
                    new.pop("kind", None)
        return json.loads(encode(replay)) == row
    replayed=False;last_error=None
    from itertools import product
    for inline_includes, annotations, roots in product((False, True), repeat=3):
        try:
            if matches(annotations, roots, inline_includes):return row
            replayed=True
        except ParseError as exc:
            raise ValueError("Malformed XML in collector closure: " + str(exc)) from exc
        except (ValueError,KeyError) as exc:
            last_error=exc
    if not replayed and last_error is not None:raise last_error
    raise ValueError("Collector library does not match offline replay")


def collector_source():
    source = os.environ.get("SEDO_COLLECTOR_SOURCE") or "sourcecraft"
    if source not in ("sourcecraft", "home"):
        raise ValueError("SEDO_COLLECTOR_SOURCE must be sourcecraft or home")
    return source


def observation_path(source, environment, digest):
    return "collector/" + environment + "/" + source + "/" + digest + ".observed-at"


def observation_time(base, source, environment, digest):
    path = base / observation_path(source, environment, digest)
    try:
        stamp = path.read_text("ascii").strip()
        return stamp if datetime.fromisoformat(stamp).tzinfo else None
    except (FileNotFoundError, ValueError):
        return None


def _read_feed_manifest(base, source, environment, digest):
    from collector_feed import manifest_path, read_manifest
    name = manifest_path(environment, digest)
    try:
        return read_manifest(base / name)
    except FileNotFoundError as exc:
        if source == "home":
            raise ValueError(f"Missing Home manifest for {environment} {digest}: {name}. "
                             "Recapture the same inputs or see docs/russian-collector.md.") from exc
        raise


def _load_pointer(base, environment, pointer, *, require_receipt=False):
    if not DIGEST.fullmatch(pointer):
        raise ValueError("Invalid current manifest identity")
    source = collector_source()
    manifest = _read_feed_manifest(base, source, environment, pointer)
    if manifest["content_id"] != pointer or manifest["environment"] != environment:
        raise ValueError("Current manifest identity mismatch")
    stamp = observation_time(base, source, environment, pointer)
    if require_receipt and stamp is None:
        raise ValueError("Missing or invalid Home receipt for " + environment + " " + pointer)
    if stamp is not None:
        manifest["observed_at"] = stamp
    return validate_manifest(manifest, lambda h: (base / "objects" / h).read_bytes())


def _sourcecraft_index(base):
    from collector_feed import validate_index
    feed = base / "collector/feed.json"
    producer = base / "index.json"
    if not feed.exists() and producer.is_file():
        feed = producer
    return validate_index(json.loads(feed.read_bytes())) if feed.exists() else None


def load_library(base, environment):
    from collector_feed import read_manifest
    base = Path(base)
    if collector_source() == "home":
        directory = base / "collector" / environment
        current = read_home_pointer_file(directory / HOME_POINTER)
        if current is not None:
            identity, _ = parse_home_pointer(current)
            return _load_pointer(base, environment, identity, require_receipt=True)
        identity = legacy_home_identity(read_home_pointer_file(directory / "current"))
        if identity is None:
            raise ValueError("Home collector has no confirmed pointer for " + environment)
        return _load_pointer(base, environment, identity)
    index = _sourcecraft_index(base)
    if index is not None:
        pointer = index["latest"].get(environment)
        return _load_pointer(base, environment, pointer) if pointer else None
    # Legacy installations predate both the SourceCraft feed and home pointers.
    collector = base / "collector"
    for env in ("prod", "test"):
        folder = collector / env
        if (any((folder / name).exists() for name in (HOME_POINTER, "current", "current-observed-at"))
                or any((folder / "home").glob("*.observed-at"))
                or any((folder / "home").glob("*.pending-at"))
                or any((folder / "sourcecraft").glob("*.observed-at"))):
            raise ValueError("Missing SourceCraft feed.json; import the confirmed feed before collecting")
    manifests = []
    for path in (base / "collector" / environment).glob("*.json"):
        manifest = read_manifest(path)
        if path.stem != manifest["content_id"] or manifest["environment"] != environment:
            raise ValueError("Collector manifest path mismatch")
        manifests.append(manifest)
    if not manifests:
        return None
    latest = max(manifests, key=lambda m: datetime.fromisoformat(m["observed_at"]))
    return validate_manifest(latest, lambda digest: (base / "objects" / digest).read_bytes())


def pending(root):
    return bool(pending_work(root))


def pending_work(root):
    root = Path(root)
    pointer = json.loads((root / "state/current.json").read_bytes())
    snapshot = json.loads((root / "state/snapshots" / (pointer["snapshot"] + ".json")).read_bytes())
    processed = processed_manifests(root / "state")
    for environment in ("prod", "test"):
        row = load_library(root / "state", environment)
        if row and row not in snapshot["entries"]:
            return "collection"
    context = None
    for manifest in feed_manifests(root / "state"):
        record = selected_records(processed).get(manifest["environment"], {}).get(manifest["content_id"], {})
        if record.get("observed_at") != manifest["observed_at"]:
            return "history"
        if record.get("status") in ("unavailable", "replayed"):
            if context is None:
                context = replay_context(root / "state")
            _, proof = replay_candidate(manifest, *context)
            if record.get("replay_proof") != proof:
                return "history"
    return None


def processed_manifests(base):
    path = base / "collector/processed.json"
    return json.loads(path.read_bytes()) if path.exists() else dict(format=1, manifests={})


def selected_records(processed):
    # The original manifests ledger belongs to SourceCraft. A matching home
    # content ID is independent evidence and must be processed independently.
    if collector_source() == "sourcecraft":
        return processed.setdefault("manifests", {})
    return processed.setdefault("sources", {}).setdefault("home", {})


def feed_manifests(base):
    """Only the selected, confirmed feed can establish replayable observations."""
    source = collector_source()
    if source == "home":
        entries = [dict(environment=env, content_id=path.name.removesuffix(".observed-at"))
                   for env in ("prod", "test")
                   for path in sorted((base / "collector" / env / "home").glob("*.observed-at"))]
    else:
        index = _sourcecraft_index(base)
        entries = index["manifests"] if index is not None else []
    result = []
    for entry in entries:
        env, digest = entry["environment"], entry["content_id"]
        if not DIGEST.fullmatch(digest):
            raise ValueError("Invalid collector receipt identity")
        stamp = observation_time(base, source, env, digest)
        if stamp is None:
            if source == 'home':
                raise ValueError('Invalid home receipt: ' + observation_path(source, env, digest))
            # The legacy envelope may have been written by home. Its library
            # can still be used today, but historical replay needs the time
            # confirmed by the next SourceCraft pull.
            continue
        manifest = _read_feed_manifest(base, source, env, digest)
        manifest["observed_at"] = stamp
        if manifest["environment"] != env or manifest["content_id"] != digest:
            raise ValueError("Feed manifest identity mismatch")
        validate_manifest(manifest, lambda h: (base / "objects" / h).read_bytes())
        result.append(manifest)
    return sorted(result, key=lambda m: (datetime.fromisoformat(m["observed_at"]), m["environment"], m["content_id"]))


def document_snapshot_at(stamp, environment, transitions, confirmed, first_seen):
    for event in reversed(transitions):
        if datetime.fromisoformat(event["observed_at"]) <= stamp:
            key = event["snapshot"]
            return key, confirmed[key]

    # Legacy stores kept the first observation only. A later return to an old
    # digest was invisible. An exact observation or one unambiguous contour
    # state among the possible observations can still establish a baseline.
    real = {key: snapshot for key, snapshot in confirmed.items()
            if snapshot.get("source_type") != "manual" and "collector_observation" not in snapshot}
    known = [(datetime.fromisoformat(first_seen[key]), key) for key in real if first_seen.get(key)]
    candidates = sorted(item for item in known if item[0] <= stamp)
    exact = [item for item in candidates if item[0] == stamp]
    if exact:
        candidates = exact
    elif len(known) != len(real):
        return None
    if not candidates:
        return None
    states = {sha(encode(matching_inputs(real[key], environment))) for _, key in candidates}
    if len(states) != 1:
        return None
    key = candidates[-1][1]
    return key, real[key]


def matching_inputs(snapshot, environment):
    """Inputs that can change document/schema matching, excluding derived WSDL."""
    rows = []
    for row in snapshot.get("entries", []):
        if (row.get("environment") != environment or row.get("kind") not in ("docx", "zip", "xsd")
                or row.get("source_type") in ("wsdl", "wsdl_bridge")):
            continue
        rows.append({key: value for key, value in row.items()
                     if key not in ("module_matches", "wsdl_archives", "schema_diagnostics")})
    return sorted(rows, key=lambda row: encode(row))


def replay_context(base):
    history_file = base / "history.json"
    history = json.loads(history_file.read_bytes()) if history_file.exists() else []
    pointer = base / "current.json"
    active = json.loads(pointer.read_bytes()) if pointer.exists() else {}
    if active:
        history.append(active["snapshot"])
    confirmed = {key: json.loads((base / "snapshots" / (key + ".json")).read_bytes())
                 for key in dict.fromkeys(history)}
    observed_file = base / "observations.json"
    observations = json.loads(observed_file.read_bytes())["snapshots"] if observed_file.exists() else {}
    return active, confirmed, observations


def replay_candidate(manifest, active, confirmed, observations):
    env = manifest["environment"]
    stamp = datetime.fromisoformat(manifest["observed_at"])
    candidate = document_snapshot_at(stamp, env, active.get("transitions", []), confirmed, observations)
    inputs = matching_inputs(candidate[1], env) if candidate else None
    recorded = bool(candidate and manifest["library"] in candidate[1].get("entries", []))
    return candidate, sha(encode(dict(algorithm=4, inputs=inputs, recorded=recorded)))


def unmatched_module_bindings(entries):
    missing = []
    for doc in entries:
        matches = doc.get("module_matches", [])
        for binding in doc.get("module_bindings", []):
            number, root = binding["number"], binding["root"]
            namespace = binding.get("namespace")
            if any(m["number"] == number and m["root"] == root
                   and (not namespace or m.get("namespace") == namespace) for m in matches):
                continue
            reasons = dict.fromkeys(issue["reason"] for issue in doc.get("schema_diagnostics", [])
                                    if issue.get("number") == number and issue.get("root") == root)
            missing.append(dict(number=number, root=root, namespace=namespace, document_sha256=doc["sha256"],
                                revision_id=None, reason="; ".join(reasons) or "Не найдена полная XSD для сообщения модуля."))
    return missing


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
    active, confirmed, observations = replay_context(base)
    replayed = []
    for manifest in manifests:
        env, identity = manifest["environment"], manifest["content_id"]
        records = selected_records(processed).setdefault(env, {})
        previous_record = records.get(identity)
        candidate, proof = replay_candidate(manifest, active, confirmed, observations)
        if (previous_record and previous_record.get("observed_at") == manifest["observed_at"]
                and (previous_record.get("status") == "recorded" or previous_record.get("replay_proof") == proof)):
            continue
        row = manifest["library"]
        if candidate and row in candidate[1].get("entries", []):
            records[identity] = dict(status="recorded", snapshot=candidate[0], observed_at=manifest["observed_at"])
            continue
        from catalog import derive, individual_number
        docs = ([r for r in candidate[1].get("entries", [])
                 if r.get("environment") == env and r.get("kind") == "docx" and r.get("sha256")
                 and (r.get("module_bindings") or individual_number(r,legacy=candidate[1].get('format',7)<7))]
                if candidate else [])
        if not docs:
            # Today's collection can still establish the active edition, but it
            # cannot retroactively confirm which DOCX was present at observed_at.
            records[identity] = dict(status="unavailable", observed_at=manifest["observed_at"], replay_proof=proof,
                reason="Недостаточно подтверждённых данных, чтобы однозначно установить DOCX на момент наблюдения WSDL.")
            if row in current["entries"]:
                records[identity]["current_snapshot"] = sha(encode(current))
            continue
        for document in docs:
            raw = (base / "objects" / document["sha256"]).read_bytes()
            if sha(raw) != document["sha256"]:
                raise ValueError("Historical DOCX digest mismatch")
        source_key, baseline = candidate
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
        historical_format = historical.get('format', 7)
        match_modules(base, selected, [row], blob, legacy_schema_roots=historical_format < 7)
        historical["collector_observation"] = dict(environment=env, content_id=identity,
            observed_at=manifest["observed_at"], document_snapshot=source_key)
        # An intermediate WSDL can omit one volume's root while the remaining
        # editions are usable. Preserve its DOCX and explain the missing tree.
        revisions = derive(dict(format=historical_format, entries=selected))["revisions"]
        unmatched = [dict(number=r["number"], revision_id=r["revision_id"],
                          document_sha256=r["document_sha256"], root=r["root"],
                          reason="В исторической библиотеке не найдена однозначная полная XSD для тома.")
                     for r in revisions if r["status"] != "matched"]
        unmatched.extend(unmatched_module_bindings(selected))
        if unmatched and not any(r["status"] == "matched" for r in revisions):
            records[identity] = dict(status="unavailable", observed_at=manifest["observed_at"],
                document_snapshot=source_key, replay_proof=proof, unmatched_documents=unmatched,
                reason="Не удалось подтвердить ни одной схемы сообщения на дату наблюдения.")
            continue
        if unmatched:
            historical["collector_observation"]["unmatched_documents"] = unmatched
        payload = encode(historical)
        digest = sha(payload)
        write(base / "snapshots" / (digest + ".json"), payload)
        record_observation(base, digest, manifest["observed_at"])
        replayed.append(digest)
        records[identity] = dict(status="replayed", snapshot=digest, observed_at=manifest["observed_at"],
                                 document_snapshot=source_key, replay_proof=proof)
        if unmatched:
            records[identity]["unmatched_documents"] = unmatched
    return replayed, processed


def apply_pending(root, *, offline=False):
    from auto_update import _run, active_inventory, update_lock
    with update_lock(root) as root:
        work = pending_work(root)
        if work == "collection" and offline:
            from unittest.mock import patch
            from verify import fetch_archived
            state = root / "state"
            with patch("sync.get", fetch_archived(state, active_inventory(state))):
                _run(root)
        elif work:
            _run(root, history_only=(work == "history"))
        else:
            print("No pending collector inputs; validated current snapshots.")

def replay_archived_inputs(base):
    """Apply historical feed observations inside the caller's staged generation."""
    from sync import write
    pointer = json.loads((base / "current.json").read_bytes())
    current = json.loads((base / "snapshots" / (pointer["snapshot"] + ".json")).read_bytes())
    def blob(raw):
        digest = sha(raw)
        target = base / "objects" / digest
        if not target.is_file() or target.read_bytes() != raw:
            write(target, raw)
        return digest
    keys, ledger = replay_history(base, current, blob)
    if ledger is None:
        return []
    path = base / "history.json"
    history = json.loads(path.read_bytes()) if path.exists() else [pointer["snapshot"]]
    write(path, encode(list(dict.fromkeys([*history, *keys]))))
    write(base / "collector/processed.json", encode(ledger))
    return [dict(environment=env, content_id=key, **record)
            for env, records in selected_records(ledger).items() for key, record in records.items()]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Preview a PR with archived SFR responses")
    apply_pending(Path(__file__).resolve().parent, offline=parser.parse_args().offline)
