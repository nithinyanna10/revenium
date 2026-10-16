"""Customer x mode table from results/*.jsonl. Usage: uv run python scripts/summarize_results.py [files...]
Uses ticket rows only; if a mode appears in several files, all of them are pooled."""
import collections
import json
import sys
from pathlib import Path

files = [Path(a) for a in sys.argv[1:]] or sorted((Path(__file__).resolve().parent.parent / "results").glob("*.jsonl"))
tix = [r for f in files for r in map(json.loads, f.read_text().splitlines()) if r["kind"] == "ticket"]
cells = collections.defaultdict(list)
for r in tix:
    cells[(r["customer"], r["mode"])].append(r)

avg = lambda xs: sum(xs) / len(xs)
print(f"{'customer':<17}{'mode':<11}{'tix':>4}{'calls/t':>8}{'in_tok/t':>10}{'$/ticket':>10}{'avg QA':>8}{'pass%':>7}{'retries':>8}")
for (cust, mode), rs in sorted(cells.items(), key=lambda kv: (kv[0][1], kv[0][0])):
    print(f"{cust:<17}{mode:<11}{len(rs):>4}{avg([r['calls'] for r in rs]):>8.1f}{avg([r['tokens_in'] for r in rs]):>10.0f}"
          f"{avg([r['est_cost'] for r in rs]):>10.4f}{avg([r['qa_score'] for r in rs]):>8.3f}"
          f"{100 * avg([r['passed'] for r in rs]):>7.0f}{sum(r['retries'] for r in rs):>8}")

print()
for mode in sorted({m for _, m in cells}):
    c, a = cells.get(("Cobalt Retail", mode)), cells.get(("Acme Logistics", mode))
    if c and a:
        print(f"{mode}: Cobalt/Acme avg cost per ticket = {avg([r['est_cost'] for r in c]) / avg([r['est_cost'] for r in a]):.1f}x")
for cust in sorted({c for c, _ in cells}):
    b, o = cells.get((cust, "baseline")), cells.get((cust, "optimized"))
    if b and o:
        cb, co = avg([r["est_cost"] for r in b]), avg([r["est_cost"] for r in o])
        print(f"{cust}: optimized vs baseline cost {100 * (co / cb - 1):+.0f}%, QA {avg([r['qa_score'] for r in o]) - avg([r['qa_score'] for r in b]):+.3f}")
