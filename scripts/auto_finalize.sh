#!/bin/bash
# Auto-finalize: wait for all retranslate processes to finish, then run audit + rebuild + summary.
# Designed to be backgrounded; logs to output/天机录/amazon/quality_audit/auto_finalize.log

set -u
cd "$(dirname "$0")/.."
LOG="output/天机录/amazon/quality_audit/auto_finalize.log"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "auto_finalize started"

# 1. Wait until no retranslate_residual processes remain
while true; do
    n=$(pgrep -f "retranslate_residual.py" | wc -l | tr -d ' ')
    if [ "$n" = "0" ]; then
        log "All retranslate processes finished"
        break
    fi
    log "Waiting: $n retranslate processes still running"
    sleep 120
done

# 2. Re-audit translations
log "Step 1/4: Re-audit translations"
source .venv/bin/activate
python scripts/audit_translations.py >> "$LOG" 2>&1 \
    && log "audit_translations OK" \
    || log "audit_translations FAILED"

# 3. Smart audit (residual detection)
log "Step 2/4: Smart audit"
python scripts/smart_audit.py >> "$LOG" 2>&1 \
    && log "smart_audit OK" \
    || log "smart_audit FAILED"

# 4. Re-build Amazon artifacts (all 4 languages)
log "Step 3/4: Re-build Amazon artifacts"
for lang in zh en ja ko; do
    log "  building $lang"
    python scripts/build_amazon_book.py --lang "$lang" >> "$LOG" 2>&1 \
        && log "  $lang OK" \
        || log "  $lang FAILED"
done

# 5. Final summary
log "Step 4/4: Final quality summary"
python -c "
import json
from pathlib import Path

audit = json.loads(Path('output/天机录/amazon/quality_audit/audit_report.json').read_text())
smart = json.loads(Path('output/天机录/amazon/quality_audit/smart_audit_report.json').read_text())

print('===== FINAL QUALITY SUMMARY =====')
for lang in ['en', 'ja', 'ko']:
    a = audit.get(lang, {})
    s = smart.get(lang, {})
    print(f'[{lang.upper()}]')
    print(f'  audit problematic chapters: {a.get(\"summary\", {}).get(\"problematic_chapters\", \"N/A\")}')
    print(f'  smart-audit affected: {s.get(\"affected_chapters\", \"N/A\")}/1200')
    print(f'  total residual strings: {s.get(\"total_residual_strings\", \"N/A\")}')
    sev = s.get('severity', {})
    print(f'  severity: critical={sev.get(\"critical\",0)} high={sev.get(\"high\",0)} medium={sev.get(\"medium\",0)}')
print()
print('Amazon artifacts at: output/天机录/amazon/')
" >> "$LOG" 2>&1

log "auto_finalize complete"
