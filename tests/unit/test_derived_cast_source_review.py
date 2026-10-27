# ruff: noqa: E501, RUF001
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bestseller.services.derived_cast_source_review import (
    _apply_editor_patches,
    _patch_targets,
    _walk_source,
    build_cast_recovery_candidate,
    build_historical_cast_evidence,
    normalize_cast_recovery_sources,
    parse_review_json,
    verify_cast_source_review,
    verify_cast_source_review_bundle,
)
from bestseller.services.story_rebuild_workspace import source_content_hash


def _compiled() -> dict:
    return {
        "protagonist": {"name": "纪年", "role": "protagonist", "goal": "当前目标"},
        "supporting_cast": [{"name": "陈瑜", "role": "rival", "goal": "批准目标"}],
        "antagonist": None,
    }


def _legacy() -> dict:
    return {
        "protagonist": {
            "name": "纪年", "role": "protagonist", "goal": "批准目标",
            "fear": "旧代价：每次使用都手抖", "background": "批准背景",
        },
        "antagonist": {"name": "纪年", "role": "antagonist", "goal": "旧反派"},
        "supporting_cast": [
            {"name": "陈瑜", "role": "rival", "goal": "批准目标", "background": "批准背景"},
            {"name": "旧人", "role": "supporting", "goal": "旧目标"},
        ],
    }


def _sources() -> dict:
    return {
        "premise": "纪年盘下半铺，陈瑜与他合作。",
        "book_spec": {"protagonist": {"name": "纪年", "goal": "批准目标"}, "陈瑜": "批准背景"},
        "world_spec": {"陈瑜": "青云宗下山弟子"},
    }


def _checks(audit: dict, *, verdict: str = "pass") -> list[dict]:
    by_domain: dict[str, list[str]] = {}
    for row in audit["source_index"]:
        by_domain.setdefault(row["source"], []).append(row["source_id"])
    domains = {
        "identity_binding": ("premise", "book_spec", "candidate"),
        "source_fidelity": ("premise", "book_spec", "world_spec", "candidate"),
        "mechanism_and_cost": ("book_spec", "world_spec", "candidate"),
        "dependency_scope": ("premise", "world_spec", "candidate"),
    }
    return [
        {
            "axis": axis,
            "verdict": verdict,
            "reason": "证据完整",
            "source_ids": [by_domain[domain][0] for domain in required],
        }
        for axis, required in domains.items()
    ]


def _receipt(audit: dict, *, run_id: UUID | None = None, **overrides: object) -> dict:
    checks = _checks(audit)
    receipt = {
        "version": "derived-cast-source-review.v5",
        "candidate_hash": audit["candidate_cast_hash"],
        "review_input_hash": audit["review_input_hash"],
        "checks": checks,
        "raw_response": json.dumps({
            "candidate_hash": audit["candidate_cast_hash"],
            "review_input_hash": audit["review_input_hash"],
            "checks": checks,
        }, ensure_ascii=False),
        "review_id": str(uuid4()),
        "llm_run_id": str(run_id or uuid4()),
        "provider": "real-provider",
        "model": "review-model",
        "fallback_used": False,
        "finish_reason": "stop",
    }
    receipt.update(overrides)
    return receipt


def test_candidate_preserves_unaffected_legacy_cast_and_unproven_fields() -> None:
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    assert candidate["protagonist"]["name"] == "纪年"
    assert candidate["protagonist"]["fear"] == "旧代价：每次使用都手抖"
    assert all(item.get("name") != "纪年" for item in candidate.get("supporting_cast", []))
    assert any(item.get("name") == "旧人" for item in candidate.get("supporting_cast", []))
    assert audit["promotion_status"] == "needs_independent_source_review"
    assert audit["candidate_cast_hash"]
    current_rows = [row for row in audit["source_index"] if row["source"] in {"premise", "book_spec", "world_spec"}]
    assert all(row["artifact_id"] in {"premise-1", "book-1", "world-1"} and row["artifact_hash"] for row in current_rows)


def test_candidate_index_is_actual_candidate_and_compiler_hints_are_non_authoritative() -> None:
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    actual: list[dict[str, str]] = []
    _walk_source(candidate, "candidate", "candidate", actual)
    indexed = [row for row in audit["source_index"] if row.get("source") == "candidate"]
    assert len({row["path"] for row in indexed}) == len(indexed)
    assert {(row["path"], row["quote"]) for row in indexed} == {
        (row["path"], row["quote"]) for row in actual
    }
    assert not any(row.get("source") == "compiler_hint" for row in audit["source_index"])
    assert audit["compiler_hint_index"]
    assert all(row["evidence_role"] == "non_authoritative_compiler_hint" for row in audit["compiler_hint_index"])


