"""Phase 3 report from results/*.jsonl. Usage: uv run python scripts/summarize_results.py [--tag bulk-v1]
Pipeline rows (kind=ticket/call) give cost, tokens, retries, in-pipeline QA; eval rows (scripts/evaluate.py)
give the offline gpt-4o score, which is the source of truth for quality comparisons."""
import argparse
import collections
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ticketfox import config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--tag", default="bulk-v1")
ap.add_argument("--samples", type=int, default=3, help="baseline traces to list for the Revenium cost cross-check")
args = ap.parse_args()

rows = [json.loads(ln) for f in sorted((ROOT / "results").glob("*.jsonl")) for ln in f.read_text().splitlines()]
rows = [r for r in rows if r.get("run_tag") == args.tag]
tix = [r for r in rows if r["kind"] == "ticket"]
evals = {(r["mode"], r["ticket"]): r for r in rows if r["kind"] == "eval"}
customers = config.load_customers()
avg = lambda xs: sum(xs) / len(xs) if xs else float("nan")
cells = collections.defaultdict(list)
for r in tix:
    cells[(r["customer"], r["mode"])].append(r)

print(f"tag {args.tag}: {len(tix)} tickets, {len(evals)} evaluated\n")
print(f"{'customer':<17}{'mode':<10}{'n':>3}{'$/ticket':>9}{'in_tok':>8}{'retries':>8}{'pipeQA':>8}{'evalQA':>8}{'pass-gap':>9}")
for (cust, mode), rs in sorted(cells.items(), key=lambda kv: (kv[0][0], kv[0][1])):
    ev = [evals[(mode, r["ticket"])] for r in rs if (mode, r["ticket"]) in evals]
    print(f"{cust:<17}{mode:<10}{len(rs):>3}{avg([r['est_cost'] for r in rs]):>9.4f}{avg([r['tokens_in'] for r in rs]):>8.0f}"
          f"{sum(r['retries'] for r in rs):>8}{avg([r['qa_score'] for r in rs]):>8.3f}"
          f"{avg([e['eval_score'] for e in ev]):>8.3f}{avg([e['disagreement'] for e in ev]):>9.3f}")
print("pass-gap = mean |pass1 - pass2| of the two temp-0 judge passes\n")

# overall
by_mode = {m: [r for r in tix if r["mode"] == m] for m in ("baseline", "optimized")}
if all(by_mode.values()):
    cb, co = (avg([r["est_cost"] for r in by_mode[m]]) for m in ("baseline", "optimized"))
    print(f"OVERALL avg $/ticket: baseline {cb:.4f} -> optimized {co:.4f} ({100 * (1 - co / cb):.1f}% reduction)")
    pairs = [(evals[("baseline", t)]["eval_score"], evals[("optimized", t)]["eval_score"])
             for t in {r["ticket"] for r in by_mode["baseline"]} if ("baseline", t) in evals and ("optimized", t) in evals]
    if len(pairs) > 2:
        d = [o - b for b, o in pairs]
        m, se = st.mean(d), st.stdev(d) / len(d) ** 0.5
        lo, hi = m - 2.05 * se, m + 2.05 * se  # t ~ 2.05 at 29 df
        print(f"OVERALL eval quality: baseline {st.mean(b for b, _ in pairs):.3f}, optimized {st.mean(o for _, o in pairs):.3f}; "
              f"paired delta {m:+.3f} (n={len(pairs)}, 95% CI {lo:+.3f}..{hi:+.3f}); "
              f"judge pass-gap {avg([e['disagreement'] for e in evals.values()]):.3f}")
        for cust in customers:
            dc = [evals[("optimized", r["ticket"])]["eval_score"] - evals[("baseline", r["ticket"])]["eval_score"]
                  for r in cells[(cust, "baseline")] if ("optimized", r["ticket"]) in evals and ("baseline", r["ticket"]) in evals]
            if len(dc) > 1:
                print(f"  {cust:<17} delta {st.mean(dc):+.3f} +/- {st.stdev(dc) / len(dc) ** 0.5:.3f} (se, n={len(dc)})")
        print("  verdict:", "CI includes 0 -> not distinguishable from noise" if lo <= 0 <= hi
              else ("optimized is measurably " + ("lower" if hi < 0 else "higher")))
        print(f"  within the 0.05 target? {'yes' if lo >= -0.05 else 'cannot rule out a drop larger than 0.05' if m >= -0.05 else 'no'}")

# margin: AI cost only (no eval, no infra), at the assumed volumes in customers.yaml
print(f"\n{'MARGIN':<17}{'mode':<10}{'$/ticket':>9}{'tix/mo':>8}{'AI cost/mo':>12}{'plan $':>8}{'margin $':>10}{'margin %':>9}")
for cust, c in customers.items():
    for mode in ("baseline", "optimized"):
        rs = cells.get((cust, mode))
        if rs:
            pt = avg([r["est_cost"] for r in rs])
            cost = pt * c["tickets_per_month"]
            print(f"{cust:<17}{mode:<10}{pt:>9.4f}{c['tickets_per_month']:>8}{cost:>12.2f}{c['monthly_price_usd']:>8}"
                  f"{c['monthly_price_usd'] - cost:>10.2f}{100 * (1 - cost / c['monthly_price_usd']):>9.1f}")

# traces to cross-check against Revenium (Data > Logs, filter trace_name == tag)
calls = [r for r in rows if r["kind"] == "call" and r["mode"] == "baseline"]
print("\nSAMPLE TRACES (estimated per-call cost; compare with Revenium):")
seen = set()
for cust in customers:
    for t in sorted({r["trace_id"] for r in calls if r["customer"] == cust}):
        if len(seen) >= args.samples or cust in seen:
            break
        seen.add(cust)
        cs = [r for r in calls if r["trace_id"] == t]
        print(f"  {t} ({cust})")
        for r in cs:
            print(f"    {r['step']:<20}{r['model']:<13}{r['tokens_in']:>6} in {r['tokens_out']:>4} out  ${r['est_cost']:.6f}  {r['transaction_id']}")
        print(f"    TOTAL ${sum(r['est_cost'] for r in cs):.6f}")
