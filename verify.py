"""Offline acceptance checks on the supplied researched snapshot."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from sync import RedirectedBytes,SITES,resource_url,sync
from sync import write
from catalog import dependency_set,derive

def failure_probe_url(snapshot, requests):
    requested = {resource_url(url) for url in requests}
    for entry in snapshot["entries"]:
        url = resource_url(entry.get("download_url") or entry["url"])
        if entry.get("kind") == "zip" and entry.get("source_type") != "wsdl" and "sha256" in entry and url in requested:
            return url
    return next(SITES[env] for env in snapshot["pages"] if SITES[env] in requested)


def fetch_archived(state, snapshot):
    from sync import sha
    urls = {SITES[k]: v for k, v in snapshot["pages"].items()}
    redirects = dict(snapshot.get("page_redirects", {}))
    def remember(url, digest):
        key = resource_url(url)
        if urls.setdefault(key, digest) != digest:
            raise ValueError("Conflicting archived bytes for " + key)
    for row in snapshot["entries"]:
        if "sha256" in row and row.get("source_type") != "wsdl":
            remember(row.get("download_url") or row["url"], row["sha256"])
        for source in row.get("source_files", []):
            remember(source["url"], source["sha256"])
            if source.get("final_url"):
                redirects[source["url"]] = source["final_url"]
    missing = sorted({digest for digest in urls.values() if not (state/"objects"/digest).is_file()})
    if missing:
        raise ValueError("Missing required replay objects: " + ", ".join(missing))
    def cached(url, **kwargs):
        key = resource_url(url)
        digest = urls[key]
        raw = (state/"objects"/digest).read_bytes()
        if sha(raw) != digest:
            raise ValueError("Archived source digest mismatch: " + key)
        return RedirectedBytes(raw, redirects[key]) if key in redirects else raw
    return cached


def verify(state,data):
    current=json.loads((state/"current.json").read_bytes())["snapshot"]
    snapshot=json.loads((state/"snapshots"/(current+".json")).read_bytes())
    history=json.loads((state/"history.json").read_bytes())
    baseline=derive(json.loads((state/"snapshots"/(history[0]+".json")).read_bytes()))
    baseline_ids={r["revision_id"] for r in baseline["revisions"]}
    revisions=json.loads((data/"catalog.json").read_bytes())["revisions"]
    pair=[r for r in revisions if r["number"]=="10110"]
    assert {r["environment"] for r in pair}=={"test","prod"}
    assert len(set(r["revision_id"] for r in pair))==len(pair)
    assert all(r["root"]=="benefitResponse" for r in pair if r["revision_id"] in baseline_ids)
    for r in revisions:
        if r["status"]=="document_only" and r.get("source_type")=="manual":continue
        obj=json.loads((data/"fields"/(r["revision_id"]+".json")).read_bytes())
        assert obj.get("revision_id") == r["revision_id"], "Field revision identity mismatch"
        f=obj["fields"];assert f[0]["name"]==r["root"]
        assert all(f[c]["parent"]==i for i,n in enumerate(f) for c in n["children"])
        # These semantic examples describe the original snapshot, not future SFR rules.
        if r["number"]=="10110" and r["revision_id"] in baseline_ids:
            p={n["path"]:n for n in f}
            assert p["benefitResponse/benefit1/assignmentParams/contractsInfo/workerAndGPHcontract/GPHcontract"]["base"]=="xs:boolean"
            assert dict(p["benefitResponse/benefit1/calculationData/calcConditionList/calcCondition"]["values"])["46"]
    # Replay the exact source bytes to test idempotence and failed retrieval.
    cached = fetch_archived(state, snapshot)
    wsdl_entries = [row for row in snapshot["entries"]
                    if row.get("source_type") == "wsdl" or row.get("kind") == "wsdl_status"]
    from collector_snapshots import make_manifest, validate_manifest
    for row in wsdl_entries:
        if row.get("source_type") == "wsdl":
            library = {key: value for key, value in row.items() if key != "retrieval_status"}
            validate_manifest(make_manifest(library, "2000-01-01T00:00:00+00:00"),
                              lambda digest: (state/"objects"/digest).read_bytes())
    with TemporaryDirectory() as tmp:
        base=Path(tmp)
        replay = dict(wsdl_entries=wsdl_entries, snapshot_format=snapshot.get("format", 1))
        # These are inputs of this exact snapshot, independent of today's
        # collector configuration, links and availability.
        for row in wsdl_entries:
            if row.get("sha256"):
                for digest in [row["sha256"], *(source["sha256"] for source in row.get("source_files", []))]:
                    write(base/"objects"/digest, (state/"objects"/digest).read_bytes())
        with patch("sync.get",side_effect=cached) as downloads:
            sync(base, **replay);first=(base/"current.json").read_bytes()
            if snapshot.get("format") in (1, 2, 3, 4, 5, 6, 7):
                assert json.loads(first)["snapshot"] == current, "Replayed snapshot differs from expected current snapshot " + current
            sync(base, **replay)
        assert first==(base/"current.json").read_bytes() and not any(json.loads((base/"last-run.json").read_bytes())["diff"].values())
        probe_url = failure_probe_url(snapshot, (call.args[0] for call in downloads.call_args_list))
        def failed(u,**kwargs):
            if u == probe_url:raise OSError("simulated failure")
            return cached(u)
        with patch("sync.get",side_effect=failed):
            try:sync(base, **replay)
            except (ValueError, OSError):pass
            else:raise AssertionError("failed download accepted")
        assert first==(base/"current.json").read_bytes()
    print("Verified revision isolation, field trees and code 46.")
if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser();p.add_argument("--state",type=Path,default=Path("state"));p.add_argument("--out",type=Path,default=Path("data"))
    a=p.parse_args();verify(a.state,a.out)

