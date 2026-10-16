"""Offline eval: re-judge each ticket's FINAL response with gpt-4o (temp 0, two passes) against the full raw
thread + full KB. Source of truth for quality comparisons; the in-pipeline reviewer is only a retry gate.
Metered as product "Ticketfox Internal Eval", environment "eval" so it never mixes with customer cost.
Usage: uv run python scripts/evaluate.py results/baseline-*.jsonl results/optimized-*.jsonl -> results/eval-<ts>.jsonl"""
import argparse
import datetime as dt
import json
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(override=True)  # before ticketfox imports: the middleware reads its config on import

import revenium_middleware  # noqa: E402

from ticketfox import agents, config  # noqa: E402
from ticketfox.metering import Spend, SpendLimitExceeded, chat  # noqa: E402

MODEL, PASSES = "gpt-4o", 2


def judge_messages(ticket, kb_text, response):
    return [
        {"role": "system", "content": (
            "You are an independent evaluator of support replies. Judge the FINAL REPLY against the full email "
            "thread and the knowledge base. Score each criterion from 0.0 to 1.0 and be strict:\n"
            "- accuracy: every fact, identifier, date and amount matches the thread; policy statements match the "
            "knowledge base (a correct policy answer is accurate even if the customer assumed otherwise); no invented policy\n"
            "- completeness: every open customer question is answered; earlier contacts are acknowledged "
            "and nothing already tried is repeated\n"
            "- tone: warm, calm, professional, owns the problem, no blame, no padding\n"
            "- actionability: concrete next steps, owners and timeframes the customer can rely on\n"
            'Reply with JSON only: {"accuracy": n, "completeness": n, "tone": n, "actionability": n, '
            '"feedback": "max 40 words on the biggest weakness"}')},
        {"role": "user", "content": (
            f"KNOWLEDGE BASE\n{kb_text}\n\nSubject: {ticket['subject']}\n\n"
            f"THREAD\n{agents.thread_text(ticket['messages'])}\n\nFINAL REPLY\n{response}")},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args()

    modes = config.load_modes()
    tickets = {t["id"]: t for t in config.load_tickets()}
    kb_text = "\n\n".join(text for _, text in config.load_kb())
    run_id = uuid.uuid4().hex[:6]
    out = ROOT / "results" / f"eval-{dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    src = [r for f in args.files for r in map(json.loads, f.read_text().splitlines())
           if r["kind"] == "ticket" and r.get("final_response")]
    spend = Spend(modes["spend_limit_usd"], modes["pricing_usd_per_1m_tokens"],
                  config.spent_so_far(src[0].get("run_tag")) if src else 0.0)  # one tag per eval invocation
    n_calls = 0
    try:
        with out.open("a") as f:
            for r in src:
                t = tickets[r["ticket"]]
                tag = r.get("run_tag") or "untagged"
                passes, cost = [], 0.0
                for p in range(PASSES):
                    md = {"organizationName": "Ticketfox Internal", "productName": "Ticketfox Internal Eval",
                          "environment": "eval", "agent": "eval-judge-agent", "task_type": "offline-eval",
                          "trace_id": f"eval-{r['ticket']}-{r['mode']}-{run_id}", "ticket_id": r["ticket"],
                          "transaction_name": f"eval-pass-{p + 1}", "trace_name": f"{tag}-eval",
                          "agent_version": "eval-1"}
                    c = chat(spend, MODEL, judge_messages(t, kb_text, r["final_response"]), md,
                             max_tokens=200, json_mode=True, temperature=0)
                    n_calls += 1
                    j = agents.parse_json(c.text)
                    subs = {k: max(0.0, min(1.0, float(j.get(k, 0.0)))) for k in agents.CRITERIA}
                    passes.append({"score": sum(subs.values()) / len(subs), "criteria": subs, "feedback": j.get("feedback", "")})
                    cost += c.cost
                s = [p["score"] for p in passes]
                row = {"kind": "eval", "run_tag": r.get("run_tag"), "run_id": r["run_id"], "mode": r["mode"],
                       "customer": r["customer"], "ticket": r["ticket"], "pipeline_qa": r["qa_score"],
                       "eval_score": round(sum(s) / len(s), 4), "disagreement": round(abs(s[0] - s[1]), 4),
                       "passes": passes, "est_cost": round(cost, 6)}
                f.write(json.dumps(row) + "\n")
                f.flush()
                print(f"{r['mode']:<9}{r['ticket']}: pipeline {r['qa_score']:.2f} eval {row['eval_score']:.2f} "
                      f"(passes {s[0]:.2f}/{s[1]:.2f}) | running spend ${spend.total:.4f} / ${spend.limit:.2f}", flush=True)
    except SpendLimitExceeded as e:
        print(f"ABORTED by spend guard: {e}")
    print(f"eval -> {out.relative_to(ROOT)}")
    for _ in range(60):
        st = revenium_middleware.get_metering_status()
        if st.success_count + st.error_count >= n_calls:
            break
        time.sleep(0.5)
    print(f"revenium metering: {st.success_count} success, {st.error_count} error (of {n_calls} calls), last_error={st.last_error}")


if __name__ == "__main__":
    main()
