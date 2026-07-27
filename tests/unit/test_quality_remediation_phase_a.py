"""Phase A remediation guards (lean default, compact discipline, scoring, deslop)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.ai_flavor_gate import (
    AiFlavorGateConfig,
    AiFlavorGateOutcome,
    needs_deslop_revise,
)
from bestseller.services.anti_ai_voice_discipline import render_compact_writer_discipline
from bestseller.services.prose_prompt_profile import resolve_prose_prompt_profile
from bestseller.services.quality_repair_playbooks import get_quality_repair_playbook
from bestseller.settings import PipelineSettings, load_settings


pytestmark = pytest.mark.unit


def test_pipeline_default_prose_prompt_profile_is_lean() -> None:
    assert PipelineSettings().prose_prompt_profile == "lean"
    settings = load_settings()
    assert getattr(settings.pipeline, "prose_prompt_profile", None) == "lean"


def test_resolve_profile_defaults_to_lean_when_unset() -> None:
    # settings_default lean + no metadata -> lean
    assert resolve_prose_prompt_profile(settings_default="lean") == "lean"
    assert resolve_prose_prompt_profile(explicit="full") == "full"


def test_compact_discipline_is_short_and_has_four_rules() -> None:
    text = render_compact_writer_discipline(language="zh-CN", scope="chapter")
    assert "写作纪律（只有四条）" in text
    assert "只输出正文" in text
    # Ablation winner band: instruction itself should stay compact.
    assert len(text) < 900


def test_dialogue_ai_flavor_playbook_exists() -> None:
    book = get_quality_repair_playbook("DIALOGUE_AI_FLAVOR")
    assert book is not None
    assert "对白" in book.instruction


def test_chapter_too_short_playbook_avoids_sensory_padding() -> None:
    book = get_quality_repair_playbook("CHAPTER_TOO_SHORT")
    assert book is not None
    assert "感官画面" not in book.instruction
    assert "选择" in book.instruction or "阻力" in book.instruction


@pytest.mark.parametrize(
    "code",
    ["CHAPTER_TOO_SHORT", "CHAPTER_BELOW_TARGET", "PERSONA_WEIGHTED_SCORE_LOW"],
)
def test_no_repair_playbook_prescribes_sensory_padding(code: str) -> None:
    """A5 covers every length/score playbook, not just the short-chapter one.

    Any prescription to "增加具体感官画面" re-creates the exact loop P0-2 and
    P1-2 describe: the writer pads with body-reaction vocabulary to raise a
    deterministic score, which is precisely what readers register as AI 味.
    Repair must ask for story movement (选择/阻力/信息差/代价) instead.
    """

    book = get_quality_repair_playbook(code)
    assert book is not None
    assert "感官画面" not in book.instruction, (
        f"{code} still prescribes sensory padding"
    )
    assert any(
        token in book.instruction for token in ("选择", "阻力", "信息差", "代价")
    ), f"{code} must prescribe story movement instead"


def test_needs_deslop_on_warn_band_score() -> None:
    outcome = AiFlavorGateOutcome(
        enabled=True,
        language="zh",
        chapter_number=31,
        before_score=32.0,
        after_score=32.0,
        patched_text=None,
        decision="pass",
        metrics={"warn_threshold": 25, "block_threshold": 38},
    )
    assert needs_deslop_revise(outcome) is True


def test_ai_flavor_config_defaults_gray_start() -> None:
    cfg = AiFlavorGateConfig()
    assert cfg.block_score_cn == 38
    assert cfg.deslop_on_warn is True


def test_style_penalties_hit_zh_flavor_and_verb_repeat() -> None:
    from bestseller.services.reviews import (
        _embodied_verb_repeat_penalty,
        _zh_ai_flavor_penalty,
    )

    clean = "沈渡推开门，卫戎已经坐在桌边。监察员点了点头。"
    flavored = (
        "命运的齿轮悄然转动。他没动，她没出声。空气仿佛凝固。"
        + ("——半寸——" * 12)
    )
    assert _zh_ai_flavor_penalty(flavored) > _zh_ai_flavor_penalty(clean)
    spam = "喉结" * 8 + "半寸" * 8
    assert _embodied_verb_repeat_penalty(spam) > 0.0
    assert _embodied_verb_repeat_penalty(clean) == 0.0
