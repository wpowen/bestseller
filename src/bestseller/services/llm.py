from __future__ import annotations

import asyncio
import contextvars
from contextlib import contextmanager
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

from bestseller.infra.db.models import LlmRunModel, ProjectModel
from bestseller.services.word_targets import (
    model_min_output_tokens,
    model_output_token_ceiling,
)
from bestseller.settings import (
    AppSettings,
    LLMRoleSettings,
    RetrySettings,
    apply_runtime_llm_profile,
    get_runtime_env_value,
    load_settings,
)

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
    max_tokens_override: int | None = Field(default=None, ge=1)
    #: Per-call model-catalog override (a ``config/model_catalog.yaml`` entry id).
    #: When set and available, it overrides the resolved role model for THIS call —
    #: and wins over the book's per-project model. Used so the commercial judges can
    #: score ranking quality with a capable (Claude-tier) model even when the book's
    #: writer runs on a budget model. ``None`` = use the configured role model.
    model_catalog_key: str | None = Field(default=None, max_length=128)
    cache_system: bool = Field(
        default=False,
        description=(
            "If True and provider is Anthropic, wrap system_prompt in "
            "cache_control=ephemeral. Only enable for stable system prompts."
        ),
    )

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


def _effective_request_max_tokens(
    role_settings: LLMRoleSettings,
    request: LLMCompletionRequest,
) -> int:
    role_cap = int(role_settings.max_tokens)
    model_ceiling = model_output_token_ceiling(role_settings.model)
    # 防截断:正文角色(writer/editor)按模型给足输出预算,不被固定 role_cap 卡短。
    # 关 thinking 时模型会在 finish_reason="stop" 提前收尾,留头空间不浪费 token。
    base = role_cap
    if request.logical_role in ("writer", "editor"):
        model_floor = model_min_output_tokens(role_settings.model)
        if model_floor and model_floor > base:
            base = model_floor
    if model_ceiling is not None:
        base = min(base, int(model_ceiling))
    if request.max_tokens_override is None:
        return base
    # An explicit override is the caller's deliberate cap and must be RESPECTED,
    # including when it intentionally LOWERS the budget (empty-length recovery retry,
    # chapter-first runaway guard). Do NOT max() it against the role/floor base — that
    # silently ignored low overrides. For prose roles still floor it to the per-model
    # minimum so a too-small override can't truncate a reasoning model mid-chapter.
    target = max(1, int(request.max_tokens_override))
    if request.logical_role in ("writer", "editor"):
        model_floor = model_min_output_tokens(role_settings.model)
        if model_floor and target < model_floor:
            target = model_floor
    if model_ceiling is not None:
        return min(target, int(model_ceiling))
    return target


def _is_empty_length_response_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "response content is empty" in message
        and "finish_reason='length'" in message
    )


_PROSE_LENGTH_RETRY_KEEP_CAP_TEMPLATES = frozenset(
    {
        "scene_writer",
        "scene_writer_regen",
        "chapter_first_writer",
        "scene_rewrite",
        "chapter_rewrite",
        "chapter_rewrite_repair",
        "chapter_rewrite_quality_retrofit_repair",
    }
)
# Prefixes catch A/B / variant suffixes (e.g. scene_writer_lean, scene_writer_c0,
# chapter_rewrite_v2). Prose generation that returns empty with
# finish_reason='length' was TRUNCATED — lowering max_tokens makes the next
# attempt truncate sooner, so prose templates must keep (not shrink) their cap.
# The set was previously empty, so EVERY prose writer wrongly lowered its cap on
# an empty-length retry and could churn without ever emitting a full scene.
_PROSE_LENGTH_RETRY_KEEP_CAP_PREFIXES = (
    "scene_writer",
    "chapter_first_writer",
    "scene_rewrite",
    "chapter_rewrite",
)
_PROMPT_TEMPLATE_RE = re.compile(r"^[a-z_]+(\.[a-z_]+)*(_repair|_v\d+)?$")
_PROMPT_TEMPLATE_ALLOWLIST_PREFIXES = ("planner_", "listing.regenerate.")


def _should_lower_max_tokens_after_empty_length(request: LLMCompletionRequest) -> bool:
    template = str(request.prompt_template or "").strip()
    if template in _PROSE_LENGTH_RETRY_KEEP_CAP_TEMPLATES:
        return False
    if template.startswith(_PROSE_LENGTH_RETRY_KEEP_CAP_PREFIXES):
        return False
    return True


def _validate_prompt_template_name(request: LLMCompletionRequest) -> None:
    template = str(request.prompt_template or "").strip()
    if not template:
        return
    if template.startswith(_PROMPT_TEMPLATE_ALLOWLIST_PREFIXES):
        return
    if _PROMPT_TEMPLATE_RE.match(template):
        return
    logger.warning("Prompt template name violates convention: %s", template)


def _max_attempts_for_request(
    retry_settings: RetrySettings,
    request: LLMCompletionRequest,
) -> int:
    per_class = retry_settings.max_attempts_per_class or {}
    template = str(request.prompt_template or "")
    if template.endswith("_repair") or "_repair" in template:
        return max(1, int(per_class.get("repair", retry_settings.max_attempts)))
    return max(1, int(per_class.get("default", retry_settings.max_attempts)))


def _rate_limit_fallback_key(logical_role: LLMRole, role_settings: LLMRoleSettings) -> str:
    return "|".join(
        [
            logical_role,
            role_settings.model,
            role_settings.api_base or "",
            role_settings.api_key_env or "",
        ]
    )


def _effective_thinking_type(role_settings: LLMRoleSettings) -> str | None:
    """Return provider thinking mode, defaulting prose calls to visible text.

    DeepSeek V4 defaults to emitting reasoning tokens before normal content.
    Through LiteLLM this can exhaust the request's max_tokens budget and return
    an empty assistant content string. MiniMax-M3 also defaults to adaptive
    thinking. For this app, these models are used for production prose/review
    text, so disable thinking unless the role explicitly opts in.
    """

    model = (role_settings.model or "").lower()
    api_base = (role_settings.api_base or "").lower()
    if role_settings.thinking_type:
        tt: str | None = role_settings.thinking_type
    elif "deepseek-v4" in model and "deepseek" in api_base:
        tt = "disabled"
    elif "minimax-m3" in model:
        tt = "disabled"
    elif "minimax-m2" in model and "highspeed" in model:
        tt = "disabled"
    else:
        tt = None
    return _normalize_thinking_type_for_model(tt, model)


def _normalize_thinking_type_for_model(thinking_type: str | None, model: str) -> str | None:
    """Coerce thinking.type to each provider's accepted vocabulary.

    MiniMax M-series only accepts {"adaptive","disabled"} — passing "enabled"
    (a generic value some other providers use) raises a 400 BadRequestError and
    breaks every call once that model is selected. Map invalid values safely.
    """

    if not thinking_type:
        return None
    m = (model or "").lower()
    if "minimax" in m:
        if thinking_type in ("adaptive", "disabled"):
            return thinking_type
        if thinking_type in ("enabled", "on", "auto", "high", "true"):
            return "adaptive"
        return "disabled"
    return thinking_type


