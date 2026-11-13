"""Append-only collector feed shared by SourceCraft producer and GitHub consumer."""
import json
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
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    path = base / pointer
    raw = encode(index)
    if not path.exists() or path.read_bytes() != raw:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.replace(path)


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
            if (base / name).exists():
                checked_object((base / name).read_bytes(), h)
            else:
                files[name] = checked_object(objects[h], h)
        name = manifest_path(env, digest)
        if (base / name).exists():
            existing = json.loads((base / name).read_bytes())
            if existing["environment"] != env or existing["content_id"] != digest:
                raise ValueError("Existing feed manifest identity mismatch")
            validate_manifest(existing, lambda h: files.get("objects/"+h) if "objects/"+h in files else (base/"objects"/h).read_bytes())
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
    files = {}
    count = 0
    for entry in index["manifests"]:
        env, digest = entry["environment"], entry["content_id"]
        name = manifest_path(env, digest)
        existing = base / name
        raw = existing.read_bytes() if existing.exists() else fetch(name)
        manifest = json.loads(raw)
        if manifest["environment"] != env or manifest["content_id"] != digest:
            raise ValueError("Feed manifest identity mismatch")
        def read(h):
            if not isinstance(h, str) or not DIGEST.fullmatch(h):
                raise ValueError("Invalid feed object identity")
            name = "objects/" + h
            if name in files:
                return files[name]
            path = base / name
            content = path.read_bytes() if path.exists() else fetch(name)
            checked_object(content, h)
            if not path.exists():
                files[name] = content
            return content
        validate_manifest(manifest, read)
        if not existing.exists():
            files[name] = raw
            count += 1
    write_files(base, files, "collector/feed.json", index)
    return dict(new_manifests=count, new_objects=sum(p.startswith("objects/") for p in files),
                latest=index["latest"], history_size=len(index["manifests"]))
