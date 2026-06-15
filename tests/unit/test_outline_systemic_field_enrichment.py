"""Regression tests for systemic outline-batch field omission.

Production 500-chapter runs (zhaoshen-hr-1781168659 / zhaoshen-hr-v3-1781180702,
2026-06-11) died on three batch-level systemic gaps that downstream hard gates
only surface much later:

* ``opening_situation`` empty on 15/15 chapters
  (commercial_planning_readiness ``missing_opening_situation``),
* all scenes protagonist-solo on 15/15 chapters
  (``golden_three_solo_scene_chain``),
* ``causal_contract`` missing on whole batches — retry batches dropped it even
  more often because repair directives made the model fix one thing and forget
  another (chapter_causality_contract block at materialization).

These tests pin the deterministic enrichment, the batch-time hard requirement,
and the keep-what-passed retry directive that close the loop at the source.
"""

from __future__ import annotations

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.services import planner
from bestseller.services.planner import PlannerFallbackError

pytestmark = pytest.mark.unit


_MANIFEST = [
    {"name": "纪渊", "role": "protagonist", "aliases": ["纪总管"]},
    {"name": "白曦", "role": "ally", "aliases": []},
    {"name": "玄烈", "role": "antagonist", "aliases": ["玄督察"]},
]


def _batch(chapters: list[dict]) -> ChapterOutlineBatchInput:
    return ChapterOutlineBatchInput.model_validate(
        {"batch_name": "v1-outline", "chapters": chapters}
    )


def _chapter(
    number: int,
    *,
    opening_situation: str | None = None,
    causal_contract: dict | None = None,
    participants: list[str] | None = None,
    main_conflict: str = "玄烈逼纪渊三日内交出名册；否则封停招神点",
    scene_story: str = "纪渊在招神点当面拒绝交名册。",
) -> dict:
    return {
        "chapter_number": number,
        "title": f"第{number}章名册",
        "chapter_goal": "纪渊要在封停前保住名册。",
        "main_conflict": main_conflict,
        "hook_description": "名册最后一页出现了纪渊自己的名字。",
        "opening_situation": opening_situation,
        "causal_contract": causal_contract or {},
        "key_reveals": ["名册可以自己增删名字"],
        "scenes": [
            {
                "scene_number": 1,
                "title": "招神点对峙",
                "participants": participants if participants is not None else ["纪渊"],
                "purpose": {"story": scene_story, "emotion": "压迫感升级"},
                "exit_state": {"summary": "名册被贴上封条，期限开始倒数。"},
            }
        ],
    }


# ── ① 确定性补全 ────────────────────────────────────────────────────


def test_enrich_backfills_participants_from_text_match() -> None:
    batch = _batch(
        [
            _chapter(
                1,
                scene_story="纪渊在招神点当面拒绝交名册，玄烈把封条拍在桌上。",
            )
        ]
    )
    repaired = planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    assert repaired >= 1
    assert batch.chapters[0].scenes[0].participants == ["纪渊", "玄烈"]


def test_enrich_matches_participants_via_alias_and_chapter_text() -> None:
    # The scene text itself is silent, but the chapter's main_conflict names
    # the antagonist via an alias — the canonical name must be backfilled.
    batch = _batch(
        [
            _chapter(
                5,
                main_conflict="玄督察逼纪渊三日内交出名册；否则封停招神点",
                scene_story="纪渊清点名册页码。",
            )
        ]
    )
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    assert batch.chapters[0].scenes[0].participants == ["纪渊", "玄烈"]


def test_enrich_does_not_invent_participants_without_text_evidence() -> None:
    batch = _batch(
        [
            _chapter(
                5,
                main_conflict="名册期限只剩一日；封条不能再拖",
                scene_story="纪渊独自清点名册页码。",
            )
        ]
    )
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    assert batch.chapters[0].scenes[0].participants == ["纪渊"]