def test_legacy_eight_roles_are_observation_baseline_and_evidenced_identity_patch_keeps_count() -> None:
    legacy = {
        "protagonist": {"name": "纪年", "role": "protagonist"},
        "antagonist": {"name": "纪年", "role": "antagonist"},
        "supporting_cast": [
            {"name": f"配角{i}", "role": "supporting"} for i in range(1, 7)
        ],
    }
    sources = {
        "premise": "纪年必须穿过矿区。",
        "book_spec": {
            "protagonist": {"name": "纪年", "role": "protagonist"},
            "antagonist": {"name": "矿主霍岐", "role": "antagonist"},
        },
        "world_spec": {"rules": "灵脉会反噬"},
    }
    candidate, audit = build_cast_recovery_candidate(
        {"protagonist": {"name": "纪年", "role": "protagonist"}, "antagonist": None},
        legacy,
        approved_sources=sources,
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    assert len(candidate["supporting_cast"]) == 6
    assert candidate["antagonist"]["name"] == "矿主霍岐"
    assert all(item["removed"] is False for item in audit["entries"])


def test_normalize_sources_replays_persisted_artifact_envelopes() -> None:
    book, world = normalize_cast_recovery_sources(
        {"book_spec": {"protagonist": {"name": "纪年"}}},
        {"world_spec": {"rules": ["灵脉"]}},
    )
    assert book == {"protagonist": {"name": "纪年"}}
    assert world == {"rules": ["灵脉"]}


def test_editor_scalar_patch_requires_existing_role_and_source_evidence() -> None:
    candidate = {"antagonist": {"name": "纪年", "role": "antagonist"}, "supporting_cast": [{"name": "阿萤"}]}
    index = [{"source_id": "S0001", "source": "book_spec", "quote": "矿主霍岐"}]
    patched, changes = _apply_editor_patches(
        candidate,
        [{"path": ["antagonist", "name"], "before": "纪年", "after": "矿主霍岐", "source_ids": ["S0001"], "reason": "approved antagonist identity"}],
        index,
    )
    assert patched["antagonist"]["name"] == "矿主霍岐"
    assert len(changes) == 1
    with pytest.raises(ValueError, match="PROTECTED_FIELD"):
        _apply_editor_patches(candidate, [{"path": ["supporting_cast", 0, "role"], "before": "", "after": "x", "source_ids": ["S0001"], "reason": "bad"}], index)


def test_editor_patch_changes_candidate_hash_and_persisted_quote_tamper_is_rejected() -> None:
    candidate = {"antagonist": {"name": "纪年", "role": "antagonist"}}
    index = [{"source_id": "S0001", "source": "book_spec", "quote": "矿主霍岐"}]
    patched, _ = _apply_editor_patches(candidate, [{"path": ["antagonist", "name"], "before": "纪年", "after": "矿主霍岐", "source_ids": ["S0001"], "reason": "role evidence"}], index)
    assert source_content_hash(patched) != source_content_hash(candidate)
    with pytest.raises(ValueError, match="AFTER_UNEVIDENCED"):
        _apply_editor_patches(candidate, [{"path": ["antagonist", "name"], "before": "纪年", "after": "矿主霍岐", "source_ids": ["S0002"], "reason": "tampered"}], [{"source_id": "S0002", "source": "book_spec", "quote": "伪造"}])


def test_editor_replace_span_preserves_field_and_applies_same_base_right_to_left() -> None:
    candidate = {"protagonist": {"goal": "纪年把柄簿多一页，纪年再卖符"}}
    field = candidate["protagonist"]["goal"]
    first = field.index("纪年")
    second = field.rindex("纪年")
    index = [{"source_id": "S0001", "source": "book_spec", "quote": "孙砚把柄簿多一页；孙砚", "artifact_status": "approved"}]
    patches = [
        {"op": "replace_span", "path": ["protagonist", "goal"], "before_hash": source_content_hash(field), "start": first, "end": first + 2, "before": "纪年", "after": "孙砚", "source_ids": ["S0001"], "reason": "角色引用"},
        {"op": "replace_span", "path": ["protagonist", "goal"], "before_hash": source_content_hash(field), "start": second, "end": second + 2, "before": "纪年", "after": "孙砚", "source_ids": ["S0001"], "reason": "角色引用"},
    ]
    patched, changes = _apply_editor_patches(candidate, patches, index)
    assert patched["protagonist"]["goal"] == "孙砚把柄簿多一页，孙砚再卖符"
    assert [change["start"] for change in changes] == [first, second]


def test_editor_replace_span_rejects_stale_hash_overlap_and_unlisted_path() -> None:
    candidate = {"protagonist": {"goal": "纪年把柄簿多一页"}}
    field = candidate["protagonist"]["goal"]
    base = {"source_id": "S0001", "source": "book_spec", "quote": "孙砚", "artifact_status": "approved"}
    stale = {"op": "replace_span", "path": ["protagonist", "goal"], "before_hash": source_content_hash("别的文本"), "start": 0, "end": 2, "before": "纪年", "after": "孙砚", "source_ids": ["S0001"], "reason": "过期"}
    with pytest.raises(ValueError, match="SPAN_HASH_MISMATCH"):
        _apply_editor_patches(candidate, [stale], [base])
    overlap = [
        {"op": "replace_span", "path": ["protagonist", "goal"], "before_hash": source_content_hash(field), "start": 0, "end": 2, "before": "纪年", "after": "孙砚", "source_ids": ["S0001"], "reason": "一"},
        {"op": "replace_span", "path": ["protagonist", "goal"], "before_hash": source_content_hash(field), "start": 1, "end": 3, "before": "年把", "after": "孙砚", "source_ids": ["S0001"], "reason": "二"},
    ]
    with pytest.raises(ValueError, match="SPAN_OVERLAP"):
        _apply_editor_patches(candidate, overlap, [base])
    unlisted = {"op": "replace_span", "path": ["protagonist", "relationships"], "before_hash": source_content_hash("x"), "start": 0, "end": 1, "before": "x", "after": "孙砚", "source_ids": ["S0001"], "reason": "越界"}
    with pytest.raises(ValueError, match="SPAN_PATH_INVALID"):
        _apply_editor_patches({"protagonist": {"relationships": "x"}}, [unlisted], [base])


def test_editor_replace_anchor_computes_unique_coordinates_and_preserves_context() -> None:
    field = "甲：纪年把柄簿；乙：纪年把柄簿"
    candidate = {"protagonist": {"goal": field}}
    index = [{"source_id": "S0001", "source": "book_spec", "quote": "孙砚把柄簿", "artifact_status": "approved"}]
    patched, changes = _apply_editor_patches(
        candidate,
        [{
            "op": "replace_anchor",
            "path": ["protagonist", "goal"],
            "before_hash": source_content_hash(field),
            "before": "纪年把柄簿",
            "context_before": "甲：",
            "context_after": "；",
            "after": "孙砚把柄簿",
            "source_ids": ["S0001"],
            "reason": "唯一上下文锚点",
        }],
        index,
    )
    assert patched["protagonist"]["goal"] == "甲：孙砚把柄簿；乙：纪年把柄簿"
    assert changes[0]["op"] == "replace_anchor"
    assert changes[0]["start"] == 2
    assert changes[0]["end"] == 7


def test_editor_replace_anchor_rejects_ambiguous_missing_and_coordinate_fields() -> None:
    field = "纪年把柄簿；纪年把柄簿"
    candidate = {"protagonist": {"goal": field}}
    base = {"source_id": "S0001", "source": "book_spec", "quote": "孙砚把柄簿", "artifact_status": "approved"}
    ambiguous = {
        "op": "replace_anchor", "path": ["protagonist", "goal"],
        "before_hash": source_content_hash(field), "before": "纪年把柄簿", "after": "孙砚把柄簿",
        "source_ids": ["S0001"], "reason": "歧义",
    }
    with pytest.raises(ValueError, match="ANCHOR_AMBIGUOUS"):
        _apply_editor_patches(candidate, [ambiguous], [base])
    missing = {**ambiguous, "before": "不存在"}
    with pytest.raises(ValueError, match="ANCHOR_NOT_FOUND"):
        _apply_editor_patches(candidate, [missing], [base])
    with_coordinates = {**ambiguous, "start": 0, "end": 5}
    with pytest.raises(ValueError, match="ANCHOR_INVALID"):
        _apply_editor_patches(candidate, [with_coordinates], [base])
    with pytest.raises(ValueError, match="ANCHOR_PATH_INVALID"):
        _apply_editor_patches(
            {"protagonist": {"social_network": {"enemies": [{"name": "旧名"}]}}, "conflict_map": [{"character_a": "旧名"}]},
            [{**ambiguous, "path": ["protagonist", "social_network", "enemies", 0, "name"], "before": "旧名", "after": "新名"}],
            [{"source_id": "S0001", "source": "book_spec", "quote": "新名", "artifact_status": "approved"}],
        )


def test_v4_editor_raw_replay_still_rejects_full_name_as_anchor() -> None:
    report_path = Path(__file__).resolve().parents[2] / "reports/bestseller-assessment-2026-09-06/cast-source-review-v4-result.json"
    if not report_path.exists():
        pytest.skip("real v4 report is outside this isolated fixture")
    report = json.loads(report_path.read_text())
    editor_raw = report["reviews"][0]["raw_response"]
    editor = parse_review_json(editor_raw)
    with pytest.raises(ValueError, match="ANCHOR_PATH_INVALID"):
        _apply_editor_patches(report["candidate_cast"], editor["patches"], report["audit"]["source_index"])


def test_editor_accepts_scalar_and_context_anchors_with_exact_approved_after_quotes() -> None:
    candidate = {
        "antagonist": {"name": "UnrelatedOldName"},
        "protagonist": {
            "goal": "L:token:R; M:token:N",
            "obvious_flaw": "cost_old",
        },
    }
    index = [
        {"source_id": "S1", "source": "book_spec", "quote": "UnrelatedNewName", "artifact_status": "approved"},
        {"source_id": "S2", "source": "book_spec", "quote": "L:ALPHA:R", "artifact_status": "approved"},
        {"source_id": "S3", "source": "world_spec", "quote": "M:BETA:N", "artifact_status": "approved"},
        {"source_id": "S4", "source": "world_spec", "quote": "cost_new", "artifact_status": "approved"},
    ]
    field = candidate["protagonist"]["goal"]
    patches = [
        {"path": ["antagonist", "name"], "before": "UnrelatedOldName", "after": "UnrelatedNewName", "source_ids": ["S1"], "reason": "完整身份字段"},
        {"op": "replace_anchor", "path": ["protagonist", "goal"], "before_hash": source_content_hash(field), "before": "token", "context_before": "L:", "context_after": ":R", "after": "ALPHA", "source_ids": ["S2"], "reason": "第一处上下文锚点"},
        {"op": "replace_anchor", "path": ["protagonist", "goal"], "before_hash": source_content_hash(field), "before": "token", "context_before": "M:", "context_after": ":N", "after": "BETA", "source_ids": ["S3"], "reason": "第二处上下文锚点"},
        {"op": "replace_anchor", "path": ["protagonist", "obvious_flaw"], "before_hash": source_content_hash("cost_old"), "before": "cost_old", "after": "cost_new", "source_ids": ["S4"], "reason": "精确代价证据"},
    ]
    patched, changes = _apply_editor_patches(candidate, patches, index)
    assert patched["antagonist"]["name"] == "UnrelatedNewName"
    assert patched["protagonist"]["goal"] == "L:ALPHA:R; M:BETA:N"
    assert patched["protagonist"]["obvious_flaw"] == "cost_new"
    assert "op" not in changes[0]
    assert [change["op"] for change in changes[1:]] == ["replace_anchor", "replace_anchor", "replace_anchor"]


def test_patch_targets_include_only_current_slot_bound_historical_null_target() -> None:
    candidate = {"supporting_cast": [{"name": "陈瑜", "power_tier": "未核验"}]}
    historical = [{
        "source": "historical_cast", "source_id": "H1", "artifact_status": "approved",
        "path": "supporting_cast[0].power_tier", "slot_path": "supporting_cast[0]",
        "slot_name": "陈瑜", "value_kind": "approved_null", "artifact_id": "artifact-1", "artifact_hash": "hash-1",
    }]
    targets = _patch_targets(candidate, historical)
    target = next(item for item in targets if item["path"] == ["supporting_cast", 0, "power_tier"])
    assert target["allowed_operation"] == "scalar"
    assert target["value_kind"] == "approved_null"
    assert target["source_ids"] == ["H1"]
    assert "before_hash" not in target
    wrong_slot = [{**historical[0], "slot_name": "别的角色"}]
    assert not any(item["path"] == ["supporting_cast", 0, "power_tier"] for item in _patch_targets(candidate, wrong_slot))


def test_editor_supports_role_reference_high_risk_and_historical_approved_null_only() -> None:
    candidate = {
        "protagonist": {"name": "纪年", "decision_policy": {"high_risk_allowances": "胆气赌注"}},
        "conflict_map": [{"character_a": "纪年", "character_b": "纪年"}],
        "supporting_cast": [{"name": "陈瑜", "power_tier": "错误修为猜测"}],
    }
    index = [
        {"source_id": "S0001", "source": "premise", "quote": "纪年与孙砚对质。", "artifact_status": "approved"},
        {"source_id": "S0002", "source": "world_spec", "quote": "能力不用不废、用多不衰。", "artifact_status": "approved"},
        {"source_id": "H0001_power_tier", "source": "historical_cast", "quote": "", "path": "supporting_cast[0].power_tier", "slot_path": "supporting_cast[0]", "slot_name": "陈瑜", "value_kind": "approved_null", "artifact_status": "approved", "artifact_hash": "history-hash"},
    ]
    patched, changes = _apply_editor_patches(candidate, [
        {"path": ["conflict_map", 0, "character_b"], "before": "纪年", "after": "孙砚", "source_ids": ["S0001"], "reason": "绑定approved反派引用"},
        {"path": ["protagonist", "decision_policy", "high_risk_allowances"], "before": "胆气赌注", "after": "能力不用不废、用多不衰。", "source_ids": ["S0002"], "reason": "移除未批准胆气资源"},
        {"path": ["supporting_cast", 0, "power_tier"], "before": "错误修为猜测", "after": None, "value_kind": "approved_null", "source_ids": ["H0001_power_tier"], "reason": "历史approved Cast明确缺省"},
    ], index)
    assert patched["conflict_map"][0]["character_b"] == "孙砚"
    assert patched["protagonist"]["decision_policy"]["high_risk_allowances"] == "能力不用不废、用多不衰。"
    assert patched["supporting_cast"][0]["power_tier"] is None
    assert len(changes) == 3
    with pytest.raises(ValueError, match="APPROVED_NULL_UNEVIDENCED"):
        _apply_editor_patches(candidate, [{"path": ["supporting_cast", 0, "power_tier"], "before": "错误修为猜测", "after": None, "value_kind": "approved_null", "source_ids": ["S0001"], "reason": "伪造null"}], index)


def test_historical_evidence_requires_approved_version_before_current_and_exact_hash() -> None:
    old = SimpleNamespace(id=UUID(int=2), version_no=1, status="approved", content={"supporting_cast": [{"name": "陈瑜", "power_tier": None}]})
    current = SimpleNamespace(id=UUID(int=3), version_no=3, status="approved", content={})
    missing = SimpleNamespace(id=UUID(int=6), version_no=2, status="approved", content={"supporting_cast": [{"name": "陈瑜"}]})
    rows = build_historical_cast_evidence(current, [old, missing, SimpleNamespace(id=UUID(int=4), version_no=4, status="approved", content=old.content), SimpleNamespace(id=UUID(int=5), version_no=2, status="draft", content=old.content)])
    assert rows
    assert {row["artifact_id"] for row in rows} == {str(old.id), str(missing.id)}
    assert any(row["value_kind"] == "approved_null" for row in rows)
    assert any(row["value_kind"] == "approved_missing" for row in rows)
    assert all(row["artifact_status"] == "approved" and row["artifact_hash"] for row in rows)
    assert len({row["source_id"] for row in rows}) == len(rows)


def test_historical_replay_rejects_changed_source_and_wrong_slot_null() -> None:
    old = SimpleNamespace(
        id=UUID(int=12), version_no=1, status="approved",
        content={"supporting_cast": [{"name": "陈瑜", "power_tier": None}]},
    )
    current = SimpleNamespace(id=UUID(int=13), version_no=2, status="approved", content={})
    historical = build_historical_cast_evidence(current, [old])
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
        historical_cast_evidence=historical,
    )
    from bestseller.services.derived_cast_source_review import verify_prepared_cast_source_review
    changed = [dict(row) for row in historical]
    changed[0]["artifact_hash"] = "tampered"
    with pytest.raises(ValueError, match="HISTORICAL_EVIDENCE_TAMPERED"):
        verify_prepared_cast_source_review(
            {"editor_receipt": {}}, base_candidate=candidate, base_audit=audit,
            historical_cast_evidence=changed,
        )
    wrong_slot = next(row for row in historical if row["value_kind"] == "approved_null")
    with pytest.raises(ValueError, match="APPROVED_NULL_UNEVIDENCED"):
        _apply_editor_patches(
            {"supporting_cast": [{"name": "陈瑜", "power_tier": "猜测"}, {"name": "旧人", "power_tier": "猜测"}]},
            [{"path": ["supporting_cast", 1, "power_tier"], "before": "猜测", "after": None, "value_kind": "approved_null", "source_ids": [wrong_slot["source_id"]], "reason": "错误slot"}],
            [wrong_slot],
        )
    with pytest.raises(ValueError, match="APPROVED_NULL_UNEVIDENCED"):
        _apply_editor_patches(
            {"supporting_cast": [{"name": "换人", "power_tier": "猜测"}]},
            [{"path": ["supporting_cast", 0, "power_tier"], "before": "猜测", "after": None, "value_kind": "approved_null", "source_ids": [wrong_slot["source_id"]], "reason": "同path异角色"}],
            [wrong_slot],
        )


