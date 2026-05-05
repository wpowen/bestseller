from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
import re
import time
from time import perf_counter
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import LlmRunModel
from bestseller.settings import AppSettings, LLMRoleSettings, RetrySettings, get_runtime_env_value


logger = logging.getLogger(__name__)

# Lazy-cached litellm module reference.  litellm is an optional dependency
# so we cannot ``import litellm`` at the top level.  Previous code called
# ``importlib.import_module("litellm")`` on every LLM request (16-20+ per
# chapter), paying dictionary-lookup overhead each time.  We cache the
# result here after the first successful import.
_litellm_module: Any = None


def _get_litellm() -> Any:
    """Return the cached litellm module, importing it on first call.

    On first import we also disable LiteLLM's internal async logging
    infrastructure.  We record every LLM call in our own ``llm_runs``
    table so we don't need LiteLLM callbacks.  Leaving them enabled
    causes a background ``LoggingWorker`` task (queue size 50 000) to
    accumulate references to full response objects inside each
    ``asyncio.run()`` call, and those tasks are "destroyed while
    pending" when the event loop closes — leaking memory across every
    chapter generation.
    """
    global _litellm_module
    if _litellm_module is None:
        _litellm_module = importlib.import_module("litellm")
        _disable_litellm_logging(_litellm_module)
    return _litellm_module


def _disable_litellm_logging(litellm: Any) -> None:
    """Turn off all LiteLLM internal success/failure callbacks and verbose logging.

    LiteLLM's ``LoggingWorker`` is only active when callbacks are registered
    or verbose mode is on.  By clearing every callback list and disabling
    verbose output we prevent the worker from enqueuing logging tasks that
    hold large response-object references across event-loop boundaries.
    """
    try:
        # Clear all callback lists — we do our own logging via llm_runs table.
        for attr in (
            "callbacks",
            "success_callback",
            "failure_callback",
            "_async_success_callback",
            "_async_failure_callback",
            "input_callback",
            "service_callback",
        ):
            if isinstance(getattr(litellm, attr, None), list):
                setattr(litellm, attr, [])

        # Disable verbose / debug output that feeds the logging worker queue.
        litellm.set_verbose = False
        litellm.verbose = False

        # Suppress request/response body logging (saves significant memory for
        # large prompts/completions stored inside the LoggingWorker queue).
        litellm.turn_off_message_logging = True

        logger.debug("LiteLLM internal logging disabled (using our own llm_runs table)")
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: worst case LiteLLM logs more than necessary.
        logger.warning("Could not fully disable LiteLLM logging: %s", exc)


LLMRole = Literal["planner", "writer", "critic", "summarizer", "editor"]


# ── Circuit Breaker ─────────────────────────────────────────────────────
#
# Prevents cascading fallback-text contamination when the LLM provider is
# down.  After ``failure_threshold`` consecutive failures, the breaker
# opens and all calls fail fast for ``recovery_timeout`` seconds.  Then a
# single probe call is allowed; if it succeeds the breaker closes.

class _CircuitBreaker:
    """Simple async-safe circuit breaker for LLM calls."""

    __slots__ = (
        "_failure_threshold",
        "_recovery_timeout",
        "_consecutive_failures",
        "_last_failure_time",
        "_state",
    )

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._consecutive_failures = 0
        self._last_failure_time = 0.0
        self._state: Literal["closed", "open", "half_open"] = "closed"

    @property
    def state(self) -> str:
        return self._state

    def reset(self) -> None:
        """Reset breaker to initial closed state (useful for testing)."""
        self._consecutive_failures = 0
        self._last_failure_time = 0.0
        self._state = "closed"

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()
        if self._consecutive_failures >= self._failure_threshold:
            self._state = "open"
            logger.warning(
                "LLM circuit breaker OPEN after %d consecutive failures (recovery in %ds)",
                self._consecutive_failures,
                self._recovery_timeout,
            )

    def allow_request(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._state = "half_open"
                logger.info("LLM circuit breaker HALF_OPEN — allowing probe request")
                return True
            return False
        # half_open: allow exactly one probe
        return True


_llm_breaker = _CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)

