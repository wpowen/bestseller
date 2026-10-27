# ruff: noqa: RUF001
"""Final consumers cannot resurrect unbound cached cost obligations."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
from bestseller.services.entry_system_kernel import (
    _requested_cost_style,
    build_fallback_entry_system_kernel,
    render_entry_system_kernel_prompt_block,
)
from bestseller.services.fanqie_short_planner import (
    build_fanqie_segment_outline_batch,
    build_short_ideology_compact,
    render_short_ideology_scene_block,
)
from bestseller.services.ideology_coherence_gate import (
    audit_ideology_outline_grounding,
    evaluate_ideology_kernel_coherence,
)
from bestseller.services.ideology_judge import (
    _deterministic_penalty,
    build_ideology_judge_system_prompt,
    build_ideology_judge_user_prompt,
    score_ideology_from_judge_json,
)
from bestseller.services.planner import (
    _compact_outline_context_block,
    _entry_system_kernel_prompt_block,
)
from test_ideology_cost_source_projection import author_metadata, cached as legacy_cached


def cached(genre="航海冒险", style="minimal", reverse=False):
    k = legacy_cached(style)
    k["cost_style"] = style
    k["cost_system"][0]["costs"] = "STALE_COST_SENTINEL"
    k["forbidden_resolutions"] = ["审计专属旧禁则"]
    k["motif_to_world_bindings"] = ["STALE_WORLD_SENTINEL"]
    # Put precisely the dangerous fields first, then exercise the opposite order.
    k = {
        key: k[key]
        for key in ("cost_style", "cost_system", "forbidden_resolutions", "motif_to_world_bindings")
    } | k
    return dict(reversed(list(k.items()))) if reverse else k


@pytest.mark.parametrize("genre", ["航海冒险", "田园生活"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("budget", [1600, 16000, 100000])
def test_compressed_outline_never_appends_raw_ideology(genre, reverse, budget):
    kernel = cached(genre, reverse=reverse)
    metadata = {
        "story_design_kernel": {"ideology_kernel": kernel, "reader_promise": "KEEP_OTHER_DESIGN"}
    }
    before = deepcopy(metadata)
    block = _compact_outline_context_block(
        SimpleNamespace(metadata_json=metadata), max_chars=budget
    )
    assert kernel["thesis_statement"] not in block
    assert "STALE_COST_SENTINEL" not in block and "STALE_WORLD_SENTINEL" not in block
    assert "审计专属旧禁则" not in block and "cost_style" not in block
    assert "KEEP_OTHER_DESIGN" in block
    assert metadata == before


@pytest.mark.parametrize(
    "style,marker", [("standard", "标准代价"), ("minimal", "极简代价"), ("external", "外置代价")]
)
def test_compressed_outline_preserves_explicit_choice(style, marker):
    metadata = author_metadata(style)
    metadata["story_design_kernel"] = {"ideology_kernel": cached(style=style)}
    block = _compact_outline_context_block(SimpleNamespace(metadata_json=metadata))
    assert marker in block and "代价来源未确认" not in block
    assert "STALE_WORLD_SENTINEL" not in block
    assert "STALE_COST_SENTINEL" not in block
    assert metadata["story_design_kernel"]["ideology_kernel"]["thesis_statement"] not in block


@pytest.mark.parametrize("genre", ["航海冒险", "田园生活"])
def test_unknown_grounding_cannot_penalize_old_cost_or_forbidden(genre):
    kernel = cached(genre)
    before = deepcopy(kernel)
    grounding = audit_ideology_outline_grounding(kernel, "审计专属旧禁则")
    assert "possible_forbidden_resolution" not in grounding.flagged
    assert "no_cost_language" not in grounding.flagged
    _, flagged = _deterministic_penalty(grounding, SimpleNamespace(penalty_max=10))
    assert "forbidden_resolution" not in flagged
    assert kernel == before


@pytest.mark.parametrize("style", ["standard", "minimal", "external"])
def test_author_bound_forbidden_still_applies_but_missing_cost_only_standard(style):
    k = cached(style=style)
    metadata = author_metadata(style, ideology_fields={
        "forbidden_resolutions": k["forbidden_resolutions"],
        "cost_system": k["cost_system"],
    })
    g = audit_ideology_outline_grounding(k, "审计专属旧禁则", metadata=metadata)
    assert "possible_forbidden_resolution" in g.flagged
    assert ("no_cost_language" in g.flagged) == (style == "standard")
    _, flags = _deterministic_penalty(g, SimpleNamespace(penalty_max=10))
    assert "forbidden_resolution" in flags
    # Cached scalar changes cannot cancel an independently authored prohibition.
    k["cost_style"] = "minimal" if style != "minimal" else "standard"
    assert (
        "possible_forbidden_resolution"
        in audit_ideology_outline_grounding(k, "审计专属旧禁则", metadata=metadata).flagged
    )


def test_judge_recomputes_stale_grounding_for_current_source_without_rewriting_scores():
    k = cached(style="standard")
    old = audit_ideology_outline_grounding(
        k, "审计专属旧禁则", metadata=author_metadata("standard", ideology_fields={"forbidden_resolutions": k["forbidden_resolutions"], "cost_system": k["cost_system"]})
    )
    assert "possible_forbidden_resolution" in old.flagged
    block = build_ideology_judge_user_prompt(kernel=k, outline_text="审计专属旧禁则", grounding=old)
    assert "possible_forbidden_resolution" not in block and "no_cost_language" not in block
    assert k["thesis_statement"] not in block
    raw = json.dumps({"sloganization_penalty": 7.0, "top_issues": ["keep model findings"]})
    before = deepcopy((k, raw, old))
    result = score_ideology_from_judge_json(raw, kernel=k, outline_text="审计专属旧禁则")
    assert result.sloganization_penalty == 7.0  # keep model evidence; no manufactured pass
    assert (k, raw, old) == before


def test_structural_gate_skips_unknown_cost_policies_not_unrelated_failures():
    k = cached()
    k["cost_system"] = k["cost_system"][:1]
    k["forbidden_resolutions"] = []
    k["motif_to_world_bindings"] = []
    k["secondary_motifs"] = []
    unknown = evaluate_ideology_kernel_coherence(k)
    codes = {f.code for f in unknown.findings}
    assert "ideology_secondary_roles_incomplete" not in codes
    assert not codes & {
        "ideology_thin_cost_system",
        "ideology_no_forbidden_resolutions",
        "ideology_no_world_bindings",
    }
    explicit = evaluate_ideology_kernel_coherence(k, metadata=author_metadata(
        ideology_fields={"secondary_motifs": []}, required_secondary_roles=["action", "suspense"]
    ))
    assert "ideology_secondary_roles_incomplete" in {f.code for f in explicit.findings}
    assert unknown.metrics["methodology_source"]["authority"] == "unknown"
    assert unknown.metrics["checks_total"] < explicit.metrics["checks_total"]


@pytest.mark.parametrize("genre", ["航海冒险", "田园生活"])
def test_legacy_short_compact_cannot_reacquire_cost_authority(genre):
    compact = {
        "thesis_statement": genre,
        "cost_style": "standard",
        "cost_laws": [{"acquires": "线索", "costs": "STALE_COST_SENTINEL"}],
        "forbidden": ["STALE_FORBIDDEN"],
    }
    before = deepcopy(compact)
    block = render_short_ideology_scene_block(compact)
    assert block == ""
    # A new author style is valid, but does not bind an old unproven compact.
    block = render_short_ideology_scene_block(compact, metadata=author_metadata("standard"))
    assert "标准代价" in block and "STALE_" not in block
    assert compact == before


@pytest.mark.parametrize(
    "style,marker",
    [("standard", "标准代价"), ("minimal", "极简代价"), ("external", "主角不自损")],
)
def test_current_short_source_can_render_and_stale_source_cannot(style, marker):
    k = cached(style=style)
    metadata = author_metadata(style, ideology_fields={
        "thesis_statement": "修复航海钟需要承认旧日错误。",
        "forbidden_resolutions": ["AUTHOR_FORBIDDEN"],
    }) | {"story_design_kernel": {"ideology_kernel": k}}
    compact = build_short_ideology_compact(k, metadata=metadata)
    before = deepcopy((k, metadata, compact))
    block = render_short_ideology_scene_block(compact, metadata=metadata)
    assert marker in block and "AUTHOR_FORBIDDEN" in block
    assert "审计专属旧禁则" not in block and "STALE_COST_SENTINEL" not in block
    if style != "standard":
        assert "STALE_COST_SENTINEL" not in block and "必付" not in block
    assert (k, metadata, compact) == before
    changed = deepcopy(metadata)
    changed["creation_intent_contract"]["hook_spec"]["ideology_contract"]["fields"] = {
        "thesis_statement": "AUTHOR_REVISED_THEME"
    }
    block = render_short_ideology_scene_block(compact, metadata=changed)
    assert "STALE_COST_SENTINEL" not in block and "审计专属旧禁则" not in block
    assert "AUTHOR_FORBIDDEN" not in block
    assert "AUTHOR_REVISED_THEME" in block


def test_old_short_transition_is_superseded_only_in_projection():
    beat = FanqieShortBeat(
        segment_number=1,
        beat_role="rising",
        purpose="调查花展",
        thesis_vector="代价显形：碎裂后付出代价并摸索新解",
    )
    sheet = FanqieShortBeatSheet(beats=[beat], ideology_compact={"thesis_statement": "坚持调查"})
    project = SimpleNamespace(
        target_chapters=1, target_word_count=2000, title="花展", genre="田园", metadata_json={}
    )
    batch = build_fanqie_segment_outline_batch(project, sheet)
    assert "付出代价" not in batch["chapters"][0]["causal_contract"]["thesis_vector"]
    assert sheet.beats[0].thesis_vector == beat.thesis_vector


@pytest.mark.parametrize("genre", ["仙侠", "悬疑", "言情", "末日"])
@pytest.mark.parametrize(
    "legacy",
    [
        {},
        {"cost_style": "minimal"},
        {"story_enhancers": {"cost_style": "minimal"}},
        {"story_enhancers": {"cost_style": "standard"}},
    ],
)
def test_entry_unknown_is_neither_standard_nor_minimal(genre, legacy):
    project = {"genre": genre, **deepcopy(legacy)}
    assert _requested_cost_style(project) == ""
    kernel = build_fallback_entry_system_kernel(project)
    assert not kernel.cost_model.default_cost_types
    assert "来源未确认" in kernel.cost_model.hard_rule
    assert all(
        "cost" not in field.lower() for item in kernel.taxonomy for field in item.required_fields
    )
    # Even a historical mandatory policy cannot survive final rendering.
    old = kernel.model_copy(
        update={
            "system_promise": "STALE_OLD_PROMISE",
            "cost_model": kernel.cost_model.model_copy(update={"hard_rule": "STALE_OLD_COST_RULE"}),
        }
    )
    before = old.model_dump(mode="json")
    block = render_entry_system_kernel_prompt_block(old)
    assert "来源未确认" in block and "STALE_" not in block
    assert old.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "style,marker",
    [
        ("standard", "都必须支付"),
        ("minimal", "能力使用不强制削弱"),
        ("external", "世界、对手或资源承担"),
    ],
)
def test_entry_author_modes_arrive_at_real_planner_renderer(style, marker):
    metadata = author_metadata(style)
    for carrier in (metadata, {"metadata_json": metadata}, {"metadata": metadata}):
        assert _requested_cost_style(carrier) == style
    old = build_fallback_entry_system_kernel({"genre": "悬疑"})
    metadata["entry_system_kernel"] = old.model_dump(mode="json")
    block = _entry_system_kernel_prompt_block(SimpleNamespace(metadata_json=metadata))
    assert marker in block and "来源未确认" not in block


@pytest.mark.parametrize("style", ["", "minimal", "external", "standard"])
def test_judge_system_rubric_uses_current_author_source(style):
    metadata = author_metadata(style) if style else {"story_enhancers": {"cost_style": "standard"}}
    block = build_ideology_judge_system_prompt(metadata=metadata)
    # Cost mode alone does not impose an ideological daily-cost formula.
    assert "会反复侵入日常的代价" not in block
    assert block
    if not style:
        assert "来源" in block


@pytest.mark.parametrize("reverse", [False, True])
def test_compact_entry_json_also_uses_effective_projection(reverse):
    k = build_fallback_entry_system_kernel({"genre": "科幻"}).model_dump(mode="json")
    k["cost_model"]["hard_rule"] = "STALE_ENTRY_OBLIGATION"
    k["system_promise"] = "STALE_ENTRY_PROMISE"
    if reverse:
        k = dict(reversed(list(k.items())))
    metadata = {"entry_system_kernel": k}
    before = deepcopy(metadata)
    block = _compact_outline_context_block(
        SimpleNamespace(metadata_json=metadata), max_chars=100000
    )
    assert "STALE_ENTRY_" not in block and "来源未确认" in block
    assert metadata == before


def test_mutable_compact_with_copied_hashes_cannot_replace_current_policy():
    k = cached(style="standard")
    metadata = author_metadata("standard", ideology_fields={
        "cost_system": [{"acquires": "公开证据", "costs": "AUTHOR_COST_SENTINEL"}],
        "forbidden_resolutions": ["AUTHOR_FORBIDDEN"],
    }) | {"story_design_kernel": {"ideology_kernel": k}}
    compact = build_short_ideology_compact(k, metadata=metadata)
    compact.setdefault("cost_laws", [{"acquires": "FORGED_ACQUISITION"}])[0]["costs"] = "FORGED_PROJECTION"
    compact["author_fields"]["cost_system"][0]["costs"] = "FORGED_AUTHOR_FIELD"
    compact["thesis_statement"] = "FORGED_THEME"
    compact["forbidden"] = ["FORGED_FORBIDDEN"]
    block = render_short_ideology_scene_block(compact, metadata=metadata)
    assert "FORGED_" not in block and "STALE_COST_SENTINEL" not in block
    assert "AUTHOR_COST_SENTINEL" in block and "AUTHOR_FORBIDDEN" in block


def test_invalid_cached_entry_cannot_break_compact_planning():
    block = _compact_outline_context_block(
        SimpleNamespace(metadata_json={"entry_system_kernel": {"taxonomy": []}})
    )
    assert "taxonomy" not in block


@pytest.mark.parametrize("style", ["standard", "minimal", "external"])
def test_cost_choice_does_not_authorize_cached_ideological_prohibitions(style):
    kernel = cached(style=style)
    metadata = author_metadata(style)
    before = deepcopy((kernel, metadata))
    grounding = audit_ideology_outline_grounding(kernel, "审计专属旧禁则", metadata=metadata)
    assert "possible_forbidden_resolution" not in grounding.flagged
    prompt = build_ideology_judge_user_prompt(kernel=kernel, outline_text="审计专属旧禁则", metadata=metadata)
    assert "STALE_COST_SENTINEL" not in prompt and "STALE_WORLD_SENTINEL" not in prompt
    assert kernel["thesis_statement"] not in prompt
    assert (kernel, metadata) == before
