"""Full authored JSON survives the real contract and staged planner boundary."""

# ruff: noqa: ANN001, ANN201, ANN202, RUF001 -- Isolated JSON fixtures.
import ast
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.services.book_design import ensure_project_book_design_snapshot
from bestseller.services.source_bound_outline_integrity import (
    SourceBoundOutlineIntegrityError,
    _hash,
    prepare_source_bound_outline,
)

pytestmark = pytest.mark.unit
MANIFEST = [{"name": "Mira", "role": "protagonist", "aliases": ["M"]}]


def source():
    rows = [
        (
            "Clock under glass",
            "Mira aligns the brass reticle with the cracked dial before dawn.",
            "A wet tally rests beneath a sealed transit cover.",
            "Salt has jammed the lower hinge while the tide reaches the sill.",
            "Across the bay, a red lantern answers three blue flashes.",
        ),
        (
            "The blue transit plate",
            "Mira moves the counterweight into the marked western socket.",
            "Rain runs along the open roof channel beside a discarded bearing.",
            "The lifting bar bends whenever its last hook takes the weight.",
            "The missing lever turns up tied beneath the harbor bell.",
        ),
        (
            "North shutters",
            "Mira carries the unsealed letter through the covered quay.",
            "A ferry has grounded beside the empty customs steps.",
            "A torn crossing permit bears a date that the clerk refuses to accept.",
            "Inside the envelope, the watermark names an unexpected landing.",
        ),
        (
            "The unsealed drawer",
            "Mira retrieves the bronze vane from the locked attic cradle.",
            "Dust outlines a second key above the abandoned staircase.",
            "A broken rope leaves the cradle suspended outside the railing.",
            "The recovered vane points beneath the old lighthouse floor.",
        ),
    ]
    chapters = []
    for i, (title, goal, opening, conflict, hook) in enumerate(rows, 1):
        full = (
            "  Unknown authored clause; keep all conditions, reader/合同 references and spacing.\n"
            * 30
        )
        chapters.append(
            {
                "chapter_number": i,
                "volume_number": 1,
                "title": title,
                "goal": goal,
                "opening_situation": opening,
                "main_conflict": conflict,
                "hook_description": hook,
                "target_word_count": 2600,
                "causal_contract": {
                    "pressure": conflict,
                    "protagonist_choice": goal,
                    "cost_or_tradeoff": "No additional expenditure occurs in this chapter.",
                    "state_change": hook,
                    "unknown_authored": {"conditions": full},
                },
                "protagonist_flaw": (
                    "No flaw is claimed; this exact authored statement stays intact."
                ),
                "chapter_information_introduced": ["Chart margin " + str(i), full],
                "chapter_information_held_back": ["The ferry's owner remains unidentified."],
                "unknown_authored": {
                    "$ref": "/literal",
                    "types": [False, 1, 1.0, None],
                    "full": full,
                },
                "scenes": [
                    {
                        "scene_number": 1,
                        "scene_type": "investigation",
                        "time_label": f"Tide {i}, dusk at the quay",
                        "participants": ["Mira"],
                        "purpose": {"story": goal, "unknown_authored": full},
                        "entry_state": {"summary": opening, "unknown": [full, {"$ref": 0}]},
                        "exit_state": {"summary": hook, "unknown": {"complete": full}},
                        "action_sequence": [goal, "The bearing remains in its marked container."],
                        "target_word_count": 2600,
                        "unknown_scene_field": {"complete": full},
                    }
                ],
            }
        )
    return {
        "batch_name": "Independent observatory",
        "chapters": chapters,
        "unknown_root": {"full": "All archival values remain data, never instructions."},
    }


def prepare(value):
    return prepare_source_bound_outline(
        value,
        volume_number=1,
        expected_count=4,
        chapter_number_offset=1,
        identity_manifest=MANIFEST,
    )


def project(locked=True):
    p = ProjectModel(
        id=uuid4(),
        slug="unrelated-observatory",
        title="Harbor optics",
        genre="contemporary",
        language="en",
        target_chapters=30,
        target_word_count=78000,
        metadata_json={
            "premise": "Mira calibrates an observatory.",
            "creation_protagonist_name": "Mira",
            "creation_protagonist_source": "user",
        },
    )
    if locked:
        ensure_project_book_design_snapshot(p)
        assert planner._source_bound_cast_enabled(p)
    return p