# Primary-model rate-limit cooldowns.  When a configured primary (MiniMax in
# production) returns 429/quota-exhausted, we send traffic to the configured
# fallback model for a short window.  When the window expires, the next call
# probes the primary again; a successful probe automatically switches traffic
# back without changing configuration.
_rate_limit_fallback_until: dict[str, float] = {}


# ── Rate-limit detection ────────────────────────────────────────────────
#
# 429 Too Many Requests is a transient signal from the provider — it means
# "back off and try again", not "your request is broken".  Unlike generic
# failures, we should be willing to wait much longer for these and must not
# silently swap in fallback content (which would silently degrade quality).


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Detect whether an exception represents a rate-limit / 429 response.

    Handles three forms:
      * ``litellm.exceptions.RateLimitError`` (the documented class).
      * Any exception whose class name ends with ``RateLimitError``
        (defensive: litellm re-exports / provider-specific subclasses).
      * Generic exceptions carrying a ``status_code`` attribute == 429.
    """
    name = type(exc).__name__
    if name.endswith("RateLimitError"):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if isinstance(status, int) and status == 429:
        return True
    message = str(exc).lower()
    if "429" in message and ("rate" in message or "too many requests" in message):
        return True
    quota_markers = (
        "quota exceeded",
        "quota exhausted",
        "insufficient quota",
        "insufficient_quota",
        "usage limit",
        "resource exhausted",
        "too many requests",
    )
    if any(marker in message for marker in quota_markers):
        return True
    return False


def _extract_retry_after_seconds(exc: BaseException) -> float | None:
    """Extract a ``Retry-After`` hint from a provider exception, if present.

    litellm exposes upstream response headers via ``.response.headers``
    on some error classes.  We look for a ``Retry-After`` header and
    interpret it as seconds (HTTP also allows HTTP-date, which we skip).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:  # noqa: BLE001
        return None
    if value is None:
        return None
    try:
        seconds = float(value)
        if seconds < 0:
            return None
        return seconds
    except (TypeError, ValueError):
        return None


# --- Opt-C: shared litellm HTTP client ----------------------------------------
#
# By default, litellm creates a fresh ``httpx.AsyncClient`` for every
# ``acompletion`` call when no shared client is configured. For OpenAI-compatible
# providers (like MiniMax via ``openai/MiniMax-M2.7-*``), this means a TLS
# handshake per call — measurably 0.5–1s of latency overhead per request, which
# adds up across the 16–20+ LLM calls per chapter.
#
# litellm exposes a documented hook: setting ``litellm.aclient_session`` to a
# long-lived ``httpx.AsyncClient`` makes the OpenAI handler reuse it
# (see ``litellm/llms/openai/common_utils.py::_get_async_http_client``).
#
# We initialize a single process-wide client lazily on first LLM call so:
#   * Test paths (``settings.llm.mock = True``) never construct it.
#   * Worker / API processes share connection pooling across all LLM calls.
#   * Errors initializing the shared client fall back silently to litellm's
#     per-call default (no behavioral regression).
# Per-event-loop litellm client cache. The web server runs each autowrite
# task in its own thread with ``asyncio.run()`` which creates a fresh event
# loop.  A single ``httpx.AsyncClient`` cannot be shared across loops — its
# internal connection pool is bound to the loop it was created on.  Re-using
# a stale client leads to "Future attached to a different loop" errors and
# cross-task response mixing.
#
# We key the cache by loop id so each ``asyncio.run()`` invocation gets its
# own pooled client, while calls within the same loop share one.
import threading as _threading

_litellm_client_by_loop: dict[int, Any] = {}
_litellm_client_lock = _threading.Lock()


