"""Append-only collector feed shared by SourceCraft producer and GitHub consumer."""
import json
import os
import tempfile
from pathlib import Path

from collector_snapshots import DIGEST, observation_path, observation_time, validate_manifest
from sync import encode, sha


def validate_index(index):
    if set(index) != {"format", "manifests", "latest"} or index["format"] != 1:
        raise ValueError("Unsupported feed format")
    entries = index["manifests"]
    if not isinstance(entries, list) or len(entries) > 10000 or not isinstance(index["latest"], dict):
        raise ValueError("Invalid feed index")
    keys = set()
    for entry in entries:
        if (set(entry) != {"environment", "content_id"} or entry["environment"] not in ("prod", "test")
                or not isinstance(entry["content_id"], str) or not DIGEST.fullmatch(entry["content_id"])):
            raise ValueError("Invalid feed entry")
        key = (entry["environment"], entry["content_id"])
        if key in keys:
            raise ValueError("Duplicate feed entry")
        keys.add(key)
    if any((env, digest) not in keys for env, digest in index["latest"].items()):
        raise ValueError("Current feed pointer is missing from history")
    return index


def manifest_path(env, digest):
    return "collector/" + env + "/" + digest + ".json"


def checked_object(raw, digest):
    if sha(raw) != digest:
        raise ValueError("Feed object SHA-256 mismatch")
    return raw


def write_files(base, files, pointer, index):
    # Callers have verified the entire batch before changing any published pointer.
    base = Path(base)
    for name, raw in files.items():
        atomic_write(base / name, raw)
    path = base / pointer
    raw = encode(index)
    if not path.exists() or path.read_bytes() != raw:
        atomic_write(path, raw)