def test_review_json_accepts_only_one_outer_json_fence_and_rejects_extra_text() -> None:
    assert parse_review_json('```json\n{"patches": []}\n```')["patches"] == []
    with pytest.raises(ValueError, match="RAW_RESPONSE_INVALID"):
        parse_review_json('说明\n```json\n{"patches": []}\n```')
    with pytest.raises(ValueError, match="RAW_RESPONSE_INVALID"):
        parse_review_json('```json\n{"patches": []}\n```\n```json\n{}\n```')


def test_review_requires_all_axes_two_pass_receipts_and_bound_hash() -> None:
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    receipt = _receipt(audit)
    verify_cast_source_review(receipt, candidate=candidate, audit=audit)


def test_review_rejects_non_aggregated_reason_over_160_chars() -> None:
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    receipt = _receipt(audit)
    receipt["checks"][0]["reason"] = "证据" * 81
    receipt["raw_response"] = json.dumps({
        "candidate_hash": receipt["candidate_hash"],
        "review_input_hash": receipt["review_input_hash"],
        "checks": receipt["checks"],
    }, ensure_ascii=False)
    with pytest.raises(ValueError, match="REASON_TOO_LONG"):
        verify_cast_source_review(receipt, candidate=candidate, audit=audit)


def test_bundle_requires_two_distinct_real_provider_receipts() -> None:
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    verify_cast_source_review_bundle([_receipt(audit), _receipt(audit)], candidate=candidate, audit=audit)


