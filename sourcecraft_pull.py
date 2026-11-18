"""Import verified public SourceCraft snapshots, without service tokens or live SFR calls."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from auto_update import update_lock
from collector_feed import FeedBudgetReached, FeedDownloads, FeedValidationError, MAX_INDEX, bounded_file
from sync import encode, sha

REPOSITORY = "https://git@git.sourcecraft.dev/alexey-sagitov/sedo-xsd-collector.git"
RAW = "https://raw.sourcecraft.tech/raw/alexey-sagitov/sedo-xsd-collector/"
COMMIT = re.compile(r"[0-9a-f]{40}")


def pull(root):
    with update_lock(root) as locked_root:
        return _pull(locked_root)


def _head():
    result = subprocess.run(["git", "-c", "credential.helper=", "ls-remote", REPOSITORY,
                             "refs/heads/snapshots"], check=True, capture_output=True, text=True, timeout=60)
    rows = result.stdout.strip().splitlines()
    if len(rows) != 1:
        raise ValueError("SourceCraft snapshots branch is unavailable")
    commit, ref = rows[0].split()
    if not COMMIT.fullmatch(commit) or ref != "refs/heads/snapshots":
        raise ValueError("Invalid SourceCraft feed ref")
    return commit


def _pinned_commit(marker, baseline):
    try:
        pinned = json.loads(bounded_file(marker, 256) or b"null")
    except ValueError:
        return None
    if (isinstance(pinned, dict) and set(pinned) == {"format", "commit", "baseline"}
            and type(pinned["format"]) is int and pinned["format"] == 1
            and pinned["baseline"] == baseline and isinstance(pinned["commit"], str)
            and COMMIT.fullmatch(pinned["commit"])):
        return pinned["commit"]
    return None


def _pull(root):
    root = Path(root)
    base = root / "state"
    pointer = base / "collector/feed.json"
    raw = bounded_file(pointer, MAX_INDEX)
    if raw is None and pointer.exists():
        raise ValueError("Confirmed SourceCraft feed exceeds size limit")
    baseline = sha(raw) if raw is not None else None
    cache = root / ".sourcecraft-import"
    downloads = FeedDownloads(cache)
    marker = cache / "pending.json"
    pinned = _pinned_commit(marker, baseline)
    commit = pinned or _head()

    def import_commit(commit, *, pin):
        binding = encode(dict(format=1, commit=commit, baseline=baseline))
        if pin:
            downloads.store(marker, binding)
        def fetch(path, limit):
            url = RAW + commit + "/" + path
            with urlopen(Request(url, headers={"User-Agent": "SEDO-reference-feed/1.0"}), timeout=45) as response:
                return response.read(limit + 1)
        return downloads.import_snapshot(base, fetch, snapshot_key=sha(binding))

    try:
        report = import_commit(commit, pin=pinned is None)
    except (FeedValidationError, HTTPError) as error:
        if isinstance(error, HTTPError) and (pinned is None or error.code not in (404, 410)):
            raise
        newest = _head()
        if newest == commit:
            raise
        commit = newest
        report = import_commit(commit, pin=True)
    marker.unlink()
    shutil.rmtree(cache)
    report.update(sourcecraft_commit=commit)
    return report


if __name__ == "__main__":
    try:
        report = pull(Path(__file__).resolve().parent)
    except FeedBudgetReached as error:
        report = dict(error=str(error), resumable=True)
    except Exception as error:
        report = dict(error=type(error).__name__ + ": " + str(error))
    Path("sourcecraft-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1 if "error" in report else 0)
