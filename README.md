# Revenium SE demo

Phase 1: prove a single metered LLM call lands in the Revenium dashboard.

```bash
uv sync
# .env needs REVENIUM_METERING_API_KEY plus OPENAI_API_KEY and/or ANTHROPIC_API_KEY
uv run python scripts/smoke_test.py
```