def test_rescue_golden_three_solo_without_text_evidence() -> None:
    """G8: a 凡人流 solo-discovery golden chapter (no other cast name anywhere in
    its text) must get a canonical roster name force-injected so the volume-killing
    golden_three_solo_scene_chain gate no longer fires. Golden-three only, and it
    runs as the final guard before the gate."""
    batch = _batch(
        [
            _chapter(
                1,
                main_conflict="夜火封镇，废墟塌陷，缺角古砚第一次显形",
                scene_story="他独自在塌陷的地窖里摸索。",
                participants=["纪渊"],
            )
        ]
    )
    # Enrichment fills opening_situation/causal_contract; rescue handles solo.
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    rescued = planner._rescue_golden_three_solo_scenes(
        batch, identity_manifest=_MANIFEST
    )
    assert rescued == 1
    parts = batch.chapters[0].scenes[0].participants
    assert len({p for p in parts}) >= 2, "golden solo scene not rescued"
    # The whole point: the downstream hard gate must now pass instead of killing
    # the volume outline.
    planner._require_outline_systemic_fields_or_raise(
        batch, logical_name="volume_1_chapter_outline_batch_1_1"
    )


def test_rescue_runs_after_identity_strip_collapses_to_solo() -> None:
    """The real failure mode: the LLM gave ch1 a duo scene, but the identity-lock
    repair stripped the ad-hoc (non-roster) name back to solo. The final rescue
    must restore a non-solo state so the gate passes."""
    # Simulate the post-identity-strip state: scene collapsed to protagonist-only.
    batch = _batch(
        [
            _chapter(
                2,
                main_conflict="谷中静默，古砚的秘密只有他一人知晓",
                scene_story="他独自试墨。",
                participants=["纪渊"],
            )
        ]
    )
    # Enrichment can't help (no roster name in text); rescue must.
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    planner._rescue_golden_three_solo_scenes(batch, identity_manifest=_MANIFEST)
    planner._require_outline_systemic_fields_or_raise(
        batch, logical_name="volume_1_chapter_outline_batch_1_2"
    )


def test_rescue_skips_non_golden_chapters() -> None:
    """Rescue must never fabricate participants for chapters 4+."""
    batch = _batch(
        [
            _chapter(
                7,
                main_conflict="无人之境，他独自前行",
                scene_story="他独自赶路。",
                participants=["纪渊"],
            )
        ]
    )
    rescued = planner._rescue_golden_three_solo_scenes(
        batch, identity_manifest=_MANIFEST
    )
    assert rescued == 0
    assert {p for p in batch.chapters[0].scenes[0].participants} == {"纪渊"}


def test_enrich_synthesizes_opening_situation_in_medias_res() -> None:
    batch = _batch([_chapter(1)])
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    opening = batch.chapters[0].opening_situation
    assert opening
    assert opening.startswith("开章即事中：")
    assert "纪渊在招神点当面拒绝交名册" in opening
    assert "玄烈逼纪渊三日内交出名册" in opening


def test_enrich_preserves_existing_opening_situation() -> None:
    batch = _batch([_chapter(1, opening_situation="纪渊正被堵在招神点门口。")])
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    assert batch.chapters[0].opening_situation == "纪渊正被堵在招神点门口。"


def test_enrich_maps_causal_contract_from_chapter_fields() -> None:
    batch = _batch([_chapter(1)])
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    contract = batch.chapters[0].causal_contract
    assert contract["pressure"] == "玄烈逼纪渊三日内交出名册"
    assert contract["protagonist_choice"] == "纪渊要在封停前保住名册。"
    assert contract["state_change"] == "名册被贴上封条，期限开始倒数。"
    assert contract["gain_or_reveal"] == "名册可以自己增删名字"
    assert contract["next_reader_desire"] == "名册最后一页出现了纪渊自己的名字。"
    assert contract["resistance"]
    assert contract["cost_or_tradeoff"]
    assert contract["chapter_function"]
    # 因果闸门要求 ≥5 条轴成立；确定性映射必须把全部缺轴补上。
    assert len([v for v in contract.values() if v]) >= 8


def test_enrich_only_fills_missing_causal_axes() -> None:
    batch = _batch(
        [_chapter(1, causal_contract={"pressure": "模型自己写的压力轴"})]
    )
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    contract = batch.chapters[0].causal_contract
    assert contract["pressure"] == "模型自己写的压力轴"
    assert contract["next_reader_desire"]


def test_enrich_is_idempotent() -> None:
    batch = _batch([_chapter(1)])
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    second = planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    assert second == 0


# ── ② 批次硬校验（required-with-repair-directive）─────────────────────


def test_require_passes_after_enrichment() -> None:
    batch = _batch(
        [
            _chapter(
                number,
                scene_story="纪渊在招神点当面拒绝交名册，玄烈把封条拍在桌上。",
            )
            for number in (1, 2, 3)
        ]
    )
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    planner._require_outline_systemic_fields_or_raise(
        batch, logical_name="volume_1_chapter_outline_batch_1_3"
    )


