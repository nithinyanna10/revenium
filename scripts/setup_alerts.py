"""Create (or list / delete) the three Phase 4 guardrails via Revenium's alert-rule ("AI anomaly") API.
POST/GET/DELETE https://api.revenium.ai/profitstream/v2/api/sources/ai/anomaly  (header x-api-key)

Needs a FULL-ACCESS key (prefix rev_sk_) in .env as REVENIUM_ADMIN_API_KEY. The metering key in
REVENIUM_METERING_API_KEY gets 403 on this API. Also needs ALERT_EMAIL in .env (who gets the emails).
Key values are never printed. Default is a dry run that prints the payloads.

  uv run python scripts/setup_alerts.py                # dry run
  uv run python scripts/setup_alerts.py --apply        # create the rules that don't exist yet
  uv run python scripts/setup_alerts.py --list
  uv run python scripts/setup_alerts.py --delete       # remove our three rules by name
  uv run python scripts/setup_alerts.py --retake       # retune the rules for the retake (dry run unless --apply too)

Retake: daily budget = today's (UTC) estimated Cobalt spend + $0.40 (estimates matched Revenium's real cost to the cent),
responder-agent spike = $0.20 (crossed ~55 s into the first incident run), jump rule disabled. Writes config/alerts.yaml,
which `ticketfox.run --watch-alerts` reads so its crossing log mirrors these thresholds.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import datetime as dt
from pathlib import Path

from dotenv import load_dotenv

API = "https://api.revenium.ai/profitstream/v2/api/sources/ai/anomaly"
COBALT = {"dimension": "ORGANIZATION", "operator": "IS", "value": "Cobalt Retail"}

# Thresholds are scaled from real bulk-v1 Cobalt costs (baseline: ~$0.093/ticket, $0.93 per 10 tickets, of which
# responder-agent ~$0.41); the incident is projected at ~$1.4 total, ~$0.66 responder-agent.
RULES = [
    {   # a. budget: cumulative Cobalt spend per UTC day
        "name": "Ticketfox: Cobalt Retail daily AI budget",
        "description": "Cumulative metered spend for organization Cobalt Retail in one day.",
        "alertType": "CUMULATIVE_USAGE", "dataSource": "SDK", "metricType": "TOTAL_COST",
        "operatorType": "GREATER_THAN", "threshold": 1.00, "periodDuration": "DAILY",
        "filters": [COBALT],
    },
    {   # b. spend spike on one agent (agent_version is not a guardrail filter dimension)
        "name": "Ticketfox: Cobalt responder-agent spend spike (15m)",
        "description": "responder-agent spend for Cobalt Retail over the last 15 minutes. Baseline 10-ticket batch ~ $0.41.",
        "alertType": "THRESHOLD", "dataSource": "SDK", "metricType": "TOTAL_COST",
        "operatorType": "GREATER_THAN", "threshold": 0.50, "periodDuration": "FIFTEEN_MINUTES",
        "filters": [COBALT, {"dimension": "AGENT", "operator": "IS", "value": "responder-agent"}],
    },
    {   # c. anomaly-style: spend vs the previous window (needs traffic in the previous window to compare against)
        "name": "Ticketfox: Cobalt spend jump vs previous 15m",
        "description": "Cobalt Retail total cost up more than 100% against the previous 15-minute window.",
        "alertType": "RELATIVE_CHANGE", "dataSource": "SDK", "metricType": "TOTAL_COST",
        "operatorType": "PERCENT_INCREASE", "threshold": 100, "isPercentage": True, "periodDuration": "FIFTEEN_MINUTES",
        "filters": [COBALT],
    },
]


def call(method, url, key, body=None):
    req = urllib.request.Request(url, method=method, headers={"x-api-key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]


def find_rules(node, out):
    """List responses are paged/HAL-wrapped; collect every dict that looks like a rule."""
    if isinstance(node, dict):
        if "id" in node and "name" in node and "alertType" in node:
            out.append(node)
        for v in node.values():
            find_rules(v, out)
    elif isinstance(node, list):
        for v in node:
            find_rules(v, out)
    return out


def cobalt_spend_today():
    """Estimated Cobalt cost metered so far today (UTC), from results/*.jsonl."""
    today, total = dt.datetime.now(dt.timezone.utc).date(), 0.0
    for f in (Path(__file__).resolve().parent.parent / "results").glob("*.jsonl"):
        for ln in f.read_text().splitlines():
            r = json.loads(ln)
            if r["kind"] == "call" and r["customer"] == "Cobalt Retail" and dt.datetime.fromisoformat(r["ts"]).date() == today:
                total += r["est_cost"]
    return total


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--delete", action="store_true")
    g.add_argument("--retake", action="store_true")
    ap.add_argument("--apply-retake", action="store_true", help="with --retake: actually PUT the changes")
    args = ap.parse_args()

    load_dotenv(override=True)
    team = os.getenv("REVENIUM_TEAM_ID", "l3r6Qv")  # the teamId in app.revenium.ai URLs
    email = os.getenv("ALERT_EMAIL", "")
    payloads = [{**r, "teamId": team, "enabled": True, "notificationAddresses": [email] if email else [],
                 "slackConfigurations": [], "webhookConfigurations": []} for r in RULES]
    if not (args.apply or args.list or args.delete or args.retake):
        print(json.dumps(payloads, indent=2))
        print("\n(dry run; add --apply to create)")
        return

    key = os.getenv("REVENIUM_ADMIN_API_KEY", "")
    if not key.startswith("rev_sk_"):
        sys.exit("Set REVENIUM_ADMIN_API_KEY in .env to a full-access key (rev_sk_..., Integrations > SDK Setup > Create Key). "
                 "The metering key (rev_mk_) cannot manage alerts.")
    if args.apply and not email:
        sys.exit("Set ALERT_EMAIL in .env (the address that should receive the alert emails).")

    status, resp = call("GET", f"{API}?{urllib.parse.urlencode({'teamId': team, 'size': 200})}", key)
    if status != 200:
        sys.exit(f"list failed: HTTP {status} {resp}")
    existing = {r["name"]: r for r in find_rules(resp, [])}
    if args.list:
        for r in existing.values():
            print(f"{r['id']}  {r['name']}  enabled={r.get('enabled')}  firingState={r.get('firingState')}")
        return
    if args.retake:
        spent = cobalt_spend_today()
        change = {payloads[0]["name"]: {"threshold": round(spent + 0.40, 2)},
                  payloads[1]["name"]: {"threshold": 0.20},
                  payloads[2]["name"]: {"enabled": False}}
        print(f"today's Cobalt spend so far (est.): ${spent:.4f}")
        for p in payloads:
            have = existing.get(p["name"])
            if not have:
                sys.exit(f"missing rule {p['name']}; run --apply first")
            new = {**p, **change[p["name"]]}
            print(f"{p['name']}: threshold {have.get('threshold')} -> {new['threshold']}, enabled {have.get('enabled')} -> {new['enabled']}")
            if args.apply_retake:
                s_, r_ = call("PUT", f"{API}/{have['id']}", key, new)
                print(f"  PUT HTTP {s_}" + ("" if s_ in (200, 201) else f" {r_}"))
        if args.apply_retake:
            (Path(__file__).resolve().parent.parent / "config" / "alerts.yaml").write_text(
                f"# written by scripts/setup_alerts.py --retake; read by ticketfox.run --watch-alerts\n"
                f"daily_budget_usd: {change[payloads[0]['name']]['threshold']}\nresponder_spike_usd: 0.20\njump_enabled: false\n")
        else:
            print("(dry run; add --apply-retake)")
        return
    for p in payloads:
        have = existing.get(p["name"])
        if args.delete:
            if have:
                s, r = call("DELETE", f"{API}/{have['id']}", key)
                print(f"delete {p['name']}: HTTP {s}")
            continue
        if have:
            print(f"skip (exists, id {have['id']}): {p['name']}")
            continue
        s, r = call("POST", API, key, p)
        print(f"create {p['name']}: HTTP {s}" + ("" if s in (200, 201) else f" {r}"))


if __name__ == "__main__":
    main()