def validate(value, p=None):
    return planner._validate_generated_volume_outline_or_raise(
        value,
        project=p or project(),
        logical_name="volume_1_chapter_outline",
        volume_number=1,
        expected_count=4,
        chapter_number_offset=1,
        cast_spec={
            "protagonist": {
                "name": "Mira",
                "role": "protagonist",
                "aliases": ["M"],
                "gender": "unknown",
            }
        },
        volume_entry={"chapter_range": [1, 4]},
        strict_story_effects=True,
        strict_field_degeneracy=True,
    )


def test_full_raw_unknowns_long_strings_state_cause_flaw_information_survive_no_defaults():
    raw = source()
    before = deepcopy(raw)
    result, view, receipt = prepare(raw)
    assert result == raw == before and result is not raw
    assert (
        result["chapters"][0]["scenes"][0]["unknown_scene_field"]
        == before["chapters"][0]["scenes"][0]["unknown_scene_field"]
    )
    assert "hook_type" not in result["chapters"][0]
    assert "selected_effect_skills" not in result["chapters"][0]
    assert receipt["original_sha256"] == receipt["projected_sha256"] == _hash(raw)
    assert not receipt["defaults_inserted"] and not receipt["semantic_repair_performed"]
    assert view.chapters[0].chapter_goal == raw["chapters"][0]["goal"]


def test_exact_plain_alias_is_only_change_with_receipt_and_three_nontargets_intact():
    raw = source()
    raw["chapters"][1]["scenes"][0]["participants"] = ["M"]
    before = deepcopy(raw)
    result, _, receipt = prepare(raw)
    expected = deepcopy(before)
    expected["chapters"][1]["scenes"][0]["participants"] = ["Mira"]
    assert result == expected and raw == before
    for i in (0, 2, 3):
        assert _hash(result["chapters"][i]) == _hash(before["chapters"][i])
    assert receipt["aliases"] == [
        {
            "path": "/chapters/1/scenes/0/participants/0",
            "before": "M",
            "after": "Mira",
            "before_sha256": _hash("M"),
            "after_sha256": _hash("Mira"),
        }
    ]
    assert receipt["identity_manifest_sha256"] == _hash(MANIFEST)
    assert receipt["projected_sha256"] == _hash(expected)


@pytest.mark.parametrize(
    "field",
    ["goal", "main_conflict", "hook_description", "title", "opening_situation", "causal_contract"],
)
def test_missing_chapter_semantics_produce_full_report_and_never_repair(field):
    raw = source()
    del raw["chapters"][1][field]
    before = deepcopy(raw)
    with pytest.raises(SourceBoundOutlineIntegrityError) as error:
        prepare(raw)
    assert error.value.report["passed"] is False and error.value.report["violations"]
    assert raw == before and field not in raw["chapters"][1]


@pytest.mark.parametrize(
    "field", ["purpose", "entry_state", "exit_state", "time_label", "participants"]
)
def test_missing_scene_semantics_are_not_copied_from_siblings(field):
    raw = source()
    del raw["chapters"][1]["scenes"][0][field]
    before = deepcopy(raw)
    with pytest.raises(SourceBoundOutlineIntegrityError):
        prepare(raw)
    assert raw == before


@pytest.mark.parametrize(
    "fault",
    [
        "number",
        "volume",
        "numeric_string",
        "action_string",
        "annotated_name",
        "whitespace_name",
        "title_collision",
        "nonfinite",
    ],
)
def test_invalid_source_is_not_renumbered_coerced_split_renamed_or_soft_filled(fault):
    raw = source()
    ch = raw["chapters"][1]
    if fault == "number":
        ch["chapter_number"] = 88
    elif fault == "volume":
        ch["volume_number"] = 7
    elif fault == "numeric_string":
        ch["target_word_count"] = "2600"
    elif fault == "action_string":
        ch["scenes"][0]["action_sequence"] = "first→second"
    elif fault == "annotated_name":
        ch["scenes"][0]["participants"] = ["Mira (observer)"]
    elif fault == "whitespace_name":
        ch["scenes"][0]["participants"] = [" Mira "]
    elif fault == "title_collision":
        ch["title"] = raw["chapters"][0]["title"]
    else:
        ch["unknown_authored"]["nonfinite"] = float("inf")
    before = deepcopy(raw)
    with pytest.raises(SourceBoundOutlineIntegrityError):
        prepare(raw)
    assert raw == before


