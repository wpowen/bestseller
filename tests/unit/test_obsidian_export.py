from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from bestseller.services.material_density import DimensionDensity, MaterialDensityReport
from bestseller.services.obsidian import (
    DistillationAggregateSummary,
    DistillationPackageSummary,
    MaterialDimensionSummary,
    MethodologyDeckSummary,
    ModelCallAsset,
    ObsidianVaultPayload,
    PromptPackSummary,
    build_obsidian_documents,
    write_obsidian_vault,
)


def _payload() -> ObsidianVaultPayload:
    project = SimpleNamespace(
        id=uuid4(),
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        sub_genre="frontier",
        target_chapters=3,
        target_word_count=9000,
        language="zh-CN",
    )
    story_bible = SimpleNamespace(
        world_backbone=SimpleNamespace(
            world_name="边境星门航道",
            world_premise="航道记录决定一切。",
            power_system_name="导航印记",
            power_structure="帝国控制解释权",
            forbidden_zones="日志库",
        ),
        world_rules=[
            SimpleNamespace(
                rule_code="R001",
                name="航道记录优先",
                description="官方航图高于个人证词。",
                story_consequence="主角无法直接翻案。",
                exploitation_potential="拿到底层日志即可翻盘。",
            )
        ],
        locations=[],
        factions=[],
        characters=[
            SimpleNamespace(
                name="沈砚",
                role="protagonist",
                goal="夺回航线真相",
                fear="失去搭档",
                flaw="不信任任何制度",
                strength="能读底层日志",
                secret="曾参与事故航线",
                arc_state="被放逐",
                alive_status="alive",
                stance="active",
            ),
            SimpleNamespace(
                name="林照",
                role="ally",
                arc_state="半信半疑",
                alive_status="alive",
                stance="testing",
            ),
        ],
        relationships=[
            SimpleNamespace(
                character_a="沈砚",
                character_b="林照",
                relationship_type="uneasy-alliance",
                strength=0.4,
                tension_summary="互相需要但都握着秘密。",
            )
        ],
        volume_frontiers=[],
        deferred_reveals=[
            SimpleNamespace(
                reveal_code="REV-1",
                label="事故航线真相",
                category="key_reveal",
                summary="航道署篡改日志。",
                reveal_volume_number=1,
                reveal_chapter_number=3,
                status="scheduled",
            )
        ],
        expansion_gates=[],
    )
    canon_fact = SimpleNamespace(
        subject_type="character",
        subject_label="沈砚",
        predicate="status",
        value_json={"state": "被放逐"},
        valid_from_chapter_no=1,
        valid_to_chapter_no=None,
        is_current=True,
    )
    timeline_event = SimpleNamespace(
        story_order=1.0,
        story_time_label="D-7",
        event_type="inciting_incident",
        event_name="沈砚发现航图被删改",
        participant_ids=["沈砚"],
        consequences=["触发调查"],
    )
    return ObsidianVaultPayload(
        project=project,
        story_bible=story_bible,
        canon_facts=[canon_fact],
        timeline_events=[timeline_event],
        include_chapters=False,
        include_raw=False,
    )


def _asset_payload() -> ObsidianVaultPayload:
    payload = _payload()
    return ObsidianVaultPayload(
        project=payload.project,
        story_bible=payload.story_bible,
        canon_facts=payload.canon_facts,
        timeline_events=payload.timeline_events,
        global_material_dimensions=[
            MaterialDimensionSummary(
                dimension="scene_templates",
                status="active",
                genre="science-fantasy",
                count=2,
                avg_confidence=0.85,
                avg_coverage_score=0.7,
            )
        ],
        material_density=MaterialDensityReport(
            project_id=str(payload.project.id),
            genre=payload.project.genre,
            sub_genre=payload.project.sub_genre,
            genre_buckets=("science-fantasy",),
            dimensions=(
                DimensionDensity(
                    dimension="scene_templates",
                    active_count=1,
                    target_count=3,
                    global_seed_count=2,
                ),
            ),
            total_active=1,
            total_target=3,
        ),
        distillation_packages=[
            DistillationPackageSummary(
                source_id="source-demo",
                relative_path="data/distillation/source-demo",
                ok=False,
                missing_files=("mechanisms.jsonl",),
            )
        ],
        distillation_aggregates=[
            DistillationAggregateSummary(
                aggregate_key="science-fantasy",
                relative_path="data/distillation/aggregates/science-fantasy",
                source_ids=("source-demo",),
                maturity_status="review",
                maturity_score=0.72,
                material_rows=12,
                mechanism_rows=5,
                anti_copy_rules=3,
            )
        ],
        prompt_packs=[
            PromptPackSummary(
                key="demo",
                name="Demo Prompt Pack",
                version="1.0",
                genres=("science-fantasy",),
                tags=("draft",),
                fragment_count=4,
                source_note_count=1,
                relative_path="config/prompt_packs/demo.yaml",
            )
        ],
        methodology_decks=[
            MethodologyDeckSummary(
                source_set_id="demo-method",
                relative_path="data/methodology_sources/demo-method",
                card_count=3,
                verified_cards=2,
                verified_sources=2,
            )
        ],
        model_call_assets=[
            ModelCallAsset(
                asset_id="prompt-pack:demo",
                asset_type="prompt_pack",
                title="Demo Prompt Pack",
                status="ready",
                source_path="config/prompt_packs/demo.yaml",
                use_for=("writer_prompt",),
                tags=("draft",),
            )
        ],
        include_chapters=False,
        include_raw=True,
        include_system_assets=True,
    )


