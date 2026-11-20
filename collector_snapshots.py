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
MODULE_INPUT_VERSION = 1
MODULE_DECISION_VERSION = 2


def _latest_module_decisions():
    from module_xsd import MODULE_MATCH_VERSION, QUOTATION_SNAPSHOT_FORMAT
    return MODULE_MATCH_VERSION >= QUOTATION_SNAPSHOT_FORMAT


def _stale_module_decision(record):
    return _latest_module_decisions() and record.get('module_decision_version', 0) < MODULE_DECISION_VERSION


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
    # Annotation traversal, root indexing, inline includes and the archive
    # layout changed independently. Try the current behavior first.
    legacy_source_paths = any(s['kind'] == 'xsd' and 'archive_path' not in s for s in sources)
    def matches(annotations, roots, inline_includes, wsdl_layout):
        replay = collect(row["source_url"], manifest["environment"], resources.__getitem__, sha,
                         legacy_annotations=annotations, legacy_schema_roots=roots,
                         legacy_inline_includes=inline_includes, legacy_wsdl_layout=wsdl_layout,
                         legacy_source_paths=legacy_source_paths)
        for expected, actual in zip(row.get("schemas", []), replay.get("schemas", [])):
            for old, new in zip(expected.get("imports", []), actual.get("imports", [])):
                if "kind" not in old:
                    new.pop("kind", None)
        return json.loads(encode(replay)) == row
    replayed=False;last_error=None
    from itertools import product
    for wsdl_layout, inline_includes, annotations, roots in product((False, True), repeat=4):
        try:
            if matches(annotations, roots, inline_includes, wsdl_layout):return row
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
    archive_graph = None
    for manifest in feed_manifests(root / "state"):
        record = selected_records(processed).get(manifest["environment"], {}).get(manifest["content_id"], {})
        if record.get("observed_at") != manifest["observed_at"]:
            return "history"
        if record.get("status") in ("recorded", "unavailable", "replayed"):
            if context is None:
                from catalog import archived_graph_reader
                active, confirmed, observations = replay_context(root / "state")
                context = active, confirmed, observations, confirmed_binding_index(active, confirmed, observations)
                archive_graph = archived_graph_reader(root / 'state')
            candidate, proof, bindings = replay_candidate(manifest, *context)
            refresh, _, _, _ = _replay_changes(manifest, candidate, proof, bindings, record, context[1], archive_graph)
            if refresh:
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
                     if key not in ("module_matches", "wsdl_archives", "schema_diagnostics", "wsdl_schema_matches")})
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


def _index_named_bindings(index, snapshot, stamp, environment=None, documents=None):
    from catalog import document_identity, named_binding_rows
    for row in named_binding_rows(snapshot):
        key = document_identity(row)
        if environment is not None and (key[0] != environment or key not in documents):
            continue
        index.setdefault(key, {})[stamp, environment is not None] = row


def confirmed_binding_index(active, confirmed, observations):
    """Index exact bindings in confirmed live and replayed inventories."""
    from catalog import individual_documents
    bindings = {}
    transitions = active.get('transitions', [])
    transition_keys = {event['snapshot'] for event in transitions}
    for key, snapshot in confirmed.items():
        if replay := snapshot.get('collector_observation'):
            env = replay['environment']
            stamp = datetime.fromisoformat(replay['observed_at'])
            baseline = document_snapshot_at(stamp, env, transitions, confirmed, observations)
            if baseline:
                documents = {identity for identity, _ in individual_documents(baseline[1]) if identity[0] == env}
                _index_named_bindings(bindings, snapshot, stamp, env, documents)
        elif key not in transition_keys:
            stamp = datetime.fromisoformat(observations[key]) if observations.get(key) else None
            _index_named_bindings(bindings, snapshot, stamp)
    for event in transitions:
        _index_named_bindings(bindings, confirmed[event['snapshot']], datetime.fromisoformat(event['observed_at']))
    return bindings


def applicable_bindings(index, documents, *, at=None):
    bindings = {}
    for key in documents:
        candidates = index.get(key, {})
        times = [(stamp, replay) for stamp, replay in candidates if at is None or
                 (stamp is not None and (stamp < at or (stamp == at and not replay)))]
        if times:
            # Live transitions take precedence over replay at the same time;
            # unknown legacy dates cannot establish a historical binding.
            chosen = max(times, key=lambda item: (item[0] is not None, item[0], not item[1]))
            bindings[key] = candidates[chosen]
    return bindings


def replay_candidate(manifest, active, confirmed, observations, index):
    from catalog import individual_documents
    env = manifest["environment"]
    stamp = datetime.fromisoformat(manifest["observed_at"])
    candidate = document_snapshot_at(stamp, env, active.get("transitions", []), confirmed, observations)
    inputs = matching_inputs(candidate[1], env) if candidate else None
    recorded = bool(candidate and manifest["library"] in candidate[1].get("entries", []))
    documents = {key for key, _ in individual_documents(candidate[1]) if key[0] == env} if candidate else set()
    bindings = applicable_bindings(index, documents, at=stamp)
    return candidate, sha(encode(dict(algorithm=4, inputs=inputs, recorded=recorded))), bindings


def rejected_replay_bindings(base):
    return processed_manifests(base).get('rejected_bindings', {})


