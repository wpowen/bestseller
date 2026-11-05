"""Shared provider errors and observable runtime state, independent of pipelines."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time


class QuotaExhausted(RuntimeError):
    """The account needs resources before its saved operation can resume."""


class ProjectError(RuntimeError):
    """A non-transient project or protocol failure needs attention."""


def extract_api_error(payload):
    """Accept native base_resp and OpenAI-compatible error envelopes."""
    if not isinstance(payload, dict):
        return None, ''
    error = payload.get('base_resp') or payload.get('error') or {}
    if not isinstance(error, dict):
        return None, str(error)[:600]
    code = error.get('status_code', error.get('code'))
    message = str(error.get('status_msg', error.get('message', error.get('type', ''))))[:600]
    if code is None:
        match = re.search(r'\((\d{4})\)', message)
        if match:
            code = match[1]
    try:
        code = int(code) if code is not None else None
    except (ValueError, TypeError):
        pass
    return code, message


def classify_api_error(code, message=''):
    """MiniMax official errorcode reference; 1039 is a request-size error."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    if code in (1008, 2053, 2056, 2067):
        return 'quota'
    if code in (1000, 1001, 1002, 1024, 1033, 1041, 1045, 2045, 2046,
                2047, 2062, 2064, 2066, 2160):
        return 'waiting'
    text = str(message).lower()
    if any(s in text for s in ('insufficient balance', 'insufficient credits',
                              'quota exhausted', '余额不足', '额度已用完', '套餐额度已用完')):
        return 'quota'
    if any(s in text for s in ('rate limit', 'overloaded', '限流', '请求频率超限')):
        return 'waiting'
    return 'error'


def retry_delay(failures, minimum=0):
    minimum = float(minimum or 0)
    if not math.isfinite(minimum) or minimum < 0:
        minimum = 0
    delay = (10, 60, 300, 600)[min(max(int(failures) - 1, 0), 3)]
    return min(600, max(delay, minimum))


def write_runtime(out, kind, stage='', **fields):
    """Replace only ephemeral runtime state; never touch chapters or checkpoints."""
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    state = dict(kind=kind, stage=stage, updated=time.time(), **fields)
    fd, name = tempfile.mkstemp(prefix='.runtime-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(state, output, ensure_ascii=False)
        os.replace(name, folder / 'runtime.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)