def _model_supports_n_param(model: str | None) -> bool:
    """Whether the provider accepts OpenAI-style ``n`` (multiple choices) param.

    MiniMax raises ``model does not support n > 1`` (400) — forwarding ``n`` there
    breaks every writer call. Application-level best-of-N (drafts.py) loops the
    request instead, so the param must simply not be sent for such models.

    qwen3.x-plus / -max default ``enable_thinking=true`` server-side and 400 with
    "n parameter must be 1 when enable_thinking is true" — and the thinking flag
    is not something we set via ``thinking_type`` here, so the only reliable
    guard is to never forward ``n`` for them (the app layer loops anyway).
    """

    m = (model or "").lower()
    if "minimax" in m:
        return False
    if "qwen" in m:
        return False
    return True


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


def _extract_prompt_contract_text(prompt: str, *field_names: str) -> str:
    """Best-effort extraction for deterministic mock prose.

    The real model receives structured prompt blocks and can obey them
    semantically. The mock writer needs a small parser so E2E smoke tests still
    exercise the same hard obligations instead of failing because the fixture
    ignored visible contract fields.
    """

    if not prompt:
        return ""
    for name in field_names:
        for pattern in (
            rf'"{re.escape(name)}"\s*:\s*"([^"]{{2,120}})"',
            rf"'{re.escape(name)}'\s*:\s*'([^']{{2,120}})'",
            rf"{re.escape(name)}[：:]\s*([^\n，,；;]{{2,120}})",
        ):
            match = re.search(pattern, prompt)
            if not match:
                continue
            value = match.group(1).strip().strip("\"' ")
            if value and value.lower() not in {"null", "none", "false"}:
                return value
    return ""


