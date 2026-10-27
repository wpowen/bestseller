"""Trace automatic prompts: approved contracts survive, recipe assets do not bind."""
import copy
import json
from types import SimpleNamespace

import pytest

from bestseller.services import (
    chapter_constraint_manifest as manifest_service,
    chapter_llm_quality_judge as chapter_judge,
    methodology_bridge as bridge,
    methodology_compiler,
    outline_reader_experience_judge as reader_judge,
    planner_prompt_helpers,
)
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.methodology_compiler import MethodologyStage
from bestseller.services.prompt_packs import get_prompt_pack
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit
PACKS = ("xuanhuan-power-fantasy", "suspense-mystery", "cozy-fantasy", None)
RECIPES = ("【opening_rules】", "【spring_model】", "【stakes_design】", "三重锚点", "至少2个角色提供反应")


def forbid_assets(*args, **kwargs):
    pytest.fail("automatic consumer read a reference recipe as authority")


def approved_source():
    # Deliberately put the contracts after background beyond both old cutoffs.
    return {
        "optional_background": "background " * 2000,
        "approved_creation_contracts": {"reader_promise": "APPROVED_FIRST_CHAPTER_PAYOFF"},
        "approved_effect_contracts": {"effect": "APPROVED_EFFECT_WITHOUT_TEMPLATE"},
        "chapter_contract": {"target_state_end": {"result": "CURRENT_CHAPTER_OBSERVABLE_RESULT"}},
        "approved_world_authority": {"authority_status": "current", "rule": "APPROVED_CURRENT_ABILITY"},
    }


def assert_boundaries(request):
    text = request.system_prompt + request.user_prompt
    assert "SOURCE AUTHORITY" in text
    assert all(recipe not in text for recipe in RECIPES)
    for marker in ("APPROVED_FIRST_CHAPTER_PAYOFF", "APPROVED_EFFECT_WITHOUT_TEMPLATE",
                   "CURRENT_CHAPTER_OBSERVABLE_RESULT", "APPROVED_CURRENT_ABILITY"):
        assert marker in request.user_prompt
    assert request.metadata["methodology_applicability"] == "approved-contracts-only.v1"


@pytest.mark.parametrize("pack_key", PACKS)
@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_automatic_planner_does_not_compile_taxonomy_or_global_recipes(monkeypatch, pack_key, language):
    monkeypatch.setattr(methodology_compiler, "compile_methodology", forbid_assets)
    monkeypatch.setattr(bridge, "get_fragment", forbid_assets)
    metadata = {"genre_intent_contract": {"prompt_pack_key": pack_key}}
    original_metadata = copy.deepcopy(metadata)
    task = "APPROVED_CURRENT_TASK: execute the existing scene; preserve its result."
    project = SimpleNamespace(language=language, genre="unrelated genre", metadata_json=metadata)
    prompt = planner_prompt_helpers.attach_planner_methodology(
        task, stage=MethodologyStage.OUTLINE_CHAPTER, project_ctx=project, chapter_no=1,
    )
    assert prompt.endswith(task)
    assert "SOURCE AUTHORITY" in prompt
    assert all(recipe not in prompt for recipe in RECIPES)
    assert metadata == original_metadata
    assert planner_prompt_helpers.attach_planner_methodology(
        task, stage=MethodologyStage.OUTLINE_CHAPTER, project_ctx=project, token_budget=0,
    ) == task


@pytest.mark.parametrize("phase", ["planner", "prewrite", "review", "judge"])
def test_automatic_phase_renderer_does_not_read_explicit_asset_api(monkeypatch, phase):
    pack = get_prompt_pack("xianxia-upgrade-core")
    monkeypatch.setattr(bridge, "get_fragment", forbid_assets)
    assert "SOURCE AUTHORITY" in bridge.render_phase_block(pack, phase=phase)


def test_explicit_asset_lookup_remains_available():
    pack = get_prompt_pack("xianxia-upgrade-core")
    assert bridge.get_fragment(pack, phase="prewrite", fragment_key="spring_model")
    assert "opening_rules" in bridge.get_fragments_for_phase(None, phase="planner")


