#!/usr/bin/env bash
# Writer-model lever: baseline prompt (production_control, --limit 1) across N models,
# on a few chapters of both books. Tic-rate per model = the de-AI lever signal.
set -u
cd "$(dirname "$0")/.."
OUT=output/prose-prompt-arena/model-lever
mkdir -p "$OUT"
pick(){ ls -t "output/$1/traces/scene-lean-c2-prompt-ch$(printf '%04d' "$2")-s01-"*.json 2>/dev/null|head -1; }
MODELS=(--writer-model minimax-m3 --writer-model deepseek-v4-flash --writer-model nim-deepseek-v4-pro --writer-model nim-kimi-k2.6 --writer-model xiaomi-mimo-v2.5-pro)
declare -a T
for ch in 1 2; do t=$(pick custom-xianxia-1782461843 $ch); [ -n "$t" ] && T+=("guaitan|$ch|$t"); done
for ch in 1 2; do t=$(pick custom-infinite-flow-1782538671 $ch); [ -n "$t" ] && T+=("modao|$ch|$t"); done
echo "traces: ${#T[@]}"
for e in "${T[@]}"; do IFS='|' read -r book ch tr <<< "$e"; echo "=== $book ch$ch ==="
  .venv/bin/python scripts/prose_prompt_strategy_arena.py --trace "$tr" \
    "${MODELS[@]}" --limit 1 --samples-per-strategy 2 --skip-judging \
    --out "$OUT/${book}-ch${ch}" 2>&1 | grep -iE "variants:|error" | grep -v -i zoxide
done
echo "ALL DONE"
