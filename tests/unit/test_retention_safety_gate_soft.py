"""Reader-retention / persona gate soft-block (accept-on-stall).

Regression guard for the "门禁过不了 / 一章写几百遍" loop: when the writer model
exhausts the retention auto-repair budget on a bar it structurally cannot clear
(e.g. PERSONA_WEIGHTED_SCORE_LOW — the 0.62 gate vs the model's ~0.51 ceiling),
the chapter must accept-on-stall and ADVANCE by default instead of hard-routing
to machine-repair and pausing the whole book. Mirrors
``chapter_review_block_on_failure``.
"""

from __future__ import annotations

import pytest

from bestseller.services.drafts import _effective_l6_gate_config
from bestseller.services.pipelines import _retention_gate_blocks_for_project
from bestseller.services.write_gate import GateConfig
from bestseller.settings import load_settings


class _StubProject:
    def __init__(self, metadata: dict | None = None) -> None:
        self.metadata_json = metadata


def _settings(block: bool):
    settings = load_settings(env={})
    settings.pipeline.retention_safety_gate_block_on_failure = block
    return settings


def test_retention_gate_default_is_soft() -> None:
    """Default must be soft (False) so books reach autonomous closure."""
    settings = load_settings(env={})
    assert settings.pipeline.retention_safety_gate_block_on_failure is False
    assert _retention_gate_blocks_for_project(_StubProject(), settings) is False


def test_retention_gate_hard_when_enabled() -> None:
    """Opt-in strict mode hard-blocks."""
    assert _retention_gate_blocks_for_project(_StubProject(), _settings(True)) is True


def test_retention_gate_per_project_warn_only_overrides_hard_default() -> None:
    """A project may opt out of a globally-strict gate via metadata."""
    proj = _StubProject({"retention_safety_gate_warn_only": True})
    assert _retention_gate_blocks_for_project(proj, _settings(True)) is False


@pytest.mark.parametrize("metadata", [None, {}, {"unrelated": 1}])
def test_retention_gate_missing_or_irrelevant_metadata_uses_default(metadata) -> None:
    assert _retention_gate_blocks_for_project(_StubProject(metadata), _settings(False)) is False
    assert _retention_gate_blocks_for_project(_StubProject(metadata), _settings(True)) is True


def test_l6_persona_codes_follow_soft_retention_policy() -> None:
    base = GateConfig(
        mode_by_violation={
            "PERSONA_WEIGHTED_SCORE_LOW": "block",
            "CANON_STATE_REGRESSION": "block",
        }
    )

    effective = _effective_l6_gate_config(
        _StubProject(), base, retention_block_on_failure=False
    )

    assert effective.mode_by_violation["PERSONA_WEIGHTED_SCORE_LOW"] == "audit_only"
    assert effective.mode_by_violation["CANON_STATE_REGRESSION"] == "block"


def test_l6_persona_codes_remain_blocking_in_explicit_strict_mode() -> None:
    base = GateConfig(mode_by_violation={"PERSONA_WEIGHTED_SCORE_LOW": "block"})

    effective = _effective_l6_gate_config(
        _StubProject(), base, retention_block_on_failure=True
    )

    assert effective.mode_by_violation["PERSONA_WEIGHTED_SCORE_LOW"] == "block"
