#!/usr/bin/env bash
# =============================================================================
# translate_all.sh — 《天机录》全书多语言翻译（一键执行）
#
# 用法：
#   ./scripts/translate_all.sh
# =============================================================================

# 只用 -u（未定义变量报错），不用 -e/-o pipefail（翻译子进程自己处理错误）
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
TOOL="$ROOT_DIR/scripts/translate_novel.py"

INPUT="$ROOT_DIR/output/天机录/if/chapters"
OUTPUT="$ROOT_DIR/output/天机录/translations"
LOG_DIR="$OUTPUT/logs"
LANGS=("en" "ja" "ko")

MODEL="openai/MiniMax-M2.7-highspeed"
API_BASE="https://api.minimaxi.com/v1"
RPM=130      # 每语言 130 RPM × 3 = 390，不超过 500 上限
WORKERS=6    # 每语言并发线程数

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
    local dir="$1"
    find "$dir" -name "ch*.json" 2>/dev/null | wc -l | tr -d ' '
}

# ---------------------------------------------------------------------------
# Phase 0：提取术语表
# ---------------------------------------------------------------------------
GLOSSARY="$OUTPUT/glossary.json"

log "═══════════════════════════════════════════════════════"
log "  《天机录》多语言翻译"
log "  章节总数：$CHAPTER_COUNT  |  语言：${LANGS[*]}"
log "  模型：$MODEL  |  RPM/语言：$RPM  |  并发：$WORKERS"
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
    || log "⚠ 术语表提取失败（继续翻译，不使用术语表），详见：$GLOG"
fi

# ---------------------------------------------------------------------------
# Phase 1：单语言翻译函数（在后台子进程中运行）
# ---------------------------------------------------------------------------
translate_lang() {
    local lang="$1"
    local logfile="$LOG_DIR/${lang}.log"
    local attempt=0
    local wait_time=$RETRY_WAIT

    # 子进程内部不用严格模式，自己管理错误
    set +euo pipefail 2>/dev/null || true

    log "▶ [$lang] 开始翻译，日志：$logfile"

    while [[ $attempt -lt $MAX_RETRIES ]]; do
        attempt=$((attempt + 1))
        done_before=$(count_chapters "$OUTPUT/$lang/chapters")

        "$VENV_PYTHON" "$TOOL" translate \
            --input    "$INPUT"           \
            --output   "$OUTPUT"          \
            --lang     "$lang"            \
            --model    "$MODEL"           \
            --api-key  "$MINIMAX_API_KEY" \
            --api-base "$API_BASE"        \
            --workers  "$WORKERS"         \
            --rpm      "$RPM"             \
            --yes                         \
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
# 三语言并行启动
# ---------------------------------------------------------------------------
log ""
log "── Phase 1：三语言并行翻译 ──"
log "  实时查看进度：tail -f $LOG_DIR/en.log"
log ""

PIDS=()
for lang in "${LANGS[@]}"; do
    translate_lang "$lang" &
    PIDS+=($!)
    sleep 2
done

# 等待所有语言完成
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
        log "  $lang：✅ 成功  —  $done_count / $CHAPTER_COUNT 章（${pct}%）"
    else
        log "  $lang：❌ 失败  —  $done_count / $CHAPTER_COUNT 章（${pct}%）"
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