class ModuleReplayOrigins:
    """Resolve copied module rows through confirmed replay receipts and inventories."""

    def __init__(self, confirmed, processed, archive_graph):
        self.confirmed = confirmed
        self.archive_graph = archive_graph
        self.receipts = {}
        self.attempts = {}
        self.rows = {}
        self.origins = {}
        ledgers = {'sourcecraft': processed.get('manifests', {}), **processed.get('sources', {})}
        receipts = [dict(source=source, environment=env, content_id=identity, **row)
                    for source, records in ledgers.items() for env, by_id in records.items()
                    for identity, row in by_id.items()]
        for record in [*processed.get('matcher_attempts', ()), *receipts]:
            if record.get('status') == 'replayed' and record.get('snapshot') in confirmed:
                self.receipts.setdefault(record['snapshot'], []).append(record)
            identity = self._receipt_identity(record)
            self.attempts.setdefault(identity, []).append(record)

    @staticmethod
    def _receipt_identity(receipt):
        return tuple(receipt.get(field) for field in ('source', 'environment', 'content_id', 'observed_at', 'replay_proof'))

    def module_rows(self, key):
        from catalog import derive
        if key not in self.rows:
            self.rows[key] = [r for r in derive(self.confirmed[key], archive_graph=self.archive_graph)['revisions']
                              if r.get('source_type') == 'module' and r['status'] == 'matched']
        return self.rows[key]

    def _matching_row(self, key, observation, source):
        from catalog import binding_observation_identity
        # The copy has the very same DOCX, archive entry and source chain.
        # Compare the recorded source as well as the resolved QName and graph.
        fields = ('archive_url', 'archive_sha256', 'schema_path', 'schema_source_type',
                  'schema_source_url', 'schema_sources', 'dependencies')
        return next((row for row in self.module_rows(key) if binding_observation_identity(row) == observation
                     and all(row.get(field) == source.get(field) for field in fields)), None)

    def origin(self, key, row, visited=()):
        from catalog import binding_observation_identity
        observation = binding_observation_identity(row)
        address = key, observation
        if address in self.origins:
            return self.origins[address]
        replay = self.confirmed[key].get('collector_observation')
        if replay is None:
            return key
        if key in visited:
            return None
        receipts = [r for r in self.receipts.get(key, ()) if all(r.get(field) == replay.get(field)
                    for field in ('environment', 'content_id', 'observed_at', 'document_snapshot'))
                    and r['source'] == replay.get('source', r['source'])]
        if not receipts:
            return None
        if 'module_checked' in replay:
            if list(observation) in replay['module_checked']:
                result = key
            else:
                parent = next((entry['snapshot'] for entry in replay.get('module_copies', ())
                               if entry['observation'] == list(observation)), None)
                result = self._copied_origin(parent, observation, row, (*visited, key))
        else:
            candidates = {self._legacy_origin(receipt, replay, key, observation, row, (*visited, key))
                          for receipt in receipts}
            result = candidates.pop() if len(candidates) == 1 else None
        self.origins[address] = result
        return result

    def _copied_origin(self, parent, observation, row, visited):
        if parent in self.confirmed and (original := self._matching_row(parent, observation, row)):
            return self.origin(parent, original, visited)

    def _legacy_origin(self, receipt, replay, key, observation, row, visited):
        same_environment = replay['environment'] == row['environment']
        attempts = self.attempts[self._receipt_identity(receipt)]
        index = next(i for i, attempt in enumerate(attempts) if attempt is receipt)
        prior = next((attempt for attempt in reversed(attempts[:index])
                      if attempt.get('snapshot') != key and attempt.get('snapshot') in self.confirmed), None)
        if prior:
            parent = prior['snapshot']
            if self._matching_row(parent, observation, row):
                # Legacy partial WSDL retries could leave module decisions
                # untouched even in their own contour. A changed matcher
                # version or extraction version proves a module retry.
                retried = any(receipt.get(field, 0) > prior.get(field, 0)
                              for field in ('module_match_version', 'module_input_version'))
                if same_environment and retried:
                    from catalog import declaration_identity, module_declaration
                    snapshot = self.confirmed[parent]
                    document = _module_documents(snapshot)[_module_document_address(row)]
                    declaration = declaration_identity(module_declaration(document, row))
                    attempted = _module_bindings(snapshot, row['environment'],
                                                 attempted_only=prior['status'] == 'recorded', reextract=True)
                    if any(declaration_identity(item) == declaration
                           for item in attempted.get(_module_document_address(row)[1:], ())):
                        return key
                return self._copied_origin(parent, observation, row, visited)
        if same_environment:
            return key
        return self._copied_origin(replay.get('document_snapshot'), observation, row, visited)


def _binding_proof(bindings, documents):
    fields = ('root', 'namespace', 'schema_docx_location', 'schema_docx_path', 'schema_path', 'resolved_schema_graph')
    return sha(encode([(key, [bindings[key].get(field) for field in fields])
                       for key in sorted(documents) if key in bindings]))


def _binding_recheck(record, confirmed, bindings, archive_graph):
    if proof := record.get('binding_proof'):
        if proof['digest'] != _binding_proof(bindings, map(tuple, proof['documents'])):
            return True
    prior = confirmed.get(record.get('snapshot')) if record.get('status') == 'replayed' else None
    return bool(bindings and prior is not None
                and next(_replayed_binding_losses(prior, bindings, archive_graph=archive_graph), None))


def _replayed_binding_losses(snapshot, bindings, *, archive_graph=None):
    from catalog import derive, document_identity, preserves_wsdl_binding
    resolutions = {}
    rows = derive(snapshot, resolutions=resolutions, archive_graph=archive_graph)['revisions']
    for row in rows:
        identity = document_identity(row)
        binding = bindings.get(identity)
        replacement = resolutions.get((identity, row['document_url']), row)
        if (binding and row['status'] == 'matched' and row.get('source_type') != 'module'
                and not preserves_wsdl_binding(binding, replacement)):
            yield row


def _wsdl_retry_documents(manifest, candidate, record):
    from catalog import INDIVIDUAL_WSDL_MATCH_VERSION, individual_number, named_wsdl_members
    if (candidate is None or record.get('status') not in ('unavailable', 'replayed')
            or record.get('wsdl_match_version', 0) >= INDIVIDUAL_WSDL_MATCH_VERSION):
        return set()
    unmatched = {(row['number'], row.get('document_sha256')) for row in record.get('unmatched_documents', [])}
    if not unmatched:
        return set()
    snapshot = candidate[1]
    environment = manifest['environment']
    legacy = snapshot.get('format', 7) < 7
    return {(row['url'], row['sha256']) for row in snapshot['entries'] if row['environment'] == environment
            and (individual_number(row, legacy=legacy), row.get('sha256')) in unmatched
            and named_wsdl_members(row, (manifest['library'],))}


