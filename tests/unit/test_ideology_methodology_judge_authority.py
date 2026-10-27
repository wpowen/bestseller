# ruff: noqa: RUF001

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.ideology import ideology_kernel_from_dict
from bestseller.services.creation_intent_contract import build_creation_intent_contract
from bestseller.services.genre_intent_contract import contract_from_selection
from bestseller.services.ideology_coherence_gate import (
    audit_ideology_outline_grounding,
    evaluate_ideology_kernel_coherence,
)
from bestseller.services.ideology_judge import (
    build_ideology_judge_system_prompt,
    build_ideology_judge_user_prompt,
    load_ideology_judge_config,
    score_ideology_from_judge_json,
)
from bestseller.services.story_enhancers import capture_story_enhancer_input

pytestmark = pytest.mark.unit


def _legacy():
    return json.loads(
        (Path(__file__).parents[1] / "fixtures" / "legacy_ideology_kernel.json").read_text()
    )


def _author(fields=None, *, layers=(), roles=(), cost=None):
    selection, cost_sources = capture_story_enhancer_input({"cost_style": cost})
    hook = {"_story_enhancer_input_sources": cost_sources}
    if fields is not None or layers or roles:
        hook["ideology_contract"] = {
            "version": "ideology-author-contract.v1", "fields": fields or {},
            "required_layers": list(layers), "required_secondary_roles": list(roles),
        }
    contract = build_creation_intent_contract(
        contract_from_selection({"genre": "urban"}),
        story_enhancers=selection, hook_spec=hook,
        field_sources={
            "hook_spec": "explicit" if "ideology_contract" in hook else "default",
            "story_enhancers": "explicit" if cost is not None else "default",
        },
    )
    return {"creation_intent_contract": contract.model_dump(mode="json")}


def _motif(role=None):
    return {
        "motif_key": "custom", "display_name": "shared craft", "layer": "subject_choice",
        "book_thesis": "修钟的人共同决定钟声。", "book_core_question": "谁来决定钟声?",
        "concrete_symbols": ["旧钟锤"], **({"role": role} if role else {}),
    }


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("kernel", [{}, {"garbage": True}, None])
def test_unknown_methodology_is_not_run_even_when_required(kernel, required):
    verdict = evaluate_ideology_kernel_coherence(kernel, required=required)
    assert verdict.verdict == "not_run"
    assert not verdict.passed and not verdict.findings
    assert verdict.metrics["checks_total"] == verdict.metrics["checks_passed"] == 0
    assert verdict.metrics["methodology_source"]["authority"] == "unknown"


@pytest.mark.parametrize("cost", [None, "standard", "minimal", "external"])
def test_cost_only_is_not_theme_authority(cost):
    kernel, metadata = _legacy(), _author(cost=cost)
    before = deepcopy((kernel, metadata))
    verdict = evaluate_ideology_kernel_coherence(kernel, metadata=metadata, required=True)
    grounding = audit_ideology_outline_grounding(kernel, "大家修好了港口的钟。", metadata=metadata)
    assert verdict.verdict == "not_run"
    assert not verdict.findings and verdict.metrics["checks_total"] == 0
    assert set(grounding.flagged) <= {"no_cost_language"}
    assert grounding.symbol_total == grounding.thesis_keyword_hits == 0
    assert grounding.forbidden_violation_hits == ()
    assert (kernel, metadata) == before


def test_custom_noncosmic_author_subset_passes_without_extra_methods():
    fields = {"thesis_statement": "共同实践让分歧变成可以讨论的选择。", "primary_motif": _motif()}
    verdict = evaluate_ideology_kernel_coherence(fields, metadata=_author(fields), required=True)
    assert verdict.passed and verdict.coverage == 1
    assert verdict.metrics["checks_total"] == 2
    assert verdict.findings == ()


@pytest.mark.parametrize(
    "supplied,code",
    [({}, "ideology_author_field_missing"),
     ({"thesis_statement": "another thesis"}, "ideology_author_field_mismatch")],
)
def test_exact_author_field_must_be_present_and_match(supplied, code):
    metadata = _author({"thesis_statement": "共同修钟。"})
    result = evaluate_ideology_kernel_coherence(supplied, metadata=metadata, required=True)
    assert result.verdict == "blocked"
    assert {finding.code for finding in result.findings} == {code}


def test_only_author_requested_layers_and_roles_are_checked():
    metadata = _author({}, layers=["subject_choice"], roles=["action"])
    missing = evaluate_ideology_kernel_coherence({}, metadata=metadata, required=True)
    assert {finding.code for finding in missing.findings} == {
        "ideology_missing_layer", "ideology_secondary_roles_incomplete",
    }
    supplied = {"secondary_motifs": [_motif("action")]}
    satisfied = evaluate_ideology_kernel_coherence(supplied, metadata=metadata, required=True)
    assert satisfied.passed and satisfied.metrics["checks_total"] == 2


def test_grounding_uses_exact_author_symbols_and_ignores_cached_forbidden():
    kernel = _legacy()
    fields = {"primary_motif": _motif(), "thesis_statement": "共同修钟。"}
    metadata = _author(fields)
    outline = "旧钟锤重新响起。共同修钟。" + " ".join(kernel["forbidden_resolutions"])
    observation = audit_ideology_outline_grounding(kernel, outline, metadata=metadata)
    assert observation.symbol_total == observation.symbol_hits == 1
    assert not observation.forbidden_violation_hits
    assert not observation.flagged


def test_exact_author_forbidden_resolution_retains_grounding_penalty():
    fields = {"forbidden_resolutions": ["毁掉共同修钟的约定"]}
    metadata = _author(fields)
    grounding = audit_ideology_outline_grounding(fields, "毁掉共同修钟的约定", metadata=metadata)
    assert "possible_forbidden_resolution" in grounding.flagged


