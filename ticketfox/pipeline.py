"""triage -> draft -> QA (with retries) for one ticket. One Revenium trace per ticket."""
from ticketfox import agents
from ticketfox.metering import build_metadata, chat


class TicketRun:
    def __init__(self, ticket, customer, cfg, kb, spend, run_id):
        self.ticket, self.customer, self.cfg, self.kb = ticket, customer, cfg, kb
        self.spend, self.run_id = spend, run_id
        self.parent = None  # previous call's transaction id -> parent_transaction_id of the next
        self.calls = self.tokens_in = self.tokens_out = 0
        self.cost = 0.0

    def call(self, role, agent, task_type, step, messages, *, retry=0, json_mode=False, max_tokens=300):
        md = build_metadata(self.ticket, self.customer, self.run_id,
                            agent=agent, task_type=task_type, step=step, parent=self.parent, retry=retry)
        c = chat(self.spend, self.cfg["models"][role], messages, md, max_tokens=max_tokens, json_mode=json_mode)
        self.parent = c.id
        self.calls += 1
        self.tokens_in += c.tokens_in
        self.tokens_out += c.tokens_out
        self.cost += c.cost
        return c.text


def _score(review):
    try:
        return float(review.get("score", 0.0))
    except (TypeError, ValueError):
        return 0.0


def run_ticket(ticket, customer, cfg, kb, spend, run_id):
    t = TicketRun(ticket, customer, cfg, kb, spend, run_id)
    triage = agents.parse_json(t.call(
        "triage", "triage-agent", "ticket-triage", "triage",
        agents.triage_messages(ticket, customer), json_mode=True, max_tokens=120))
    thread, kb_text = agents.responder_context(ticket, kb, cfg)

    attempt, feedback = 0, None
    while True:
        draft = t.call(
            "responder", "responder-agent", "draft-response", f"draft-response-{attempt + 1}",
            agents.draft_messages(ticket, customer, triage, thread, kb_text, feedback),
            retry=attempt, max_tokens=400)
        review = agents.parse_json(t.call(
            "qa", "qa-reviewer-agent", "quality-review", f"quality-review-{attempt + 1}",
            agents.review_messages(ticket, draft), retry=attempt, json_mode=True, max_tokens=150))
        score, feedback = _score(review), review.get("feedback", "")
        # ponytail: the SDK reads usage_metadata when a call starts, so this score can't be attached
        # to the responder's record afterwards; it only lives in these results.
        if score >= cfg["qa_threshold"] or attempt >= cfg["qa_max_retries"]:
            break
        attempt += 1

    return {"customer": customer["name"], "ticket": ticket["id"], "calls": t.calls,
            "tokens_in": t.tokens_in, "tokens_out": t.tokens_out, "retries": attempt,
            "cost": t.cost, "score": score, "passed": score >= cfg["qa_threshold"]}
