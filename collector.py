"""Small Python 3.9 collector: HTTPS from Russia, atomic Git Data API commits."""
import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, build_opener, HTTPRedirectHandler

from collector_snapshots import make_manifest, validate_manifest
from sync import SITES, Links, get, sha, encode
from wsdl_sources import collect, discover


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub API redirects are not allowed")


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


def publish_inputs(api, manifests, objects):
    files = {}
    for manifest in manifests:
        row = validate_manifest(manifest, objects.__getitem__)
        files["state/collector/" + manifest["environment"] + "/" + manifest["content_id"] + ".json"] = encode(manifest)
        for digest in [row["sha256"], *(s["sha256"] for s in row["source_files"])]:
            files["state/objects/" + digest] = objects[digest]
    blobs = {}
    for attempt in range(3):
        head = api("GET", "git/ref/heads/main")["object"]["sha"]
        tree = api("GET", "git/commits/" + head)["tree"]["sha"]
        listing = api("GET", "git/trees/" + tree + "?recursive=1")
        if listing.get("truncated"):
            raise ValueError("GitHub tree is truncated; refusing partial comparison")
        existing = {r["path"] for r in listing["tree"] if r["type"] == "blob"}
        # A manifest is immutable; seeing its path means this exact content was
        # already collected. Keep the first observation date instead of churn.
        new = {p: raw for p, raw in files.items() if p not in existing}
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
    page = SITES[environment]
    parser = Links()
    parser.feed(get(page, timeout=30, attempts=2).decode("utf-8-sig"))
    seeds = discover([dict(environment=environment, url=urljoin(page, href.strip())) for href, _ in parser.items], environment)
    objects = {}
    def blob(raw):
        digest = sha(raw)
        objects[digest] = raw
        return digest
    errors = []
    for seed in seeds:
        try:
            row = collect(seed, environment, lambda url: get(url, timeout=30, attempts=2), blob)
            return make_manifest(row, datetime.now(timezone.utc).isoformat()), objects
        except Exception as exc:
            errors.append(type(exc).__name__)
    raise ValueError("No complete " + environment + " WSDL closure: " + ", ".join(errors))


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
        from sync import write
        for manifest in manifests:
            target = args.capture_to / "collector" / manifest["environment"] / (manifest["content_id"] + ".json")
            if not target.exists():
                write(target, encode(manifest))
        for digest, raw in objects.items():
            write(args.capture_to / "objects" / digest, raw)
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
