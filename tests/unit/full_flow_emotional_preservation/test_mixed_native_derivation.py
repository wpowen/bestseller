"""Mixed derivation RED; main alone executes the tests.

The positive normalization/union cases are PURE COMPONENT evidence. The native
positive prefix uses actual SQLite IDs, gateway, operation/attempt settlement,
physical response and delegates to the actual source readers; its unavailable
review cycle must remain Unknown. No available cycle, grade, current/promoted
draft or approval is manufactured. A complete native positive remains Unknown.

Negative cases must reject before another critic call, including exact raw
bytes. Native custody is not permission to fall back to fact-only or identity.
Franklin's full-report fixture uses real parsers/projectors with synthetic raw
returns; it is never represented as a native paid review or adoption authority.
"""

from copy import deepcopy
import json
from uuid import UUID, uuid4

from full_flow_fact_scope import test_mixed_repair_scope as mixed_contract
import pytest

from bestseller.infra.db import models as m
from bestseller.services import budget, drafts, llm, reader_judge
from bestseller.services import recorded_review_sources as recorded
from bestseller.services import rewrite_fact_scope as scopes
from bestseller.services.retained_rewrite_projection import project_retained_rewrite
from bestseller.services.reviews import chapter_review_body_fingerprint

from .support import (
    SOURCE,
    SafeDifference,
    anchor,
    anchor_answer,
    equal,
    rejected,
    require,
    sha,
    summary,
    verify_answer,
)
from .test_native_derivation_boundaries_red import fact_case as fact_case
from .test_reader_revision_preservation import CANDIDATE

pytestmark = pytest.mark.unit
__all__ = ["fact_case"]

VERSION = "3.4-verified-mixed-edit-scope"
SCHEMA = "verified-mixed-repair-scope.v1"
MARKER = "\n\nBACKEND_MIXED_REPAIR_SCOPE_JSON:\n"
TASK_MARKER = "\n\nBACKEND_MIXED_REPAIR_TASK_JSON:\n"
TASK_FIELDS = (
    "project_id", "chapter_id", "source_draft_id", "source_body_sha256",
    "review_report_id", "rewrite_task_id",
)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def derive(body, report):
    function = getattr(scopes, "derive_mixed_repair_scope", None)
    require("mixed.public_scope_api", callable(function))
    before = deepcopy(report)
    result = function(body, report)
    equal("mixed.original_report_immutable", before, report)
    equal("mixed.independent_schema", SCHEMA, result["schema"])
    equal("mixed.full_report_hash", sha(report), result["report_payload_sha256"])
    equal("mixed.parent_hash", sha(body), result["parent_sha256"])
    for key in mixed_contract.FLAGS:
        equal("mixed.no_grant." + key, False, result[key])
    return result


def normalization_request(body):
    return llm.LLMCompletionRequest(
        logical_role="editor",
        system_prompt="Component normalization only; no adoption authority.",
        user_prompt=body,
        fallback_response="unavailable",
        prompt_template="chapter_rewrite",
        prompt_version=VERSION,
    )


