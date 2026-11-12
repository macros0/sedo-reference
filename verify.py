"""Offline acceptance checks on the supplied researched snapshot."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from sync import SITES,sync
from catalog import dependency_set,derive
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
        obj=json.loads((data/"fields"/(r["revision_id"]+".json")).read_bytes())
        f=obj["fields"];assert f[0]["name"]==r["root"]
        assert all(f[c]["parent"]==i for i,n in enumerate(f) for c in n["children"])
        # These semantic examples describe the original snapshot, not future SFR rules.
        if r["number"]=="10110" and r["revision_id"] in baseline_ids:
            p={n["path"]:n for n in f}
            assert p["benefitResponse/benefit1/assignmentParams/contractsInfo/workerAndGPHcontract/GPHcontract"]["base"]=="xs:boolean"
            assert dict(p["benefitResponse/benefit1/calculationData/calcConditionList/calcCondition"]["values"])["46"]
    # Replay the exact source bytes to test idempotence and failed retrieval.
    urls={SITES[k]:v for k,v in snapshot["pages"].items()}
    urls.update({r["url"]:r["sha256"] for r in snapshot["entries"] if "sha256" in r})
    if all((state/"objects"/v).exists() for v in urls.values()):
        def cached(u):return (state/"objects"/urls[u]).read_bytes()
        with TemporaryDirectory() as tmp:
            base=Path(tmp)
            with patch("sync.get",side_effect=cached):
                sync(base);first=(base/"current.json").read_bytes();sync(base)
            assert first==(base/"current.json").read_bytes() and not any(json.loads((base/"last-run.json").read_bytes())["diff"].values())
            def failed(u):
                if u.endswith("xsd_19_09_26.zip"):raise OSError("simulated failure")
                return cached(u)
            with patch("sync.get",side_effect=failed):
                try:sync(base)
                except ValueError:pass
                else:raise AssertionError("failed download accepted")
            assert first==(base/"current.json").read_bytes()
    print("Verified revision isolation, field trees and code 46.")
if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser();p.add_argument("--state",type=Path,default=Path("state"));p.add_argument("--out",type=Path,default=Path("data"))
    a=p.parse_args();verify(a.state,a.out)

