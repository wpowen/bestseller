from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bestseller.services.mode_b_bridge import (
    MODE_B_FRAMEWORK_PACKAGE,
    ModeBChapterOutcome,
    enqueue_repair_item,
    load_mode_b_framework_package,
    resolve_mode_b_root,
    sync_progress_yaml,
)

pytestmark = pytest.mark.unit


def _outcome(passed: bool) -> ModeBChapterOutcome:
    return ModeBChapterOutcome(
        chapter_number=7,
        passed=passed,
        requires_human_review=not passed,
        word_count=4200 if passed else 530,
        verdict="pass" if passed else "rewrite",
        block_codes=() if passed else ("WORD_COUNT_METADATA_MISMATCH",),
        output_path="/tmp/ch-007.md",
        next_state="COMMIT_CHAPTER" if passed else "REWRITE_CHAPTER",
        workflow_run_id="workflow-007",
    )


def test_resolve_mode_b_root() -> None:
    root = resolve_mode_b_root("fen-xin-jue", output_base_dir="output")
    assert root == Path("output/ai-generated/fen-xin-jue")


def test_sync_progress_writes_truth_on_pass(tmp_path: Path) -> None:
    root = tmp_path / "ai-generated" / "fen-xin-jue"
    root.mkdir(parents=True)
    (root / "progress.yaml").write_text(
        yaml.safe_dump(
            {"project_slug": "fen-xin-jue", "state": "WRITE_CHAPTER", "chapters": {}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    path = sync_progress_yaml(
        "fen-xin-jue", _outcome(True), output_base_dir=str(tmp_path)
    )
    assert path is not None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["state"] == "COMMIT_CHAPTER"
    assert data["chapters"]["007"]["state"] == "committed"
    assert data["chapters"]["007"]["word_count"] == 4200
    assert data["chapters"]["007"]["runtime_workflow_run_id"] == "workflow-007"
    assert data["runtime_projection"]["source"] == "postgresql"


def test_sync_progress_routes_to_rewrite_on_fail(tmp_path: Path) -> None:
    root = tmp_path / "ai-generated" / "fen-xin-jue"
    root.mkdir(parents=True)
    (root / "progress.yaml").write_text(
        yaml.safe_dump({"chapters": {}}, allow_unicode=True), encoding="utf-8"
    )

    path = sync_progress_yaml(
        "fen-xin-jue", _outcome(False), output_base_dir=str(tmp_path)
    )
    assert path is not None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["state"] == "REWRITE_CHAPTER"
    assert data["chapters"]["007"]["state"] == "rewriting"
    assert "WORD_COUNT_METADATA_MISMATCH" in data["chapters"]["007"]["block_codes"]


def test_sync_progress_noop_when_missing(tmp_path: Path) -> None:
    assert (
        sync_progress_yaml(
            "ghost", _outcome(True), output_base_dir=str(tmp_path)
        )
        is None
    )


def test_enqueue_repair_item_blocks_and_appends(tmp_path: Path) -> None:
    root = tmp_path / "ai-generated" / "fen-xin-jue"
    root.mkdir(parents=True)
    (root / "progress.yaml").write_text(
        yaml.safe_dump({"state": "ADVANCE_CHAPTER", "repair_queue": []}),
        encoding="utf-8",
    )
    path = enqueue_repair_item(
        "fen-xin-jue",
        affected_chapter=20,
        issue_type="consistency_audit",
        description="canon conflict at ch20",
        output_base_dir=str(tmp_path),
    )
    assert path is not None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["state"] == "DRAIN_REPAIR_QUEUE"
    assert len(data["repair_queue"]) == 1
    assert data["repair_queue"][0]["affected_chapter"] == 20
    assert data["repair_queue"][0]["status"] == "pending"


def test_load_mode_b_framework_package_converts_contracts_to_hidden_nodes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ai-generated" / "logic-book"
    (root / "contracts").mkdir(parents=True)
    (root / "meta.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": "logic-book",
                "title": "Logic Book",
                "genre": "fantasy",
                "target_chapters": 1,
                "internal_beats_per_chapter": 2,
                "words_per_chapter": {"target": 2800},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (root / MODE_B_FRAMEWORK_PACKAGE).write_text(
        yaml.safe_dump(
            {
                "story_bible": {
                    "book_spec": {"title": "Logic Book"},
                    "cast_spec": {
                        "protagonist": {"name": "许照川"},
                        "supporting_cast": [],
                    },
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (root / "contracts" / "ch-001.yaml").write_text(
        yaml.safe_dump(
            {
                "chapter": 1,
                "title": "秤砣",
                "input_state": {
                    "story_time": "申末",
                    "location": "守灰房",
                    "participants": ["许照川", "无名收票杂役"],
                },
                "causal_chain": ["票被扣", "许照川要求复秤", "票被退回"],
                "mandatory_events": [
                    {"order": 1, "id": "扣票", "outcome": "票被压上木匣"},
                    {"order": 2, "id": "复秤", "outcome": "误差超过五钱"},
                    {"order": 3, "id": "退票", "outcome": "炭票退回"},
                ],
                "numeric_facts": [{"fact": "阈值", "value": "五钱", "source": "规程"}],
                "state_transitions": ["炭票: 扣留 -> 退回"],
                "knowledge_boundaries": {"许照川_must_not_know": ["后台主使"]},
                "cheap_solutions": {"撕票": "会失去配炭"},
                "exit_state": {"location": "守灰房", "object_ownership": {"炭票": "许照川"}},
                "chapter_end_change": "门外开始换封",
                "anti_ai_focus": "不向读者总结证据意义",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    package = load_mode_b_framework_package(
        "logic-book", output_base_dir=tmp_path
    )

    chapter = package.outline_batch.chapters[0]
    assert chapter.whole_chapter_logic_contract["numeric_facts"][0]["source"] == "规程"
    assert len(chapter.scenes) == 2
    assert sum(scene.target_word_count for scene in chapter.scenes) == 2800
    assert chapter.scenes[0].title == "扣票 / 复秤"
    assert chapter.scenes[1].title == "退票"
    assert chapter.scenes[0].participants == ["许照川"]
    assert "无名收票杂役" in chapter.whole_chapter_logic_contract["entry_state"]["participants"]
