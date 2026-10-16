"""Phase 3.5 report. Usage: uv run python scripts/summarize_results.py --export results/revenium-export-raw.csv
Costs are Revenium's own (Transaction Log CSV joined to our call rows on transaction id), not our estimates.
Quality is the offline gpt-4o judge (scripts/evaluate.py). Everything is compared to the baseline tag, paired by ticket."""
import argparse
import collections
import csv
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ticketfox import config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--export", type=Path, required=True, help="Revenium Data > Logs > Exports > Transaction Log CSV")
ap.add_argument("--baseline", default="bulk-v1", help="run tag of the baseline runs")
ap.add_argument("--tags", default="bulk-v1,bulk-v2")
args = ap.parse_args()
tags = args.tags.split(",")

rows = [json.loads(ln) for f in sorted((ROOT / "results").glob("*.jsonl")) for ln in f.read_text().splitlines()]
rows = [r for r in rows if r.get("run_tag") in tags]
real = {r["Transaction ID"]: r for r in csv.DictReader(args.export.open())}
calls = [r for r in rows if r["kind"] == "call"]
missing = [c for c in calls if c["transaction_id"] not in real]
print(f"{len(calls)} pipeline calls, {len(calls) - len(missing)} matched to the Revenium export (eval rows are never used)\n")
calls = [c for c in calls if c["transaction_id"] in real]
for c in calls:
    x = real[c["transaction_id"]]
    c["real_cost"], c["cached"], c["in"] = float(x["Total Cost"]), int(x["Cached Tokens"] or 0), int(x["Input Tokens"])

tix = {(r["run_tag"], r["mode"], r["ticket"]): r for r in rows if r["kind"] == "ticket"}
cost = collections.defaultdict(float)
for c in calls:
    cost[(c["run_tag"], c["mode"], c["ticket"])] += c["real_cost"]
ev = {(r["run_tag"], r["mode"], r["ticket"]): r for r in rows if r["kind"] == "eval"}
customers = config.load_customers()
avg = lambda xs: sum(xs) / len(xs) if xs else float("nan")
T = {9: 2.262, 29: 2.045}  # two-sided 95% t critical values for the n=10 and n=30 comparisons


def delta(keys_a, keys_b):
    """Paired (a - b) eval score per ticket id. Returns mean, 95% CI half-width, n."""
    d = [ev[a]["eval_score"] - ev[b]["eval_score"] for a, b in zip(keys_a, keys_b) if a in ev and b in ev]
    if len(d) < 3:
        return float("nan"), float("nan"), len(d)
    return st.mean(d), T.get(len(d) - 1, 1.96) * st.stdev(d) / len(d) ** 0.5, len(d)


groups = sorted({(t, m) for t, m, _ in tix}, key=lambda g: (g[1] != "baseline", g[0]))
base = (args.baseline, "baseline")
print(f"{'customer':<17}{'run':<20}{'n':>3}{'real $/t':>9}{'est $/t':>9}{'cached%':>8}{'retries':>8}{'pipeQA':>7}{'evalQA':>7}{'delta vs baseline (95% CI)':>29}")
for cust in customers:
    for tag, mode in groups:
        ks = [k for k in tix if k[:2] == (tag, mode) and tix[k]["customer"] == cust]
        if not ks:
            continue
        cs = [c for c in calls if (c["run_tag"], c["mode"]) == (tag, mode) and c["customer"] == cust]
        bk = [(base[0], base[1], k[2]) for k in ks]
        m, hw, n = delta(ks, bk) if (tag, mode) != base else (float("nan"), 0, 0)
        d = "-" if (tag, mode) == base else f"{m:+.3f} +/- {hw:.3f} (n={n})"
        print(f"{cust:<17}{mode + '/' + tag:<20}{len(ks):>3}{avg([cost[k] for k in ks]):>9.4f}{avg([tix[k]['est_cost'] for k in ks]):>9.4f}"
              f"{100 * sum(c['cached'] for c in cs) / sum(c['in'] for c in cs):>8.1f}{sum(tix[k]['retries'] for k in ks):>8}"
              f"{avg([tix[k]['qa_score'] for k in ks]):>7.3f}{avg([ev[k]['eval_score'] for k in ks if k in ev]):>7.3f}{d:>29}")

