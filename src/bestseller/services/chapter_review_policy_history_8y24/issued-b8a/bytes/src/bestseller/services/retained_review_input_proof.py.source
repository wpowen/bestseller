"""Read-only input/route proof for explicit completed-review reconciliation.

No gateway, prompt rerender, journal preparation, flush or commit is performed.
This supplements the caller's code-equivalence and semantic-dependency fences;
it does not establish that the retained literary judgments are correct.
"""

from __future__ import annotations

# ORM/context adapters accept heterogeneous mapped rows and SELECT results.
# ruff: noqa: ANN001,ANN002,ANN003,ANN202,ANN401
from collections.abc import Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from asyncio import current_task
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    ReviewReportModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services import llm, model_catalog
from bestseller.services.context import build_chapter_writer_context
from bestseller.services.review_source_context import SOURCE_POLICY, load_review_source_context
from bestseller.settings import AppSettings, apply_runtime_llm_profile


def _require(condition: Any, code: str) -> None:
    if not condition:
        raise ValueError(f"retained_review_input_{code}")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


_AUDIT_POINTER = "/current_source_channels/source_selection_receipt/original_source_sha256"
_SELECTION_POLICY = "typed-world-and-relevant-current-state.v1"
_SOURCE_AUTHORITY = (
    "Exact adopted prose establishes occurrences; source-bound observations "
    "retain temporal scope. Current designs are plans, not past events."
)


def _valid_sha(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _retained_source(prompt: str, expected) -> dict | None:
    """Only the exact canonical source object bound by the paid receipt qualifies."""
    from bestseller.services.editable_plan_source import REVIEW_SOURCE_SCHEMA

    if not _valid_sha(expected):
        return None
    decoder = json.JSONDecoder()
    position = 0
    while True:
        start = prompt.find("{", position)
        if start < 0:
            return None
        position = start + 1
        try:
            candidate, end = decoder.raw_decode(prompt, start)
        except (ValueError, RecursionError):
            continue
        if (not isinstance(candidate, dict)
                or candidate.get("schema") not in {SOURCE_POLICY, REVIEW_SOURCE_SCHEMA}):
            continue
        canonical = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if prompt[start:end] == canonical and _hash(candidate) == expected:
            return candidate



def _primary_source_integrity(request, receipt, source) -> dict | None:
    """Bind a physically verified primary request to its entire current source.

    Payment, effective-input and full-return verification remain mandatory in
    the caller before this source-only check. A transport receipt is no grade.
    """
    if request is None or not isinstance(receipt, Mapping):
        return None
    if receipt.get("primary_source_transport") is not None:
        from bestseller.services.primary_review_transport import verify_primary_review_transport
        verify_primary_review_transport(
            request.system_prompt, request.user_prompt, source_context=source,
            receipt=receipt, tools=request.tools, output_tokens=request.max_tokens_override,
        )
        return {
            "schema": "retained-primary-source-transport-proof.v1",
            "source_sha256": _hash(source),
            "wire_user_sha256": hashlib.sha256(request.user_prompt.encode()).hexdigest(),
            "source_complete": True, "grade_recomputed": False,
        }
    if (receipt.get("source_complete") is True
            and receipt.get("source_sha256") == _hash(source)
            and json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            in request.user_prompt):
        return {"schema": "retained-primary-literal-source-proof.v1",
                "source_sha256": _hash(source), "source_complete": True}
    return None


def _known_selection_source(source) -> bool:
    """Recognize the existing complete typed projection, never an arbitrary hash field."""
    from bestseller.services import editable_plan_source as editable

    if not isinstance(source, dict):
        return False
    schema = source.get("schema")
    base = {"schema", "adopted_continuity", "current_source_channels", "authority"}
    if schema == editable.REVIEW_SOURCE_SCHEMA:
        base.add("editable_plan")
        if "draft_manuscript_continuity" in source:
            base.remove("adopted_continuity")
            base.add("draft_manuscript_continuity")
        if "source_correction_review" in source:
            from bestseller.services.adopted_source_correction import SCHEMA as CORRECTION_SCHEMA

            correction = source["source_correction_review"]
            if (not isinstance(correction, dict) or correction.get("schema") != CORRECTION_SCHEMA
                    or correction.get("authority") != "source_bound_observation_and_edit_proposal"
                    or correction.get("grade_asserted") is not False
                    or correction.get("adoption_authorized") is not False
                    or correction.get("fresh_review_required") is not True
                    or correction.get("is_observation_not_canon") is not True):
                return False
            base.add("source_correction_review")
        plan = source.get("editable_plan")
        if (not isinstance(plan, dict) or plan.get("schema") != editable.SCHEMA
                or plan.get("authority") != editable.AUTHORITY
                or plan.get("interpretation") != editable.INTERPRETATION):
            return False
        if plan.get("status") == "available":
            if (set(plan) != {"schema", "authority", "interpretation", "status", "binding", "plan"}
                    or not isinstance(plan.get("binding"), dict)
                    or not isinstance(plan.get("plan"), dict)):
                return False
        elif plan.get("status") == "not_available":
            if (set(plan) != {"schema", "authority", "interpretation", "status", "reason", "binding", "plan"}
                    or plan.get("reason") != "no_selected_approved_prefix"
                    or plan.get("binding") is not None or plan.get("plan") is not None):
                return False
        else:
            return False
    elif schema != SOURCE_POLICY:
        return False
    if (
        set(source) != base
        or source["authority"] != _SOURCE_AUTHORITY
        or not isinstance(source.get("draft_manuscript_continuity"
                                      if "draft_manuscript_continuity" in base
                                      else "adopted_continuity"), dict)
    ):
        return False
    selected = source["current_source_channels"]
    required = {
        "schema_version",
        "project",
        "chapter",
        "approved_creation_contracts",
        "explicit_creator_requirements",
        "established_facts",
        "prior_prose_reference",
        "story_bible",
        "quality_targets",
        "source_selection_receipt",
    }
    optional = {
        "approved_effect_contracts",
        "approved_chapter_contract",
        "approved_scene_contract",
        "approved_world_authority",
        "identity_authority",
        "adopted_continuity",
    }
    if (
        not isinstance(selected, dict)
        or not required <= selected.keys()
        or not selected.keys() <= required | optional
        or selected["schema_version"] != "commercial-reader-source-input.v2"
        or any(
            not isinstance(selected[key], dict)
            for key in required - {"schema_version", "source_selection_receipt"}
        )
    ):
        return False
    receipt = selected["source_selection_receipt"]
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {
            "policy",
            "original_source_sha256",
            "original_bible_sha256",
            "retained_bible_sha256",
            "original_bible_characters",
            "retained_bible_characters",
            "retained_bible_fields",
            "omitted_bible_fields",
            "authority",
        }
        or receipt["policy"] != _SELECTION_POLICY
        or receipt["authority"]
        != "unverified_context; adopted facts and explicit clauses remain separate"
        or any(
            not _valid_sha(receipt[key])
            for key in ("original_source_sha256", "original_bible_sha256", "retained_bible_sha256")
        )
        or any(
            type(receipt[key]) is not int or receipt[key] < 0
            for key in ("original_bible_characters", "retained_bible_characters")
        )
        or any(
            not isinstance(receipt[key], list)
            or any(not isinstance(item, str) for item in receipt[key])
            or len(set(receipt[key])) != len(receipt[key])
            for key in ("retained_bible_fields", "omitted_bible_fields")
        )
    ):
        return False
    return (
        receipt["retained_bible_sha256"] == _hash(selected["story_bible"])
        and set(receipt["retained_bible_fields"]) == set(selected["story_bible"])
        and receipt["retained_bible_characters"]
        == len(json.dumps(selected["story_bible"], ensure_ascii=False, default=str))
    )


