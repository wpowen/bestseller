"""L3 真机验证：死模型条目必须显式报不可用，而非静默 fallback。

背景（2026-08-08）：`nim-deepseek-v4-pro`（上游 2026-08-07 EOL，410 Gone）与
`nim-kimi-k2.6`（404 Function Not Found）曾被 `get_model_catalog_entry` 判为
available=True（只查 api_key_env），任何用这两个 key 的调用重试 3 次后静默用
fallback_response，且把共享 circuit breaker 打开 60s 殃及健康模型。

本脚本验证修复后的四段行为：
  A. catalog 层：两个退役条目 available=False 且带「已下线」原因；同 key 的
     活条目不受影响。
  B. 解析层：resolve_project_model_entry 对退役选择返回 None（并告警一次）。
  C. 调用层（mock，零 token）：complete_text 带死 key 时在 llm_run metadata
     里落 model_catalog_override_key / model_catalog_override_unavailable。
  D. LIVE 探测（需 NVIDIA_API_KEY；无 key 自动跳过）：真打死端点，断言
     fail-fast（仅 1 次尝试）、共享 breaker 保持 closed、运行时死模型登记表
     记入原因、catalog 可用性随之翻转为「上游探测不可用」。

零 token：D 段只会命中错误响应，不产生计费补全；A–C 全程 mock/无网络。
零副作用：不建 DB 会话；llm_run 仅落在进程内的假 session 上。

用法：
    NVIDIA_API_KEY=... .venv/bin/python scripts/verify_model_catalog_dead_entries.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


DEAD_ENTRIES = ("nim-deepseek-v4-pro", "nim-kimi-k2.6")


def section_a_catalog() -> None:
    print("\n[A] catalog 可用性")
    os.environ.setdefault("NVIDIA_API_KEY", "placeholder-for-availability-check")
    from bestseller.services import model_catalog as mc

    by_id = {e.id: e for e in mc.load_model_catalog()}
    for dead in DEAD_ENTRIES:
        entry = by_id.get(dead)
        check(
            f"{dead} available=False",
            entry is not None and entry.available is False,
        )
        check(
            f"{dead} 带「已下线」原因",
            entry is not None and "已下线" in (entry.unavailable_reason or ""),
            (entry.unavailable_reason or "") if entry else "entry missing",
        )
    live = by_id.get("nim-mistral-large-3")
    check(
        "同 key 活条目 nim-mistral-large-3 不受影响",
        live is not None and live.available is True,
    )


def section_b_resolution() -> None:
    print("\n[B] 项目选择解析")
    from bestseller.services import model_catalog as mc

    for dead in DEAD_ENTRIES:
        resolved = mc.resolve_project_model_entry({"llm_model_id": dead})
        check(f"resolve({dead}) → None（回落默认模型）", resolved is None)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    def in_nested_transaction(self) -> bool:
        return False


def section_c_complete_text_metadata() -> None:
    print("\n[C] complete_text metadata 上报（mock，零 token）")
    from bestseller.infra.db.models import LlmRunModel
    from bestseller.services.llm import LLMCompletionRequest, complete_text
    from bestseller.settings import load_settings

    async def _run(dead_key: str) -> LlmRunModel:
        session = _FakeSession()
        settings = load_settings(env={"BESTSELLER__LLM__MOCK": "true"})
        await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                model_catalog_key=dead_key,
                system_prompt="probe",
                user_prompt="probe",
                fallback_response="fb",
            ),
        )
        return next(o for o in session.added if isinstance(o, LlmRunModel))

    for dead in DEAD_ENTRIES:
        run = asyncio.run(_run(dead))
        meta = run.metadata_json or {}
        check(
            f"{dead} → metadata.model_catalog_override_key",
            meta.get("model_catalog_override_key") == dead,
        )
        check(
            f"{dead} → metadata 带不可用原因",
            "已下线" in str(meta.get("model_catalog_override_unavailable", "")),
            str(meta.get("model_catalog_override_unavailable", "")),
        )


def section_d_live_probe() -> None:
    print("\n[D] LIVE 探测死端点（fail-fast + breaker 不受污染）")
    key = os.environ.get("NVIDIA_API_KEY", "")
    if not key or key == "placeholder-for-availability-check":
        print("  SKIP  未提供真实 NVIDIA_API_KEY，跳过 live 探测")
        return

    from bestseller.services import llm as llm_mod
    from bestseller.services import model_catalog as mc
    from bestseller.services.llm import (
        LLMCompletionRequest,
        _call_litellm_with_retry,
        _llm_breaker,
    )
    from bestseller.settings import LLMRoleSettings, RetrySettings

    dead_models = {
        "nim-deepseek-v4-pro": "openai/deepseek-ai/deepseek-v4-pro",
        "nim-kimi-k2.6": "openai/moonshotai/kimi-k2.6",
    }
    retry = RetrySettings(max_attempts=3, wait_min_seconds=1, wait_max_seconds=2)

    orig_call = llm_mod._call_litellm
    for catalog_id, model in dead_models.items():
        mc.clear_runtime_dead_models()
        _llm_breaker.reset()
        calls = {"n": 0}

        async def counting(request, role_settings, _orig=orig_call, _calls=calls):
            _calls["n"] += 1
            return await _orig(request, role_settings)

        llm_mod._call_litellm = counting
        try:
            role_settings = LLMRoleSettings(
                model=model,
                temperature=0.2,
                max_tokens=16,
                timeout_seconds=30,
                api_base="https://integrate.api.nvidia.com/v1",
                api_key_env="NVIDIA_API_KEY",
                stream=False,
            )
            request = LLMCompletionRequest(
                logical_role="critic",
                system_prompt="ping",
                user_prompt="ping",
                fallback_response="fb",
            )
            try:
                asyncio.run(_call_litellm_with_retry(request, role_settings, retry))
                check(f"{catalog_id} 调用应失败", False, "调用竟然成功了——上游复活？")
                continue
            except Exception as exc:  # noqa: BLE001 — 预期路径
                exc_desc = f"{type(exc).__name__}: {str(exc)[:120]}"
            check(
                f"{catalog_id} fail-fast（1 次尝试，无重试）",
                calls["n"] == 1,
                f"attempts={calls['n']} · {exc_desc}",
            )
            check(
                f"{catalog_id} 共享 breaker 保持 closed",
                _llm_breaker.state == "closed"
                and _llm_breaker._consecutive_failures == 0,
                f"state={_llm_breaker.state}",
            )
            dead_reason = mc.runtime_dead_reason(model)
            check(
                f"{catalog_id} 运行时死模型登记表已记入",
                bool(dead_reason),
                str(dead_reason)[:120],
            )
            entry = next(
                (e for e in mc.load_model_catalog() if e.id == catalog_id), None
            )
            # 该条目已 retired，理由以「已下线」优先；若未 retired 应显示探测原因。
            check(
                f"{catalog_id} catalog 层 available=False",
                entry is not None and entry.available is False,
                (entry.unavailable_reason or "") if entry else "entry missing",
            )
        finally:
            llm_mod._call_litellm = orig_call
    mc.clear_runtime_dead_models()
    _llm_breaker.reset()


def main() -> int:
    section_a_catalog()
    section_b_resolution()
    section_c_complete_text_metadata()
    section_d_live_probe()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed ==")
    if failed:
        print("FAILED:")
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
