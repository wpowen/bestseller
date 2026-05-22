from __future__ import annotations

import pytest

from bestseller.domain.signature_scene import (
    SignatureSceneArchetype,
    SignatureSceneMandate,
    SignatureSceneStake,
)
from bestseller.services.retention_safety_gate import (
    SIGNATURE_SCENE_BLOCK_CODE,
    evaluate_retention_safety,
)
from bestseller.services.signature_scene_critic import (
    judge_signature_scene_semantics,
)

pytestmark = pytest.mark.unit


def test_signature_scene_critic_passes_literal_hint() -> None:
    mandate = SignatureSceneMandate(
        chapter_position=1,
        archetype=SignatureSceneArchetype.REVELATION,
        stake=SignatureSceneStake.IDENTITY_TRUTH,
        must_include_line=["镜中人终于说出真名"],
    )

    report = judge_signature_scene_semantics(
        "林渊抬眼，镜中人终于说出真名。", mandate
    )

    assert report.passed
    assert report.confidence >= 0.9


def test_signature_scene_critic_passes_semantic_revelation_without_literal_hint() -> None:
    mandate = SignatureSceneMandate(
        chapter_position=1,
        archetype=SignatureSceneArchetype.REVELATION,
        stake=SignatureSceneStake.IDENTITY_TRUTH,
        summary="旧账身份真相揭开",
        must_include_image=["朱砂镜裂开"],
    )

    report = judge_signature_scene_semantics(
        "他终于看清旧账背后的身份，原来谜底从第一夜就写在账页里。",
        mandate,
    )

    assert report.passed
    assert "原来" in report.matched_markers


def test_signature_scene_critic_fails_flat_scene() -> None:
    mandate = SignatureSceneMandate(
        chapter_position=1,
        archetype=SignatureSceneArchetype.OATH_BOUND,
        stake=SignatureSceneStake.LIFE_DEATH,
        must_include_line=["我以血还账"],
    )

    report = judge_signature_scene_semantics("他喝了一杯水，然后离开房间。", mandate)

    assert not report.passed
    assert report.confidence < 0.5


def test_retention_gate_uses_signature_semantic_fallback() -> None:
    text = (
        "他终于看清旧账背后的身份，真相不是失踪，而是有人替他改了命。"
        "原来第一夜的账页就是谜底。"
    )

    report = evaluate_retention_safety(
        chapter_position=1,
        chapter_text=text,
        skip_hook_echo=True,
        skip_exposition=True,
    )

    assert SIGNATURE_SCENE_BLOCK_CODE not in report.auto_repair_codes
