#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.runtime/dev.env"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
RUN_BIN="$ROOT_DIR/scripts/run.sh"
OUTLINE_FILE="$ROOT_DIR/examples/planning/chapter_outline_batch.json"
BOOK_SPEC_FILE="$ROOT_DIR/examples/planning/book_spec.json"
WORLD_SPEC_FILE="$ROOT_DIR/examples/planning/world_spec.json"
CAST_SPEC_FILE="$ROOT_DIR/examples/planning/cast_spec.json"
VOLUME_PLAN_FILE="$ROOT_DIR/examples/planning/volume_plan.json"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing runtime environment file: $ENV_FILE" >&2
  echo "Run ./scripts/start.sh first." >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing Python virtual environment. Run ./scripts/start.sh first." >&2
  exit 1
fi

if [[ ! -x "$RUN_BIN" ]]; then
  echo "Missing CLI wrapper: $RUN_BIN" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

SLUG="verify-$(date +%Y%m%d%H%M%S)"
TITLE="Verify Story $(date +%H%M%S)"
OUTPUT_DIR="$ROOT_DIR/output/$SLUG"
AUTOWRITE_SLUG="${SLUG}-autowrite"
AUTOWRITE_OUTPUT_DIR="$ROOT_DIR/output/$AUTOWRITE_SLUG"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/bestseller-verify.XXXXXX")"

cleanup() {
  rm -rf "$TMP_DIR"
}

trap cleanup EXIT

echo "Running unit tests..."
"$PYTHON_BIN" -m pytest tests/unit -q

echo "Initializing database..."
"$RUN_BIN" db init >/dev/null

echo "Creating project: $SLUG"
"$RUN_BIN" project create "$SLUG" "$TITLE" sci-fi 80000 20 >/dev/null
"$RUN_BIN" planning import "$SLUG" book_spec --file "$BOOK_SPEC_FILE" >/dev/null
"$RUN_BIN" planning import "$SLUG" world_spec --file "$WORLD_SPEC_FILE" >/dev/null
"$RUN_BIN" planning import "$SLUG" cast_spec --file "$CAST_SPEC_FILE" >/dev/null
"$RUN_BIN" planning import "$SLUG" volume_plan --file "$VOLUME_PLAN_FILE" >/dev/null
"$RUN_BIN" planning import "$SLUG" chapter_outline_batch --file "$OUTLINE_FILE" >/dev/null
"$RUN_BIN" workflow materialize-story-bible "$SLUG" >/dev/null
"$RUN_BIN" workflow materialize-outline "$SLUG" >/dev/null

echo "Running chapter-level context, review and rewrite cycle..."
"$RUN_BIN" scene draft "$SLUG" 1 1 >/dev/null
"$RUN_BIN" scene draft "$SLUG" 1 2 >/dev/null
"$RUN_BIN" chapter assemble "$SLUG" 1 >/dev/null
"$RUN_BIN" chapter context "$SLUG" 1 >"$TMP_DIR/chapter-context.json"
"$RUN_BIN" chapter review "$SLUG" 1 >"$TMP_DIR/chapter-review-initial.json"
cat "$TMP_DIR/chapter-review-initial.json"

CHAPTER_REWRITE_TASK_ID="$("$PYTHON_BIN" - <<'PY' "$TMP_DIR/chapter-review-initial.json"
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(payload.get("rewrite_task_id") or "")
PY
)"

if [[ -n "$CHAPTER_REWRITE_TASK_ID" ]]; then
  "$RUN_BIN" chapter rewrite "$SLUG" 1 --rewrite-task-id "$CHAPTER_REWRITE_TASK_ID" >/dev/null
  "$RUN_BIN" chapter review "$SLUG" 1 >"$TMP_DIR/chapter-review-final.json"
else
  echo "Chapter review passed without rewrite task; skipping chapter rewrite branch."
fi

echo "Running project repair over pending rewrite tasks..."
"$RUN_BIN" project repair "$SLUG" >"$TMP_DIR/project-repair.json"

echo "Running scene-level review and rewrite cycle..."
"$RUN_BIN" scene review "$SLUG" 1 1 >"$TMP_DIR/scene-review-initial.json"
cat "$TMP_DIR/scene-review-initial.json"

REWRITE_TASK_ID="$("$PYTHON_BIN" - <<'PY' "$TMP_DIR/scene-review-initial.json"
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(payload.get("rewrite_task_id") or "")
PY
)"

if [[ -n "$REWRITE_TASK_ID" ]]; then
  "$RUN_BIN" rewrite impacts "$SLUG" --rewrite-task-id "$REWRITE_TASK_ID"
  "$RUN_BIN" rewrite cascade "$SLUG" --rewrite-task-id "$REWRITE_TASK_ID" >/dev/null
  "$RUN_BIN" scene rewrite "$SLUG" 1 1 --rewrite-task-id "$REWRITE_TASK_ID" >/dev/null
  "$RUN_BIN" scene review "$SLUG" 1 1
