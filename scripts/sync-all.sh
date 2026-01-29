#!/usr/bin/env bash
# sync-all.sh — Run full sync pipeline: issues → PRs → aggregate
# Cron-safe: all output to stderr, exits cleanly
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TRIAGE_DIR="$(dirname "$SCRIPT_DIR")"

log() { echo "[sync-all] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2; }

log "Starting full sync..."
log "Triage dir: $TRIAGE_DIR"

# Step 1: Sync issues
log "=== Step 1/3: Syncing issues ==="
bash "$SCRIPT_DIR/sync-issues.sh"

# Step 2: Sync PRs
log "=== Step 2/3: Syncing PRs ==="
bash "$SCRIPT_DIR/sync-prs.sh"

# Step 3: Aggregate
log "=== Step 3/3: Running aggregation ==="
python3 "$SCRIPT_DIR/aggregate.py"

log "=== All done! ==="
log "Results in: $TRIAGE_DIR/aggregated/"
