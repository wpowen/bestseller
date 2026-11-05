"""Standard-library MiniMax transport and recoverable request ledger."""

import datetime as dt
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import fcntl
from http.client import HTTPException
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error
import urllib.request

from tools.runtime_support import (
    ProjectError,
    QuotaExhausted,
    classify_api_error,
    extract_api_error,
    retry_delay,
    write_runtime,
)
from .io import parse_json, read_json, safe, save, read_text

STOP = threading.Event()


class LLMError(ProjectError):
    """A non-transient transport or protocol failure."""


class InvalidResponse(LLMError):
    """A malformed completion that the stage may request again with feedback."""

    error_kind = 'format'


class APIError(LLMError):
    def __init__(self, code, message):
        super().__init__(f'API {code}: {message}')
        self.kind = classify_api_error(code, message)


def runtime_root(out):
    """Chapter and standalone workspaces publish state beside the source book."""
    out = Path(out).resolve()
    finals = [parent for parent in (out, *out.parents) if parent.name == 'final']
    return finals[-1].parent if finals else out


def pause(seconds):
    if STOP.wait(max(0, seconds)):
        raise InterruptedError('Interrupted')


@contextmanager
def locked(path):
    with path.open('a+') as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                pause(0.1)
        yield


@contextmanager
def request_slot():
    """Bound concurrent HTTP sends without occupying a slot during backoff."""
    slot = None
    try:
        while slot is None:
            for number in range(6):
                candidate = Path(tempfile.gettempdir(), f'deai-slot-{os.getuid()}-{number}.lock').open('a+')
                try:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    candidate.close()
                    continue
                slot = candidate
                break
            if slot is None:
                pause(0.1)
        rate_path = Path(tempfile.gettempdir(), f'deai-rate-{os.getuid()}.lock')
        with locked(rate_path):
            previous = float(rate_path.read_text() or 0)
            pause(0.5 - (time.time() - previous))
            rate_path.write_text(str(time.time()))
        yield
    finally:
        if slot is not None:
            slot.close()


def retry_after(headers):
    value = headers.get('Retry-After', '0') if headers else '0'
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0