else
  echo "Scene review passed without rewrite task; skipping rewrite/cascade branch."
fi

echo "Running project pipeline..."
"$RUN_BIN" project pipeline "$SLUG" --materialize-story-bible
"$RUN_BIN" project review "$SLUG"
"$RUN_BIN" planning list "$SLUG" >"$TMP_DIR/planning-list.json"
"$RUN_BIN" planning show "$SLUG" book_spec >"$TMP_DIR/planning-show.json"
"$RUN_BIN" project structure "$SLUG" >"$TMP_DIR/project-structure.json"
"$RUN_BIN" story-bible show "$SLUG" >"$TMP_DIR/story-bible.json"
"$RUN_BIN" scene context "$SLUG" 2 1 >"$TMP_DIR/scene-context.json"
"$RUN_BIN" chapter context "$SLUG" 2 >"$TMP_DIR/chapter-context-project.json"
"$RUN_BIN" retrieval refresh "$SLUG" >/dev/null
"$RUN_BIN" retrieval search "$SLUG" --query "沈砚 找证据 真相" >"$TMP_DIR/retrieval-search.json"

"$PYTHON_BIN" - <<'PY' "$TMP_DIR/planning-list.json" "$TMP_DIR/planning-show.json" "$TMP_DIR/project-structure.json" "$TMP_DIR/story-bible.json" "$TMP_DIR/scene-context.json" "$TMP_DIR/chapter-context.json" "$TMP_DIR/chapter-context-project.json" "$TMP_DIR/retrieval-search.json" "$TMP_DIR/project-repair.json"
import json
import pathlib
import sys

planning_list = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
planning_show = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
project_structure = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"))
story_bible = json.loads(pathlib.Path(sys.argv[4]).read_text(encoding="utf-8"))
scene_context = json.loads(pathlib.Path(sys.argv[5]).read_text(encoding="utf-8"))
chapter_context = json.loads(pathlib.Path(sys.argv[6]).read_text(encoding="utf-8"))
chapter_context_project = json.loads(pathlib.Path(sys.argv[7]).read_text(encoding="utf-8"))
retrieval_search = json.loads(pathlib.Path(sys.argv[8]).read_text(encoding="utf-8"))
project_repair = json.loads(pathlib.Path(sys.argv[9]).read_text(encoding="utf-8"))

assert planning_list, "planning list returned no artifacts"
assert planning_show["artifact_type"] == "book_spec", "planning show did not return book_spec"
assert project_structure["total_chapters"] >= 2, "project structure missing chapters"
assert story_bible["characters"], "story bible returned no characters"
assert scene_context["story_bible"], "scene context missing story bible"
assert "recent_scene_summaries" in scene_context, "scene context missing recent summaries"
assert chapter_context["chapter_scenes"], "chapter context missing chapter scenes"
assert chapter_context_project["chapter_scenes"], "project-stage chapter context missing chapter scenes"
assert retrieval_search["chunks"], "retrieval search returned no chunks"
assert "pending_rewrite_task_count" in project_repair, "project repair missing repair counters"
PY

echo "Exporting artifacts..."
"$RUN_BIN" export markdown "$SLUG" >/dev/null
"$RUN_BIN" export docx "$SLUG" >/dev/null
"$RUN_BIN" export epub "$SLUG" >/dev/null
"$RUN_BIN" export pdf "$SLUG" >/dev/null

echo "Running autowrite pipeline..."
"$RUN_BIN" project autowrite "$AUTOWRITE_SLUG" "Autowrite Demo" sci-fi 12000 4 \
  --premise "一名被放逐的导航员发现帝国正在篡改边境航线记录，并被迫在追杀中揭穿真相。" >/dev/null

if [[ ! -f "$OUTPUT_DIR/project.md" ]]; then
  echo "Missing Markdown export: $OUTPUT_DIR/project.md" >&2
  exit 1
fi
if [[ ! -f "$OUTPUT_DIR/project.docx" ]]; then
  echo "Missing DOCX export: $OUTPUT_DIR/project.docx" >&2
  exit 1
fi
if [[ ! -f "$OUTPUT_DIR/project.epub" ]]; then
  echo "Missing EPUB export: $OUTPUT_DIR/project.epub" >&2
  exit 1
fi
if [[ ! -f "$OUTPUT_DIR/project.pdf" ]]; then
  echo "Missing PDF export: $OUTPUT_DIR/project.pdf" >&2
  exit 1
fi
if [[ ! -f "$AUTOWRITE_OUTPUT_DIR/project.md" ]]; then
  echo "Missing autowrite Markdown export: $AUTOWRITE_OUTPUT_DIR/project.md" >&2
  exit 1
fi

echo "Verification completed successfully."
echo "Project slug: $SLUG"
echo "Autowrite slug: $AUTOWRITE_SLUG"
echo "Artifacts:"
ls -1 "$OUTPUT_DIR"
