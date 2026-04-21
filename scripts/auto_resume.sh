#!/bin/bash
# auto_resume.sh — Poll MiniMax API, resume retranslate when quota recovers,
# then chain into audit + Amazon rebuild.
# Designed to run as long-lived daemon. Logs to auto_resume.log.

set -u
cd "$(dirname "$0")/.."
LOG="output/天机录/amazon/quality_audit/auto_resume.log"
mkdir -p "$(dirname "$LOG")"
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "auto_resume started; will poll MiniMax API every 5min until usable"

# 1. Wait for API recovery
source .venv/bin/activate
while true; do
    if python -c "
import os, sys
from pathlib import Path
import litellm
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
        k, v = line.split('=', 1); os.environ[k.strip()] = v.strip().strip('\"').strip(\"'\")
key = os.environ.get('MINIMAX_API_KEY')
try:
    litellm.completion(model='openai/MiniMax-M2.7-highspeed', api_key=key,
        api_base='https://api.minimaxi.com/v1',
        messages=[{'role':'user','content':'Hi'}], max_tokens=5, timeout=15)
    sys.exit(0)
except Exception as e:
    sys.exit(1)
"; then
        log "API recovered — proceeding"
        break
    fi
    log "API still limited, sleeping 5min"
    sleep 300
done

# 2. Resume retranslate at LOW concurrency (1 process per lang, workers=2, rpm=30)
log "Step 1/3: resume retranslate at low concurrency"
mkdir -p output/天机录/amazon/quality_audit
nohup python scripts/retranslate_residual.py run --lang ja --model openai/MiniMax-M2.7-highspeed --workers 2 --rpm 30 --yes > output/天机录/amazon/quality_audit/retranslate_ja_resume.log 2>&1 &
JA_PID=$!
nohup python scripts/retranslate_residual.py run --lang ko --model openai/MiniMax-M2.7-highspeed --workers 2 --rpm 30 --yes > output/天机录/amazon/quality_audit/retranslate_ko_resume.log 2>&1 &
KO_PID=$!
nohup python scripts/retranslate_residual.py run --lang en --model openai/MiniMax-M2.7-highspeed --workers 2 --rpm 30 --yes > output/天机录/amazon/quality_audit/retranslate_en_resume.log 2>&1 &
EN_PID=$!
log "Started: ja=$JA_PID ko=$KO_PID en=$EN_PID"

# 3. Wait for all to finish
while true; do
    n=$(pgrep -f "retranslate_residual.py" | wc -l | tr -d ' ')
    if [ "$n" = "0" ]; then
        log "All retranslate processes finished"
        break
    fi
    log "Waiting: $n retranslate processes still running"
    sleep 300
done

# 4. Revert any new regressions
log "Step 2/3: revert regressions"
python scripts/revert_regressions.py >> "$LOG" 2>&1

# 5. Re-audit + smart audit
log "Step 3/3: re-audit + Amazon rebuild"
python scripts/audit_translations.py >> "$LOG" 2>&1
python scripts/smart_audit.py >> "$LOG" 2>&1
for lang in zh en ja ko; do
    log "  building $lang"
    python scripts/build_amazon_book.py --lang "$lang" >> "$LOG" 2>&1 || log "  $lang FAILED"
done

log "auto_resume complete"