def _module_bindings(snapshot, environment, *, attempted_only=False, reextract=False):
    from catalog import declaration_identity, individual_number, module_declaration

    bindings = {}
    for doc in snapshot['entries']:
        if doc['environment'] != environment or doc['kind'] != 'docx' or not doc.get('sha256'):
            continue
        named = (individual_number(doc, legacy=snapshot.get('format', 7) < 7)
                 if doc.get('wsdl_schema_matches') else None)
        declared = doc.get('module_bindings', ())
        selected = [binding for binding in declared if binding['number'] != named]
        if attempted_only:
            matched = {declaration_identity(module_declaration(doc, row)) for row in doc.get('module_matches', ())}
            issues = doc.get('schema_diagnostics', ())
            attempted_numbers = {item[0] for item in matched} | {item.get('number') for item in issues}
            selected = [binding for binding in selected if binding['number'] in attempted_numbers]
        # Older inventories can list a message without recognizing its root,
        # including alongside successfully recognized messages in that DOCX.
        if reextract and snapshot.get('format', 1) >= 2:
            known_numbers = {b['number'] for b in declared} | {named}
            selected.extend(dict(number=item['number'], root=None) for item in doc.get('message_types', ())
                            if item['number'] not in known_numbers)
        if selected:
            bindings[doc['url'], doc['sha256']] = selected
    return bindings


def _issue_applies(issue, binding):
    return (issue.get('number') == binding['number'] and issue.get('root', binding.get('root')) == binding.get('root')
            and ('namespace' not in issue or issue['namespace'] == binding.get('namespace')))


def _replay_changes(manifest, candidate, proof, bindings, record, confirmed, archive_graph):
    """Apply the same receipt and per-input retry rules in the queue and replay."""
    from module_xsd import MODULE_MATCH_VERSION, QUOTATION_SNAPSHOT_FORMAT

    if not record or record.get('observed_at') != manifest['observed_at']:
        return True, False, set(), {}
    recorded = record.get('status') == 'recorded'
    prior = confirmed.get(record.get('snapshot'))
    stale_decision = _stale_module_decision(record)
    same_proof = (record['replay_proof'] == proof if 'replay_proof' in record else
                  recorded and candidate is not None and record['snapshot'] == candidate[0])
    recorded_version = prior.get('format', 1) if recorded and prior else 0
    checked_version = record.get('module_match_version', recorded_version)
    stale_inputs = (candidate and MODULE_MATCH_VERSION >= QUOTATION_SNAPSHOT_FORMAT
                    and candidate[1].get('format', 1) < MODULE_MATCH_VERSION
                    and (not same_proof or record.get('module_input_version', 0) < MODULE_INPUT_VERSION))
    retry_modules = (_module_bindings(candidate[1], manifest['environment'], attempted_only=recorded,
                                      reextract=bool(stale_inputs)) if candidate and
                     (not same_proof or checked_version < MODULE_MATCH_VERSION or stale_inputs or stale_decision) else {})
    if recorded:
        return bool(retry_modules or stale_decision), bool(same_proof and prior and retry_modules), set(), retry_modules
    binding_changed = same_proof and _binding_recheck(record, confirmed, bindings, archive_graph)
    partial = same_proof and not binding_changed and record.get('status') == 'replayed'
    if partial and retry_modules and candidate and record.get('document_snapshot') != candidate[0]:
        baseline = confirmed.get(record.get('document_snapshot'))
        env = manifest['environment']
        partial = baseline is not None and matching_inputs(baseline, env) == matching_inputs(candidate[1], env)
    retry_wsdl = _wsdl_retry_documents(manifest, candidate, record) if same_proof and not binding_changed else set()
    return bool(not same_proof or binding_changed or retry_wsdl or retry_modules or stale_decision), partial, retry_wsdl, retry_modules


def _inspect_historical_module(base, document, blob, cache):
    from sync import inspect_download

    digest = document['sha256']
    if digest not in cache:
        raw = (base / 'objects' / digest).read_bytes()
        if sha(raw) != digest:
            raise ValueError('Historical DOCX digest mismatch')
        metadata, packages = inspect_download(raw, 'docx')
        for package in packages:
            blob(package['content'])
        cache[digest] = metadata
    metadata = cache[digest]
    for field in ('message_types', 'module_bindings', 'embedded_archives'):
        document.pop(field, None)
        if field in metadata:
            document[field] = copy.deepcopy(metadata[field])
    return metadata.get('schema_diagnostics', ())


