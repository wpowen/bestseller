#!/bin/bash
# auto_p3_finalize.sh — Wait for 3rd-pass retranslate, then revert + audit + Amazon rebuild.

set -u
cd "$(dirname "$0")/.."
LOG="output/天机录/amazon/quality_audit/auto_p3_finalize.log"
mkdir -p "$(dirname "$LOG")"
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "auto_p3_finalize started"
source .venv/bin/activate

# 1. Wait for all retranslate processes to finish
while true; do
    n=$(pgrep -f "retranslate_residual.py" | wc -l | tr -d ' ')
    if [ "$n" = "0" ]; then
        log "All retranslate processes finished"
        break
    fi
    log "Waiting: $n retranslate still running"
    sleep 300
done

# 2. Revert regressions
log "Step 1/3: revert regressions"
python scripts/revert_regressions.py >> "$LOG" 2>&1 \
    && log "revert OK" || log "revert FAILED"

# 3. Re-audit
log "Step 2/3: smart audit"
python scripts/smart_audit.py >> "$LOG" 2>&1 \
    && log "smart_audit OK" || log "smart_audit FAILED"

# 4. Rebuild Amazon artifacts (all 4 langs)
log "Step 3/3: rebuild Amazon artifacts"
for lang in zh en ja ko; do
    log "  building $lang"
    python scripts/build_amazon_book.py --lang "$lang" >> "$LOG" 2>&1 \
        && log "  $lang OK" || log "  $lang FAILED"
done

log "auto_p3_finalize complete"
