"""Cached style labels do not acquire author authority through renderer hydration."""
# ruff: noqa: RUF001 -- Exact Chinese renderer output.

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.ideology import (
    ideology_kernel_from_dict,
    render_ideology_compact_block,
    render_ideology_kernel_prompt_block,
)
from bestseller.services.creation_intent_contract import build_creation_intent_contract
from bestseller.services.genre_intent_contract import contract_from_selection
from bestseller.services.ideology_kernel import (
    build_ideology_cost_source_supersession,
    resolve_cached_ideology_cost_source,
)
from bestseller.services.story_enhancers import capture_story_enhancer_input


def author_metadata(style=None, *, ideology_fields=None, required_layers=None, required_secondary_roles=None):
    selection, fields = capture_story_enhancer_input(
        {"cost_style": style} if style is not None else {}
    )
    genre = contract_from_selection({"channel": "male", "genre": "xianxia"}, enhancers=selection)
    hook = {"_story_enhancer_input_sources": fields}
    sources = {"story_enhancers": "explicit" if style is not None else "default"}
    if ideology_fields is not None:
        hook["ideology_contract"] = {
            "version": "ideology-author-contract.v1",
            "fields": deepcopy(ideology_fields),
            **({"required_layers": required_layers} if required_layers is not None else {}),
            **({"required_secondary_roles": required_secondary_roles} if required_secondary_roles is not None else {}),
        }
        sources["hook_spec"] = "explicit"
    contract = build_creation_intent_contract(
        genre, story_enhancers=selection, field_sources=sources, hook_spec=hook,
    )
    return {"creation_intent_contract": contract.model_dump(mode="json")}


def cached(style="standard"):
    kernel = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "legacy_ideology_kernel.json").read_text()
    )
    kernel["cost_style"] = style
    return kernel


@pytest.mark.parametrize("style", ["standard", "external", "minimal"])
@pytest.mark.parametrize("hydrate", [False, True])
@pytest.mark.parametrize(
    "render", [render_ideology_compact_block, render_ideology_kernel_prompt_block]
)
def test_unbound_cache_is_neutral_in_actual_renderers(style, hydrate, render):
    kernel = cached(style)
    before = deepcopy(kernel)
    target = ideology_kernel_from_dict(kernel) if hydrate else kernel
    block = render(target)
    assert block == ""
    assert "不可白给" not in block and "必付「" not in block
    assert "硬规则：一切代价外置" not in block
    assert "不写削弱主角的代价账" not in block
    assert kernel["forbidden_resolutions"][0] not in block
    assert kernel == before
    assert kernel["thesis_statement"] not in block


@pytest.mark.parametrize(
    "style,marker",
    [
        ("standard", "标准代价"),
        ("external", "外置代价"),
        ("minimal", "极简代价"),
    ],
)
def test_current_author_entry_overrides_conflicting_cached_label(style, marker):
    kernel = cached("minimal" if style != "minimal" else "standard")
    metadata = author_metadata(style)
    metadata["story_enhancers"] = {"cost_style": "external"}
    before = deepcopy((kernel, metadata))
    block = render_ideology_kernel_prompt_block(kernel, metadata=metadata)
    assert marker in block and "代价来源未确认" not in block
    for value in (kernel["thesis_statement"], kernel["cosmic_premise"],
                  kernel["cost_system"][0]["costs"], kernel["forbidden_resolutions"][0]):
        assert value not in block
    receipt = resolve_cached_ideology_cost_source(kernel, metadata)
    assert receipt["effective_cost_style"] == style
    assert receipt["cached_policy_superseded"]
    assert (kernel, metadata) == before


def test_unknown_cached_style_cannot_claim_authority_by_self_label():
    kernel = cached("minimal")
    kernel["source"] = "author"
    kernel["approved"] = True
    metadata = {
        "story_enhancers": {"cost_style": "minimal", "source": "user"},
        "canon": ["the bell is cracked"],
        "overrides": [{"id": "preserve"}],
    }
    before = deepcopy((kernel, metadata))
    receipt = resolve_cached_ideology_cost_source(kernel, metadata)
    assert receipt["authority"] == "unknown" and receipt["effective_cost_style"] == ""
    assert "不可白给" not in render_ideology_kernel_prompt_block(kernel, metadata=metadata)
    assert (kernel, metadata) == before


def dry_run_records():
    project = SimpleNamespace(
        id=uuid4(),
        metadata_json={"canon": ["the bell is cracked"], "overrides": [{"id": "preserve"}]},
    )
    artifact = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        status="approved",
        artifact_type="story_design_kernel",
        content={"reader_promise": "repair the harbor bell", "ideology_kernel": cached("minimal")},
    )
    proof = resolve_cached_ideology_cost_source(
        artifact.content["ideology_kernel"], project.metadata_json
    )
    hashes = {
        "expected_source_sha256": proof["source_kernel_sha256"],
        "expected_creation_sha256": proof["source_creation_sha256"],
    }
    return project, artifact, hashes


