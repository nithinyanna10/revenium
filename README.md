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
