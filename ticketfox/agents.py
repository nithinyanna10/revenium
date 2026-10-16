"""Prompts and context selection for the three agents. No API calls here."""
import json
import math
import re
from collections import Counter

CATEGORIES = "shipping, billing, returns, account, appointments, product, other"


def thread_text(messages):
    return "\n".join(f"{'Customer' if m['from'] == 'customer' else 'Support'}: {m['text']}" for m in messages)


def _words(text):
    # crude stem (billed/billing -> bill) so inflected forms match
    return [re.sub(r"(ing|ed|es|s)$", "", w) for w in re.findall(r"[a-z]{4,}", text.lower())]


def top_kb(kb, query, n):
    """Simple keyword match: query words weighted by rarity across articles (IDF), no vector DB."""
    q = set(_words(query))
    counts = {name: Counter(_words(text)) for name, text in kb}
    idf = {w: math.log(len(kb) / sum(w in c for c in counts.values())) for w in q if any(w in c for c in counts.values())}

    def score(article):
        c = counts[article[0]]
        return sum(idf[w] * min(c[w], 3) for w in idf)

    return sorted(kb, key=score, reverse=True)[:n]


def responder_context(ticket, kb, cfg):
    """Apply the mode's context rules: how much thread, how much knowledge base."""
    msgs = ticket["messages"]
    n = cfg["responder_thread_messages"]
    if n != "all":
        msgs = msgs[-n:]
    k = cfg["responder_kb_articles"]
    articles = kb if k == "all" else top_kb(kb, ticket["subject"] + " " + thread_text(msgs), k)
    return thread_text(msgs), "\n\n".join(text for _, text in articles)


def parse_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return {}


def triage_messages(ticket, customer):
    return [
        {"role": "system", "content": (
            f"You are triage-agent on the support desk of {customer['name']}. Classify the ticket. "
            f'Reply with JSON only: {{"category": one of [{CATEGORIES}], '
            '"urgency": "low"|"medium"|"high", "summary": "max 20 words"}')},
        {"role": "user", "content": f"Subject: {ticket['subject']}\n\n{thread_text(ticket['messages'])}"},
    ]


def draft_messages(ticket, customer, triage, thread, kb_text, feedback=None):
    system = (
        f"You are responder-agent, drafting a reply on behalf of the {customer['name']} support team. "
        "Use only the knowledge base below; never invent policies, dates or amounts. "
        "Answer every open question, be warm and concise, and give concrete next steps.\n\n"
        f"KNOWLEDGE BASE\n{kb_text}"
    )
    user = (f"Subject: {ticket['subject']}\nTriage: {json.dumps(triage)}\n\nTHREAD\n{thread}\n\n"
            "Write the next reply to the customer.")
    if feedback:
        user += f"\n\nA reviewer rejected your previous draft. Fix this: {feedback}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def review_messages(ticket, draft):
    return [
        {"role": "system", "content": (
            "You are qa-reviewer-agent. Score a drafted support reply from 0.0 to 1.0 and be strict. "
            "Deduct for: any open customer question left unanswered; ignoring or repeating things "
            "already tried earlier in the thread; no concrete next step or owner; invented policy, "
            "dates or amounts; wrong tone; padding. 0.9 or higher means ready to send with no edits. "
            'Reply with JSON only: {"score": number, "feedback": "max 40 words on the biggest fix"}')},
        {"role": "user", "content": (
            f"Subject: {ticket['subject']}\n\nTHREAD\n{thread_text(ticket['messages'])}\n\nDRAFT REPLY\n{draft}")},
    ]
