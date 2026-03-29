#!/usr/bin/env bash
# tianjilu.sh — 《天机录》1200章互动小说生成器
#
# 用法：
#   ./scripts/tianjilu.sh              # 全新生成（会先确认）
#   ./scripts/tianjilu.sh --resume     # 从断点继续（崩溃后恢复）
#   ./scripts/tianjilu.sh --dry-run    # 仅展示配置，不开始生成
#
# 输出目录：./output/天机录/if/

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
SCRIPT="$ROOT_DIR/scripts/generate_if.py"
CONCEPT="$ROOT_DIR/story-factory/concepts/tianjilu_001.json"

# 检查 venv
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "错误：未找到 .venv/bin/python，请先运行 scripts/start.sh 安装依赖。"
    exit 1
fi

# 加载 .env / .env.local
for env_file in "$ROOT_DIR/.env" "$ROOT_DIR/.env.local"; do
    if [[ -f "$env_file" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$env_file" 2>/dev/null || true
        set +a
    fi
done

# 解析参数
RESUME=""
for arg in "$@"; do
    case "$arg" in
        --resume) RESUME="--resume" ;;
        --dry-run)
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "  《天机录》生成配置（试运行，不执行生成）"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "  concept   : $CONCEPT"
            echo "  章节数    : 1200"
            echo "  卷结构    : 每卷 120 章，共 10 卷"
            echo "  弧线大小  : 15 章/弧"
            echo "  分支      : 3 条硬分支，每条 40 章"
            echo "  结局      : 5 个（含1个隐藏结局）"
            echo "  输出目录  : ./output/天机录/"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo ""
            echo "运行命令："
            echo "  ./scripts/tianjilu.sh"
            echo "  ./scripts/tianjilu.sh --resume   # 断点续写"
            exit 0
            ;;
    esac
done

exec "$VENV_PYTHON" "$SCRIPT" generate \
    --concept "$CONCEPT" \
    --output "./output" \
    --branches \
    --branch-count 3 \
    $RESUME
