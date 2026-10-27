"""Ordinary editorial debt stays inside a finite plan; explicit sources survive."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from bestseller.services import planner
from bestseller.services.finite_book_budget import bind_run
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_finite_plan_defers_taste_without_removing_explicit_effect_contracts():
    project = SimpleNamespace(
        id=uuid4(),
        metadata_json={
            "commercial_strict_prewrite": True,
            "creation_intent_contract": {
                "field_sources": {"story_enhancers": "explicit"},
                "story_enhancers": {"effect_skills": ["hype_satisfaction_engine"]},
            },
        },
    )
    settings = load_settings()
    settings.pipeline.outline_commercial_judge_repair_rounds = 3
    original = deepcopy(project.metadata_json)
    assert planner._may_promote_outline_quality_debt(project) is False
    for pid, stage, expected in (
        (project.id, "7:plan", True),
        (uuid4(), "7:plan", False),
        (project.id, "7:review", False),
    ):
        with bind_run(uuid4(), project_id=pid, stage=stage):
            assert planner._may_promote_outline_quality_debt(project) is expected
            assert planner._outline_commercial_repair_round_budget(project, settings) == (
                0 if expected else 3
            )
            assert project.metadata_json == original
    assert not planner._may_promote_outline_quality_debt(project)
