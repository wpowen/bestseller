"""Structural repair cannot replace canonical assets before joint validation."""

# Chinese story fixtures intentionally use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
from uuid import uuid4

import pytest

from bestseller.services import (
    book_design,
    concept_contract,
    conception,
    conception_repair_guard,
    conception_structure_repair,
)

pytestmark = pytest.mark.unit

_ASSET_FIELDS = (
    "premise",
    "synopsis",
    "story_spine",
    "concept_contract",
    "tournament_winner",
    "writing_profile",
    "world_model_payload",
    "ctx",
    "title",
    "tags",
    "explicit_concept_seed",
    "_contract_body_fingerprint",
    "story_appeal_report",
)


def _state() -> dict[str, Any]:
    return {
        "premise": "林舟要救出妹妹",
        "synopsis": "林舟寻找妹妹，反复向村人打听消息。",
        "story_spine": {"who": "林舟", "wants": "救妹妹", "against": "封城的债主"},
        "concept_contract": {"version": "original-contract"},
        "tournament_winner": {"concept": "original-winner"},
        "writing_profile": {
            "market": {"logline": "原有钩子", "reader_promise": "轻松破局"},
            "character": {"protagonist": "林舟"},
            "world": {"rule": "只有城内人可以开启城门"},
        },
        "world_model_payload": {"laws": ["城内人控制城门"]},
        "ctx": {"language": "zh-CN", "tone_preference": "轻松"},
        "explicit_concept_seed": "林舟救妹妹，能力不自损",
        "title": "城门外的哥哥",
        "tags": ["轻松"],
        "chapter_count": 80,
        "_ap_genre": "仙侠",
        "_ap_sub": "幻想修仙",
        "_ap_language": "zh-CN",
        "_contract_body_fingerprint": "original-body",
        "story_appeal_report": {"logline_gate": {"old": True}, "title": {"passed": True}},
        "conception_log": [{"agent": "prior-stage"}],
        "llm_run_ids": [str(uuid4())],
    }


