"""Tool-use runtime for iterative LLM + function-calling loops.

This module wraps :func:`bestseller.services.llm.complete_text` with a
multi-round loop that parses ``tool_calls`` from the model's response,
dispatches them to registered handlers, and feeds the results back to the
model until it either:

* emits a plain-text answer (no ``tool_calls``),
* calls a terminal tool registered with ``is_terminal=True``,
* reaches ``max_rounds``,
* or signals a non-recoverable error.

Only this module knows about the multi-turn message array — callers build
a :class:`ToolRegistry`, craft the initial system + user prompts, and
hand the rest off.  The loop is deliberately transport-agnostic: the
registered handlers receive plain ``dict`` arguments and return plain
``dict`` results.  They may be backed by an HTTP search client, a
pgvector query, an MCP server, or any other source.

Design notes
------------

* **Strict JSON.** Tool arguments arrive as JSON strings from the
  provider; we attempt ``json.loads`` with a graceful fallback to an
  empty dict so a single malformed call doesn't kill the whole loop.
* **Parallel dispatch.** Every ``tool_calls`` batch is dispatched via
  :func:`asyncio.gather`, which mirrors how OpenAI / Anthropic / MiniMax
  emit multiple calls in one turn.
* **Error transparency.** Handler exceptions do NOT propagate up the
  loop — they are serialised back to the model as the tool result
  payload ``{"error": "..."}``, so the model can recover.  Unrecoverable
  runtime errors (e.g. LLM itself down) DO propagate.
* **Telemetry.** Each round's request/response + tool_call dispatches
  are recorded in :attr:`ToolLoopResult.trace` for post-mortem analysis.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    complete_text,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


# ── Tool specification ─────────────────────────────────────────────────


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolSpec(BaseModel):
    """Describes a single callable tool exposed to the model.

    Attributes
    ----------
    name:
        The function name the model sees (must match OpenAI function
        naming rules: ``^[a-zA-Z0-9_-]{1,64}$``).
    description:
        Free-form description the model uses to decide when to call
        this tool.  Keep it one or two sentences.
    parameters:
        A full JSON-Schema object describing the function's arguments.
    handler:
        Async callable that receives the parsed argument ``dict`` and
        returns a JSON-serialisable ``dict``.
    is_terminal:
        If ``True``, calling this tool ends the loop after its result
        is returned.  Use this for ``finalize_*`` style tools that
        emit the final output.
    """

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    handler: ToolHandler
    is_terminal: bool = False

    model_config = {"arbitrary_types_allowed": True}

    def to_openai_schema(self) -> dict[str, Any]:
        """Render the OpenAI ``tools`` array entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


class ToolRegistry:
    """Collects :class:`ToolSpec` instances for a single loop invocation.

    The registry is built up-front by the caller; it cannot be mutated
    mid-loop.  This is a deliberate safety constraint: once the model has
    been told a tool exists, removing it mid-turn would confuse it.
    """

    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool {spec.name!r} already registered.")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [spec.to_openai_schema() for spec in self._specs.values()]

    def is_empty(self) -> bool:
        return not self._specs

    def names(self) -> list[str]:
        return list(self._specs.keys())


# ── Loop result ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolCallRecord:
    round_index: int
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    error: str | None = None


@dataclass(frozen=True)
class ToolLoopResult:
    """Aggregate outcome of :func:`run_tool_loop`.

    Attributes
    ----------
    final_content:
        The model's final plain-text content, if it terminated without
        calling a tool.  ``""`` when the loop ended via a terminal tool.
    final_tool_results:
        Dict of ``{tool_name: last_result}`` for every tool that was
        actually called; useful when a terminal tool emits the final
        artefact.
    rounds:
        Number of round-trips to the LLM (1 = single call).
    exit_reason:
        ``"text"`` | ``"terminal_tool"`` | ``"max_rounds"`` | ``"error"``.
    trace:
        Full ordered list of tool calls dispatched during the loop.
    last_completion:
        The final :class:`LLMCompletionResult` (for token accounting).
    """

    final_content: str
    final_tool_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    rounds: int = 0
    exit_reason: str = "text"
    trace: list[ToolCallRecord] = field(default_factory=list)
    last_completion: LLMCompletionResult | None = None


# ── The loop ───────────────────────────────────────────────────────────


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Turn the provider's ``arguments`` blob into a dict, defensively."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Tool arguments JSON decode failed: %s; raw=%r", exc, raw[:200])
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


