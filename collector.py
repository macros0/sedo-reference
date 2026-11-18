"""Small Python 3.9 collector: HTTPS from Russia, atomic Git Data API commits."""
import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from collector_snapshots import (DIGEST, HOME_POINTER, HOME_POINTER_LIMIT, legacy_home_identity, make_manifest,
                                 observation_path, parse_home_pointer, validate_manifest)
from sync import SITES, Links, get, sha, encode
from wsdl_sources import MAX_TOTAL, collect, discover


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub API redirects are not allowed")


def git_blob_sha(raw):
    digest = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0", usedforsecurity=False)
    digest.update(raw)
    return digest.hexdigest()


class GitHub:
    def __init__(self, repository, token):
        if repository != "macros0/sedo-reference":
            raise ValueError("Collector repository is fixed")
        self.base = "https://api.github.com/repos/" + repository + "/"
        self.token = token
        self.opener = build_opener(NoRedirect())

    def __call__(self, method, path, payload=None):
        request = Request(self.base + path, method=method,
                          data=encode(payload) if payload is not None else None,
                          headers={"Authorization": "Bearer " + self.token,
                                   "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json",
                                   "X-GitHub-Api-Version": "2022-11-28",
                                   "User-Agent": "SEDO-reference-collector"})
        with self.opener.open(request, timeout=45) as response:
            return json.load(response)


def repaired_envelope(previous, manifest, objects, read_receipt, *, encoded=None):
    """Keep a valid shared envelope; rebuild missing or invalid bytes from the earliest receipt."""
    from collector_feed import encode_manifest
    if encoded is None:
        encoded = encode_manifest(manifest)
    try:
        prior = json.loads(previous)
        if any(prior[key] != manifest[key] for key in ("content_id", "environment")):
            raise ValueError("Existing manifest has another identity")
        validate_manifest(prior, objects.__getitem__)
        return None
    except (ValueError, TypeError, KeyError):
        pass
    stamps = []
    for source in ("home", "sourcecraft"):
        try:
            value = (read_receipt(source) or b"").decode("ascii").strip()
            observed = datetime.fromisoformat(value)
            if observed.tzinfo:
                stamps.append((observed, value))
        except ValueError:
            continue
    first = min(stamps)[1] if stamps else manifest["observed_at"]
    return encoded if first == manifest["observed_at"] else encode_manifest(dict(manifest, observed_at=first))


def observation_date(stamp):
    try:
        date = datetime.fromisoformat(stamp)
        return date if date.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def receipt_date(raw):
    try:
        return observation_date((raw or b'').decode('ascii').strip())
    except UnicodeError:
        return None


def recorded_home_receipt(processed, environment, identity):
    record = processed.get('sources', {}).get('home', {}).get(environment, {}).get(identity, {})
    stamp = record.get('observed_at')
    if record.get('status') in ('replayed', 'unavailable', 'recorded') and observation_date(stamp):
        return (stamp + '\n').encode('ascii')
    return None


def repaired_home_receipt(raw, manifest, read_processed, *, current_id=None, unconfirmed=()):
    """Never invent an earlier home observation from another collector's envelope."""
    if receipt_date(raw) is not None:
        return None
    try:
        processed = json.loads(read_processed() or b'{}')
        restored = recorded_home_receipt(processed, manifest['environment'], manifest['content_id'])
    except (AttributeError, ValueError, TypeError):
        restored = None
    if restored is not None:
        return restored
    if raw is None:
        if current_id == manifest['content_id']:
            raise ValueError('Missing home receipt for the current capture without confirmed historical home evidence: '
                             + manifest['content_id'])
        for saved in unconfirmed:
            if receipt_date(saved) is not None:
                return saved
        return (manifest['observed_at'] + '\n').encode('ascii')
    raise ValueError('Corrupt home receipt without confirmed historical home evidence: ' + manifest['content_id'])


def legacy_pointer_observation(raw):
    if raw is None:
        return None
    try:
        identity, stamp = raw.decode("ascii").splitlines()
        started = datetime.fromisoformat(stamp)
        if not DIGEST.fullmatch(identity) or started.tzinfo is None:
            raise ValueError("Invalid observation")
    except ValueError as exc:
        raise ValueError("Invalid legacy Home current observation") from exc
    return identity, started