def test_build_obsidian_documents_creates_linked_vault_notes() -> None:
    docs = build_obsidian_documents(_payload())
    by_path = {doc.relative_path.as_posix(): doc.content_md for doc in docs}

    assert "00-主页.md" in by_path
    assert "人物/人物索引.md" in by_path
    assert "人物/沈砚.md" in by_path
    assert "[[人物/林照|林照]]" in by_path["人物/沈砚.md"]
    assert "[[Canon/当前事实|Canon 当前事实]]" in by_path["00-主页.md"]
    assert "DB 是真值源" in by_path["维护/维护看板.md"]
    assert "type: \"character\"" in by_path["人物/沈砚.md"]


def test_build_obsidian_documents_includes_asset_workbench_and_raw_indexes() -> None:
    docs = build_obsidian_documents(_asset_payload())
    by_path = {doc.relative_path.as_posix(): doc.content_md for doc in docs}

    assert "资料资产/总览.md" in by_path
    assert "资料资产/缺口看板.md" in by_path
    assert "模型调用索引.md" in by_path
    assert "raw/model-call-index.json" in by_path
    assert "raw/material-coverage.json" in by_path
    assert "raw/asset-workbench.json" in by_path
    assert "prompt-pack:demo" in by_path["模型调用索引.md"]
    assert "补充项目物料" in by_path["资料资产/缺口看板.md"]

    model_index = json.loads(by_path["raw/model-call-index.json"])
    assert model_index["schema"] == "bestseller.model_call_index.v1"
    assert model_index["assets"][0]["asset_id"] == "prompt-pack:demo"


def test_build_obsidian_documents_can_disable_system_asset_pages() -> None:
    payload = _payload()
    payload = ObsidianVaultPayload(
        project=payload.project,
        story_bible=payload.story_bible,
        canon_facts=payload.canon_facts,
        timeline_events=payload.timeline_events,
        include_chapters=False,
        include_raw=True,
        include_system_assets=False,
    )
    docs = build_obsidian_documents(payload)
    by_path = {doc.relative_path.as_posix(): doc.content_md for doc in docs}

    assert "资料资产/总览.md" not in by_path
    assert "模型调用索引.md" not in by_path
    assert "raw/model-call-index.json" not in by_path
    assert "[[资料资产/总览|资料资产]]" not in by_path["00-主页.md"]


def test_write_obsidian_vault_preserves_manual_inbox_notes(tmp_path) -> None:
    payload = _payload()
    docs = build_obsidian_documents(payload)
    manual_note = tmp_path / "Inbox" / "manual.md"
    manual_note.parent.mkdir(parents=True)
    manual_note.write_text("作者人工补丁", encoding="utf-8")

    manifest = write_obsidian_vault(tmp_path, docs, payload=payload)

    assert manual_note.read_text(encoding="utf-8") == "作者人工补丁"
    manifest_path = tmp_path / "_manifest.json"
    assert manifest_path.exists()
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_payload["source_of_truth"] == "postgresql"
    assert manifest_payload["managed_file_count"] == len(docs)
    assert manifest["project_slug"] == "my-story"
    assert (tmp_path / ".obsidian" / "app.json").exists()
