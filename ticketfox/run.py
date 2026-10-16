"""uv run python -m ticketfox.run --mode baseline --customers all --tickets 1"""
import argparse
import os
import time
import uuid

from dotenv import load_dotenv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["baseline", "optimized"], required=True)
    ap.add_argument("--customers", default="all", help="'all' or comma-separated names, e.g. acme,cobalt")
    ap.add_argument("--tickets", type=int, default=1, help="tickets per customer (fixture order)")
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
    cfg = modes["modes"][args.mode]
    customers, tickets, kb = config.load_customers(), config.load_tickets(), config.load_kb()
    wanted = [s.strip().lower() for s in args.customers.split(",")]
    names = [n for n in customers if args.customers == "all" or any(w in n.lower() for w in wanted)]
    if not names:
        raise SystemExit(f"No customer matches {args.customers!r}; have {list(customers)}")

    spend = Spend(modes["spend_limit_usd"], modes["pricing_usd_per_1m_tokens"])
    run_id = uuid.uuid4().hex[:6]
    results = []
    try:
        for name in names:
            for ticket in [t for t in tickets if t["customer"] == name][: args.tickets]:
                r = run_ticket(ticket, customers[name], cfg, kb, spend, run_id)
                results.append(r)
                print(f"{r['ticket']}: {r['calls']} calls, {r['retries']} retries, score {r['score']:.2f}, "
                      f"${r['cost']:.4f} | running spend ${spend.total:.4f} / ${spend.limit:.2f}", flush=True)
    except SpendLimitExceeded as e:
        print(f"ABORTED by spend guard: {e}")

    print(f"\nrun {run_id}, mode={args.mode}")
    print(f"{'customer':<18}{'mode':<11}{'tix':>4}{'calls':>6}{'in_tok':>9}{'out_tok':>9}{'retries':>8}{'$/ticket':>10}")
    for name in names:
        rs = [r for r in results if r["customer"] == name]
        if rs:
            print(f"{name:<18}{args.mode:<11}{len(rs):>4}{sum(r['calls'] for r in rs):>6}"
                  f"{sum(r['tokens_in'] for r in rs):>9}{sum(r['tokens_out'] for r in rs):>9}"
                  f"{sum(r['retries'] for r in rs):>8}{sum(r['cost'] for r in rs) / len(rs):>10.4f}")
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