def _audit_source_compatibility(prompt: str, receipt: Mapping[str, Any], current) -> dict | None:
    """An explicit semantic compatibility edge, not equivalence of paid request bytes.

    The opaque raw bundles are unavailable and are NOT asserted equivalent.
    Only one typed audit digest may differ; every other admitted field and
    provenance receipt must remain canonically identical.
    """
    if receipt.get("source_complete") is not True:
        return None
    original = _retained_source(prompt, receipt.get("source_sha256"))
    if not _known_selection_source(original) or not _known_selection_source(current):
        return None
    old, new = deepcopy(original), deepcopy(current)
    old_audit = old["current_source_channels"]["source_selection_receipt"].pop(
        "original_source_sha256"
    )
    new_audit = new["current_source_channels"]["source_selection_receipt"].pop(
        "original_source_sha256"
    )
    if old_audit == new_audit or json.dumps(
        old, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ) != json.dumps(new, sort_keys=True, ensure_ascii=False, separators=(",", ":")):
        return None
    return {
        "schema": "retained-review-source-audit-compatibility.v1",
        "source_schema": current["schema"],
        "selection_policy": _SELECTION_POLICY,
        "old_full_source_sha256": _hash(original),
        "current_full_source_sha256": _hash(current),
        "selected_semantic_sha256": _hash(old),
        "changed_pointer": _AUDIT_POINTER,
        "old_opaque_audit_sha256": old_audit,
        "current_opaque_audit_sha256": new_audit,
        "original_source_verified": True,
        "raw_bundle_equivalence_asserted": False,
        "paid_request_bytes_equivalence_asserted": False,
    }


def _source_comparison(prompt: str, receipt: Mapping[str, Any], current: Any) -> dict[str, Any]:
    """Diagnostic only: hashes and JSON pointers, never source values or raw text."""
    expected = receipt.get("source_sha256")
    expected = expected if _valid_sha(expected) else None
    diagnostic = {
        "schema": "retained-review-source-comparison.v1",
        "original_source_verified": False,
        "expected_old_source_sha256": expected,
        "old_source_sha256": None,
        "new_source_sha256": _hash(current),
        "retained_receipt_complete": receipt.get("source_complete") is True,
        "differences": [],
        "limit": 64,
        "truncated": False,
    }
    original = _retained_source(prompt, expected)
    if original is None:
        diagnostic["status"] = "original_source_not_found_or_unverified"
        return diagnostic
    diagnostic.update(original_source_verified=True, old_source_sha256=expected, status="compared")
    missing = object()

    def visit(old, new, pointer):
        if old is not missing and new is not missing and _hash(old) == _hash(new):
            return
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(old.keys() | new.keys()):
                visit(
                    old.get(key, missing),
                    new.get(key, missing),
                    pointer + "/" + key.replace("~", "~0").replace("/", "~1"),
                )
                if diagnostic["truncated"]:
                    return
            return
        if isinstance(old, list) and isinstance(new, list):
            for index in range(max(len(old), len(new))):
                visit(
                    old[index] if index < len(old) else missing,
                    new[index] if index < len(new) else missing,
                    pointer + f"/{index}",
                )
                if diagnostic["truncated"]:
                    return
            return
        if old is missing and isinstance(new, (dict, list)) and new:
            for key, value in new.items() if isinstance(new, dict) else enumerate(new):
                visit(
                    missing, value, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
                )
                if diagnostic["truncated"]:
                    return
            return
        if new is missing and isinstance(old, (dict, list)) and old:
            for key, value in old.items() if isinstance(old, dict) else enumerate(old):
                visit(
                    value, missing, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
                )
                if diagnostic["truncated"]:
                    return
            return
        if len(diagnostic["differences"]) == diagnostic["limit"]:
            diagnostic["truncated"] = True
            return
        diagnostic["differences"].append(
            {
                "pointer": pointer,
                "old_present": old is not missing,
                "new_present": new is not missing,
                "old_sha256": _hash(old) if old is not missing else None,
                "new_sha256": _hash(new) if new is not missing else None,
            }
        )

    visit(original, current, "")
    return diagnostic


class _ReadOnlySession:
    """Prevent context helpers from autoflushing or rebuilding retrieval indexes."""

    _bestseller_read_views_validate_only = True

    def __init__(self, session) -> None:
        self._session = session

    @property
    def no_autoflush(self):
        return self._session.no_autoflush

    @property
    def dirty(self):
        return tuple(self._session.dirty)

    @property
    def new(self):
        return tuple(self._session.new)

    @property
    def deleted(self):
        return tuple(self._session.deleted)

    @asynccontextmanager
    async def begin_nested(self):
        # AsyncSession.begin_nested() unconditionally flushes pending ORM work.
        # Context readers need only a database savepoint for recoverable SELECT
        # errors, so use the existing Core connection without an ORM checkpoint.
        with self._session.no_autoflush:
            connection = await self._session.connection()
            transaction = await connection.begin_nested()
            try:
                yield self
                await transaction.commit()
            except BaseException:
                if transaction.is_active:
                    await transaction.rollback()
                raise

    async def get(self, model, key, **kwargs):
        kwargs["populate_existing"] = True
        with self._session.no_autoflush:
            return await self._session.get(model, key, **kwargs)

    async def _query(self, method, statement, *args, **kwargs):
        _require(getattr(statement, "is_select", False), "readonly_query_required")
        with self._session.no_autoflush:
            return await getattr(self._session, method)(statement, *args, **kwargs)

    async def scalar(self, statement, *args, **kwargs):
        return await self._query("scalar", statement, *args, **kwargs)

    async def scalars(self, statement, *args, **kwargs):
        return await self._query("scalars", statement, *args, **kwargs)

    async def execute(self, statement, *args, **kwargs):
        return await self._query("execute", statement, *args, **kwargs)

    def __getattr__(self, name) -> Any:
        raise ValueError("retained_review_input_readonly_operation_required")


def _fresh_catalog() -> tuple[dict[str, Any], str]:
    # Do not clear or consult either catalog/project-route global cache.
    raw = model_catalog._CATALOG_PATH.read_bytes()
    data = yaml.safe_load(raw)
    _require(isinstance(data, Mapping) and isinstance(data.get("models"), list), "catalog_invalid")
    entries = {}
    for value in data["models"]:
        entry = model_catalog.ModelCatalogEntry.model_validate(value)
        _require(entry.id not in entries, "catalog_duplicate_id")
        entries[entry.id] = entry
    return entries, hashlib.sha256(raw).hexdigest()



# Verification-only exact request bindings. New gateway dispatch never reads
# this context, and child tasks cannot inherit an actionable binding.
_HISTORICAL_ROUTE_WITNESSES = ContextVar('retained_exact_historical_routes', default=None)


def _route_owner():
    try:
        return current_task()
    except RuntimeError:
        return None


def _route_request_key(request):
    keys = ('logical_role', 'project_id', 'workflow_run_id', 'step_run_id',
            'prompt_template', 'prompt_version', 'system_prompt', 'user_prompt',
            'tools', 'tool_choice', 'model_tier', 'model_catalog_key',
            'temperature_override', 'max_tokens_override', 'cache_system', 'messages_override')
    return _hash({key: request.model_dump(mode='json')[key] for key in keys})


def _remember_historical_route(request, run):
    receipt = (run.metadata_json or {}).get('effective_input_receipt') or {}
    historical_protocol = (request.prompt_template, request.prompt_version) in {
        ('draft_continuity_hard_safety', '3.0'),
        ('draft_continuity_hard_safety', '4.0-completion-supplement'),
        ('foundation_creative_delta_review', '1.2-source-sections'),
        ('foundation_creative_delta_review', '1.3-dependency-scope'),
        ('planning_draft_admission', 'volume-source-admission.v3'),
    }
    if (not historical_protocol
        or request.model_catalog_key is not None
        or receipt.get('schema') != 'effective-input.v1'
        or receipt.get('hash') != run.prompt_hash or receipt.get('model') != run.model_name):
        return
    owner = _route_owner()
    previous = _HISTORICAL_ROUTE_WITNESSES.get()
    witnesses = dict(previous[1]) if previous and previous[0] is owner else {}
    if len(witnesses) >= 128:
        witnesses = {}
    key = _route_request_key(request)
    binding = {'project_id': str(run.project_id), 'run_id': str(run.id),
               'model': run.model_name, 'input_hash': run.prompt_hash}
    _require(key not in witnesses or witnesses[key] == binding, 'historical_route_ambiguous')
    witnesses[key] = binding
    _HISTORICAL_ROUTE_WITNESSES.set((owner, witnesses))


