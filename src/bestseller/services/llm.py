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


class LLMCompletionResult(BaseModel):
    content: str
    provider: str
    model_name: str
    llm_run_id: UUID | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    finish_reason: str | None = None


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


async def _call_litellm(
    request: LLMCompletionRequest,
    role_settings: LLMRoleSettings,
) -> tuple[str, int | None, int | None, str | None]:
    # Opt-C: install a shared httpx.AsyncClient into litellm on first use, so
    # subsequent calls reuse keep-alive connections to the model provider and
    # avoid per-request TLS handshakes.
    _ensure_shared_litellm_http_client()
    litellm = _get_litellm()
    acompletion = getattr(litellm, "acompletion", None)
    if acompletion is None:
        raise RuntimeError("litellm.acompletion is not available.")

    completion_kwargs: dict[str, Any] = {
        "model": role_settings.model,
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ],
        "temperature": role_settings.temperature,
        "max_tokens": role_settings.max_tokens,
        "timeout": role_settings.timeout_seconds,
        "stream": role_settings.stream,
    }
    # Only pass n when >1 — many providers (MiniMax, Gemini) ignore or
    # reject the parameter, and n=1 is the default anyway.
    if role_settings.n_candidates > 1:
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

    if role_settings.stream:
        return await asyncio.wait_for(
            _collect_streaming_content(response),
            timeout=hard_timeout,
        )

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
    content = _extract_text_content(choice.message.content)
    input_tokens, output_tokens = _extract_usage_fields(getattr(response, "usage", None))
    finish_reason = getattr(choice, "finish_reason", None)
    if not content.strip():
        raise ValueError("LLM response content is empty.")
    return content.strip(), input_tokens, output_tokens, finish_reason


async def _call_litellm_with_retry(
    request: LLMCompletionRequest,
    role_settings: LLMRoleSettings,
    retry_settings: RetrySettings,
) -> tuple[str, int | None, int | None, str | None]:
    """Invoke ``_call_litellm`` with exponential back-off retry.

    Uses the configured :class:`RetrySettings` for max attempts and
    wait bounds.  Each retry doubles the wait (jittered between
    ``wait_min_seconds`` and the current backoff ceiling).
    """
    last_exc: Exception | None = None
    max_attempts = max(1, retry_settings.max_attempts)
    wait_min = retry_settings.wait_min_seconds
    wait_max = retry_settings.wait_max_seconds

    for attempt in range(1, max_attempts + 1):
        try:
            result = await _call_litellm(request, role_settings)
            _llm_breaker.record_success()
            return result
        except Exception as exc:
            last_exc = exc
            _llm_breaker.record_failure()
            if attempt < max_attempts:
                # Exponential backoff: min(wait_max, wait_min * 2^(attempt-1))
                backoff = min(wait_max, wait_min * (2 ** (attempt - 1)))
                logger.warning(
                    "LLM call attempt %d/%d failed (%s: %s) — retrying in %.1fs",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
            else:
                logger.error(
                    "LLM call failed after %d attempts (%s: %s) — falling back",
                    max_attempts,
                    type(exc).__name__,
                    exc,
                )
    raise last_exc  # type: ignore[misc]


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
    prompt_hash = _hash_prompt(request.system_prompt, request.user_prompt)
    metadata = dict(request.metadata)
    latency_ms: int | None = None
    provider = "mock"
    model_name = f"mock-{request.logical_role}"
    content = request.fallback_response.strip()
    input_tokens = _estimate_tokens(request.system_prompt) + _estimate_tokens(request.user_prompt)
    output_tokens = _estimate_tokens(content)
    finish_reason = "mock"

    started_at = perf_counter()
    if not settings.llm.mock:
        try:
            provider = _provider_from_model(role_settings.model)
            model_name = role_settings.model
            (
                content,
                input_tokens,
                output_tokens,
                finish_reason,
            ) = await _call_litellm_with_retry(
                request, role_settings, settings.llm.retry,
            )
        except Exception as exc:
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
    )
