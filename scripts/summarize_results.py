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
ap.add_argument("--final", type=Path, help="also write the locked demo numbers to this markdown file")
ap.add_argument("--tags", default="bulk-v1,bulk-v2,bulk-v3")
args = ap.parse_args()
tags = args.tags.split(",")

rows = [{**json.loads(ln), "_file": f.name} for f in sorted((ROOT / "results").glob("*.jsonl")) for ln in f.read_text().splitlines()]
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

print("\nOVERALL (vs baseline on the same tickets, paired by ticket)")
for tag, mode in groups:
    if (tag, mode) == base:
        continue
    ks = [k for k in tix if k[:2] == (tag, mode)]
    bks = [(base[0], base[1], k[2]) for k in ks]  # baseline on the same tickets (v3 is Cobalt only)
    b_cost, b_eval = avg([cost[k] for k in bks]), avg([ev[k]["eval_score"] for k in bks if k in ev])
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
    if miss and mode.startswith("optimized") and tag == tags[-1]:
        k = min(miss, key=lambda k: ev[k]["eval_score"])
        qa = next(r for r in rows if r["kind"] == "call" and r["agent"] == "qa-reviewer-agent" and r["run_tag"] == tag and r["ticket"] == k[2] and r.get("qa_feedback") is not None)
        print(f"    example {k[2]}: pipeline reviewer {tix[k]['qa_score']:.2f} ({qa['qa_feedback']!r}); "
              f"offline judge {ev[k]['eval_score']:.2f} ({ev[k]['passes'][0]['feedback']!r})")

# ---- diagnosis: how much of baseline Cobalt's pipeline score came from retries? (pipeline reviewer, NOT the offline judge;
# first drafts were never stored, so they could not be re-judged offline)
def first_attempt(tag, mode, ticket):
    return next(r["qa_score"] for r in calls if r["ticket"] == ticket and r["run_tag"] == tag and r["mode"] == mode
                and r["agent"] == "qa-reviewer-agent" and r["retry"] == 0)


print("\nFIRST ATTEMPT vs FINAL, pipeline reviewer score (not the offline judge)")
for tag, mode in groups:
    ks = [k for k in tix if k[:2] == (tag, mode) and tix[k]["customer"] == "Cobalt Retail"]
    firsts = [first_attempt(tag, mode, k[2]) for k in ks]
    if ks:
        print(f"  Cobalt {mode}/{tag}: first attempt {avg(firsts):.3f} -> final {avg([tix[k]['qa_score'] for k in ks]):.3f}; "
              f"tickets that retried: {sum(tix[k]['retries'] > 0 for k in ks)} of {len(ks)}; ended below the gate: {sum(not tix[k]['passed'] for k in ks)}")
v2, v3 = ("bulk-v2", "optimized"), ("bulk-v3", "optimized_v3")
ks3 = [k for k in tix if k[:2] == v3]
if ks3:
    m, hw, n = delta(ks3, [(v2[0], v2[1], k[2]) for k in ks3])
    print(f"\nCobalt v3 vs v2 (paired): eval {m:+.3f} +/- {hw:.3f} (95% CI, n={n}); real $/ticket "
          f"{avg([cost[(v2[0], v2[1], k[2])] for k in ks3]):.4f} -> {avg([cost[k] for k in ks3]):.4f}")