def test_unchosen_legacy_forbidden_cannot_raise_model_scores_or_penalty():
    kernel = _legacy()
    before = deepcopy(kernel)
    config = load_ideology_judge_config()
    raw = {dim.key: dim.max // 2 for dim in config.dimensions}
    raw.update(sloganization_penalty=5, evidence=["model evidence"],
               top_issues=["model issue"], revision_priority=["model revision"])
    raw_json = json.dumps(raw)
    result = score_ideology_from_judge_json(
        raw_json, kernel=kernel, outline_text=" ".join(kernel["forbidden_resolutions"]),
    )
    assert result.dimension_scores == {dim.key: raw[dim.key] for dim in config.dimensions}
    assert result.sloganization_penalty == 5
    assert result.base_score == sum(result.dimension_scores.values())
    assert result.final_score == result.base_score - 5
    assert result.top_issues == ("model issue",)
    assert result.revision_priority == ("model revision",)
    assert result.raw_excerpt == raw_json and not result.penalty_flagged
    assert kernel == before


@pytest.mark.parametrize("entrypoint", [evaluate_ideology_kernel_coherence,
                                      audit_ideology_outline_grounding,
                                      build_ideology_judge_user_prompt,
                                      score_ideology_from_judge_json])
def test_current_cache_hash_mismatch_rejects_stale_argument(entrypoint):
    old = _legacy()
    current = {**old, "thesis_statement": "new current thesis"}
    metadata = {"story_design_kernel": {"ideology_kernel": current}}
    with pytest.raises(ValueError, match="current exact cache"):
        if entrypoint is evaluate_ideology_kernel_coherence:
            entrypoint(old, metadata=metadata)
        elif entrypoint is audit_ideology_outline_grounding:
            entrypoint(old, "outline", metadata=metadata)
        elif entrypoint is score_ideology_from_judge_json:
            entrypoint("{}", kernel=old, outline_text="outline", metadata=metadata)
        else:
            entrypoint(kernel=old, outline_text="outline", metadata=metadata)


@pytest.mark.parametrize("change", ["version", "field", "layers", "roles", "shape", "payload"])
def test_malformed_explicit_author_contract_fails(change):
    metadata = _author({"thesis_statement": "共同修钟。"})
    contract = metadata["creation_intent_contract"]["hook_spec"]["ideology_contract"]
    if change == "version":
        contract["version"] = "old"
    elif change == "field":
        contract["fields"]["cost_style"] = "standard"
    elif change == "layers":
        contract["required_layers"] = ["not-a-layer"]
    elif change == "roles":
        contract["required_secondary_roles"] = ["not-a-role"]
    elif change == "shape":
        contract["fields"] = []
    else:
        contract["fields"] = {"primary_motif": "invalid"}
    with pytest.raises(ValueError):
        evaluate_ideology_kernel_coherence({}, metadata=metadata)
    with pytest.raises(ValueError):
        score_ideology_from_judge_json("{}", kernel=None, outline_text="", metadata=metadata)


def test_source_aware_prompts_preserve_dimension_keys_and_bounds():
    kernel = _legacy()
    metadata = _author({"thesis_statement": "共同修钟。"}, cost="standard")
    system = build_ideology_judge_system_prompt(kernel=kernel, metadata=metadata)
    user = build_ideology_judge_user_prompt(
        kernel=kernel, outline_text="钟声响起", metadata=metadata,
    )
    for dim in load_ideology_judge_config().dimensions:
        assert f"{dim.key}（0-{dim.max}）" in system
    assert "共同修钟。" in user
    for old in (kernel["cosmic_premise"], kernel["belief_arc"]["final_reconstruction"],
                kernel["forbidden_resolutions"][0]):
        assert old not in system + user
    assert "不自动补分" in system


def test_prompt_drops_stale_precomputed_theme_flags():
    old = _legacy()
    grounding = audit_ideology_outline_grounding(
        old, "", metadata=_author({"thesis_statement": old["thesis_statement"]}),
    )
    assert "thesis_absent_from_outline" in grounding.flagged
    prompt = build_ideology_judge_user_prompt(kernel=old, outline_text="", grounding=grounding)
    assert "thesis_absent_from_outline" not in prompt


def test_hydrated_current_cache_retains_exact_binding():
    raw = _legacy()
    hydrated = ideology_kernel_from_dict(raw)
    verdict = evaluate_ideology_kernel_coherence(
        hydrated, metadata={"ideology_kernel": raw}, required=True,
    )
    assert verdict.verdict == "not_run"
    assert verdict.metrics["methodology_source"]["cache_binding"] == "current_cache_match"


@pytest.mark.asyncio
async def test_mocked_judge_keeps_actual_model_scores_and_receipt(monkeypatch):
    from bestseller.services import ideology_judge

    config = load_ideology_judge_config()
    payload = {dim.key: 2 for dim in config.dimensions}
    payload["sloganization_penalty"] = 4
    run_id = uuid4()
    requests = []

    async def fake_complete(session, settings, request):
        requests.append(request)
        return SimpleNamespace(content=json.dumps(payload), llm_run_id=run_id)

    monkeypatch.setattr(ideology_judge, "complete_text", fake_complete)
    result = await ideology_judge.judge_outline_ideology(
        None, SimpleNamespace(), kernel=_legacy(), outline_text="钟声响起。",
        genre_context=SimpleNamespace(display_genre="custom"),
    )
    assert len(requests) == 1
    assert requests[0].prompt_version == "methodology-source.v2"
    assert result.dimension_scores == {dim.key: 2 for dim in config.dimensions}
    assert result.sloganization_penalty == 4 and result.final_score == 14
    assert result.llm_run_id == str(run_id)
