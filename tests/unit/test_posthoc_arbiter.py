"""The observer gathers evidence; it must not smuggle in judgement."""

from __future__ import annotations

import pytest

from bestseller.services.posthoc_arbiter import (
    ArbiterMode,
    BeatVerdict,
    arbiter_mode,
    observe_chapter,
)


@pytest.mark.unit
def test_a_delivered_beat_is_marked_landed_with_evidence() -> None:
    observation = observe_chapter(
        chapter_number=1,
        contract={"chapter_goal": "陈默在废弃厂房发现了铜钥匙"},
        prose=(
            "他推开门。灰尘在光柱里翻涌。陈默在废弃厂房发现了铜钥匙，"
            "指腹压过齿口，凉得发涩。"
        ),
    )

    (beat,) = observation.beats
    assert beat.verdict is BeatVerdict.LANDED
    assert beat.evidence_excerpt, "a verdict without evidence cannot be audited"
    assert observation.landing_rate == 1.0


@pytest.mark.unit
def test_an_absent_beat_is_marked_missed() -> None:
    observation = observe_chapter(
        chapter_number=2,
        contract={"chapter_goal": "陈默在废弃厂房发现了铜钥匙"},
        prose="她把账本推过桌面，窗外的雨没有停。整晚谁也没有提起那笔钱。",
    )

    (beat,) = observation.beats
    assert beat.verdict is BeatVerdict.MISSED
    assert observation.landing_rate == 0.0


@pytest.mark.unit
def test_partial_evidence_is_reported_as_weak_not_resolved_by_guessing() -> None:
    observation = observe_chapter(
        chapter_number=3,
        contract={"chapter_goal": "陈默在废弃厂房发现了铜钥匙并交给老刘保管"},
        prose="陈默在废弃厂房停了很久，风从破窗灌进来。",
    )

    (beat,) = observation.beats
    assert beat.verdict is BeatVerdict.WEAK
    assert 0.0 < beat.evidence_ratio < 1.0


@pytest.mark.unit
def test_every_contract_slot_becomes_its_own_beat() -> None:
    observation = observe_chapter(
        chapter_number=4,
        contract={
            "chapter_goal": "目标文本内容在这里出现",
            "main_conflict": "冲突文本内容在这里出现",
            "hook_description": "钩子文本内容在这里出现",
            "information_revealed": ["第一条信息揭示", "第二条信息揭示"],
        },
        prose="无关的正文。",
    )

    assert [b.beat.field for b in observation.beats] == [
        "chapter_goal",
        "main_conflict",
        "hook_description",
        "information_revealed[0]",
        "information_revealed[1]",
    ]


@pytest.mark.unit
def test_punctuation_and_whitespace_do_not_hide_a_landed_beat() -> None:
    observation = observe_chapter(
        chapter_number=5,
        contract={"chapter_goal": "他签下了那份合同"},
        prose="他，签下了……那份\n合同。",
    )

    (beat,) = observation.beats
    assert beat.verdict is BeatVerdict.LANDED


@pytest.mark.unit
def test_empty_prose_or_contract_yields_no_beats_rather_than_false_misses() -> None:
    assert observe_chapter(chapter_number=6, contract={}, prose="正文").beats == ()
    assert (
        observe_chapter(
            chapter_number=6, contract={"chapter_goal": "目标"}, prose=""
        ).beats
        == ()
    )


@pytest.mark.unit
def test_landing_rate_is_none_when_nothing_was_measurable() -> None:
    """None means "not measured"; 0.0 would falsely read as total failure."""

    assert observe_chapter(chapter_number=7, contract={}, prose="").landing_rate is None


@pytest.mark.unit
def test_observation_payload_is_auditable() -> None:
    payload = observe_chapter(
        chapter_number=8,
        contract={"chapter_goal": "陈默在废弃厂房发现了铜钥匙"},
        prose="陈默在废弃厂房发现了铜钥匙。",
    ).to_payload()

    assert payload["chapter_number"] == 8
    assert payload["beats"][0]["verdict"] == "landed"
    assert payload["beats"][0]["planned"]


@pytest.mark.unit
def test_arbiter_is_inert_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Merging this module must not silently add work to a book run."""

    monkeypatch.delenv("BESTSELLER_POSTHOC_ARBITER_MODE", raising=False)
    assert arbiter_mode() is ArbiterMode.OFF

    monkeypatch.setenv("BESTSELLER_POSTHOC_ARBITER_MODE", "shadow")
    assert arbiter_mode() is ArbiterMode.SHADOW

    monkeypatch.setenv("BESTSELLER_POSTHOC_ARBITER_MODE", "enforce")
    assert arbiter_mode() is ArbiterMode.OFF, "unknown modes must not enable anything"