def _historical_route_role(settings, project, request, catalog):
    state = _HISTORICAL_ROUTE_WITNESSES.get()
    if state is None or state[0] is not _route_owner() or request.model_catalog_key is not None:
        return None
    binding = state[1].get(_route_request_key(request))
    if binding is None:
        return None
    _require(binding['project_id'] == str(project.id), 'historical_route_project')
    selected = catalog.get(model_catalog.selected_model_id(project.metadata_json))
    if selected is None or selected.model == binding['model']:
        return None
    base = llm._get_role_settings(apply_runtime_llm_profile(settings), request.logical_role)
    matches = []
    for entry in catalog.values():
        if entry.model != binding['model'] or not model_catalog._resolve_availability(entry)[0]:
            continue
        role = llm._apply_model_override(base, entry)
        if request.model_tier == 'strong' and role.model_override:
            role = role.model_copy(update={'model': role.model_override})
        if llm._effective_input_hash(request, role) == binding['input_hash']:
            matches.append(role)
    _require(bool(matches), 'historical_route_unprovable')
    # Candidate routes must reproduce the entire old effective input, including
    # endpoint, tools, temperature, output cap, thinking and stream semantics.
    # The caller still rechecks the immutable physical ledger/result afterward.
    _require(len({role.api_base for role in matches}) == 1, 'historical_route_ambiguous')
    return matches[0]


def _fresh_role(settings, project, request, catalog):
    historical = _historical_route_role(settings, project, request, catalog)
    if historical is not None:
        return historical
    effective = apply_runtime_llm_profile(settings)
    role = llm._get_role_settings(effective, request.logical_role)
    for key in (model_catalog.selected_model_id(project.metadata_json), request.model_catalog_key):
        if not key:
            continue
        entry = catalog.get(key)
        _require(
            entry is not None and model_catalog._resolve_availability(entry)[0], "route_unavailable"
        )
        role = llm._apply_model_override(role, entry)
    if request.model_tier == "strong" and role.model_override:
        role = role.model_copy(update={"model": role.model_override})
    _require(not effective.llm.mock, "mock_route")
    from bestseller.services.production_strategy import apply_production_stream_settings
    return apply_production_stream_settings(request,role,
        profile=(project.metadata_json or {}).get('production_profile'))


def _primary6(run) -> bool:
    return (
        run.prompt_template == "chapter_review"
        and run.prompt_version == "6.0-structured-body-submission"
    )


def _primary6_body_in_prompt(prompt: str, body: str) -> bool:
    """Bind the designated current body, not an occurrence in prior sources."""
    from bestseller.services.critic_evidence_gate import build_critic_body_anchors

    opening, ending = "CURRENT_DRAFT:\n", "\nEND_CURRENT_DRAFT\nBODY_ANCHOR_CATALOG:\n"
    if opening in prompt or ending in prompt:
        if prompt.count(opening) != 1 or prompt.count(ending) != 1:
            return False
        current, catalog = prompt.split(opening, 1)[1].split(ending, 1)
        if current != body:
            try:
                current = json.loads(current)
            except (ValueError, TypeError):
                return False
            if not isinstance(current, str) or current != body:
                return False
        try:
            retained, _ = json.JSONDecoder().raw_decode(catalog.lstrip())
        except ValueError:
            return False
        return retained == list(build_critic_body_anchors(body))
    # A fully JSON-encoded task must bind both named current-body and catalog
    # fields in the SAME object. Generic source/content/body leaves cannot do so.
    decoder = json.JSONDecoder()
    candidates = []
    for start, char in enumerate(prompt):
        if char != "{":
            continue
        try:
            item, _ = decoder.raw_decode(prompt, start)
        except ValueError:
            continue
        if isinstance(item, dict) and "CURRENT_DRAFT" in item:
            candidates.append(item)
    return (
        len(candidates) == 1
        and candidates[0].get("CURRENT_DRAFT") == body
        and candidates[0].get("BODY_ANCHOR_CATALOG") == list(build_critic_body_anchors(body))
    )


async def _primary6_body(session, run, *, project_id, chapter_id):
    from bestseller.services.primary_review_submission import LEGACY_SUBMISSION_POLICY

    metadata = run.metadata_json or {}
    expected = metadata.get("primary_review_body_sha256")
    _require(
        run.project_id == project_id
        and metadata.get("primary_review_submission_policy") == LEGACY_SUBMISSION_POLICY
        and _valid_sha(expected),
        "primary6_body_metadata",
    )
    rows = list(
        await session.scalars(
            select(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.project_id == project_id,
                ChapterDraftVersionModel.chapter_id == chapter_id,
            )
            .execution_options(populate_existing=True)
        )
    )
    matches = [
        row
        for row in rows
        if hashlib.sha256((row.content_md or "").encode()).hexdigest() == expected
    ]
    _require(len(matches) == 1, "primary6_body_not_unique")
    return matches[0]


def _retained_request(settings, run, *, chapter_body=None):
    _require(settings.artifact_store.mode == "local", "payload_store_unavailable")
    root = Path(settings.artifact_store.local_dir).expanduser().resolve()
    ref = run.request_payload_ref
    _require(isinstance(ref, str) and bool(ref), "request_payload_missing")
    path = (root / ref).resolve()
    _require(path.is_relative_to(root), "request_payload_outside_store")
    original_bytes = path.read_bytes()
    payload = json.loads(original_bytes)
    reconstruct_tools = (
        _primary6(run)
        and payload.get("replayable") is False
        and payload.get("replayability_reasons") == ["tools_not_retained"]
    )
    _require(
        payload.get("schema_version") == "llm-payload.v1"
        and payload.get("kind") == "request"
        and (payload.get("replayable") is True or reconstruct_tools),
        "request_not_replayable",
    )
    for key, expected in (
        ("llm_run_id", str(run.id)),
        ("workflow_run_id", str(run.workflow_run_id)),
        ("project_id", str(run.project_id)),
        ("logical_role", run.logical_role),
        ("prompt_template", run.prompt_template),
        ("prompt_version", run.prompt_version),
    ):
        _require(payload.get(key) == expected, "request_identity_mismatch")
    messages = payload.get("messages") or {}
    _require(
        messages.get("source") == "system_user"
        and messages.get("replayable") is (False if reconstruct_tools else True),
        "request_messages_unavailable",
    )
    texts = {}
    for key in ("system", "user"):
        item = messages.get(key) or {}
        text = item.get("text")
        _require(
            isinstance(text, str)
            and text
            and item.get("truncated") is False
            and item.get("original_chars") == len(text),
            "request_prompt_incomplete",
        )
        texts[key] = text
    requested = payload.get("requested")
    fields = {
        "model_tier",
        "model_catalog_key",
        "temperature_override",
        "max_tokens_override",
        "cache_system",
    }
    _require(
        isinstance(requested, dict) and fields <= requested.keys(), "request_parameters_missing"
    )
    _require(
        payload.get("replayability_reasons", [])
        == (["tools_not_retained"] if reconstruct_tools else []),
        "request_unknown_missing_fields",
    )
    tool_fields = {}
    # Retained tools are physical request evidence. Accept the explicit fields
    # at the top level or in requested; conflicting aliases fail closed.
    for key in ("tools", "tool_choice"):
        if key in payload and key in requested:
            _require(payload[key] == requested[key], "retained_tool_alias_conflict")
        if key in payload or key in requested:
            tool_fields[key] = deepcopy(payload[key] if key in payload else requested[key])
    reconstruction = None
    if _primary6(run):
        from bestseller.services.primary_review_submission import (
            LEGACY_SUBMISSION_POLICY,
            primary_review_submission_tools,
        )

        metadata = run.metadata_json or {}
        _require(
            metadata.get("primary_review_submission_policy") == LEGACY_SUBMISSION_POLICY
            and isinstance(chapter_body, str)
            and chapter_body
            and hashlib.sha256(chapter_body.encode()).hexdigest()
            == metadata.get("primary_review_body_sha256")
            and _primary6_body_in_prompt(texts["user"], chapter_body),
            "primary6_body_binding",
        )
    if reconstruct_tools:
        _require(not tool_fields, "primary6_partial_tool_retention")
        tool_fields = {
            "tools": primary_review_submission_tools(
                chapter_body, submission_policy=LEGACY_SUBMISSION_POLICY,
            ),
            "tool_choice": "required",
        }
        reconstruction = {
            "schema": "primary6-required-tools-reconstruction.v1",
            "physical_request_file_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "original_request_replayable": False,
            "original_messages_replayable": False,
            "original_replayability_reasons": ["tools_not_retained"],
            "physical_tool_schema_retained": False,
            "tool_schema_origin": "code-derived-required-tools",
            "submission_policy": LEGACY_SUBMISSION_POLICY,
            "body_sha256": hashlib.sha256(chapter_body.encode()).hexdigest(),
            "tool_schema_sha256": _hash(tool_fields["tools"]),
            "tool_choice": "required",
        }
    elif tool_fields:
        _require(set(tool_fields) == {"tools", "tool_choice"}, "retained_tools_incomplete")
        receipts = payload.get("structured_payload_receipts")
        _require(isinstance(receipts, dict), "retained_tools_receipt_missing")
        for key, value in tool_fields.items():
            encoded = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            record = receipts.get(key)
            _require(
                isinstance(record, dict)
                and record.get("status") == "retained"
                and record.get("encoding") == "json-utf8"
                and record.get("truncated") is False
                and record.get("original_chars") == len(encoded)
                and type(record.get("limit_chars")) is int
                and record["limit_chars"] >= len(encoded)
                and record.get("sha256") == hashlib.sha256(encoded.encode()).hexdigest(),
                "retained_tools_receipt_mismatch",
            )
    if _primary6(run):
        _require(
            tool_fields.get("tools") is not None and tool_fields.get("tool_choice") == "required",
            "primary6_required_tools_missing",
        )
    request = llm.LLMCompletionRequest(
        logical_role=run.logical_role,
        system_prompt=texts["system"],
        user_prompt=texts["user"],
        fallback_response="proof only; never dispatched",
        project_id=run.project_id,
        workflow_run_id=run.workflow_run_id,
        step_run_id=run.step_run_id,
        prompt_template=run.prompt_template,
        prompt_version=run.prompt_version,
        **{key: requested[key] for key in fields},
        **tool_fields,
    )
    if reconstruction is not None:
        request.metadata["retained_request_reconstruction"] = reconstruction
    _remember_historical_route(request, run)
    return request


