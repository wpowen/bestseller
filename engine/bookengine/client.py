"""Independent MiniMax HTTP client and stage checkpoints, adapted from mini.py."""

import json
import os
import datetime as dt
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
import re
import socket
import time
import urllib.error
import urllib.request

from .storage import load, now, save
from .text import parse_json
from .retries import next_network_wait
from tools.runtime_support import QuotaExhausted, ProjectError, classify_api_error, extract_api_error

MODEL = "MiniMax-M3.1-Flash"
SYSTEM = (
    "你是一位中文小说作者和编辑。直接交付当前任务，不解释工作过程。故事服从用户题材。只在要求时输出JSON，必须完整。"
)


def credentials(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("\"'")
    name = values.get("BESTSELLER__LLM__WRITER__API_KEY_ENV", "MINIMAX_API_KEY")
    key = values.get(name)
    base = values.get("BESTSELLER__LLM__WRITER__API_BASE", "https://api.minimaxi.com/v1")
    if not key or not base.startswith("https://"):
        raise ValueError("Missing API key or HTTPS endpoint")
    return base.rstrip("/"), key


def content_of(result):
    choices = result.get("choices") or [{}]
    text = choices[0].get("message", {}).get("content") or ""
    if isinstance(text, list):
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def write_runtime(out, kind, stage="", **fields):
    save(Path(out) / "runtime.json", dict(kind=kind, stage=stage, updated=time.time(), **fields))


class Retryable(ValueError):
    def __init__(self, reason, delay=0):
        super().__init__(reason)
        self.delay = delay


class NetworkRetry(Retryable):
    pass


def retry_after(value):
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - dt.datetime.now(dt.timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


class Client:
    def __init__(self, out, env, prefix, timeout=180, model=MODEL):
        self.out, self.prefix, self.timeout = Path(out), prefix, timeout
        self.model = model
        self.context_limit = None
        self.clip_context = False
        self.base, self._key = credentials(env)
        self.history_path = self.out / "calls.json"
        self.calls = load(self.history_path) if self.history_path.exists() else []
        for entry in self.calls:
            if entry["status"] == "pending":
                entry["status"] = "interrupted"
        save(self.history_path, self.calls)

    def totals(self):
        totals = dict(
            calls=len(self.calls),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            missing_usage_calls=0,
            retries=0,
        )
        for entry in self.calls:
            usage = entry.get("usage") or {}
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                totals[key] += usage.get(key, 0)
            totals["missing_usage_calls"] += not bool(usage)
            totals["retries"] += entry["attempt"] > 1
        return totals

    def request(self, stage, prompt, cap, effort, attempt):
        if self.context_limit and len(SYSTEM) + len(prompt) > self.context_limit:
            raise RuntimeError("Context exceeds configured character limit: " + stage)
        entry = dict(
            seq=len(self.calls) + 1,
            stage=stage,
            attempt=attempt,
            model=self.model,
            input_chars=len(SYSTEM) + len(prompt),
            started=now(),
            status="pending",
            effort=effort,
        )
        self.calls.append(entry)
        save(self.history_path, self.calls)
        payload = dict(
            model=self.model,
            messages=[dict(role="system", content=SYSTEM), dict(role="user", content=prompt)],
            max_completion_tokens=cap,
            reasoning_effort=effort,
            stream=False,
        )
        if getattr(self, "thinking", None) is not None:
            payload["thinking"] = self.thinking
        body = json.dumps(payload, ensure_ascii=False).encode()
        rate_path = os.environ.get("BOOKENGINE_RATE_FILE")
        if rate_path:
            from .rate import acquire

            acquire(rate_path, len(body), cap)
        entry.update(input_bytes=len(body), output_token_cap=cap)
        save(self.history_path, self.calls)
        request = urllib.request.Request(
            self.base + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._key},
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
            entry["usage"] = result.get("usage") or {}
            entry["model"] = result.get("model")
            entry["finish_reason"] = (result.get("choices") or [{}])[0].get("finish_reason")
            save(self.out / "responses" / f"{entry['seq']:03d}.json", result)
            code, message = extract_api_error(result)
            if code not in (None, 0) or result.get("error"):
                message = message.replace(self._key, "[redacted]")
                entry.update(provider_code=code, provider_message=message)
                kind = classify_api_error(code, message)
                if kind == "quota":
                    entry["status"] = "quota_exhausted"
                    raise QuotaExhausted(f"{stage}: MiniMax {code}: {message}")
                if kind == "waiting":
                    entry["status"] = "provider_wait"
                    raise NetworkRetry(f"MiniMax {code}: {message}")
                raise ProjectError(f"{stage}: MiniMax {code}: {message}")
            if not content_of(result):
                raise Retryable("empty_return")
            if entry["finish_reason"] == "length":
                raise Retryable("truncated_return")
            entry["status"] = "ok"
            return content_of(result)
        except urllib.error.HTTPError as exc:
            entry["status"], entry["http_status"] = "http_error", exc.code
            try:
                error = json.loads(exc.read())
            except (ValueError, TypeError):
                error = {}
            code, message = extract_api_error(error)
            message = message.replace(self._key, "[redacted]")
            entry.update(provider_code=code, provider_message=message)
            kind = classify_api_error(code, message)
            if kind == "quota":
                entry["status"] = "quota_exhausted"
                raise QuotaExhausted(f"{stage}: MiniMax {code}: {message}") from None
            if kind == "waiting" or exc.code in (408, 429) or 500 <= exc.code < 600:
                delay = (exc.headers or {}).get("Retry-After", "0")
                raise NetworkRetry(f"http_{exc.code}: {message}", retry_after(delay)) from None
            raise ProjectError(f"{stage}: HTTP {exc.code}; {message}") from None
        except (socket.timeout, TimeoutError, urllib.error.URLError, ConnectionError, HTTPException) as exc:
            entry["status"] = "transport_error"
            detail = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            entry["error_type"] = type(detail).__name__
            entry["error_number"] = getattr(detail, "errno", None)
            raise NetworkRetry("timeout_or_connection_error") from None
        except NetworkRetry:
            raise
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            entry["status"] = "invalid_output"
            raise Retryable(str(exc) if isinstance(exc, Retryable) else "invalid_response") from None
        finally:
            entry["seconds"] = round(time.monotonic() - start, 3)
            if entry["status"] == "pending":
                entry["status"] = "failed"
            save(self.history_path, self.calls)
            print(f"{entry['seq']:02d} {stage} {entry['status']} {entry['seconds']}s", flush=True)

    def wait_network(self, name, state, path):
        if not state.get("not_before"):
            return
        print(f"waiting {name}: network backoff until {state['not_before']}", flush=True)
        while True:
            if getattr(self, "stop_requested", lambda: False)():
                raise InterruptedError("Interrupted during network backoff: " + name)
            remaining = (
                dt.datetime.fromisoformat(state["not_before"]) - dt.datetime.now(dt.timezone.utc)
            ).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 30))
        state["waited"] += state.pop("delay")
        state.pop("not_before")
        save(path, state)

    def stage(
        self,
        name,
        prompt,
        schema=None,
        validate=None,
        cap=10000,
        effort="low",
        normalize=None,
        max_attempts=6,
        parser=None,
        cumulative_output_failures=False,
    ):
        max_attempts = max(6, max_attempts)
        target = self.out / "steps" / (name + (".json" if schema else ".md"))

        def convert(raw):
            value = (parser or parse_json)(raw) if schema else raw.strip()
            if not value and not schema:
                raise Retryable("empty_return")
            if getattr(value, "recovered_output", False) and self.calls:
                self.calls[-1]["recovered_output"] = True
                save(self.history_path, self.calls)
            if normalize:
                value = normalize(value)
            if validate and validate(value) is False:
                raise ValueError("stage validation returned false")
            return value

        repaired = target.with_name(target.stem + ".recovered" + target.suffix)
        if repaired.exists():
            target = repaired
        cache_error = ""
        if target.exists():
            try:
                value = load(target) if schema else target.read_text(encoding="utf-8")
                if not schema and not value.strip():
                    raise ValueError("empty saved output")
                if normalize:
                    value = normalize(value)
                if validate and validate(value) is False:
                    raise ValueError("stage validation returned false")
                print(f"resume {name}", flush=True)
                return value
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                if target == repaired:
                    raise ProjectError(f"{name}: invalid recovered checkpoint: {exc}") from None
                cache_error = str(exc)[:180]
                target = repaired
        write_runtime(self.out, "running", name)
        full = self.prefix + "\n\n" + prompt
        if schema:
            full += "\n只返回完整JSON，结构：\n" + schema
        if self.context_limit and len(SYSTEM) + len(full) + 256 > self.context_limit:
            if self.clip_context:
                full = full[: max(0, self.context_limit - len(SYSTEM) - 256)]
            else:
                raise RuntimeError("Context exceeds configured character limit: " + name)
        if cache_error:
            full += "\n上次保存的输出无效：" + cache_error + "。请完整输出，旧文件将保留。"
        base_full = full
        previous = [c for c in self.calls if c["stage"] == name]
        # Recover a saved HTTP response if termination preceded its stage checkpoint.
        for entry in previous:
            path = self.out / "responses" / f"{entry['seq']:03d}.json"
            if path.exists():
                try:
                    result = load(path)
                    if (result.get("choices") or [{}])[0].get("finish_reason") == "length":
                        continue
                    value = convert(content_of(result))
                    entry.update(reused_response=True, usage=result.get("usage") or {}, model=result.get("model"))
                    save(self.history_path, self.calls)
                    save(target, value)
                    return value
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
        retry_path = self.out / "steps" / (name + ".retry.json")
        network = load(retry_path) if retry_path.exists() else dict(failures=0, waited=0)
        self.wait_network(name, network, retry_path)
        output_failures = (
            sum(c.get("status") in ("invalid_stage_output", "invalid_output") for c in previous)
            if cumulative_output_failures
            else 0
        )
        attempt = len(previous)
        max_attempts += output_failures
        detail = "invalid output"
        while output_failures < max_attempts:
            attempt += 1
            try:
                retry_cap = (
                    max(cap, 16000) if output_failures and self.calls[-1].get("finish_reason") == "length" else cap
                )
                write_runtime(self.out, "running", name)
                raw = self.request(name, full, retry_cap, effort if not output_failures else "low", attempt)
                value = convert(raw)
                save(target, value)
                write_runtime(self.out, "running", name)
                return value
            except NetworkRetry as exc:
                network["failures"] += 1
                delay = next_network_wait(network["failures"], network["waited"], exc.delay)
                network.update(
                    delay=delay, not_before=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay)).isoformat()
                )
                save(retry_path, network)
                write_runtime(
                    self.out,
                    "waiting",
                    name,
                    retry=network["failures"],
                    next_retry_at=time.time() + delay,
                    error=str(exc),
                )
                self.wait_network(name, network, retry_path)
            except (QuotaExhausted, ProjectError) as exc:
                kind = "quota_exhausted" if isinstance(exc, QuotaExhausted) else "error"
                write_runtime(self.out, kind, name, error=str(exc))
                raise
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                output_failures += 1
                self.calls[-1]["status"] = "invalid_stage_output"
                save(self.history_path, self.calls)
                detail = (
                    str(exc).replace(getattr(self, "_key", ""), "[redacted]")[:180]
                    if getattr(self, "_key", "")
                    else str(exc)[:180]
                )
                full = base_full + "\n上次输出无效：" + detail + "。请完整输出，段落编号须来自输入，逐字引用须准确。"
        message = f"Stage {name} failed after {max_attempts} attempts: {detail}"
        write_runtime(self.out, "error", name, error=message)
        raise ProjectError(message)