def _assets(state: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy({key: state[key] for key in _ASSET_FIELDS})


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    passed: bool = True,
    fail_at: str | None = None,
) -> dict[str, Any]:
    candidate = {
        "premise": "林舟必须在封城前救出妹妹",
        "synopsis": "证人被债主转移，林舟必须说服守门人与自己合作。",
        "story_spine": {"who": "林舟", "wants": "救妹妹", "against": "转移证人的债主"},
    }
    rebuilt_spine = {**candidate["story_spine"], "question": "守门人为何同意合作"}
    rebuilt = {
        "version": "rebuilt-contract",
        "story_spine": rebuilt_spine,
        "hook_card": {"one_liner": "守门人的选择改变了封城局面"},
    }
    winner = {"concept": "reconciled-winner"}
    ids = {stage: str(uuid4()) for stage in ("propose", "reconcile", "guard")}
    calls: list[tuple[str, dict[str, Any]]] = []
    original_candidate = copy.deepcopy(candidate)

    def record(stage: str, kwargs: dict[str, Any]) -> None:
        calls.append((stage, copy.deepcopy(kwargs)))
        if fail_at == stage:
            raise concept_contract.ConceptContractError([f"fixture_{stage}_failed"])

    async def propose(_session: Any, _settings: Any, **kwargs: Any) -> dict[str, Any]:
        record("propose", kwargs)
        source_json = json.dumps(
            kwargs["state"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "candidate": candidate,
            "llm_run_id": ids["propose"],
            "source_hash": hashlib.sha256(source_json.encode("utf-8")).hexdigest(),
        }

    async def reconcile(_session: Any, _settings: Any, **kwargs: Any):
        record("reconcile", kwargs)
        return copy.deepcopy(winner), [ids["reconcile"]]

    def build(**kwargs: Any) -> dict[str, Any]:
        record("build", kwargs)
        return copy.deepcopy(rebuilt)

    def require(contract: Any, **kwargs: Any) -> dict[str, Any]:
        stage = "require" if contract.get("version") == "rebuilt-contract" else "require_source"
        record(stage, {"contract": contract, **kwargs})
        return contract

    async def guard(_session: Any, _settings: Any, **kwargs: Any) -> dict[str, Any]:
        record("guard", kwargs)
        return {"passed": passed, "llm_run_id": ids["guard"], "regressions": []}

    monkeypatch.setattr(conception_structure_repair, "propose_conception_structure_repair", propose)
    monkeypatch.setattr(conception, "_reconcile_concept_seed_with_final_premise", reconcile)
    monkeypatch.setattr(concept_contract, "build_concept_contract", build)
    monkeypatch.setattr(concept_contract, "require_conception_contract_for_target", require)
    monkeypatch.setattr(conception_repair_guard, "review_conception_repair", guard)
    monkeypatch.setattr(book_design, "extract_creation_protagonist_name", lambda _value: "林舟")
    return {
        "calls": calls,
        "candidate": candidate,
        "original_candidate": original_candidate,
        "rebuilt": rebuilt,
        "winner": winner,
        "ids": ids,
    }


async def test_rejected_candidate_retains_original_assets_and_auditable_receipt(monkeypatch):
    state = _state()
    before = _assets(state)
    wired = _wire(monkeypatch, passed=False)

    with pytest.raises(concept_contract.ConceptContractError, match="REGRESSION_REVIEW_UNRESOLVED"):
        await conception._repair_conception_candidate_structure(
            None,
            None,
            state,
            {"scope": "story", "reason": "缺少行动升级"},
        )

    assert _assets(state) == before
    assert [stage for stage, _ in wired["calls"]] == [
        "require_source",
        "propose",
        "reconcile",
        "build",
        "require",
        "guard",
    ]
    receipt = state["conception_log"][-1]
    assert receipt["status"] == "rejected"
    assert receipt["candidate"] == wired["original_candidate"]
    assert receipt["reviewed_candidate"]["story_spine"] == wired["rebuilt"]["story_spine"]
    assert receipt["regression_report"]["passed"] is False
    assert state["llm_run_ids"][-3:] == list(wired["ids"].values())
    assert wired["candidate"] == wired["original_candidate"]


async def test_guard_reviews_rebuilt_candidate_before_assets_are_updated(monkeypatch):
    state = _state()
    before = _assets(state)
    wired = _wire(monkeypatch)

    await conception._repair_conception_candidate_structure(
        None,
        None,
        state,
        {"scope": "story", "reason": "缺少行动升级"},
    )

    calls = dict(wired["calls"])
    assert calls["require"]["target_chapters"] == state["chapter_count"]
    assert calls["guard"]["before"] == {
        key: before[key] for key in ("premise", "synopsis", "story_spine")
    }
    assert calls["guard"]["candidate"] == {
        **wired["original_candidate"],
        "story_spine": wired["rebuilt"]["story_spine"],
    }
    assert calls["guard"]["creation_intent"]["concept_seed"] == before["explicit_concept_seed"]
    assert state["premise"] == wired["original_candidate"]["premise"]
    assert state["synopsis"] == wired["original_candidate"]["synopsis"]
    assert state["story_spine"] == wired["rebuilt"]["story_spine"]
    assert state["concept_contract"] == wired["rebuilt"]
    assert state["tournament_winner"] == wired["winner"]
    assert (
        state["writing_profile"]["market"]["logline"] == wired["rebuilt"]["hook_card"]["one_liner"]
    )
    assert state["story_appeal_report"] == {"title": {"passed": True}}
    assert state["_contract_body_fingerprint"] == conception._materialized_story_body_fingerprint(
        premise=state["premise"],
        writing_profile=state["writing_profile"],
        story_spine=state["story_spine"],
        world_model=state["world_model_payload"],
    )
    for key in ("title", "tags", "world_model_payload", "explicit_concept_seed"):
        assert state[key] == before[key]
    assert state["ctx"] == {**before["ctx"], "high_concept": wired["winner"]}
    assert state["writing_profile"]["character"] == before["writing_profile"]["character"]
    assert state["writing_profile"]["world"] == before["writing_profile"]["world"]
    assert state["conception_log"][-1]["status"] == "pending_common_review"
    assert wired["candidate"] == wired["original_candidate"]


async def test_missing_source_winner_cannot_replace_an_existing_contract(monkeypatch):
    state = _state()
    state["tournament_winner"] = None
    before = _assets(state)
    wired = _wire(monkeypatch)

    with pytest.raises(concept_contract.ConceptContractError, match="MISSING_SOURCE_WINNER"):
        await conception._repair_conception_candidate_structure(
            None,
            None,
            state,
            {"scope": "story", "reason": "缺少行动升级"},
        )

    assert _assets(state) == before
    assert [stage for stage, _ in wired["calls"]] == ["require_source", "propose"]
    assert state["conception_log"][-1]["candidate"] == wired["original_candidate"]


@pytest.mark.parametrize("fail_at", ["reconcile", "build", "require", "guard"])
async def test_rebuild_or_review_failure_never_adopts_partial_assets(monkeypatch, fail_at):
    state = _state()
    before = _assets(state)
    wired = _wire(monkeypatch, fail_at=fail_at)

    with pytest.raises(concept_contract.ConceptContractError, match=f"fixture_{fail_at}_failed"):
        await conception._repair_conception_candidate_structure(
            None,
            None,
            state,
            {"scope": "story", "reason": "缺少行动升级"},
        )

    assert _assets(state) == before
    stages = [stage for stage, _ in wired["calls"]]
    assert stages[-1] == fail_at
    if fail_at != "guard":
        assert "guard" not in stages
    assert state["conception_log"][-1]["candidate"] == wired["original_candidate"]


async def test_short_candidate_without_contract_still_requires_guard(monkeypatch):
    state = _state()
    state["chapter_count"] = 5
    state["concept_contract"] = {}
    state["tournament_winner"] = None
    wired = _wire(monkeypatch, passed=False)
    before = _assets(state)

    with pytest.raises(concept_contract.ConceptContractError, match="REGRESSION_REVIEW_UNRESOLVED"):
        await conception._repair_conception_candidate_structure(
            None,
            None,
            state,
            {"scope": "story", "reason": "缺少行动升级"},
        )

    assert [stage for stage, _ in wired["calls"]] == ["require_source", "propose", "guard"]
    assert _assets(state) == before


async def test_long_candidate_without_source_contract_cannot_be_adopted(monkeypatch):
    state = _state()
    state["concept_contract"] = {}
    state["tournament_winner"] = None
    before = copy.deepcopy(state)
    wired = _wire(monkeypatch, fail_at="require_source")

    with pytest.raises(concept_contract.ConceptContractError):
        await conception._repair_conception_candidate_structure(
            None,
            None,
            state,
            {"scope": "story", "reason": "缺少行动升级"},
        )

    assert state == before
    assert [stage for stage, _ in wired["calls"]] == ["require_source"]


@pytest.mark.parametrize("guard_raises", [False, True])
async def test_candidate_checkpoint_precedes_rebuild_and_guard_failure(monkeypatch, guard_raises):
    state = _state()
    before = _assets(state)
    wired = _wire(monkeypatch, passed=False, fail_at="guard" if guard_raises else None)
    saved = []

    async def checkpoint_save():
        snapshot = copy.deepcopy(state)
        saved.append(snapshot)
        wired["calls"].append(("checkpoint", {"state": snapshot}))

    with pytest.raises(concept_contract.ConceptContractError):
        await conception._repair_conception_candidate_structure(
            None,
            None,
            state,
            {"scope": "story", "reason": "缺少行动升级"},
            checkpoint_save=checkpoint_save,
        )

    stages = [stage for stage, _ in wired["calls"]]
    assert stages == [
        "require_source",
        "propose",
        "checkpoint",
        "reconcile",
        "build",
        "require",
        "guard",
    ]
    assert len(saved) == 1
    assert _assets(saved[0]) == before
    assert saved[0]["conception_log"][-1]["candidate"] == wired["original_candidate"]
    assert saved[0]["conception_log"][-1]["status"] == "candidate_pending_review"
    assert saved[0]["llm_run_ids"][-1] == wired["ids"]["propose"]
    assert _assets(state) == before