def test_bundle_rejects_one_receipt_or_uncertain_axis() -> None:
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    base = _receipt(audit)
    with pytest.raises(ValueError, match="TWO_RECEIPTS"):
        verify_cast_source_review_bundle([{**base, "review_id": "r1"}], candidate=candidate, audit=audit)


def test_review_rejects_missing_hash_same_run_mock_and_tampered_raw_response() -> None:
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    with pytest.raises(ValueError, match="INPUT_TAMPERED"):
        verify_cast_source_review({k: v for k, v in _receipt(audit).items() if k != "review_input_hash"}, candidate=candidate, audit=audit)
    same = uuid4()
    with pytest.raises(ValueError, match="NOT_INDEPENDENT"):
        verify_cast_source_review_bundle([_receipt(audit, run_id=same), _receipt(audit, run_id=same)], candidate=candidate, audit=audit)
    with pytest.raises(ValueError, match="PROVIDER_UNAVAILABLE"):
        verify_cast_source_review_bundle([_receipt(audit, provider="mock"), _receipt(audit)], candidate=candidate, audit=audit)
    with pytest.raises(ValueError, match="CHECKS_TAMPERED"):
        verify_cast_source_review({**_receipt(audit), "raw_response": json.dumps({"checks": []})}, candidate=candidate, audit=audit)
    forged = _receipt(audit)
    forged["raw_response"] = json.dumps({
        "candidate_hash": "forged",
        "review_input_hash": audit["review_input_hash"],
        "checks": forged["checks"],
    })
    with pytest.raises(ValueError, match="CHECKS_TAMPERED"):
        verify_cast_source_review(forged, candidate=candidate, audit=audit)


