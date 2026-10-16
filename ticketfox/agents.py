"""Prompts and context selection for the four agents. No API calls here."""
import json
import math
import re
from collections import Counter

CATEGORIES = "shipping, billing, returns, account, appointments, product, other"
CRITERIA = ("accuracy", "completeness", "tone", "actionability")


def thread_text(messages):
    """The raw email thread exactly as a helpdesk stores it: every email with its quotes/signatures."""
    return "\n\n=====\n\n".join(m["text"] for m in messages)


def _words(text):
    # crude stem (billed/billing -> bill, reschedule/rescheduling -> reschedul) so inflections match
    return [re.sub(r"(ing|ed|es|s|e)$", "", w) for w in re.findall(r"[a-z]{4,}", text.lower())]


def top_kb(kb, query, n):
    """Simple keyword match: query words weighted by rarity across articles (IDF), no vector DB.
    Words in an article's title count triple, since titles name the topic."""
    q = set(_words(query))
    counts = {}
    for name, text in kb:
        c = Counter(_words(text))
        for w in _words(text.splitlines()[0]):
            c[w] += 3
        counts[name] = c
    idf = {w: math.log(len(kb) / sum(w in c for c in counts.values())) for w in q if any(w in c for c in counts.values())}

    def score(article):
        c = counts[article[0]]
        return sum(idf[w] * min(c[w], 3) for w in idf)

    return sorted(kb, key=score, reverse=True)[:n]


def kb_query(ticket, summary=None):
    return " ".join([ticket["subject"], ticket["subject"], ticket["latest"], summary or ""])


def responder_context(ticket, kb, cfg, summary=None):
    """Apply the mode's context rules: how much thread, how much knowledge base."""
    if cfg["responder_context"] == "full_thread":
        thread = thread_text(ticket["messages"])
    else:
        thread = f"SUMMARY OF THE THREAD SO FAR\n{summary}\n\nLATEST CUSTOMER MESSAGE\n{ticket['latest']}"
    k = cfg["responder_kb_articles"]
    articles = kb if k == "all" else top_kb(kb, kb_query(ticket, summary), k)
    return thread, "\n\n".join(text for _, text in articles)


def parse_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return {}


def summary_messages(ticket, customer):
    return [
        {"role": "system", "content": (
            f"You are summarizer-agent for the support desk of {customer['name']}. Condense the email thread "
            "into a factual brief of at most 150 words. Ignore quoted repeats, signatures and disclaimers. "
            "Include: who the customer is, order/shipment/invoice/account identifiers, amounts and dates, "
            "what was already tried or promised, what is still unresolved, and the customer's mood.")},
        {"role": "user", "content": f"Subject: {ticket['subject']}\n\nTHREAD\n{thread_text(ticket['messages'])}"},
    ]


def triage_input(ticket, cfg):
    msgs = ticket["messages"]
    if cfg["triage_context"] == "first_plus_latest" and len(msgs) > 1:
        return f"FIRST MESSAGE\n{msgs[0]['text']}\n\nLATEST CUSTOMER MESSAGE\n{ticket['latest']}"
    return thread_text(msgs)


def triage_messages(ticket, customer, cfg):
    return [
        {"role": "system", "content": (
            f"You are triage-agent on the support desk of {customer['name']}. Classify the ticket. "
            f'Reply with JSON only: {{"category": one of [{CATEGORIES}], '
            '"urgency": "low"|"medium"|"high", "summary": "max 20 words"}')},
        {"role": "user", "content": f"Subject: {ticket['subject']}\n\n{triage_input(ticket, cfg)}"},
    ]


def draft_messages(ticket, customer, triage, thread, kb_text, feedback=None):
    system = (
        f"You are responder-agent, drafting a reply on behalf of the {customer['name']} support team. "
        "Use only the knowledge base below and the facts in the ticket; never invent policies, dates or amounts. "
        "Answer every open question, be warm and concise, and give concrete next steps.\n\n"
        f"KNOWLEDGE BASE\n{kb_text}"
    )
    user = (f"Subject: {ticket['subject']}\nTriage: {json.dumps(triage)}\n\nTHREAD\n{thread}\n\n"
            "Write the next reply to the customer.")
    if feedback:
        user += f"\n\nA reviewer rejected your previous draft. Fix this: {feedback}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def review_messages(ticket, draft, kb_text):
    """Always judges against the FULL original thread, in every mode, plus the KB the responder saw."""
    return [
        {"role": "system", "content": (
            "You are qa-reviewer-agent. Judge a drafted support reply against the full email thread. "
            "Score each criterion from 0.0 to 1.0 and be strict:\n"
            "- accuracy: every fact, identifier, date and amount matches the thread; policy statements match the "
            "knowledge base (a correct policy answer is accurate even if the customer assumed otherwise); "
            "no invented policy\n"
            "- completeness: every open customer question is answered; earlier contacts are acknowledged "
            "and nothing already tried is repeated\n"
            "- tone: warm, calm, professional, owns the problem, no blame, no padding\n"
            "- actionability: concrete next steps, owners and timeframes the customer can rely on\n"
            'Reply with JSON only: {"accuracy": n, "completeness": n, "tone": n, "actionability": n, '
            '"feedback": "max 40 words on the biggest fix"}')},
        {"role": "user", "content": (
            f"KNOWLEDGE BASE (the policies the responder was allowed to use)\n{kb_text}\n\n"
            f"Subject: {ticket['subject']}\n\nTHREAD\n{thread_text(ticket['messages'])}\n\nDRAFT REPLY\n{draft}")},
    ]
