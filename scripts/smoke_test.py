"""One metered LLM call. Run with: uv run python scripts/smoke_test.py"""
import os
import time
import uuid

from dotenv import load_dotenv

load_dotenv(override=True)  # .env wins over stale shell vars
os.environ["REVENIUM_LOG_LEVEL"] = "DEBUG"  # must be set before the middleware import

import revenium_middleware  # noqa: E402

# ponytail: OpenAI preferred when both keys are set; one call, no provider abstraction.
if os.getenv("OPENAI_API_KEY"):
    import openai
    import revenium_middleware.openai  # noqa: F401  (auto-instruments on import)
    provider, model = "openai", "gpt-4o-mini"
elif os.getenv("ANTHROPIC_API_KEY"):
    import anthropic
    import revenium_middleware.anthropic  # noqa: F401
    provider, model = "anthropic", "claude-haiku-4-5"
else:
    raise SystemExit("Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env")

if not os.getenv("REVENIUM_METERING_API_KEY"):
    raise SystemExit("Set REVENIUM_METERING_API_KEY in .env")

trace_id = f"smoke-{uuid.uuid4().hex[:8]}"
usage_metadata = {
    "organizationName": "Acme Logistics",
    "productName": "Support Automation",  # backend requires organizationName + productName
    "agent": "triage-agent",
    "task_type": "ticket-triage",
    "trace_id": trace_id,
}
prompt = "Classify this ticket as billing, shipping or technical: 'My pallet never arrived.' One word."

if provider == "openai":
    r = openai.OpenAI().chat.completions.create(
        model=model, max_tokens=10, usage_metadata=usage_metadata,
        messages=[{"role": "user", "content": prompt}],
    )
    print("LLM reply:", r.choices[0].message.content)
else:
    r = anthropic.Anthropic().messages.create(
        model=model, max_tokens=10, usage_metadata=usage_metadata,
        messages=[{"role": "user", "content": prompt}],
    )
    print("LLM reply:", r.content[0].text)

print(f"provider={provider} model={model} trace_id={trace_id}")

# Metering is async on a background thread; wait for it to report.
for _ in range(20):
    s = revenium_middleware.get_metering_status()
    if s.success_count or s.error_count:
        break
    time.sleep(0.5)
print(f"metering: success={s.success_count} error={s.error_count} last_error={s.last_error}")
