"""Import verified public SourceCraft snapshots, without service tokens or live SFR calls."""
import json
import re
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

from collector_feed import import_feed

REPOSITORY = "https://git@git.sourcecraft.dev/alexey-sagitov/sedo-xsd-collector.git"
RAW = "https://raw.sourcecraft.tech/raw/alexey-sagitov/sedo-xsd-collector/"


def pull(root):
    result = subprocess.run(["git", "-c", "credential.helper=", "ls-remote", REPOSITORY,
                             "refs/heads/snapshots"], check=True, capture_output=True, text=True, timeout=60)
    rows = result.stdout.strip().splitlines()
    if len(rows) != 1:
        raise ValueError("SourceCraft snapshots branch is unavailable")
    commit, ref = rows[0].split()
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or ref != "refs/heads/snapshots":
        raise ValueError("Invalid SourceCraft feed ref")
    def fetch(path):
        # Paths are derived locally from validated SHA-256 identities, never from arbitrary URLs.
        url = RAW + commit + "/" + path
        with urlopen(Request(url, headers={"User-Agent":"SEDO-reference-feed/1.0"}), timeout=45) as response:
            raw = response.read(64*1024*1024+1)
        if len(raw) > 64*1024*1024:
            raise ValueError("Feed file exceeds size limit")
        return raw
    report = import_feed(Path(root)/"state", fetch)
    report.update(sourcecraft_commit=commit)
    return report


if __name__ == "__main__":
    try:
        report = pull(Path(__file__).resolve().parent)
    except Exception as error:
        report = dict(error=type(error).__name__ + ": " + str(error))
    Path("sourcecraft-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1 if "error" in report else 0)
