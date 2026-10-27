"""Pure, source-bound deadlines for complete native planning reviews.

The caller still enforces the original absolute execution deadline. This
resolver changes neither a role nor an effective provider input or retry count.
"""

from __future__ import annotations

import math

from bestseller.services import minimax_m31_protocol as m31
from bestseller.services import source_complete_retention as retention
from bestseller.services.llm import LLMCompletionRequest
from bestseller.settings import AppSettings, LLMRoleSettings

SCHEMA = "planning-native-deadline.v1"
_VERSIONS = frozenset({"v4-literal-full-source.v1", "v5-literal-full-source.v1"})


def _need(condition: object, code: str) -> None:
    if not condition:
        raise ValueError("planning_native_deadline:" + code)


def _seconds(value: object, code: str) -> int | float:
    _need(type(value) in (int, float) and math.isfinite(value) and value > 0, code)
    return value


def validate_native_deadline_policy(value: object) -> dict:
    """Closed credential-free execution evidence, independent of later settings."""
    _need(type(value) is dict and set(value) == {
        "schema", "applies", "sdk_timeout_seconds", "hard_timeout_seconds",
        "configured_role_timeout_seconds"}, "policy_shape")
    _need(value["schema"] == SCHEMA and value["applies"] is True, "policy_identity")
    sdk = _seconds(value["sdk_timeout_seconds"], "policy_sdk_timeout")
    hard = _seconds(value["hard_timeout_seconds"], "policy_hard_timeout")
    _seconds(value["configured_role_timeout_seconds"], "policy_configured_timeout")
    _need(hard == sdk + 5.0, "policy_hard_timeout")
    return dict(value)


def _verify_source(
    request: LLMCompletionRequest, role: LLMRoleSettings, settings: AppSettings | None
) -> None:
    _need(isinstance(settings, AppSettings), "runtime_settings_required")
    _need(settings.artifact_store.retain_prompt_payloads is True, "retention_disabled")
    _need(settings.artifact_store.mode == "local", "local_store_required")
    _need(
        len(request.tools) == 1
        and request.tools[0].get("type") == "function"
        and isinstance(request.tools[0].get("function"), dict)
        and request.tools[0]["function"].get("name") == "submit_planning_admission"
        and request.tool_choice
        == {"type": "function", "function": {"name": "submit_planning_admission"}},
        "native_tool_contract",
    )
    receipt = request.metadata.get(retention.METADATA_KEY)
    _need(type(receipt) is dict and receipt.get("schema") == retention.SCHEMA, "source_contract")
    _need(
        len(request.system_prompt) + len(request.user_prompt) <= retention.MAX_WIRE_CHARACTERS,
        "source_character_limit",
    )
    # Reuse the complete codec/output/scope/effective-input proof, without
    # introducing an ambient audit or changing current finite budget state.
    _, _, source = retention._packet(
        request, receipt.get("packet_start"), receipt.get("packet_end")
    )
    expected = retention.source_complete_retention_metadata(
        request,
        role=role,
        logical_source=source,
        packet_start=receipt["packet_start"],
        packet_end=receipt["packet_end"],
    )
    _need(retention._json(receipt) == retention._json(expected), "source_contract_changed")
    payload = retention.llm._effective_input_payload(request, retention._role(request, role))
    wire = {key: payload[key] for key in ("messages", "tools", "tool_choice")}
    _need(len(retention._json(wire)) <= retention.MAX_WIRE_CHARACTERS, "wire_character_limit")


def resolve_native_deadline(
    request: LLMCompletionRequest,
    role: LLMRoleSettings,
    settings: AppSettings | None,
    *,
    conception_timeout: int | float | None = None,
) -> dict:
    """Resolve SDK and wall-clock limits, retaining legacy routes byte for byte.

    Only the exact M3.1 literal critic route with a rebuilt full-source receipt
    uses the configured planner lifetime. An explicit conception cap can shorten
    that lifetime. The original ledger's absolute deadline remains an outer bound.
    """
    configured = _seconds(role.timeout_seconds, "role_timeout_invalid")
    sdk = configured
    hard_base = float(conception_timeout) if conception_timeout else float(configured)
    eligible = (
        request.logical_role == "critic"
        and request.prompt_template == "planning_draft_admission"
        and request.prompt_version in _VERSIONS
        and m31.applies(role.model)
        and bool(request.tools)
    )
    if eligible:
        _verify_source(request, role, settings)
        planner = _seconds(settings.llm.planner.timeout_seconds, "planner_timeout_invalid")
        sdk = max(configured, planner)
        if conception_timeout is not None:
            sdk = min(sdk, _seconds(conception_timeout, "conception_timeout_invalid"))
        hard_base = float(sdk)
    return {
        "schema": SCHEMA,
        "applies": eligible,
        "sdk_timeout_seconds": sdk,
        "hard_timeout_seconds": hard_base + 5.0,
        "configured_role_timeout_seconds": configured,
    }