def _mock_content_for_request(request: LLMCompletionRequest) -> str:
    """Return deterministic mock content that can pass local functional verification."""

    content = request.fallback_response.strip()
    if (
        request.prompt_template in {"scene_writer", "scene_writer_regen"}
        and content.startswith("<!-- scene-draft-fallback")
    ):
        chapter_number = int(request.metadata.get("chapter_number") or 1)
        scene_number = int(request.metadata.get("scene_number") or 1)
        protagonist_name = str(request.metadata.get("protagonist_name") or "").strip() or "沈砚"
        supporting_name = str(request.metadata.get("supporting_name") or "").strip() or "顾临"
        context_query = str(request.metadata.get("context_query") or "")
        prompt_signal = "\n".join(
            [
                request.system_prompt,
                request.user_prompt,
                context_query,
                str(request.metadata.get("project_slug") or ""),
            ]
        )
        prompt_signature_image = _extract_prompt_contract_text(
            prompt_signal,
            "signature_image",
            "标志画面",
        )
        prompt_cut_point = _extract_prompt_contract_text(
            prompt_signal,
            "cut_point",
            "breakpoint",
            "断点",
            "ending_hook_payload",
        )
        is_apocalypse_supply = any(
            term in prompt_signal
            for term in ("apocalypse-supply", "末日", "囤货", "物资", "避难所", "重建秩序")
        )
        scene_focus = (
            "顾临与失踪巡逻舰"
            if "顾临" in context_query or chapter_number >= 2
            else ("偏移航标与旧日志" if scene_number >= 2 else "封港命令与港务官")
        )
        details = [
            ("警报", "碎潮星港的警报沿着穹顶逐层亮起，红光把每一张值守脸都切成锋利的阴影。"),
            ("钥匙", "沈砚把旧校准钥匙藏进掌心，金属边缘硌着伤口，提醒他不能再把真相交给别人的报告。"),
            ("阻力", "港务频道反复播报禁令，港务官要求他交出权限卡，巡检无人机已经贴着舱门降低高度。"),
            ("选择", "他没有和任何人争辩，而是把临时任务单折成两半，借签收动作遮住接入底层端口的手势。"),
            ("发现", "航标日志里跳出一串不该存在的校准签名，时间戳新得像刚凝固的血，签名人却写着沈砚。"),
            ("代价", "如果这条接入记录被追踪，他会重新背上七年前的事故罪名，甚至连离开星港的资格都被抹掉。"),
            ("反应", "他听见自己呼吸变轻，视野却异常清楚，所有航线偏移值像冰冷星点一样排成可追索的路径。"),
            ("人物", "港务官敲了三下封港章，声音短促而克制，像是在提醒他这里没有友情，只有流程和责任。"),
            ("推进", f"{scene_focus}不再是传闻，而变成摆在他眼前的坐标、签名和被删除的二级校准指令。"),
            ("情绪", "沈砚忽然意识到，自己这些年反复梦见的并不是舰队爆炸，而是最后一秒无人相信他的沉默。"),
            ("行动", "他把日志切成三份缓存，一份写入袖扣里的旧芯片，一份伪装成维护回执，一份投向顾临留下的暗线地址。"),
            ("压力", "倒计时还剩十七分钟，外港封锁闸已经开始下落，所有没有授权的船只都会被锁进黑潮前的死水区。"),
            ("转折", "就在他准备拔出钥匙时，底层日志自动展开第二个隐藏页，里面记录着祁镇亲自批准的非法校准。"),
            ("钩子", "屏幕最下方忽然亮起一段求救声纹：那不是事故录音的结尾，而是有人刚刚从静默航道重新发送的坐标。"),
        ]
        extensions = [
            "他把眼前的每个读数都和七年前的事故报告对照，发现官方叙述里有一条始终没有被解释的空白。",
            "远处的牵引臂拖着货箱缓慢转向，箱体编号与被封存的航线档案出现了同一组尾码。",
            "通讯器里传来三秒静默，那是顾临过去约定的危险信号，说明军方频道已经有人旁听。",
            "港口外壁震了一下，黑潮前锋擦过防波盾，所有灯光都短暂偏成不自然的蓝色。",
            "沈砚逼自己不去想失败后的审判，只把证据链按时间顺序压进脑海，像重新校准一条濒死航线。",
            "港务官的手停在封锁确认键上，迟疑短得几乎看不见，却足以证明他也听出了日志里的异常。",
            "一名年轻巡检员想要开口，又被上级频道的噪声压回去，整个星港像在共同保守一个不敢说出的秘密。",
            "沈砚把旧钥匙转过半圈，端口发出轻微咬合声，隐藏字段终于从灰色变成可读取的白色。",
            "屏幕反光里，他看见自己的脸比七年前更冷，也更像那个被所有人要求认罪的幸存者。",
            "如果现在撤退，他可以活得久一点；如果继续读取，他至少能让下一艘船知道自己为什么会死。",
            "顾临留下的暗线地址闪了一下，像有人在远处确认收到缓存，却又不敢暴露自己的位置。",
            "祁镇的批注没有情绪，只有整齐的权限编号，正因为太整齐，反而像一把擦净血迹的刀。",
            "封港闸下落到三分之二时，沈砚终于把证据包推送出去，代价是自己的实时位置被系统标红。",
            "下一秒，静默航道传回坐标回声，回声里夹着一个不该存活的舰队呼号。",
        ]
        if is_apocalypse_supply:
            scene_focus = "地下冷库、社区仓库与第一条物资秩序"
            details = [
                ("冷库", f"{protagonist_name}撬开地下冷库的外锁时，先闻到断电肉类的酸味，再看见货架深处还冻着三箱胰岛素。"),
                ("筹码", "邻居们挤在消防门外等水，他手里的清单却显示整栋楼只剩二十七桶可饮用水。"),
                ("误判", "物业群里有人直播骂他囤货，下一秒楼下超市卷帘门被人群砸出第一道裂缝。"),
                ("选择", f"{protagonist_name}没有急着解释，而是把退烧药、净水片和柴油分成三组，先换来最缺人的巡楼班。"),
                ("阻力", "一个自称救援队亲戚的人拿着空白证明闯进来，开口就要接管仓库钥匙。"),
                ("发现", "旧账本里夹着一张停电前的配送单，目的地不是医院，而是城北一处刚搭起围挡的私仓。"),
                ("代价", "如果他现在追查私仓，避难所今晚的分粮就会少一个能压住局面的人。"),
                ("反应", "孩子的咳嗽声从楼道拐角传来，所有争吵都停了一秒，又在缺水的现实里重新变硬。"),
                ("人物", f"{supporting_name}把菜刀反扣在桌面上，没有威胁谁，只问每个人愿不愿意按登记表领物资。"),
                ("推进", f"{scene_focus}不再是口号，而变成写在墙上的领用规则、巡逻路线和违规后果。"),
                ("情绪", f"{protagonist_name}忽然明白，重生最大的优势不是知道灾难，而是敢在别人还等救援时先建立秩序。"),
                ("行动", "他把仓库钥匙拆成两把，一把交给登记员，一把压在公共白板下，让每次开仓都被所有人看见。"),
                ("压力", "暴雨开始倒灌地下车库，备用柴油最多撑六小时，冰柜里的药比任何金条都更怕时间。"),
                ("钩子", "配送单背面忽然浮出一行水印：城北私仓的签收人，竟然是他上一世临死前救过的那个人。"),
            ]
            extensions = [
                "他没有把物资当成胜利奖杯，而是把每件东西都换成可执行的规则。",
                "楼道里的目光从敌意变成计算，说明这套秩序还没有赢，却已经开始被认真衡量。",
                "水声贴着台阶往上爬，逼每个人承认时间比面子更值钱。",
                "有人想趁乱多拿两瓶药，被登记员当众划掉下一轮优先权，现场第一次安静下来。",
                "重生记忆只能告诉他哪里会塌，不能替他决定该先救谁。",
                "他在白板上写下第一条硬规则：谁维护秩序，谁先获得下一轮物资信用。",
                "那张配送单让囤货线从个人自救变成对隐藏仓储网络的追查。",
                "每一次分配都在制造新的同盟，也在制造下一次背叛的理由。",
                "楼外的雨水把城市泡成灰色，楼内的秤砣却第一次让人相信还能活过今晚。",
                "他知道这不是善良问题，而是避难所能不能熬到第二天的问题。",
                "当第一支巡楼班出发时，反对他的人也不得不把门缝开大一点。",
                "柴油味、湿衣服和退烧药的苦味混在一起，构成末日第一夜真正的权力气味。",
                "城北私仓像一枚还没拆开的雷，既可能救命，也可能把刚立起来的秩序炸碎。",
                "他把签收人的名字记下来，决定明天用一箱净水片换一次出城机会。",
            ]
            apocalypse_profiles = [
                (
                    "地下冷库与第一张分粮表",
                    [
                        ("冷库", f"{protagonist_name}撬开地下冷库的外锁时，先闻到断电肉类的酸味，再看见货架深处还冻着三箱胰岛素。"),
                        ("清点", "楼道里挤满等水的人，他把矿泉水、净水片和退烧药分成三栏，当众写下剩余数量。"),
                        ("误判", "物业群里有人直播骂他囤货，下一秒楼下超市卷帘门被人群砸出第一道裂缝。"),
                        ("分工", f"{supporting_name}把登记本摊到消防箱上，要求每户派一个人加入夜巡，才有下一轮领用资格。"),
                        ("试探", "一个自称救援队亲戚的人拿着空白证明闯进来，开口就要接管仓库钥匙。"),
                        ("证据", "旧账本里夹着一张停电前的配送单，目的地不是医院，而是城北一处刚搭起围挡的私仓。"),
                        ("取舍", "如果他现在追查私仓，避难所今晚的分粮就会少一个能压住局面的人。"),
                        ("公开", "他把仓库钥匙拆成两把，一把交给登记员，一把压在公共白板下，让每次开仓都被所有人看见。"),
                        ("压力", "暴雨开始倒灌地下车库，备用柴油最多撑六小时，冰柜里的药比任何金条都更怕时间。"),
                        ("钩子", "配送单背面浮出一行水印：城北私仓的签收人，是他上一世临死前救过的人。"),
                    ],
                    [
                        "他没有把物资当成胜利奖杯，而是把每件东西都换成可执行的规则。",
                        "楼道里的目光从敌意变成计算，说明这套秩序还没有赢，却已经开始被认真衡量。",
                        "水声贴着台阶往上爬，逼每个人承认时间比面子更值钱。",
                        "登记本第一次让争吵有了边界，也让反对者看见可以讨价还价的筹码。",
                        "他没有解释重生，只让对方在所有人面前报出所属单位和物资去向。",
                        "那张配送单把囤货线从个人自救推向隐藏仓储网络。",
                        "他知道善良不是免费发放，而是让明天还能继续发放。",
                        "透明规则让人不舒服，却比黑箱更难被立刻推翻。",
                        "柴油味、湿衣服和药片苦味混在一起，构成末日第一夜真正的权力气味。",
                        "他记下签收人的名字，决定用一箱净水片换一次出城机会。",
                    ],
                ),
                (
                    "地下车库抽水线",
                    [
                        ("水位", "地下车库的积水已经淹过脚踝，漂起来的纸箱撞在车门上，像一排没有声音的警告。"),
                        ("电源", f"{protagonist_name}把柴油机推上坡道，发现油箱盖被人撬过，地上只剩一圈新鲜油渍。"),
                        ("交换", "三户人家愿意拿私藏电瓶换净水片，却要求先给自己家楼层恢复照明。"),
                        ("病人", "七楼老人低烧不退，家属跪在楼梯口要药，身后的人群开始质问凭什么他能插队。"),
                        ("规则", f"{supporting_name}把体温计递给登记员，让病情优先级代替哭声大小。"),
                        ("背刺", "昨晚支持他的保安偷偷放进两个外来人，理由是对方手里有半桶柴油。"),
                        ("损耗", "抽水泵每运行十分钟就要停三分钟，错过窗口，整层仓库都会泡进污水。"),
                        ("承诺", f"{protagonist_name}当众承诺先救药品，再保食物，最后才轮到私人物资。"),
                        ("惩罚", "偷油的人被要求带队下水搬沙袋，若能补回损耗，下一轮口粮不扣给家属。"),
                        ("钩子", "抽水泵重新启动时，排水口冲出一枚印着城北私仓标识的塑封门禁卡。"),
                    ],
                    [
                        "这个现场不再是分粮争吵，而是避难所能不能守住底层基础设施。",
                        "他看见的不是偷窃本身，而是规则还没硬到让人相信违规会付代价。",
                        "电瓶成了临时货币，也把每个人的自救算盘摆到台面上。",
                        "哭声不能成为分配标准，否则今晚的队伍会被最会崩溃的人接管。",
                        "医疗优先级让人难堪，却把无序的人情压回可核验的流程。",
                        "他没有立刻赶人，因为末日里每一份燃料都有可能买来下一小时。",
                        "机器的喘息声提醒所有人，秩序不是口号，而是会被水位直接检验的东西。",
                        "这条承诺把他自己也绑进规则里，下一次偏私会更容易被抓住。",
                        "惩罚不是泄愤，而是把破坏者临时改造成可用劳力。",
                        "那张门禁卡证明，外部仓储网络已经伸进小区内部。",
                    ],
                ),
                (
                    "超市废墟与私仓线索",
                    [
                        ("出门", f"{protagonist_name}带着三个人穿过被雨水泡胀的商业街，背包里只有净水片和一把断柄消防斧。"),
                        ("废墟", "超市货架被抢空，地上却散着没拆封的婴儿奶粉，说明有人只拿指定清单。"),
                        ("伏击", "收银台后方传来金属碰撞声，两个戴袖章的人要求他们交出避难所地址。"),
                        ("谈判", f"{supporting_name}没有拔刀，只把一瓶抗生素放到台面上，问对方知道不知道城北私仓入口。"),
                        ("骗局", "对方报出的路线绕过三处积水，却故意漏掉一座已经坍塌的人行桥。"),
                        ("验证", f"{protagonist_name}用上一世记忆校对街区广告牌，确认真正入口藏在冷链配送站后院。"),
                        ("救人", "他们在冷柜下救出一个被压住的配送员，对方手腕上还扣着私仓临时通行环。"),
                        ("代价", "配送员愿意带路，条件是先把一袋奶粉送回隔壁楼的婴儿房。"),
                        ("选择", f"{protagonist_name}同意绕路，却把净水片拆成两份，防止带路人半途反悔。"),
                        ("钩子", "冷链站后门打开时，里面亮着不该存在的应急灯，还有人正在按名单分装药品。"),
                    ],
                    [
                        "这次离开避难所，让物资秩序第一次面对外部势力。",
                        "奶粉被留下不是善意，而是抢货者有更准确的目标。",
                        "避难所地址变成新的资源，泄露出去会比少一箱食物更危险。",
                        "抗生素的出现把谈判从恐吓拉回交易，因为双方都知道药比刀更稀缺。",
                        "错误路线暴露了对方想让他们死在路上，却又不敢亲自动手。",
                        "重生记忆在这里不是外挂，而是校准谎言的一把尺。",
                        "这个配送员既是累赘，也是第一把能打开私仓的钥匙。",
                        "婴儿房让任务多出人命重量，也逼队伍承认秩序不能只服务强者。",
                        "拆分净水片不是不信任，而是让背叛成本变得可计算。",
                        "应急灯把城北私仓从传闻变成了眼前的敌人。",
                    ],
                ),
                (
                    "避难所第一次公开审判",
                    [
                        ("回归", f"{protagonist_name}回到小区时，白板上的分粮表被人撕掉一半，只剩药品栏还挂在墙上。"),
                        ("谣言", "有人散播他说服外人搬空仓库，楼道里每一扇门都只开出一条审视的缝。"),
                        ("证人", "被救回的配送员坐在折叠椅上，颤声说出私仓名单里有本楼三户人的名字。"),
                        ("摊牌", f"{supporting_name}把门禁卡、配送单和半袋奶粉摆成一排，要求所有领过额外物资的人站出来。"),
                        ("失控", "一名住户抱着孩子冲出来，承认拿过药，却说那是给孩子换命。"),
                        ("规则", f"{protagonist_name}没有没收她的药，只要求她公开私仓联系人，并加入夜间医疗队。"),
                        ("反扑", "真正的中间人趁人群松动想下楼，刚到转角就被巡楼班堵回公共大厅。"),
                        ("审判", "第一次公开处罚不是逐出避难所，而是取消三轮优先权、补齐两班最危险的巡逻。"),
                        ("认可", "沉默最久的老人把自家半桶水推到白板下，说这一轮按新规登记。"),
                        ("钩子", "中间人的手机忽然亮起新消息：城北私仓今晚转移，目标正是他们刚守住的地下冷库。"),
                    ],
                    [
                        "外出带回的不只是线索，还有足以撕裂内部信任的新证据。",
                        "谣言逼他证明规则不是个人权力，而是所有人能共同核验的东西。",
                        "配送员的证词让敌人从外部影子变成邻里之间的真实名字。",
                        "证据摆出来以后，争吵终于不能只靠嗓门决定输赢。",
                        "孩子让规则遇到最难看的例外，也让所有人盯住他会不会偏私。",
                        "他把惩罚改造成义务，因为避难所不能浪费任何一个还能工作的人。",
                        "巡楼班的存在证明上一场分粮换来的不是服从，而是可执行的组织。",
                        "处罚不够痛快，却足够让下一次违规前先计算代价。",
                        "这桶水不是投降，而是避难所第一次用行动承认新秩序。",
                        "新消息把内部审判直接推向下一场仓库保卫战。",
                    ],
                ),
            ]
            profile_focus, profile_details, profile_extensions = apocalypse_profiles[
                (scene_number - 1) % len(apocalypse_profiles)
            ]
            scene_focus = profile_focus
            details = [*profile_details[:3], profile_details[-1]]
            extensions = [*profile_extensions[:3], profile_extensions[-1]]
            if prompt_signature_image:
                label, sentence = details[0]
                if prompt_signature_image not in sentence:
                    details[0] = (label, f"{sentence}{prompt_signature_image}")
            if prompt_cut_point:
                label, sentence = details[-1]
                if prompt_cut_point not in sentence:
                    details[-1] = (label, f"{sentence}{prompt_cut_point}")
        if chapter_number >= 2 and not is_apocalypse_supply:
            details = [
                ("重逢", "顾临站在巡逻舰断裂的登舰桥尽头，军装肩章被冷雾打湿，却始终没有放低枪口。"),
                ("黑匣", "失踪巡逻舰的黑匣子卡在主控台下方，外壳有烧灼痕，仍按旧军规每隔九秒闪一次蓝灯。"),
                ("军令", "军方频道命令顾临撤离，理由是静默航道存在污染风险，但撤离码和祁镇办公室的私钥相连。"),
                ("对质", "沈砚把签名实证投到顾临面前，没有解释旧怨，只问他还敢不敢看完最后一段航行记录。"),
                ("误会", "顾临的手指停在扳机外侧，他想起七年前自己被迫送走求救包时，沈砚正被全舰广播点名定罪。"),
                ("违令", "他最终切断上级监听，把舰桥门反锁，代价是自己的副官编号立刻进入军纪审查队列。"),
                ("读取", "黑匣子吐出断续声纹，第一句不是求救，而是有人低声要求舰队关闭对外定位。"),
                ("证人", "录音里出现第三个人的呼吸声，频率和祁镇私人护卫队的加密通道完全一致。"),
                ("裂痕", "沈砚没有立刻胜利的快感，只有迟来的寒意，因为真相证明顾临当年并不是抛下他。"),
                ("选择", "顾临把自己的权限徽章塞给沈砚，承认这会毁掉仕途，却能打开失踪舰最后一层舱门。"),
                ("追兵", "舱外传来磁靴落地声，巡逻队没有喊话，直接把切割枪贴上封闭门缝。"),
                ("交换", "沈砚用缓存证据换顾临三分钟掩护，顾临则要求他若自己被捕，必须把黑匣子送出边境。"),
                ("揭露", "舱门内侧刻着一排遇难者名字，其中一个名字被新鲜划掉，旁边补上了沈砚的旧权限号。"),
                ("尾声", "黑匣子最后弹出一枚坐标，坐标指向祁镇亲自封存的边境校准总库。"),
            ]
            extensions = [
                "顾临没有再说抱歉，因为那两个字太轻，压不住七年前那艘船沉没时留下的重量。",
                "沈砚蹲下去拆开保护扣，闻到绝缘层烧焦后的甜腥味，知道这艘船不是自然失联。",
                "监听灯熄灭的一瞬间，舰桥里只剩两个人的呼吸和远处推进器冷却时的金属哀鸣。",
                "旧搭档之间的信任没有恢复，只是被更大的危险逼出一条临时通道。",
                "每一段声纹都像从海底拖上来的尸体，沉默多年后终于开始指认活人。",
                "顾临把军帽扣在终端摄像头上，那动作轻得像玩笑，却等于向整套军纪宣战。",
                "沈砚听见自己的名字被录音里的陌生人念出，终于确认有人从一开始就在替他写罪名。",
                "封锁门外的切割火花照亮顾临侧脸，他没有回头，只让沈砚继续读下去。",
                "他们谁也没有提旧情，只在同一张星图前同时伸手，指向完全相同的异常节点。",
                "这一次顾临没有选择服从命令，沈砚也没有选择独自背走全部证据。",
                "追兵的脚步越来越近，黑匣子的进度条却慢得像在故意折磨每一个幸存者。",
                "沈砚把证据包压缩成三层密钥，第一层写顾临的编号，第二层写失踪舰呼号，第三层留给死者。",
                "那排名字让顾临终于变了脸，因为他认出其中一人曾在事故前夜给自己发过空白讯息。",
                "坐标出现时，整艘巡逻舰短暂恢复供电，像某个被压住的亡魂终于睁开眼睛。",
            ]
        if chapter_number >= 3 and not is_apocalypse_supply:
            stage = "审计塔数据库" if chapter_number == 3 else "边境校准总库"
            secondary = supporting_name if supporting_name != protagonist_name else "沈远"
            objects = [
                "一枚被拆封的许可章",
                "半张烧焦的航线批文",
                "审计塔底层的白色门禁卡",
                "写着旧舰队呼号的缴款单",
                "被调换序号的证据柜",
                "一段只剩背景噪声的听证录音",
                "夹在档案袋里的儿童照片",
                "总库外墙新刷的禁行编号",
                "二十七分钟前才生成的拘捕令",
                "没有签名却盖过章的调度函",
                "被人为降权的遇难者名单",
                "一支还带着温度的加密笔",
                "从备用电梯落下的蓝色灰尘",
                "指向总库地下层的纸质地图",
            ]
            obstacles = [
                "值守系统把他的权限降成访客",
                f"{secondary}要求先救被扣住的线人",
                "广播里开始重复他的旧罪名",
                "两名审计员同时改口说从未见过这份文件",
                "备用通道被临时焊死",
                "对方把证据拆成三段分别转移",
                "总库的温控突然降到会冻裂设备的程度",
                "一份伪造自白被投到公共频道",
                "封锁车队提前七分钟抵达",
                "档案管理员主动递来一份明显过新的口供",
                "旧案编号被系统改写成不存在",
                "一名证人隔着玻璃向他摇头",
                "地面震动暴露了地下层仍在运转",
                "最后一道门只接受死者权限",
            ]
            details = [
                (
                    label,
                    f"{protagonist_name}在{stage}追到{objects[(index + scene_number) % len(objects)]}，"
                    f"却发现{obstacles[(index + chapter_number) % len(obstacles)]}。",
                )
                for index, (label, _sentence) in enumerate(details)
            ]
            extensions = [
                (
                    f"{secondary}没有立刻表态，只把第{index + 1}份旁证推到灯下，"
                    f"让{protagonist_name}看见这不是上一章遗留的同一条线索。"
                )
                for index in range(len(details))
            ]
        if chapter_number >= 4 and not is_apocalypse_supply:
            secondary = supporting_name if supporting_name != protagonist_name else "沈远"
            final_events = [
                f"{protagonist_name}关闭总库检索屏，改用公开频道播放第一段遇难者回声。",
                f"{secondary}把封存门的死者权限交到他手里，要求他先决定是否让全城听见真相。",
                "总库中庭的穹顶灯一盏盏熄灭，所有旁观者的终端却同时亮起事故坐标。",
                f"{protagonist_name}没有再追问谁批准了命令，而是把批准链逐级投到墙面上。",
                "封锁队冲进中庭时没有开枪，因为每支枪的执法记录都已经被同步到公共屏。",
                f"{secondary}承认自己曾经删掉一段求救包，声音不高，却让人群第一次停止后退。",
                f"{protagonist_name}把旧校准钥匙折断，露出里面藏着的原始航线种子。",
                "顾铭的远程影像试图切断直播，却被遇难者名单反向锁进同一个频道。",
                "一名曾经沉默的审计员走出队列，把第二枚许可章放在地上。",
                f"{protagonist_name}终于说出七年前自己没有说完的证词，每个字都压着迟来的怒意。",
                "总库地下层传来机械解锁声，真正的校准核心从地面升起。",
                f"{secondary}挡在他身前，替他接下第一份公开逮捕令。",
                f"{protagonist_name}没有逃，他把证据包拆成四份，分别交给敌人、证人、死者家属和自己。",
                "最后一条航线恢复原始编号，边境所有灯塔在同一秒转向真实坐标。",
            ]
            details = [
                (label, final_events[index % len(final_events)])
                for index, (label, _sentence) in enumerate(details)
            ]
            extensions = [
                f"这一刻的重点不再是寻找第{index + 1}件证据，而是让证据承担公开后的代价。"
                for index in range(len(details))
            ]
        details = [(label, sentence.replace("沈砚", protagonist_name)) for label, sentence in details]
        extensions = [extension.replace("沈砚", protagonist_name) for extension in extensions]
        rotation = (chapter_number * 3 + scene_number) % len(details)
        ordered = details[rotation:] + details[:rotation]
        lead = (
            f"{protagonist_name}在警报第一声落下时就接入底层日志，指尖贴着旧校准钥匙的裂口，"
            "他没有抬头看任何人，只盯住那枚新出现的异常签名。"
            f"如果第{scene_number}轮警戒切换前不能判断真伪，他会失去本章唯一能撬开真相的入口。"
        )
        if is_apocalypse_supply:
            apocalypse_leads = [
                (
                    f"{protagonist_name}把净水片倒进周转箱。"
                    "楼道里有人哭着问救援什么时候来，手机信号却只剩一格灰色。"
                    "如果第一轮分粮前不能立下公开规则，仓库会先于城市失守。"
                ),
                (
                    f"{protagonist_name}踩进地下车库的污水里，手电照见柴油机旁边一串新鲜脚印。"
                    "抽水泵停摆后，整栋楼的药品和食物都会在天亮前泡烂。"
                    "他必须先找回燃料，再决定谁有资格用电瓶换药。"
                ),
                (
                    f"{protagonist_name}推开超市后门时，货架上的灰尘被雨风卷成一层白雾。"
                    "这里没有幸存者欢呼，只有被精准挑空的补给位和一条通向城北私仓的假线索。"
                    "他带出来的净水片只够谈判一次。"
                ),
                (
                    f"{protagonist_name}回到避难所，看见白板上的分粮表被撕掉一半。"
                    "每扇门后都有人听着，等他解释外出的代价。"
                    "他不能只带回物资，还必须带回一套能处理背叛的公开规则。"
                ),
            ]
            lead = apocalypse_leads[(scene_number - 1) % len(apocalypse_leads)]
        elif chapter_number == 2:
            lead = (
                f"{protagonist_name}踏进失踪巡逻舰的断裂舰桥时，先看见顾临没有放下的枪口，"
                "再看见主控台下仍在闪蓝灯的黑匣子。"
                "旧怨没有给他们寒暄的余地，门外切割枪已经贴上封闭舱门。"
            )
        elif chapter_number == 3:
            lead = (
                f"{protagonist_name}在审计塔数据库门口停住脚步，先把{supporting_name}递来的许可章翻到背面，"
                "再确认章柄里藏着一枚刚被抹除的航线尾码。"
                "这一章的危险不在枪口，而在每一份文件都可能替他说谎。"
            )
        elif chapter_number >= 4:
            lead = (
                f"{protagonist_name}抵达边境校准总库时，穹顶下所有灯带同时熄灭，"
                f"{supporting_name}在黑暗里报出最后一道门的死者权限。"
                "他终于明白，终局不是找到真相，而是决定让谁承担公开真相的代价。"
            )
        paragraphs = [lead]
        ordered_extensions = extensions[rotation:] + extensions[:rotation]
        connectors = [
            "这让局势从猜测变成必须立即处理的证据。",
            "短暂沉默后，场面里的每个人都明白退路正在缩窄。",
            "这条线索不再停在屏幕上，而是直接改变了下一步行动。",
            "他压住情绪，把注意力放回可验证的细节。",
            "危险没有爆开，却在每一次读数跳动里继续逼近。",
            "这一次判断会留下痕迹，也会暴露他的位置。",
            "没有人替他解释，事实只能靠行动抢回来。",
        ]
        pressure_clauses = [
            "这一步把矛盾从口头争执压到可以验证的现场。",
            "新的阻力没有重复旧问题，而是逼出下一层代价。",
            "场面因此换了重心，旁观者也被拖进选择里。",
            "他没有赢得轻松喘息，只换来一次更危险的主动权。",
            "局势继续收紧，却终于出现可以抓住的缝隙。",
            "这次推进让每个人都看见行动会留下后果。",
            "旧压力被拆开后，真正要命的部分反而更清楚。",
        ]
        decision_clauses = [
            f"{protagonist_name}必须在第{scene_number}轮警戒切换前做出判断。",
            f"{protagonist_name}把可疑字段和旧案时间线重新对齐，胸口的紧意没有散。",
            f"{protagonist_name}选择先保留证据，再承受被追踪的风险。",
            f"{protagonist_name}没有争辩，只把心头的急跳压进能执行的动作。",
            f"{protagonist_name}逼自己记住每一个偏移值。",
            f"{protagonist_name}知道现在撤退只会让真相再次被封存。",
            f"{protagonist_name}把胸口的恐惧压低到不影响手指的程度。",
        ]
        consequence_clauses = [
            "于是证据链多出一枚可以落地的钉子，可下一次阻拦会从哪里突然出现？",
            "而门外的压力也因此提前变得可见，谁会先动手抢走这条线索？",
            "这一步没有解决全部问题，却切开了旧叙事的第一道缝，缝后忽然传来新的动静。",
            "下一次阻拦来临时，他至少知道该质问谁，可答案为什么现在才露头？",
            "沉默多年的记录终于开始指向一个活人，那个名字忽然变得不能再回避。",
            "局势被迫向更危险、也更清楚的方向移动，倒计时还在继续缩短。",
            "读数归零前，他已经把下一枚线索送了出去，可回传信号为什么立刻亮起？",
        ]
        if chapter_number >= 4:
            connectors = [
                "这不是追查动作，而是一次无法撤回的公开选择。",
                "人群的反应立刻改变了权力现场的重量。",
                "每个旁观者都被迫从沉默位置上站出来。",
                "真相第一次不再依赖私人逃亡来保存。",
                "对方还能封锁出口，却封不住已经扩散的声音。",
                "旧案从个人罪名变成所有人必须回应的公共问题。",
                "终局压力因此落在选择谁来承担代价上。",
            ]
            decision_clauses = [
                f"{protagonist_name}选择把证据交给公共频道，而不是继续私藏。",
                f"{protagonist_name}让每一段记录都对应一个还活着的见证人。",
                f"{protagonist_name}不再请求相信，只要求所有人核验。",
                f"{protagonist_name}把逃生时间换成完整播放时间。",
                f"{protagonist_name}用自己的旧权限承担第一轮反噬。",
                f"{protagonist_name}知道这会毁掉退路，但也能毁掉伪证链。",
                f"{protagonist_name}把最后一次选择留给那些曾被迫沉默的人。",
            ]
            consequence_clauses = [
                "于是结局开始从追杀转向清算。",
                "权力结构第一次出现公开裂缝。",
                "被抹掉的人名重新回到航线记录里。",
                "这一步让胜利有了代价，也让代价有了见证。",
                "总库再也不能把事故伪装成技术故障。",
                "他失去安全身份，却换回真相的公共生命。",
                "边境灯塔转向时，旧案终于离开了黑箱。",
            ]
        for index, ((label, sentence), extension) in enumerate(
            zip(ordered, ordered_extensions, strict=True),
            start=1,
        ):
            connector = connectors[(index + scene_number) % len(connectors)]
            pressure = pressure_clauses[(index + scene_number + chapter_number) % len(pressure_clauses)]
            decision = decision_clauses[(index + chapter_number) % len(decision_clauses)]
            consequence = consequence_clauses[(index + chapter_number + scene_number) % len(consequence_clauses)]
            paragraphs.append(
                f"{sentence}"
                f"{pressure}"
                f"{connector}"
                f"{decision}"
                f"{extension}"
                f"{consequence}"
            )
        if prompt_cut_point:
            paragraphs.append(
                f"第{scene_number}场断点落下时，{prompt_cut_point}"
                f"为什么会在这时候出现？"
            )
        elif is_apocalypse_supply:
            paragraphs.append(
                f"第{scene_number}场的城北私仓消息忽然亮起，"
                "为什么目标会改成他们刚守住的仓库？"
            )
        return "\n\n".join(paragraphs)
    return content


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


