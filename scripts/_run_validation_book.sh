#!/usr/bin/env bash
# Validation-run launcher: generate a fresh book end-to-end in "soft strict" mode
# (gates RUN + REPORT + drive REPAIR, but never hard-abort the whole book — the
# direction the user chose for verifying the new scene-grounding capabilities).
# All LLM roles use DeepSeek (MiniMax is rate-limited by the docker workers and
# times out on heavy planner/writer calls). Scene-grounding + material-concreteness
# are model-agnostic prompt injections, so this still validates them.
set -u
cd "$(dirname "$0")/.."

# Book-specific fields are all overridable args so this harness is not tied to any
# one title. Provide a SLUG (arg 1) and, if you want a real run, a PREMISE
# (BESTSELLER_VALIDATION_PREMISE) + TITLE/GENRE/SUBGENRE/PROMPT_PACK envs.
SLUG="${1:-validation-book}"
LOG="${2:-/tmp/_autowrite_validation.log}"

TITLE="${BESTSELLER_VALIDATION_TITLE:-验证样书}"
GENRE="${BESTSELLER_VALIDATION_GENRE:-都市异能}"
SUBGENRE="${BESTSELLER_VALIDATION_SUBGENRE:-身份反转}"
PROMPT_PACK="${BESTSELLER_VALIDATION_PROMPT_PACK:-urban-power-reversal}"
PREMISE="${BESTSELLER_VALIDATION_PREMISE:-主角在一场意外中觉醒一种带代价的异能：每使用一次，都要付出一段珍贵的东西作为抵押。他借此一步步逼近多年前那桩悬案的真凶，必须在代价彻底吞没自己之前查明真相。}"

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
# Per-chapter review + whole-book consistency: run + report + repair, but a
# non-converging critic verdict must not halt the whole-book loop (the user's
# chosen "跑+报告+修复但不硬中止" semantic). Lets all 10 chapters generate so the
# full pipeline can be validated end-to-end.
export BESTSELLER__PIPELINE__CHAPTER_REVIEW_BLOCK_ON_FAILURE="false"
export BESTSELLER__PIPELINE__PROJECT_CONSISTENCY_BLOCK_ON_FAILURE="false"

exec .venv/bin/bestseller project autowrite "$SLUG" "$TITLE" "$GENRE" 25000 10 \
  --sub-genre "$SUBGENRE" --premise "$PREMISE" --prompt-pack "$PROMPT_PACK" \
  --progress --auto-repair --export-markdown
