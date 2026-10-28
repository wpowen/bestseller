"""Fail-closed, offline OpenAI Chat Completions transport for simulation.

API::

    tape = Tape(document, audit_path=path, template_observer=observer)
    # Or Tape.from_file(path, audit_path=..., template_observer=...)
    with install_scripted_http(tape):
        ...  # unchanged SDK, HTTPX hooks, native guard and ledger
    tape.summary()

The document uses TAPE_SCHEMA, simulation_only=True, exact allowed_endpoints
(scheme/host/port/path), and entries with unique id, match, responses. Match
selectors are wire_fingerprints() keys, native_operation_key, native_input_hash,
and the ANDed pair prompt_template/call_index (positive, 1-based integer).
Optional project_id/workflow_run_id UUID selectors and audit fields come from
actual native request observations, never from the tape or HTTP request body.
The audit also reads native_attempt_id from the current native attempt; it is
not a tape selector, ordinal, receipt, or assertion of authorization.
Responses are a physical-send sequence: mode auto/json/sse/timeout/disconnect;
content, tool_calls (OpenAI function shape), usage (prompt_tokens and
completion_tokens), finish_reason stop/length/tool_calls/content_filter,
chunk_characters and disconnect_after_chunks. No fallback or fixture loading.

TemplateObserver(request, native_call) returns NativeObservation or an awaitable
of it. It MUST read actual execution context or prompt DB; the transport never
infers a template from wire content, tape order, or len(call.attempts).
Baseline _ExecutionCall has neither field: a template match therefore requires
an observer. NativeTemplateObserver() supplies the baseline adapter by reading
native task frames and counting observed sends per (project_id, template).
For async DB reads, use an async observer and the async HTTPX path.
An observer receives the actual ambient call (or None for standalone tests),
so it can read claim.key/attempt_id/effective_input_hash to identify DB rows.
It must not mutate context, counters or ledger, or derive authority from tape.
The adapter checks native send count independently; the observer cannot supply
or override it. Missing native context is supported only for standalone tests.

Only the two HTTPX low transport methods are patched. Existing SDK, native
single-send guard/hooks, settings, request body, and settlement remain intact.
Timeout/disconnect raise HTTPX errors; native settlement must classify Unknown.
This module does not claim billing/ledger/quality/production evidence. Non-HTTPX
and custom transports are unsupported; the runner needs its own network fence.
Installation is process-global, exclusive, and must span all in-flight streams.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any
from uuid import UUID, uuid4

import httpx

TAPE_SCHEMA = "v3-scripted-provider-tape.v1"
MARKER = "v3-synthetic-http.v1"
_PATCH_LOCK = threading.RLock()
_ACTIVE: InstalledTransport | None = None
_HASH_KEYS = {
    "body_sha256",
    "messages_tools_sha256",
    "system_tools_sha256",
    "native_operation_key",
    "native_input_hash",
}
_UUID_KEYS = {"project_id", "workflow_run_id"}
_TEMPLATE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


@dataclass(frozen=True)
class NativeObservation:
    """Observed logical call identity; never a reservation/authority assertion."""

    prompt_template: str | None = None
    call_index: int | None = None
    project_id: str | UUID | None = None
    workflow_run_id: str | UUID | None = None


TemplateObserver = Callable[[httpx.Request, Any], NativeObservation | Awaitable[NativeObservation]]


class ScriptedTransportError(httpx.TransportError):
    """Safe harness failure, not a provider result or product defect."""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("nonfinite_json")


def _loads(raw):
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _hex(value):
    return type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None


def _positive(value):
    return type(value) is int and value > 0


def _uuid(value):
    if isinstance(value, UUID):
        return str(value)
    if type(value) is not str:
        raise ValueError("observed_uuid_invalid")
    try:
        result = str(UUID(value))
    except ValueError:
        raise ValueError("observed_uuid_invalid") from None
    if result != value:
        raise ValueError("observed_uuid_invalid")
    return result


def _template(value):
    return type(value) is str and _TEMPLATE.fullmatch(value) is not None


def wire_fingerprints(body):
    if (
        type(body) is not dict
        or type(body.get("messages")) is not list
        or not all(type(m) is dict and type(m.get("role")) is str for m in body["messages"])
        or (body.get("tools") is not None and type(body["tools"]) is not list)
        or type(body.get("model")) is not str
        or not body["model"]
        or ("stream" in body and type(body["stream"]) is not bool)
    ):
        raise ValueError("unsupported_openai_body")
    messages, tools = body["messages"], body.get("tools")
    return {
        "body_sha256": digest(body),
        "messages_tools_sha256": digest({"messages": messages, "tools": tools}),
        "system_tools_sha256": digest(
            {
                "messages": [m for m in messages if m["role"] in {"system", "developer"}],
                "tools": tools,
            }
        ),
    }


def _native_context():
    """Read only existing ContextVars; never import native services or set them."""
    module = sys.modules.get("bestseller.services.llm")
    if module is None:
        return None, {}
    call_var = getattr(module, "_execution_call_var", None)
    count_var = getattr(module, "_transport_send_count", None)
    call = call_var.get() if call_var is not None else None
    counter = count_var.get() if count_var is not None else None
    info = {}
    if call is not None or counter is not None:
        observed = counter[0] if type(counter) in (list, tuple) and counter else None
        if type(observed) is not int or observed != 1:
            raise ValueError("native_reserved_send_not_observed")
        info["observed_native_send_count"] = observed
    if call is not None:
        key = getattr(getattr(call, "claim", None), "key", None)
        if _hex(key):
            info["native_operation_key"] = key
        attempts = getattr(call, "attempts", ())
        if attempts and _hex(attempts[-1].get("effective_input_hash")):
            info["native_input_hash"] = attempts[-1]["effective_input_hash"]
        if attempts:
            attempt_id = attempts[-1].get("attempt_id")
            if type(attempt_id) is str and re.fullmatch(r"[0-9a-f]{32}", attempt_id):
                info["native_attempt_id"] = attempt_id
    return call, info


class NativeTemplateObserver:
    """Read native parent coroutine frames, including wait_for task boundaries.

    At the low handler the SDK task may no longer have the native request in
    its own await chain. Inspect all live tasks on this event loop, requiring
    frame globals to be the loaded native llm module, the frame's execution_call
    to be the exact ambient call object, and request to be LLMCompletionRequest.
    Accept only a unique (template, project UUID, workflow UUID) tuple. No DB,
    imports of native services, context writes, claims, or ledger flags.

    Ordinals count actual low-transport observations per (project_id, template),
    starting at 1 on this observer instance. This is routing only, never native
    authorization, durable logical-call identity, or a retry permission. Share
    one instance across a run; use a fresh instance for an explicit fresh run.
    Scheduling of concurrent sends for the same project/template sets order.
    """

    def __init__(self):
        self._counts = Counter()
        self._lock = threading.RLock()

    def __call__(self, _request, native_call):
        module = sys.modules.get("bestseller.services.llm")
        request_type = getattr(module, "LLMCompletionRequest", None)
        if native_call is None or not isinstance(request_type, type):
            raise ValueError("native_template_context_missing")
        try:
            tasks = asyncio.all_tasks(asyncio.get_running_loop())
        except RuntimeError:
            raise ValueError("native_template_requires_event_loop") from None
        identities = set()
        for task in tasks:
            pending = task.get_coro()
            seen = set()
            while pending is not None and id(pending) not in seen:
                seen.add(id(pending))
                frame = (
                    getattr(pending, "cr_frame", None)
                    or getattr(pending, "gi_frame", None)
                    or getattr(pending, "ag_frame", None)
                )
                if frame is not None and frame.f_globals is module.__dict__:
                    locals_ = frame.f_locals
                    actual = locals_.get("request")
                    if locals_.get("execution_call") is native_call and isinstance(
                        actual, request_type
                    ):
                        template = actual.prompt_template
                        if not _template(template):
                            raise ValueError("native_template_invalid")
                        project = (
                            _uuid(actual.project_id) if actual.project_id is not None else None
                        )
                        workflow = (
                            _uuid(actual.workflow_run_id)
                            if actual.workflow_run_id is not None
                            else None
                        )
                        identities.add((template, project, workflow))
                pending = (
                    getattr(pending, "cr_await", None)
                    or getattr(pending, "gi_yieldfrom", None)
                    or getattr(pending, "ag_await", None)
                )
        if len(identities) != 1:
            raise ValueError("native_template_not_unique")
        template, project, workflow = identities.pop()
        with self._lock:
            self._counts[(project, template)] += 1
            return NativeObservation(template, self._counts[(project, template)], project, workflow)


def _default_observation(call):
    # No fallback to claim, wire metadata, attempt position or tape cursor.
    return NativeObservation(
        getattr(call, "prompt_template", None), getattr(call, "call_index", None)
    )


def _observation_fields(value):
    if not isinstance(value, NativeObservation):
        raise ValueError("observer_shape_invalid")
    fields = {}
    if value.prompt_template is not None or value.call_index is not None:
        if not _template(value.prompt_template) or not _positive(value.call_index):
            raise ValueError("observer_shape_invalid")
        fields.update(prompt_template=value.prompt_template, call_index=value.call_index)
    for key in _UUID_KEYS:
        identifier = getattr(value, key)
        if identifier is not None:
            fields[key] = _uuid(identifier)
    return fields


def _message(spec):
    content = spec.get("content", "")
    if content is not None and type(content) is not str:
        raise ValueError("response_content_invalid")
    calls = spec.get("tool_calls", [])
    if type(calls) is not list:
        raise ValueError("response_tools_invalid")
    tools = []
    for item in calls:
        fn = item.get("function") if type(item) is dict else None
        if (
            type(fn) is not dict
            or type(fn.get("name")) is not str
            or not fn["name"]
            or type(fn.get("arguments")) is not str
            or item.get("type", "function") != "function"
        ):
            raise ValueError("response_function_invalid")
        tools.append(
            {
                "id": "v3-synthetic-tool-" + uuid4().hex,
                "type": "function",
                "function": {"name": fn["name"], "arguments": fn["arguments"]},
            }
        )
    usage = spec.get("usage")
    if type(usage) is not dict or set(usage) - {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }:
        raise ValueError("response_usage_invalid")
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(type(n) is not int or n < 0 for n in (prompt, completion)):
        raise ValueError("response_usage_invalid")
    if "total_tokens" in usage and (
        type(usage["total_tokens"]) is not int or usage["total_tokens"] != prompt + completion
    ):
        raise ValueError("response_usage_invalid")
    finish = spec.get("finish_reason", "tool_calls" if tools else "stop")
    if finish not in {"stop", "length", "tool_calls", "content_filter"}:
        raise ValueError("response_finish_invalid")
    message = {"role": "assistant", "content": content}
    if tools:
        message["tool_calls"] = tools
    return (
        message,
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
        finish,
    )


class _SyncStream(httpx.SyncByteStream):
    def __init__(self, chunks, request, disconnect=None):
        self.chunks, self.request, self.disconnect = chunks, request, disconnect

    def __iter__(self):
        for index, chunk in enumerate(self.chunks):
            if self.disconnect is not None and index >= self.disconnect:
                break
            yield chunk
        if self.disconnect is not None:
            raise httpx.ReadError("v3-synthetic-stream-disconnected", request=self.request)


class _AsyncStream(httpx.AsyncByteStream):
    def __init__(self, *args):
        self.sync = _SyncStream(*args)

    async def __aiter__(self):
        for chunk in self.sync:
            yield chunk


class Tape:
    def __init__(
        self, document, *, audit_path=None, template_observer: TemplateObserver | None = None
    ):
        if (
            type(document) is not dict
            or set(document) != {"schema", "simulation_only", "allowed_endpoints", "entries"}
            or document.get("schema") != TAPE_SCHEMA
            or document.get("simulation_only") is not True
        ):
            raise ValueError("synthetic_tape_schema")
        self._document = deepcopy(document)
        self._entries = self._document["entries"]
        self._endpoints = self._document["allowed_endpoints"]
        if (
            type(self._entries) is not list
            or type(self._endpoints) is not list
            or not self._endpoints
        ):
            raise ValueError("synthetic_tape_shape")
        if template_observer is not None and not callable(template_observer):
            raise ValueError("template_observer_invalid")
        self.template_observer = template_observer
        self.audit_path = Path(audit_path) if audit_path is not None else None
        self.lock = threading.RLock()
        self.events = []
        self.used = Counter()
        self.tape_sha256 = digest(self._document)
        for endpoint in self._endpoints:
            if (
                type(endpoint) is not dict
                or set(endpoint) != {"scheme", "host", "port", "path"}
                or endpoint["scheme"] not in {"http", "https"}
                or type(endpoint["host"]) is not str
                or not endpoint["host"]
                or any(c in endpoint["host"] for c in "*@/?#")
                or endpoint["host"] != endpoint["host"].lower()
                or not _positive(endpoint["port"])
                or endpoint["port"] > 65535
                or type(endpoint["path"]) is not str
                or not endpoint["path"].startswith("/")
                or any(c in endpoint["path"] for c in "*?#")
            ):
                raise ValueError("synthetic_endpoint_invalid")
        ids = set()
        for entry in self._entries:
            if (
                type(entry) is not dict
                or set(entry) != {"id", "match", "responses"}
                or type(entry["id"]) is not str
                or not entry["id"]
                or entry["id"] in ids
            ):
                raise ValueError("synthetic_entry_invalid")
            ids.add(entry["id"])
            match = entry["match"]
            if (
                type(match) is not dict
                or not match
                or set(match) - (_HASH_KEYS | _UUID_KEYS | {"prompt_template", "call_index"})
                or any(not _hex(v) for k, v in match.items() if k in _HASH_KEYS)
            ):
                raise ValueError("synthetic_match_invalid")
            for key in _UUID_KEYS & match.keys():
                _uuid(match[key])
            if "prompt_template" in match or "call_index" in match:
                if not _template(match.get("prompt_template")) or not _positive(
                    match.get("call_index")
                ):
                    raise ValueError("synthetic_template_match_invalid")
            if type(entry["responses"]) is not list or not entry["responses"]:
                raise ValueError("synthetic_responses_invalid")
            for spec in entry["responses"]:
                self._validate_spec(spec)

    @staticmethod
    def _validate_spec(spec):
        if (
            type(spec) is not dict
            or set(spec)
            - {
                "mode",
                "content",
                "tool_calls",
                "usage",
                "finish_reason",
                "chunk_characters",
                "disconnect_after_chunks",
            }
            or spec.get("mode", "auto") not in {"auto", "json", "sse", "timeout", "disconnect"}
            or not _positive(spec.get("chunk_characters", 32))
        ):
            raise ValueError("synthetic_response_invalid")
        count = spec.get("disconnect_after_chunks", 1)
        if type(count) is not int or count < 0:
            raise ValueError("synthetic_disconnect_invalid")
        if spec.get("mode") != "timeout":
            _message(spec)

    @property
    def document(self):
        return deepcopy(self._document)

    @classmethod
    def from_file(cls, path, *, audit_path=None, template_observer=None):
        return cls(
            _loads(Path(path).read_bytes()),
            audit_path=audit_path,
            template_observer=template_observer,
        )

    def _event(self, value):
        event = {
            "schema": MARKER,
            "simulation_only": True,
            "tape_sha256": self.tape_sha256,
            **value,
        }
        if self.audit_path is not None:
            try:
                self.audit_path.parent.mkdir(parents=True, exist_ok=True)
                with self.audit_path.open("a", encoding="utf-8") as stream:
                    stream.write(canonical(event) + "\n")
            except OSError:
                raise ScriptedTransportError("harness_UNMAPPED:audit_write_failed") from None
        self.events.append(event)

    def _fail(self, reason, fields=None):
        with self.lock:
            self._event({"event": "harness_UNMAPPED", "reason": reason, **(fields or {})})
        raise ScriptedTransportError("harness_UNMAPPED:" + reason) from None

    def _prepare(self, request, raw):
        url = request.url
        if (
            request.method != "POST"
            or url.query
            or url.fragment
            or url.username
            or url.password
            or not any(
                url.scheme == e["scheme"]
                and url.host == e["host"]
                and (url.port or (443 if url.scheme == "https" else 80)) == e["port"]
                and url.path == e["path"]
                for e in self._endpoints
            )
        ):
            self._fail("unsupported_endpoint")
        try:
            body = _loads(raw)
            fields = wire_fingerprints(body)
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            self._fail("unsupported_request_json", {"wire_bytes_sha256": _hash(raw)})
        try:
            call, native = _native_context()
        except ValueError:
            self._fail("native_reserved_send_not_observed", fields)
        except Exception:
            self._fail("native_context_invalid", fields)
        return body, {**fields, **native, "wire_bytes_sha256": _hash(raw)}, call

    def _observe(self, request, call):
        return (
            self.template_observer(request, call)
            if self.template_observer is not None
            else _default_observation(call)
        )

    def _choose(self, body, fields, observation):
        try:
            observed = _observation_fields(observation)
            if any(key in fields and fields[key] != value for key, value in observed.items()):
                raise ValueError("observer_identity_conflict")
            fields = {**fields, **observed}
        except ValueError:
            self._fail("observer_shape_invalid", fields)
        with self.lock:
            matches = [
                entry
                for entry in self._entries
                if all(fields.get(k) == v for k, v in entry["match"].items())
            ]
            if len(matches) != 1:
                self._fail("no_unique_entry", {**fields, "matching_entries": len(matches)})
            entry = matches[0]
            position = self.used[entry["id"]]
            if position >= len(entry["responses"]):
                self._fail("tape_exhausted", fields)
            spec = deepcopy(entry["responses"][position])
            mode = spec.get("mode", "auto")
            if (mode == "json" and body.get("stream") is True) or (
                mode in {"sse", "disconnect"} and body.get("stream") is not True
            ):
                self._fail("stream_contract_mismatch", fields)
            self._event(
                {
                    "event": "synthetic_http_send",
                    **fields,
                    "entry_id_sha256": digest(entry["id"]),
                    "response_position": position + 1,
                    "message_count": len(body["messages"]),
                    "tool_count": len(body.get("tools") or []),
                }
            )
            self.used[entry["id"]] += 1
            return spec

    def handle(self, request):
        body, fields, call = self._prepare(request, request.read())
        try:
            observation = self._observe(request, call)
        except Exception:
            self._fail("observer_failed", fields)
        if inspect.isawaitable(observation):
            if inspect.iscoroutine(observation):
                observation.close()
            self._fail("async_observer_requires_async_transport", fields)
        return self._response(
            request, body, self._choose(body, fields, observation), asynchronous=False
        )

    async def handle_async(self, request):
        body, fields, call = self._prepare(request, await request.aread())
        try:
            observation = self._observe(request, call)
            if inspect.isawaitable(observation):
                observation = await observation
        except Exception:
            self._fail("observer_failed", fields)
        return self._response(
            request, body, self._choose(body, fields, observation), asynchronous=True
        )

    def _response(self, request, body, spec, *, asynchronous):
        mode = spec.get("mode", "auto")
        if mode == "timeout":
            raise httpx.ReadTimeout("v3-synthetic-read-timeout", request=request)
        if mode == "auto":
            mode = "sse" if body.get("stream") is True else "json"
        message, usage, finish = _message(spec)
        base = {
            "id": "v3-synthetic-completion-" + uuid4().hex,
            "created": int(time.time()),
            "model": body["model"],
            "v3_synthetic": {"schema": MARKER, "simulation_only": True},
        }
        headers = {
            "x-v3-synthetic-transport": MARKER,
            "x-request-id": "v3-synthetic-http-" + uuid4().hex,
        }
        if mode == "json":
            headers["content-type"] = "application/json"
            chunks = [
                canonical(
                    {
                        **base,
                        "object": "chat.completion",
                        "usage": usage,
                        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    }
                ).encode()
            ]
        else:
            headers["content-type"] = "text/event-stream"

            def event(delta, reason=None, usage_item=None):
                value = {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": (
                        []
                        if usage_item is not None
                        else [{"index": 0, "delta": delta, "finish_reason": reason}]
                    ),
                }
                if usage_item is not None:
                    value["usage"] = usage_item
                return ("data: " + canonical(value) + "\n\n").encode()

            step = spec.get("chunk_characters", 32)

            def pieces(value):
                return (value[i : i + step] for i in range(0, len(value), step))

            chunks = [event({"role": "assistant", "content": ""})]
            chunks.extend(event({"content": part}) for part in pieces(message["content"] or ""))
            for index, tool in enumerate(message.get("tool_calls", [])):
                name_parts = list(pieces(tool["function"]["name"]))
                chunks.append(
                    event(
                        {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": tool["id"],
                                    "type": "function",
                                    "function": {"name": name_parts[0], "arguments": ""},
                                }
                            ]
                        }
                    )
                )
                chunks.extend(
                    event(
                        {
                            "tool_calls": [
                                {"index": index, "function": {"name": part, "arguments": ""}}
                            ]
                        }
                    )
                    for part in name_parts[1:]
                )
                chunks.extend(
                    event({"tool_calls": [{"index": index, "function": {"arguments": part}}]})
                    for part in pieces(tool["function"]["arguments"])
                )
            chunks.extend([event({}, finish), event({}, usage_item=usage)])
            if mode != "disconnect":
                chunks.append(b"data: [DONE]\n\n")
        disconnect = spec.get("disconnect_after_chunks", 1) if mode == "disconnect" else None
        stream = (_AsyncStream if asynchronous else _SyncStream)(chunks, request, disconnect)
        return httpx.Response(200, headers=headers, stream=stream, request=request)

    def summary(self):
        with self.lock:
            return {
                "schema": MARKER,
                "simulation_only": True,
                "tape_sha256": self.tape_sha256,
                "entries": len(self._entries),
                "consumed": sum(self.used.values()),
                "events": len(self.events),
                "event_counts": dict(Counter(e["event"] for e in self.events)),
            }


@dataclass
class InstalledTransport:
    tape: Tape
    original_async: Any
    original_sync: Any
    patched_async: Any
    patched_sync: Any
    closed: bool = False

    def uninstall(self):
        global _ACTIVE
        with _PATCH_LOCK:
            if self.closed:
                return
            if (
                httpx.AsyncHTTPTransport.handle_async_request is not self.patched_async
                or httpx.HTTPTransport.handle_request is not self.patched_sync
            ):
                raise RuntimeError("scripted_transport_patch_ownership_changed")
            httpx.AsyncHTTPTransport.handle_async_request = self.original_async
            httpx.HTTPTransport.handle_request = self.original_sync
            self.closed = True
            _ACTIVE = None

    __call__ = uninstall

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.uninstall()


def install_scripted_http(tape):
    """Install exclusive sync/async low-HTTPX handlers; never call real transport."""
    global _ACTIVE
    if not isinstance(tape, Tape):
        raise TypeError("scripted_transport_requires_tape")
    with _PATCH_LOCK:
        if _ACTIVE is not None:
            raise RuntimeError("scripted_transport_already_installed")
        original_async = httpx.AsyncHTTPTransport.handle_async_request
        original_sync = httpx.HTTPTransport.handle_request

        async def async_handle(_transport, request):
            return await tape.handle_async(request)

        def sync_handle(_transport, request):
            return tape.handle(request)

        httpx.AsyncHTTPTransport.handle_async_request = async_handle
        httpx.HTTPTransport.handle_request = sync_handle
        _ACTIVE = InstalledTransport(tape, original_async, original_sync, async_handle, sync_handle)
        return _ACTIVE
