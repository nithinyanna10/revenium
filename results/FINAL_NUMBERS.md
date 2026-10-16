# Locked demo numbers

Ticketfox AI Assistant, simulated workload: 3 fictional customers, 10 hand-written tickets each. Costs are Revenium's own (Transaction Log CSV), quality is an offline gpt-4o judge. Nothing here is a production measurement.

## What is being compared

- **Baseline** (agent_version 1.0.0): gpt-4o on every step, full raw thread plus the whole knowledge base, strict QA gate with up to 4 retries. Run tag `bulk-v1`.
- **Optimized**: cheaper models for triage/summary/review, a summary instead of the full thread, top-2 knowledge-base articles, fewer retries.
  Acme and Bluepeak use optimized v2 (2.0.0, tag `bulk-v2`). Cobalt Retail uses optimized v3 (2.1.0, tag `bulk-v3`: v2 plus a gpt-4o reviewer, 0.85 threshold and up to 2 retries on long threads) because its offline score was higher than v2's (rule set in advance), though the difference (+0.049 +/- 0.138, 95% CI, n=10) is not statistically distinguishable, and v3 costs about 5x v2 per Cobalt ticket.

## Cost per ticket (real, from the Revenium export)

| customer | run | baseline $/ticket | locked $/ticket | reduction |
|---|---|---|---|---|
| Acme Logistics | optimized/bulk-v2 | 0.0278 | 0.0047 | 83.2% |
| Bluepeak Health | optimized/bulk-v2 | 0.0284 | 0.0050 | 82.6% |
| Cobalt Retail | optimized_v3/bulk-v3 | 0.0930 | 0.0419 | 55.0% |

- Simple average over all 30 tickets: $0.0498 -> $0.0172 per ticket, **65.5% cheaper**.
- Weighted by the assumed monthly ticket volumes in `config/customers.yaml`: $671 -> $271 per month across the three customers, **59.7% cheaper**.

## Quality (offline judge, gpt-4o, temperature 0, two passes averaged; same 4-criterion rubric)

| customer | baseline | locked | delta (95% CI) |
|---|---|---|---|
| Acme Logistics | 0.988 | 0.922 | -0.065 +/- 0.097 (n=10) |
| Bluepeak Health | 0.956 | 0.904 | -0.053 +/- 0.111 (n=10) |
| Cobalt Retail | 0.894 | 0.816 | -0.077 +/- 0.154 (n=10) |

- Overall: baseline 0.946, locked 0.881; paired delta **-0.065 +/- 0.062** (95% CI, n=30 tickets).
- Honest reading: The optimized pipeline scores lower than baseline (-0.065); the 95% interval (-0.127 to -0.003) just excludes zero, so treat it as a real but small-sample drop. The drop is not shown to be within the 0.05 target we set. Run-to-run noise on unchanged code paths was about 0.04 per customer (Acme and Bluepeak v1 vs v2), and with 10 tickets per customer the per-customer intervals are wide.

## Gross margin per customer (AI cost only; plan prices and monthly volumes from `config/customers.yaml`, volumes are assumed)

| customer | plan $/mo | tickets/mo | baseline AI cost/mo | baseline margin | locked AI cost/mo | locked margin |
|---|---|---|---|---|---|---|
| Acme Logistics | 299 | 1500 | 41.75 | 257.25 (86.0%) | 7.03 | 291.97 (97.6%) |
| Bluepeak Health | 799 | 2500 | 71.00 | 728.00 (91.1%) | 12.38 | 786.62 (98.5%) |
| Cobalt Retail | 799 | 6000 | 558.15 | 240.85 (30.1%) | 251.20 | 547.80 (68.6%) |

Eval-judge cost, infrastructure and the Revenium subscription are not in these margins.

## Where Cobalt's quality gap comes from (pipeline reviewer scores, not the offline judge)

- Both runs below were scored by a gpt-4o reviewer: baseline first attempt 0.835 -> final 0.897; v3 first attempt 0.747 -> final 0.780.
- Retries lift the score by roughly 0.03-0.06 in both; the first-attempt gap (about 0.09) is larger, so most of the gap looks like the responder's reduced context, not missing retries. Caveat: the baseline reviewer saw the whole knowledge base and v3's saw the top 2 articles, and first drafts were never judged offline.

## Example: the in-pipeline reviewer passed a reply the offline judge rejected (CRT-002, optimized v2)

- Pipeline reviewer (gpt-4o-mini): 1.00. Its feedback: "All aspects of the reply are accurate, complete, professional, and offer concrete next steps."
- Offline judge (gpt-4o): 0.53. Its feedback: "The reply lacks a specific resolution date and does not address the customer's threat to dispute with the bank. It also incorrectly states the inspection timeframe and fails to acknowledge previous contacts."
- Same ticket under v3: pipeline reviewer 0.95, offline judge 0.81.

Reviewer-pass / judge-fail counts (reviewer passed, judge below 0.75): baseline/bulk-v1: 1 of 30; optimized/bulk-v1: 5 of 30; optimized/bulk-v2: 6 of 30; optimized_v3/bulk-v3: 0 of 10.

## Limits

- 10 tickets per customer; Cobalt's interval is the widest. First drafts were not stored, so "how much quality came from retries" was not measured with the offline judge.
- The judge is gpt-4o, the same family as the responder, and was only checked against itself (two temperature-0 passes agree closely).
- Monthly volumes are assumptions, not customer data. Prices and cached-token discounts are as billed in the Revenium export.

## Where each number comes from

- `baseline/bulk-v1`: `results/baseline-20261016-180152.jsonl`, `results/eval-20261016-180848.jsonl`
- `optimized/bulk-v2`: `results/eval-20261016-181859.jsonl`, `results/optimized-20261016-181521.jsonl`
- `optimized_v3/bulk-v3`: `results/eval-20261016-183110.jsonl`, `results/optimized_v3-20261016-182908.jsonl`
- Real costs and cached tokens: `results/revenium-costs-bulk.csv` (Revenium Transaction Log CSV, pipeline calls only), joined to the files above on transaction id.
- Regenerate: `uv run python scripts/summarize_results.py --export results/revenium-costs-bulk.csv --final results/FINAL_NUMBERS.md`