async def _physical_return(session, run, *, scope, expected_hash, expected_model,
                           allow_unbound_workflow=False):
    from bestseller.services.budget import execution_scope_block_code

    # Explicit technical provenance verification may preserve an original NULL
    # workflow. Ordinary retained-review callers keep their existing binding.
    _require(type(allow_unbound_workflow) is bool, "unbound_workflow_policy_invalid")

    _require(
        run.prompt_hash == expected_hash
        and run.model_name == expected_model
        and run.provider == llm._provider_from_model(expected_model)
        and run.provider not in {"mock", "fallback", "test"},
        "effective_route_mismatch",
    )
    metadata = run.metadata_json or {}
    _require(
        (metadata.get("effective_input_receipt") or {}).get("hash") == expected_hash,
        "effective_receipt_mismatch",
    )
    execution = metadata.get("execution_receipt") or {}
    _require(execution.get("scope_id") == scope, "paid_scope_mismatch")
    budget = await session.get(ExecutionBudgetModel, scope)
    blocked = execution_scope_block_code(budget) if budget is not None else "missing"
    _require(
        blocked in (None, "execution_deadline_exhausted", "execution_budget_exhausted"),
        "paid_scope_unsettled",
    )
    operation = await session.get(ExecutionOperationModel, execution.get("operation_key"))
    attempts = [item for item in execution.get("attempts", []) if item.get("status") == "completed"]
    _require(
        operation is not None
        and operation.scope_id == scope
        and operation.status == "completed"
        and operation.input_hash == expected_hash
        and len(attempts) == 1,
        "paid_operation_unavailable",
    )
    attempt = await session.get(ExecutionAttemptModel, attempts[0]["attempt_id"])
    _require(
        attempt is not None
        and attempt.scope_id == scope
        and attempt.status == "completed"
        and attempt.operation_key == operation.key
        and attempt.input_hash == expected_hash
        and attempt.model_name == expected_model,
        "paid_attempt_mismatch",
    )
    key = hashlib.sha256(f"transport-result.v1\n{scope}\n{attempt.id}".encode()).hexdigest()
    transport = await session.get(ExecutionOperationModel, key)
    _require(
        transport is not None
        and transport.scope_id == scope
        and transport.status == "transport_result",
        "paid_transport_missing",
    )
    proof = transport.result_json or {}
    _require(
        proof.get("operation_key") == operation.key
        and proof.get("attempt_id") == attempt.id
        and proof.get("input_hash") == expected_hash,
        "paid_transport_mismatch",
    )
    paid = llm.LLMCompletionResult.model_validate(proof.get("result"))
    completed = llm.LLMCompletionResult.model_validate(operation.result_json)
    for field in (
        "content",
        "provider",
        "model_name",
        "tool_calls",
        "raw_message",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "fallback_used",
        "fallback_source",
    ):
        _require(getattr(paid, field) == getattr(completed, field), "paid_return_mismatch")
    _require(
        not paid.fallback_used
        and paid.model_name == run.model_name
        and paid.provider == run.provider
        and paid.finish_reason == run.finish_reason,
        "paid_result_mismatch",
    )
    origin = (paid.metadata or {}).get("paid_call_origin") or {}
    for field, expected in (
        ("project_id", str(run.project_id)),
        ("workflow_run_id", None if allow_unbound_workflow and run.workflow_run_id is None
         else str(run.workflow_run_id)),
        ("logical_role", run.logical_role),
        ("prompt_template", run.prompt_template),
        ("prompt_version", run.prompt_version),
        ("prompt_hash", expected_hash),
    ):
        _require(origin.get(field) == expected, "paid_origin_mismatch")
    _require(
        (paid.metadata.get("effective_input_receipt") or {}).get("hash") == expected_hash,
        "paid_input_mismatch",
    )
    return paid, operation.key


async def _commercial_quarantine_proofs(
    session, *, project, chapter, owner, scope, content_md, result, sources
):
    """Authenticate read-only custody; a missing ambient scope grants no send."""
    from contextlib import nullcontext

    from bestseller.services.budget import bind_execution_scope, current_execution_scope
    from bestseller.services.commercial_slot_quarantine import load_verified_quarantined_slot_proofs
    from bestseller.services.retry_ledger import project_execution_scope

    ambient = current_execution_scope()
    _require(ambient in {None, scope}, "commercial_original_scope_mismatch")
    if ambient is None:
        stored_project = await session.get(ProjectModel, project.id, populate_existing=True)
        stored_owner = await session.get(WorkflowRunModel, owner.id, populate_existing=True)
        budget = await session.get(ExecutionBudgetModel, scope, populate_existing=True)
        _require(
            stored_project is not None
            and stored_owner is not None
            and budget is not None
            and stored_project.id == chapter.project_id == stored_owner.project_id
            and stored_owner.scope_type == "chapter"
            and stored_owner.scope_id == chapter.id
            and (stored_project.metadata_json or {}).get("execution_scope_id") == scope
            and (stored_owner.metadata_json or {}).get("execution_scope_id") == scope
            and project_execution_scope(stored_project) == scope
            and budget.scope_id == scope,
            "commercial_explicit_read_scope_required",
        )
    with bind_execution_scope(scope) if ambient is None else nullcontext():
        return await load_verified_quarantined_slot_proofs(
            session,
            project_id=project.id,
            chapter_id=chapter.id,
            content_md=content_md,
            result=result,
            sources=sources,
        )