@pytest.mark.parametrize("raw_newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_pure_mixed_normalization_and_union_replay_exact_bytes_without_authority(raw_newline):
    """Positive component, explicitly not complete native review evidence."""
    # Franklin owns BODY. Its public fixture accepts explicit paragraph
    # numbers: use 1 (fact) and 3 (primary), retaining the entire paragraph 2
    # as unique unchanged context between the two independently owned spans.
    # Only the physical raw return varies its line endings; the parent is LF.
    body = mixed_contract.BODY
    equal("mixed.parent_line_endings", False, "\r" in body)
    report = mixed_contract.mixed_report(body, numbers=(3,))
    scope = derive(body, report)
    equal("mixed.nonadjacent_scope_quotes", [
        mixed_contract.FACT, "The witness paused before naming the empty seat.",
    ], [span["quote"] for span in scope["authorized_spans"]])
    gap = body[scope["authorized_spans"][0]["end"]:scope["authorized_spans"][1]["start"]]
    require("mixed.unchanged_unique_between_spans", "copper key" in gap and body.count(gap) == 1)
    equal("mixed.cannot_downgrade_to_fact", None, scopes.derive_fact_repair_scope(body, report))
    expected = body.replace("totals five", "totals four").replace("witness paused", "witness answered")
    returned = expected.replace("Protected paragraph 7", "Unrequested paragraph 7")
    raw = ("<!-- canonical normalization component -->\n" + returned).replace("\n", raw_newline)
    sanitized = drafts.sanitize_novel_markdown_content(raw)
    require("mixed.raw_differs_from_sanitized", raw != sanitized)
    equal("mixed.sanitizer_canonical_lf", False, "\r" in sanitized)
    normalization = reader_judge._reader_revision_candidate_derivation(
        raw, sanitized, request=normalization_request(body),
        source_binding={}, project_language="en",
    )
    equal("mixed.raw_hash", sha(raw), normalization["raw_body_sha256"])
    equal("mixed.normalized_hash", sha(sanitized), normalization["candidate_body_sha256"])
    require("mixed.normalizer_code_custody", len(normalization["code_sha256"]) == 64)
    projection = project_retained_rewrite(
        body, sanitized, authorized_spans=scope["authorized_spans"],
        expected_parent_sha256=scope["parent_sha256"], expected_return_sha256=sha(sanitized),
    )
    require("mixed.union_projection_accepted", projection.accepted is True)
    # Sanitizer normalizes returned line endings/framing whitespace; outside
    # bytes still come from the LF parent, including Unicode and its suffix.
    equal("mixed.exact_projected_bytes", expected.encode(), projection.candidate_text.encode())
    equal("mixed.gap_preserved_exactly", 1, projection.candidate_text.count(gap))
    require("mixed.unrequested_changes_discarded", bool(projection.receipt.discarded_opcodes))
    equal("mixed.no_automatic_promotion", False, projection.receipt.automatic_promotion_allowed)
    equal("mixed.fresh_review_required", True, projection.receipt.fresh_review_required)
    equal("mixed.full_report_unchanged", sha(report), scope["report_payload_sha256"])


@pytest.mark.parametrize("boundary", ["adjacent_lf", "adjacent_crlf", "nonadjacent_crlf_parent"])
def test_pure_mixed_original_unprovable_boundaries_remain_rejected(boundary):
    """Retain the original failing inputs as strict negative controls.

    Repeated inter-span separators cannot supply a unique adjacent anchor.
    Also, canonical sanitizer changes a CRLF parent's immediate outside-span
    boundaries to LF; the projector cannot prove their exact alignment. Neither
    case licenses widening/merging spans, normalizing the parent, or adoption.
    """
    adjacent = boundary.startswith("adjacent_")
    # Remove only Franklin's later fixture indentation to retain the original
    # repeated-separator failure. Nonadjacent uses his current BODY unchanged.
    body = mixed_contract.BODY
    if adjacent:
        body = body.replace("\n\n  Mara", "\n\nMara", 1)
    if boundary != "adjacent_lf":
        body = body.replace("\n", "\r\n")
    report = mixed_contract.mixed_report(body, numbers=(2,) if adjacent else (3,))
    scope = derive(body, report)
    returned = body.replace("totals five", "totals four")
    returned = (
        returned.replace("copper key", "silver key") if adjacent
        else returned.replace("witness paused", "witness answered")
    )
    returned = returned.replace("Protected paragraph 7", "Unrequested paragraph 7")
    raw = "<!-- original rejected normalization fixture -->\n" + returned
    sanitized = drafts.sanitize_novel_markdown_content(raw)
    reader_judge._reader_revision_candidate_derivation(
        raw, sanitized, request=normalization_request(body),
        source_binding={}, project_language="en",
    )
    before = sha(body), sha(scope), sha(report)
    projection = project_retained_rewrite(
        body, sanitized, authorized_spans=scope["authorized_spans"],
        expected_parent_sha256=scope["parent_sha256"], expected_return_sha256=sha(sanitized),
    )
    equal("mixed.unprovable_alignment_rejected", False, projection.accepted)
    equal("mixed.original_rejection_reason", ("ambiguous_alignment",), projection.failure_codes)
    equal("mixed.rejection_retains_parent", body.encode(), projection.candidate_text.encode())
    equal("mixed.rejected_inputs_unchanged", before, (sha(body), sha(scope), sha(report)))
    equal("mixed.rejected_no_promotion", False, projection.receipt.automatic_promotion_allowed)
    equal("mixed.rejected_still_requires_review", True, projection.receipt.fresh_review_required)


async def task_snapshot(case, task_id, report_id):
    async with case.sessions() as session:
        task = await session.get(m.RewriteTaskModel, task_id)
        report = await session.get(m.ReviewReportModel, report_id)
        return {
            "task": None if task is None else (
                str(task.project_id), str(task.trigger_source_id), task.status, task.attempts,
                deepcopy(task.metadata_json),
            ),
            "report": None if report is None else (
                str(report.project_id), str(report.target_id), report.verdict,
                deepcopy(report.structured_output),
            ),
        }


async def native_task_binding(case, task_id):
    async with case.sessions() as session:
        task = await session.get(m.RewriteTaskModel, task_id)
        source = await session.get(m.ChapterDraftVersionModel, case.source_id)
        report = await session.get(m.ReviewReportModel, UUID(task.metadata_json["review_report_id"]))
        return {
            "project_id": str(source.project_id), "chapter_id": str(source.chapter_id),
            "source_draft_id": str(source.id), "source_body_sha256": sha(source.content_md),
            "review_report_id": str(report.id), "rewrite_task_id": str(task.id),
        }


def task_wire(binding, fault=None):
    value = deepcopy(binding)
    if fault == "removed":
        return ""
    if fault == "extra_key":
        value["extra"] = "not in the six-field contract"
    elif fault == "missing_key":
        value.pop("source_body_sha256")
    elif fault == "not_object":
        value = [value]
    elif fault and fault.startswith("forged."):
        key = fault.split(".", 1)[1]
        value[key] = "0" * 64 if key == "source_body_sha256" else str(uuid4())
    elif fault and fault.startswith("typed."):
        value[fault.split(".", 1)[1]] = False
    encoded = canonical(value)
    if fault == "duplicate_key":
        encoded = encoded[:-1] + ',"rewrite_task_id":' + canonical(binding["rewrite_task_id"]) + "}"
    block = TASK_MARKER + encoded
    return block + block if fault == "duplicate_marker" else block


async def mixed_candidate(
    case, task_id, scope, *, sanitized=False, marker=MARKER, body_text=CANDIDATE,
    wire_fault=None, template="chapter_rewrite", prior_run_id=None,
):
    """Actual complete_text/paid ledger, SDK return only is offline."""
    raw = ("<!-- native canonical normalization -->\n" if sanitized else "") + body_text
    case.answers.append(raw)
    binding = await native_task_binding(case, task_id)
    user_prompt = case.body + marker + canonical(scope) + task_wire(binding, wire_fault)
    metadata = {"rewrite_task_id": str(task_id), "automatic_revision_source": {
        "chapter_id": str(case.chapter_id), "source_draft_id": str(case.source_id),
    }}
    version = VERSION
    if template != "chapter_rewrite":
        from bestseller.services.rewrite_repair_source import build_rewrite_repair_user_prompt

        version = "2.0-candidate-source"
        user_prompt = build_rewrite_repair_user_prompt(
            original_user_prompt=user_prompt, failed_candidate=body_text, findings=[],
            stage="retrofit_repair" if "retrofit" in template else "semantic_repair", language="en",
        )
        if prior_run_id is not None:
            metadata["semantic_repair_of"] = str(prior_run_id)
    request = llm.LLMCompletionRequest(
        logical_role="editor", system_prompt="Return the complete revised chapter.",
        user_prompt=user_prompt, fallback_response="unavailable",
        prompt_template=template, prompt_version=version,
        project_id=case.project_id, workflow_run_id=case.workflow_id,
        operation_id=f"fixture-native-mixed:{uuid4()}",
        metadata=metadata,
    )
    with budget.bind_execution_scope(case.scope), llm.bind_project_prompt_audit(
        case.project_id, reason="offline mixed native derivation boundary"
    ):
        async with case.sessions() as session:
            completion = await llm.complete_text(session, case.settings, request)
            await session.commit()
    run, _path, _physical = await case.physical(completion.llm_run_id)
    equal("mixed.native_template", template, run.prompt_template)
    equal("mixed.native_version", version, run.prompt_version)
    equal("mixed.native_owner", case.workflow_id, run.workflow_run_id)
    return completion


async def unresolved_scope(case, task_id, report_id):
    """An untrusted declaration only; no synthesized approval/grade fields."""
    async with case.sessions.begin() as session:
        report = await session.get(m.ReviewReportModel, report_id)
        task = await session.get(m.RewriteTaskModel, task_id)
        declaration = {
            "schema": SCHEMA, "parent_sha256": sha(case.body),
            "report_payload_sha256": sha(report.structured_output),
        }
        task.metadata_json = {
            **task.metadata_json, "verified_mixed_repair_scope": deepcopy(declaration),
        }
    return declaration


@pytest.mark.asyncio
@pytest.mark.parametrize("template", [
    "chapter_rewrite", "chapter_rewrite_repair", "chapter_rewrite_quality_retrofit_repair",
])
async def test_native_mixed_positive_prefix_rederives_full_rows_then_preserves_unknown(
    fact_case, monkeypatch, request, template,
):
    """Positive dispatch to actual readers; NOT a native successful verdict."""
    case, task_id, report_id = fact_case
    body = mixed_contract.BODY
    payload = mixed_contract.mixed_report(body, numbers=(3,))
    payload.update(draft_id=str(case.source_id),
                   reviewed_body_fingerprint=chapter_review_body_fingerprint(body))
    # Reuse the actual strict component producer. No native paid review proof
    # exists for its synthetic critic returns, and none is inserted below.
    scope = derive(body, payload)
    case.body = body
    async with case.sessions.begin() as session:
        source = await session.get(m.ChapterDraftVersionModel, case.source_id)
        source.content_md, source.word_count = body, len(body)
        report = await session.get(m.ReviewReportModel, report_id)
        report.structured_output = deepcopy(payload)
        task = await session.get(m.RewriteTaskModel, task_id)
        task.metadata_json = {
            **task.metadata_json, "parent_draft_hash": sha(body),
            "verified_mixed_repair_scope": deepcopy(scope),
        }
    quote = "Audit-only observation: the window faced the quiet street."
    case.answers.append(anchor_answer(body, [anchor(body, quote, kind="action")]))
    await case.freeze()
    candidate = body.replace("totals five", "totals four").replace("witness paused", "witness answered")
    completion = await mixed_candidate(case, task_id, scope, body_text=candidate)
    expected_wires = 2
    if template != "chapter_rewrite":
        completion = await mixed_candidate(
            case, task_id, scope, body_text=candidate,
            template=template, prior_run_id=completion.llm_run_id,
        )
        expected_wires = 3
    derived, reader_calls, fact_calls = [], [], []
    original_derive = scopes.derive_mixed_repair_scope
    original_reader = recorded.read_recorded_review_sources
    original_fact = scopes.require_fact_repair_scope

    def observe_derive(parent, full_report):
        derived.append((parent, deepcopy(full_report)))
        return original_derive(parent, full_report)

    async def observe_reader(*args, **kwargs):
        reader_calls.append(deepcopy(kwargs))
        return await original_reader(*args, **kwargs)

    def observe_fact(*args, **kwargs):
        fact_calls.append(True)
        return original_fact(*args, **kwargs)

    # These observers all delegate; no source reader/verifier returns a fake
    # successful receipt. Missing native current/cycle must remain Unknown.
    monkeypatch.setattr(scopes, "derive_mixed_repair_scope", observe_derive)
    monkeypatch.setattr(scopes, "require_fact_repair_scope", observe_fact)
    monkeypatch.setattr(recorded, "read_recorded_review_sources", observe_reader)
    before = await case.ledger(), case.files(), await case.protected()
    refusal = await rejected(lambda: case.verify(candidate, completion.llm_run_id))
    # Retain the actual refusal instead of replacing its evidence with only
    # the later observer mismatch. rejected() already validates these safe
    # field/type/len/SHA operands. Main owns the diagnostic execution/receipt.
    native_rows = await task_snapshot(case, task_id, report_id)
    native_task_metadata = native_rows["task"][4]
    async with case.sessions() as session:
        native_source = await session.get(m.ChapterDraftVersionModel, case.source_id)
        native_run = await session.get(m.LlmRunModel, completion.llm_run_id)
        source_body_summary = summary(native_source.content_md)
        run_metadata = deepcopy(native_run.metadata_json or {})
    diagnostic = {
        "schema": "mixed-native-prefix-diagnostic.v1",
        "rejection": {
            "exception_type": type(refusal).__name__, "code": refusal.code,
            "differences": deepcopy(refusal.differences),
        },
        "observer_calls": {"mixed": len(derived), "recorded": len(reader_calls), "fact": len(fact_calls)},
        "source_bodies": {"fixture": summary(body), "native": source_body_summary},
        "scope": summary(scope), "candidate": summary(candidate),
        "native_task_fields": {
            key: {"present": key in native_task_metadata, "value": summary(native_task_metadata.get(key))}
            for key in ("verified_fact_repair_scope", "verified_mixed_repair_scope",
                        "parent_draft_id", "parent_draft_hash", "review_report_id")
        },
        "native_run_fields": {
            key: {"present": key in run_metadata, "value": summary(run_metadata.get(key))}
            for key in ("rewrite_task_id", "semantic_repair_of", "automatic_revision_source")
        },
    }
    # Avoid pytest's record_property/xunit2 setup warning while retaining the
    # same safe diagnostic property for the main runner's evidence collector.
    request.node.user_properties.append(("mixed_native_prefix_diagnostic", canonical(diagnostic)))
    try:
        equal("mixed.complete_native_rows_rederived", [(body, payload)], derived)
        equal("mixed.strict_native_reader_reached", [{
            "project_id": case.project_id, "chapter_id": case.chapter_id,
            "draft_id": case.source_id,
        }], reader_calls)
        equal("mixed.never_downgrades_to_fact", [], fact_calls)
        equal("mixed.unknown_preserves_native_state", before,
              (await case.ledger(), case.files(), await case.protected()))
        equal("mixed.no_extra_paid_critic", expected_wires, len(case.wires))
    except SafeDifference as mismatch:
        # Preserve every original assertion and its operands. The main runner
        # can now retain the originating guard's code/differences in the chain.
        raise mismatch from refusal


async def require_wire_refusal(case, anchors, completion, expected_code, *, wires=2):
    case.answers.append(verify_answer(anchors["anchors"], CANDIDATE))
    before = await case.ledger(), case.files(), await case.protected()
    error = await rejected(lambda: case.verify(CANDIDATE, completion.llm_run_id))
    equal("mixed.exact_wire_guard", "reader_revision:" + expected_code, error.code)
    equal("mixed.wire_refusal_preserves_native_state", before,
          (await case.ledger(), case.files(), await case.protected()))
    equal("mixed.wire_refusal_no_critic", wires, len(case.wires))
    equal("mixed.wire_refusal_answer_unconsumed", 1, len(case.answers))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "removed", "duplicate_marker", "duplicate_key", "extra_key", "missing_key", "not_object",
    *("forged." + field for field in TASK_FIELDS),
    *("typed." + field for field in TASK_FIELDS),
])
async def test_native_mixed_paid_task_marker_is_exact_before_scope_or_critic(fact_case, fault):
    case, task_id, report_id = fact_case
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    scope = await unresolved_scope(case, task_id, report_id)
    completion = await mixed_candidate(case, task_id, scope, wire_fault=fault)
    prefix = "candidate.mixed_task_binding"
    if fault in {"removed", "duplicate_marker"}:
        code = prefix + ".marker_count"
    elif fault in {"extra_key", "missing_key"}:
        code = prefix + ".keys"
    elif fault == "duplicate_key":
        code = "json_duplicate_key"
    elif fault == "not_object":
        code = "json_object"
    elif fault.startswith("typed."):
        code = prefix + "." + fault.split(".", 1)[1] + ".type"
    else:
        code = prefix + "." + fault.split(".", 1)[1]
    await require_wire_refusal(case, anchors, completion, code)


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", ["missing", "different_native_task"])
async def test_native_run_metadata_is_only_a_locator_not_a_task_wire_substitute(fact_case, locator):
    case, task_id, report_id = fact_case
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    scope = await unresolved_scope(case, task_id, report_id)
    completion = await mixed_candidate(case, task_id, scope)
    async with case.sessions.begin() as session:
        run = await session.get(m.LlmRunModel, completion.llm_run_id)
        metadata = deepcopy(run.metadata_json)
        if locator == "missing":
            metadata.pop("rewrite_task_id")
            code = "candidate.fact_task_id"
        else:
            original = await session.get(m.RewriteTaskModel, task_id)
            other = m.RewriteTaskModel(
                project_id=original.project_id, trigger_type=original.trigger_type,
                trigger_source_id=original.trigger_source_id, rewrite_strategy=original.rewrite_strategy,
                status="running", attempts=1, instructions=original.instructions,
                metadata_json=deepcopy(original.metadata_json),
            )
            session.add(other)
            await session.flush()
            metadata["rewrite_task_id"] = str(other.id)
            code = "candidate.mixed_task_binding.rewrite_task_id"
        run.metadata_json = metadata
    await require_wire_refusal(case, anchors, completion, code)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["current_removed", "prior_removed", "prior_forged", "missing_prior"])