class MiniMax:
    def __init__(self, out, env, model='MiniMax-M3.1-Flash', timeout=180):
        self.runtime_out = runtime_root(out)
        self.out = Path(out).resolve() / 'http'
        self.out.mkdir(parents=True, exist_ok=True)
        values = dict(os.environ)
        for line in Path(env).read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name, value = line.removeprefix('export ').split('=', 1)
                values[name.strip()] = value.strip().strip('"\'')
        self._key = values.get(values.get('BESTSELLER__LLM__WRITER__API_KEY_ENV', 'MINIMAX_API_KEY'), '')
        self.base = values.get(
            'MINIMAX_BASE_URL', values.get('BESTSELLER__LLM__WRITER__API_BASE', 'https://api.minimaxi.com/v1')
        ).rstrip('/')
        if not self._key or not self.base.startswith('https://') or '@' in self.base or '?' in self.base:
            raise LLMError('Missing key or invalid HTTPS endpoint')
        self.model = model
        self.timeout = timeout
        self._local = threading.local()
        self.calls = read_json(safe(self.out, 'ledger.json'), [])
        self.last_usage = {}

    @property
    def last_usage(self):
        return getattr(self._local, 'usage', {})

    @last_usage.setter
    def last_usage(self, usage):
        self._local.usage = usage

    def redact(self, text):
        return str(text).replace(self._key, '[REDACTED]')

    def cost(self, row):
        prompt, completion = (0.15, 0.60) if 'Flash' in row.get('model', self.model) else (0.30, 1.20)
        usage = row.get('usage') or {}
        incoming, outgoing = usage.get('prompt_tokens'), usage.get('completion_tokens')
        return (
            (row['input_bytes'] if incoming is None else incoming) * prompt
            + (row['max_tokens'] if outgoing is None else outgoing) * completion
        ) / 1000000

    def estimate(self):
        """USD; missing usage/inflight retain input bytes and output cap estimates."""
        return sum(self.cost(row) for row in self.calls)

    def update(self, index, row):
        with locked(safe(self.out, '.ledger.lock')):
            self.calls = read_json(safe(self.out, 'ledger.json'), [])
            self.calls[index] = row
            save(self.out, 'ledger.json', self.calls)

    def backoff(self, index, row):
        delay = min(600, row.get('retry_remaining', 0))
        if delay <= 0:
            return
        write_runtime(
            self.runtime_out,
            'waiting',
            row['stage'],
            retry=row['attempt'],
            next_retry_at=time.time() + delay,
            error=row.get('error', 'request failed'),
        )
        tick = time.monotonic()
        try:
            pause(delay)
        finally:
            spent = max(0, time.monotonic() - tick)
            row['waited'] = row.get('waited', 0) + spent
            row['retry_remaining'] = max(0, delay - spent)
            self.update(index, row)

    def reserve(self, stage, model, limit, body, waited, attempt):
        with locked(safe(self.out, '.ledger.lock')):
            self.calls = read_json(safe(self.out, 'ledger.json'), [])
            index = len(self.calls)
            stem = f'{index:06d}'
            row = dict(
                stage=stage,
                model=model,
                input_bytes=len(body),
                max_tokens=limit,
                usage=None,
                waited=waited,
                attempt=attempt + 1,
                retry_remaining=0,
                status='inflight',
                http_status=None,
                elapsed=0,
                started=dt.datetime.now(dt.timezone.utc).isoformat(),
                request=stem + '.request.json',
                output=stem + '.text',
            )
            save(self.out, row['request'], body.decode('utf-8'))
            self.calls.append(row)
            save(self.out, 'ledger.json', self.calls)
        return index, stem, row

    def content(self, raw, row):
        try:
            data = parse_json(raw)
        except ValueError as exc:
            raise InvalidResponse(str(exc)) from exc
        if not isinstance(data, dict):
            raise InvalidResponse('Invalid JSON response envelope')
        row['usage'] = data.get('usage')
        code, message = extract_api_error(data)
        if code not in (None, 0, '0') or data.get('error'):
            raise APIError(code, self.redact(message))
        choices = data.get('choices')
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get('message') if isinstance(choice, dict) else None
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise InvalidResponse('Missing/empty completion; inspect raw response')
        if choice.get('finish_reason') in ('length', 'max_tokens'):
            raise InvalidResponse('Truncated completion; output token cap reached')
        return content

    def __call__(self, messages, **opts):
        stage = opts['stage']
        try:
            previous = ''
            for attempt in range(6):
                prompt = [dict(message) for message in messages]
                if previous:
                    prompt.append(
                        dict(
                            role='user',
                            content=(
                                f'上次返回失败（第{attempt}次）：{previous}。请直接返回完整正文或JSON，不要截断。'
                            ),
                        )
                    )
                try:
                    return self.request(prompt, **opts)
                except InvalidResponse as exc:
                    previous = self.redact(exc)
            error = ProjectError(f'stage {stage}: invalid response after 6 attempts: {previous}')
            error.error_kind = 'format'
            raise error
        except InterruptedError:
            write_runtime(self.runtime_out, 'interrupted', stage)
            raise
        except QuotaExhausted as exc:
            exc.stage = stage
            write_runtime(self.runtime_out, 'quota_exhausted', stage, error=self.redact(exc))
            raise
        except ProjectError as exc:
            exc.stage = stage
            write_runtime(self.runtime_out, 'error', stage, error=self.redact(exc))
            raise

    def request(self, messages, **opts):
        stage = opts['stage']
        model = opts.get('model') or self.model
        limit = opts.get('max_tokens', 3000)
        payload = dict(model=model, messages=messages, max_completion_tokens=limit)
        if 'Flash' in model:
            payload['reasoning_effort'] = 'low'
        else:
            payload['thinking'] = {'type': 'disabled'}
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        name = stage.encode().hex()
        stage_lock = safe(
            self.out, 'stages/' + '/'.join(name[i : i + 120] for i in range(0, len(name), 120)) + '/.lock'
        )
        stage_lock.parent.mkdir(parents=True, exist_ok=True)
        with locked(stage_lock):
            waited = attempt = 0
            for index, row in reversed(list(enumerate(read_json(safe(self.out, 'ledger.json'), [])))):
                if row['stage'] != stage:
                    continue
                if read_json(safe(self.out, row['request'])) != payload:
                    continue
                if row.get('status') == 'ok':
                    self.last_usage = row.get('usage') or {}
                    write_runtime(self.runtime_out, 'running', stage)
                    return read_text(safe(self.out, row['output']))
                if row.get('retryable'):
                    self.backoff(index, row)
                waited, attempt = row.get('waited', 0), row.get('attempt', 0)
                break
            while True:
                if STOP.is_set():
                    raise InterruptedError('Interrupted')
                index, stem, row = self.reserve(stage, model, limit, body, waited, attempt)
                tick = time.monotonic()
                kind, minimum, failure, content = 'error', 0, None, None
                try:
                    with request_slot():
                        request = urllib.request.Request(
                            self.base + '/chat/completions',
                            body,
                            {'Authorization': 'Bearer ' + self._key, 'Content-Type': 'application/json'},
                        )
                        row['sent_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
                        tick = time.monotonic()
                        with urllib.request.urlopen(request, timeout=self.timeout) as response:
                            row['http_status'] = response.status
                            raw = response.read().decode('utf-8', errors='replace')
                    save(self.out, stem + '.raw', self.redact(raw))
                    content = self.content(raw, row)
                    save(self.out, row['output'], content)
                    row['status'] = 'ok'
                    self.last_usage = row['usage'] or {}
                except urllib.error.HTTPError as exc:
                    row['http_status'] = exc.code
                    row['error'] = self.redact(exc.read().decode('utf-8', errors='replace'))
                    save(self.out, stem + '.raw', row['error'])
                    try:
                        data = parse_json(row['error'])
                        code, message = extract_api_error(data)
                        row['usage'] = data.get('usage') if isinstance(data, dict) else None
                    except ValueError:
                        code, message = None, row['error']
                    kind = classify_api_error(code, message)
                    if kind != 'quota' and (exc.code in (408, 429) or 500 <= exc.code < 600):
                        kind = 'waiting'
                    minimum = retry_after(exc.headers)
                except APIError as exc:
                    kind, row['error'] = exc.kind, self.redact(exc)
                except InterruptedError:
                    row['status'] = 'interrupted'
                    raise
                except (urllib.error.URLError, OSError, HTTPException) as exc:
                    row['error'], kind = self.redact(exc), 'waiting'
                except InvalidResponse as exc:
                    failure, row['error'] = exc, self.redact(exc)
                except Exception as exc:
                    failure = LLMError(self.redact(exc))
                    row['error'] = self.redact(exc)
                finally:
                    row['elapsed'] = time.monotonic() - tick
                    row['finished'] = dt.datetime.now(dt.timezone.utc).isoformat()
                    row['retryable'] = kind == 'waiting'
                    if row['status'] not in ('ok', 'interrupted'):
                        row['status'] = 'error'
                    row['retry_remaining'] = retry_delay(attempt + 1, minimum) if row['retryable'] else 0
                    self.update(index, row)
                if row['status'] == 'ok':
                    write_runtime(self.runtime_out, 'running', stage)
                    return content
                if kind != 'waiting':
                    error = f'stage {stage}: HTTP {row["http_status"]}: {row.get("error", "request failed")}'
                    raise QuotaExhausted(error) if kind == 'quota' else failure or LLMError(error)
                self.backoff(index, row)
                waited, attempt = row['waited'], row['attempt']