def test_source_index_excludes_control_metadata_and_requires_cross_domain_evidence() -> None:
    sources = _sources()
    sources["premise"] = {"text": sources["premise"], "_meta": {"secret": "do not cite"}, "audit": "ignore"}
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=sources,
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    assert all("secret" not in row["quote"] for row in audit["source_index"])
    checks = _checks(audit)
    premise_id = next(row["source_id"] for row in audit["source_index"] if row["source"] == "premise")
    world_id = next(row["source_id"] for row in audit["source_index"] if row["source"] == "world_spec")
    checks[0]["source_ids"] = [premise_id, world_id]
    receipt = _receipt(audit, checks=checks, raw_response=json.dumps({"candidate_hash": audit["candidate_cast_hash"], "review_input_hash": audit["review_input_hash"], "checks": checks}, ensure_ascii=False))
    with pytest.raises(ValueError, match="SOURCE_COVERAGE"):
        verify_cast_source_review(receipt, candidate=candidate, audit=audit)


@pytest.mark.parametrize("rename", [False, True])
async def test_prepare_review_fake_llm_generates_and_consumes_two_real_receipts(monkeypatch: pytest.MonkeyPatch, rename: bool) -> None:
    from copy import deepcopy

    from bestseller.services.derived_cast_source_review import (
        prepare_cast_source_review,
        verify_prepared_cast_source_review,
    )

    sources = _sources()
    sources["premise"] += "县衙孙砚扣住了契约。"
    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=sources,
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    calls = 0
    prompts: list[str] = []

    async def fake_complete(_session: object, _settings: object, request: object) -> object:
        nonlocal calls
        calls += 1
        prompts.append(request.user_prompt)
        if request.user_prompt.startswith("editor"):
            editor_spec = json.loads(request.user_prompt.split("\n", 1)[1])
            assert editor_spec["patch_targets"]
            assert all(item["allowed_operation"] in {"scalar", "replace_anchor"} for item in editor_spec["patch_targets"])
            assert all("before_hash" not in item for item in editor_spec["patch_targets"] if item["allowed_operation"] == "scalar")
            assert all(
                item["allowed_operation"] == "scalar"
                or item["before_hash"] == source_content_hash(item["value"])
                for item in editor_spec["patch_targets"]
            )
            anchor_schema_text = json.dumps(editor_spec["anchor_patch_schema"], ensure_ascii=False)
            assert "纪年" not in anchor_schema_text
            assert "孙砚" not in anchor_schema_text
            assert "patch_targets" in editor_spec["rules"]
            assert "禁止自行计算或臆造" in editor_spec["rules"]
            assert "replace_anchor" in editor_spec["rules"]
            assert "新编辑必须优先使用replace_anchor" in editor_spec["rules"]
            assert "replace_span仅用于既有旧回执" in editor_spec["rules"]
            assert "响应patch禁止携带allowed_operation字段" in editor_spec["rules"]
            assert "before不是整字段" in editor_spec["rules"]
            assert "span_patch_schema" not in editor_spec
            assert "legacy_cast" not in editor_spec
            assert "compiler_hint_index" not in editor_spec
            name_target = next(item for item in editor_spec["patch_targets"] if item["path"] == ["antagonist", "name"])
            assert name_target["allowed_operation"] == "scalar"
            patches = [{"path": name_target["path"], "before": name_target["value"], "after": "孙砚", "source_ids": [editor_spec["source_index"][0]["source_id"]], "reason": "修复主反派同名污染"}] if rename else []
            editor_payload = json.dumps({"base_candidate_hash": editor_spec["base_candidate_hash"], "review_input_hash": editor_spec["review_input_hash"], "patches": patches})
            return SimpleNamespace(
                llm_run_id=uuid4(), provider="real-provider", model_name="review-model",
                fallback_used=False, finish_reason="stop", content=f"```json\n{editor_payload}\n```",
            )
        spec = json.loads(request.user_prompt.split("\n", 1)[1])
        assert spec["response_schema"]["candidate_hash"] == spec["candidate_hash"]
        assert spec["response_schema"]["review_input_hash"] == spec["review_input_hash"]
        assert [item["axis"] for item in spec["response_schema"]["checks"]] == ["identity_binding", "source_fidelity", "mechanism_and_cost", "dependency_scope"]
        assert len(spec["response_schema"]["checks"]) == 4
        assert "恰好包含四个checks" in spec["rules"]
        assert "每个issue追加axis" in spec["rules"]
        actual_candidate_rows: list[dict[str, str]] = []
        _walk_source(spec["candidate"], "candidate", "candidate", actual_candidate_rows)
        indexed_candidate_rows = [row for row in spec["source_index"] if row.get("source") == "candidate"]
        assert all(set(row) <= {"source_id", "source", "path", "quote", "value_kind", "slot_path", "slot_name", "artifact_status"} for row in spec["source_index"])
        assert len({row["path"] for row in indexed_candidate_rows}) == len(indexed_candidate_rows)
        assert {(row["path"], row["quote"]) for row in indexed_candidate_rows} == {
            (row["path"], row["quote"]) for row in actual_candidate_rows
        }
        assert not any(row.get("source") == "compiler_hint" for row in spec["source_index"])
        assert "legacy_cast" not in spec
        assert "compiler_hint_index" not in spec
        checks = _checks(spec)
        return SimpleNamespace(
            llm_run_id=uuid4(), provider="real-provider", model_name="review-model",
            fallback_used=False, finish_reason="stop",
            content=json.dumps({
                "review_input_hash": spec["review_input_hash"],
                "candidate_hash": spec["candidate_hash"],
                "checks": checks,
            }, ensure_ascii=False),
        )

    monkeypatch.setattr("bestseller.services.llm.complete_text", fake_complete)
    report = await prepare_cast_source_review(None, None, project_id=UUID(int=1), candidate=candidate, audit=audit)
    assert calls == 3
    assert report["status"] == "review_passed"
    assert report["phase"] == "prepare-cast-source-review"
    assert report["budget"] == {"max_calls": 3, "calls": 3}
    assert '"patches"' in prompts[0]
    assert report["editor_receipt"]["raw_response"].startswith("```json\n")
    assert "pass|revise|uncertain" in prompts[1]
    assert "证据不足、冲突或越界时必须判revise/uncertain" in prompts[1]
    assert "legacy_cast只是保留审计" in prompts[1]
    assert "hard_conflict" in prompts[1]
    assert "合理派生配角" in prompts[1]
    assert "candidate只能证明待审字段的存在" in prompts[1]
    replayed, rebuilt_audit = verify_prepared_cast_source_review(report, base_candidate=candidate, base_audit=audit)
    assert rebuilt_audit == report["audit"]
    assert replayed["antagonist"]["name"] == ("孙砚" if rename else "纪年")
    assert replayed["supporting_cast"] == candidate["supporting_cast"]
    if rename:
        assert report["audit"]["review_input_hash"] != audit["review_input_hash"]
    for domain in ("premise", "candidate"):
        tampered = deepcopy(report)
        row = next(r for r in tampered["audit"]["source_index"] if r["source"] == domain)
        row["quote"] = "伪造证据"
        with pytest.raises(ValueError, match="AUDIT_TAMPERED"):
            verify_prepared_cast_source_review(tampered, base_candidate=candidate, base_audit=audit)