def _retry_module_matches(base, selected, row, blob, bindings, cache, *, reextract, legacy_schema_roots):
    from catalog import declaration_identity, individual_number, module_declaration
    from sync import match_modules

    # Re-evaluate the selected module bindings against the confirmed inputs.
    # Individual matches and bindings outside this set retain their evidence.
    inputs = [{key: value for key, value in doc.items() if key != 'module_bindings'} for doc in selected]
    retrying, attempted = [], {}
    for original, filtered in zip(selected, inputs):
        address = original['url'], original.get('sha256')
        if address in bindings:
            retry = bindings[address]
            original_bindings = original.get('module_bindings', ())
            if _latest_module_decisions() and not reextract:
                numbers = {item['number'] for item in retry}
                retry = [item for item in original_bindings if item['number'] in numbers]
            for field in ('module_decisions', 'module_matches', 'wsdl_archives', 'schema_diagnostics', 'schema_attempt_diagnostics'):
                filtered.pop(field, None)
            if reextract:
                issues = _inspect_historical_module(base, original, blob, cache)
                for field in ('embedded_archives', 'message_types'):
                    filtered.pop(field, None)
                    if field in original:
                        filtered[field] = original[field]
                if issues:
                    filtered['schema_diagnostics'] = list(issues)
                named = individual_number(original, legacy=legacy_schema_roots) if original.get('wsdl_schema_matches') else None
                requested = {item['number'] for item in retry}
                retry = [item for item in original.get('module_bindings', ()) if item['number'] != named
                         and (not requested or item['number'] in requested)]
            if retry:
                filtered['module_bindings'] = retry
            attempted[address] = retry
            retrying.append((original, original_bindings, filtered, [*bindings[address], *retry]))
    match_modules(base, inputs, [row], blob, legacy_schema_roots=legacy_schema_roots,
                  diagnostic_namespaces=_latest_module_decisions(), declaration_decisions=_latest_module_decisions())
    for original, original_bindings, filtered, declarations in retrying:
        scopes = {declaration_identity(b) for b in declarations}
        for field in ('module_decisions', 'module_matches', 'schema_diagnostics', 'schema_attempt_diagnostics'):
            previous = original.pop(field, [])
            if field == 'module_decisions':
                retained = [item for item in previous if declaration_identity(item) not in scopes]
            elif field == 'module_matches':
                doc = dict(module_bindings=original_bindings)
                retained = [item for item in previous if
                            declaration_identity(module_declaration(doc, item)) not in scopes]
            else:
                retained = [item for item in previous if not any(_issue_applies(item, b) for b in declarations)]
            updated = [*retained, *filtered.get(field, [])]
            if updated:
                original[field] = updated
        for item in filtered.get('wsdl_archives', []):
            values = original.setdefault('wsdl_archives', [])
            if item not in values:
                values.append(item)
    return attempted


def unmatched_module_bindings(entries, *, legacy_filenames=False, bindings=None):
    from catalog import declaration_failure, individual_number
    missing = []
    for doc in entries:
        named_individual = individual_number(doc, legacy=legacy_filenames) if doc.get('wsdl_schema_matches') else None
        selected = doc.get('module_bindings', ()) if bindings is None else bindings.get((doc['url'], doc.get('sha256')), ())
        for binding in selected:
            number, root = binding["number"], binding["root"]
            if number == named_individual or root is None:
                continue
            namespace = binding.get("namespace")
            reason = declaration_failure(doc, binding)
            if reason is None:
                continue
            missing.append(dict(number=number, root=root, namespace=namespace, document_sha256=doc["sha256"],
                                document_url=doc['url'],
                                revision_id=None, reason=reason))
    return missing


def _reject_replaced_binding(processed, previous, confirmed, bindings, archive_graph):
    from catalog import binding_observation_identity, derive, document_identity, replayed_binding_rejections
    if not bindings or not previous or previous['status'] != 'replayed':
        return
    key = previous['snapshot']
    if key not in confirmed:
        return
    losses = list(_replayed_binding_losses(confirmed[key], bindings, archive_graph=archive_graph))
    if not losses:
        return
    ledger = processed.setdefault('rejected_bindings', {})
    prior = ledger.get(key, ())
    rows = derive(confirmed[key], archive_graph=archive_graph)['revisions'] if prior else ()
    reasons = replayed_binding_rejections(rows, prior, snapshot=key)
    for row in losses:
        location = bindings[document_identity(row)]['schema_docx_location']
        reasons[binding_observation_identity(row)] = f'Утрачена подтверждённая привязка DOCX к XSD: {location}.'
    ledger[key] = [dict(observation=observation, reason=reason) for observation, reason in reasons.items()]


class ArchivedModuleProofs:
    """Evaluate a binding against the inputs of its own confirmed observation."""

    def __init__(self, base, blob, archive_graph):
        self.base = base
        self.blob = blob
        self.archive_graph = archive_graph
        self.inspections = {}
        self.archives = {}
        self.verified_archives = set()
        self.verified_snapshots = set()
        self.decisions = {}

    def verify_snapshot(self, digest):
        if digest in self.verified_snapshots:
            return
        raw = (self.base / 'snapshots' / (digest + '.json')).read_bytes()
        if sha(raw) != digest:
            raise ValueError('Historical replay snapshot digest mismatch: ' + digest)
        self.verified_snapshots.add(digest)

    def refresh_archives(self, entries):
        """Keep archived library provenance and renew published ZIP metadata."""
        from sync import inspect_file

        for entry in entries:
            digest = entry.get('sha256')
            if entry['kind'] != 'zip' or not digest:
                continue
            published = entry.get('source_type') not in ('wsdl', 'wsdl_bridge')
            if digest not in self.verified_archives or (published and digest not in self.archives):
                raw = (self.base / 'objects' / digest).read_bytes()
                if sha(raw) != digest:
                    raise ValueError('Historical XSD archive digest mismatch: ' + digest)
                self.verified_archives.add(digest)
                if published:
                    self.archives[digest] = inspect_file(raw, 'zip')['schemas']
            if published:
                entry['schemas'] = self.archives[digest]

    def decision(self, snapshot, document, declaration):
        from catalog import individual_number, schema_binding
        from module_xsd import MODULE_MATCH_VERSION
        from sync import match_modules

        env = document['environment']
        number, root, namespace = declaration
        entries = [r for r in snapshot['entries'] if r['environment'] == env]
        archives = [dict(r) for r in entries if r['kind'] == 'zip' and r.get('sha256')]
        supports = [r for r in entries if r.get('source_type') == 'wsdl' and r.get('sha256')]
        self.refresh_archives(archives)
        legacy = snapshot.get('format', 7) < 7
        doc = copy.deepcopy(document)
        issues = _inspect_historical_module(self.base, doc, self.blob, self.inspections)
        named = individual_number(doc, legacy=legacy) if doc.get('wsdl_schema_matches') else None
        decl = [b for b in doc.get('module_bindings', ()) if b['number'] == number and number != named]
        proof = sha(encode([env, doc['url'], doc['sha256'], declaration, decl,
                            MODULE_INPUT_VERSION, MODULE_MATCH_VERSION,
                            [(r['sha256'], r.get('schemas', ())) for r in archives],
                            [(r['sha256'], r.get('source_files', ())) for r in supports]]))
        if proof not in self.decisions:
            doc['module_bindings'] = decl
            for field in ('module_decisions', 'module_matches', 'wsdl_archives', 'schema_diagnostics', 'schema_attempt_diagnostics'):
                doc.pop(field, None)
            if issues:
                doc['schema_diagnostics'] = list(issues)
            match_modules(self.base, [doc, *archives], supports, self.blob, diagnostic_namespaces=True)
            evidence = [d for d in doc.get('module_decisions', ()) if
                        (d['number'], d['root'], d.get('namespace')) == declaration]
            matches = {schema_binding(dict(r, resolved_schema_graph=self.archive_graph(
                r['archive_sha256'], r['schema_path']))) for r in doc.get('module_matches', ()) if r['number'] == number
                and r['root'] == root and (namespace is None or namespace == (r.get('namespace') or ''))
                and (not doc.get('module_decisions') or any(
                    (r['archive_sha256'], r['schema_path']) == (d.get('archive_sha256'), d.get('schema_path')) for d in evidence))}
            declaration = dict(number=number, root=root, namespace=namespace)
            roots = [b for b in decl if b['root'] == root]
            if namespace is None and len(roots) == 1:
                declaration = roots[0]
            reasons = dict.fromkeys(r['reason'] for r in doc.get('schema_diagnostics', ())
                                    if _issue_applies(r, declaration))
            if evidence:
                reasons = [d['reason'] for d in evidence if 'reason' in d]
            self.decisions[proof] = matches, '; '.join(reasons)
        return self.decisions[proof]