print("\nOVERALL (30 tickets each, paired by ticket)")
b_keys = [k for k in tix if k[:2] == base]
b_cost = avg([cost[k] for k in b_keys])
b_eval = avg([ev[k]["eval_score"] for k in b_keys if k in ev])
for tag, mode in groups:
    if (tag, mode) == base:
        continue
    ks = [k for k in tix if k[:2] == (tag, mode)]
    c = avg([cost[k] for k in ks])
    m, hw, n = delta(ks, [(base[0], base[1], k[2]) for k in ks])
    cs = [x for x in calls if (x["run_tag"], x["mode"]) == (tag, mode)]
    print(f"  {mode}/{tag}: real $/ticket {b_cost:.4f} -> {c:.4f} ({100 * (1 - c / b_cost):.1f}% cheaper); "
          f"eval {b_eval:.3f} -> {avg([ev[k]['eval_score'] for k in ks if k in ev]):.3f}, delta {m:+.3f} +/- {hw:.3f} (95% CI, n={n}); "
          f"within 0.05 of baseline: {'yes' if m - hw >= -0.05 else 'not shown' if m >= -0.05 else 'no'}")
for tag, mode in groups:
    cs = [x for x in calls if (x["run_tag"], x["mode"]) == (tag, mode)]
    est = sum(r["est_cost"] for r in calls if (r["run_tag"], r["mode"]) == (tag, mode))
    print(f"  {mode}/{tag}: cached-token share {100 * sum(x['cached'] for x in cs) / sum(x['in'] for x in cs):.1f}% of input tokens; "
          f"real total ${sum(x['real_cost'] for x in cs):.3f} vs our estimate ${est:.3f}")

print(f"\n{'MARGIN (real cost)':<19}{'run':<20}{'$/ticket':>9}{'tix/mo':>8}{'AI cost/mo':>12}{'plan $':>8}{'margin $':>10}{'margin %':>9}")
for cust, c in customers.items():
    for tag, mode in groups:
        ks = [k for k in tix if k[:2] == (tag, mode) and tix[k]["customer"] == cust]
        if ks:
            pt = avg([cost[k] for k in ks])
            mc = pt * c["tickets_per_month"]
            print(f"{cust:<19}{mode + '/' + tag:<20}{pt:>9.4f}{c['tickets_per_month']:>8}{mc:>12.2f}{c['monthly_price_usd']:>8}"
                  f"{c['monthly_price_usd'] - mc:>10.2f}{100 * (1 - mc / c['monthly_price_usd']):>9.1f}")

print("\nDOES THE PIPELINE REVIEWER MISS WHAT THE OFFLINE JUDGE CATCHES? (reviewer passed, judge < 0.75)")
for tag, mode in groups:
    miss = [k for k in tix if k[:2] == (tag, mode) and tix[k]["passed"] and k in ev and ev[k]["eval_score"] < 0.75]
    print(f"  {mode}/{tag}: {len(miss)} of {sum(1 for k in tix if k[:2] == (tag, mode))} tickets")
    if miss and mode == "optimized" and tag == tags[-1]:
        k = min(miss, key=lambda k: ev[k]["eval_score"])
        qa = next(r for r in rows if r["kind"] == "call" and r["agent"] == "qa-reviewer-agent" and r["run_tag"] == tag and r["ticket"] == k[2] and r.get("qa_feedback") is not None)
        print(f"    example {k[2]}: pipeline reviewer {tix[k]['qa_score']:.2f} ({qa['qa_feedback']!r}); "
              f"offline judge {ev[k]['eval_score']:.2f} ({ev[k]['passes'][0]['feedback']!r})")