def home_pointer_state(read, environment, *, validated_id):
    """Read the active identity and plan migration of a confirmed legacy receipt."""
    prefix = 'collector/' + environment + '/'
    raw = read(prefix + HOME_POINTER, HOME_POINTER_LIMIT)
    if raw is not None:
        current = parse_home_pointer(raw)
        return raw, current[0], current, None
    identity = legacy_home_identity(read(prefix + 'current', HOME_POINTER_LIMIT))
    bound = legacy_pointer_observation(read(prefix + 'current-observed-at', HOME_POINTER_LIMIT))
    if identity is None:
        return None, None, bound, None
    first, migration = legacy_home_receipt(read, environment, identity, recaptured=identity == validated_id)
    # The split marker orders captures, even if the last pointer write failed.
    if bound is None or first >= bound[1]:
        bound = identity, first
    return None, identity, bound, migration


def legacy_home_receipt(read, environment, identity, *, recaptured=False):
    from collector_feed import MAX_INDEX, MAX_MANIFEST, manifest_path, validate_index

    receipt = observation_path('home', environment, identity)
    saved = read(receipt, HOME_POINTER_LIMIT)
    first = receipt_date(saved)
    if first is not None and recaptured:
        # The caller already validated this exact identity from fresh inputs.
        # It can restore the old files while keeping the confirmed Home date.
        return first, None

    name = manifest_path(environment, identity)
    try:
        envelope = json.loads(read(name, MAX_MANIFEST) or b'')
        if envelope['content_id'] != identity or envelope['environment'] != environment:
            raise ValueError('Manifest identity mismatch')
        validate_manifest(envelope, lambda digest: read('objects/' + digest, MAX_TOTAL) or b'')
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('Cannot validate legacy Home manifest ' + name + ': ' + str(exc)) from exc

    restored = None
    if first is None:
        ledger = read('collector/processed.json', MAX_MANIFEST)
        processed = json.loads(ledger) if ledger is not None else {}
        restored = recorded_home_receipt(processed, environment, identity)
        if restored is None:
            sourcecraft = observation_path('sourcecraft', environment, identity)
            shared = (read(sourcecraft, HOME_POINTER_LIMIT) is not None
                      or identity in processed.get('manifests', {}).get(environment, {}))
            if not shared:
                entry = {'environment': environment, 'content_id': identity}
                for path in ('collector/feed.json', 'index.json'):
                    index = read(path, MAX_INDEX)
                    if index is not None and entry in validate_index(json.loads(index))['manifests']:
                        shared = True
                        break
            if shared:
                raise ValueError('Ambiguous legacy Home first observation for ' + environment + ' ' + identity +
                                 '. Restore the confirmed Home receipt ' + receipt + ' (docs/russian-collector.md).')
            if saved is not None:
                raise ValueError('Corrupt legacy Home receipt: ' + receipt)
            restored = (envelope['observed_at'] + '\n').encode('ascii')
        first = receipt_date(restored)
    return first, (receipt, restored) if restored is not None else None


def pointer_update(manifest, previous, *, canonical):
    """Order each contour by capture start, independently of its immutable receipts."""
    identity, started = manifest["content_id"], datetime.fromisoformat(manifest["observed_at"])
    if previous is not None:
        previous_id, previous_start = previous
        if started < previous_start or (started == previous_start and (canonical or identity != previous_id)):
            return None
    return encode(dict(format=1, content_id=identity, capture_started_at=started.isoformat()))


