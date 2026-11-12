"""Update inventory and generate per-revision trees for review; no publication."""
import argparse
import json
from pathlib import Path
from sync import sync
from catalog import export
from fields import build
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--state",type=Path,default=Path("state"));p.add_argument("--out",type=Path,default=Path("data"));p.add_argument("--offline",action="store_true");args=p.parse_args()
    if not args.offline:sync(args.state)
    index=args.out/"catalog.json"
    export(args.state,index)
    build(index,args.state,args.out/"fields")
    status=json.loads((args.out/"fields"/"index.json").read_bytes())
    if any(r["status"] not in ("ready","document_only") for r in status): raise SystemExit("Нужна проверка новых схем. См. fields/index.json")
if __name__=="__main__":main()

