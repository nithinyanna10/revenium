#!/usr/bin/env bash
# Phase 4 incident: baseline pipeline + agent_version 1.1.0, QA threshold 0.95, max 6 retries, 10 Cobalt tickets.
# Tag incident-v1, spend guard $4 per tag (config/modes.yaml). Prints UTC timestamps so alert latency can be measured.
# Estimate from bulk-v1 Cobalt real costs: ~ $1.4 likely, $0.9 - $2.1 range.
#
# Timing: a 3-ticket baseline prelude (tag incident-v1-prelude) must land fully inside one 15-minute window and the
# incident in the next, so the "vs previous 15m" rule has a non-zero previous window. Revenium doesn't document whether
# its 15m windows are rolling or clock-aligned; this schedule works for both: prelude finishes before a :00/:15/:30/:45
# boundary, incident starts just after it.
#
# Run only after the alerts exist AND you've confirmed:  scripts/run_incident.sh --confirm
#   --no-prelude  skip the 3-ticket baseline prelude
#   --no-wait     don't align to a 15m boundary; start the incident immediately (retake)
#   RUN_TAG=...   env override of the incident run tag (default incident-v1); a fresh tag keeps spend/log counts per run
set -euo pipefail
cd "$(dirname "$0")/.."
CONFIRM=0 PRELUDE=1 WAIT=1 TAG=${RUN_TAG:-incident-v1}
for a in "$@"; do
  case "$a" in
    --confirm) CONFIRM=1 ;; --no-prelude) PRELUDE=0 ;; --no-wait) WAIT=0 ;;
    *) echo "unknown flag $a"; exit 2 ;;
  esac
done
[ "$CONFIRM" = 1 ] || { echo "Dry run. Re-run with --confirm to execute (estimated cost ~\$1.4, +~\$0.4 with the prelude, guard \$4)."; exit 0; }

PRELUDE_BUDGET=300  # s: prelude (3 Cobalt tickets, ~1-2 min) must end >= 20 s before the boundary; start only if >= this far from it
SETTLE=20           # s after the boundary before the incident starts
ts() { date -u +%H:%M:%S; }
secs_to_boundary() { echo $(( 900 - $(date -u +%s) % 900 )); }
wait_until_boundary() {
  while [ "$(secs_to_boundary)" -gt 1 ] && [ "$(secs_to_boundary)" -lt 899 ]; do
    echo "$(ts)Z  waiting for the 15m boundary: $(secs_to_boundary)s"; sleep $(( $(secs_to_boundary) > 30 ? 30 : 1 ))
  done
}

if [ "$PRELUDE" = 1 ]; then
  [ "$(secs_to_boundary)" -ge "$PRELUDE_BUDGET" ] || { echo "$(ts)Z  too close to a boundary for the prelude; waiting for it"; wait_until_boundary; }
  echo "$(ts)Z  === PRELUDE (baseline, 3 Cobalt tickets) start ==="
  uv run python -m ticketfox.run --mode baseline --customers cobalt --tickets 3 --run-tag incident-v1-prelude --watch-alerts
  echo "$(ts)Z  === PRELUDE end ==="
  [ "$(secs_to_boundary)" -ge 20 ] || { echo "$(ts)Z  prelude ran into the boundary; windows are not clean, aborting before the incident"; exit 1; }
fi

if [ "$WAIT" = 1 ]; then wait_until_boundary; sleep "$SETTLE"; fi
echo "$(ts)Z  === INCIDENT start (agent_version 1.1.0, tag $TAG, 10 Cobalt tickets, guard \$4) ==="
uv run python -m ticketfox.run --mode incident --customers cobalt --tickets 10 --run-tag "$TAG" --watch-alerts
echo "$(ts)Z  === INCIDENT end ==="