async def test_prepare_review_preserves_partial_receipts_and_actual_budget_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from bestseller.services.derived_cast_source_review import prepare_cast_source_review

    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    calls = 0

    async def fake_complete(_session: object, _settings: object, _request: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(
                llm_run_id=uuid4(), provider="real-provider", model_name="review-model",
                fallback_used=False, finish_reason="stop", content="{bad-json",
            )
        if calls == 2:
            return SimpleNamespace(
                llm_run_id=uuid4(), provider="real-provider", model_name="review-model",
                fallback_used=False, finish_reason="stop", content="{bad-json",
            )
        raise TimeoutError("second-review-timeout")

    monkeypatch.setattr("bestseller.services.llm.complete_text", fake_complete)
    report = await prepare_cast_source_review(None, None, project_id=UUID(int=1), candidate=candidate, audit=audit)
    assert report["status"] == "needs_review"
    assert report["budget"] == {"max_calls": 3, "calls": 3}
    assert len(report["reviews"]) == 2
    assert "second-review-timeout" in " ".join(report["errors"])


async def test_failed_editor_critics_receive_actual_candidate_index(monkeypatch: pytest.MonkeyPatch) -> None:
    from bestseller.services.derived_cast_source_review import prepare_cast_source_review

    candidate, audit = build_cast_recovery_candidate(
        _compiled(), _legacy(), approved_sources=_sources(),
        parent_ids={"book": "book-1", "world": "world-1", "premise": "premise-1"},
        revision_report_id="revision-1", legacy_artifact_id="legacy-1",
    )
    prompts: list[str] = []

    async def fake_complete(_session: object, _settings: object, request: object) -> object:
        prompts.append(request.user_prompt)
        spec = json.loads(request.user_prompt.split("\n", 1)[1])
        if request.user_prompt.startswith("editor"):
            field = next(item for item in spec["patch_targets"] if item["path"] == ["protagonist", "goal"])
            source_id = spec["source_index"][0]["source_id"]
            payload = {
                "base_candidate_hash": spec["base_candidate_hash"],
                "review_input_hash": spec["review_input_hash"],
                "patches": [{
                    "op": "replace_span", "path": field["path"], "before_hash": field["before_hash"],
                    "start": 0, "end": 999, "before": "", "after": "孙砚",
                    "source_ids": [source_id], "reason": "故意触发坐标拒绝",
                }],
            }
            return SimpleNamespace(
                llm_run_id=uuid4(), provider="real-provider", model_name="review-model",
                fallback_used=False, finish_reason="stop", content=json.dumps(payload, ensure_ascii=False),
            )
        actual: list[dict[str, str]] = []
        _walk_source(spec["candidate"], "candidate", "candidate", actual)
        indexed = [row for row in spec["source_index"] if row.get("source") == "candidate"]
        assert len({row["path"] for row in indexed}) == len(indexed)
        assert {(row["path"], row["quote"]) for row in indexed} == {(row["path"], row["quote"]) for row in actual}
        assert not any(row.get("source") == "compiler_hint" for row in spec["source_index"])
        checks = _checks(spec)
        return SimpleNamespace(
            llm_run_id=uuid4(), provider="real-provider", model_name="review-model",
            fallback_used=False, finish_reason="stop",
            content=json.dumps({"review_input_hash": spec["review_input_hash"], "candidate_hash": spec["candidate_hash"], "checks": checks}),
        )

    monkeypatch.setattr("bestseller.services.llm.complete_text", fake_complete)
    report = await prepare_cast_source_review(None, None, project_id=UUID(int=1), candidate=candidate, audit=audit)
    assert report["status"] == "needs_review"
    assert any("SPAN_RANGE_INVALID" in error for error in report["errors"])
    assert len(prompts) == 3
