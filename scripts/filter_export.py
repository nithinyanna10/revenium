"""Keep only the Revenium export rows that are our bulk pipeline calls (matched on transaction id), so eval traces
never enter the repo. Usage: uv run python scripts/filter_export.py results/revenium-export-raw.csv results/revenium-costs-bulk.csv"""
import csv
import glob
import json
import sys

ids = {r["transaction_id"] for f in glob.glob("results/*.jsonl") for ln in open(f) for r in [json.loads(ln)]
       if r["kind"] == "call" and r.get("run_tag") in ("bulk-v1", "bulk-v2", "bulk-v3")}
src = list(csv.DictReader(open(sys.argv[1])))
keep = [r for r in src if r["Transaction ID"] in ids]
w = csv.DictWriter(open(sys.argv[2], "w", newline=""), fieldnames=list(src[0]))
w.writeheader()
w.writerows(keep)
print(f"{len(keep)} of {len(src)} rows kept ({len(ids)} pipeline calls expected)")
