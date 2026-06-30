#!/usr/bin/env bash
# Scale-up: baseline vs human_process_first across multiple chapters of both books,
# both writer models, no judging (tic-counts are the metric). See arena --limit 2.
set -u
cd "$(dirname "$0")/.."
OUT=output/prose-prompt-arena/scaleup
mkdir -p "$OUT"

pick() { # latest lean-c2 s01 trace for a slug+chapter
  ls -t "output/$1/traces/scene-lean-c2-prompt-ch$(printf '%04d' "$2")-s01-"*.json 2>/dev/null | head -1
}

declare -a TRACES
for ch in 1 2 3 4 5; do t=$(pick custom-xianxia-1782461843 "$ch"); [ -n "$t" ] && TRACES+=("guaitan|$ch|$t"); done
for ch in 1 2 3;       do t=$(pick custom-infinite-flow-1782538671 "$ch"); [ -n "$t" ] && TRACES+=("modao|$ch|$t"); done

echo "traces to run: ${#TRACES[@]}"
for entry in "${TRACES[@]}"; do
  IFS='|' read -r book ch trace <<< "$entry"
  echo "=== $book ch$ch ==="
  .venv/bin/python scripts/prose_prompt_strategy_arena.py --trace "$trace" \
    --writer-model minimax-m3 --writer-model deepseek-v4-flash \
    --limit 2 --samples-per-strategy 2 --skip-judging \
    --out "$OUT/${book}-ch${ch}" 2>&1 | grep -iE "variants:|drafts:|error" | grep -v -i zoxide
done
echo "ALL DONE"
