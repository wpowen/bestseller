#!/usr/bin/env bash
# =============================================================================
# translate_all.sh — 《天机录》全书多语言翻译（一键执行）
#
# 全局限速：整个 API 合计不超过 40 TPS
# 三语言通过共享 FIFO 令牌桶实现全局限速
# =============================================================================

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
TOOL="$ROOT_DIR/scripts/translate_novel.py"

INPUT="$ROOT_DIR/output/天机录/if/chapters"
OUTPUT="$ROOT_DIR/output/天机录/translations"
LOG_DIR="$OUTPUT/logs"
LANGS=("en" "ja" "ko")

MODEL="openai/MiniMax-M2.7"
API_BASE="https://api.minimaxi.com/v1"

# ---------------------------------------------------------------------------
# 全局限速：总并发 40，三语言均分
# ---------------------------------------------------------------------------
TOTAL_TPS=40                          # 整个 API 最大 40 TPS
TOTAL_WORKERS=40                      # 总并发线程数
WORKERS_PER_LANG=$((TOTAL_WORKERS / ${#LANGS[@]}))   # ~13 per lang
RPM_PER_LANG=$(( TOTAL_TPS * 60 / ${#LANGS[@]} ))    # 800 RPM per lang

MAX_RETRIES=20
RETRY_WAIT=60

# ---------------------------------------------------------------------------
# 加载 .env
# ---------------------------------------------------------------------------
for env_file in "$ROOT_DIR/.env" "$ROOT_DIR/.env.local"; do
    [[ -f "$env_file" ]] && { set -a; source "$env_file" 2>/dev/null || true; set +a; }
done

# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
if [[ -z "${MINIMAX_API_KEY:-}" ]]; then
    echo "❌ MINIMAX_API_KEY 未设置，请检查 .env 文件"
    exit 1
fi
if [[ ! -d "$INPUT" ]]; then
    echo "❌ 源章节目录不存在：$INPUT"
    exit 1
fi

CHAPTER_COUNT=$(find "$INPUT" -name "ch*.json" 2>/dev/null | wc -l | tr -d ' ')
if [[ "$CHAPTER_COUNT" -eq 0 ]]; then
    echo "❌ 未找到章节文件"
    exit 1
fi

mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
MAIN_LOG="$LOG_DIR/main.log"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg" | tee -a "$MAIN_LOG"
}

count_chapters() {
    find "$1" -name "ch*.json" 2>/dev/null | wc -l | tr -d ' '
}

# ---------------------------------------------------------------------------
# Phase 0：提取术语表
# ---------------------------------------------------------------------------
GLOSSARY="$OUTPUT/glossary.json"

log "═══════════════════════════════════════════════════════"
log "  《天机录》多语言翻译"
log "  章节总数：$CHAPTER_COUNT  |  语言：${LANGS[*]}"
log "  模型：$MODEL"
log "  全局 TPS 上限：$TOTAL_TPS  |  总并发：$TOTAL_WORKERS"
log "  per lang: RPM=$RPM_PER_LANG, workers=$WORKERS_PER_LANG"
log "═══════════════════════════════════════════════════════"

if [[ -f "$GLOSSARY" ]]; then
    TERM_COUNT=$("$VENV_PYTHON" -c "
import json
g = json.load(open('$GLOSSARY'))
print(sum(len(v) for v in g.values() if isinstance(v, dict)))
" 2>/dev/null || echo "?")
    log "✓ 术语表已存在（${TERM_COUNT} 个条目），跳过提取"
else
    log "── Phase 0：提取专有名词术语表 ──"
    GLOG="$LOG_DIR/glossary.log"
    "$VENV_PYTHON" "$TOOL" extract-glossary \
        --input    "$INPUT"   \
        --output   "$OUTPUT"  \
        --model    "$MODEL"   \
        --api-key  "$MINIMAX_API_KEY" \
        --api-base "$API_BASE" \
        --sample   50         \
        >> "$GLOG" 2>&1 \
    && log "✓ 术语表提取完成：$GLOSSARY" \
    || log "⚠ 术语表提取失败（继续翻译），详见：$GLOG"
fi

# ---------------------------------------------------------------------------
# Phase 1：单语言翻译函数
# ---------------------------------------------------------------------------
translate_lang() {
    local lang="$1"
    local logfile="$LOG_DIR/${lang}.log"
    local attempt=0
    local wait_time=$RETRY_WAIT

    set +euo pipefail 2>/dev/null || true

    log "[$lang] starting: workers=$WORKERS_PER_LANG, rpm=$RPM_PER_LANG"

    while [[ $attempt -lt $MAX_RETRIES ]]; do
        attempt=$((attempt + 1))
        done_before=$(count_chapters "$OUTPUT/$lang/chapters")

        "$VENV_PYTHON" "$TOOL" translate \
            --input    "$INPUT"              \
            --output   "$OUTPUT"             \
            --lang     "$lang"               \
            --model    "$MODEL"              \
            --api-key  "$MINIMAX_API_KEY"    \
            --api-base "$API_BASE"           \
            --workers  "$WORKERS_PER_LANG"   \
            --rpm      "$RPM_PER_LANG"       \
            --yes                            \
            >> "$logfile" 2>&1
        EXIT_CODE=$?

        done_after=$(count_chapters "$OUTPUT/$lang/chapters")
        log "[$lang] 第 $attempt 次 | 退出码：$EXIT_CODE | 本轮完成：$((done_after - done_before)) 章 | 累计：$done_after/$CHAPTER_COUNT 章"

        if [[ $EXIT_CODE -eq 0 ]]; then
            log "✅ [$lang] 翻译完成（$done_after 章）"
            return 0
        fi

        if [[ "$done_after" -ge "$CHAPTER_COUNT" ]]; then
            log "✅ [$lang] 全部章节已完成（$done_after 章）"
            return 0
        fi

        if [[ $attempt -lt $MAX_RETRIES ]]; then
            if [[ $done_after -gt $done_before ]]; then
                wait_time=$RETRY_WAIT
            else
                wait_time=$((wait_time * 2))
                [[ $wait_time -gt 300 ]] && wait_time=300
            fi
            log "[$lang] 等待 ${wait_time}s 后重试..."
            sleep "$wait_time"
        fi
    done

    log "❌ [$lang] 已达最大重试次数（$MAX_RETRIES），停止"
    return 1
}

# ---------------------------------------------------------------------------
# 进度表
# ---------------------------------------------------------------------------
print_progress() {
    local elapsed="$1"
    local mins=$((elapsed / 60))
    local secs=$((elapsed % 60))
    if [[ "${PROGRESS_PRINTED:-0}" -eq 1 ]]; then
        printf '\033[7A\033[J'
    fi
    PROGRESS_PRINTED=1

    echo "┌─────────────────────────────────────────────────────┐"
    printf "│  翻译进度  已运行: %02d:%02d                           │\n" "$mins" "$secs"
    echo "├──────┬──────────────────────────┬─────────────────────┤"
    for lang in "${LANGS[@]}"; do
        local done_count
        done_count=$(count_chapters "$OUTPUT/$lang/chapters")
        local pct
        pct=$(awk "BEGIN{printf \"%.1f\", $done_count*100/$CHAPTER_COUNT}")
        local bar_filled
        bar_filled=$(awk "BEGIN{printf \"%d\", $done_count*20/$CHAPTER_COUNT}")
        local bar="" i=0
        while [[ $i -lt $bar_filled ]]; do bar="${bar}█"; i=$((i+1)); done
        while [[ $i -lt 20 ]];         do bar="${bar}░"; i=$((i+1)); done
        printf "│  %-4s│ %s │ %4s/%-4s  %5s%% │\n" \
            "$lang" "$bar" "$done_count" "$CHAPTER_COUNT" "$pct"
    done
    echo "└──────┴──────────────────────────┴─────────────────────┘"
}

# ---------------------------------------------------------------------------
# 三语言并行启动
# ---------------------------------------------------------------------------
log ""
log "── Phase 1：三语言并行翻译 ──"
log "  详细日志：tail -f $LOG_DIR/en.log"
log ""

PIDS=()
for lang in "${LANGS[@]}"; do
    translate_lang "$lang" &
    PIDS+=($!)
    sleep 2
done

START_TIME=$(date +%s)
PROGRESS_PRINTED=0
all_done=false
while ! $all_done; do
    all_done=true
    for pid in "${PIDS[@]}"; do
        kill -0 "$pid" 2>/dev/null && all_done=false && break
    done
    elapsed=$(( $(date +%s) - START_TIME ))
    print_progress "$elapsed"
    $all_done || sleep 30
done

elapsed=$(( $(date +%s) - START_TIME ))
print_progress "$elapsed"
echo ""

EXIT_CODES=()
for pid in "${PIDS[@]}"; do
    wait "$pid"
    EXIT_CODES+=($?)
done

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
log ""
log "═══════════════════════════════════════════════════════"
log "  翻译结果汇总"
log "═══════════════════════════════════════════════════════"

ALL_OK=true
for i in "${!LANGS[@]}"; do
    lang="${LANGS[$i]}"
    code="${EXIT_CODES[$i]}"
    done_count=$(count_chapters "$OUTPUT/$lang/chapters")
    pct=$(awk "BEGIN{printf \"%.1f\", $done_count*100/$CHAPTER_COUNT}" 2>/dev/null || echo "?")
    if [[ "$code" -eq 0 ]]; then
        log "  $lang: success -- $done_count / $CHAPTER_COUNT ($pct%)"
    else
        log "  $lang: FAILED -- $done_count / $CHAPTER_COUNT ($pct%)"
        ALL_OK=false
    fi
done

log ""
if $ALL_OK; then
    log "🎉 全部翻译完成！输出：$OUTPUT"
else
    log "⚠ 部分语言未完成，重新执行此脚本可断点续传"
fi
log "═══════════════════════════════════════════════════════"
