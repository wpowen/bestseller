from copy import deepcopy

import pytest
from uuid import UUID

from bestseller.services import conception

pytestmark = pytest.mark.unit


def passing_title_feedback():
    return {
        "title_min": 80,
        "ranking_candidate": {
            "metrics": {
                "title_total": 90,
                "title_dimensions": {"hook_power": 4, "graspable_subject": 4},
            }
        },
    }


def test_focus_requires_measured_title_and_no_semantic_conflict():
    feedback = passing_title_feedback()
    report = {"hard_conflicts": [{"source": "deterministic_packaging_length"}]}
    assert conception._can_focus_packaging_repair_on_blurb(report, feedback)
    assert not conception._can_focus_packaging_repair_on_blurb(report, {})
    low = deepcopy(feedback)
    low["ranking_candidate"]["metrics"]["title_dimensions"]["hook_power"] = 1.5
    assert not conception._can_focus_packaging_repair_on_blurb(report, low)
    assert not conception._can_focus_packaging_repair_on_blurb(
        {
            "hard_conflicts": [
                {"classification": "hard_conflict", "fact": "title changed mechanism"}
            ]
        },
        feedback,
    )


@pytest.mark.asyncio
async def test_focus_preserves_passing_title_and_repairs_listing_only(monkeypatch):
    captured = {}
    calls = []

    async def fake(*args, **kwargs):
        captured.update(kwargs)
        calls.append(kwargs["template"])
        return {
            "title_candidates": ["unrequested replacement"],
            "synopsis": "守城人拆桥救人。" * 12,
        }, [UUID("00000000-0000-0000-0000-000000000001")]

    monkeypatch.setattr(conception, "_llm_call_json", fake)
    result, _ = await conception._repair_final_packaging_to_explicit_seed(
        object(),
        object(),
        concept_seed="守城人拆掉自己的房子修桥救人。",
        title="从守城人到渡河人",
        premise="不变的故事",
        synopsis="超长旧简介。",
        report={"hard_conflicts": [{"source": "deterministic_packaging_length"}]},
        quality_feedback=passing_title_feedback(),
        ctx={
            "language": "zh-CN",
            "genre_intent_contract": {
                "tag_source": "user",
                "user_tags": ["群像"],
                "default_tags": ["升级流"],
                "tags": ["升级流", "群像"],
            },
        },
        attempt=2,
        candidate_feedback={
            "synopsis_selection": [
                {
                    "candidate": "待改短候选。",
                    "in_band": True,
                    "failed_axes": ["emotion_charge"],
                    "dimensions": {"emotion_charge": 2.35},
                    "required": {"emotion_charge": 3.0},
                }
            ]
        },
        target_platform="番茄小说",
    )
    assert result["title_candidates"] == ["从守城人到渡河人"]
    assert result["synopsis_candidates"] == ["守城人拆桥救人。" * 12]
    assert calls == ["conception_final_packaging_focused_blurb_repair"]
    assert len(_) == 1
    assert captured["template"] == "conception_final_packaging_focused_blurb_repair"
    assert "守城人拆掉自己的房子修桥救人。" in captured["user_prompt"]
    assert "待改短候选。" in captured["user_prompt"]
    assert "六个" not in captured["user_prompt"]
    assert "群像" in captured["user_prompt"]
    assert "升级流" not in captured["user_prompt"]
    assert '"emotion_charge": 2.35' in captured["user_prompt"]
    assert '"emotion_charge": 3.0' in captured["user_prompt"]


def _focused_kwargs():
    return dict(
        session=object(),
        settings=object(),
        concept_seed="收尸人纪白让死者完成最后一个动作。",
        title="引路尸",
        premise="仙门屠城后，纪白带孩子逃亡。",
        synopsis="旧简介。",
        report={"hard_conflicts": [{"source": "deterministic_packaging_length"}]},
        quality_feedback=passing_title_feedback(),
        ctx={
            "language": "zh-CN",
            "genre_intent_contract": {
                "tag_source": "user",
                "user_tags": ["群像"],
                "default_tags": ["升级流"],
                "genre_label": "东方玄幻",
                "sub_genre_label": "东方玄幻",
                "tone_preference": "悲怆克制",
            },
        },
        attempt=2,
        target_platform="番茄小说",
    )


@pytest.mark.asyncio
async def test_focus_retries_once_when_first_result_is_short(monkeypatch):
    calls = []

    async def fake(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"synopsis": "短简介。"}, [UUID("00000000-0000-0000-0000-000000000011")]
        return {"synopsis": "守城人拆桥救人。" * 12}, [
            UUID("00000000-0000-0000-0000-000000000012")
        ]

    monkeypatch.setattr(conception, "_llm_call_json", fake)
    result, ids = await conception._repair_final_packaging_to_explicit_seed(**_focused_kwargs())

    assert len(calls) == 2
    assert calls[1]["template"] == "conception_final_packaging_focused_blurb_retry"
    assert "首次改稿实测 CJK 字数" in calls[1]["user_prompt"]
    assert "收尸人纪白让死者完成最后一个动作。" in calls[1]["user_prompt"]
    assert "群像" in calls[1]["user_prompt"]
    assert "升级流" not in calls[1]["user_prompt"]
    assert len(ids) == 2
    assert result["synopsis"] == "守城人拆桥救人。" * 12


@pytest.mark.asyncio
async def test_focus_retries_once_then_leaves_still_short_for_audit(monkeypatch):
    calls = []

    async def fake(*_args, **kwargs):
        calls.append(kwargs["template"])
        return {"synopsis": "仍然很短。"}, [UUID(int=20 + len(calls))]

    monkeypatch.setattr(conception, "_llm_call_json", fake)
    result, ids = await conception._repair_final_packaging_to_explicit_seed(**_focused_kwargs())

    assert calls == [
        "conception_final_packaging_focused_blurb_repair",
        "conception_final_packaging_focused_blurb_retry",
    ]
    assert len(ids) == 2
    assert result["synopsis"] == "仍然很短。"


@pytest.mark.asyncio
async def test_focus_does_not_retry_an_in_band_result(monkeypatch):
    calls = []

    async def fake(*_args, **kwargs):
        calls.append(kwargs["template"])
        return {"synopsis": "守城人拆桥救人。" * 12}, [UUID(int=30)]

    monkeypatch.setattr(conception, "_llm_call_json", fake)
    result, ids = await conception._repair_final_packaging_to_explicit_seed(**_focused_kwargs())

    assert calls == ["conception_final_packaging_focused_blurb_repair"]
    assert len(ids) == 1
    assert result["title_candidates"] == ["引路尸"]


@pytest.mark.asyncio
async def test_focus_retries_once_when_first_result_is_overlong(monkeypatch):
    calls = []

    async def fake(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"synopsis": "守城人拆桥救人。" * 20}, [UUID(int=40)]
        return {"synopsis": "守城人拆桥救人。" * 12}, [UUID(int=41)]

    monkeypatch.setattr(conception, "_llm_call_json", fake)
    result, ids = await conception._repair_final_packaging_to_explicit_seed(**_focused_kwargs())

    assert len(calls) == 2
    assert "首次改稿实测 CJK 字数" in calls[1]["user_prompt"]
    assert "目标平台字数带：80-130" in calls[1]["user_prompt"]
    assert len(ids) == 2
    assert result["synopsis"] == "守城人拆桥救人。" * 12