def _module_documents(snapshot):
    return {(doc['environment'], doc['url'], doc['sha256']): doc for doc in snapshot['entries']
            if doc.get('module_matches')}


def _module_document_address(row):
    return row['environment'], row['document_url'], row['document_sha256']


def _recheck_rejected_choices(snapshot, choices, proofs):
    from catalog import declaration_identity, module_declarations, schema_binding
    docs = _module_documents(snapshot)
    rejected = []
    for row in choices:
        if row.get('source_type') != 'module':
            return choices
        doc = docs[_module_document_address(row)]
        if not any(schema_binding(row) in proofs.decision(snapshot, doc, declaration_identity(binding))[0]
                   for binding in module_declarations(doc, row)):
            rejected.append(row)
    return rejected if len(rejected) == 1 else choices


def _upgrade_rejection_addresses(processed, confirmed, proofs):
    from catalog import derive, replayed_binding_rejections
    ledger = processed.get('rejected_bindings', {})
    for digest, records in ledger.items():
        if not isinstance(records, dict) and all(len(item['observation']) == 7 for item in records):
            continue
        if digest not in confirmed:
            raise ValueError('Не найден подтверждённый снимок для архивного отзыва: ' + digest)
        snapshot = confirmed[digest]
        proofs.verify_snapshot(digest)
        rows = derive(snapshot, archive_graph=proofs.archive_graph)['revisions']
        reasons = replayed_binding_rejections(rows, records, snapshot=digest,
            resolve=lambda choices: _recheck_rejected_choices(snapshot, choices, proofs))
        ledger[digest] = [dict(observation=address, reason=reason) for address, reason in reasons.items()]


def _reject_lost_module_matches(processed, env, candidate, previous, library, confirmed,
                                requested, attempted, matched, unmatched, proofs):
    from catalog import (binding_observation_identity, declaration_identity, derive, module_declarations,
                         replayed_binding_rejections, schema_binding)

    previous_key = previous.get('snapshot')
    checked_key, checked_snapshot = candidate
    retained = {binding_observation_identity(row) for row in matched
                if row.get('source_type') == 'module' and row['status'] == 'matched'}
    failures = {((row['document_url'], row['document_sha256']), declaration_identity(row)): row['reason']
                for row in unmatched}
    addressed = {address: {declaration_identity(b) for b in [*requested[address], *attempted.get(address, ())]}
                 for address in requested}
    scopes = {(address[1], declaration) for address, declarations in addressed.items() for declaration in declarations}
    if not scopes:
        return
    direct = {previous_key} if previous.get('status') == 'replayed' else set()
    if library in checked_snapshot['entries']:
        direct.add(checked_key)
    origins = ModuleReplayOrigins(confirmed, processed, proofs.archive_graph)
    ledger = processed.setdefault('rejected_bindings', {})
    for digest, snapshot in confirmed.items():
        if snapshot.get('source_type') == 'manual':
            continue
        docs = {address: doc for address, doc in _module_documents(snapshot).items() if address[0] == env
                and any((address[2], declaration_identity(b)) in scopes
                        for m in doc['module_matches'] for b in module_declarations(doc, m))}
        if not docs:
            continue
        proofs.verify_snapshot(digest)
        old_rows = derive(snapshot, archive_graph=proofs.archive_graph)['revisions']
        prior = replayed_binding_rejections(old_rows, ledger.get(digest, ()), snapshot=digest,
            resolve=lambda choices: _recheck_rejected_choices(snapshot, choices, proofs))
        reasons = prior.copy()
        for row in old_rows:
            doc_address = _module_document_address(row)
            if row.get('source_type') != 'module' or row['status'] != 'matched' or doc_address not in docs:
                continue
            doc = docs[doc_address]
            declarations = [declaration_identity(b) for b in module_declarations(doc, row)]
            checked = [d for d in declarations if (doc_address[2], d) in scopes]
            if not checked or origins.origin(digest, row) != digest:
                continue
            observation = binding_observation_identity(row)
            if digest in direct and any(d in addressed.get(doc_address[1:], ()) for d in checked):
                valid = observation in retained
                detail = next((failures[doc_address[1:], d] for d in checked if (doc_address[1:], d) in failures), None)
                if detail is None and any(d[2] is None for d in checked):
                    detail = failures.get((doc_address[1:], (row['number'], row['root'], observation[-2])))
            else:
                decisions = [proofs.decision(snapshot, doc, d) for d in declarations]
                valid = any(schema_binding(row) in matches for matches, _ in decisions)
                detail = '; '.join(dict.fromkeys(reason for _, reason in decisions if reason))
                # Only the exact receipt under review can lift a previous
                # rejection. Replaying a sibling's old inputs cannot undo it.
                if valid:
                    continue
            if valid:
                reasons.pop(observation, None)
            else:
                reasons[observation] = 'Повторное сопоставление схемы модуля: ' + (
                    detail or 'Прежний корень и граф XSD больше не подтверждены.')
        if reasons != prior:
            if reasons:
                ledger[digest] = [dict(observation=obs, reason=reason) for obs, reason in sorted(reasons.items())]
            else:
                ledger.pop(digest, None)
    if not ledger:
        processed.pop('rejected_bindings', None)


