#!/usr/bin/env bash
# Validation-run launcher: generate a fresh book end-to-end in "soft strict" mode
# (gates RUN + REPORT + drive REPAIR, but never hard-abort the whole book — the
# direction the user chose for verifying the new scene-grounding capabilities).
# All LLM roles use DeepSeek (MiniMax is rate-limited by the docker workers and
# times out on heavy planner/writer calls). Scene-grounding + material-concreteness
# are model-agnostic prompt injections, so this still validates them.
set -u
cd "$(dirname "$0")/.."

SLUG="${1:-memory-pawn-v2}"
LOG="${2:-/tmp/_autowrite_validation.log}"

PREMISE="网约车司机周野在一场连环车祸中觉醒「记忆典当」异能：他能买走他人脑中的一段记忆据为己有，借此获得对应的技能、情报或人脉；但每典当一段记忆，他自己最珍视的一段记忆就被抵押扣押，七天内赎不回便永久消失。他从第一笔典当来的车祸现场记忆里，认出了三年前害死妹妹的真凶——本市顶级催眠师、能批量抹除他人记忆的「静默会」会长沈隅。周野必须在赎金清单一天天加码、自己的过去被一点点抽空之前，用别人的记忆拼出真相、扳倒静默会。"

DS_BASE="https://api.deepseek.com/v1"
DS_MODEL="openai/deepseek-chat"

export BESTSELLER__LLM__PLANNER__MODEL="$DS_MODEL"     BESTSELLER__LLM__PLANNER__API_BASE="$DS_BASE"     BESTSELLER__LLM__PLANNER__API_KEY_ENV="DEEPSEEK_API_KEY"
export BESTSELLER__LLM__WRITER__MODEL="$DS_MODEL"      BESTSELLER__LLM__WRITER__API_BASE="$DS_BASE"      BESTSELLER__LLM__WRITER__API_KEY_ENV="DEEPSEEK_API_KEY"
export BESTSELLER__LLM__CRITIC__MODEL="$DS_MODEL"      BESTSELLER__LLM__CRITIC__API_BASE="$DS_BASE"      BESTSELLER__LLM__CRITIC__API_KEY_ENV="DEEPSEEK_API_KEY"
export BESTSELLER__LLM__SUMMARIZER__MODEL="$DS_MODEL"  BESTSELLER__LLM__SUMMARIZER__API_BASE="$DS_BASE"  BESTSELLER__LLM__SUMMARIZER__API_KEY_ENV="DEEPSEEK_API_KEY"
export BESTSELLER__LLM__EDITOR__MODEL="$DS_MODEL"      BESTSELLER__LLM__EDITOR__API_BASE="$DS_BASE"      BESTSELLER__LLM__EDITOR__API_KEY_ENV="DEEPSEEK_API_KEY"

export BESTSELLER__PIPELINE__STORY_DESIGN_KERNEL_CANDIDATE_COUNT="1"

# Soft strict: every gate still evaluates + reports + drives repair, but no hard abort.
export BESTSELLER__PIPELINE__SCENE_RICHNESS_BLOCK_ON_CRITICAL="false"
export BESTSELLER__PIPELINE__CHAPTER_CAUSALITY_GATE_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__CHAPTER_PREDRAFT_QUALITY_GATE_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__CHAPTER_LLM_COMMERCIAL_JUDGE_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__CHAPTER_WINDOW_LLM_JUDGE_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__COMMERCIAL_PLANNING_READINESS_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__FANQIE_LONG_RANKING_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__METHODOLOGY_PLANNING_READINESS_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__OUTLINE_LLM_COMMERCIAL_JUDGE_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__OUTLINE_READER_EXPERIENCE_JUDGE_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__REVERSE_OUTLINE_GATE_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__VOLUME_LLM_CHECKPOINT_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__QIMAO_OPENING_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__WHOLE_BOOK_QUALITY_GATE_BLOCK_ON_FAILURE="false"

exec .venv/bin/bestseller project autowrite "$SLUG" "典当记忆" "都市异能" 25000 10 \
  --sub-genre "身份反转" --premise "$PREMISE" --prompt-pack urban-power-reversal \
  --progress --auto-repair --export-markdown
