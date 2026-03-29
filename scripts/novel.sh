#!/usr/bin/env bash
# novel.sh — 互动爽文小说生成器入口
#
# 用法（无需手动激活 venv）：
#   ./scripts/novel.sh                            # 交互模式
#   ./scripts/novel.sh generate --chapters 50     # 直接传参
#   ./scripts/novel.sh generate --resume          # 从断点继续
#   ./scripts/novel.sh test                       # 10章快速测试
#   ./scripts/novel.sh --help

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
SCRIPT="$ROOT_DIR/scripts/generate_if.py"

# 检查 venv
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "错误：未找到 .venv/bin/python，请先运行 scripts/start.sh 安装依赖。"
    exit 1
fi

# 加载 .env / .env.local（可选，不报错）
for env_file in "$ROOT_DIR/.env" "$ROOT_DIR/.env.local"; do
    if [[ -f "$env_file" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$env_file" 2>/dev/null || true
        set +a
    fi
done

# 若没有任何参数，默认进入 generate 交互模式
if [[ $# -eq 0 ]]; then
    exec "$VENV_PYTHON" "$SCRIPT" generate
fi

exec "$VENV_PYTHON" "$SCRIPT" "$@"