@pytest.mark.parametrize("pack_key", PACKS)
def test_fusion_requires_explicit_true_even_for_loop_taxonomy(pack_key):
    assert bridge.resolve_shuangwen_fusion(enabled_flag=False, pack_key=pack_key) is False
    assert bridge.resolve_shuangwen_fusion(enabled_flag=True, pack_key=pack_key) is True


@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("pack_key", PACKS)
def test_prewrite_keeps_current_contract_without_default_recipe(monkeypatch, language, pack_key):
    manifest = manifest_service.compile_chapter_constraint_manifest(
        chapter_number=1, scene_number=1, participants=["Mira"],
        scene_metadata={"location_name": "Garden"},
        scene_exit_state={"result": "CURRENT_SCENE_PAYOFF"},
    )
    before = manifest.model_dump(mode="json")
    monkeypatch.setattr(bridge, "get_fragment", forbid_assets)
    prompt = manifest_service.render_prewrite_plan_prompt(
        manifest, language=language, chapter_number=1,
        pack=get_prompt_pack(pack_key) if pack_key else None,
    )
    assert "SOURCE AUTHORITY" in prompt
    assert "Mira" in prompt and "Garden" in prompt and "CURRENT_SCENE_PAYOFF" in prompt
    assert "情绪压缩弹簧法" not in prompt and "冲突筹码设计" not in prompt
    assert manifest.model_dump(mode="json") == before
    plan = manifest_service.build_safe_prewrite_plan(manifest)
    assert manifest_service.validate_prewrite_plan(plan, manifest).passed
    plan.characters_to_use = ["Unapproved stranger"]
    assert not manifest_service.validate_prewrite_plan(plan, manifest).passed


@pytest.fixture
def capture_requests(monkeypatch):
    requests = []

    async def capture(session, settings, request):
        requests.append(request)
        return LLMCompletionResult(content=json.dumps({
            "pass": True, "overall_score": 0.99, "dimension_scores": {},
            "blocking_issues": [], "audit_issues": [], "rewrite_plan": {"scope": "chapter"},
        }), provider="mock", model_name="mock-critic")

    monkeypatch.setattr(chapter_judge, "complete_text", capture)
    monkeypatch.setattr(reader_judge, "complete_text", capture)
    monkeypatch.setattr(bridge, "get_fragment", forbid_assets)
    return requests


@pytest.mark.parametrize("pack_key", PACKS)
@pytest.mark.parametrize("language", ["zh-CN", "en"])
async def test_actual_chapter_judge_request_keeps_approved_contracts_only(capture_requests, pack_key, language):
    source = approved_source()
    before = copy.deepcopy(source)
    await chapter_judge.judge_chapter_commercial_quality(
        object(), load_settings(env={}), chapter_number=1, content_md="Mira completes the agreed task.",
        generation_input=source, language=language,
        pack=get_prompt_pack(pack_key) if pack_key else None,
    )
    assert_boundaries(capture_requests[-1])
    assert "强制二元检查项" not in capture_requests[-1].system_prompt
    assert source == before


@pytest.mark.parametrize("pack_key", PACKS)
async def test_actual_reader_judge_keeps_source_contract_past_summary_cutoff(capture_requests, pack_key):
    source = approved_source()
    before = copy.deepcopy(source)
    await reader_judge.judge_outline_reader_experience(
        object(), load_settings(env={}),
        chapters_payload=[{"chapter_number": 1, "goal": "CURRENT_CHAPTER_OBSERVABLE_RESULT"}],
        project_brief=source, pack=get_prompt_pack(pack_key) if pack_key else None,
    )
    assert_boundaries(capture_requests[-1])
    assert "> 3 个具名角色" not in capture_requests[-1].system_prompt
    assert "> 2 个未解释" not in capture_requests[-1].system_prompt
    assert "完全忘掉项目简介内容" not in capture_requests[-1].user_prompt
    assert source == before