def _clear_restored_bindings(processed, digest, snapshot, env, modules, documents, archive_graph):
    from catalog import (binding_observation_identity, declaration_identity, derive,
                         module_declaration, replayed_binding_rejections)

    ledger = processed.get('rejected_bindings', {})
    if digest not in ledger:
        return
    rows = derive(snapshot, archive_graph=archive_graph)['revisions']
    reasons = replayed_binding_rejections(rows, ledger[digest], snapshot=digest)
    docs = _module_documents(snapshot)
    previous = reasons.copy()
    for row in rows:
        if row['environment'] != env or row['status'] != 'matched':
            continue
        address = row['document_url'], row['document_sha256']
        if row.get('source_type') == 'module':
            doc = docs[_module_document_address(row)]
            if modules is not None and declaration_identity(module_declaration(doc, row)) not in (
                    declaration_identity(b) for b in modules.get(address, ())):
                continue
        elif documents is not None and address not in documents:
            continue
        reasons.pop(binding_observation_identity(row), None)
    if reasons != previous:
        if reasons:
            ledger[digest] = [dict(observation=obs, reason=reason) for obs, reason in reasons.items()]
        else:
            del ledger[digest]


def replay_history(base, current, blob):
    """Reconstruct unprocessed feed observations against confirmed DOCX bytes.

    The caller publishes history, current and this ledger only after all replay
    work succeeds. Orphaned content files from an interruption are harmless:
    confirmation always comes from history/current, and replay is deterministic.
    """
    from sync import encode, match_modules, record_observation, write
    from catalog import (archived_graph_reader, binding_observation_identity, declaration_identity, document_identity,
                         module_declaration, schema_binding)
    from module_xsd import DECLARATION_SNAPSHOT_FORMAT, MODULE_MATCH_VERSION, QUOTATION_SNAPSHOT_FORMAT
    manifests = feed_manifests(base)
    if not manifests:
        return [], None
    processed = processed_manifests(base)
    active, confirmed, observations = replay_context(base)
    index = confirmed_binding_index(active, confirmed, observations)
    archive_graph = archived_graph_reader(base)
    proofs = ArchivedModuleProofs(base, blob, archive_graph)
    modern_inputs = MODULE_MATCH_VERSION >= QUOTATION_SNAPSHOT_FORMAT
    replayed = []
    migrated_addresses = False
    for manifest in manifests:
        env, identity = manifest["environment"], manifest["content_id"]
        records = selected_records(processed).setdefault(env, {})
        previous_record = records.get(identity)
        candidate, proof, bindings = replay_candidate(manifest, active, confirmed, observations, index)
        refresh, retry_partial, retry_wsdl, retry_modules = _replay_changes(
            manifest, candidate, proof, bindings, previous_record, confirmed, archive_graph)
        if not refresh:
            continue
        if not migrated_addresses and previous_record and _stale_module_decision(previous_record):
            _upgrade_rejection_addresses(processed, confirmed, proofs)
            migrated_addresses = True
        decision_version = {'module_decision_version': MODULE_DECISION_VERSION} if _latest_module_decisions() else {}
        retry = retry_wsdl or retry_modules
        prior_snapshot = confirmed.get(previous_record.get('snapshot')) if previous_record else None
        preserve_origin = (prior_snapshot and previous_record.get('status') == 'replayed'
                           and (previous_record.get('replay_proof') != proof
                                or previous_record['observed_at'] != manifest['observed_at'])
                           and _module_documents(prior_snapshot))
        def remember_attempt():
            attempt = dict(source=collector_source(), environment=env, content_id=identity, **previous_record)
            attempts = processed.setdefault('matcher_attempts', [])
            if attempt not in attempts:
                attempts.append(attempt)
        if preserve_origin:
            remember_attempt()
        started_retry = False
        row = manifest["library"]
        if candidate and row in candidate[1].get("entries", []) and (
                not retry or (not retry_partial and candidate[1].get('format', 1) >= MODULE_MATCH_VERSION
                              and not (retry_modules and _stale_module_decision(previous_record)))):
            source_key, baseline = candidate
            record = dict(status="recorded", snapshot=source_key, document_snapshot=source_key,
                          observed_at=manifest["observed_at"], replay_proof=proof)
            recorded_modules = _module_bindings(baseline, env, attempted_only=True,
                reextract=modern_inputs and baseline.get('format', 1) < MODULE_MATCH_VERSION)
            if recorded_modules:
                record['module_match_version'] = baseline.get('format', 1)
                selected = [doc for doc in baseline['entries'] if doc['environment'] == env]
                unmatched = unmatched_module_bindings(selected, legacy_filenames=baseline.get('format', 7) < 7,
                                                      bindings=recorded_modules)
                if unmatched:
                    record['unmatched_documents'] = unmatched
            ambiguous_receipt = (baseline.get('format', 1) < DECLARATION_SNAPSHOT_FORMAT and _latest_module_decisions() and any(
                len({b['number'] for b in group}) < len(group) for group in recorded_modules.values()))
            if recorded_modules and (record['module_match_version'] < MODULE_MATCH_VERSION or ambiguous_receipt):
                # Settle a newly discovered older snapshot in this transaction,
                # using its live decision as the evidence to compare against.
                previous_record = record
                retry_partial, retry_modules = True, recorded_modules
                started_retry = True
            else:
                records[identity] = dict(record, **decision_version)
                continue
        from catalog import (INDIVIDUAL_WSDL_MATCH_VERSION, derive, individual_documents,
                             individual_number, protect_replayed_bindings, record_individual_wsdl_matches)
        reextract = modern_inputs and candidate and candidate[1].get('format', 1) < MODULE_MATCH_VERSION
        historical_modules = _module_bindings(candidate[1], env, reextract=True) if reextract else {}
        docs = ([r for r in candidate[1].get("entries", [])
                 if r.get("environment") == env and r.get("kind") == "docx" and r.get("sha256")
                 and (r.get("module_bindings") or (r['url'], r['sha256']) in retry_modules
                      or (r['url'], r['sha256']) in historical_modules
                      or individual_number(r,legacy=candidate[1].get('format',7)<7))]
                if candidate else [])
        if not docs:
            # Today's collection can still establish the active edition, but it
            # cannot retroactively confirm which DOCX was present at observed_at.
            records[identity] = dict(status="unavailable", observed_at=manifest["observed_at"], replay_proof=proof,
                reason="Недостаточно подтверждённых данных, чтобы однозначно установить DOCX на момент наблюдения WSDL.",
                **decision_version)
            if row in current["entries"]:
                records[identity]["current_snapshot"] = sha(encode(current))
            continue
        if retry or started_retry:
            remember_attempt()
        for document in docs:
            raw = (base / "objects" / document["sha256"]).read_bytes()
            if sha(raw) != document["sha256"]:
                raise ValueError("Historical DOCX digest mismatch")
        source_key, baseline = candidate
        checked_modules = False
        checked_module_inputs = False
        attempted_modules = None
        origin_parent = source_key
        if reextract:
            proofs.verify_snapshot(source_key)
        if retry_partial:
            prior_digest = source_key if previous_record['status'] == 'recorded' else previous_record['snapshot']
            origin_parent = prior_digest
            prior = (base / 'snapshots' / (prior_digest + '.json')).read_bytes()
            if sha(prior) != prior_digest:
                raise ValueError("Historical replay snapshot digest mismatch")
            historical = json.loads(prior)
            if previous_record['status'] != 'recorded':
                source_key = previous_record.get('document_snapshot', source_key)
            previous_editions = {(*document_identity(r), *schema_binding(r))
                                 for r in derive(historical, archive_graph=archive_graph)['revisions']
                                 if r['status'] == 'matched'}
            selected = [r for r in historical["entries"] if r.get("environment") == env]
            inputs = [r for r in selected if r.get('source_type') == 'wsdl'
                      or (r['url'], r.get('sha256')) in retry_wsdl]
            if retry_modules:
                if reextract:
                    proofs.refresh_archives(selected)
                attempted_modules = _retry_module_matches(base, selected, row, blob, retry_modules, proofs.inspections,
                    reextract=reextract, legacy_schema_roots=historical.get('format', 7) < 7)
                checked_modules = bool(attempted_modules)
                checked_module_inputs = checked_modules and reextract
        else:
            historical = copy.deepcopy(baseline)
            historical["entries"] = [r for r in historical["entries"]
                                     if not (r.get("environment") == env and
                                             (r.get("source_type") == "wsdl" or r.get("kind") == "wsdl_status"))]
            historical["entries"].append(copy.deepcopy(row))
            selected = [r for r in historical["entries"] if r.get("environment") == env]
            if historical_modules:
                proofs.refresh_archives(selected)
            # Rebuild this contour's module matches without mutating other contours.
            for document in selected:
                if document.get("module_bindings"):
                    for field in ("module_decisions", "module_matches", "wsdl_archives", "schema_diagnostics", "schema_attempt_diagnostics"):
                        document.pop(field, None)
                if (document['url'], document.get('sha256')) in historical_modules:
                    issues = _inspect_historical_module(base, document, blob, proofs.inspections)
                    if issues:
                        document['schema_diagnostics'] = list(issues)
                    checked_module_inputs = True
            match_modules(base, selected, [row], blob,
                          legacy_schema_roots=not reextract and historical.get('format', 7) < 7,
                          diagnostic_namespaces=_latest_module_decisions(), declaration_decisions=_latest_module_decisions())
            checked_modules = checked_module_inputs or any(document.get('module_bindings') for document in selected)
            inputs = selected
        historical_format = historical.get('format', 7)
        checked_wsdl = record_individual_wsdl_matches(inputs, lambda digest: (base / 'objects' / digest).read_bytes(),
                                                      legacy_filenames=historical_format < 7)
        historical["collector_observation"] = dict(environment=env, content_id=identity,
            observed_at=manifest["observed_at"], document_snapshot=source_key)
        if checked_modules:
            historical['collector_observation']['module_match_version'] = MODULE_MATCH_VERSION
        # An intermediate WSDL can omit one volume's root while the remaining
        # editions are usable. Preserve its DOCX and explain the missing tree.
        matching_snapshot = dict(format=historical_format, entries=selected)
        resolutions = {}
        derived = derive(matching_snapshot, resolutions=resolutions, archive_graph=archive_graph)
        suppressed = set()
        if not retry_partial:
            _reject_replaced_binding(processed, previous_record, confirmed, bindings, archive_graph)
            missing = protect_replayed_bindings(matching_snapshot, resolutions, bindings)
            if missing:
                checked_wsdl = True
                suppressed = {document_identity(r) for r in derived['revisions'] if r['status'] == 'matched'
                              and (document_identity(r), r['document_url']) in missing}
                derived = derive(matching_snapshot, archive_graph=archive_graph)
        revisions = derived['revisions']
        module_failures = unmatched_module_bindings(selected, legacy_filenames=historical_format < 7,
            bindings=attempted_modules if retry_partial and previous_record['status'] == 'recorded' else None)
        if retry_modules:
            attempted = attempted_modules if retry_partial else _module_bindings(matching_snapshot, env)
            _reject_lost_module_matches(processed, env, candidate, previous_record, row, confirmed,
                                        retry_modules, attempted or {}, revisions, module_failures, proofs)
        if retry_partial and not any(r['status'] == 'matched'
                                     and (*document_identity(r), *schema_binding(r)) not in previous_editions
                                     for r in revisions):
            records[identity] = dict(previous_record, **decision_version)
            if retry_wsdl:
                records[identity]['wsdl_match_version'] = INDIVIDUAL_WSDL_MATCH_VERSION
            if checked_modules:
                records[identity]['module_match_version'] = MODULE_MATCH_VERSION
                if checked_module_inputs:
                    records[identity]['module_input_version'] = MODULE_INPUT_VERSION
                records[identity]['document_snapshot'] = source_key
                records[identity]['replay_proof'] = proof
                previous_failures = [r for r in previous_record.get('unmatched_documents', []) if 'namespace' not in r]
                if previous_failures or module_failures:
                    records[identity]['unmatched_documents'] = [*previous_failures, *module_failures]
                else:
                    records[identity].pop('unmatched_documents', None)
            continue
        unmatched = [dict(number=r["number"], revision_id=r["revision_id"],
                          document_sha256=r["document_sha256"], root=r["root"],
                          reason="В исторической библиотеке не найдена однозначная полная XSD для тома.")
                     for r in revisions if r["status"] != "matched"]
        unmatched.extend(dict(number=r['number'], revision_id=None, document_sha256=r['document_sha256'],
                              root=r['root'], reason=r['schema_reason'])
                         for r in derived['document_sources'] if 'namespace_declared' in r)
        unmatched.extend(module_failures)
        binding_proof = {}
        if suppressed:
            binding_proof['binding_proof'] = dict(documents=sorted(suppressed), digest=_binding_proof(bindings, suppressed))
        elif retry_partial and previous_record.get('binding_proof'):
            binding_proof['binding_proof'] = previous_record['binding_proof']
        if unmatched and not any(r["status"] == "matched" for r in revisions):
            records[identity] = dict(status="unavailable", observed_at=manifest["observed_at"],
                document_snapshot=source_key, replay_proof=proof, unmatched_documents=unmatched,
                reason="Не удалось подтвердить ни одной схемы сообщения на дату наблюдения.",
                **binding_proof, **decision_version)
            if checked_wsdl:
                records[identity]['wsdl_match_version'] = INDIVIDUAL_WSDL_MATCH_VERSION
            if checked_modules:
                records[identity]['module_match_version'] = MODULE_MATCH_VERSION
                if checked_module_inputs:
                    records[identity]['module_input_version'] = MODULE_INPUT_VERSION
            continue
        if unmatched:
            historical["collector_observation"]["unmatched_documents"] = unmatched
        if decision_version:
            replay = historical['collector_observation']
            replay['source'] = collector_source()
            replay['module_checked'] = []
            replay['module_copies'] = []
            module_scopes = attempted_modules or {} if retry_partial else _module_bindings(matching_snapshot, env)
            replay['module_declarations'] = [dict(document_url=url, document_sha256=doc_hash,
                number=b['number'], root=b['root'], namespace=b.get('namespace'))
                for (url, doc_hash), declarations in module_scopes.items() for b in declarations]
            docs = _module_documents(historical)
            for edition in derive(historical, archive_graph=archive_graph)['revisions']:
                if edition.get('source_type') != 'module' or edition['status'] != 'matched':
                    continue
                address = _module_document_address(edition)
                observation = binding_observation_identity(edition)
                checked = (edition['environment'] == env and
                           declaration_identity(module_declaration(docs[address], edition)) in {
                               declaration_identity(b) for b in module_scopes.get(address[1:], ())})
                if checked:
                    replay['module_checked'].append(observation)
                else:
                    replay['module_copies'].append(dict(snapshot=origin_parent, observation=observation))
        payload = encode(historical)
        digest = sha(payload)
        write(base / "snapshots" / (digest + ".json"), payload)
        record_observation(base, digest, manifest["observed_at"])
        stamp = datetime.fromisoformat(manifest['observed_at'])
        documents = {key for key, _ in individual_documents(baseline) if key[0] == env}
        _index_named_bindings(index, historical, stamp, env, documents)
        _clear_restored_bindings(processed, digest, historical, env,
                                 attempted_modules or {} if retry_partial else None,
                                 retry_wsdl if retry_partial else None, archive_graph)
        replayed.append(digest)
        records[identity] = dict(status="replayed", snapshot=digest, observed_at=manifest["observed_at"],
                                 document_snapshot=source_key, replay_proof=proof, **binding_proof, **decision_version)
        for field, version, checked in (('module_match_version', MODULE_MATCH_VERSION, checked_modules),
                                       ('wsdl_match_version', INDIVIDUAL_WSDL_MATCH_VERSION, checked_wsdl)):
            if checked:
                records[identity][field] = version
            elif retry_partial and field in previous_record:
                records[identity][field] = previous_record[field]
        if unmatched:
            records[identity]["unmatched_documents"] = unmatched
        if checked_module_inputs:
            records[identity]['module_input_version'] = MODULE_INPUT_VERSION
        elif retry_partial and 'module_input_version' in previous_record:
            records[identity]['module_input_version'] = previous_record['module_input_version']
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