def _projection_audit_compatibility(retained, current, *, sources, source_compatibility,
                                  packet_witnesses=None):
    """One derived audit hash may change after a verified typed source audit edge.

    Caller has already verified every physical slot and the exact derived
    assessment. The original packet must also be reconstructed from only that
    audit leaf and found in a physically bound retained request. This creates a
    new proof, never edits the retained projection or claims old/new wire equality.
    """
    if (not isinstance(source_compatibility, Mapping)
            or source_compatibility.get("schema") != "retained-review-source-audit-compatibility.v1"
            or source_compatibility.get("changed_pointer") != _AUDIT_POINTER
            or source_compatibility.get("original_source_verified") is not True
            or source_compatibility.get("raw_bundle_equivalence_asserted") is not False
            or source_compatibility.get("paid_request_bytes_equivalence_asserted") is not False):
        return None
    if (source_compatibility.get("selection_policy") != _SELECTION_POLICY
            or source_compatibility.get("old_opaque_audit_sha256")
                == source_compatibility.get("current_opaque_audit_sha256")):
        return None
    if not all(_valid_sha(source_compatibility.get(key)) for key in (
            "old_full_source_sha256", "current_full_source_sha256", "selected_semantic_sha256",
            "old_opaque_audit_sha256", "current_opaque_audit_sha256")):
        return None
    if not isinstance(retained, Mapping) or not isinstance(current, Mapping):
        return None
    old, new = deepcopy(dict(retained)), deepcopy(dict(current))
    for value in (old, new):
        if (value.get("schema") != "commercial-action-projection.v1"
                or value.get("status") != "projected"
                or any(value.get(key) is not False for key in (
                    "scores_modified", "raw_cohort_modified", "grants_slots", "authorizes_replay"))):
            return None
    old_hash, new_hash = old.pop("source_text_sha256", None), new.pop("source_text_sha256", None)
    if (not _valid_sha(old_hash) or not _valid_sha(new_hash) or old_hash == new_hash
            or _hash(old) != _hash(new)
            or new_hash != hashlib.sha256(sources.text.encode()).hexdigest()):
        return None
    if (not isinstance(packet_witnesses, list) or not packet_witnesses
            or any(not isinstance(witness, Mapping)
                   or witness.get("schema") != "retained-commercial-packet-audit-proof.v1"
                   or witness.get("packet_sha256") != old_hash
                   or witness.get("current_packet_sha256") != new_hash
                   or witness.get("changed_pointer") != "/source_context/source_selection_receipt/original_source_sha256"
                   or witness.get("literal_occurrences") != 1
                   or not all(_valid_sha(witness.get(key)) for key in (
                       "effective_input_hash", "operation_key", "old_opaque_audit_sha256",
                       "current_opaque_audit_sha256")) for witness in packet_witnesses)):
        return None
    return {
        "schema": "retained-commercial-projection-audit-compatibility.v1",
        "changed_pointer": "/source_text_sha256",
        "retained_projection_sha256": _hash(retained), "current_projection_sha256": _hash(current),
        "retained_source_text_sha256": old_hash, "current_source_text_sha256": new_hash,
        "unchanged_projection_semantics_sha256": _hash(old),
        "typed_source_audit_compatibility": deepcopy(dict(source_compatibility)),
        "historical_source_packet_reconstructed": True,
        "paid_packet_witnesses": deepcopy(packet_witnesses),
        "paid_request_bytes_equivalence_asserted": False,
        "scores_modified": False, "raw_cohort_modified": False, "grants_slots": False,
        "authorizes_replay": False,
    }