def _build_messages(request: LLMCompletionRequest, provider: str) -> list[dict[str, Any]]:
    if request.messages_override is not None:
        return list(request.messages_override)
    if request.cache_system and provider == "anthropic":
        system_content: Any = [
            {
                "type": "text",
                "text": request.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system_content = request.system_prompt
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": request.user_prompt},
    ]


def _warn_language_system_mismatch(request: LLMCompletionRequest) -> None:
    language = str(request.metadata.get("language") or request.metadata.get("project_language") or "")
    if language and language.lower().startswith("en"):
        return
    if "You are a" in request.system_prompt:
        logger.warning(
            "English system prompt detected on likely zh path template=%s role=%s",
            request.prompt_template,
            request.logical_role,
        )


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


async def _release_session_before_external_llm_call(session: AsyncSession) -> None:
    """Commit pending DB work before a potentially long provider call.

    Chapter generation can spend 60s+ inside a single LLM request. Holding an
    open DB transaction during that wait makes the next flush vulnerable to a
    stale/closed connection. Pipeline checkpoints already commit between major
    stages; this does the same at the LLM boundary.
    """

    commit = getattr(session, "commit", None)
    if commit is None:
        return
    in_nested_transaction = getattr(session, "in_nested_transaction", None)
    try:
        if callable(in_nested_transaction) and in_nested_transaction():
            return
        await commit()
    except Exception:
        logger.debug(
            "LLM pre-call DB checkpoint failed; continuing with existing session",
            exc_info=True,
        )


async def _persist_llm_run_safely(
    session: AsyncSession,
    llm_run: LlmRunModel,
) -> UUID | None:
    """Persist LLM telemetry without turning a good completion into a failure."""

    try:
        session.add(llm_run)
        await session.flush()
        return llm_run.id
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to persist llm_run telemetry for role=%s model=%s; "
            "continuing without llm_run_id: %s: %s",
            getattr(llm_run, "logical_role", None),
            getattr(llm_run, "model_name", None),
            type(exc).__name__,
            exc,
        )
        rollback = getattr(session, "rollback", None)
        if rollback is not None:
            try:
                await rollback()
            except Exception:
                logger.debug(
                    "Rollback after llm_run telemetry failure also failed",
                    exc_info=True,
                )
        return None


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
    provider = _provider_from_model(role_settings.model)
    messages = _build_messages(request, provider)

    max_tokens = _effective_request_max_tokens(role_settings, request)

    completion_kwargs: dict[str, Any] = {
        "model": role_settings.model,
        "messages": messages,
        "temperature": role_settings.temperature,
        "max_tokens": max_tokens,
        "timeout": role_settings.timeout_seconds,
        "stream": role_settings.stream,
    }
    thinking_type = _effective_thinking_type(role_settings)
    if thinking_type:
        completion_kwargs["extra_body"] = {
            "thinking": {"type": thinking_type}
        }
    if role_settings.reasoning_effort:
        completion_kwargs["reasoning_effort"] = role_settings.reasoning_effort

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

    # Only pass n when >1 AND the model actually supports it. MiniMax 400s on
    # n>1 (model does not support n > 1); Gemini ignores it. Thinking-mode models
    # (e.g. qwen3.x-plus with thinking enabled) 400 with "n must be 1 when
    # enable_thinking is true" — forwarding n there makes EVERY writer call fall
    # back to placeholder content. n_candidates>1 is still honoured at the
    # application layer (drafts.py loops the call and keeps the best-scoring
    # draft), so we simply must not forward the unsupported param.
    _thinking_active = thinking_type is not None and thinking_type not in (
        "disabled",
        "off",
        "none",
    )
    if (
        role_settings.n_candidates > 1
        and not request.tools
        and not _thinking_active
        and _model_supports_n_param(role_settings.model)
    ):
        # n>1 + tools is rarely meaningful and more likely to confuse
        # providers; keep n=1 whenever tools are involved.
        completion_kwargs["n"] = role_settings.n_candidates
    if role_settings.api_base:
        completion_kwargs["api_base"] = role_settings.api_base
        completion_kwargs["base_url"] = role_settings.api_base
    if role_settings.api_key_env:
        api_key = get_runtime_env_value(role_settings.api_key_env)
        if api_key:
            completion_kwargs["api_key"] = api_key
            if role_settings.api_key_header:
                completion_kwargs["extra_headers"] = {
                    str(role_settings.api_key_header): api_key
                }

    # Enforce a hard wall-clock deadline via asyncio.wait_for.  litellm
    # passes ``timeout`` to httpx, but when a shared ``aclient_session`` is
    # installed, httpx may ignore per-request timeouts and use the client
    # default instead — allowing calls to hang far beyond the configured
    # role timeout.  The asyncio deadline guarantees cancellation.
    # A conception-scoped cap (set via bind_conception_model) overrides the
    # role timeout so a stalled provider during the project-less conception
    # phase surfaces well before the no-progress watchdog. Outside conception
    # the contextvar is unset and the role's own timeout applies unchanged.
    _conception_timeout = _conception_call_timeout_var.get()
    _base_timeout = (
        float(_conception_timeout)
        if _conception_timeout
        else float(role_settings.timeout_seconds)
    )
    hard_timeout = _base_timeout + 5.0  # small grace
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
    max_attempts = _max_attempts_for_request(retry_settings, request)
    wait_min = retry_settings.wait_min_seconds
    wait_max = retry_settings.wait_max_seconds

    rl_max_attempts = max(1, retry_settings.rate_limit_max_attempts)
    rl_wait_min = retry_settings.rate_limit_wait_min_seconds
    rl_wait_max = retry_settings.rate_limit_wait_max_seconds

    generic_attempt = 0
    rate_limit_attempt = 0
    active_request = request

    while True:
        try:
            result = await _call_litellm(active_request, role_settings)
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
            if _is_empty_length_response_error(exc) and _should_lower_max_tokens_after_empty_length(active_request):
                current_cap = _effective_request_max_tokens(role_settings, active_request)
                lowered_cap = max(4096, int(current_cap * 0.67))
                if lowered_cap < current_cap:
                    active_request = active_request.model_copy(
                        update={"max_tokens_override": lowered_cap}
                    )
                    logger.warning(
                        "LLM empty length response for template=%s; retrying with "
                        "lower max_tokens %d -> %d",
                        active_request.prompt_template,
                        current_cap,
                        lowered_cap,
                    )
            elif _is_empty_length_response_error(exc):
                logger.warning(
                    "LLM empty length response for prose template=%s; retrying with "
                    "same max_tokens to avoid truncating novel output",
                    active_request.prompt_template,
                )
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


