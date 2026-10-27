"""Conservative formal M31 accounting bound, independent of token estimation.

Official OpenAI API model table specifies a 1,000,000 context window. Reserve
input at that full ceiling and output at the exact max_completion_tokens
(including reasoning), at standard undiscounted CNY prices. Actual usage
releases the unused reservation; local token estimates do not price the bound.
No priority/server tools/multimodal route is admitted under this contract.
"""

import hashlib
import json

CONTRACT = {
    "schema": "minimax-m31-standard-contract-bound.v1",
    "model": "openai/MiniMax-M3.1-Flash",
    "context_window": 1_000_000,
    "input_ceiling": 1_000_000,
    "maximum_supported_output": 524288,
    "source_output": "https://platform.minimax.cn/docs/api-reference/text-chat-openai",
    "input_micro_cny_per_token": 1,
    "output_micro_cny_per_token": 4,
    "source_api": "https://platform.minimax.cn/docs/api-reference/text-openai-api",
    "source_price": "https://platform.minimax.cn/docs/pricing/overview",
    "checked_date": "2026-10-20",
    "priority": False,
    "server_tools": False,
}
CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(CONTRACT, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def input_bound(payload):
    if (
        payload.get("model") != CONTRACT["model"]
        or payload.get("n_candidates") != 1
        or not 0 < payload.get("max_tokens", 0) <= CONTRACT["maximum_supported_output"]
        or any(tool.get("type") != "function" for tool in payload.get("tools") or [])
        or any(
            not (
                isinstance(v.get("content"), str)
                or (
                    v.get("role") == "assistant"
                    and v.get("content") is None
                    and isinstance(v.get("tool_calls"), list)
                    and v["tool_calls"]
                    and all(
                        t.get("type") == "function"
                        and isinstance(t.get("function", {}).get("arguments"), str)
                        for t in v["tool_calls"]
                    )
                )
            )
            for v in payload["messages"]
        )
    ):
        raise ValueError("finite_provider_contract_not_applicable")
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema": "verified-input-upper-bound.v1",
        "model": CONTRACT["model"],
        "input_hash": digest,
        "input_tokens_upper": CONTRACT["input_ceiling"],
        "output_tokens_upper": payload["max_tokens"],
        "counter_sha256": CONTRACT_SHA256,
        "method": "published-provider-context-ceiling-double-sided",
    }