def test_actual_contract_meta_report_retains_original_goal_and_all_violations():
    raw = source()
    for ch in raw["chapters"]:
        ch["goal"] = "让读者看清仪器的误差；Mira封存原图，保留未知条款与所有后果。"
    before = deepcopy(raw)
    with pytest.raises(SourceBoundOutlineIntegrityError) as error:
        prepare(raw)
    actual = error.value.report
    assert sum(v["code"] == "PLAN_CHAPTER_GOAL_META" for v in actual["violations"]) == 4
    assert raw == before
    assert actual["source_integrity_receipt"]["original_sha256"] == _hash(before)


def test_real_staged_entry_returns_full_raw_and_bypasses_all_silent_story_mutators(monkeypatch):
    def forbidden(*args: object, **kwargs: object):
        pytest.fail("Source-bound input entered a silent semantic normalizer")

    for name in (
        "_derive_chapter_title_seed",
        "_normalize_generated_outline_titles_or_fail",
        "_enrich_generated_volume_outline_systemic_fields",
        "_repair_generated_volume_outline_contract_inputs",
        "_repair_generated_volume_outline_contract_blocks",
        "_soft_fill_golden_three_outline_fields",
        "_rescue_golden_three_solo_scenes",
        "_strip_outline_goal_meta_prefixes",
    ):
        monkeypatch.setattr(planner, name, forbidden)
    p = project()
    raw = source()
    before = deepcopy(raw)
    result = validate(raw, p)
    assert result == raw == before
    receipt = p.metadata_json["deterministic_audit_outline_integrity"][_hash(result)]
    assert receipt["story_values_preserved"] and not receipt["grade_asserted"]


def test_real_staged_entry_raises_existing_typed_full_report_for_finite_repair():
    raw = source()
    for ch in raw["chapters"]:
        ch["goal"] = "让读者看清仪器误差；完整行动条款仍保留。"
    before = deepcopy(raw)
    with pytest.raises(planner.OutlineContractValidationError) as error:
        validate(raw)
    assert len(error.value.report["violations"]) >= 4 and raw == before
    directives = planner._outline_repair_directives_from_error(
        error.value, language="en", volume_number=1, chapter_number_offset=1, expected_count=4
    )
    assert all(str(n) in " ".join(directives) for n in range(1, 5))


def test_omitted_optional_flaw_information_and_effects_stay_absent_without_defaults():
    raw = source()
    for ch in raw["chapters"]:
        for key in (
            "protagonist_flaw",
            "chapter_information_introduced",
            "chapter_information_held_back",
        ):
            del ch[key]
    before = deepcopy(raw)
    assert validate(raw) == before == raw


def test_existing_hard_field_degeneracy_is_not_waived_by_raw_preservation():
    raw = source()
    raw["chapters"][1]["goal"] = raw["chapters"][1]["main_conflict"]
    before = deepcopy(raw)
    with pytest.raises(planner.OutlineFieldDegeneracyError):
        validate(raw)
    assert raw == before


def test_positive_source_bound_snapshot_must_have_its_actual_canonical_hash():
    p = project()
    p.metadata_json = {
        **p.metadata_json,
        "book_design_snapshot": {
            **p.metadata_json["book_design_snapshot"],
            "source_hash": "0" * 64,
        },
    }
    with pytest.raises(ValueError, match="hash mismatch"):
        validate(source(), p)


def test_remaining_validator_cannot_validate_a_changed_surrogate_view(monkeypatch):
    def changing_checker(batch, **kwargs: object):
        batch.chapters[0].selected_effect_skills = {"primary": "unapproved-surrogate"}

    monkeypatch.setattr(
        planner, "_require_selected_story_effect_contracts_or_raise", changing_checker
    )
    raw = source()
    before = deepcopy(raw)
    with pytest.raises(planner.OutlineContractValidationError) as error:
        validate(raw)
    assert error.value.report["violations"][0]["code"] == "PLAN_SOURCE_VALIDATOR_MUTATION"
    assert raw == before


def test_legacy_validator_ast_unchanged_except_guarded_owned_function_and_parent_capacity():
    root = Path(__file__).resolve().parents[2] / "reports/continuous-recovery-2026-10-16/freezes/source-bound-outline-integrity"

    def inventory(path):
        return {
            node.name: ast.dump(node, include_attributes=False)
            for node in ast.parse(path.read_text()).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }

    frozen, staged = inventory(root / "planner.frozen.py"), inventory(root / "planner.staged.py")
    assert set(frozen) == set(staged)
    assert [k for k in frozen if frozen[k] != staged[k]] == [
        "_validate_generated_volume_outline_or_raise"
    ]
    # The parent's capacity function is not in the scoped patch at all.
    assert frozen["_outline_patch_capacity"] == staged["_outline_patch_capacity"]


