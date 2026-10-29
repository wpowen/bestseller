"""Explicit runtime builder -> original P3 SDK transport bridge.

No HTTP driver lives here. The coordinator installs the returned P3 Tape in
each API/worker process. Each response is bound after NativeTemplateObserver
observes the actual request, so quickstart needs no predetermined project UUID.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202, ANN204 -- P3 observer and JSON runtime boundary.
from copy import deepcopy
import importlib

from .checkpoint import PhysicalOrdinalStore
from .contracts import (
    DEFAULT_LIMITS,
    TAPE_SCHEMA,
    CallIdentity,
    ScriptContractError,
    TemplateCatalog,
    build_document,
    canonical,
    digest,
    need,
)
from .quickstart_builders import BuilderContext, build_response, require_builder


def create_runtime_tape(
    *,
    catalog,
    context_resolver,
    usage_resolver,
    allowed_endpoints,
    audit_path=None,
    limits=DEFAULT_LIMITS,
    checkpoint=None,
):
    """Return a P3 Tape subclass; never installs or starts anything.

    context_resolver(observation, wire_body) returns a private BuilderContext;
    usage_resolver(observation, wire_body) supplies explicit synthetic counters.
    Both execute only when the coordinator later runs an SDK physical send.
    No callback may authorize a retry or mutate native application state.

    P3's actual _prepare, _observe, _choose, HTTPX stream encoding and audit are
    retained. Only a finite explicit entry is appended before the existing
    matcher consumes it. No empty/unknown response or wildcard fallback exists.
    """
    need(
        isinstance(catalog, TemplateCatalog)
        and callable(context_resolver)
        and callable(usage_resolver),
        "SCRIPT_BUILDER_INPUT_INVALID",
    )
    try:
        module = importlib.import_module("e2e_sim.transport")
    except ImportError:
        raise ScriptContractError("SCRIPT_P3_TRANSPORT_UNAVAILABLE") from None
    need(
        getattr(module, "TAPE_SCHEMA", None) == TAPE_SCHEMA
        and callable(getattr(module, "NativeTemplateObserver", None))
        and callable(getattr(getattr(module, "Tape", None), "_choose", None)),
        "SCRIPT_P3_TRANSPORT_INTERFACE_DRIFT",
    )

    class RuntimeBuilderTape(module.Tape):
        def __init__(self):
            super().__init__(
                {
                    "schema": TAPE_SCHEMA,
                    "simulation_only": True,
                    "allowed_endpoints": deepcopy(allowed_endpoints),
                    "entries": [],
                },
                audit_path=audit_path,
                template_observer=module.NativeTemplateObserver(),
            )
            self.builder_send_count = 0
            self.builder_reply_bytes = 0
            self.builder_bound_identities = set()
            self._reply_ordinals = {}

        def _choose(self, body, fields, observation):
            with self.lock:
                ticket = None
                try:
                    need(
                        self.builder_send_count < limits.max_entries, "SCRIPT_ENTRY_COUNT_UNBOUNDED"
                    )
                    if checkpoint is not None:
                        ticket = checkpoint.claim_send(
                            template=observation.prompt_template,
                            native_operation_key=fields.get("native_operation_key"),
                            native_input_hash=fields.get("native_input_hash"),
                            native_attempt_id=fields.get("native_attempt_id"),
                            wire_sha256=module.wire_fingerprints(body)["body_sha256"],
                            project_id=observation.project_id,
                            workflow_run_id=observation.workflow_run_id,
                        )
                        # Fixture routing only. P3's native send counter, SDK
                        # attempt and financial context are never modified.
                        observation = module.NativeObservation(
                            observation.prompt_template,
                            ticket["global_ordinal"],
                            observation.project_id,
                            observation.workflow_run_id,
                        )
                    identity = CallIdentity.from_observation(
                        observation, body_sha256=module.wire_fingerprints(body)["body_sha256"]
                    )
                    require_builder(observation.prompt_template)
                    context = (
                        context_resolver(observation, body, ticket)
                        if checkpoint is not None
                        else context_resolver(observation, body)
                    )
                    need(
                        isinstance(context, BuilderContext)
                        and context.call_index == observation.call_index,
                        "SCRIPT_OBSERVATION_CONTEXT_MISMATCH",
                    )
                    spec = build_response(
                        observation.prompt_template,
                        context,
                        catalog=catalog,
                        wire_body=body,
                        usage=usage_resolver(observation, body),
                        limits=limits,
                    )
                    if ticket is not None:
                        checkpoint.record_reply(
                            ticket["global_ordinal"], response_sha256=digest(spec)
                        )
                    # build_response may resolve a finite numeric outline name.
                    # Its source-specific range is checked before catalog extension.
                    local = catalog
                    if observation.prompt_template not in catalog.names:
                        inventory = deepcopy(catalog.inventory)
                        inventory["templates"].append(
                            {"template": observation.prompt_template, "historical_only": False}
                        )
                        local = TemplateCatalog(inventory)
                    document = build_document(
                        local,
                        [(identity, [spec])],
                        allowed_endpoints=deepcopy(allowed_endpoints),
                        limits=limits,
                    )
                    entry = document["entries"][0]
                    key = digest(entry["match"])
                    need(key not in self.builder_bound_identities, "SCRIPT_SEND_IDENTITY_REUSED")
                    size = len(canonical(entry).encode())
                    need(
                        self.builder_reply_bytes + size <= limits.max_document_bytes,
                        "SCRIPT_DOCUMENT_UNBOUNDED",
                    )
                    self._entries.append(entry)
                    # P3 stores its document separately from the mutable entries.
                    self._document["entries"] = deepcopy(self._entries)
                    self.tape_sha256 = digest(self._document)
                    selected = super()._choose(body, fields, observation)
                    if ticket is not None:
                        self._reply_ordinals[id(selected)] = ticket["global_ordinal"]
                    self.builder_bound_identities.add(key)
                    self.builder_reply_bytes += size
                    self.builder_send_count += 1
                    self._event(
                        {
                            "event": "script_builder_prefix",
                            "prefix_revision": self.builder_send_count,
                            "prefix_sha256": self.tape_sha256,
                            "template": observation.prompt_template,
                            "call_index": observation.call_index,
                            "global_physical_ordinal": ticket["global_ordinal"] if ticket else None,
                        }
                    )
                    return selected
                except ScriptContractError as error:
                    if ticket is not None:
                        checkpoint.record_transport(
                            ticket["global_ordinal"], outcome="builder_blocked"
                        )
                    self._event(
                        {
                            "event": "harness_UNMAPPED",
                            "reason": error.code,
                            "prompt_template": getattr(observation, "prompt_template", None),
                            "call_index": getattr(observation, "call_index", None),
                        }
                    )
                    raise

        def _response(self, request, body, spec, *, asynchronous):
            ordinal = self._reply_ordinals.pop(id(spec), None)
            try:
                response = super()._response(request, body, spec, asynchronous=asynchronous)
            except BaseException:
                if ordinal is not None:
                    checkpoint.record_transport(ordinal, outcome="transport_fault")
                raise
            if ordinal is None:
                return response
            # Plain JSON is already materialized by HTTPX. Streaming returns
            # remain unresolved until the actual byte iterator is exhausted.
            if response.is_stream_consumed:
                checkpoint.record_transport(ordinal, outcome="wire_complete")
                return response
            import httpx

            original = response.stream
            completed = [False]

            def finish(outcome):
                if not completed[0]:
                    checkpoint.record_transport(ordinal, outcome=outcome)
                    completed[0] = True

            class SyncStream(httpx.SyncByteStream):
                def __iter__(self):
                    try:
                        yield from original
                    except BaseException:
                        finish("transport_fault")
                        raise
                    else:
                        finish("wire_complete")

                def close(self):
                    try:
                        original.close()
                    finally:
                        finish("transport_fault")

            class AsyncStream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    try:
                        async for chunk in original:
                            yield chunk
                    except BaseException:
                        finish("transport_fault")
                        raise
                    else:
                        finish("wire_complete")

                async def aclose(self):
                    try:
                        await original.aclose()
                    finally:
                        finish("transport_fault")

            response.stream = AsyncStream() if asynchronous else SyncStream()
            return response

    return RuntimeBuilderTape()


def create_checkpointed_runtime_tape(
    *,
    checkpoint,
    catalog,
    context_resolver,
    usage_resolver,
    allowed_endpoints,
    audit_path=None,
    limits=DEFAULT_LIMITS,
):
    """Normal SDK path: shared durable global ordinal, never P3 process ordinals.

    Call checkpoint.initialize() explicitly before booting web/worker processes.
    context_resolver(observation, wire_body, ticket) receives the durable ticket.
    record_transport is transport evidence only; native UNKNOWN is never cleared.
    """
    need(isinstance(checkpoint, PhysicalOrdinalStore), "SCRIPT_CHECKPOINT_REQUIRED")
    return create_runtime_tape(
        catalog=catalog,
        context_resolver=context_resolver,
        usage_resolver=usage_resolver,
        allowed_endpoints=allowed_endpoints,
        audit_path=audit_path,
        limits=limits,
        checkpoint=checkpoint,
    )
