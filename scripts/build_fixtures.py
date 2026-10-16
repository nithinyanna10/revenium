"""Render fixtures/src/*.yaml into fixtures/tickets.json: email headers, signatures, nested
quoted replies (as mail clients produce), forwarded emails and pasted logs.
Run: uv run python scripts/build_fixtures.py"""
import datetime as dt
import json
import statistics
from pathlib import Path

import tiktoken
import yaml

SRC = Path(__file__).resolve().parent.parent / "fixtures"
sigs = yaml.safe_load((SRC / "src" / "signatures.yaml").read_text())
tickets = yaml.safe_load((SRC / "src" / "tickets.yaml").read_text())
EMAIL = {"acme": "acmelogistics", "bluepeak": "bluepeakhealth", "cobalt": "cobaltretail"}
enc = tiktoken.get_encoding("o200k_base")


def sender(ticket, m):
    if m["from"] == "support":
        return sigs["support"][ticket["customer"]]["from"], sigs["support"][ticket["customer"]]["sig"]
    u = sigs["users"][ticket["user"]]
    return f"{u['name']} <{u['name'].lower().replace(' ', '.')}@example.com>", u["sig"]


def content(ticket, m):
    """The email as typed: text, pasted log, forwarded email, then signature."""
    parts = [m["text"]]
    if m.get("log"):
        parts.append(m["log"].rstrip())
    if m.get("forward"):
        parts.append("---------- Forwarded message ---------\n" + m["forward"].rstrip())
    return "\n\n".join(parts)


GAP_MIN = {"Acme Logistics": 10, "Bluepeak Health": 45, "Cobalt Retail": 600}  # default minutes between emails


def stamp(ticket, i):
    """Email i's send time: the ticket's hand-set start + i gaps, so headers agree with dates in the text."""
    gap = ticket.get("gap_min", GAP_MIN[ticket["customer"]])
    return dt.datetime.strptime(ticket["start"], "%Y-%m-%d %H:%M") + dt.timedelta(minutes=gap * i)


def body(ticket, i, depth):
    """Content + signature + (if depth left) the previous email quoted with '> ' (itself nested)."""
    m = ticket["messages"][i]
    who, sig = sender(ticket, m)
    out = content(ticket, m) + ("\n\n" + sig.rstrip() if sig.strip() else "")
    if depth > 0 and i > 0:
        prev = ticket["messages"][i - 1]
        pwho, _ = sender(ticket, prev)
        when = stamp(ticket, i - 1).strftime("%a, %d %b %Y at %H:%M")
        quoted = body(ticket, i - 1, depth - 1)
        out += f"\n\nOn {when}, {pwho} wrote:\n" + "\n".join("> " + ln for ln in quoted.splitlines())
    return out


def render(ticket):
    depth = sigs["styles"][ticket["customer"]]["quote_depth"]
    msgs = []
    for i, m in enumerate(ticket["messages"]):
        who, _ = sender(ticket, m)
        head = (f"From: {who}\nDate: {stamp(ticket, i).strftime('%a, %d %b %Y %H:%M')}\n"
                f"Subject: {'Re: ' if i else ''}{ticket['subject']}")
        msgs.append({"from": m["from"], "text": head + "\n\n" + body(ticket, i, depth)})
    last = ticket["messages"][-1]
    assert last["from"] == "customer", ticket["id"]
    return {"id": ticket["id"], "customer": ticket["customer"], "user": ticket["user"],
            "subject": ticket["subject"], "messages": msgs,
            "latest": content(ticket, last)}  # newest customer message only: no quotes, no signature


out = [render(t) for t in tickets]
(SRC / "tickets.json").write_text(json.dumps(out, indent=1) + "\n")

for name in sigs["styles"]:
    toks = [len(enc.encode("\n\n=====\n\n".join(m["text"] for m in t["messages"]))) for t in out if t["customer"] == name]
    print(f"{name:<16} {len(toks)} tickets, thread tokens min {min(toks)} avg {statistics.mean(toks):.0f} max {max(toks)}")
