"""Validate Russian collector inputs by replaying their complete WSDL closure offline."""
import json
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
    if json.loads(encode(replay)) != row:
        raise ValueError("Collector library does not match offline replay")
    return row


def load_library(base, environment):
    base = Path(base)
    feed = base / "collector/feed.json"
    if feed.exists():
        from collector_feed import validate_index, manifest_path
        pointer = validate_index(json.loads(feed.read_bytes()))["latest"].get(environment)
        if pointer:
            manifest = json.loads((base / manifest_path(environment, pointer)).read_bytes())
            if manifest["content_id"] != pointer or manifest["environment"] != environment:
                raise ValueError("Feed current manifest identity mismatch")
            return validate_manifest(manifest, lambda h: (base / "objects" / h).read_bytes())
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
    for environment in ("prod", "test"):
        row = load_library(root / "state", environment)
        if row and not any(r.get("source_type") == "wsdl" and
                           r["environment"] == environment and r.get("sha256") == row["sha256"]
                           for r in snapshot["entries"]):
            return True
    return False


if __name__ == "__main__":
    from auto_update import run
    root = Path(__file__).resolve().parent
    if pending(root):
        run(root)
    else:
        print("No pending collector inputs; validated current snapshots.")
