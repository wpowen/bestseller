"""Exact writer source allowance; never financial or provider authorization."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from types import SimpleNamespace
from typing import TYPE_CHECKING

from bestseller.services import finite_book_budget
from bestseller.services.llm_capacity import ModelRequestCapacityError, measure_request_capacity
from bestseller.services.prompt_compiler import (
    CompiledPrompt,
    PromptBlock,
    PromptBudgetError,
    _block_sort_key,
    _render_channels,
    compile_prompt,
    estimate_prompt_tokens,
)

SCHEMA = "writer-source-capacity.v1"

if TYPE_CHECKING:
    from bestseller.infra.db.models import ChapterModel, ProjectModel
    from bestseller.settings import AppSettings


@dataclass(frozen=True)
class _ReaderFirstSourceScope:
    project_id: str
    chapter_id: str
    chapter_number: int
    target_chapters: int
    language: str
    binding_json: str


@dataclass(frozen=True)
class WriterSourceCapacity:
    output_tokens: int
    n_candidates: int
    capacity_limit: int
    delivery_bindings: tuple[tuple[str, str], ...]
    model: str | None = None
    endpoint_hash: str | None = None
    reader_first_scope: _ReaderFirstSourceScope | None = None


def _delivery_binding(block: PromptBlock) -> tuple[str, str]:
    provenance = block.provenance
    if not (
        block.key == "chapter_first.approved_current_delivery"
        and block.source in {"chapter_writer_delivery.v1", "chapter_writer_delivery.v2"}
        and block.instruction_family == "writer.current_chapter.delivery"
        and block.channel == "user"
        and block.layer == "scene_spec"
        and block.required
        and block.trim_policy == "preserve"
        and provenance is not None
        and provenance.kind == "scene_spec"
        and provenance.source_id
        and provenance.source_hash
        and provenance.field_paths
    ):
        raise ValueError("writer_source_capacity_verified_delivery_required")
    payload = json.loads(block.text[block.text.index("{") :])
    if (
        payload.get("schema")
        not in {
            "approved-current-chapter-delivery.v1",
            "approved-current-chapter-delivery.v2",
        }
        or payload.get("artifact_id") != provenance.source_id
        or payload.get("approved_chapter_sha256") != provenance.source_hash
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in (provenance.source_hash, payload.get("readiness_receipt_sha256"))
        )
    ):
        raise ValueError("writer_source_capacity_delivery_binding_changed")
    return block.key, sha256(block.model_dump_json().encode()).hexdigest()


def _reader_first_bound_payload(block: PromptBlock, scope: _ReaderFirstSourceScope) -> dict:
    from bestseller.services.chapter_writer_sources import verify_reader_first_delivery

    return verify_reader_first_delivery(
        project=SimpleNamespace(
            id=scope.project_id,
            target_chapters=scope.target_chapters,
            language=scope.language,
            metadata_json={"reader_first_planning": json.loads(scope.binding_json)},
        ),
        chapter=SimpleNamespace(id=scope.chapter_id, chapter_number=scope.chapter_number),
        block=block,
    )


def _reader_first_source_evidence(block: PromptBlock, payload: dict) -> dict:
    """Hashes describe retained source bodies; they never assert a manuscript grade."""
    from bestseller.services.prompt_source_policy import content_hash

    census = [
        {
            "chapter_number": row["chapter_number"],
            "chapter_sha256": content_hash(row),
            "scene_numbers": [scene["scene_number"] for scene in row["scenes"]],
            "field_roles_sha256": content_hash(row["_meta"]["reader_first_field_roles"]),
        }
        for row in payload["review_source"]["outline"]["chapters"]
    ]
    raw_review = payload["raw_review"]
    review_body = (
        raw_review
        if isinstance(raw_review, str)
        else json.dumps(
            raw_review,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return {
        "schema": "reader-first-writer-consumer-evidence.v1",
        "source_binding": payload["source_binding"],
        "reader_first_planning": payload["reader_first_planning"],
        "block_text_sha256": sha256(block.text.encode()).hexdigest(),
        "block_model_sha256": sha256(block.model_dump_json().encode()).hexdigest(),
        "approved_chapter_sha256": payload["approved_chapter_sha256"],
        "review_source_sha256": payload["review_source_sha256"],
        "raw_review_sha256": content_hash(raw_review),
        "raw_review_body_sha256": sha256(review_body.encode()).hexdigest(),
        "raw_review_body_encoding": "retained_utf8"
        if isinstance(raw_review, str)
        else "canonical_json",
        "reader_receipt_sha256": payload["reader_receipt_sha256"],
        "readiness_receipt_sha256": payload["readiness_receipt_sha256"],
        "census": census,
        "census_sha256": content_hash(census),
        "native_positive": "unknown",
    }


def prepare_writer_source_capacity(
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    delivery_blocks: Sequence[PromptBlock],
    model_tier: str,
    max_tokens_override: int | None,
) -> WriterSourceCapacity | None:
    """Called only after the production loader verifies current chapter delivery."""
    from bestseller.services.chapter_writer_sources import verify_reader_first_delivery
    from bestseller.services.source_bound_planning_content import reader_first_binding

    binding = reader_first_binding(project)
    scope = None
    if binding is not None:
        if len(delivery_blocks) != 1:
            raise ValueError("writer_source_capacity_reader_first_delivery_required")
        verify_reader_first_delivery(project=project, chapter=chapter, block=delivery_blocks[0])
        scope = _ReaderFirstSourceScope(
            str(project.id),
            str(chapter.id),
            chapter.chapter_number,
            project.target_chapters,
            getattr(project, "language", None) or "zh",
            json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )
    if not delivery_blocks:
        return None
    bindings = (
        tuple(
            (block.key, sha256(block.model_dump_json().encode()).hexdigest())
            for block in delivery_blocks
        )
        if scope is not None
        else tuple(_delivery_binding(block) for block in delivery_blocks)
    )
    for block in delivery_blocks:
        payload = json.loads(block.text[block.text.index("{") :])
        if (
            scope is None
            and {"reader_first_planning", "review_source", "raw_review"} & payload.keys()
        ):
            raise ValueError("writer_source_capacity_reader_first_scope_required")
        if (
            payload.get("project_id") != str(project.id)
            or payload.get("chapter_id") != str(chapter.id)
            or payload.get("chapter_number") != chapter.chapter_number
        ):
            raise ValueError("writer_source_capacity_delivery_scope_changed")
    from bestseller.services import llm
    from bestseller.services.model_catalog import legacy_project_role, resolve_project_model_entry
    from bestseller.services.model_context_capacity import documented_capacity

    active = llm.apply_runtime_llm_profile(settings)
    role = llm._get_role_settings(active, "writer")
    entry = resolve_project_model_entry(project.metadata_json or {})
    if entry is not None and entry.available:
        role = llm._apply_model_override(role, entry)
    elif not (project.metadata_json or {}).get("llm_model_id"):
        role = legacy_project_role(active, project.id, "writer") or role
    if model_tier == "strong" and role.model_override:
        role = role.model_copy(update={"model": role.model_override})
    request = llm.LLMCompletionRequest(
        logical_role="writer",
        model_tier=model_tier,
        system_prompt="capacity",
        user_prompt="capacity",
        fallback_response="not dispatched",
        max_tokens_override=max_tokens_override,
    )
    effective = llm._effective_input_payload(request, role)
    ceiling = min(
        finite_book_budget.current_capacity(),
        documented_capacity(role.model, endpoint=role.api_base),
    )
    return WriterSourceCapacity(
        effective["max_tokens"],
        effective["n_candidates"],
        ceiling,
        bindings,
        effective["model"],
        effective["endpoint_hash"],
        scope,
    )


def _complete(compiled: CompiledPrompt, core: Sequence[PromptBlock]) -> bool:
    report = compiled.report
    changed = {*report.dropped, *report.truncated, *report.duplicates}
    return all(
        block.key in report.kept
        and block.key not in changed
        and block.text.strip() in (compiled.system if block.channel == "system" else compiled.user)
        for block in core
    )


def compile_writer_source_prompt(
    blocks: Sequence[PromptBlock],
    *,
    integrity_keys: set[str],
    base_budget_tokens: int,
    safety_margin: float,
    capacity: WriterSourceCapacity,
) -> CompiledPrompt:
    """Reserve source integrity independently of instruction/canon authority."""
    if not isinstance(capacity, WriterSourceCapacity):
        raise ValueError("writer_source_capacity_context_invalid")
    if base_budget_tokens <= 0 or not 0 <= safety_margin < 1:
        raise ValueError("writer_source_capacity_budget_invalid")
    if not integrity_keys.issubset(block.key for block in blocks):
        raise ValueError("writer_source_capacity_integrity_keys_missing")
    if any(
        type(value) is not int or value <= 0
        for value in (
            capacity.output_tokens,
            capacity.n_candidates,
            capacity.capacity_limit,
        )
    ):
        raise ValueError("writer_source_capacity_limits_invalid")
    delivery = [block for block in blocks if block.key in dict(capacity.delivery_bindings)]
    scope = capacity.reader_first_scope
    delivery_evidence = {}
    if scope is not None:
        if not isinstance(scope, _ReaderFirstSourceScope) or len(delivery) != 1:
            raise ValueError("writer_source_capacity_reader_first_scope_invalid")
        bindings = tuple(
            (block.key, sha256(block.model_dump_json().encode()).hexdigest()) for block in delivery
        )
        if bindings != capacity.delivery_bindings:
            raise ValueError("writer_source_capacity_delivery_binding_changed")
        from bestseller.services.chapter_writer_delivery import READER_FIRST_DELIVERY_SOURCE

        if any(
            block.source == READER_FIRST_DELIVERY_SOURCE and block not in delivery
            for block in blocks
        ):
            raise ValueError("writer_source_capacity_unbound_reader_first_delivery")
        for block in delivery:
            payload = _reader_first_bound_payload(block, scope)
            delivery_evidence[block.key] = _reader_first_source_evidence(block, payload)
    else:
        bindings = tuple(_delivery_binding(block) for block in delivery)
    if not delivery or bindings != capacity.delivery_bindings:
        raise ValueError("writer_source_capacity_delivery_binding_changed")
    core = [block for block in blocks if block.required or block.key in integrity_keys]
    system, user = _render_channels(sorted(core, key=_block_sort_key))
    core_tokens = estimate_prompt_tokens(system) + estimate_prompt_tokens(user)
    minimum = math.ceil(core_tokens / (1 - safety_margin))
    while math.floor(minimum * (1 - safety_margin)) < core_tokens:
        minimum += 1
    options = {"safety_margin": safety_margin, "require_provenance": True}
    try:
        compiled = compile_prompt(blocks, total_budget_tokens=base_budget_tokens, **options)
    except PromptBudgetError:
        if minimum <= base_budget_tokens:
            raise
        compiled = None
    expanded = compiled is None or not _complete(compiled, core)
    allowance = max(base_budget_tokens, minimum) if expanded else base_budget_tokens
    ceiling = min(capacity.capacity_limit, finite_book_budget.current_capacity())

    def request_capacity(system: str, user: str) -> dict:
        measured = measure_request_capacity(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=None,
            output_tokens=capacity.output_tokens,
            n_candidates=capacity.n_candidates,
            total_budget=ceiling,
        )
        if delivery_evidence:
            measured = {**measured, "reader_first_delivery_evidence": delivery_evidence}
        return measured

    # Do not process optional material, or construct an over-capacity prompt,
    # when the complete source alone already cannot fit the physical request.
    core_capacity = request_capacity(system, user)
    if not core_capacity["budget_passed"]:
        raise ModelRequestCapacityError(core_capacity)
    # An input allowance is a ceiling, not padding. Even a source-complete
    # base candidate may include optional text that exceeds physical capacity.
    if expanded or not request_capacity(compiled.system, compiled.user)["budget_passed"]:
        compiled = compile_prompt(core, total_budget_tokens=allowance, **options)
        if not _complete(compiled, core):
            raise PromptBudgetError(
                "writer source core incomplete",
                required_keys=sorted(integrity_keys),
            )
        # Optional styles/references may consume only remaining space. Never
        # raise a legacy block's required bit, layer, or provenance to reserve it.
        selected = list(core)
        rejected = {}
        for candidate in sorted((b for b in blocks if b not in core), key=_block_sort_key):
            trial = compile_prompt([*selected, candidate], total_budget_tokens=allowance, **options)
            if not _complete(trial, core):
                rejected[candidate.key] = "reserved_source_core"
            elif not request_capacity(trial.system, trial.user)["budget_passed"]:
                rejected[candidate.key] = "physical_request_capacity"
            else:
                selected.append(candidate)
                compiled = trial
        if rejected:
            compiled = compiled.model_copy(
                update={
                    "report": compiled.report.model_copy(
                        update={
                            "dropped": tuple(dict.fromkeys((*compiled.report.dropped, *rejected))),
                            "drop_reasons": {
                                **compiled.report.drop_reasons,
                                **rejected,
                            },
                        }
                    )
                }
            )
    if not _complete(compiled, core):
        raise PromptBudgetError(
            "writer source core incomplete",
            required_keys=sorted(integrity_keys),
        )
    measured = request_capacity(compiled.system, compiled.user)
    if not measured["budget_passed"]:
        raise ModelRequestCapacityError(measured)
    receipt = {
        "schema": SCHEMA,
        "base_budget_tokens": base_budget_tokens,
        "minimum_core_budget_tokens": minimum,
        "core_input_tokens": core_tokens,
        "input_allowance_tokens": allowance,
        "expanded": allowance > base_budget_tokens,
        "integrity_keys": sorted(block.key for block in core),
        "source_block_sha256": {
            block.key: sha256(block.text.encode()).hexdigest() for block in core
        },
        "model": capacity.model,
        "endpoint_hash": capacity.endpoint_hash,
        "core_capacity": core_capacity,
        "capacity": measured,
        "source_only": True,
        "financial_authorization": False,
        "gateway_revalidation_required": True,
    }
    if delivery_evidence:
        receipt["reader_first_delivery_evidence"] = delivery_evidence
    return compiled.model_copy(
        update={
            "report": compiled.report.model_copy(
                update={
                    "writer_source_capacity": receipt,
                }
            )
        }
    )
