"""Durable, reason-bearing retries without changing prior accounting or steps."""

import json
import os
from pathlib import Path
import tempfile

from tools.runtime_support import ProjectError, QuotaExhausted


class StepFormatError(ProjectError):
    """A model response remained unusable after the stage's format attempts."""

    error_kind = "format"


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("返回为空或不是文本")
    for index, char in enumerate(raw):
        if char in "[{":
            try:
                return json.JSONDecoder().raw_decode(raw[index:])[0]
            except ValueError:
                pass
    raise ValueError("无有效JSON")


def _history(folder, name, request):
    """Import legacy attempts without modifying the HTTP ledger, even Unknown."""
    path = folder / "attempts" / (name + ".json")
    state = read(path)
    if state is not None:
        if state["request"] != request:
            raise ProjectError("步骤输入变化，保留原记录；请使用新批次目录:" + name)
        return path, state
    prior = [row for row in read(folder / "http/ledger.json", []) if row["stage"] == name]
    attempts = []
    if prior:
        latest = prior[-1]
        old = read(folder / "http" / latest["request"], {}) if latest.get("request") else {}
        if old and any(old.get(key) != value for key, value in request.items()):
            raise ProjectError("步骤输入变化，保留原记录；请使用新批次目录:" + name)
        if latest.get("status") != "ok":
            attempts.append(dict(stage=name, status="unknown", reason="上次请求结果未知或失败，费用记录保留"))
    state = dict(request=request, attempts=attempts)
    save(path, state)
    return path, state


def step(folder, name, prompt, client, tokens, validate=None):
    """Replay completed steps; allow six new format attempts per explicit resume."""
    folder = Path(folder)
    target = folder / "steps" / (name + ".json")
    if target.exists():
        try:
            result = read(target)
            if validate is not None:
                validate(result)
        except (ValueError, KeyError, TypeError) as exc:
            # Older versions saved responses before validating their structure.
            # Keep those files intact; the repair has its own resumable checkpoint.
            repair_prompt = prompt + "\n上一次失败原因：旧步骤返回无效（" + str(exc) + "），请返回完整结果。"
            return step(folder, name + "-repair", repair_prompt, client, tokens, validate)
        return result
    request = dict(
        messages=[dict(role="user", content=prompt)],
        max_completion_tokens=tokens,
        model=getattr(client, "model", "MiniMax-M3.1-Flash"),
    )
    path, state = _history(folder, name, request)
    attempts = state["attempts"]
    for _ in range(6):
        previous = attempts[-1] if attempts else None
        replay = previous is not None and previous["status"] == "started"
        if replay:
            rows = read(folder / "http/ledger.json", [])
            latest = next((row for row in reversed(rows) if row["stage"] == previous["stage"]), {})
            replay = latest.get("status") == "ok"
            if not replay:
                previous.update(status="unknown", reason="上次请求结果未知或失败，费用记录保留")
        if replay:
            attempt = previous
        else:
            number = len(attempts)
            attempt = dict(
                stage=name if number == 0 else f"{name}-retry-{number}",
                status="started",
                messages=[dict(role="user", content=prompt)],
            )
            if previous:
                attempt["messages"].append(
                    dict(role="user", content="上一次失败原因：" + previous["reason"] + "。请修正后返回完整结果。")
                )
            attempts.append(attempt)
        save(path, state)
        # Network/account failures belong to the transport/runtime boundary, not a judge fallback.
        try:
            raw = client(attempt["messages"], stage=attempt["stage"], max_tokens=tokens)
        except Exception as exc:
            reason = (
                "账户额度用尽" if isinstance(exc, QuotaExhausted) else "客户端请求未完成（" + type(exc).__name__ + "）"
            )
            attempt.update(status="client_error", reason=reason)
            save(path, state)
            if isinstance(exc, ProjectError) and getattr(exc, "error_kind", "") == "format":
                raise StepFormatError("客户端已耗尽格式重试：" + name) from exc
            raise
        try:
            result = parse(raw)
            if validate is not None:
                validate(result)
        except (ValueError, KeyError, TypeError) as exc:
            # Validation errors are our own bounded messages, never raw provider responses.
            attempt.update(status="invalid", reason=str(exc))
            save(path, state)
            continue
        save(target, result)
        attempt.update(status="complete")
        save(path, state)
        return result
    raise StepFormatError(f"{name}: 6次坏返回后仍失败：{attempts[-1]['reason']}")
