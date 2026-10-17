# Phase 4: guardrails and incident run (test run, 2026-10-17)

Cobalt Retail, agent_version 1.1.0 ("bad config deploy": QA threshold 0.95, up to 6 retries). All times UTC (EDT = UTC-4).
Cost numbers are estimates from token counts x list prices; they matched Revenium's own figure to the cent
(Guardrails page: $1.85 for the day vs 0.3911 + 1.4602 = $1.8513 here).

## Rules (created with `scripts/setup_alerts.py --apply`, all email to ALERT_EMAIL)

| Rule | ID | Type | Threshold | Window | Scope |
|---|---|---|---|---|---|
| Cobalt Retail daily AI budget | 5jLRjv | CUMULATIVE_USAGE | total cost > $1.00 | DAILY | org = Cobalt Retail |
| Cobalt responder-agent spend spike (15m) | vKVp0D | THRESHOLD | total cost > $0.50 | FIFTEEN_MINUTES | org = Cobalt Retail, agent = responder-agent |
| Cobalt spend jump vs previous 15m | l3dy25 | RELATIVE_CHANGE | +100% | FIFTEEN_MINUTES | org = Cobalt Retail |

UI: Spend Management > Guardrails (`/spend`); fired alerts under the History tab.

## Timeline

| Time | Event |
|---|---|
| 17:23:19 | prelude start (baseline, 3 Cobalt tickets, tag `incident-v1-prelude`) |
| 17:24:04 | prelude end: 27 calls, est $0.3911 (responder-agent $0.1799) |
| 17:30:19 | incident start (tag `incident-v1`, 10 tickets, spend guard $4) |
| 17:31:43.7 | est. daily budget crossed $1.00 (est $1.0003, 84 s into the incident) |
| 17:31:48.6 | est. responder-agent 15m total crossed $0.50 (est $0.5147; window includes the prelude) |
| 17:32:01.7 | est. jump crossed under clock-aligned windows ($0.7837 vs 2 x $0.3911) |
| 17:33:00-17:33:36 | **daily budget fired** (History: detected 1:33 PM EDT, data through 1:32 PM, trigger value 1.684675; first seen FIRING by API at 17:33:36) |
| 17:33:25 | incident end: 118 calls, all metered, est $1.4602 (responder-agent $0.7112, qa-reviewer $0.6626, triage $0.0865) |
| 17:45 | **responder spike fired** (History: detected 1:45 PM EDT, data through 1:37 PM, trigger value 0.8910875; API polls: NOT_FIRING at 17:43:56, FIRING at 17:45:56) |
| n/a | **jump rule never fired** (still NOT_FIRING at 17:47) |

Incident tickets: CRT-001 0 retries (QA 1.00, $0.043); CRT-002..004 6 retries (QA 0.78/0.82/0.70); CRT-005 4 (1.00); CRT-006 1 (0.97);
CRT-007..009 6 (0.65/0.90/0.53); CRT-010 3 (0.95). Six of ten hit the retry cap. Incident cost $1.46 vs ~$0.93 for the 10-ticket baseline.

## Alert latency (threshold crossing to firing)

| Rule | Crossing (est.) | Fired | Latency |
|---|---|---|---|
| Daily budget | 17:31:43.7 | 17:33:00-17:33:36 | 1.3-1.9 min |
| Responder spike (15m) | 17:31:48.6 | 17:45 (poll bracket 17:43:56-17:45:56) | about 13 min |
| Jump vs previous 15m | 17:32:01.7 (aligned only) | never | n/a |

Email delivery time: to be added once reported.

## What the evidence says about evaluation

- The API has no history endpoint (every path tried 404s); `firingState` only brackets the fire time to the 2-minute poll. Exact detection minutes come from the History tab, which shows minutes only.
- The daily rule is evaluated about every minute with about 1 minute of data lag (data through 1:32 PM, detected 1:33 PM).
- The 15m rules are evaluated on a slower cadence. The spike detection landed on the 17:45 boundary and used data only through 17:37. The API refuses windows shorter than 15 minutes ("ONE_MINUTE/FIVE_MINUTES no longer supported; use FIFTEEN_MINUTES or longer"); valid values seen: FIFTEEN_MINUTES, THIRTY_MINUTES, ONE_HOUR, DAILY.
- The spike's trigger value (0.8910875) equals the responder-agent spend of the rolling window 17:22-17:37 (prelude $0.1799 + incident $0.7112), not the clock-aligned 17:30-17:45 bucket ($0.7112). So the windows are rolling and end at the data-through time, not clock-aligned.
- Why the jump rule did not fire: evaluated the same way, its current window (17:22-17:37) contained both the prelude and the incident ($1.85) and its previous window (17:07-17:22) was empty, so there was no non-zero baseline to compare against. The prelude was meant to supply that baseline, but I scheduled it for clock-aligned windows. This is an inference from one run, not something Revenium documents.

## Fixes made after the run

- Every pipeline call in every mode now sends `trace_type` ("ticket-short"/"ticket-long" by thread size; baseline and incident previously sent none). Confirmed on the wire for one Acme baseline ticket.
- `scripts/run_incident.sh`: `--no-prelude`, `--no-wait`, `RUN_TAG` override.
- `scripts/setup_alerts.py --retake`: retunes the rules (daily budget = today's Cobalt spend + $0.40, spike $0.20, jump disabled) and writes `config/alerts.yaml` for the run-time crossing log.