async def _commercial_proof(
    session, settings, *, project, chapter, draft, report, catalog, scope, commercial_sources=None,
    source_compatibility=None,
):
    from bestseller.services.chapter_llm_quality_judge import (
        _judge_samples_count,
        resolve_commercial_judge_model_key,
    )
    from bestseller.services.commercial_judge_recovery import _BINDING_KEYS, _EXACT_AUDIT_KEYS
    from bestseller.services.commercial_review_tool import submitted_review_payload

    evidence = report.structured_output.get("evidence_summary") or {}
    commercial = evidence.get("llm_commercial_judge")
    projection = evidence.get("commercial_action_projection")
    original = commercial
    if projection is not None:
        from bestseller.services.commercial_source_evidence import CommercialSources

        _require(isinstance(commercial_sources, CommercialSources), "commercial_sources_required")
        _require(isinstance(commercial, dict) and commercial, "commercial_result_missing")
        original = evidence.get("llm_commercial_judge_original")
        _require(
            isinstance(original, dict) and isinstance(projection, dict),
            "commercial_original_missing",
        )
    if not commercial:
        _require(
            not settings.pipeline.enable_chapter_llm_commercial_judge, "commercial_result_missing"
        )
        return None
    key = _hash(
        {
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
        }
    )
    pointer = ((chapter.metadata_json or {}).get("commercial_component_cohorts") or {}).get(key)
    _require(isinstance(pointer, dict), "commercial_pointer_missing")
    owner = await session.get(WorkflowRunModel, UUID(pointer["workflow_run_id"]))
    _require(
        owner is not None
        and owner.project_id == project.id
        and owner.scope_id == chapter.id
        and owner.scope_type == "chapter",
        "commercial_owner_mismatch",
    )
    journal = ((owner.metadata_json or {}).get("commercial_component_cohorts") or {}).get(key) or {}
    binding = journal.get("binding") or {}
    _require(
        journal.get("schema") == "commercial-component-cohort.v1"
        and pointer.get("binding_sha256") == _hash(binding)
        and all(
            binding.get(k) == str(v)
            for k, v in (
                ("project_id", project.id),
                ("chapter_id", chapter.id),
                ("draft_id", draft.id),
            )
        )
        and binding.get("scope_id") == scope,
        "commercial_binding_mismatch",
    )
    route_request = llm.LLMCompletionRequest(
        logical_role="critic",
        model_tier="strong",
        system_prompt="proof",
        user_prompt="proof",
        fallback_response="proof",
        model_catalog_key=resolve_commercial_judge_model_key(settings),
    )
    if commercial_sources is not None:
        from bestseller.services.commercial_review_tool import review_submission_tools

        # The producer uses native structured tools. A tool-less dummy would
        # spuriously activate the complete-profile text streaming settings.
        route_request = route_request.model_copy(update={
            "tools": review_submission_tools(commercial_sources), "tool_choice": "required",
        })
    role = _fresh_role(settings, project, route_request, catalog)
    _require(
        binding.get("effective_critic_sha256") == _hash(role.model_dump(mode="json")),
        "commercial_effective_route_changed",
    )
    alias_view = None
    if commercial_sources is not None:
        from bestseller.services.commercial_judge_recovery import _verified_source_alias_cohort_view

        alias_view = await _verified_source_alias_cohort_view(
            session, settings, journal=journal, sources=commercial_sources,
            project_id=project.id, scope=scope, expected_model=role.model,
        )
        if alias_view is not None:
            journal = alias_view
    slots = journal.get("slots") or {}
    n = _judge_samples_count()
    names = {f"chapter-quality.independent-v2:{i}" for i in range(n)}
    _require(
        binding.get("samples") == n
        and set(slots) == names
        and all(item.get("state") in {"completed", "quarantined"} for item in slots.values()),
        "commercial_cohort_incomplete",
    )
    aggregate = journal.get("aggregate") or {}
    _require(
        aggregate.get("slots_sha256") == _hash({k: v["report"] for k, v in slots.items()})
        and LLMQualityJudgeResult.model_validate(aggregate.get("report")).model_dump()
        == LLMQualityJudgeResult.model_validate(original).model_dump(),
        "commercial_aggregate_mismatch",
    )
    quarantine_names = {name for name, item in slots.items() if item["state"] == "quarantined"}
    quarantine_proofs = {}
    if quarantine_names:
        from bestseller.services.commercial_source_evidence import CommercialSources

        _require(isinstance(commercial_sources, CommercialSources), "commercial_sources_required")
        quarantine_proofs = await _commercial_quarantine_proofs(
            session,
            project=project,
            chapter=chapter,
            owner=owner,
            scope=scope,
            content_md=draft.content_md,
            result=LLMQualityJudgeResult.model_validate(aggregate["report"]),
            sources=commercial_sources,
        )
        _require(set(quarantine_proofs) == quarantine_names, "commercial_quarantine_set_changed")
    project_actions = projection is not None or bool(quarantine_names)
    if project_actions:
        raw_aggregate = LLMQualityJudgeResult.model_validate(aggregate["report"])
        _require(
            _hash(aggregate["report"])
            in {
                _hash(raw_aggregate.model_dump(mode="json")),
                _hash(raw_aggregate.model_dump(mode="json", by_alias=True)),
            },
            "commercial_aggregate_mismatch",
        )
        cohort = json.loads(raw_aggregate.raw_excerpt)
        if n == 1:
            _require(
                raw_aggregate.model_dump()
                == LLMQualityJudgeResult.model_validate(
                    slots[next(iter(names))]["report"]
                ).model_dump(),
                "commercial_aggregate_slots_mismatch",
            )
        else:
            samples = cohort.get("samples")
            _require(
                isinstance(samples, list)
                and len(samples) == n
                and {sample.get("sample_slot") for sample in samples} == names
                and all(
                    _hash(sample.get("report")) == _hash(slots[sample["sample_slot"]]["report"])
                    for sample in samples
                ),
                "commercial_aggregate_slots_mismatch",
            )
    ids, operations = [], []
    for slot in sorted(names):
        entry = slots[slot]
        if slot in quarantine_names:
            continue  # Full ledger custody above, never a paid return or LlmRun.
        value = LLMQualityJudgeResult.model_validate(entry["report"])
        envelope = json.loads(value.raw_excerpt)
        _require(
            envelope.get("schema_version") == "commercial-slot-evidence.v1"
            and envelope.get("sample_slot") == slot
            and envelope.get("attempt") == entry.get("used_attempts")
            and type(envelope.get("attempt")) is int
            and envelope["attempt"] in (1, 2)
            and envelope.get("source_binding") == binding.get("source_binding")
            and envelope.get("evaluation_contract") == binding.get("evaluation_contract"),
            "commercial_slot_mismatch",
        )
        run = await session.get(LlmRunModel, UUID(value.llm_run_id))
        _require(
            run is not None
            and run.project_id == project.id
            and run.prompt_template == "chapter_commercial_quality_judge",
            "commercial_run_missing",
        )
        source = (run.metadata_json or {}).get("commercial_source_input_receipt") or {}
        _require(
            all(source.get(k) == envelope["source_binding"].get(k) for k in _BINDING_KEYS)
            and all(
                source.get(k) == (envelope.get("exact_source_audit") or {}).get(k)
                for k in _EXACT_AUDIT_KEYS
            ),
            "commercial_source_mismatch",
        )
        source_transport = (run.metadata_json or {}).get("commercial_source_transport")
        if source_transport is not None:
            _require(commercial_sources is not None, "commercial_transport_current_source_required")
            from bestseller.services.commercial_review_transport import (
                verify_commercial_review_transport,
            )

            retained_request = _retained_request(settings, run)
            retained_role = _fresh_role(settings, project, retained_request, catalog)
            _require(
                llm._effective_input_hash(retained_request, retained_role) == run.prompt_hash,
                "commercial_transport_effective_input_mismatch",
            )
            verify_commercial_review_transport(
                retained_request.user_prompt, sources=commercial_sources, receipt=source_transport
            )
            _require(
                hashlib.sha256(retained_request.user_prompt.encode()).hexdigest()
                == source.get("user_prompt_sha256"),
                "commercial_transport_request_hash_mismatch",
            )
        paid, operation = await _physical_return(
            session,
            run,
            scope=scope,
            expected_hash=run.prompt_hash,
            expected_model=role.model,
        )
        raw, submission, submission_errors = submitted_review_payload(paid)
        _require(
            (project_actions or not submission_errors)
            and raw == envelope.get("raw_report")
            and hashlib.sha256(raw.encode()).hexdigest() == envelope.get("raw_report_sha256")
            and submission == (envelope.get("adapter_receipt") or {}).get("submission"),
            "commercial_raw_mismatch",
        )
        if project_actions:
            from bestseller.services.commercial_review_wire import parse_commercial_wire

            _require(
                all(
                    commercial_sources.receipt.get(k) == envelope["source_binding"].get(k)
                    for k in _BINDING_KEYS
                ),
                "commercial_current_source_mismatch",
            )
            _, rebuilt_adapter, wire_errors = parse_commercial_wire(raw, commercial_sources)
            combined_errors = [*submission_errors, *wire_errors]
            if submission_errors:
                rebuilt_adapter["wire_validation_status"] = rebuilt_adapter["status"]
                rebuilt_adapter["status"] = "rejected_submission"
            retained_adapter = envelope.get("adapter_receipt") or {}
            if paid.finish_reason in {"length", "max_tokens", "max_output_tokens"}:
                from bestseller.services.commercial_action_projection import _slot
                from bestseller.services.commercial_judge_recovery import _verified_paid_slot

                # Reuse actual paid-slot reconstruction and the native exact
                # neutral-domain/adapter validator, never a finish-flag waiver.
                neutral, neutral_envelope, _ = await _verified_paid_slot(
                    session, report=entry["report"], sources=commercial_sources,
                    contract=binding["evaluation_contract"], project_id=project.id,
                    scope=scope, slot=slot, expected_model=role.model, settings=settings,
                )
                neutral_slots = []
                neutral_projection, _ = _slot(
                    entry["report"], slot, commercial_sources, {}, neutral_slots,
                )
                _require(
                    neutral.infrastructure_failure and neutral_projection.infrastructure_failure
                    and _hash(neutral.model_dump(mode="json")) == _hash(value.model_dump(mode="json"))
                    and _hash(neutral_projection.model_dump(mode="json")) == _hash(value.model_dump(mode="json"))
                    and _hash(neutral_envelope) == _hash(envelope)
                    and len(neutral_slots) == 1 and neutral_slots[0].get("status") == "unknown"
                    and neutral_slots[0].get("abstains") is True
                    and neutral_slots[0].get("actions_contributed") == 0
                    and neutral_slots[0].get("scores_contributed") == 0,
                    "commercial_truncated_slot_domain_changed",
                )
                combined_errors = deepcopy(neutral_envelope["validation_errors"])
                rebuilt_adapter = deepcopy(neutral_envelope["adapter_receipt"])
            # Historical exact source audit remains bound to the physical run
            # above; the projector separately verifies current semantic identity.
            rebuilt_adapter["source_binding"] = retained_adapter.get("source_binding")
            rebuilt_adapter["submission"] = submission
            _require(
                combined_errors == envelope.get("validation_errors")
                and _hash(rebuilt_adapter) == _hash(retained_adapter),
                "commercial_producer_adapter_mismatch",
            )
        ids.append(str(run.id))
        operations.append(operation)
    paid_slots = n - len(quarantine_names)
    _require(
        len(set(ids)) == paid_slots and len(set(operations)) == paid_slots,
        "commercial_duplicate_paid_slots",
    )
    if project_actions:
        from bestseller.services.commercial_action_projection import project_commercial_actions
        from bestseller.services.quality_gates_config import get_quality_gates_config

        derived, rebuilt_projection = project_commercial_actions(
            original,
            sources=commercial_sources,
            gate_config=get_quality_gates_config().l6_gate,
            chapter_number=chapter.chapter_number,
            verified_quarantine_slots=quarantine_proofs or None,
        )
        _require(derived is not None, "commercial_projection_mismatch")
    projection_compatibility = None
    if projection is not None:
        if _hash(rebuilt_projection) != _hash(projection):
            packet_witnesses = []
            for run_id in ids:
                run = await session.get(LlmRunModel, UUID(run_id))
                paid_source = (run.metadata_json or {}).get("commercial_source_input_receipt") or {}
                selection = paid_source.get("source_selection") or {}
                old_audit = selection.get("original_source_sha256")
                if not _valid_sha(old_audit):
                    continue
                current_selection = deepcopy(dict(commercial_sources.receipt.get("source_selection") or {}))
                original_selection = deepcopy(dict(selection))
                current_audit = current_selection.pop("original_source_sha256", None)
                original_selection.pop("original_source_sha256", None)
                if not _valid_sha(current_audit) or _hash(current_selection) != _hash(original_selection):
                    continue
                packet = json.loads(commercial_sources.text)
                packet["source_context"]["source_selection_receipt"]["original_source_sha256"] = old_audit
                original_text = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                packet_hash = hashlib.sha256(original_text.encode()).hexdigest()
                if (packet_hash != projection.get("source_text_sha256")
                        or packet_hash != paid_source.get("assembled_source_sha256")):
                    continue
                request = _retained_request(settings, run)
                retained_role = _fresh_role(settings, project, request, catalog)
                if (llm._effective_input_hash(request, retained_role) != run.prompt_hash
                        or request.user_prompt.count(original_text) != 1):
                    continue
                packet_witnesses.append({
                    "schema": "retained-commercial-packet-audit-proof.v1", "llm_run_id": str(run.id),
                    "operation_key": (run.metadata_json or {}).get("execution_receipt", {}).get("operation_key"),
                    "effective_input_hash": run.prompt_hash, "packet_sha256": packet_hash,
                    "current_packet_sha256": hashlib.sha256(commercial_sources.text.encode()).hexdigest(),
                    "changed_pointer": "/source_context/source_selection_receipt/original_source_sha256",
                    "old_opaque_audit_sha256": old_audit, "current_opaque_audit_sha256": current_audit,
                    "literal_occurrences": 1,
                })
            projection_compatibility = _projection_audit_compatibility(
                projection, rebuilt_projection, sources=commercial_sources,
                source_compatibility=source_compatibility, packet_witnesses=packet_witnesses,
            )
        _require(
            derived is not None
            and _hash(derived) == _hash(commercial)
            and (_hash(rebuilt_projection) == _hash(projection) or projection_compatibility is not None),
            "commercial_projection_mismatch",
        )
    return {
        "owner_workflow_run_id": str(owner.id),
        "llm_run_ids": ids,
        "effective_critic_sha256": binding["effective_critic_sha256"],
        "route_proof": "retained-journal-effective-critic.v1",
        **({"projection_audit_compatibility": projection_compatibility}
           if projection_compatibility is not None else {}),
        **(
            {
                "quarantined_slots": {
                    name: {
                        "proof_sha256": _hash(value["proof"]),
                        "original_report_sha256": value["original_report_sha256"],
                        "operation_key": value["proof"]["root"]["key"],
                        "abstains": True,
                    }
                    for name, value in quarantine_proofs.items()
                }
            }
            if quarantine_proofs
            else {}
        ),
    }


