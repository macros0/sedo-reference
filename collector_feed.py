"""Append-only collector feed shared by SourceCraft producer and GitHub consumer."""
import json
import os
import tempfile
from pathlib import Path

from collector_snapshots import DIGEST, validate_manifest
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
            raise ValueError("Incomplete feed file write: " + str(path))
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
        if (base / name).exists():
            try:
                existing = json.loads((base / name).read_bytes())
                if existing["environment"] != env or existing["content_id"] != digest:
                    raise ValueError("Existing feed manifest identity mismatch")
                validate_manifest(existing, lambda h: files.get("objects/"+h) if "objects/"+h in files else (base/"objects"/h).read_bytes())
            except (ValueError, KeyError, TypeError):
                files[name] = encode(manifest)
        else:
            files[name] = encode(manifest)
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


def import_feed(base, fetch):
    """fetch takes a relative feed path; never executes remote code or follows paths from it."""
    base = Path(base)
    index = validate_index(json.loads(fetch("index.json")))
    previous = base / "collector/feed.json"
    if previous.exists():
        old = validate_index(json.loads(previous.read_bytes()))
        old_entries = {(e["environment"], e["content_id"]) for e in old["manifests"]}
        new_entries = {(e["environment"], e["content_id"]) for e in index["manifests"]}
        if not old_entries <= new_entries or not old["latest"].keys() <= index["latest"].keys():
            raise ValueError("Feed lost previously confirmed history or contour pointers")
    files = {}
    count = 0
    for entry in index["manifests"]:
        env, digest = entry["environment"], entry["content_id"]
        name = manifest_path(env, digest)
        existing = base / name
        raw = existing.read_bytes() if existing.exists() else None
        def read(h):
            if not isinstance(h, str) or not DIGEST.fullmatch(h):
                raise ValueError("Invalid feed object identity")
            object_name = "objects/" + h
            if object_name in files:
                return files[object_name]
            path = base / object_name
            content = path.read_bytes() if path.exists() else None
            if content is None or sha(content) != h:
                content = checked_object(fetch(object_name), h)
                files[object_name] = content
            return content
        def parse(raw):
            manifest = json.loads(raw)
            if (manifest["environment"] != env or manifest["content_id"] != digest
                    or sha(encode(manifest["library"])) != digest):
                raise ValueError("Feed manifest identity mismatch")
            validate_manifest(manifest, read)
            return manifest
        try:
            manifest = parse(raw) if raw is not None else None
        except (ValueError, KeyError, TypeError):
            manifest = None
        if manifest is None:
            raw = fetch(name)
            manifest = parse(raw)
        if not existing.exists() or existing.read_bytes() != raw:
            files[name] = raw
            count += not existing.exists()
    write_files(base, files, "collector/feed.json", index)
    return dict(new_manifests=count, new_objects=sum(p.startswith("objects/") for p in files),
                latest=index["latest"], history_size=len(index["manifests"]))
