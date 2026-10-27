# ruff: noqa: E501
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bestseller.services import derived_cast_repair as repair
from bestseller.services.llm import LLMCompletionResult

pytestmark = pytest.mark.unit


CAST = {
    "protagonist": {"name": "纪年", "obvious_flaw": "手头紧", "decision_policy": {"high_risk_allowances": "先算账"}},
    "supporting_cast": [{"name": "陈瑜", "role": "盟友", "history": "旧识"}],
    "emotion_tremors": {"hand_tremor": "紧张时发作"},
    "vendor_extension": {"keep": ["all"]},
}
WORLD = {"power_system": "怕字瞳读取恐惧", "rules": ["本期只读怕"]}
ISSUES = [{"path": ["protagonist", "obvious_flaw"], "quote": "手头紧", "problem": "依赖缺失", "required_outcome": "保留身份缺陷并与世界规则相容"}]


def _result(content: str, *, fallback: bool = False) -> LLMCompletionResult:
    return LLMCompletionResult(content=content, provider="fixture", model_name="fixture", llm_run_id=uuid4(), fallback_used=fallback, finish_reason="stop")


def test_issue_scope_and_patch_preserve_unmentioned_cast_structure():
    candidate, changes = repair._apply_patches(CAST, ISSUES, {"patches": [{"path": ["protagonist", "obvious_flaw"], "after": "手头紧但不妨碍他做决定", "reason": "仅修复依赖"}]})
    assert candidate["protagonist"]["name"] == CAST["protagonist"]["name"]
    assert candidate["supporting_cast"] == CAST["supporting_cast"]
    assert candidate["emotion_tremors"] == CAST["emotion_tremors"]
    assert candidate["vendor_extension"] == CAST["vendor_extension"]
    assert changes[0]["before"] == "手头紧"


@pytest.mark.parametrize("patches,match", [
    ([{"path": ["protagonist", "name"], "after": "新名", "reason": "越界"}], "PATCH_OUT_OF_SCOPE"),
    ([{"path": ["protagonist", "obvious_flaw"], "after": "手头紧", "reason": "无变化"}], "NOOP_PATCH"),
])
def test_outside_scope_and_noop_are_rejected(patches, match):
    with pytest.raises(ValueError, match=match):
        repair._apply_patches(CAST, ISSUES, {"patches": patches})


@pytest.mark.asyncio
async def test_repair_runs_one_editor_and_two_critics_and_replays(monkeypatch):
    calls = []
    after = deepcopy(CAST)
    after["protagonist"]["obvious_flaw"] = "手头紧但不妨碍他做决定"

    async def fake_complete(_session, _settings, request):
        calls.append(request.logical_role)
        if request.logical_role == "editor":
            return _result(json.dumps({"patches": [{"path": ["protagonist", "obvious_flaw"], "after": after["protagonist"]["obvious_flaw"], "reason": "仅修复依赖"}]}, ensure_ascii=False))
        body = json.loads(request.user_prompt.split("\n", 1)[1])
        index = {x["path"][0]: x["source_id"] for x in repair.world_authority_source_index(repair._sources(premise="开符铺", cost_policy="minimal", world=WORLD, before=CAST, after=after))}
        checks = []
        for axis, domains in (("mechanism_preservation", ("before", "after")), ("cost_compatibility", ("cost_policy", "after")), ("story_identity", ("premise", "after")), ("dependency_scope", ("world", "after"))):
            checks.append({"axis": axis, "verdict": "pass", "reason": "证据充分", "source_ids": [index[domains[0]], index[domains[1]]]})
        return _result(json.dumps({"review_input_hash": body["review_input_hash"], "checks": checks}, ensure_ascii=False))

    monkeypatch.setattr(repair, "complete_text", fake_complete)
    report = await repair.repair_cast_dependency(None, SimpleNamespace(), project_id=UUID("00000000-0000-0000-0000-000000000001"), cast_content=CAST, world_content=WORLD, premise="开符铺", cost_policy="minimal", issues=ISSUES)
    assert calls == ["editor", "critic", "critic"]
    assert report["status"] == "review_passed"
    repair.verify_cast_dependency_report(report, cast_content=CAST, world_content=WORLD)


@pytest.mark.asyncio
async def test_fallback_is_rejected_without_outer_retry(monkeypatch):
    calls = 0

    async def fake_complete(_session, _settings, request):
        nonlocal calls
        calls += 1
        return _result('{"error":"provider_unavailable"}', fallback=True)

    monkeypatch.setattr(repair, "complete_text", fake_complete)
    with pytest.raises(repair.CastDependencyRepairError, match="PROVIDER_UNAVAILABLE"):
        await repair.repair_cast_dependency(None, SimpleNamespace(), project_id=UUID("00000000-0000-0000-0000-000000000001"), cast_content=CAST, world_content=WORLD, premise="开符铺", cost_policy="minimal", issues=ISSUES)
    assert calls == 1