# Per-project model override cache (project_id -> (expiry_monotonic, entry)).
# Lets a book pick a vendor/version from the model catalog without a DB hit on
# every LLM call. Short TTL so a UI model switch takes effect within seconds.
_PROJECT_MODEL_OVERRIDE_CACHE: dict[str, tuple[float, Any]] = {}
_PROJECT_MODEL_OVERRIDE_TTL_SECONDS = 15.0

# Conception runs *before* a DB project row exists, so its LLM calls carry
# ``project_id=None`` and cannot resolve the per-book model from project
# metadata. This contextvar lets a caller (the web conception runner) bind the
# chosen catalog model for the duration of the conception pipeline so those
# calls honour it too — keeping model selection consistent across the whole
# flow (conception → planning → writing). It is consulted only as a last
# resort (no per-call key, no project override), and is reset on scope exit so
# it never leaks into the later planning/writing phases.
_conception_model_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "bestseller_conception_model_catalog_key", default=None
)

# Conception's LLM calls are small structured proposals (facets, market/character
# /world briefs) yet ride the ``planner`` role whose ``timeout_seconds`` (900s) is
# sized for large planning batches. With ``max_attempts=3`` a single *stalled*
# provider connection (the shared httpx client has no read timeout) burns
# 3×905s ≈ 2715s — tripping the 2700s no-progress watchdog before any progress
# event is emitted, killing the book with a useless error. Capping the per-call
# wall-clock during conception keeps the worst-case stall well under the
# watchdog so it recovers via retry (or fails fast with a real error) instead.
_conception_call_timeout_var: contextvars.ContextVar[float | None] = (
    contextvars.ContextVar("bestseller_conception_call_timeout_seconds", default=None)
)


