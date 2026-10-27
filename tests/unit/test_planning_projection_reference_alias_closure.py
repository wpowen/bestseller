"""Two producer-proved aliases close bidirectionally without widening other scopes."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
from bestseller.services import llm
from bestseller.services import planning_projection_consistency as projection
from bestseller.services.fanqie_short_planner import build_fanqie_segment_outline_batch
from bestseller.services.lossless_source_encoding import _json
from bestseller.settings import load_settings, reset_settings_cache

pytestmark = pytest.mark.unit

FAMILIES = pytest.mark.parametrize(
    "section,source_leaf,scene_leaf",
    [
        ("opening_contract", "immediate_stakes", "conflict_stakes"),
        ("continuity_contract", "fact_boundary", "information_control"),
    ],
)
ROOTS = ("causal_contract", "methodology_contract", "scenes/0/methodology_contract")


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Alias closure tests forbid DB/provider/network")

    monkeypatch.setenv(
        "BESTSELLER__DATABASE__URL", f"sqlite+aiosqlite:///{tmp_path / 'alias-test.sqlite3'}"
    )
    reset_settings_cache()
    assert str(load_settings().database.url).startswith("sqlite")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    monkeypatch.setattr(AsyncSession, "execute", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


@pytest.fixture
def produced():
    sheet = FanqieShortBeatSheet(
        beats=[
            FanqieShortBeat(
                segment_number=number,
                beat_role="observational",
                purpose=f"Inspect archive instrument {number}",
                payoff=f"Keep the provisional measurement {number}",
                emotional_turn="The observer remains uncertain",
                opening_contract={
                    "immediate_stakes": f"The ferry leaves before reading {number} is verified",
                    "signature_image": "A lantern beside the gauge",
                },
                continuity_contract={
                    "fact_boundary": f"Only the observer has seen reading {number}",
                },
            )
            for number in range(1, 5)
        ]
    )
    candidate = build_fanqie_segment_outline_batch(
        SimpleNamespace(target_chapters=4, target_word_count=8000, metadata_json={}), sheet
    )
    observations = [
        {
            "draft_eligible": False,
            "hard_violations": [
                {"code": "CONTINUITY_CONFLICT", "path": "chapters[0].scenes[0]", "source_refs": []}
            ],
        }
    ]
    return candidate, observations


def family_paths(section, source_leaf, scene_leaf):
    return [
        *(f"/chapters/0/{root}/fanqie_short_v2/{section}/{source_leaf}" for root in ROOTS),
        f"/chapters/0/scenes/0/{scene_leaf}",
        f"/chapters/0/scenes/0/methodology_contract/{scene_leaf}",
    ]


def native(candidate, contract, changes):
    values = {
        key: changes.get(path, projection._at(candidate, path))
        for key, path in contract["id_to_path"].items()
    }
    call = {
        "id": "alias-closure-observation",
        "type": "function",
        "function": {"name": projection.TOOL, "arguments": _json(values)},
    }
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "tool_calls": [call],
        "raw_message": {"role": "assistant", "content": "", "tool_calls": [deepcopy(call)]},
    }


@FAMILIES
@pytest.mark.parametrize("seed_index", range(6))
def test_every_producer_copy_selects_the_same_complete_alias_family(
    produced, section, source_leaf, scene_leaf, seed_index
):
    candidate, observations = produced
    paths = family_paths(section, source_leaf, scene_leaf)
    assert len({projection._at(candidate, path) for path in paths}) == 1
    refs = [paths[seed_index]] if seed_index < 5 else list(reversed(paths))
    before = deepcopy((candidate, observations, refs))
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=refs
    )
    assert contract["paths"] == sorted(paths)
    assert list(contract["projection_groups"].values()) == [sorted(paths)]
    for binding in contract["exact_reference_bindings"]:
        assert binding["paths"] == sorted(paths)
    assert not contract["grade_asserted"] and not contract["source_authority_granted"]
    assert (candidate, observations, refs) == before


@FAMILIES
def test_new_candidate_can_repair_all_alias_copies_and_freezes_every_other_value(
    produced, section, source_leaf, scene_leaf
):
    candidate, observations = produced
    paths = family_paths(section, source_leaf, scene_leaf)
    # Custody, rather than current equality, proves the mapped copy family.
    candidate["chapters"][0]["scenes"][0][scene_leaf] = "A previously diverged projection"
    before = deepcopy((candidate, observations))
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[paths[0]]
    )
    assert contract["paths"] == sorted(paths)
    changes = dict.fromkeys(paths, "The observer reports only the verified measurement")
    result, receipt = projection.parse_projection_consistency(
        native(candidate, contract, changes), candidate=candidate, contract=contract
    )
    expected = deepcopy(candidate)
    for path, value in changes.items():
        projection._set(expected, path, value)
    assert _json(result) == _json(expected)
    assert receipt["changed_paths"] == sorted(paths)
    assert receipt["non_targets_unchanged"] and receipt["inverse_verified"]
    assert not receipt["grade_asserted"] and not receipt["source_authority_granted"]
    assert receipt["full_domain_readiness_and_new_admission_required"]
    assert (candidate, observations) == before


@FAMILIES
def test_unknown_same_named_fields_and_wrong_contract_sections_are_not_linked(
    produced, section, source_leaf, scene_leaf
):
    candidate, observations = produced
    chapter = candidate["chapters"][0]
    original = projection._at(candidate, family_paths(section, source_leaf, scene_leaf)[0])
    chapter[scene_leaf] = original
    for root in ROOTS:
        body = projection._at(candidate, f"/chapters/0/{root}")
        body.setdefault("unknown_authored", {})[scene_leaf] = original
        body["fanqie_short_v2"]["payoff_contract"][source_leaf] = original
        body["fanqie_short_v2"][section][scene_leaf] = original
    chapter["methodology_contract"][scene_leaf] = original
    paths = family_paths(section, source_leaf, scene_leaf)
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[paths[3]]
    )
    assert contract["paths"] == sorted(paths)
    wrong = f"/chapters/0/causal_contract/fanqie_short_v2/payoff_contract/{source_leaf}"
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[wrong]
    )
    assert contract["paths"] == sorted(
        f"/chapters/0/{root}/fanqie_short_v2/payoff_contract/{source_leaf}" for root in ROOTS
    )
    flat = f"/chapters/0/methodology_contract/{scene_leaf}"
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[flat]
    )
    assert contract["paths"] == [flat]


@FAMILIES
@pytest.mark.parametrize("missing", ["absent", "empty"])
def test_no_scene_does_not_infer_alias_edges(produced, section, source_leaf, scene_leaf, missing):
    candidate, observations = produced
    chapter = candidate["chapters"][0]
    chapter.pop("scenes")
    if missing == "empty":
        chapter["scenes"] = []
    paths = family_paths(section, source_leaf, scene_leaf)
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[paths[0]]
    )
    assert contract["paths"] == sorted(paths[:2])


@FAMILIES
@pytest.mark.parametrize("seed_index", [0, 3, 4])
def test_multiple_scenes_do_not_infer_alias_correspondence(
    produced, section, source_leaf, scene_leaf, seed_index
):
    candidate, observations = produced
    chapter = candidate["chapters"][0]
    other = deepcopy(chapter["scenes"][0])
    other["scene_number"] = 2
    chapter["scenes"].append(other)
    paths = family_paths(section, source_leaf, scene_leaf)
    seed = paths[seed_index]
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[seed]
    )
    expected = [seed]
    if seed_index == 0:
        expected = [*paths[:3], paths[2].replace("/scenes/0/", "/scenes/1/")]
    assert contract["paths"] == sorted(expected)


@FAMILIES
@pytest.mark.parametrize("seed_index", [3, 4])
def test_aliases_require_an_actual_registered_producer_root(
    produced, section, source_leaf, scene_leaf, seed_index
):
    candidate, observations = produced
    chapter = candidate["chapters"][0]
    chapter["unknown_authored"] = {
        "fanqie_short_v2": deepcopy(chapter["causal_contract"]["fanqie_short_v2"])
    }
    for root in ROOTS:
        projection._at(candidate, f"/chapters/0/{root}").pop("fanqie_short_v2")
    path = family_paths(section, source_leaf, scene_leaf)[seed_index]
    contract = projection.build_projection_consistency_contract(
        candidate, observations, reference_paths=[path]
    )
    assert contract["paths"] == [path]


@FAMILIES
@pytest.mark.parametrize("copy_index", [3, 4])
def test_present_non_scalar_alias_copy_fails_closed(
    produced, section, source_leaf, scene_leaf, copy_index
):
    candidate, observations = produced
    paths = family_paths(section, source_leaf, scene_leaf)
    projection._set(candidate, paths[copy_index], {"summary": "Not a scalar counterpart"})
    with pytest.raises(ValueError, match="reference_closure_scalar"):
        projection.build_projection_consistency_contract(
            candidate, observations, reference_paths=[paths[0]]
        )


def test_default_contract_tool_and_derived_receipt_match_the_frozen_v4_baseline(produced):
    candidate, observations = produced
    path = (
        Path(__file__).resolve().parents[2]
        / "reports/framework-recovery-2026-10-21/phase8j-before-referenced-projection-repair"
        / "planning_projection_consistency.py"
    )
    spec = importlib.util.spec_from_file_location("projection_alias_v4_baseline", path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    authority = {"source": "Original approved source; no new authority"}
    old = baseline.build_projection_consistency_contract(
        candidate, observations, source_authority=authority
    )
    default = projection.build_projection_consistency_contract(
        candidate, observations, source_authority=authority
    )
    explicit = projection.build_projection_consistency_contract(
        candidate, observations, source_authority=authority, reference_paths=None
    )
    assert _json(default) == _json(explicit) == _json(old)
    assert _json(projection.tools_for_projection_consistency(default)) == _json(
        baseline.tools_for_projection_consistency(old)
    )
    completion = native(candidate, old, {"/chapters/0/chapter_goal": "Inspect another instrument"})
    assert _json(
        projection.parse_projection_consistency(completion, candidate=candidate, contract=default)
    ) == _json(baseline.parse_projection_consistency(completion, candidate=candidate, contract=old))