def _ensure_shared_litellm_http_client() -> None:
    """Install a per-loop ``httpx.AsyncClient`` into litellm.

    Creates a fresh client for each event loop (thread-safe) and caches it
    for the loop's lifetime.  The previous process-wide singleton caused
    cross-loop contamination when two autowrite tasks ran concurrently.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop — nothing to install

    loop_id = id(loop)
    with _litellm_client_lock:
        if loop_id in _litellm_client_by_loop:
            return

    try:
        import httpx

        litellm = _get_litellm()
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=10.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=300.0,
            ),
            follow_redirects=True,
        )
        litellm.aclient_session = client
        with _litellm_client_lock:
            _litellm_client_by_loop[loop_id] = client

        # Register a proper shutdown callback to close the client and remove
        # it from the cache when the event loop finishes.  This prevents the
        # memory leak where orphaned httpx clients (with their connection
        # pools and TLS state) accumulated after each asyncio.run().
        def _cleanup_client(client: Any = client, loop_id: int = loop_id) -> None:
            with _litellm_client_lock:
                _litellm_client_by_loop.pop(loop_id, None)
            try:
                # httpx.AsyncClient.aclose() is a coroutine; since the loop
                # is shutting down we close synchronously via the transport.
                client._transport.close()
            except Exception:
                pass

        # Use weakref.finalize so the callback fires when the loop is
        # garbage-collected (which happens at the end of asyncio.run()).
        import weakref
        weakref.finalize(loop, _cleanup_client)

        logger.info(
            "Installed per-loop httpx.AsyncClient into litellm (loop=%d, keepalive=10)",
            loop_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to install litellm http client for loop %d: %s",
            loop_id,
            exc,
        )


def _cleanup_stale_litellm_clients() -> None:
    """Remove entries from ``_litellm_client_by_loop`` whose httpx client is
    no longer usable (transport closed or event loop gone).

    Called periodically by the web server's watchdog to prevent unbounded
    growth in long-running processes.
    """
    with _litellm_client_lock:
        stale_ids: list[int] = []
        for lid, client in _litellm_client_by_loop.items():
            try:
                # A closed client's transport is_closed; if so it's stale.
                if getattr(client, "is_closed", False):
                    stale_ids.append(lid)
            except Exception:
                stale_ids.append(lid)
        for lid in stale_ids:
            client = _litellm_client_by_loop.pop(lid, None)
            if client is not None:
                try:
                    client._transport.close()
                except Exception:
                    pass
        if stale_ids:
            logger.info("Cleaned up %d stale litellm httpx client(s)", len(stale_ids))


class LLMCompletionRequest(BaseModel):
    logical_role: LLMRole
    model_tier: Literal["standard", "strong"] = "standard"
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    fallback_response: str = Field(min_length=1)
    prompt_template: str | None = Field(default=None, max_length=128)
    prompt_version: str | None = Field(default=None, max_length=32)
    project_id: UUID | None = None
    workflow_run_id: UUID | None = None
    step_run_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── Tool-use / function-calling extensions (Batch 1 Stage 0) ──────────
    # ``tools`` is the OpenAI-style function schema list passed straight
    # through to the provider.  ``tool_choice`` is "auto" | "none" | a
    # specific ``{"type":"function","function":{"name":...}}`` dict.
    # Both are forwarded verbatim to litellm.acompletion.
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None

    # When running a multi-round tool loop, the caller needs to replay the
    # prior assistant ``tool_calls`` + ``tool`` response messages on each
    # turn.  If ``messages_override`` is provided, it REPLACES the default
    # ``[system, user]`` wrapper — callers are responsible for including
    # the system + initial user messages themselves.  This is intentional:
    # it makes the override explicit rather than silently concatenating.
    messages_override: list[dict[str, Any]] | None = None


class LLMCompletionResult(BaseModel):
    content: str
    provider: str
    model_name: str
    llm_run_id: UUID | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    finish_reason: str | None = None

    # ── Tool-use extensions ────────────────────────────────────────────────
    # ``tool_calls`` is a list of structured tool-call records parsed from
    # the provider's response.  ``None`` means the model returned plain
    # text; an empty list means the model was offered tools but declined.
    tool_calls: list[dict[str, Any]] | None = None
    # ``raw_message`` is the full assistant message dict (content +
    # tool_calls if any) suitable for appending to ``messages_override``
    # on the next round of a tool loop.
    raw_message: dict[str, Any] | None = None


def _hash_prompt(system_prompt: str, user_prompt: str) -> str:
    payload = f"{system_prompt}\n\n{user_prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _estimate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, len(stripped) // 2)


def _get_role_settings(settings: AppSettings, logical_role: LLMRole) -> LLMRoleSettings:
    return cast(LLMRoleSettings, getattr(settings.llm, logical_role))


def _rate_limit_fallback_key(logical_role: LLMRole, role_settings: LLMRoleSettings) -> str:
    return "|".join(
        [
            logical_role,
            role_settings.model,
            role_settings.api_base or "",
            role_settings.api_key_env or "",
        ]
    )


def _build_rate_limit_fallback_settings(
    role_settings: LLMRoleSettings,
) -> LLMRoleSettings | None:
    if not role_settings.rate_limit_fallback_model:
        return None
    fallback_key_env = role_settings.rate_limit_fallback_api_key_env
    if fallback_key_env and not get_runtime_env_value(fallback_key_env):
        return None
    return role_settings.model_copy(
        update={
            "model": role_settings.rate_limit_fallback_model,
            "api_base": role_settings.rate_limit_fallback_api_base,
            "api_key_env": fallback_key_env,
            "stream": role_settings.rate_limit_fallback_stream,
            "model_override": None,
        }
    )


def _is_rate_limit_fallback_active(key: str) -> bool:
    until = _rate_limit_fallback_until.get(key)
    if until is None:
        return False
    if time.monotonic() >= until:
        _rate_limit_fallback_until.pop(key, None)
        return False
    return True


def _mark_rate_limit_fallback_active(key: str, cooldown_seconds: int) -> None:
    _rate_limit_fallback_until[key] = time.monotonic() + max(0, cooldown_seconds)


def _clear_rate_limit_fallback(key: str) -> None:
    _rate_limit_fallback_until.pop(key, None)


def _primary_retry_settings_for_rate_limit_fallback(
    retry_settings: RetrySettings,
) -> RetrySettings:
    # If a fallback is configured, a provider 429 should fail over immediately
    # instead of waiting through the normal patient 429 retry budget.
    return retry_settings.model_copy(update={"rate_limit_max_attempts": 1})


def _provider_from_model(model_name: str) -> str:
    if "/" not in model_name:
        return "unknown"
    return model_name.split("/", maxsplit=1)[0]


_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_thinking_tokens(text: str) -> str:
    """Remove ``<think>…</think>`` blocks emitted by reasoning models (e.g. MiniMax-M2.7).

    These blocks contain the model's internal chain-of-thought and must not
    leak into planning artifacts or novel prose.
    """
    return _THINK_TAG_RE.sub("", text).strip()


def _extract_text_content(raw_content: Any) -> str:
    if isinstance(raw_content, str):
        return _strip_thinking_tokens(raw_content)
    if isinstance(raw_content, list):
        parts: list[str] = []
        for item in raw_content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict) and item.get("type") == "text":
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
        return _strip_thinking_tokens("\n".join(part for part in parts if part))
    return ""


def _lookup_field(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _extract_usage_fields(usage: Any) -> tuple[int | None, int | None]:
    if usage is None:
        return None, None
    input_tokens = _lookup_field(usage, "prompt_tokens")
    output_tokens = _lookup_field(usage, "completion_tokens")
    if input_tokens is None:
        input_tokens = _lookup_field(usage, "input_tokens")
    if output_tokens is None:
        output_tokens = _lookup_field(usage, "output_tokens")
    return (
        int(input_tokens) if isinstance(input_tokens, int) else None,
        int(output_tokens) if isinstance(output_tokens, int) else None,
    )


async def _collect_streaming_content(
    response: Any,
) -> tuple[str, int | None, int | None, str | None]:
    parts: list[str] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None

    async for chunk in response:
        choices = _lookup_field(chunk, "choices") or []
        if choices:
            choice = choices[0]
            delta = _lookup_field(choice, "delta")
            if delta is None:
                delta = _lookup_field(choice, "message")
            raw_content = _lookup_field(delta, "content") if delta is not None else None
            text = _extract_text_content(raw_content)
            if text:
                parts.append(text)
            chunk_finish_reason = _lookup_field(choice, "finish_reason")
            if isinstance(chunk_finish_reason, str) and chunk_finish_reason:
                finish_reason = chunk_finish_reason

        chunk_input_tokens, chunk_output_tokens = _extract_usage_fields(_lookup_field(chunk, "usage"))
        if chunk_input_tokens is not None:
            input_tokens = chunk_input_tokens
        if chunk_output_tokens is not None:
            output_tokens = chunk_output_tokens

    content = "".join(parts).strip()
    if not content:
        raise ValueError("LLM streaming response content is empty.")
    return content, input_tokens, output_tokens, finish_reason


def _extract_tool_calls(message: Any) -> list[dict[str, Any]] | None:
    """Normalise an LLM assistant message's ``tool_calls`` into plain dicts.

    Providers return tool_calls in different shapes (pydantic models, dicts,
    None).  We produce a uniform list[dict] of the form::

        [{"id": "...", "type": "function",
          "function": {"name": "...", "arguments": "{...json-string...}"}}]

    or ``None`` if the model returned plain text with no tool calls.
    """
    if message is None:
        return None
    raw = _lookup_field(message, "tool_calls")
    if not raw:
        return None
    if not isinstance(raw, list):
        return None
    normalised: list[dict[str, Any]] = []
    for call in raw:
        call_id = _lookup_field(call, "id")
        call_type = _lookup_field(call, "type") or "function"
        fn = _lookup_field(call, "function")
        fn_name = _lookup_field(fn, "name") if fn is not None else None
        fn_args = _lookup_field(fn, "arguments") if fn is not None else None
        if not isinstance(fn_name, str) or not fn_name:
            continue
        if fn_args is None:
            fn_args = ""
        elif not isinstance(fn_args, str):
            # Some providers occasionally return pre-parsed dicts; normalise
            # to JSON string so downstream consumers have a single contract.
            import json as _json  # local import to avoid top-level noise
            try:
                fn_args = _json.dumps(fn_args, ensure_ascii=False)
            except Exception:
                fn_args = str(fn_args)
        normalised.append(
            {
                "id": call_id if isinstance(call_id, str) else "",
                "type": call_type if isinstance(call_type, str) else "function",
                "function": {"name": fn_name, "arguments": fn_args},
            }
        )
    return normalised or None


def _build_raw_assistant_message(
    content: str,
    tool_calls: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Construct an OpenAI-shaped assistant message for tool-loop replay."""
    msg: dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