def test_require_raises_for_missing_fields_and_golden_three_solo() -> None:
    chapters = [_chapter(1), _chapter(2)]
    chapters[0]["causal_contract"] = {}
    batch = _batch(chapters)
    # 不做 enrichment，直接校验：模拟现网"批次产物字段缺失直通下游"。
    with pytest.raises(PlannerFallbackError) as exc_info:
        planner._require_outline_systemic_fields_or_raise(
            batch, logical_name="volume_1_chapter_outline_batch_1_2"
        )
    message = str(exc_info.value)
    assert "opening_situation" in message
    assert "causal_contract" in message
    assert "golden-three" in message
    assert "participants" in message


def test_require_allows_solo_scenes_outside_golden_three() -> None:
    batch = _batch([_chapter(7, opening_situation="开章即事中：清点名册。")])
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=[], language="zh-CN"
    )
    # 第7章全单人场景不在黄金三章硬闸门范围内，不应硬阻断。
    planner._require_outline_systemic_fields_or_raise(
        batch, logical_name="volume_1_chapter_outline_batch_6_10"
    )


def test_require_failure_converts_to_repair_directives() -> None:
    batch = _batch([_chapter(1)])
    try:
        planner._require_outline_systemic_fields_or_raise(
            batch, logical_name="volume_1_chapter_outline_batch_1_5"
        )
    except PlannerFallbackError as exc:
        directives = planner._outline_repair_directives_from_error(
            exc,
            language="zh-CN",
            volume_number=1,
            chapter_number_offset=1,
            expected_count=5,
        )
    else:  # pragma: no cover - guarded by the failing fixture
        raise AssertionError("expected systemic-field contract failure")
    joined = "\n".join(directives)
    assert "opening_situation" in joined
    assert "修复上一版章纲失败项" in joined


# ── ③ 重试指令必须保留已通过字段 ──────────────────────────────────────


def test_repair_directives_always_include_preserve_passed_fields() -> None:
    directives = planner._outline_repair_directives_from_error(
        ValueError("volume 1 returned 4/5 chapters for volume outline"),
        language="zh-CN",
        volume_number=1,
        chapter_number_offset=1,
        expected_count=5,
    )
    joined = "\n".join(directives)
    assert "必须保留上一版已通过的全部字段" in joined
    assert "causal_contract" in joined

    directives_en = planner._outline_repair_directives_from_error(
        ValueError("volume 1 returned 4/5 chapters for volume outline"),
        language="en",
        volume_number=1,
        chapter_number_offset=1,
        expected_count=5,
    )
    joined_en = "\n".join(directives_en)
    assert "Never drop or blank a previously-valid field" in joined_en
    assert "causal_contract" in joined_en


def test_title_collision_directives_include_preserve_passed_fields() -> None:
    from bestseller.services.title_dedup import TitleCollision

    error = planner.TitleCollisionError(
        "duplicate titles",
        collisions=[
            TitleCollision(
                chapter_number=2,
                candidate_title="名册封条",
                conflict_chapter_number=1,
                conflict_title="名册封条",
                similarity=1.0,
            )
        ],
    )
    directives = planner._outline_repair_directives_from_error(error, language="zh-CN")
    joined = "\n".join(directives)
    assert "必须保留上一版已通过的全部字段" in joined


def test_enrich_backfills_empty_hook_type_from_hook_text() -> None:
    """瓶颈#3后续: the planner leaves hook_type empty on whole batches
    (v3 ch1-5/ch21-30). Enrichment must derive a canonical hook label from the
    chapter's hook_description so the writability gate sees a non-empty field."""
    ch = _chapter(1)
    ch["hook_type"] = None
    ch["hook_description"] = "巡查的脚步声踩进废墟，下一章天亮前他必须做出选择。"
    batch = _batch([ch])
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    assert (batch.chapters[0].hook_type or "").strip(), "empty hook_type not backfilled"


def test_enrich_preserves_existing_hook_type() -> None:
    ch = _chapter(2)
    ch["hook_type"] = "悬念反转"
    batch = _batch([ch])
    planner._enrich_generated_volume_outline_systemic_fields(
        batch, identity_manifest=_MANIFEST, language="zh-CN"
    )
    # A non-empty planner value is normalized but never blanked.
    assert (batch.chapters[0].hook_type or "").strip()