@contextmanager
def bind_conception_model(
    model_catalog_key: str | None,
    *,
    call_timeout_seconds: float | None = None,
):
    """Bind conception-scoped LLM overrides (fallback model + per-call timeout).

    ``model_catalog_key`` gives project-less conception calls the chosen model;
    ``call_timeout_seconds`` caps each call's wall-clock so a stalled provider
    surfaces fast instead of hanging into the watchdog. Both are no-ops when
    falsy. Safe to nest; always resets.
    """
    model_token = _conception_model_var.set(
        (model_catalog_key or "").strip() or None
    )
    timeout_token = _conception_call_timeout_var.set(
        float(call_timeout_seconds) if call_timeout_seconds else None
    )
    try:
        yield
    finally:
        _conception_model_var.reset(model_token)
        _conception_call_timeout_var.reset(timeout_token)


async def _resolve_project_model_override(
    session: AsyncSession, project_id: UUID | None
) -> Any:
    """Return the project's selected, available ModelCatalogEntry (or None)."""
    if project_id is None:
        return None
    from bestseller.services.model_catalog import resolve_project_model_entry

    key = str(project_id)
    now = time.monotonic()
    cached = _PROJECT_MODEL_OVERRIDE_CACHE.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        from sqlalchemy import select

        metadata = await session.scalar(
            select(ProjectModel.metadata_json).where(ProjectModel.id == project_id)
        )
    except Exception:
        logger.debug("model override lookup failed for %s", project_id, exc_info=True)
        metadata = None
    entry = resolve_project_model_entry(metadata if isinstance(metadata, dict) else None)
    _PROJECT_MODEL_OVERRIDE_CACHE[key] = (now + _PROJECT_MODEL_OVERRIDE_TTL_SECONDS, entry)
    return entry