def test_operator_dry_run_is_bound_repeatable_and_retains_all_history():
    project, artifact, hashes = dry_run_records()
    before = deepcopy((vars(project), vars(artifact)))
    first = build_ideology_cost_source_supersession(project, artifact, **hashes)
    second = build_ideology_cost_source_supersession(project, artifact, **hashes)
    assert first == second
    assert first["source_artifact_id"] == str(artifact.id)
    assert first["project_id"] == str(project.id)
    assert first["dry_run"] and first["writes"] == []
    assert first["cached_policy_superseded"] and first["effective_cost_style"] == ""
    assert (vars(project), vars(artifact)) == before


@pytest.mark.parametrize(
    "mutation",
    ["foreign_project", "stale_kernel", "new_author_entry", "unapproved", "foreign_type"],
)
def test_operator_refuses_wrong_or_stale_source_binding(mutation):
    project, artifact, hashes = dry_run_records()
    if mutation == "foreign_project":
        artifact.project_id = uuid4()
    elif mutation == "stale_kernel":
        artifact.content["ideology_kernel"]["thesis_statement"] += "changed"
    elif mutation == "new_author_entry":
        project.metadata_json.update(author_metadata("standard"))
    elif mutation == "unapproved":
        artifact.status = "candidate"
    elif mutation == "foreign_type":
        artifact.artifact_type = "world_spec"
    before = deepcopy((vars(project), vars(artifact)))
    with pytest.raises(ValueError):
        build_ideology_cost_source_supersession(project, artifact, **hashes)
    assert (vars(project), vars(artifact)) == before


def test_dry_run_operator_uses_only_bound_local_capture(tmp_path):
    import json
    from pathlib import Path
    import subprocess
    import sys

    project, artifact, hashes = dry_run_records()
    capture = tmp_path / "ideology_cost_source_test.json"
    capture.write_text(
        json.dumps({"project": vars(project), "artifact": vars(artifact)}, default=str)
    )
    before = capture.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            str(Path("scripts/ideology_cost_source_dry_run.py")),
            str(capture),
            "--expected-kernel-sha256",
            hashes["expected_source_sha256"],
            "--expected-creation-sha256",
            hashes["expected_creation_sha256"],
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    receipt = json.loads(result.stdout)
    assert receipt["dry_run"] and receipt["writes"] == []
    assert receipt["source_artifact_id"] == str(artifact.id)
    assert receipt["source_kernel_sha256"] == hashes["expected_source_sha256"]
    assert capture.read_bytes() == before


def test_old_approved_artifact_cannot_supersede_a_new_current_cache():
    project, artifact, hashes = dry_run_records()
    current = deepcopy(artifact.content)
    current["ideology_kernel"]["thesis_statement"] += "current revision"
    project.metadata_json["story_design_kernel"] = current
    before = deepcopy((vars(project), vars(artifact)))
    with pytest.raises(ValueError, match="current cached kernel"):
        build_ideology_cost_source_supersession(project, artifact, **hashes)
    assert (vars(project), vars(artifact)) == before


def test_matching_current_cache_is_explicit_in_dry_run_receipt():
    project, artifact, hashes = dry_run_records()
    project.metadata_json["story_design_kernel"] = deepcopy(artifact.content)
    report = build_ideology_cost_source_supersession(project, artifact, **hashes)
    assert report["cache_binding"] == "current_cache_match"
    assert report["writes"] == []


@pytest.mark.parametrize("render", [render_ideology_compact_block, render_ideology_kernel_prompt_block])
def test_current_author_theme_survives_without_cached_motifs_or_cost_authority(render):
    kernel = cached()
    fields = {"thesis_statement": "修复信任需要坦诚承认自己的错误。", "core_question": "修钟人愿意公开哪段失误？"}
    metadata = author_metadata(ideology_fields=fields)
    before = deepcopy((kernel, metadata))
    block = render(kernel, metadata=metadata)
    assert all(value in block for value in fields.values())
    for value in (kernel["thesis_statement"], kernel["cosmic_premise"],
                  kernel["primary_motif"]["display_name"], kernel["forbidden_resolutions"][0]):
        assert value not in block
    assert (kernel, metadata) == before


@pytest.mark.parametrize("source", ["default", "derived", "legacy"])
def test_hook_contract_requires_explicit_canonical_field_source(source):
    kernel = cached()
    metadata = author_metadata(ideology_fields={"thesis_statement": "UNTRUSTED_THEME"})
    metadata["creation_intent_contract"]["field_sources"]["hook_spec"] = source
    assert render_ideology_kernel_prompt_block(kernel, metadata=metadata) == ""