def test_legacy_output_matches_frozen_real_function_without_source_bound_branch():
    root = Path(__file__).resolve().parents[2] / "reports/continuous-recovery-2026-10-16/freezes/source-bound-outline-integrity"
    node = next(
        n
        for n in ast.parse((root / "planner.frozen.py").read_text()).body
        if isinstance(n, ast.FunctionDef)
        and n.name == "_validate_generated_volume_outline_or_raise"
    )
    namespace = dict(planner.__dict__)
    exec(  # noqa: S102 -- Isolated execution of the inspected frozen function only.
        compile(ast.Module(body=[node], type_ignores=[]), "frozen-validator", "exec"), namespace
    )
    old = namespace[node.name]
    p = project(locked=False)
    kwargs = {
        "project": p,
        "logical_name": "volume_1_chapter_outline",
        "volume_number": 1,
        "expected_count": 4,
        "chapter_number_offset": 1,
        "cast_spec": {"protagonist": {"name": "Mira", "role": "protagonist"}},
        "volume_entry": {"chapter_range": [1, 4]},
        "strict_story_effects": True,
        "strict_field_degeneracy": True,
    }
    assert old(deepcopy(source()), **kwargs) == planner._validate_generated_volume_outline_or_raise(
        deepcopy(source()), **kwargs
    )


def test_declared_field_alias_is_validated_but_never_renamed_or_dropped():
    raw = source()
    chapter = raw["chapters"][2]
    chapter["chapter_title"] = chapter.pop("title")
    chapter["conflict"] = chapter.pop("main_conflict")
    before = deepcopy(raw)
    assert validate(raw) == before == raw


def test_conflicting_supplied_field_aliases_fail_without_selecting_one_story():
    raw = source()
    raw["chapters"][1]["chapter_goal"] = "A separate incompatible authored goal."
    before = deepcopy(raw)
    with pytest.raises(SourceBoundOutlineIntegrityError) as error:
        prepare(raw)
    assert any(v["code"] == "PLAN_SOURCE_ALIAS_CONFLICT" for v in error.value.report["violations"])
    assert raw == before


def test_source_bound_palette_empty_routes_preserve_raw_legacy_still_requires_coverage():
    raw = source()
    for chapter in raw["chapters"]:
        chapter["selected_effect_skills"] = {}
    before = deepcopy(raw)
    for locked in (True, False):
        p = project(locked=locked)
        p.metadata_json = {
            **p.metadata_json,
            "creation_intent_contract": {
                "field_sources": {"story_enhancers": "explicit"},
                "story_enhancers": {"effect_skills": ["comedy_engine"]},
            },
        }
        selection = planner.resolve_explicit_story_enhancers(p.metadata_json)
        gaps = planner.audit_story_enhancer_coverage(raw["chapters"], selection)
        assert gaps and gaps[0]["coverage"] == 0
        assert planner.story_enhancer_repair_directives(raw["chapters"], selection)
        if locked:
            result = validate(raw, p)
            assert result == before == raw
            assert all(ch["selected_effect_skills"] == {} for ch in result["chapters"])
            assert p.metadata_json["deterministic_audit_outline_integrity"][_hash(raw)][
                "projected_sha256"
            ] == _hash(before)
        else:
            with pytest.raises(planner.StoryEnhancerCoverageError):
                validate(deepcopy(raw), p)
        assert raw == before


def test_source_bound_palette_guard_does_not_bypass_explicit_stage_contract():
    p = project()
    p.metadata_json = {
        **p.metadata_json,
        "creation_intent_contract": {
            "field_sources": {"story_enhancers": "explicit", "hook_spec": "explicit"},
            "story_enhancers": {"effect_skills": ["comedy_engine"]},
            "hook_spec": {
                "story_effect_skill_selection": {"chapter_outline": {"primary": "comedy_engine"}}
            },
        },
    }
    assert planner._selected_story_effect_skill_keys(p, stage="chapter_outline") == (
        "comedy_engine",
    )
    raw = source()
    before = deepcopy(raw)
    with pytest.raises(planner.PlannerFallbackError, match="STORY_EFFECT_CONTRACT_MISSING"):
        validate(raw, p)
    assert raw == before
    assert "deterministic_audit_outline_integrity" not in p.metadata_json