def publish_inputs(api, manifests, objects):
    from collector_feed import MAX_MANIFEST, encode_manifest
    files = {}
    pointers = []
    for manifest in manifests:
        row = validate_manifest(manifest, objects.__getitem__)
        prefix = "state/collector/" + manifest["environment"] + "/"
        files[prefix + manifest["content_id"] + ".json"] = encode_manifest(manifest)
        receipt = observation_path("home", manifest["environment"], manifest["content_id"])
        files["state/" + receipt] = (manifest["observed_at"] + "\n").encode("ascii")
        pointers.extend((prefix + "current", prefix + "current-observed-at"))
        for digest in [row["sha256"], *(s["sha256"] for s in row["source_files"])]:
            files["state/objects/" + digest] = objects[digest]
    object_ids = {path: git_blob_sha(raw) for path, raw in files.items() if path.startswith("state/objects/")}
    existing_blobs = {}
    def read_blob(digest, limit=MAX_MANIFEST):
        if digest not in existing_blobs:
            response = api("GET", "git/blobs/" + digest)
            if response.get("encoding") != "base64" or response["size"] > limit:
                return None
            existing_blobs[digest] = base64.b64decode(response["content"])
        raw = existing_blobs[digest]
        return raw if len(raw) <= limit else None

    blobs = {}
    legacy_currents = {}
    for attempt in range(3):
        head = api("GET", "git/ref/heads/main")["object"]["sha"]
        tree = api("GET", "git/commits/" + head)["tree"]["sha"]
        listing = api("GET", "git/trees/" + tree + "?recursive=1")
        if listing.get("truncated"):
            raise ValueError("GitHub tree is truncated; refusing partial comparison")
        existing = {r["path"]: r["sha"] for r in listing["tree"] if r["type"] == "blob"}
        new = {p: raw for p, raw in files.items() if p not in existing}
        if attempt == 0:
            original_pointers = {p: existing.get(p) for p in pointers}
        for manifest in manifests:
            env, identity = manifest["environment"], manifest["content_id"]
            prefix = f"state/collector/{env}/"
            path, pointer = prefix + identity + ".json", prefix + HOME_POINTER
            def read_state(path, limit):
                path = 'state/' + path
                return (read_blob(existing[path], limit) or b'') if path in existing else None
            repaired_receipts = {}
            def read_receipt(source):
                if source in repaired_receipts:
                    return repaired_receipts[source]
                receipt = "state/" + observation_path(source, env, identity)
                # None means absent only; an existing unreadable blob is corrupt.
                return (read_blob(existing[receipt]) or b'') if receipt in existing else None
            current, current_id, previous, migration = home_pointer_state(read_state, env, validated_id=identity)
            # A failed CAS can reveal another legacy current. Keep the first
            # observation of each confirmed pointer, by rechecking it on the
            # new base before retrying the write.
            seen = legacy_currents.setdefault(env, set())
            if current is None and current_id is not None:
                seen.add(current_id)
            for old_id in sorted(seen - {current_id} if current is None else seen):
                receipt_path = observation_path('home', env, old_id)
                if receipt_date(read_state(receipt_path, HOME_POINTER_LIMIT)) is not None:
                    continue
                _, prior_migration = legacy_home_receipt(read_state, env, old_id)
                if prior_migration is not None:
                    receipt_path, restored = prior_migration
                    new['state/' + receipt_path] = restored
                    if old_id == identity:
                        repaired_receipts['home'] = restored
            if migration is not None:
                receipt_path, restored = migration
                new['state/' + receipt_path] = restored
                if current_id == identity:
                    repaired_receipts['home'] = restored
            processed = 'state/collector/processed.json'
            restored_receipt = repaired_home_receipt(read_receipt('home'), manifest,
                lambda: read_blob(existing[processed]) if processed in existing else None,
                current_id=current_id)
            if restored_receipt is not None:
                new['state/' + observation_path('home', env, identity)] = restored_receipt
                repaired_receipts['home'] = restored_receipt
            if existing.get(path) != git_blob_sha(files[path]):
                old_envelope = read_blob(existing[path]) if path in existing else None
                restored = repaired_envelope(old_envelope, manifest, objects, read_receipt, encoded=files[path])
                if restored is not None:
                    new[path] = restored
            if current is None and any(existing.get(key) != original_pointers[key]
                                       for key in (prefix + "current", prefix + "current-observed-at")):
                continue
            update = pointer_update(manifest, previous, canonical=current is not None)
            if update is not None:
                new[pointer] = update
        for path, expected in object_ids.items():
            if existing.get(path) != expected:
                new[path] = files[path]
        if not new:
            return False
        entries = []
        for path, raw in sorted(new.items()):
            digest = sha(raw)
            if digest not in blobs:
                blobs[digest] = api("POST", "git/blobs", dict(content=base64.b64encode(raw).decode("ascii"), encoding="base64"))["sha"]
            entries.append(dict(path=path, mode="100644", type="blob", sha=blobs[digest]))
        new_tree = api("POST", "git/trees", dict(base_tree=tree, tree=entries))["sha"]
        commit = api("POST", "git/commits", dict(message="Collect SFR WSDL snapshots from Russian server", tree=new_tree, parents=[head],
                     author=dict(name="macros0", email="macros0@users.noreply.github.com")))["sha"]
        try:
            api("PATCH", "git/refs/heads/main", dict(sha=commit, force=False))
            return True
        except HTTPError as exc:
            if exc.code not in (409, 422) or attempt == 2:
                raise
    raise ValueError("Concurrent GitHub updates did not settle")


def capture(environment):
    started_at = datetime.now(timezone.utc).isoformat()
    page = SITES[environment]
    parser = Links()
    content = get(page, timeout=30, attempts=2)
    parser.feed(content.decode("utf-8-sig"))
    base = getattr(content, "final_url", page)
    seeds = discover([dict(environment=environment, url=url) for url, _ in parser.resolved(base)], environment)
    objects = {}
    def blob(raw):
        digest = sha(raw)
        objects[digest] = raw
        return digest
    errors = []
    for seed in seeds:
        try:
            row = collect(seed, environment, lambda url: get(url, timeout=30, attempts=2, wsdl_seed=seed), blob)
            return make_manifest(row, started_at), objects
        except Exception as exc:
            errors.append(type(exc).__name__)
    raise ValueError("No complete " + environment + " WSDL closure: " + ", ".join(errors))