if args.final:
    lock = {c: (("bulk-v2", "optimized") if c != "Cobalt Retail" else None) for c in customers}
    m3, hw3, _ = delta(ks3, [(v2[0], v2[1], k[2]) for k in ks3]) if ks3 else (float("nan"),) * 3
    lock["Cobalt Retail"] = v3 if ks3 and m3 > 0 else v2  # rule from the brief: v3 only if better than v2 on quality
    locked = []  # (customer, key-of-locked-run, key-of-baseline) per ticket
    for cust in customers:
        for k in tix:
            if k[:2] == lock[cust] and tix[k]["customer"] == cust:
                locked.append((cust, k, (base[0], base[1], k[2])))
    L = lambda rows_: [r_ for r_ in rows_]
    f = lambda x, d=4: f"{x:.{d}f}"
    out = ["# Locked demo numbers", "",
           "Ticketfox AI Assistant, simulated workload: 3 fictional customers, 10 hand-written tickets each. Costs are Revenium's own "
           "(Transaction Log CSV), quality is an offline gpt-4o judge. Nothing here is a production measurement.", "",
           "## What is being compared", "",
           "- **Baseline** (agent_version 1.0.0): gpt-4o on every step, full raw thread plus the whole knowledge base, strict QA gate with up to 4 retries. Run tag `bulk-v1`.",
           "- **Optimized**: cheaper models for triage/summary/review, a summary instead of the full thread, top-2 knowledge-base articles, fewer retries.",
           "  Acme and Bluepeak use optimized v2 (2.0.0, tag `bulk-v2`). Cobalt Retail uses "
           + ("optimized v3 (2.1.0, tag `bulk-v3`: v2 plus a gpt-4o reviewer, 0.85 threshold and up to 2 retries on long threads) because its offline score was higher than v2's (rule set in advance), "
              f"though the difference ({m3:+.3f} +/- {hw3:.3f}, 95% CI, n=10) is not statistically distinguishable, and v3 costs about {avg([cost[k] for k in ks3]) / avg([cost[(v2[0], v2[1], k[2])] for k in ks3]):.0f}x v2 per Cobalt ticket."
              if lock["Cobalt Retail"] == v3 else "optimized v2, because v3 did not score higher than v2 on the offline judge."), "",
           "## Cost per ticket (real, from the Revenium export)", "",
           "| customer | run | baseline $/ticket | locked $/ticket | reduction |", "|---|---|---|---|---|"]
    tot_b = tot_l = 0.0
    for cust, c in customers.items():
        mine = [x for x in locked if x[0] == cust]
        b, l = avg([cost[x[2]] for x in mine]), avg([cost[x[1]] for x in mine])
        tot_b += b * c["tickets_per_month"]; tot_l += l * c["tickets_per_month"]
        out.append(f"| {cust} | {lock[cust][1]}/{lock[cust][0]} | {f(b)} | {f(l)} | {100 * (1 - l / b):.1f}% |")
    ab, al = avg([cost[x[2]] for x in locked]), avg([cost[x[1]] for x in locked])
    out += ["", f"- Simple average over all 30 tickets: ${f(ab)} -> ${f(al)} per ticket, **{100 * (1 - al / ab):.1f}% cheaper**.",
            f"- Weighted by the assumed monthly ticket volumes in `config/customers.yaml`: ${tot_b:,.0f} -> ${tot_l:,.0f} per month across the three customers, **{100 * (1 - tot_l / tot_b):.1f}% cheaper**.", "",
            "## Quality (offline judge, gpt-4o, temperature 0, two passes averaged; same 4-criterion rubric)", "",
            "| customer | baseline | locked | delta (95% CI) |", "|---|---|---|---|"]
    for cust in customers:
        mine = [x for x in locked if x[0] == cust]
        m, hw, n = delta([x[1] for x in mine], [x[2] for x in mine])
        out.append(f"| {cust} | {f(avg([ev[x[2]]['eval_score'] for x in mine]), 3)} | {f(avg([ev[x[1]]['eval_score'] for x in mine]), 3)} | {m:+.3f} +/- {hw:.3f} (n={n}) |")
    m, hw, n = delta([x[1] for x in locked], [x[2] for x in locked])
    out += ["", f"- Overall: baseline {avg([ev[x[2]]['eval_score'] for x in locked]):.3f}, locked {avg([ev[x[1]]['eval_score'] for x in locked]):.3f}; "
            f"paired delta **{m:+.3f} +/- {hw:.3f}** (95% CI, n={n} tickets)."]
    verdict = (f"The 95% interval ({m - hw:+.3f} to {m + hw:+.3f}) includes zero, so this run cannot distinguish the optimized pipeline from baseline."
               if abs(m) <= hw else f"The optimized pipeline scores lower than baseline ({m:+.3f}); the 95% interval ({m - hw:+.3f} to {m + hw:+.3f}) just excludes zero, so treat it as a real but small-sample drop.")
    target = "within" if m - hw >= -0.05 else "not shown to be within"
    out += [f"- Honest reading: {verdict} The drop is {target} the 0.05 target we set. Run-to-run noise on unchanged code paths was about 0.04 per customer (Acme and Bluepeak v1 vs v2), "
            "and with 10 tickets per customer the per-customer intervals are wide.", "",
            "## Gross margin per customer (AI cost only; plan prices and monthly volumes from `config/customers.yaml`, volumes are assumed)", "",
            "| customer | plan $/mo | tickets/mo | baseline AI cost/mo | baseline margin | locked AI cost/mo | locked margin |", "|---|---|---|---|---|---|---|"]
    for cust, c in customers.items():
        mine = [x for x in locked if x[0] == cust]
        cb, cl = avg([cost[x[2]] for x in mine]) * c["tickets_per_month"], avg([cost[x[1]] for x in mine]) * c["tickets_per_month"]
        P = c["monthly_price_usd"]
        out.append(f"| {cust} | {P} | {c['tickets_per_month']} | {cb:,.2f} | {P - cb:,.2f} ({100 * (1 - cb / P):.1f}%) | {cl:,.2f} | {P - cl:,.2f} ({100 * (1 - cl / P):.1f}%) |")
    out += ["", "Eval-judge cost, infrastructure and the Revenium subscription are not in these margins."]
    cb_b = [k for k in tix if k[:2] == base and tix[k]["customer"] == "Cobalt Retail"]
    cb_3 = [k for k in tix if k[:2] == v3]
    out += ["", "## Where Cobalt's quality gap comes from (pipeline reviewer scores, not the offline judge)", "",
            f"- Both runs below were scored by a gpt-4o reviewer: baseline first attempt {avg([first_attempt(*k[:2], k[2]) for k in cb_b]):.3f} -> final {avg([tix[k]['qa_score'] for k in cb_b]):.3f}; "
            f"v3 first attempt {avg([first_attempt(*k[:2], k[2]) for k in cb_3]):.3f} -> final {avg([tix[k]['qa_score'] for k in cb_3]):.3f}.",
            "- Retries lift the score by roughly 0.03-0.06 in both; the first-attempt gap (about 0.09) is larger, so most of the gap looks like the responder's reduced context, not missing retries. "
            "Caveat: the baseline reviewer saw the whole knowledge base and v3's saw the top 2 articles, and first drafts were never judged offline."]
    # the reviewer-miss example
    ex = ("CRT-002", "bulk-v2", "optimized")
    q = lambda tag, mode: next(r for r in rows if r["kind"] == "call" and r["agent"] == "qa-reviewer-agent" and r["run_tag"] == tag and r["mode"] == mode and r["ticket"] == "CRT-002" and r.get("qa_feedback") is not None)
    q2 = q("bulk-v2", "optimized")
    out += ["", "## Example: the in-pipeline reviewer passed a reply the offline judge rejected (CRT-002, optimized v2)", "",
            f"- Pipeline reviewer (gpt-4o-mini): {tix[('bulk-v2', 'optimized', 'CRT-002')]['qa_score']:.2f}. Its feedback: \"{q2['qa_feedback']}\"",
            f"- Offline judge (gpt-4o): {ev[('bulk-v2', 'optimized', 'CRT-002')]['eval_score']:.2f}. Its feedback: \"{ev[('bulk-v2', 'optimized', 'CRT-002')]['passes'][0]['feedback']}\""]
    if ("bulk-v3", "optimized_v3", "CRT-002") in tix:
        t3 = tix[("bulk-v3", "optimized_v3", "CRT-002")]
        out.append(f"- Same ticket under v3: pipeline reviewer {t3['qa_score']:.2f}, offline judge {ev[('bulk-v3', 'optimized_v3', 'CRT-002')]['eval_score']:.2f}.")
    miss = {(t, m_): sum(1 for k in tix if k[:2] == (t, m_) and tix[k]["passed"] and k in ev and ev[k]["eval_score"] < 0.75) for t, m_ in groups}
    out += ["", "Reviewer-pass / judge-fail counts (reviewer passed, judge below 0.75): " + "; ".join(f"{m_}/{t}: {v} of {sum(1 for k in tix if k[:2] == (t, m_))}" for (t, m_), v in miss.items()) + ".", "",
            "## Limits", "",
            "- 10 tickets per customer; Cobalt's interval is the widest. First drafts were not stored, so \"how much quality came from retries\" was not measured with the offline judge.",
            "- The judge is gpt-4o, the same family as the responder, and was only checked against itself (two temperature-0 passes agree closely).",
            "- Monthly volumes are assumptions, not customer data. Prices and cached-token discounts are as billed in the Revenium export.", "",
            "## Where each number comes from", ""]
    srcs = collections.defaultdict(set)
    for r in rows:
        if (r.get("run_tag"), r.get("mode")) in {lock[c] for c in customers} | {base}:
            srcs[f"{r['mode']}/{r['run_tag']}"].add((r["kind"] if r["kind"] != "call" else "ticket", r["_file"]))
    for key, fs in sorted(srcs.items()):
        out.append(f"- `{key}`: " + ", ".join(sorted({f'`results/{n}`' for _, n in fs})))
    out.append(f"- Real costs and cached tokens: `{args.export.relative_to(ROOT) if args.export.is_absolute() else args.export}` (Revenium Transaction Log CSV, pipeline calls only), joined to the files above on transaction id.")
    out.append("- Regenerate: `uv run python scripts/summarize_results.py --export results/revenium-costs-bulk.csv --final results/FINAL_NUMBERS.md`")
    args.final.write_text("\n".join(out) + "\n")
    print(f"\nwrote {args.final}")
