"""uv run python -m ticketfox.run --mode baseline --customers all --tickets 3"""
import argparse
import datetime as dt
import json
import os
import time
import uuid

from dotenv import load_dotenv


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"


def alert_watcher():
    """Mirror the three Cobalt guardrails on estimated cost; print the first UTC time each would trip.
    Seeds with today's (UTC) Cobalt calls from results/ so the daily total and the previous window include the prelude.
    The 15m windows are evaluated both rolling and clock-aligned, since Revenium doesn't document which it uses."""
    from ticketfox import config
    today = dt.datetime.now(dt.timezone.utc).date()
    ev = []  # (epoch, agent, cost)
    for f in (config.ROOT / "results").glob("*.jsonl"):
        for ln in f.read_text().splitlines():
            r = json.loads(ln)
            if r["kind"] != "call" or r["customer"] != "Cobalt Retail":
                continue
            t = dt.datetime.fromisoformat(r["ts"])
            if t.date() == today:
                ev.append((t.timestamp(), r["agent"], r["est_cost"]))
    path = config.ROOT / "config" / "alerts.yaml"  # written by scripts/setup_alerts.py --retake
    thr = {"daily_budget_usd": 1.00, "responder_spike_usd": 0.50, "jump_enabled": True}
    if path.exists():
        thr.update(config.yaml.safe_load(path.read_text()))
    fired = set()

    def win(lo, hi, agent=None):
        return sum(c for t, a, c in ev if lo < t <= hi and (agent is None or a == agent))

    def on_call(cost, agent):
        now = time.time()
        ev.append((now, agent, cost))
        b = int(now // 900) * 900
        checks = {
            f"daily budget > ${thr['daily_budget_usd']:.2f}": (win(0, now), thr["daily_budget_usd"], True),
            f"responder-agent rolling 15m > ${thr['responder_spike_usd']:.2f}": (win(now - 900, now, "responder-agent"), thr["responder_spike_usd"], True),
            "jump vs previous 15m, rolling (> +100%)": (win(now - 900, now), 2 * win(now - 1800, now - 900), thr["jump_enabled"] and win(now - 1800, now - 900) > 0),
            "jump vs previous 15m, clock-aligned (> +100%)": (win(b - 1, now), 2 * win(b - 901, b - 1), thr["jump_enabled"] and win(b - 901, b - 1) > 0),
        }
        for name, (val, limit, ok) in checks.items():
            if ok and val > limit and name not in fired:
                fired.add(name)
                print(f"{utc_now()}  *** THRESHOLD CROSSED: {name}  (est ${val:.4f} > ${limit:.4f}) ***", flush=True)
    return on_call


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, help="a key under modes: in config/modes.yaml (baseline, optimized, optimized_v3, incident)")
    ap.add_argument("--customers", default="all", help="'all' or comma-separated names, e.g. acme,cobalt")
    ap.add_argument("--tickets", type=int, default=1, help="tickets per customer (fixture order)")
    ap.add_argument("--run-tag", help="sent as trace_name so one run can be filtered in Revenium Data > Logs")
    ap.add_argument("--watch-alerts", action="store_true", help="log the UTC time each Phase 4 alert threshold is crossed (est. cost)")
    ap.add_argument("--debug", action="store_true", help="REVENIUM_LOG_LEVEL=DEBUG")
    args = ap.parse_args()

    load_dotenv(override=True)  # .env wins over stale shell vars
    if args.debug:
        os.environ["REVENIUM_LOG_LEVEL"] = "DEBUG"
    if not os.getenv("REVENIUM_METERING_API_KEY") or not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Set REVENIUM_METERING_API_KEY and OPENAI_API_KEY in .env")

    # Imported after env is set: the middleware reads its config on import.
    import revenium_middleware
    from ticketfox import config
    from ticketfox.metering import Spend, SpendLimitExceeded
    from ticketfox.pipeline import run_ticket

    modes = config.load_modes()
    if args.mode not in modes["modes"]:
        raise SystemExit(f"Unknown mode {args.mode!r}; have {list(modes['modes'])}")
    cfg = modes["modes"][args.mode]
    customers, tickets, kb = config.load_customers(), config.load_tickets(), config.load_kb()
    wanted = [s.strip().lower() for s in args.customers.split(",")]
    names = [n for n in customers if args.customers == "all" or any(w in n.lower() for w in wanted)]
    if not names:
        raise SystemExit(f"No customer matches {args.customers!r}; have {list(customers)}")

    spend = Spend(modes["spend_limit_usd"], modes["pricing_usd_per_1m_tokens"], config.spent_so_far(args.run_tag))
    if args.watch_alerts:
        spend.on_call = alert_watcher()
    run_id = uuid.uuid4().hex[:6]
    out = config.ROOT / "results" / f"{args.mode}-{dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    out.parent.mkdir(exist_ok=True)
    results = []
    try:
        with out.open("a") as f:
            for name in names:
                for ticket in [t for t in tickets if t["customer"] == name][: args.tickets]:
                    rows = []
                    try:
                        r = run_ticket(ticket, customers[name], args.mode, cfg, kb, spend, run_id, rows, args.run_tag)
                        results.append(r)
                        rows.append(r)
                    finally:  # a spend-guard abort mid-ticket still keeps that ticket's call rows
                        f.write("".join(json.dumps(x) + "\n" for x in rows))
                        f.flush()
                    print(f"{utc_now()}  {r['ticket']}: {r['calls']} calls, {r['retries']} retries, QA {r['qa_score']:.2f}, "
                          f"${r['est_cost']:.4f} | running spend ${spend.total:.4f} / ${spend.limit:.2f}", flush=True)
    except SpendLimitExceeded as e:
        print(f"ABORTED by spend guard: {e}")

    print(f"\nrun {run_id}, mode={args.mode}, results -> {out.relative_to(config.ROOT)}")
    print(f"{'customer':<18}{'mode':<11}{'tix':>4}{'calls':>6}{'in_tok':>9}{'out_tok':>9}{'retries':>8}{'$/ticket':>10}{'avg QA':>8}")
    for name in names:
        rs = [r for r in results if r["customer"] == name]
        if rs:
            print(f"{name:<18}{args.mode:<11}{len(rs):>4}{sum(r['calls'] for r in rs):>6}"
                  f"{sum(r['tokens_in'] for r in rs):>9}{sum(r['tokens_out'] for r in rs):>9}"
                  f"{sum(r['retries'] for r in rs):>8}{sum(r['est_cost'] for r in rs) / len(rs):>10.4f}"
                  f"{sum(r['qa_score'] for r in rs) / len(rs):>8.3f}")
    print(f"estimated total spend: ${spend.total:.4f}")

    # Metering runs on a background thread; wait for every record to be reported.
    expected = sum(r["calls"] for r in results)
    for _ in range(60):
        s = revenium_middleware.get_metering_status()
        if s.success_count + s.error_count >= expected:
            break
        time.sleep(0.5)
    print(f"revenium metering: {s.success_count} success, {s.error_count} error "
          f"(of {expected} calls), last_error={s.last_error}")


if __name__ == "__main__":
    main()
