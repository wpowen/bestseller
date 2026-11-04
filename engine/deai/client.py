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
from .io import parse_json, read_json, safe, save, read_text

STOP = threading.Event()

class LLMError(RuntimeError):
    """Transport/protocol/budget failure, never an empty completion."""

def pause(seconds):
    if STOP.wait(max(0, seconds)): raise InterruptedError('Interrupted')

@contextmanager
def locked(path):
    with path.open('a+') as handle:
        while True:
            try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError: pause(.1)
        yield

class MiniMax:
    def __init__(self, out, env, model='MiniMax-M3.1-Flash', timeout=180, budget=None):
        self.out = Path(out).resolve() / 'http'; self.out.mkdir(parents=True, exist_ok=True)
        values = dict(os.environ)
        for line in Path(env).read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name, value = line.removeprefix('export ').split('=', 1); values[name.strip()] = value.strip().strip('\"\'')
        self._key = values.get(values.get('BESTSELLER__LLM__WRITER__API_KEY_ENV', 'MINIMAX_API_KEY'), '')
        self.base = values.get('MINIMAX_BASE_URL', values.get('BESTSELLER__LLM__WRITER__API_BASE', 'https://api.minimaxi.com/v1')).rstrip('/')
        if not self._key or not self.base.startswith('https://') or '@' in self.base or '?' in self.base: raise LLMError('Missing key or invalid HTTPS endpoint')
        self.model, self.timeout, self.budget = model, timeout, budget
        self._local = threading.local(); self.calls = read_json(safe(self.out, 'ledger.json'), []); self.last_usage = {}
    @property
    def last_usage(self): return getattr(self._local, 'usage', {})
    @last_usage.setter
    def last_usage(self, usage): self._local.usage = usage
    def redact(self, text): return str(text).replace(self._key, '[REDACTED]')
    def cost(self, row):
        prompt, completion = (.15, .60) if 'Flash' in row.get('model', self.model) else (.30, 1.20)
        usage = row.get('usage') or {}
        incoming, outgoing = usage.get('prompt_tokens'), usage.get('completion_tokens')
        return ((row['input_bytes'] if incoming is None else incoming) * prompt + (row['max_tokens'] if outgoing is None else outgoing) * completion) / 1000000
    def estimate(self):
        """USD; missing usage/inflight reserve input bytes and output cap."""
        return sum(self.cost(row) for row in self.calls)
    def update(self, index, row):
        with locked(safe(self.out, '.ledger.lock')):
            self.calls = read_json(safe(self.out, 'ledger.json'), []); self.calls[index] = row; save(self.out, 'ledger.json', self.calls)
    def backoff(self, index, row):
        while row.get('retry_remaining', 0) > 0:
            tick = time.monotonic()
            try: pause(min(30, row['retry_remaining']))
            finally:
                spent = max(0, time.monotonic() - tick); row['waited'] += spent; row['retry_remaining'] = max(0, row['retry_remaining'] - spent); self.update(index, row)
    def __call__(self, messages, **opts):
        stage = opts['stage']; model = opts.get('model') or self.model; limit = opts.get('max_tokens', 3000)
        payload = dict(model=model, messages=messages, max_completion_tokens=limit)
        payload.update(reasoning_effort='low') if 'Flash' in model else payload.update(thinking={'type': 'disabled'})
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8'); name = stage.encode().hex()
        stage_lock = safe(self.out, 'stages/' + '/'.join(name[i:i+120] for i in range(0, len(name), 120)) + '/.lock'); stage_lock.parent.mkdir(parents=True, exist_ok=True)
        with locked(stage_lock):
            waited = 0; attempt = 0
            for index, row in reversed(list(enumerate(read_json(safe(self.out, 'ledger.json'), [])))):
                if row['stage'] != stage: continue
                if row.get('status') in ('pending', 'inflight'):
                    row['status'] = 'interrupted'; self.update(index, row)
                if read_json(safe(self.out, row['request'])) != payload: continue
                if row.get('status') == 'ok':
                    self.last_usage = row.get('usage') or {}; return read_text(safe(self.out, row['output']))
                if row.get('error','').startswith('Missing/empty completion'):raise LLMError(row['error'])
                if row.get('http_status') in (400, 401, 403, 404, 413, 422): raise LLMError(f"HTTP {row['http_status']}: {row.get('error', '')}")
                self.backoff(index, row); waited = row.get('waited', 0); attempt = row.get('attempt', 0)
                if row.get('retryable') and waited >= 900: raise LLMError('Network retries exhausted after >=900 seconds actual backoff')
                break
            while True:
                if STOP.is_set(): raise InterruptedError('Interrupted')
                with locked(safe(self.out, '.ledger.lock')):
                    self.calls = read_json(safe(self.out, 'ledger.json'), [])
                    row = dict(stage=stage, model=model, input_bytes=len(body), max_tokens=limit, usage=None, waited=waited, attempt=attempt+1, retry_remaining=0, status='inflight', http_status=None, elapsed=0, started=dt.datetime.now(dt.timezone.utc).isoformat())
                    if self.budget is not None and self.estimate() + self.cost(row) > self.budget: raise LLMError('USD budget exhausted (including missing usage/inflight)')
                    index = len(self.calls); stem = f'{index:06d}'; row.update(request=stem+'.request.json', output=stem+'.text')
                    save(self.out, row['request'], body.decode('utf-8')); self.calls.append(row); save(self.out, 'ledger.json', self.calls)
                tick = time.monotonic(); wait = 0; retry = False; slot = None
                try:
                    while slot is None:
                        for n in range(6):
                            candidate = Path(tempfile.gettempdir(), f'deai-slot-{os.getuid()}-{n}.lock').open('a+')
                            try: fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except BlockingIOError: candidate.close(); continue
                            slot = candidate; break
                        if slot is None: pause(.1)
                    with locked(Path(tempfile.gettempdir(), f'deai-rate-{os.getuid()}.lock')):
                        rate_path = Path(tempfile.gettempdir(), f'deai-rate-{os.getuid()}.lock'); previous = float(rate_path.read_text() or 0); pause(.5 - (time.time() - previous)); rate_path.write_text(str(time.time()))
                    request = urllib.request.Request(self.base + '/chat/completions', body, {'Authorization': 'Bearer ' + self._key, 'Content-Type': 'application/json'})
                    row['sent_at']=dt.datetime.now(dt.timezone.utc).isoformat();tick=time.monotonic()
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        row['http_status'] = response.status; raw = response.read().decode('utf-8', errors='replace')
                    save(self.out, stem + '.raw', raw); data = parse_json(raw); row['usage'] = data.get('usage')
                    content = data.get('choices', [{}])[0].get('message', {}).get('content')
                    if not isinstance(content, str) or not content.strip(): raise LLMError('Missing/empty completion; inspect raw response')
                    save(self.out, row['output'], content); row['status'] = 'ok'; self.last_usage = row['usage'] or {}; return content
                except urllib.error.HTTPError as exc:
                    row['http_status'] = exc.code; row['error'] = self.redact(exc.read().decode('utf-8', errors='replace')); retry = exc.code in (408,429) or 500 <= exc.code < 600
                    header = exc.headers.get('Retry-After', '0') if exc.headers else '0'
                    try: wait = max(0, float(header))
                    except ValueError:
                        try: wait = max(0, parsedate_to_datetime(header).timestamp() - time.time())
                        except (ValueError, TypeError, OverflowError): wait = 0
                    save(self.out, stem + '.raw', row['error'])
                except (urllib.error.URLError, OSError, HTTPException) as exc:
                    row['error'] = self.redact(exc); retry = not STOP.is_set()
                except Exception as exc:
                    row['error'] = self.redact(exc)
                finally:
                    if slot is not None: slot.close()
                    row['elapsed'] = time.monotonic() - tick; row['finished']=dt.datetime.now(dt.timezone.utc).isoformat(); row['retryable'] = retry
                    if row['status'] != 'ok': row['status'] = 'error'
                    row['retry_remaining'] = max(wait, (10, 60, 300, 600)[min(attempt, 3)]) if retry and waited < 900 else 0
                    self.update(index, row)
                if not retry or waited >= 900: raise LLMError(f"stage {stage}: HTTP {row['http_status']}: {row.get('error', 'request failed')}") from None
                self.backoff(index, row); waited = row['waited']; attempt += 1
