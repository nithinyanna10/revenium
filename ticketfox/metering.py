"""All Revenium metadata building and every OpenAI call live here.

Importing revenium_middleware.openai auto-instruments the OpenAI client; the only thing a call
needs to be metered is the usage_metadata kwarg built by build_metadata().
"""
from typing import NamedTuple

import openai
import revenium_middleware  # noqa: F401
import revenium_middleware.openai  # noqa: F401  (auto-instruments on import)

PRODUCT = "Ticketfox AI Assistant"
ENVIRONMENT = "demo"


class SpendLimitExceeded(RuntimeError):
    pass


class Spend:
    """Running cost estimate from token counts x pricing table. Aborts the run past the limit."""

    def __init__(self, limit, pricing, start=0.0):
        self.limit, self.pricing, self.total = limit, pricing, start
        self.on_call = None  # optional hook(cost, agent) after every priced call

    def check(self):
        if self.total >= self.limit:
            raise SpendLimitExceeded(f"estimated spend ${self.total:.4f} reached limit ${self.limit:.2f}")

    def add(self, model, tokens_in, tokens_out, cached=0, agent=None):
        p = self.pricing[model]
        cost = ((tokens_in - cached) * p["input"] + cached * p["cached_input"] + tokens_out * p["output"]) / 1_000_000
        self.total += cost
        if self.on_call:
            self.on_call(cost, agent)
        self.check()
        return cost


class Call(NamedTuple):
    text: str
    id: str  # provider response id; Revenium records it as the transaction id
    tokens_in: int
    tokens_out: int
    cost: float
    cached: int = 0  # prompt tokens served from OpenAI's prompt cache (billed at the cached rate)


def build_metadata(ticket, customer, run_id, agent_version, *, agent, task_type, step, parent=None, retry=0, run_tag=None, trace_class=None):
    user = next(u for u in customer["users"] if u["id"] == ticket["user"])
    trace_id = f"{ticket['id']}-{run_id}"  # one trace per ticket per run
    md = {
        "organizationName": customer["name"],
        "productName": PRODUCT,
        "subscriber": {"id": user["id"], "email": user["email"]},
        "agent": agent,
        "task_type": task_type,
        "trace_id": trace_id,
        "ticket_id": ticket["id"],
        "agentic_job_id": f"job-{trace_id}",
        "transaction_name": step,
        "environment": ENVIRONMENT,
        "agent_version": agent_version,
        "retry_number": retry,
    }
    if parent:
        md["parent_transaction_id"] = parent
    if trace_class:
        md["trace_type"] = f"ticket-{trace_class}"  # the SDK has no free-form field; trace_type is a filterable Logs column
    if run_tag:
        md["trace_name"] = run_tag  # a Data > Logs column: filter/group on it to isolate one run
    return md


_client = None


def chat(spend, model, messages, metadata, *, max_tokens, json_mode=False, temperature=None):
    global _client
    _client = _client or openai.OpenAI()
    spend.check()
    extra = {"response_format": {"type": "json_object"}} if json_mode else {}
    if temperature is not None:
        extra["temperature"] = temperature
    r = _client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, usage_metadata=metadata, **extra
    )
    tin, tout = r.usage.prompt_tokens, r.usage.completion_tokens
    cached = getattr(r.usage.prompt_tokens_details, "cached_tokens", 0) or 0
    return Call(r.choices[0].message.content or "", r.id, tin, tout, spend.add(model, tin, tout, cached, metadata.get("agent")), cached)
