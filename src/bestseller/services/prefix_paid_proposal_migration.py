"""Zero-provider extraction of one historical over-capacity patch proposal.

Old attempts stay invalid/spent. A new server candidate is not an approved plan.
Apply owns atomic audit/state writes in a savepoint; caller owns outer commit.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN202, ANN401
import ast
from copy import deepcopy
import hashlib
import inspect
import json
from uuid import NAMESPACE_URL, UUID, uuid5
from types import SimpleNamespace

from sqlalchemy import select

from bestseller.infra.db.models import (
    ProjectModel,
    WorkflowRunModel,
    ChapterModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ExecutionAttemptModel,
    LlmRunModel,
)
from bestseller.services import chapter_outline_readiness as prefix
from bestseller.services import planner, llm, retained_review_input_proof as physical
from bestseller.services import outline_llm_judge, source_bound_outline_integrity as integrity
from bestseller.services.retained_candidate_continuation import _payload
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.services.budget import current_execution_scope, execution_operation_id
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.outline_llm_judge import render_outline_source_brief

POLICY = "prefix-paid-proposal-migration.v1"
CAPACITY_POLICY = "registered-outline-targets.v2"
_BODY = prefix._body
_body = prefix._body
_PROJECTION_REPAIR_RULES = prefix._PROJECTION_REPAIR_RULES


def _need(value, code):
    if not value:
        raise ValueError("prefix_paid_proposal_" + code)


def _sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _sha_text(value):
    return _sha_bytes(value.encode())


def _row(row):
    from sqlalchemy import inspect as orm_inspect

    return json.loads(
        json.dumps(
            {c.key: deepcopy(getattr(row, c.key)) for c in orm_inspect(type(row)).column_attrs},
            default=str,
            allow_nan=False,
        )
    )


def _load(text):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            _need(key not in out, "duplicate_json_key")
            out[key] = value
        return out

    def invalid(_):
        raise ValueError("prefix_paid_proposal_nonfinite_json")

    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid)


_PROMPT_ORDER_POLICY = "prefix-prompt-object-order-proof.v1"


def _prompt_order_blocks(user):
    markers = (
        "\n已采用前文：",
        "\n\n[SOURCE AUTHORITY]\n",
        "\n[/SOURCE AUTHORITY]",
        "\n[EVALUATION SCOPE]\n",
        "\n[/EVALUATION SCOPE]",
        "\n当前候选：",
        "\n来源约束诊断：",
        "\n严格补丁协议：",
        "\n此前无效补丁诊断：",
        "\n返回 ",
    )
    _need(all(user.count(m) == 1 for m in markers), "prompt_order_delimiters")
    positions = [user.index(m) for m in markers]
    _need(positions == sorted(positions), "prompt_order_boundaries")
    specs = (
        ("evaluation_scope_json", markers[3], dict),
        ("candidate", markers[5], dict),
        ("prior_invalid_diagnostics", markers[8], list),
    )
    blocks = {}
    for name, marker, expected_type in specs:
        start = user.index(marker) + len(marker)
        try:
            _, length = json.JSONDecoder().raw_decode(user[start:])
            raw = user[start : start + length]
            value = _load(raw)
            serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("prefix_paid_proposal_prompt_order_invalid_json") from exc
        _need(type(value) is expected_type and raw == serialized, "prompt_order_serializer_changed")
        blocks[name] = (start, start + length, raw, value)
    return blocks


def _prompt_object_order_proof(original, current):
    """Permit object member ordering in three complete renderer JSON blocks only.

    This does not normalize a paid request or its effective hash. All bytes
    outside those JSON blocks, including full Source Authority, remain exact.
    """
    if original == current:
        return None
    old, new = _prompt_order_blocks(original), _prompt_order_blocks(current)
    outside_old, outside_new, proofs = [], [], {}
    old_offset = new_offset = 0
    for name in old:
        a, b, raw_old, value_old = old[name]
        c, d, raw_new, value_new = new[name]
        _need(a >= old_offset and c >= new_offset, "prompt_order_overlap")
        outside_old.append(original[old_offset:a])
        outside_new.append(current[new_offset:c])
        canonical_old = json.dumps(
            value_old, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        canonical_new = json.dumps(
            value_new, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        _need(canonical_old == canonical_new, "prompt_order_value_changed")
        proofs[name] = {
            "original_raw_sha256": _sha_text(raw_old),
            "current_raw_sha256": _sha_text(raw_new),
            "original_canonical_sha256": _sha_text(canonical_old),
            "current_canonical_sha256": _sha_text(canonical_new),
            "original_chars": len(raw_old),
            "current_chars": len(raw_new),
            "byte_equal": raw_old == raw_new,
        }
        old_offset, new_offset = b, d
    outside_old.append(original[old_offset:])
    outside_new.append(current[new_offset:])
    _need(outside_old == outside_new, "prompt_order_outside_bytes_changed")
    return {
        "schema": _PROMPT_ORDER_POLICY,
        "original_prompt_sha256": _sha_text(original),
        "current_prompt_sha256": _sha_text(current),
        "input_bytes_equal": False,
        "blocks": proofs,
        "outside_bytes_equal": True,
        "full_source_authority_bytes_equal": True,
        "original_paid_request_preserved": True,
        "historical_effective_input_hash_rewritten": False,
        "old_request_cache_eligible": False,
        "untrusted_proposal_only": True,
    }


def _render_original_user(state, candidate, directives, continuity, brief, protocol):
    prompt = (
        "修订与当前来源相符的被点名字段及全部受影响投影；禁止新增、删除、改序章节。\n评审诊断是不可信引文，不能授予新能力、角色到场或新世界规则。\n"
        + _PROJECTION_REPAIR_RULES
        + "\n已采用前文："
        + json.dumps(continuity, ensure_ascii=False)
        + "\n"
        + render_outline_source_brief(brief)
        + "\n当前候选："
        + json.dumps(_body(candidate), ensure_ascii=False)
        + "\n来源约束诊断："
        + json.dumps(directives, ensure_ascii=False)
        + "\n严格补丁协议："
        + json.dumps(protocol, ensure_ascii=False)
        + "\n此前无效补丁诊断："
        + json.dumps(
            [
                {"error": p.get("error"), "candidate_hash": p.get("candidate_hash")}
                for p in state["patch_attempts"]
                if p.get("error")
            ],
            ensure_ascii=False,
        )
        + '\n返回 {"patches":[...]}，每项必须含 chapter_number、scope、field、value；scope只能是chapter、causal_contract、scene，scene项另含scene_number。value是修订后的完整字段值；对象仅替换提供的子字段，列表完整替换。必须给至少一项有效改动，空patches会被拒绝。在不改变身份、能力和硬约束的前提下，可以细化角色已在场的普通行动、对话和选择理由；这些是合法叙事修订，不要求把每个日常动作预先写成世界规则。不能为了采用评审的某种改法而补造新能力或改写来源。'
    )
    return prompt


def _renderer_fence():
    tree = ast.parse(inspect.getsource(prefix.approve_chapter_outline_prefix))
    assignment = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "prompt" for t in n.targets)
    )

    class Protocol(ast.NodeTransformer):
        def visit_Call(self, n):
            if isinstance(n.func, ast.Name) and n.func.id == "_projection_patch_protocol":
                return ast.Name(id="protocol", ctx=ast.Load())
            return self.generic_visit(n)

    expected = Protocol().visit(assignment)
    actual = ast.parse(inspect.getsource(_render_original_user)).body[0].body[0]
    _need(
        ast.dump(expected, include_attributes=False) == ast.dump(actual, include_attributes=False),
        "original_prompt_renderer_changed",
    )


def _old_protocol(user, parent):
    marker = "\n严格补丁协议："
    _need(user.count(marker) == 1, "protocol_not_unique")
    tail = user.split(marker, 1)[1]
    old, end = json.JSONDecoder().raw_decode(tail)
    _need(tail[end:].startswith("\n此前无效补丁诊断："), "protocol_boundary")
    current = prefix._projection_patch_protocol(parent)
    expected = deepcopy(current)
    _need(expected.pop("capacity_policy", None) == CAPACITY_POLICY, "capacity_policy_unknown")
    expected["max_patches"] = 12
    _need(old == expected, "old_protocol_not_capacity_only")
    return old, current


def _namespace(state, wid, parent_hash, index):
    _need(state.get("operation_binding_version") == "workflow-candidate-v1", "operation_binding")
    with prefix._prefix_operation(state, wid, "repair", parent_hash, index):
        namespace, slot = llm._semantic_operation_namespace.get()
    _need(slot == 0, "operation_slot")
    return namespace


async def _paid(
    session, settings, project, workflow, state, attempt, index, scope, parent, brief, continuity
):
    rid = UUID(attempt["completion"]["llm_run_id"])
    run = await session.get(LlmRunModel, rid, populate_existing=True)
    _need(
        run
        and run.project_id == project.id
        and run.workflow_run_id == workflow.id
        and run.logical_role == "planner"
        and run.prompt_template == "chapter_outline_prefix_surgical_repair"
        and run.prompt_version == "2"
        and run.finish_reason == "stop",
        "paid_run_binding",
    )
    request_file, request_sha = _payload(settings, run, "request")
    response_file, response_sha = _payload(settings, run, "response")
    request = physical._retained_request(settings, run)
    _need(
        request.project_id == project.id
        and request.workflow_run_id == workflow.id
        and request.logical_role == "planner"
        and request.prompt_template == run.prompt_template
        and request.prompt_version == run.prompt_version
        and request.max_tokens_override == 12000
        and not request.tools
        and request.tool_choice is None,
        "retained_request_contract",
    )
    old_protocol, current_protocol = _old_protocol(request.user_prompt, parent)
    _renderer_fence()
    expected_brief = deepcopy(brief)
    # The caller built brief with the same actual original candidate and full sources.
    _need(
        expected_brief.get("prefix_evaluation_scope")
        == {
            **state["phase_scope"],
            "approved_scope_only": state["chapter_numbers"],
            "full_window": state["binding"]["window"],
            "full_source_hash": state["identity"]["source_hash"],
            "tail_status": "unapproved; no complete-volume claim",
        },
        "evaluation_scope",
    )
    original = deepcopy(state)
    original["patch_attempts"] = deepcopy(state["patch_attempts"][: index - 1])
    grade = prefix.LLMQualityJudgeResult.model_validate(
        next(
            c["report"]
            for c in state["candidates"]
            if c["candidate_hash"] == attempt["parent_hash"]
        )
    )
    directives = prefix.build_outline_repair_directives(grade, source_authority=True)
    expected_user = _render_original_user(
        original, parent, directives, continuity, expected_brief, old_protocol
    )
    expected_system = (
        "你是章纲定点修订编辑，只输出JSON补丁。"
        + prefix.OUTLINE_SOURCE_AUTHORITY_RULES
        + prefix._PROJECTION_REPAIR_RULES
    )
    _need(request.system_prompt == expected_system, "paid_source_or_prompt_changed")
    prompt_order_proof = _prompt_object_order_proof(request.user_prompt, expected_user)
    catalog, catalog_sha = physical._fresh_catalog()
    role = physical._fresh_role(settings, project, request, catalog)
    effective_hash = llm._effective_input_hash(request, role)
    paid, key = await physical._physical_return(
        session, run, scope=scope, expected_hash=effective_hash, expected_model=role.model
    )
    namespace = _namespace(state, workflow.id, attempt["parent_hash"], index)
    opid = execution_operation_id(
        namespace,
        subject=f"planner:{run.prompt_template}:{run.prompt_version}",
        revision=effective_hash,
    )
    _need(key == _sha_text(f"{scope}\n{opid}:sample:0"), "paid_operation_namespace")
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key == key)
        )
    )
    recorded = (run.metadata_json or {}).get("execution_receipt", {}).get("attempts", [])
    # Explicitly require the full, closed attempt set, never select just a winning return.
    _need(
        len(attempts) == len(recorded) == 1
        and attempts[0].status == "completed"
        and {a.id for a in attempts} == {a.get("attempt_id") for a in recorded},
        "paid_full_attempt_set",
    )
    _need(
        not paid.tool_calls
        and paid.finish_reason == "stop"
        and paid.raw_message == attempt["completion"].get("raw_message"),
        "paid_raw_or_tools",
    )
    for field in (
        "content",
        "provider",
        "model_name",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "fallback_used",
        "fallback_source",
        "tool_calls",
        "raw_message",
    ):
        _need(
            getattr(paid, field)
            == getattr(llm.LLMCompletionResult.model_validate(attempt["completion"]), field),
            "saved_completion_changed",
        )
    content = response_file.get("content") or {}
    _need(
        content.get("truncated") is False
        and content.get("text") == paid.content
        and content.get("original_chars") == len(paid.content),
        "physical_response_content",
    )
    for field in (
        "provider",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "fallback_used",
        "fallback_source",
        "tool_calls",
        "raw_message",
    ):
        _need(response_file.get(field) == getattr(paid, field), "physical_response_changed")
    _need(response_file.get("model") == paid.model_name, "physical_response_model")
    for field in ("tool_calls", "raw_message"):
        value = getattr(paid, field)
        record = (response_file.get("structured_payload_receipts") or {}).get(field) or {}
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        _need(
            record.get("status") == "retained"
            and record.get("encoding") == "json-utf8"
            and record.get("truncated") is False
            and record.get("original_chars") == len(encoded)
            and record.get("sha256") == _sha_text(encoded)
            and type(record.get("limit_chars")) is int
            and record["limit_chars"] >= len(encoded),
            "physical_structured_receipt",
        )
    parsed, parse_receipt = prefix._parse_prefix_json(paid.content, allow_internal_closers=False)
    _need(
        parse_receipt["mode"] == "strict"
        and attempt["json_parse_receipt"]["mode"] == "strict"
        and all(attempt["json_parse_receipt"].get(k) == v for k, v in parse_receipt.items()),
        "strict_parse_receipt_changed",
    )
    return parsed, {
        **(
            {"prompt_object_order_proof": prompt_order_proof}
            if prompt_order_proof is not None
            else {}
        ),
        "request_file_sha256": request_sha,
        "response_file_sha256": response_sha,
        "effective_input_hash": effective_hash,
        "fresh_catalog_sha256": catalog_sha,
        "operation_key": key,
        "operation_namespace": namespace,
        "attempts_sha256": {a.id: content_hash(_row(a)) for a in attempts},
        "raw_sha256": _sha_text(paid.content),
        "old_protocol": old_protocol,
        "current_protocol": current_protocol,
        "request_sha256": content_hash(request.model_dump(mode="json")),
        "physical_tool_schema_retained": bool(request_file.get("tools")),
        "tools": request.tools,
        "tool_choice": request.tool_choice,
    }


def verify_derived_prefix_lineage(state, record):
    """Pure verifier used by the proposed prefix lineage branch; returns None."""
    ref = record.get("derived_proposal")
    _need(
        type(ref) is dict
        and set(ref) == {"schema", "audit_workflow_id", "receipt_sha256"}
        and ref["schema"] == POLICY,
        "derived_reference",
    )
    matches = [
        r
        for r in state.get("paid_proposal_migrations", [])
        if r.get("audit_workflow_id") == ref["audit_workflow_id"]
    ]
    _need(len(matches) == 1, "derived_receipt_missing_or_duplicate")
    receipt = matches[0]
    _need(content_hash(receipt) == ref["receipt_sha256"], "derived_receipt_hash")
    index = receipt["attempt_index"]
    _need(
        type(index) is int and 1 <= index <= len(state["patch_attempts"]), "derived_attempt_index"
    )
    old = state["patch_attempts"][index - 1]
    _need(
        content_hash(old) == receipt["original_attempt_sha256"]
        and old["status"] == "invalid"
        and old["consumed"] is True
        and old["error"]["message"] == "prefix_patch_capacity_exceeded",
        "derived_old_attempt_changed",
    )
    _need(
        record.get("patch_llm_run_id") == old["completion"]["llm_run_id"] == receipt["llm_run_id"]
        and state["identity"] == receipt["identity"]
        and state["foundation"] == receipt["foundation"]
        and state["binding"] == receipt["binding"]
        and receipt["new_patch_rounds"] == 0
        and receipt["grants_credit"] is False
        and receipt["adoption_authorized"] is False,
        "derived_binding",
    )
    parents = [c for c in state["candidates"] if c["candidate_hash"] == old["parent_hash"]]
    _need(
        len(parents) == 1 and content_hash(parents[0]["candidate"]) == old["parent_hash"],
        "derived_parent",
    )
    payload, parsed = prefix._parse_prefix_json(
        old["completion"]["content"], allow_internal_closers=False
    )
    _need(
        parsed["mode"] == "strict" and parsed["raw_sha256"] == receipt["physical"]["raw_sha256"],
        "derived_raw_changed",
    )
    current_protocol = prefix._projection_patch_protocol(parents[0]["candidate"])
    _need(current_protocol == receipt["physical"]["current_protocol"], "derived_capacity_changed")
    try:
        applied = prefix._apply_projection_patches(parents[0]["candidate"], payload["patches"])
    except planner.PlannerFallbackError as exc:
        raise ValueError("prefix_paid_proposal_derived_patch_invalid") from exc
    normalization = receipt.get("normalization")
    _need(type(normalization) is dict, "derived_normalization_missing")
    if normalization.get("mode") == integrity.POLICY:
        expected, _, proof = integrity.prepare_source_bound_outline(
            applied,
            volume_number=state["binding"]["volume_number"],
            expected_count=len(state["chapter_numbers"]),
            chapter_number_offset=state["chapter_numbers"][0],
            identity_manifest=normalization["identity_manifest"],
        )
        _need(proof == normalization["receipt"], "derived_normalization_changed")
    else:
        _need(normalization == {"mode": "byte_exact"}, "derived_normalization_unknown")
        expected = applied
    _need(
        integrity._json(_body(expected)) == integrity._json(_body(record["candidate"]))
        and content_hash(record["candidate"])
        == record["candidate_hash"]
        == receipt["candidate_hash"],
        "derived_candidate_changed",
    )


def _validate_current_proposal(proposed, *, project, state, cast):
    from sqlalchemy import inspect as orm_inspect

    # Current source-bound validation records runtime receipts on its argument.
    # Give it full actual columns without ORM attachment, preserving inspect 0 writes.
    view = SimpleNamespace(
        **{
            c.key: deepcopy(getattr(project, c.key))
            for c in orm_inspect(type(project)).column_attrs
        }
    )
    candidate = prefix._validate(proposed, project=view, state=state, cast=cast)
    normalization = {"mode": "byte_exact"}
    expected = proposed
    if planner._source_bound_cast_enabled(project):
        manifest = planner._chapter_outline_identity_manifest(cast)
        expected, _, receipt = integrity.prepare_source_bound_outline(
            proposed,
            volume_number=state["binding"]["volume_number"],
            expected_count=len(state["chapter_numbers"]),
            chapter_number_offset=state["chapter_numbers"][0],
            identity_manifest=manifest,
        )
        retained = (view.metadata_json.get("deterministic_audit_outline_integrity") or {}).get(
            receipt["projected_sha256"]
        )
        _need(retained == receipt, "current_validator_integrity_receipt_missing")
        normalization = {
            "mode": integrity.POLICY,
            "identity_manifest": manifest,
            "receipt": receipt,
        }
    _need(integrity._json(candidate) == integrity._json(expected), "validation_changed_proposal")
    return candidate, normalization


async def _ledger_snapshot(session, scope, *, lock=False):
    q = (
        select(ExecutionBudgetModel)
        .where(ExecutionBudgetModel.scope_id == scope)
        .execution_options(populate_existing=True, autoflush=False)
    )
    budget = await session.scalar(q.with_for_update() if lock else q)
    _need(
        budget is not None and budget.status not in {"unknown", "cancelled"},
        "financial_scope_unsettled",
    )
    ops = list(
        await session.scalars(
            select(ExecutionOperationModel)
            .where(ExecutionOperationModel.scope_id == scope)
            .execution_options(populate_existing=True, autoflush=False)
        )
    )
    attempts = list(
        await session.scalars(
            select(ExecutionAttemptModel)
            .where(ExecutionAttemptModel.scope_id == scope)
            .execution_options(populate_existing=True, autoflush=False)
        )
    )
    _need(
        all(
            op.status
            in {
                "completed",
                "stopped",
                "transport_result",
                "transport_failure_receipt",
                "reconciliation_receipt",
                "unsent_reclaim_receipt",
            }
            for op in ops
        ),
        "financial_operation_unsettled",
    )
    _need(
        all(
            a.status in {"completed", "not_sent", "rejected", "reconciled_charged"}
            for a in attempts
        ),
        "financial_attempt_unsettled",
    )
    keys = {op.key for op in ops}
    _need(all(a.operation_key in keys for a in attempts), "financial_attempt_orphan")
    return {
        "budget": _row(budget),
        "operations": {r.key: content_hash(_row(r)) for r in ops},
        "attempts": {a.id: content_hash(_row(a)) for a in attempts},
    }


async def _inspect(session, settings, args, *, lock=False):
    def query(model, rid):
        q = (
            select(model)
            .where(model.id == rid)
            .execution_options(populate_existing=True, autoflush=False)
        )
        return q.with_for_update() if lock else q

    project = await session.scalar(query(ProjectModel, args["project_id"]))
    workflow = await session.scalar(query(WorkflowRunModel, args["workflow_run_id"]))
    _need(project and workflow and prefix._workflow_scope(workflow, project.id), "workflow_scope")
    state = deepcopy(workflow.metadata_json or {})
    scope = project_execution_scope(project)
    _need(current_execution_scope() in {None, scope}, "scope_changed")
    ledger = await _ledger_snapshot(session, scope, lock=lock)
    _need(
        state.get("schema") == prefix.SCHEMA
        and state["identity"].get("schema") == prefix.SCHEMA
        and state["identity"].get("project_id") == str(project.id)
        and state["identity"].get("binding") == state.get("binding"),
        "source_identity_changed",
    )
    source = state["source_payload"]
    record, volume = prefix._source_record(project, source)
    _need(
        record == state["source_record"]
        and prefix._window_binding(project, source, volume) == state["binding"],
        "current_window_source_changed",
    )
    foundation, contents = await prefix._foundation(session, project)
    _need(
        foundation == state["foundation"] == state["identity"]["foundation"]
        and content_hash(source) == state["identity"]["source_hash"],
        "current_foundation_changed",
    )
    if lock:
        list(
            await session.scalars(
                select(ChapterModel)
                .where(
                    ChapterModel.project_id == project.id,
                    ChapterModel.chapter_number.in_(state["chapter_numbers"]),
                )
                .with_for_update()
                .execution_options(populate_existing=True, autoflush=False)
            )
        )
    await prefix._assert_unadopted_range(
        session, project_id=project.id, numbers=state["chapter_numbers"]
    )
    _need(
        state["chapter_numbers"]
        == list(range(state["chapter_numbers"][0], state["identity"]["through_chapter"] + 1)),
        "prefix_scope",
    )
    attempts = state["patch_attempts"]
    index = args["attempt_index"]
    _need(
        type(index) is int
        and 1 <= index <= len(attempts) == state["identity"]["max_repair_rounds"] == 2,
        "spent_attempt_set_required",
    )
    attempt = attempts[index - 1]
    _need(
        attempt["status"] == "invalid"
        and attempt["consumed"] is True
        and attempt.get("error")
        == {
            "type": "OutlinePrefixReadinessError",
            "code": "prefix_patch_protocol_or_validation_failed",
            "message": "prefix_patch_capacity_exceeded",
        }
        and attempt["completion"]["llm_run_id"] == str(args["llm_run_id"]),
        "failure_or_run_not_capacity_only",
    )
    parents = [c for c in state["candidates"] if c["candidate_hash"] == attempt["parent_hash"]]
    _need(
        len(parents) == 1 and content_hash(parents[0]["candidate"]) == attempt["parent_hash"],
        "parent_changed",
    )
    parent = parents[0]["candidate"]
    continuity = None
    if state["chapter_numbers"][0] > 1:
        from bestseller.services.planning_continuity_context import (
            build_planning_continuity_context,
        )

        continuity = await build_planning_continuity_context(
            session, project_id=project.id, before_chapter=state["chapter_numbers"][0]
        )
        _need(
            continuity["context_hash"] == state["identity"]["continuity_hash"], "continuity_changed"
        )
    semantic = prefix.evaluate_outline_semantic_gate(_body(parent))
    brief = planner._outline_judge_project_brief(
        project,
        metadata=project.metadata_json or {},
        semantic_candidates=[f.to_dict() for f in prefix.llm_adjudication_candidates(semantic)],
        world_spec=contents["world_spec"],
    )
    brief["prefix_evaluation_scope"] = {
        **state["phase_scope"],
        "approved_scope_only": state["chapter_numbers"],
        "full_window": state["binding"]["window"],
        "full_source_hash": state["identity"]["source_hash"],
        "tail_status": "unapproved; no complete-volume claim",
    }
    if continuity is not None:
        brief["adopted_continuity"] = continuity
    parsed, proof = await _paid(
        session,
        settings,
        project,
        workflow,
        state,
        attempt,
        index,
        scope,
        parent,
        brief,
        continuity,
    )
    _need(
        set(parsed) == {"patches"} and len(parsed["patches"]) > 12, "proposal_not_over_old_capacity"
    )
    history_physical = {str(index): proof}
    for ordinal, old_attempt in enumerate(attempts, 1):
        if ordinal == index:
            continue
        _need(
            old_attempt.get("parent_hash") == attempt["parent_hash"]
            and old_attempt.get("status") == "invalid"
            and old_attempt.get("consumed") is True
            and old_attempt.get("error") == attempt["error"],
            "historical_attempt_binding",
        )
        _, old_paid_proof = await _paid(
            session,
            settings,
            project,
            workflow,
            state,
            old_attempt,
            ordinal,
            scope,
            parent,
            brief,
            continuity,
        )
        history_physical[str(ordinal)] = old_paid_proof
    proposed = prefix._apply_projection_patches(parent, parsed["patches"])
    candidate, normalization = _validate_current_proposal(
        proposed, project=project, state=state, cast=contents["cast_spec"]
    )
    candidate_hash = content_hash(candidate)
    wid = uuid5(
        NAMESPACE_URL,
        POLICY + "/" + str(workflow.id) + "/" + str(args["llm_run_id"]) + "/" + CAPACITY_POLICY,
    )
    existing = await session.scalar(query(WorkflowRunModel, wid))
    if existing:
        receipt = (existing.metadata_json or {}).get("receipt")
        _need(
            existing.project_id == project.id
            and existing.status == "completed"
            and existing.workflow_type == POLICY
            and existing.scope_type == "project"
            and existing.scope_id == project.id
            and type(receipt) is dict
            and receipt["candidate_hash"] == candidate_hash
            and receipt["physical"] == proof
            and receipt.get("normalization") == normalization,
            "existing_audit_changed",
        )
        derived = [
            c
            for c in state["candidates"]
            if (c.get("derived_proposal") or {}).get("audit_workflow_id") == str(wid)
        ]
        _need(len(derived) == 1, "derived_candidate_missing")
        verify_derived_prefix_lineage(state, derived[0])
        matches = [
            r
            for r in state.get("paid_proposal_migrations", [])
            if r.get("audit_workflow_id") == str(wid)
        ]
        _need(
            matches == [receipt] and receipt.get("historical_paid_proofs") == history_physical,
            "audit_receipt_changed",
        )
        return (
            project,
            workflow,
            state,
            None,
            {**receipt, "status": "already_applied", "database_writes": 0},
            True,
        )
    _need(
        workflow.status == "machine_blocked"
        and state["phase"] == "rejected"
        and state["reason"] == "prefix_judge_rejected"
        and len(state["candidates"]) == 1
        and not state.get("paid_proposal_migrations"),
        "original_terminal_required",
    )
    _need(not state.get("artifact_id") and not state.get("receipt"), "approved_prefix_immutable")
    _need(
        candidate_hash not in {c["candidate_hash"] for c in state["candidates"]}, "no_new_candidate"
    )
    plan = {
        "schema": POLICY,
        "status": "prepared",
        "audit_workflow_id": str(wid),
        "workflow_run_id": str(workflow.id),
        "project_id": str(project.id),
        "scope_id": scope,
        "attempt_index": index,
        "llm_run_id": str(args["llm_run_id"]),
        "identity": deepcopy(state["identity"]),
        "binding": deepcopy(state["binding"]),
        "foundation": foundation,
        "original_state_sha256": content_hash(state),
        "original_attempt_sha256": content_hash(attempt),
        "original_workflow_sha256": content_hash(_row(workflow)),
        "physical": proof,
        "historical_paid_proofs": history_physical,
        "ledger_before": ledger,
        "candidate_hash": candidate_hash,
        "normalization": normalization,
        "legacy_parent_authority": "provisional_input_only",
        "source_facts_verified": False,
        "source_policy_sha256": content_hash(
            {
                m.__name__: _sha_bytes(open(m.__file__, "rb").read())
                for m in (prefix, planner, outline_llm_judge, integrity)
            }
        ),
        "requested_by": args["requested_by"],
        "evidence_ref": args["evidence_ref"],
        "provider_calls": 0,
        "new_patch_rounds": 0,
        "grants_credit": False,
        "adoption_authorized": False,
        "formal_judge_required": True,
    }
    plan["plan_sha256"] = content_hash(plan)
    return project, workflow, state, candidate, plan, False


async def migrate_paid_prefix_proposal(
    session,
    settings,
    *,
    project_id,
    workflow_run_id,
    attempt_index,
    llm_run_id,
    requested_by,
    evidence_ref,
    execute=False,
    expected_plan_sha256=None,
):
    """Inspect is read-only. Apply is atomic, does not commit the outer transaction."""
    _need(
        type(execute) is bool
        and type(attempt_index) is int
        and type(requested_by) is str
        and bool(requested_by.strip())
        and type(evidence_ref) is str
        and bool(evidence_ref.strip()),
        "arguments",
    )
    _need(
        not session.new
        and not session.dirty
        and not session.deleted
        and not session.in_nested_transaction()
        and session.sync_session._trans_context_manager is None,
        "clean_session_required",
    )
    args = dict(
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        attempt_index=attempt_index,
        llm_run_id=llm_run_id,
        requested_by=requested_by,
        evidence_ref=evidence_ref,
    )
    with session.no_autoflush:
        project, workflow, state, candidate, plan, replay = await _inspect(
            session, settings, args, lock=execute
        )
    if replay or not execute:
        return plan
    _need(
        type(expected_plan_sha256) is str and expected_plan_sha256 == plan["plan_sha256"],
        "plan_changed",
    )
    connection = await session.connection()
    if connection.dialect.name == "sqlite" and not await connection.run_sync(
        lambda c: c.connection.driver_connection.in_transaction
    ):
        await connection.exec_driver_sql("BEGIN")
    async with session.begin_nested():
        with session.no_autoflush:
            fresh = await _inspect(session, settings, args, lock=True)
        _need(not fresh[5] and fresh[4] == plan, "source_or_paid_proof_changed")
        project, workflow, state, candidate, _, _ = fresh
        receipt = {**deepcopy(plan), "status": "applied"}
        audit = WorkflowRunModel(
            id=UUID(plan["audit_workflow_id"]),
            project_id=project.id,
            workflow_type=POLICY,
            scope_type="project",
            scope_id=project.id,
            status="completed",
            current_step="completed",
            requested_by=requested_by,
            idempotency_key=plan["audit_workflow_id"],
            metadata_json={
                "receipt": receipt,
                "original_workflow": _row(workflow),
                "original_state": deepcopy(state),
            },
        )
        session.add(audit)
        await session.flush()  # immutable audit exists before the candidate is selected
        state.setdefault("paid_proposal_migrations", []).append(receipt)
        derived = {
            "candidate": candidate,
            "candidate_hash": plan["candidate_hash"],
            "validated": True,
            "patch_llm_run_id": str(llm_run_id),
            "derived_proposal": {
                "schema": POLICY,
                "audit_workflow_id": str(audit.id),
                "receipt_sha256": content_hash(receipt),
            },
        }
        state["candidates"].append(derived)
        verify_derived_prefix_lineage(state, derived)
        terminal = {
            k: deepcopy(state[k])
            for k in ("phase", "reason", "final_report", "failure", "admission_status")
            if k in state
        }
        terminal.update(
            workflow_status=workflow.status,
            workflow_current_step=workflow.current_step,
            audit_workflow_id=str(audit.id),
        )
        state.setdefault("terminal_history", []).append(terminal)
        for key in ("reason", "final_report", "failure", "admission_status"):
            state.pop(key, None)
        state["phase"] = "judge"
        workflow.metadata_json = state
        workflow.current_step = "judge"
        await session.flush()
    return {**receipt, "database_writes": 2}