async def verify_retained_review_inputs(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter: ChapterModel,
    draft: ChapterDraftVersionModel,
    report: ReviewReportModel,
    cycle: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify retained requests against fresh routes and current full sources.

    Missing evidence raises ValueError. The caller owns source locks, idle-writer,
    adoption, code-equivalence and semantic fences and commits its own migration.
    """
    from bestseller.services.budget import current_execution_scope

    _require(not session.deleted, "unflushed_source_deletion")
    _require(
        project.id == chapter.project_id == draft.project_id == report.project_id
        and draft.chapter_id == chapter.id
        and report.target_id == chapter.id
        and report.target_type == "chapter"
        and cycle.get("state") == "completed"
        and cycle.get("source_draft_id") == str(draft.id)
        and (report.structured_output or {}).get("draft_id") == str(draft.id),
        "scope_mismatch",
    )
    body = hashlib.sha256(
        draft.content_md.replace("\r\n", "\n").replace("\r", "\n").encode()
    ).hexdigest()
    _require(cycle.get("source_body_hash") == body, "body_mismatch")
    ro = _ReadOnlySession(session)
    catalog, catalog_hash = _fresh_catalog()
    workflow_id = UUID(cycle["review_workflow_run_id"])
    workflow = await ro.get(WorkflowRunModel, workflow_id)
    _require(
        workflow is not None
        and workflow.project_id == project.id
        and workflow.scope_id == chapter.id
        and workflow.scope_type == "chapter",
        "workflow_mismatch",
    )
    all_runs = list(
        await ro.scalars(
            select(LlmRunModel)
            .where(
                LlmRunModel.project_id == project.id,
                LlmRunModel.workflow_run_id == workflow_id,
            )
            .execution_options(populate_existing=True)
        )
    )
    runs = [row for row in all_runs if row.prompt_template != "chapter_commercial_quality_judge"]
    _require(runs and report.llm_run_id is not None, "primary_run_missing")
    primary = next((row for row in runs if row.id == report.llm_run_id), None)
    _require(
        primary is not None and primary.prompt_template == "chapter_review", "primary_run_mismatch"
    )
    scope = ((primary.metadata_json or {}).get("execution_receipt") or {}).get("scope_id")
    _require(isinstance(scope, str) and scope, "original_scope_missing")
    expected_scope = current_execution_scope() or cycle.get("execution_scope_id")
    _require(not expected_scope or expected_scope == scope, "original_scope_mismatch")
    paid_rows, operations = [], []
    primary_request = None
    for run in runs:
        run_body = (
            await _primary6_body(
                ro,
                run,
                project_id=project.id,
                chapter_id=chapter.id,
            )
            if _primary6(run)
            else None
        )
        request = _retained_request(
            settings, run, chapter_body=run_body.content_md if run_body else None
        )
        if run.id == primary.id:
            primary_request = request
        role = _fresh_role(settings, project, request, catalog)
        effective_hash = llm._effective_input_hash(request, role)
        _, operation = await _physical_return(
            ro,
            run,
            scope=scope,
            expected_hash=effective_hash,
            expected_model=role.model,
        )
        operations.append(operation)
        reconstruction = deepcopy(request.metadata.get("retained_request_reconstruction"))
        if reconstruction is not None:
            reconstruction.update(
                effective_input_sha256=effective_hash,
                source_draft_id=str(run_body.id),
                hash_domain="effective-input.v1",
                effective_input_identity_proven=True,
                full_paid_return_equality_verified=True,
                grade_recomputed=False,
            )
        paid_rows.append(
            {
                "llm_run_id": str(run.id),
                "prompt_template": run.prompt_template,
                "effective_input_sha256": effective_hash,
                **(
                    {"request_reconstruction": reconstruction} if reconstruction is not None else {}
                ),
            }
        )
    _require(len(set(operations)) == len(operations), "duplicate_paid_operations")
    window = (report.structured_output.get("evidence_summary") or {}).get(
        "llm_window_quality_judge"
    )
    if window:
        _require(
            any(
                str(row.id) == str(window.get("llm_run_id"))
                and row.prompt_template == "chapter_window_quality_judge"
                for row in runs
            ),
            "window_run_missing",
        )
    packet = await build_chapter_writer_context(ro, settings, project.slug, chapter.chapter_number)
    scenes = list(
        await ro.scalars(
            select(SceneCardModel)
            .where(
                SceneCardModel.project_id == project.id,
                SceneCardModel.chapter_id == chapter.id,
            )
            .order_by(SceneCardModel.scene_number)
        )
    )
    source = await load_review_source_context(
        ro,
        project=project,
        chapter=chapter,
        scenes=scenes,
        context_packet=packet,
        content_md=draft.content_md,
        settings=settings,
    )
    source_hash = _hash(source)
    receipt = (primary.metadata_json or {}).get("review_source_receipt") or {}
    source_compatibility = None
    primary_source_proof = _primary_source_integrity(primary_request, receipt, source)
    if primary_source_proof is None:
        source_compatibility = _audit_source_compatibility(
            primary_request.user_prompt if primary_request is not None else "",
            receipt,
            source,
        )
        if source_compatibility is None:
            error = ValueError("retained_review_input_primary_source_changed")
            error.source_comparison = _source_comparison(
                primary_request.user_prompt if primary_request is not None else "",
                receipt,
                source,
            )
            raise error
    commercial_sources = None
    if (report.structured_output.get("evidence_summary") or {}).get("commercial_action_projection"):
        from bestseller.services.chapter_generation_input_builder import build_chapter_generation_input_bundle
        from bestseller.services.chapter_llm_quality_judge import _commercial_source_projection
        from bestseller.services.commercial_review_context import load_authorized_commercial_history
        from bestseller.services.creation_contract_authority import select_creation_contracts
        from bestseller.services.commercial_source_evidence import assemble_commercial_sources
        from bestseller.services.planner import _source_bound_cast_enabled
        # Replay the commercial caller's own history reader. Chapter one's
        # empty history differs from the primary source's typed empty context;
        # pending references also use their own qualified continuity channel.
        history = await load_authorized_commercial_history(
            ro, project_id=project.id, chapter_number=chapter.chapter_number,
        )
        previous = history.get("recent_reference_prose") or history.get("recent_adopted_prose") or []
        generation = build_chapter_generation_input_bundle(
            project=project, chapter=chapter, scenes=scenes, context_packet=packet,
            target_word_count=int(chapter.target_word_count or draft.word_count or 0),
        ).model_dump(mode="json")
        generation["approved_creation_contracts"] = select_creation_contracts(
            project.metadata_json or {}, source_bound=_source_bound_cast_enabled(project),
        )
        generation["draft_manuscript_continuity" if history.get("qualified_pending_sources")
                   else "adopted_continuity"] = {
                       key: value for key, value in history.items() if key != "recent_adopted_prose"}
        commercial_sources = assemble_commercial_sources(
            chapter_number=chapter.chapter_number, content_md=draft.content_md,
            source=_commercial_source_projection(generation, chapter_number=chapter.chapter_number,
                                                  content_md=draft.content_md, previous_chapters=previous),
            previous_chapters=previous,
        )
    commercial = await _commercial_proof(
        ro,
        settings,
        project=project,
        chapter=chapter,
        draft=draft,
        report=report,
        catalog=catalog,
        scope=scope,
        commercial_sources=commercial_sources,
        source_compatibility=source_compatibility,
    )
    return {
        "schema": "retained-review-input-proof.v1",
        "project_id": str(project.id),
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "review_report_id": str(report.id),
        "cycle_id": cycle["cycle_id"],
        "review_workflow_run_id": str(workflow_id),
        "source_sha256": source_hash,
        "retained_source_sha256": receipt.get("source_sha256"),
        "source_compatibility": source_compatibility,
        "primary_source_integrity": primary_source_proof,
        "catalog_sha256": catalog_hash,
        "paid_rows": sorted(paid_rows, key=lambda row: row["llm_run_id"]),
        "commercial": commercial,
        "provider_calls": 0,
        "writes": 0,
    }


@asynccontextmanager
async def historical_reference_route_scope(session, settings, *, project, chapter):
    """Scoped witness for original safety operations whose ORM run rolled back.

    Never inserts a run or result. The unchanged legacy verifier still checks
    the full source/body, original code, native return, HTTP and source actions.
    Only a settled exact operation/attempt/transport may supply its old route.
    """
    from bestseller.services import draft_continuity as dc
    pointer = (chapter.metadata_json or {}).get(dc.POINTER)
    if not pointer:
        yield
        return
    row = await session.get(WorkflowRunModel, UUID(str(pointer)), populate_existing=True)
    proof = (row.metadata_json or {}).get('proof', {}) if row is not None else {}
    if proof.get('schema') not in {'source-adjudicated-draft-continuity.v1',
                                   'source-adjudicated-axis-continuity.v1'}:
        yield
        return
    _require(row.project_id == project.id and row.scope_id == chapter.id
             and row.scope_type == 'chapter' and row.workflow_type == 'draft_continuity_reference'
             and row.status == 'completed', 'historical_operation_reference_scope')
    evidence = proof['native_evidence']
    request = llm.LLMCompletionRequest.model_validate(evidence['request'])
    _require(request.project_id == project.id and str(request.workflow_run_id) == evidence['workflow_id']
             and request.prompt_template == 'draft_continuity_hard_safety'
             and request.prompt_version == '3.0' and request.logical_role == 'critic', 'historical_operation_request_scope')
    scope = project.metadata_json['execution_scope_id']
    operation = await session.get(ExecutionOperationModel, evidence['operation_key'], populate_existing=True)
    _require(operation is not None and operation.scope_id == scope and operation.status == 'completed'
             and _valid_sha(operation.input_hash), 'historical_operation_not_settled')
    paid = llm.LLMCompletionResult.model_validate(operation.result_json)
    catalog, _ = _fresh_catalog()
    selected = catalog.get(model_catalog.selected_model_id(project.metadata_json))
    if request.model_catalog_key is not None or selected is None or selected.model == paid.model_name:
        # Existing route behaviour and every legacy check retain their force.
        yield
        return
    attempts = list(await session.scalars(select(ExecutionAttemptModel).where(
        ExecutionAttemptModel.scope_id == scope, ExecutionAttemptModel.operation_key == operation.key)))
    _require(len(attempts) == 1 and attempts[0].status == 'completed'
             and attempts[0].input_hash == operation.input_hash
             and attempts[0].model_name == paid.model_name, 'historical_operation_attempt_set')
    attempt = attempts[0]
    key = hashlib.sha256(f'transport-result.v1\n{scope}\n{attempt.id}'.encode()).hexdigest()
    transport = await session.get(ExecutionOperationModel, key, populate_existing=True)
    _require(transport is not None and transport.scope_id == scope and transport.status == 'transport_result',
             'historical_operation_transport_missing')
    wire = transport.result_json
    fields = ('content', 'provider', 'model_name', 'tool_calls', 'raw_message', 'finish_reason',
              'input_tokens', 'output_tokens', 'fallback_used', 'fallback_source')
    _require(wire.get('operation_key') == operation.key and wire.get('attempt_id') == attempt.id
             and wire.get('input_hash') == operation.input_hash
             and all(getattr(paid, field) == wire['result'].get(field) for field in fields)
             and not paid.fallback_used and paid.finish_reason == 'tool_calls'
             and paid.provider == llm._provider_from_model(paid.model_name)
             and paid.provider not in {'mock', 'test', 'fallback'}
             and attempt.actual_tokens == paid.input_tokens + paid.output_tokens,
             'historical_operation_full_return')
    origin = paid.metadata.get('paid_call_origin') or {}
    effective = paid.metadata.get('effective_input_receipt') or {}
    execution = paid.metadata.get('execution_receipt') or {}
    _require(origin.get('schema') == 'paid-call-origin.v1' and origin.get('status') == 'recorded'
             and origin.get('logical_role') == request.logical_role
             and origin.get('step_run_id') == (str(request.step_run_id) if request.step_run_id else None)
             and origin.get('project_id') == str(project.id)
             and origin.get('workflow_run_id') == str(request.workflow_run_id)
             and origin.get('prompt_template') == request.prompt_template
             and origin.get('prompt_version') == request.prompt_version
             and origin.get('prompt_hash') == operation.input_hash
             and effective.get('schema') == 'effective-input.v1'
             and effective.get('hash') == operation.input_hash and effective.get('model') == paid.model_name
             and execution.get('scope_id') == scope and execution.get('operation_key') == operation.key
             and [item['attempt_id'] for item in execution.get('attempts', []) if item.get('status') == 'completed'] == [attempt.id],
             'historical_operation_origin_or_effective_receipt')
    root = Path(settings.artifact_store.local_dir).expanduser().resolve()
    http = (root / evidence['http_body_ref']).resolve()
    _require(http.is_relative_to(root) and hashlib.sha256(http.read_bytes()).hexdigest() == evidence['http_body_sha256'],
             'historical_operation_http_witness')
    owner = _route_owner()
    previous = _HISTORICAL_ROUTE_WITNESSES.get()
    witnesses = dict(previous[1]) if previous and previous[0] is owner else {}
    request_key = _route_request_key(request)
    binding = {'project_id': str(project.id), 'model': paid.model_name,
               'input_hash': operation.input_hash, 'operation_key': operation.key,
               'source_kind': 'settled_operation_without_synthetic_run'}
    prior = witnesses.get(request_key)
    _require(prior is None or all(prior.get(k) == binding[k] for k in ('project_id', 'model', 'input_hash')),
             'historical_operation_route_conflict')
    witnesses[request_key] = prior or binding
    token = _HISTORICAL_ROUTE_WITNESSES.set((owner, witnesses))
    try:
        yield
    finally:
        _HISTORICAL_ROUTE_WITNESSES.reset(token)