async def _call_litellm(
    request: LLMCompletionRequest,
    role_settings: LLMRoleSettings,
) -> tuple[str, int | None, int | None, str | None, list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Invoke litellm.acompletion and return content + tokens + tool_calls.

    Returns a 6-tuple: ``(content, input_tokens, output_tokens,
    finish_reason, tool_calls, raw_assistant_message)``.  The last two are
    ``None`` when the caller did not request tools, preserving prior
    semantics for existing callers.
    """
    # Opt-C: install a shared httpx.AsyncClient into litellm on first use, so
    # subsequent calls reuse keep-alive connections to the model provider and
    # avoid per-request TLS handshakes.
    _ensure_shared_litellm_http_client()
    litellm = _get_litellm()
    acompletion = getattr(litellm, "acompletion", None)
    if acompletion is None:
        raise RuntimeError("litellm.acompletion is not available.")

    # ── Assemble messages ─────────────────────────────────────────────────
    if request.messages_override is not None:
        # Caller provides the complete message array (including system +
        # assistant + tool turns for a multi-round tool loop).  We trust
        # it and pass through verbatim.
        messages = list(request.messages_override)
    else:
        messages = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]

    completion_kwargs: dict[str, Any] = {
        "model": role_settings.model,
        "messages": messages,
        "temperature": role_settings.temperature,
        "max_tokens": role_settings.max_tokens,
        "timeout": role_settings.timeout_seconds,
        "stream": role_settings.stream,
    }

    # ── Tool-use wiring (Batch 1 Stage 0) ─────────────────────────────────
    # Pass tools/tool_choice straight through to litellm.  When tools are
    # present we force stream=False: streaming tool_call deltas would
    # require a very different accumulator than ``_collect_streaming_content``
    # currently does, and tool-loop callers do not need token streaming.
    if request.tools:
        completion_kwargs["tools"] = request.tools
        if request.tool_choice is not None:
            completion_kwargs["tool_choice"] = request.tool_choice
        completion_kwargs["stream"] = False

    # Only pass n when >1 — many providers (MiniMax, Gemini) ignore or
    # reject the parameter, and n=1 is the default anyway.
    if role_settings.n_candidates > 1 and not request.tools:
        # n>1 + tools is rarely meaningful and more likely to confuse
        # providers; keep n=1 whenever tools are involved.
        completion_kwargs["n"] = role_settings.n_candidates
    if role_settings.api_base:
        completion_kwargs["api_base"] = role_settings.api_base
    if role_settings.api_key_env:
        api_key = get_runtime_env_value(role_settings.api_key_env)
        if api_key:
            completion_kwargs["api_key"] = api_key

    # Enforce a hard wall-clock deadline via asyncio.wait_for.  litellm
    # passes ``timeout`` to httpx, but when a shared ``aclient_session`` is
    # installed, httpx may ignore per-request timeouts and use the client
    # default instead — allowing calls to hang far beyond the configured
    # role timeout.  The asyncio deadline guarantees cancellation.
    hard_timeout = float(role_settings.timeout_seconds) + 5.0  # small grace
    response = await asyncio.wait_for(
        acompletion(**completion_kwargs),
        timeout=hard_timeout,
    )

    if completion_kwargs["stream"]:
        content, in_tok, out_tok, finish = await asyncio.wait_for(
            _collect_streaming_content(response),
            timeout=hard_timeout,
        )
        return content, in_tok, out_tok, finish, None, None

    # When multiple candidates are returned, pick the longest (most
    # detailed) response instead of blindly using choices[0].
    choices = response.choices or []
    if not choices:
        raise ValueError("LLM response contains no choices.")
    if len(choices) == 1:
        choice = choices[0]
    else:
        choice = max(
            choices,
            key=lambda c: len(_extract_text_content(c.message.content)),
        )
    message = getattr(choice, "message", None)
    content = _extract_text_content(_lookup_field(message, "content"))
    tool_calls = _extract_tool_calls(message)
    input_tokens, output_tokens = _extract_usage_fields(getattr(response, "usage", None))
    finish_reason = getattr(choice, "finish_reason", None)

    # With tools, an empty content + non-empty tool_calls is the normal
    # "model wants to call a tool" state — do NOT raise on empty content.
    if not content.strip() and not tool_calls:
        raise ValueError(
            f"LLM response content is empty (finish_reason={finish_reason!r}, "
            f"output_tokens={output_tokens!r})."
        )
    raw_message = _build_raw_assistant_message(content.strip(), tool_calls)
    return content.strip(), input_tokens, output_tokens, finish_reason, tool_calls, raw_message


async def _call_litellm_with_retry(
    request: LLMCompletionRequest,
    role_settings: LLMRoleSettings,
    retry_settings: RetrySettings,
) -> tuple[str, int | None, int | None, str | None, list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Invoke ``_call_litellm`` with exponential back-off retry.

    Separate budgets for generic failures and rate-limit (HTTP 429)
    responses.  429 is transient — we retry it much more patiently,
    honour ``Retry-After`` when present, and deliberately do NOT count
    it against the circuit breaker (otherwise a burst of 429s would
    open the breaker for 60s on top of the provider's throttle).
    """
    max_attempts = max(1, retry_settings.max_attempts)
    wait_min = retry_settings.wait_min_seconds
    wait_max = retry_settings.wait_max_seconds

    rl_max_attempts = max(1, retry_settings.rate_limit_max_attempts)
    rl_wait_min = retry_settings.rate_limit_wait_min_seconds
    rl_wait_max = retry_settings.rate_limit_wait_max_seconds

    generic_attempt = 0
    rate_limit_attempt = 0

    while True:
        try:
            result = await _call_litellm(request, role_settings)
            _llm_breaker.record_success()
            return result
        except Exception as exc:
            if _is_rate_limit_error(exc):
                rate_limit_attempt += 1
                if rate_limit_attempt >= rl_max_attempts:
                    logger.error(
                        "LLM rate-limit persisted across %d attempts (%s: %s) — giving up",
                        rl_max_attempts,
                        type(exc).__name__,
                        exc,
                    )
                    raise
                retry_after = _extract_retry_after_seconds(exc)
                if retry_after is not None:
                    backoff = min(rl_wait_max, max(rl_wait_min, retry_after))
                else:
                    backoff = min(
                        rl_wait_max,
                        rl_wait_min * (2 ** (rate_limit_attempt - 1)),
                    )
                logger.warning(
                    "LLM rate-limited (429) attempt %d/%d (%s: %s) — waiting %.1fs%s",
                    rate_limit_attempt,
                    rl_max_attempts,
                    type(exc).__name__,
                    exc,
                    backoff,
                    " [Retry-After]" if retry_after is not None else "",
                )
                await asyncio.sleep(backoff)
                continue

            generic_attempt += 1
            _llm_breaker.record_failure()
            if generic_attempt >= max_attempts:
                logger.error(
                    "LLM call failed after %d attempts (%s: %s) — falling back",
                    max_attempts,
                    type(exc).__name__,
                    exc,
                )
                raise
            backoff = min(wait_max, wait_min * (2 ** (generic_attempt - 1)))
            logger.warning(
                "LLM call attempt %d/%d failed (%s: %s) — retrying in %.1fs",
                generic_attempt,
                max_attempts,
                type(exc).__name__,
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)


async def complete_text(
    session: AsyncSession,
    settings: AppSettings,
    request: LLMCompletionRequest,
) -> LLMCompletionResult:
    role_settings = _get_role_settings(settings, request.logical_role)
    if request.model_tier == "strong" and role_settings.model_override:
        role_settings = role_settings.model_copy(
            update={"model": role_settings.model_override}
        )
    rate_limit_fallback_settings = (
        _build_rate_limit_fallback_settings(role_settings)
        if settings.llm.retry.rate_limit_fallback_enabled
        else None
    )
    rate_limit_fallback_key = _rate_limit_fallback_key(
        request.logical_role,
        role_settings,
    )
    prompt_hash = _hash_prompt(request.system_prompt, request.user_prompt)
    metadata = dict(request.metadata)
    latency_ms: int | None = None
    provider = "mock"
    model_name = f"mock-{request.logical_role}"
    content = request.fallback_response.strip()
    input_tokens = _estimate_tokens(request.system_prompt) + _estimate_tokens(request.user_prompt)
    output_tokens = _estimate_tokens(content)
    finish_reason = "mock"

    tool_calls: list[dict[str, Any]] | None = None
    raw_message: dict[str, Any] | None = None
    started_at = perf_counter()
    if not settings.llm.mock:
        try:
            call_settings = role_settings
            retry_settings = settings.llm.retry
            if rate_limit_fallback_settings and _is_rate_limit_fallback_active(
                rate_limit_fallback_key
            ):
                call_settings = rate_limit_fallback_settings
                metadata["rate_limit_fallback_active"] = True
                metadata["rate_limit_fallback_primary_model"] = role_settings.model
            elif rate_limit_fallback_settings:
                retry_settings = _primary_retry_settings_for_rate_limit_fallback(
                    settings.llm.retry
                )

            provider = _provider_from_model(call_settings.model)
            model_name = call_settings.model
            (
                content,
                input_tokens,
                output_tokens,
                finish_reason,
                tool_calls,
                raw_message,
            ) = await _call_litellm_with_retry(
                request, call_settings, retry_settings,
            )
            if call_settings is role_settings:
                _clear_rate_limit_fallback(rate_limit_fallback_key)
        except Exception as exc:
            if (
                call_settings is role_settings
                and rate_limit_fallback_settings
                and _is_rate_limit_error(exc)
            ):
                _mark_rate_limit_fallback_active(
                    rate_limit_fallback_key,
                    settings.llm.retry.rate_limit_fallback_cooldown_seconds,
                )
                metadata["rate_limit_fallback_primary_model"] = role_settings.model
                metadata["rate_limit_fallback_reason"] = f"{type(exc).__name__}: {exc}"
                try:
                    provider = _provider_from_model(rate_limit_fallback_settings.model)
                    model_name = rate_limit_fallback_settings.model
                    (
                        content,
                        input_tokens,
                        output_tokens,
                        finish_reason,
                        tool_calls,
                        raw_message,
                    ) = await _call_litellm_with_retry(
                        request,
                        rate_limit_fallback_settings,
                        settings.llm.retry,
                    )
                except Exception as fallback_exc:
                    provider = "fallback"
                    model_name = f"fallback-{request.logical_role}"
                    metadata["configured_model"] = role_settings.model
                    metadata["fallback_model"] = rate_limit_fallback_settings.model
                    metadata["fallback_reason"] = (
                        f"{type(fallback_exc).__name__}: {fallback_exc}"
                    )
                    metadata["primary_rate_limit_reason"] = f"{type(exc).__name__}: {exc}"
                    metadata["retry_exhausted"] = True
                    finish_reason = "fallback"
                    logger.error(
                        "LLM rate-limit fallback FAILED for role=%s primary=%s fallback=%s "
                        "template=%s — using fallback content. Error: %s: %s",
                        request.logical_role,
                        role_settings.model,
                        rate_limit_fallback_settings.model,
                        request.prompt_template,
                        type(fallback_exc).__name__,
                        fallback_exc,
                    )
            else:
                provider = "fallback"
                model_name = f"fallback-{request.logical_role}"
                metadata["configured_model"] = role_settings.model
                metadata["fallback_reason"] = f"{type(exc).__name__}: {exc}"
                metadata["retry_exhausted"] = True
                finish_reason = "fallback"
                logger.error(
                    "LLM call FAILED for role=%s model=%s template=%s — using fallback content. "
                    "Error: %s: %s",
                    request.logical_role,
                    role_settings.model,
                    request.prompt_template,
                    type(exc).__name__,
                    exc,
                )
    latency_ms = int((perf_counter() - started_at) * 1000)

    llm_run = LlmRunModel(
        project_id=request.project_id,
        workflow_run_id=request.workflow_run_id,
        step_run_id=request.step_run_id,
        logical_role=request.logical_role,
        provider=provider,
        model_name=model_name,
        prompt_template=request.prompt_template,
        prompt_version=request.prompt_version,
        prompt_hash=prompt_hash,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        metadata_json=metadata,
    )
    session.add(llm_run)
    await session.flush()

    return LLMCompletionResult(
        content=content,
        provider=provider,
        model_name=model_name,
        llm_run_id=llm_run.id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        raw_message=raw_message,
    )
