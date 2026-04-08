from __future__ import annotations

import hashlib
import importlib
import logging
from time import perf_counter
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import LlmRunModel
from bestseller.settings import AppSettings, LLMRoleSettings, get_runtime_env_value


logger = logging.getLogger(__name__)


LLMRole = Literal["planner", "writer", "critic", "summarizer", "editor"]


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
_shared_litellm_async_client: Any = None  # httpx.AsyncClient when initialized


def _ensure_shared_litellm_http_client() -> None:
    """Install a process-wide ``httpx.AsyncClient`` into litellm.

    Idempotent: subsequent calls are no-ops once the shared client is set.
    Failures are logged and swallowed — litellm falls back to its built-in
    per-call client.
    """
    global _shared_litellm_async_client
    if _shared_litellm_async_client is not None:
        return
    try:
        import httpx

        litellm = importlib.import_module("litellm")
        # If something else (tests, an integration) already set the session,
        # do not overwrite it.
        if getattr(litellm, "aclient_session", None) is not None:
            _shared_litellm_async_client = litellm.aclient_session
            return
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=300.0,
            ),
            follow_redirects=True,
        )
        litellm.aclient_session = client
        _shared_litellm_async_client = client
        logger.info(
            "Installed shared httpx.AsyncClient into litellm.aclient_session "
            "(keepalive=10, timeout=180s)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to install shared litellm http client; falling back to per-call default: %s",
            exc,
        )


class LLMCompletionRequest(BaseModel):
    logical_role: LLMRole
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


def _extract_text_content(raw_content: Any) -> str:
    if isinstance(raw_content, str):
        return raw_content
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
        return "\n".join(part for part in parts if part)
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
    litellm = importlib.import_module("litellm")
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
        "n": role_settings.n_candidates,
    }
    if role_settings.api_base:
        completion_kwargs["api_base"] = role_settings.api_base
    if role_settings.api_key_env:
        api_key = get_runtime_env_value(role_settings.api_key_env)
        if api_key:
            completion_kwargs["api_key"] = api_key

    response = await acompletion(
        **completion_kwargs,
    )

    if role_settings.stream:
        return await _collect_streaming_content(response)

    choice = response.choices[0]
    content = _extract_text_content(choice.message.content)
    input_tokens, output_tokens = _extract_usage_fields(getattr(response, "usage", None))
    finish_reason = getattr(choice, "finish_reason", None)
    if not content.strip():
        raise ValueError("LLM response content is empty.")
    return content.strip(), input_tokens, output_tokens, finish_reason


async def complete_text(
    session: AsyncSession,
    settings: AppSettings,
    request: LLMCompletionRequest,
) -> LLMCompletionResult:
    role_settings = _get_role_settings(settings, request.logical_role)
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
            ) = await _call_litellm(request, role_settings)
        except Exception as exc:
            provider = "fallback"
            model_name = f"fallback-{request.logical_role}"
            metadata["configured_model"] = role_settings.model
            metadata["fallback_reason"] = f"{type(exc).__name__}: {exc}"
            finish_reason = "fallback"
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