async def _dispatch_single_call(
    registry: ToolRegistry,
    call: dict[str, Any],
    round_index: int,
) -> tuple[dict[str, Any], ToolCallRecord]:
    """Dispatch one tool call; return the tool-response message + trace."""
    fn = call.get("function") or {}
    name = fn.get("name", "")
    arguments_raw = fn.get("arguments", "")
    arguments = _parse_tool_arguments(arguments_raw)
    call_id = call.get("id") or ""

    spec = registry.get(name)
    if spec is None:
        error_payload = {"error": f"unknown_tool:{name}"}
        record = ToolCallRecord(
            round_index=round_index,
            tool_name=name,
            arguments=arguments,
            result=error_payload,
            error="unknown_tool",
        )
        message = {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": json.dumps(error_payload, ensure_ascii=False),
        }
        return message, record

    try:
        result = await spec.handler(arguments)
        if not isinstance(result, dict):
            result = {"value": result}
        record = ToolCallRecord(
            round_index=round_index,
            tool_name=name,
            arguments=arguments,
            result=result,
        )
    except Exception as exc:  # noqa: BLE001  — transparently surface to model
        logger.exception("Tool handler %s raised", name)
        result = {"error": f"{type(exc).__name__}: {exc}"}
        record = ToolCallRecord(
            round_index=round_index,
            tool_name=name,
            arguments=arguments,
            result=result,
            error=str(exc),
        )

    message = {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": json.dumps(result, ensure_ascii=False),
    }
    return message, record


async def run_tool_loop(
    session: AsyncSession,
    settings: AppSettings,
    *,
    base_request: LLMCompletionRequest,
    registry: ToolRegistry,
    max_rounds: int = 5,
    tool_choice: str | dict[str, Any] | None = "auto",
) -> ToolLoopResult:
    """Drive a multi-round tool-use conversation until the model settles.

    Parameters
    ----------
    session:
        Active async DB session (used by :func:`complete_text` to persist
        ``llm_runs`` rows).
    settings:
        Global :class:`AppSettings`.
    base_request:
        A :class:`LLMCompletionRequest` with at least ``system_prompt``
        and ``user_prompt`` set.  ``tools``, ``tool_choice``, and
        ``messages_override`` should be left at their defaults — this
        function manages them.
    registry:
        The tool set available to the model.
    max_rounds:
        Hard cap on LLM round-trips.  Exceeding this ends the loop with
        ``exit_reason="max_rounds"``.
    tool_choice:
        ``"auto"`` (default), ``"none"``, or a specific tool dict.
        Passed straight through to the model each round.

    Returns
    -------
    ToolLoopResult
        See that class for field semantics.
    """
    if registry.is_empty():
        raise ValueError("run_tool_loop requires at least one registered tool.")
    if max_rounds < 1:
        raise ValueError("max_rounds must be >= 1.")

    tools_schema = registry.openai_schemas()

    # Bootstrap messages array with the initial system + user turn.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": base_request.system_prompt},
        {"role": "user", "content": base_request.user_prompt},
    ]

    final_tool_results: dict[str, dict[str, Any]] = {}
    trace: list[ToolCallRecord] = []
    last_completion: LLMCompletionResult | None = None

    for round_index in range(1, max_rounds + 1):
        per_round_request = base_request.model_copy(
            update={
                "messages_override": list(messages),
                "tools": tools_schema,
                "tool_choice": tool_choice,
            }
        )
        completion = await complete_text(session, settings, per_round_request)
        last_completion = completion

        tool_calls = completion.tool_calls
        if not tool_calls:
            # Model produced plain text — we're done.
            return ToolLoopResult(
                final_content=completion.content,
                final_tool_results=final_tool_results,
                rounds=round_index,
                exit_reason="text",
                trace=trace,
                last_completion=completion,
            )

        # Append the assistant's tool_call turn to the transcript.
        if completion.raw_message is not None:
            messages.append(completion.raw_message)
        else:
            messages.append(
                {
                    "role": "assistant",
                    "content": completion.content or None,
                    "tool_calls": tool_calls,
                }
            )

        # Dispatch every call this round in parallel, then append the
        # resulting tool messages in the SAME ORDER the model emitted
        # them (some providers care about ordering).
        dispatched = await asyncio.gather(
            *(
                _dispatch_single_call(registry, call, round_index)
                for call in tool_calls
            )
        )

        terminated_via_tool = False
        for (tool_message, record), call in zip(dispatched, tool_calls):
            messages.append(tool_message)
            trace.append(record)
            if record.error is None:
                final_tool_results[record.tool_name] = record.result
                spec = registry.get(record.tool_name)
                if spec is not None and spec.is_terminal:
                    terminated_via_tool = True

        if terminated_via_tool:
            return ToolLoopResult(
                final_content="",
                final_tool_results=final_tool_results,
                rounds=round_index,
                exit_reason="terminal_tool",
                trace=trace,
                last_completion=completion,
            )

    logger.warning(
        "run_tool_loop hit max_rounds=%d without settling (tools=%s)",
        max_rounds,
        registry.names(),
    )
    return ToolLoopResult(
        final_content=last_completion.content if last_completion else "",
        final_tool_results=final_tool_results,
        rounds=max_rounds,
        exit_reason="max_rounds",
        trace=trace,
        last_completion=last_completion,
    )


__all__ = [
    "ToolHandler",
    "ToolSpec",
    "ToolRegistry",
    "ToolCallRecord",
    "ToolLoopResult",
    "run_tool_loop",
]
