# Revenium SE demo

Phase 1: prove a single metered LLM call lands in the Revenium dashboard.

```bash
uv sync
# .env needs REVENIUM_METERING_API_KEY plus OPENAI_API_KEY and/or ANTHROPIC_API_KEY
uv run python scripts/smoke_test.py
```

## Phase 2: Ticketfox simulated workload

```bash
uv run python -m ticketfox.run --mode baseline  --customers all --tickets 1
uv run python -m ticketfox.run --mode optimized --customers acme,cobalt --tickets 3 --debug
```

- `config/modes.yaml`: the whole baseline-vs-optimized diff (models, thresholds, context rules), pricing and the $3 spend guard.
- `config/customers.yaml`: plans, prices, assumed monthly ticket volumes, end users.
- `fixtures/tickets.json` (30 hand-written tickets) and `fixtures/kb/` (8 help-center articles).
- `--tickets N` is per customer, taken in fixture order. Modes: baseline, optimized, incident. Per-call results go to `results/<mode>-<timestamp>.jsonl`; `scripts/summarize_results.py` compares them.
- Edit `fixtures/src/*.yaml`, then `uv run python scripts/build_fixtures.py` to re-render `fixtures/tickets.json`.

## Phase 3: tagged runs, offline eval, report
- `--run-tag bulk-v1` is sent as `trace_name`. In Revenium: Data > Logs > Advanced Filter > Identifiers & Context > Trace Name.
  Sanity runs use `bulk-v1-sanity`; eval calls use `<tag>-eval`.
- The in-pipeline reviewer is a retry gate (it sees the KB the responder saw). Quality comparisons use
  `uv run python scripts/evaluate.py results/baseline-*.jsonl results/optimized-*.jsonl` (gpt-4o, temp 0, 2 passes,
  full thread + full KB), metered as product "Ticketfox Internal Eval", environment "eval".
- `uv run python scripts/summarize_results.py --tag bulk-v1` prints the customer x mode table, quality delta, margin table and sample traces.
- Spend guard ($12) counts every call/eval row carrying a run tag, across all result files.

## Phase 3.5: tiered routing + real costs
- Optimized mode routes by raw-thread size (`long_thread_tokens: 2000`): short threads keep the free-form summary + latest message;
  long threads get a structured JSON summary (summarizer task_type `thread-summary-structured`) + the last 2 raw messages + top-2 KB.
  `route` is stored on every row and sent as `trace_type` = `ticket-short` / `ticket-long` (the SDK has no free-form field).
- Spend guard ($6) is per `--run-tag`.
- Real costs: Revenium Data > Logs > Exports > Transaction Log CSV (no trace-name column; rows join to ours on transaction id).
  `results/revenium-costs-bulk.csv` is that export filtered to the bulk-v1/bulk-v2 pipeline calls (eval calls removed).
  `uv run python scripts/summarize_results.py --export results/revenium-costs-bulk.csv`

## Phase 3.6: risk-based gate, locked numbers
- `optimized_v3` (config/modes.yaml) = optimized + `long_route_gate` (gpt-4o reviewer, 0.85, 2 retries, feedback fed back to the responder) on the long route only.
  Run: `uv run python -m ticketfox.run --mode optimized_v3 --customers cobalt --tickets 10 --run-tag bulk-v3`.
- `scripts/filter_export.py` strips a raw Revenium export down to our pipeline calls (no eval rows).
- `results/FINAL_NUMBERS.md` is generated: `uv run python scripts/summarize_results.py --export results/revenium-costs-bulk.csv --final results/FINAL_NUMBERS.md`