async def test_mixed_repair_checks_both_current_and_original_paid_task_bindings(fact_case, fault):
    case, task_id, report_id = fact_case
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    scope = await unresolved_scope(case, task_id, report_id)
    prior_fault = {"prior_removed": "removed", "prior_forged": "forged.rewrite_task_id"}.get(fault)
    prior = await mixed_candidate(case, task_id, scope, wire_fault=prior_fault)
    completion = await mixed_candidate(
        case, task_id, scope, template="chapter_rewrite_quality_retrofit_repair",
        prior_run_id=None if fault == "missing_prior" else prior.llm_run_id,
        wire_fault="removed" if fault == "current_removed" else None,
    )
    code = {
        "current_removed": "candidate.mixed_task_binding.marker_count",
        "prior_removed": "candidate.mixed_prior_task_binding.marker_count",
        "prior_forged": "candidate.mixed_prior_task_binding.rewrite_task_id",
        "missing_prior": "candidate.mixed_request_protocol",
    }[fault]
    await require_wire_refusal(case, anchors, completion, code, wires=3)


@pytest.mark.asyncio
@pytest.mark.parametrize("sanitized", [False, True], ids=["raw", "sanitized"])
@pytest.mark.parametrize("fault", [
    "unverified_report", "report_tamper", "scope_tamper", "task_project",
    "report_chapter", "paid_scope_tamper", "fact_schema_downgrade",
    "fact_prompt_marker", "outside_edits",
])
async def test_native_mixed_unknown_or_tampered_sources_never_use_identity_fallback(
    fact_case, fault, sanitized,
):
    case, task_id, report_id = fact_case
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    declaration = await unresolved_scope(case, task_id, report_id)
    paid_scope = deepcopy(declaration)
    marker = MARKER
    async with case.sessions.begin() as session:
        task = await session.get(m.RewriteTaskModel, task_id)
        report = await session.get(m.ReviewReportModel, report_id)
        if fault == "report_tamper":
            report.structured_output = {
                **report.structured_output, "retained_unresolved_issue": {"index": 0},
            }
        elif fault == "scope_tamper":
            task.metadata_json = {
                **task.metadata_json, "verified_mixed_repair_scope": {
                    **declaration, "parent_sha256": "0" * 64,
                },
            }
        elif fault == "task_project":
            task.project_id = uuid4()
        elif fault == "report_chapter":
            report.target_id = uuid4()
        elif fault == "paid_scope_tamper":
            paid_scope["report_payload_sha256"] = "0" * 64
        elif fault == "fact_schema_downgrade":
            paid_scope["schema"] = "verified-fact-repair-scope.v1"
            task.metadata_json = {
                **task.metadata_json, "verified_mixed_repair_scope": deepcopy(paid_scope),
            }
        elif fault == "fact_prompt_marker":
            marker = "\n\nBACKEND_FACT_REPAIR_SCOPE_JSON:\n"
    completion = await mixed_candidate(
        case, task_id, paid_scope, sanitized=sanitized, marker=marker,
    )
    candidate = CANDIDATE + ("\nAn unrequested new scene." if fault == "outside_edits" else "")
    case.answers.append(verify_answer(anchors["anchors"], candidate))
    before = (
        await case.ledger(), case.files(), await case.protected(),
        await task_snapshot(case, task_id, report_id),
    )
    await rejected(lambda: case.verify(candidate, completion.llm_run_id))
    equal("mixed.negative_does_not_pay_or_adopt", before, (
        await case.ledger(), case.files(), await case.protected(),
        await task_snapshot(case, task_id, report_id),
    ))
    equal("mixed.no_critic_answer_consumed", 1, len(case.answers))
    equal("mixed.no_extra_wire", 2, len(case.wires))