def store_local_inputs(state, manifests, objects):
    from collector_feed import atomic_write, bounded_file, MAX_MANIFEST
    from sync import write

    verified_objects, envelopes, receipts, pending, pointers = {}, {}, {}, {}, {}
    markers = []
    def read_state(path, limit):
        path = state / path
        raw = bounded_file(path, limit)
        return b'' if raw is None and path.exists() else raw

    for manifest in manifests:
        row = validate_manifest(manifest, objects.__getitem__)
        for digest in [row["sha256"], *(source["sha256"] for source in row["source_files"])]:
            verified_objects[digest] = objects[digest]
        env, identity = manifest["environment"], manifest["content_id"]
        directory = state / "collector" / env
        target = directory / (identity + ".json")
        pointer = directory / HOME_POINTER
        repaired_receipts = {}
        def read_receipt(source):
            if source in repaired_receipts:
                return repaired_receipts[source]
            return read_state(observation_path(source, env, identity), HOME_POINTER_LIMIT)
        current, current_id, previous, migration = home_pointer_state(read_state, env, validated_id=identity)
        if migration is not None:
            receipt_path, restored = migration
            receipts[state / receipt_path] = restored
            if current_id == identity:
                repaired_receipts['home'] = restored
        receipt = state / observation_path("home", env, identity)
        marker = receipt.with_suffix(".pending-at")
        markers.append(marker)
        orphan = receipt.parent / "orphans" / receipt.name
        existing_receipt = read_receipt('home')
        restored_receipt = repaired_home_receipt(existing_receipt, manifest,
            lambda: bounded_file(state / 'collector/processed.json', MAX_MANIFEST),
            current_id=current_id,
            unconfirmed=(bounded_file(path, 256) for path in (orphan, marker)))
        if restored_receipt is not None:
            receipts[receipt] = restored_receipt
            repaired_receipts['home'] = restored_receipt
            if existing_receipt is None and bounded_file(marker, 256) != restored_receipt:
                pending[marker] = restored_receipt
        restored = repaired_envelope(bounded_file(target, MAX_MANIFEST), manifest, objects, read_receipt)
        if restored is not None:
            envelopes[target] = restored
        update = pointer_update(manifest, previous, canonical=current is not None)
        if update is not None:
            pointers[pointer] = update
    # Preserve the first attempt's time before writing any objects.
    for target, raw in pending.items():
        write(target, raw)
    for digest, raw in verified_objects.items():
        write(state / "objects" / digest, raw)
    for files in (envelopes, receipts):
        for target, raw in files.items():
            write(target, raw)
    for target, raw in pointers.items():
        atomic_write(target, raw)
    for marker in markers:
        marker.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--capture-to", type=Path, help="Capture locally without publishing")
    parser.add_argument("--environment", choices=("prod", "test", "both"), default="both")
    args = parser.parse_args()
    if not args.capture_to and not args.token_file:
        parser.error("--token-file or --capture-to is required")
    manifests, objects, errors = [], {}, []
    for env in (("prod", "test") if args.environment == "both" else (args.environment,)):
        try:
            manifest, captured = capture(env)
            manifests.append(manifest)
            objects.update(captured)
            print(env + ": " + manifest["content_id"] + "; sources=" + str(len(manifest["library"]["source_files"])), flush=True)
        except Exception as exc:
            errors.append(env + ": " + str(exc))
    if args.capture_to:
        if manifests:
            from state_lock import catalog_lock_root, file_lock
            state = args.capture_to.resolve()
            root = catalog_lock_root(state)
            root.mkdir(parents=True, exist_ok=True)
            with file_lock(root):
                if (root / ".update-recovery").exists() or (state / ".catalog-recovery").exists():
                    raise ValueError("Catalog recovery is pending for " + str(state) +
                                     "; repeat the original update before capturing")
                store_local_inputs(state, manifests, objects)
    elif manifests:
        token = args.token_file.read_text().strip()
        if not token.startswith("github_pat_") or any(c.isspace() for c in token):
            raise ValueError("Expected a dedicated fine-grained GitHub token")
        changed = publish_inputs(GitHub("macros0/sedo-reference", token), manifests, objects)
        print("Published new inputs." if changed else "Unchanged; no commit needed.")
    if errors:
        raise ValueError("; ".join(errors))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never include request headers, token values or remote response bodies.
        print("Collector failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
