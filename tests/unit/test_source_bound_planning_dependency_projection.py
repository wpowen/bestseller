"""Planning reuse bookkeeping must not become a downstream story dependency."""

from copy import deepcopy
import hashlib
import json
from uuid import UUID

import pytest
from test_planning_nested_output_contract import _assert_current_planning_bytes, _frozen_8r_prompts
from test_source_bound_planning_content import _compile, _expand_prompt_refs, _project

from bestseller.services import planner
from bestseller.services.source_bound_planning_content import build_source_bound_planning_prompts

KINDS = ("book_spec", "world_spec", "cast_spec", "volume_plan")
PREMISE = "两位观测员修复望远镜。"
SUFFIX = (
    "\nIf the provider offers the supplied structured function, prefer exactly one call "
    "to that function; otherwise return one complete strict JSON object. "
    "Do not emit both formats.\n"
)


def _chain(reused=(), changed=None):
    project = _project()
    assets = {}
    outputs = {}
    for index, kind in enumerate(KINDS):
        args = [project, PREMISE, *assets.values()]
        compile_asset = getattr(planner, "_compile_source_bound_" + kind)
        projection = compile_asset(*args)
        before = deepcopy(assets)
        system, user = build_source_bound_planning_prompts(
            kind=kind, project=project, premise=PREMISE, projection=projection, **assets
        )
        assert assets == before
        document = json.loads(user)
        assert _expand_prompt_refs(document["projection"], document) == projection
        input_hash = planner._planner_artifact_input_hash(
            logical_name=kind, system_prompt=system,
            user_prompt=user.rstrip() + SUFFIX, fallback_payload=projection,
        )
        outputs[kind] = (projection, system, user, input_hash)
        if kind == "volume_plan":
            continue
        asset = planner._with_planning_meta(
            projection, logical_name=kind, input_hash=str(index + 1) * 64,
            workflow_run_id=UUID(int=1), finish_reason="tool_calls",
        )
        asset["_meta"]["authored_unknown"] = {"literal": "Keep this authored requirement"}
        if kind in reused:
            original = deepcopy(asset)
            asset = planner._with_planning_meta(
                asset, logical_name=kind, input_hash=str(index + 1) * 64,
                workflow_run_id=UUID(int=2), reused_artifact_id=UUID(int=3),
            )
            assert original["_meta"]["reused"] is False
            assert asset["_meta"]["reused"] is True
        if changed and changed[0] == kind:
            changed[1](asset)
        assets[kind] = asset
    return assets, outputs


@pytest.mark.parametrize("reused", [("book_spec",), ("world_spec",), ("cast_spec",), KINDS[:3]])
def test_normal_reuse_recompile_render_and_actual_request_hash_stay_stable(reused):
    fresh_assets, fresh = _chain()
    reused_assets, cached = _chain(reused)
    assert cached == fresh
    for kind in reused:
        assert reused_assets[kind]["_meta"]["workflow_run_id"] != (
            fresh_assets[kind]["_meta"]["workflow_run_id"]
        )
        assert reused_assets[kind]["_meta"]["reused_artifact_id"] == str(UUID(int=3))
    for kind, (_, _, user, _) in cached.items():
        effective = json.loads(user)["existing_planning_assets"]
        for asset in effective.values():
            assert set(asset["_meta"]).isdisjoint(
                {"workflow_run_id", "reused", "reused_artifact_id"}
            )
            assert asset["_meta"]["authored_unknown"] == {
                "literal": "Keep this authored requirement"
            }
        assert set(effective) == set(KINDS[:KINDS.index(kind)])


@pytest.mark.parametrize("asset_kind,next_kind", [
    ("book_spec", "world_spec"), ("world_spec", "cast_spec"), ("cast_spec", "volume_plan"),
])
@pytest.mark.parametrize("key", ["authored_unknown", "source_content_policy", "input_hash"])
def test_unknown_authored_and_source_policy_metadata_still_change_dependency_hash(
    asset_kind, next_kind, key,
):
    _, baseline = _chain()
    _, changed = _chain(changed=(asset_kind, lambda asset: asset["_meta"].update({key: "Changed"})))
    assert changed[next_kind][0] != baseline[next_kind][0]
    assert changed[next_kind][2:] != baseline[next_kind][2:]


def test_only_exact_root_asset_metadata_paths_are_omitted_and_refs_roundtrip():
    project = _project({"authored": {"_meta": {"reused": "Canonical literal"}}})
    asset = {
        "workflow_run_id": "Authored root literal",
        "chapter": {"_meta": {"workflow_run_id": "Nested literal", "reused": True}},
        "_meta": {
            "workflow_run_id": "Runtime", "reused": True, "reused_artifact_id": "Runtime ID",
            "warn_only": True, "future_authored_field": {"reused": "Preserve"},
        },
    }
    before = deepcopy(asset)
    expected = deepcopy(asset)
    for key in ("workflow_run_id", "reused", "reused_artifact_id"):
        expected["_meta"].pop(key)
    _, user = build_source_bound_planning_prompts(
        kind="world_spec", project=project, premise=PREMISE,
        projection={"mirror": expected}, book_spec=asset,
    )
    data = json.loads(user)
    assert data["existing_planning_assets"]["book_spec"] == expected
    assert _expand_prompt_refs(data["projection"], data) == {"mirror": expected}
    assert data["projection"]["mirror"] == {"$ref": "#/existing_planning_assets/book_spec"}
    assert data["approved_sources"]["canonical_snapshot"]["content"]["authored"] == {
        "_meta": {"reused": "Canonical literal"}
    }
    assert asset == before
    changed = deepcopy(asset)
    changed["chapter"]["_meta"]["workflow_run_id"] = "Different authored literal"
    assert build_source_bound_planning_prompts(
        kind="world_spec", project=project, premise=PREMISE,
        projection={"mirror": expected}, book_spec=changed,
    )[1] != user


@pytest.mark.parametrize(("kind", "system_sha", "user_sha"), [
    ("book_spec", "1d313709af5136668036742a2a5f71497382c9ad5ab08504eb056ad5763a7be3",
     "ef994b24ed16ccf156b938af0f9c212b51a67d6e5ac65e09ac6fba47cbfdf270"),
    # Original legacy volume hashes remain the case identity for composite reports.
    # Expected bytes come from the immutable 8R native-volume release instead.
    pytest.param(
        "volume_plan", "5c293fe2c7b90f8216be7d7b56d528eda73232d8b47a0200e38981c543f80d1d",
        "4db81877a8a0f8336b84a1ed945604b12cc66f836c24fbf516de00e243e3d117",
        id=("volume_plan-2e47961ae0f16cd34f565bb473eaa9d40e4f8f947dbe3ee6ee0157a90baeeb7f-"
            "55b5abc2aaffdaba2bbcd8c1843f295c7dd57760d116034f2958506e42ccfdad"),
    ),
])
def test_prechange_book_and_volume_prompt_bytes_without_runtime_keys(kind, system_sha, user_sha):
    project = _project()
    assets = _compile(project)
    system, user = build_source_bound_planning_prompts(
        kind=kind, project=project, premise=PREMISE, projection=assets[KINDS.index(kind)],
        **dict(zip(KINDS[:3], assets[:3], strict=True)),
    )
    historical = _frozen_8r_prompts()[kind]
    assert hashlib.sha256(historical[0].encode()).hexdigest() == system_sha
    assert hashlib.sha256(historical[1].encode()).hexdigest() == user_sha
    _assert_current_planning_bytes((system, user), historical)
