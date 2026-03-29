#!/usr/bin/env bash
# tianjilu_auto.sh — 《天机录》自动续写守护脚本
#
# 中断后自动等待并重试，直到生成完成。
# 进度面板正常显示，日志写入 generate.log。
#
# 用法：
#   ./scripts/tianjilu_auto.sh          # 从断点续写（推荐）
#   ./scripts/tianjilu_auto.sh --fresh  # 全新开始

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
CONCEPT="$ROOT_DIR/story-factory/concepts/tianjilu_001.json"
OUTPUT="$ROOT_DIR/output"
LOG_FILE="$ROOT_DIR/output/天机录/if/generate.log"
PROGRESS_FILE="$ROOT_DIR/output/天机录/if/if_progress.json"

MAX_RETRIES=50
RETRY_WAIT_BASE=30
RETRY_WAIT_MAX=300
FRESH=false

for arg in "$@"; do
    [[ "$arg" == "--fresh" ]] && FRESH=true
done

# 加载 .env
for env_file in "$ROOT_DIR/.env" "$ROOT_DIR/.env.local"; do
    [[ -f "$env_file" ]] && { set -a; source "$env_file" 2>/dev/null || true; set +a; }
done

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

chapter_count() {
    ls "$ROOT_DIR/output/天机录/if/chapters/"ch*.json 2>/dev/null | wc -l | tr -d ' '
}

log "═══════════════════════════════════════════"
log "《天机录》自动续写守护进程启动"
log "═══════════════════════════════════════════"

attempt=0
wait_time=$RETRY_WAIT_BASE

while [[ $attempt -lt $MAX_RETRIES ]]; do
    attempt=$((attempt + 1))
    ch_before=$(chapter_count)

    # 每次启动前检查并修复章节空洞
    if [[ -f "$PROGRESS_FILE" ]]; then
        "$VENV_PYTHON" "$ROOT_DIR/scripts/fix_arc_gap.py" \
            "$ROOT_DIR/output/天机录" >> "$LOG_FILE" 2>&1 || true
    fi

    log "─── 第 $attempt 次生成（当前已有 $ch_before 章）───"

    # 构建参数
    BASE_ARGS="--concept $CONCEPT --output $OUTPUT --branches --branch-count 3 --yes"
    if [[ "$FRESH" == "true" && $attempt -eq 1 ]]; then
        EXTRA_ARGS=""
    else
        EXTRA_ARGS="--resume"
    fi

    # 运行生成器（直接输出到终端，Rich 面板正常显示）
    # 同时把关键事件追加到日志
    set +e
    "$VENV_PYTHON" "$ROOT_DIR/scripts/generate_if.py" generate \
        $BASE_ARGS $EXTRA_ARGS
    EXIT_CODE=$?
    set -e

    ch_after=$(chapter_count)
    log "退出码: $EXIT_CODE | 本轮新增: $((ch_after - ch_before)) 章 | 累计: $ch_after 章"

    if [[ $EXIT_CODE -eq 0 ]]; then
        log "✅ 生成完成！共 $ch_after 章"

        # 更新 story_package.json 供阅读器使用
        log "更新 story_package.json..."
        "$VENV_PYTHON" - << 'PYEOF'
import json
from pathlib import Path
base = Path('output/天机录/if')
prog_path = base / 'if_progress.json'
pkg_path = base / 'story_package.json'
if not prog_path.exists():
    print("progress 文件不存在，跳过")
    exit(0)
prog = json.loads(prog_path.read_text())
chapters = []
for f in sorted((base / 'chapters').glob('ch*.json')):
    try:
        chapters.append(json.loads(f.read_text()))
    except Exception:
        pass
if pkg_path.exists():
    pkg = json.loads(pkg_path.read_text())
else:
    pkg = {"book": prog.get("bible", {}).get("book", {}), "chapters": []}
pkg['chapters'] = chapters
pkg_path.write_text(json.dumps(pkg, ensure_ascii=False, indent=2))
print(f"story_package.json 更新至 {len(chapters)} 章")
PYEOF
        echo ""
        echo "🎉 《天机录》全部生成完成！"
        echo "   阅读地址：http://localhost:8787/read-if/天机录"
        echo "   日志：$LOG_FILE"
        exit 0
    fi

    # 根据是否有进展调整等待时间
    if [[ $ch_after -gt $ch_before ]]; then
        wait_time=$RETRY_WAIT_BASE
        log "有进展，${wait_time}s 后重试..."
    else
        wait_time=$((wait_time * 2))
        [[ $wait_time -gt $RETRY_WAIT_MAX ]] && wait_time=$RETRY_WAIT_MAX
        log "无进展（可能 API 过载），等待 ${wait_time}s 后重试..."
    fi

    sleep "$wait_time"
done

log "❌ 已达到最大重试次数 ($MAX_RETRIES)，停止。"
exit 1
