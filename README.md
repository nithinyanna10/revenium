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