@pytest.mark.parametrize("render", [render_ideology_compact_block, render_ideology_kernel_prompt_block])
def test_self_labelled_cache_cannot_supply_an_author_ideology_contract(render):
    kernel = cached()
    forged = {"version": "ideology-author-contract.v1", "fields": {"thesis_statement": "FORGED_THEME"}}
    kernel.update(source="author", approved=True, ideology_contract=forged)
    metadata = {"hook_spec": {"ideology_contract": forged}, "field_sources": {"hook_spec": "explicit"}}
    assert render(kernel, metadata=metadata) == ""


def test_ideology_contract_cannot_smuggle_cost_style_without_separate_cost_entry():
    kernel = cached("minimal")
    metadata = author_metadata(ideology_fields={"thesis_statement": "公开失误才能重建信任。", "cost_style": "minimal"})
    with pytest.raises(ValueError, match="field scope"):
        render_ideology_kernel_prompt_block(kernel, metadata=metadata)


@pytest.mark.parametrize("hydrate", [False, True])
def test_methodology_projection_binds_current_cache_and_preserves_exact_author_subset(hydrate):
    from bestseller.services.ideology_methodology import resolve_ideology_methodology_source

    kernel = cached()
    fields = {"thesis_statement": "修钟人选择公开自己的失误。"}
    metadata = author_metadata(ideology_fields=fields)
    metadata["story_design_kernel"] = {"ideology_kernel": deepcopy(kernel)}
    target = ideology_kernel_from_dict(kernel) if hydrate else kernel
    before = deepcopy((kernel, metadata))
    source = resolve_ideology_methodology_source(target, metadata)
    assert source["effective_fields"] == fields
    assert source["required_layers"] == source["required_secondary_roles"] == []
    assert source["cache_binding"] == "current_cache_match"
    assert (kernel, metadata) == before
    metadata["story_design_kernel"]["ideology_kernel"]["core_question"] += "CURRENT_REVISION"
    changed = deepcopy((kernel, metadata))
    with pytest.raises(ValueError, match="current exact cache"):
        render_ideology_kernel_prompt_block(target, metadata=metadata)
    assert (kernel, metadata) == changed


def test_revised_author_theme_replaces_projection_without_rewriting_cache():
    kernel = cached()
    original = deepcopy(kernel)
    metadata = author_metadata(ideology_fields={"thesis_statement": "AUTHOR_THEME_A"})
    metadata["story_design_kernel"] = {"ideology_kernel": kernel}
    assert "AUTHOR_THEME_A" in render_ideology_compact_block(kernel, metadata=metadata)
    metadata["creation_intent_contract"]["hook_spec"]["ideology_contract"]["fields"] = {
        "thesis_statement": "AUTHOR_THEME_B"
    }
    block = render_ideology_compact_block(kernel, metadata=metadata)
    assert "AUTHOR_THEME_B" in block and "AUTHOR_THEME_A" not in block
    assert kernel == original


@pytest.mark.parametrize("mutation", ["version", "field_alias", "layers", "roles", "extra"])
def test_malformed_explicit_methodology_contract_fails_closed(mutation):
    kernel = cached()
    metadata = author_metadata(ideology_fields={"thesis_statement": "AUTHOR_SELECTED_THEME"})
    contract = metadata["creation_intent_contract"]["hook_spec"]["ideology_contract"]
    if mutation == "version":
        contract["version"] = "unknown.v9"
    elif mutation == "field_alias":
        contract["fields"]["theme"] = "UNTRUSTED_ALIAS"
    elif mutation == "layers":
        contract["required_layers"] = ["invented_layer"]
    elif mutation == "roles":
        contract["required_secondary_roles"] = ["invented_role"]
    else:
        contract["approved"] = True
    before = deepcopy((kernel, metadata))
    with pytest.raises(ValueError):
        render_ideology_kernel_prompt_block(kernel, metadata=metadata)
    assert (kernel, metadata) == before


@pytest.mark.parametrize("render", [render_ideology_compact_block, render_ideology_kernel_prompt_block])
def test_selected_layers_and_roles_remain_requirements_without_synthesizing_motifs(render):
    kernel = cached()
    metadata = author_metadata(
        ideology_fields={}, required_layers=["subject_choice"], required_secondary_roles=["action"]
    )
    block = render(kernel, metadata=metadata)
    assert "subject_choice" in block and "action" in block
    assert kernel["primary_motif"]["display_name"] not in block
    assert kernel["hidden_endgame_motif"]["display_name"] not in block