def atomic_write(path, raw):
    """Publish complete bytes by replacing a file on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_bytes(raw)
        if temporary.read_bytes() != raw:
            raise OSError("Incomplete feed file write: " + str(path))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def store_feed(base, manifests, objects):
    base = Path(base)
    pointer = base / "index.json"
    old = validate_index(json.loads(pointer.read_bytes())) if pointer.exists() else dict(format=1, manifests=[], latest={})
    index = json.loads(encode(old))
    known = {(e["environment"], e["content_id"]) for e in index["manifests"]}
    files = {}
    environments = set()
    for manifest in manifests:
        row = validate_manifest(manifest, objects.__getitem__)
        env, digest = manifest["environment"], manifest["content_id"]
        if env in environments:
            raise ValueError("Duplicate contour in collection")
        environments.add(env)
        for h in [row["sha256"], *(s["sha256"] for s in row["source_files"])]:
            name = "objects/" + h
            if not (base / name).exists() or sha((base / name).read_bytes()) != h:
                files[name] = checked_object(objects[h], h)
        name = manifest_path(env, digest)
        previous = bounded_file(base / name, MAX_MANIFEST)
        if previous is not None:
            try:
                existing = json.loads(previous)
                if existing["environment"] != env or existing["content_id"] != digest:
                    raise ValueError("Existing feed manifest identity mismatch")
                validate_manifest(existing, lambda h: files.get("objects/"+h) if "objects/"+h in files else (base/"objects"/h).read_bytes())
            except (ValueError, KeyError, TypeError):
                previous = None
        stamp = observation_time(base, "sourcecraft", env, digest)
        if stamp is None:
            if previous is not None:
                stamp = existing["observed_at"]
            elif (env, digest) in known:
                raise ValueError("First observation date is unavailable for indexed manifest: " + digest)
            else:
                stamp = manifest["observed_at"]
            files[observation_path("sourcecraft", env, digest)] = (stamp + "\n").encode("ascii")
        confirmed = dict(manifest, observed_at=stamp)
        if previous is None or existing["observed_at"] != stamp:
            files[name] = encode_manifest(confirmed)
        if (env, digest) not in known:
            index["manifests"].append(dict(environment=env, content_id=digest))
            known.add((env, digest))
        index["latest"][env] = digest
    index["manifests"].sort(key=lambda e: (e["environment"], e["content_id"]))
    validate_index(index)
    changed = bool(files) or index != old
    if changed:
        write_files(base, files, "index.json", index)
    return changed


# Legacy producers could publish descriptions larger than one MiB. Bound both
# new publications and reads by the same per-manifest budget.
MAX_MANIFEST = 16 * 1024 * 1024
MAX_INDEX = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_DOWNLOAD_REQUESTS = 256
MAX_CHECKPOINT_BYTES = 512 * 1024 * 1024
MAX_VERIFICATION_BYTES = 2 * 1024 * 1024 * 1024


class FeedBudgetReached(RuntimeError):
    """Verified downloads can be reused by another bounded invocation."""

class FeedCapacityExceeded(ValueError):
    """An input exceeds the supported storage or per-file capacity."""


class FeedValidationError(ValueError):
    """The remote snapshot cannot produce a confirmed feed."""


def _validate_remote(read, *args):
    try:
        return read(*args)
    except FeedCapacityExceeded:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise FeedValidationError(str(error)) from error


def bounded_file(path, limit):
    if not path.is_file() or path.stat().st_size > limit:
        return None
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    return raw if len(raw) <= limit else None


def encode_manifest(manifest):
    raw = encode(manifest)
    if len(raw) > MAX_MANIFEST:
        raise ValueError("Collector manifest exceeds size limit: " + manifest["content_id"])
    return raw


def read_manifest(path):
    raw = bounded_file(path, MAX_MANIFEST)
    if raw is None:
        if not path.is_file():
            raise FileNotFoundError(path)
        raise ValueError("Collector manifest exceeds size limit: " + str(path))
    return json.loads(raw)


class FeedDownloads:
    def __init__(self, folder, max_requests=MAX_DOWNLOAD_REQUESTS, max_bytes=MAX_DOWNLOAD_BYTES):
        self.folder = folder
        self.max_requests, self.max_bytes = max_requests, max_bytes
        self.requests = self.transferred = 0
        self.cache_bytes = self.cache_files = 0
        if min(max_requests, max_bytes) <= 0:
            raise ValueError("SourceCraft download budget must be positive")
        if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
            raise ValueError("Unexpected link in SourceCraft checkpoint")
        for path in folder.rglob("*"):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("Unexpected link in SourceCraft checkpoint")
            if path.is_file():
                self.cache_files += 1
                self.cache_bytes += path.stat().st_size
                if self.cache_bytes > MAX_CHECKPOINT_BYTES or self.cache_files > 100000:
                    raise FeedCapacityExceeded("SourceCraft checkpoint exceeds storage budget")

    def download(self, name, limit, expected=None):
        if expected is not None and expected > self.max_bytes:
            raise FeedCapacityExceeded("Single SourceCraft input exceeds the configured run budget")
        remaining = self.max_bytes - self.transferred
        if self.requests >= self.max_requests or remaining <= 0 or (expected is not None and expected > remaining):
            raise FeedBudgetReached("SourceCraft download budget reached; rerun to continue")
        self.requests += 1
        raw = self.fetch(name, min(limit, remaining))
        self.transferred += min(len(raw), remaining)
        if len(raw) > limit:
            raise ValueError("SourceCraft input exceeds size limit: " + name)
        if len(raw) > remaining:
            if remaining == self.max_bytes:
                raise FeedCapacityExceeded("Single SourceCraft input exceeds the configured run budget")
            raise FeedBudgetReached("SourceCraft byte budget reached; rerun to continue")
        return raw

    def store(self, path, raw):
        previous = path.stat().st_size if path.exists() else 0
        self.cache_bytes += len(raw) - previous
        self.cache_files += int(not path.exists())
        if self.cache_bytes > MAX_CHECKPOINT_BYTES or self.cache_files > 100000:
            raise FeedCapacityExceeded("SourceCraft checkpoint exceeds storage budget")
        atomic_write(path, raw)

    def object(self, name, digest, size):
        path = self.folder / name
        raw = bounded_file(path, size)
        if raw is not None and sha(raw) == digest:
            return raw
        raw = checked_object(self.download(name, size, size), digest)
        self.store(path, raw)
        return raw

    def manifest(self, name, digest):
        path = self.folder / name
        saved = bounded_file(path, MAX_MANIFEST + 130)
        if saved is not None:
            identity, _, remainder = saved.partition(b"\n")
            checksum, _, raw = remainder.partition(b"\n")
            if identity == self.identity and checksum == sha(raw).encode("ascii"):
                return raw
        raw = self.download(name, MAX_MANIFEST)
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or manifest.get("content_id") != digest or sha(encode(manifest["library"])) != digest:
            raise ValueError("Feed manifest identity mismatch")
        self.store(path, self.identity + b"\n" + sha(raw).encode("ascii") + b"\n" + raw)
        return raw

    def import_snapshot(self, base, fetch, snapshot_key=None):
        previous = base / "collector/feed.json"
        old = validate_index(json.loads(previous.read_bytes())) if previous.exists() else None
        raw, index = _validate_remote(_read_remote_index, fetch)
        if old is not None:
            old_entries = {(e["environment"], e["content_id"]) for e in old["manifests"]}
            new_entries = {(e["environment"], e["content_id"]) for e in index["manifests"]}
            if not old_entries <= new_entries or not old["latest"].keys() <= index["latest"].keys():
                raise FeedValidationError("Feed lost previously confirmed history or contour pointers")
        self.fetch = fetch
        self.identity = (snapshot_key or sha(raw)).encode("ascii")
        with tempfile.TemporaryDirectory(prefix="sedo-feed-") as tmp:
            return _import_batch(base, index, Path(tmp) / "candidate", self)


def _read_remote_index(fetch):
    # Index reads have their own bound. The shared download budget covers
    # manifests and objects across both attempts.
    raw = fetch("index.json", MAX_INDEX)
    if len(raw) > MAX_INDEX:
        raise ValueError("Feed index exceeds size limit")
    return raw, validate_index(json.loads(raw))


def import_feed(base, fetch, *, checkpoint=None, snapshot_key=None, max_requests=MAX_DOWNLOAD_REQUESTS,
                max_bytes=MAX_DOWNLOAD_BYTES, fetch_limited=None):
    """Validate a disk-staged batch before atomically changing its feed pointer."""
    base = Path(base)
    limited = fetch_limited or (lambda name, limit: fetch(name))
    if checkpoint is not None:
        return FeedDownloads(Path(checkpoint), max_requests, max_bytes).import_snapshot(base, limited, snapshot_key)
    with tempfile.TemporaryDirectory(prefix="sedo-feed-downloads-") as tmp:
        return FeedDownloads(Path(tmp), max_requests, max_bytes).import_snapshot(base, limited, snapshot_key)


def _import_batch(base, index, stage, downloads):
    from wsdl_sources import MAX_FILE, MAX_FILES, MAX_TOTAL
    files = set()
    count = work = 0
    def stage_file(name, raw):
        atomic_write(stage / name, raw)
        files.add(name)

    def parse(raw, env, digest):
        nonlocal work
        manifest = json.loads(raw)
        row = manifest["library"]
        if manifest["environment"] != env or manifest["content_id"] != digest or sha(encode(row)) != digest:
            raise ValueError("Feed manifest identity mismatch")
        sources = row["source_files"]
        if (len(sources) > MAX_FILES or not 0 <= row["bytes"] <= MAX_TOTAL
                or any(not 0 <= source["bytes"] <= MAX_FILE for source in sources)):
            raise ValueError("Collector closure exceeds limits")
        sizes = {s["sha256"]: s["bytes"] for s in sources}
        sizes[row["sha256"]] = row["bytes"]
        work += len(raw) + sum(s["bytes"] for s in sources) + row["bytes"]
        if work > MAX_VERIFICATION_BYTES:
            raise RuntimeError("SourceCraft feed exceeds cumulative offline verification budget")
        def read(h):
            if not isinstance(h, str) or not DIGEST.fullmatch(h):
                raise ValueError("Invalid feed object identity")
            name = "objects/" + h
            content = bounded_file((stage if name in files else base) / name, sizes[h])
            if content is None or sha(content) != h:
                content = downloads.object(name, h, sizes[h])
                stage_file(name, content)
            return content
        validate_manifest(manifest, read)
        return manifest

    for entry in index["manifests"]:
        env, digest = entry["environment"], entry["content_id"]
        name = manifest_path(env, digest)
        existing = base / name
        raw = bounded_file(existing, MAX_MANIFEST)
        try:
            manifest = parse(raw, env, digest) if raw is not None else None
        except FeedCapacityExceeded:
            raise
        except (ValueError, KeyError, TypeError):
            manifest = None
        if manifest is None or observation_time(base, "sourcecraft", env, digest) is None:
            remote = _validate_remote(downloads.manifest, name, digest)
            confirmed = _validate_remote(parse, remote, env, digest)
            stamp = observation_time(base, "sourcecraft", env, digest) or confirmed["observed_at"]
            stage_file(observation_path("sourcecraft", env, digest), (stamp + "\n").encode("ascii"))
            if manifest is None:
                stage_file(name, remote)
            count += not existing.exists()
    for name in sorted(files):
        atomic_write(base / name, (stage / name).read_bytes())
    write_files(base, {}, "collector/feed.json", index)
    return dict(new_manifests=count, new_objects=sum(name.startswith("objects/") for name in files),
                latest=index["latest"], history_size=len(index["manifests"]))