@pytest.mark.asyncio
async def test_same_bytes_from_distinct_native_mixed_runs_cannot_borrow_other_task_scope(fact_case):
    case, task_id, report_id = fact_case
    case.answers.append(anchor_answer())
    anchors = await case.freeze()
    scope = await unresolved_scope(case, task_id, report_id)
    first = await mixed_candidate(case, task_id, scope)
    async with case.sessions.begin() as session:
        other_report = m.ReviewReportModel(
            project_id=case.project_id, target_type="chapter", target_id=case.chapter_id,
            reviewer_type="critic", verdict="unknown", structured_output={
                "draft_id": str(case.source_id),
                "reviewed_body_fingerprint": chapter_review_body_fingerprint(SOURCE),
                "unavailable_source": "independent native row; no paid review proof",
            },
        )
        session.add(other_report)
        await session.flush()
        other_task = m.RewriteTaskModel(
            project_id=case.project_id, trigger_type="chapter_review",
            trigger_source_id=case.chapter_id, rewrite_strategy="chapter_rewrite",
            status="running", attempts=1, instructions="Retain unresolved source evidence.",
            metadata_json={"parent_draft_id": str(case.source_id), "parent_draft_hash": sha(SOURCE),
                           "review_report_id": str(other_report.id)},
        )
        session.add(other_task)
        await session.flush()
        other_id, other_report_id = other_task.id, other_report.id
    other_scope = await unresolved_scope(case, other_id, other_report_id)
    require("mixed.other_full_report_identity", scope["report_payload_sha256"] != other_scope["report_payload_sha256"])
    second = await mixed_candidate(case, other_id, scope)  # Intentionally replay the first scope.
    require("mixed.distinct_native_run_ids", first.llm_run_id != second.llm_run_id)
    equal("mixed.identical_wire_bodies", first.content, second.content)
    case.answers.append(verify_answer(anchors["anchors"], CANDIDATE))
    before = await case.ledger(), case.files(), await case.protected()
    await rejected(lambda: case.verify(CANDIDATE, second.llm_run_id))
    equal("mixed.no_cross_run_borrow", before,
          (await case.ledger(), case.files(), await case.protected()))
    equal("mixed.two_native_body_calls_only", 3, len(case.wires))
    equal("mixed.shared_round_limit_not_reset", 1, len(case.answers))