def _apply_model_override(role_settings: LLMRoleSettings, entry: Any) -> LLMRoleSettings:
    update: dict[str, Any] = {
        "model": entry.model,
        "api_base": entry.api_base,
        # Always set the auth scheme from the entry: a model may need a custom
        # header (e.g. MiMo's "api-key") or plain Bearer (None). Carrying over
        # the previous role's header would break auth on a different vendor.
        "api_key_header": getattr(entry, "api_key_header", None),
    }
    if entry.api_key_env:
        update["api_key_env"] = entry.api_key_env
    # Keep model_override consistent so the strong-tier path uses the same model.
    if role_settings.model_override:
        update["model_override"] = entry.model
    return role_settings.model_copy(update=update)


async def complete_text(
    session: AsyncSession,
    settings: AppSettings,
    request: LLMCompletionRequest,
) -> LLMCompletionResult:
    # Defensive: a caller that omits settings (passes ``None``) must NOT
    # silently degrade to the mock provider — that turned real generation
    # into empty mock output for standalone callers (e.g. ai_flavor_loop_gen).
    # Resolve real settings instead; ``mock`` only when explicitly configured.
    if settings is None:
        settings = load_settings()
    settings = apply_runtime_llm_profile(settings)
    role_settings = _get_role_settings(settings, request.logical_role)
    _project_model_entry = await _resolve_project_model_override(session, request.project_id)
    if _project_model_entry is not None:
        role_settings = _apply_model_override(role_settings, _project_model_entry)
    # A per-call model-catalog override (e.g. a capable commercial-judge model) wins
    # over the book's per-project model so judging quality is independent of the
    # writer tier. Falls back silently when the entry is missing/unavailable.
    # When neither a per-call key nor a project override applies (the
    # conception phase, where no project row exists yet), fall back to the
    # conception-bound model so the chosen model is used across the whole flow.
    _effective_catalog_key = request.model_catalog_key
    if _effective_catalog_key is None and request.project_id is None:
        _effective_catalog_key = _conception_model_var.get()
    if _effective_catalog_key:
        from bestseller.services.model_catalog import get_model_catalog_entry

        _override_entry = get_model_catalog_entry(_effective_catalog_key)
        if _override_entry is not None and _override_entry.available:
            role_settings = _apply_model_override(role_settings, _override_entry)
    _warn_language_system_mismatch(request)
    _validate_prompt_template_name(request)
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
    if request.cache_system:
        metadata["cache_system"] = True
    if request.max_tokens_override is not None:
        metadata["max_tokens_override"] = int(request.max_tokens_override)
    latency_ms: int | None = None
    provider = "mock"
    model_name = f"mock-{request.logical_role}"
    content = _mock_content_for_request(request)
    input_tokens = _estimate_tokens(request.system_prompt) + _estimate_tokens(request.user_prompt)
    output_tokens = _estimate_tokens(content)
    finish_reason = "mock"

    tool_calls: list[dict[str, Any]] | None = None
    raw_message: dict[str, Any] | None = None
    started_at = perf_counter()
    if not settings.llm.mock:
        try:
            await _release_session_before_external_llm_call(session)
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
    llm_run_id = await _persist_llm_run_safely(session, llm_run)

    return LLMCompletionResult(
        content=content,
        provider=provider,
        model_name=model_name,
        llm_run_id=llm_run_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        raw_message=raw_message,
    )
