"""Failure metadata only: no message text, source lines, or frame locals."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

_CODE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+){1,12}\Z")
_MESSAGE_CODE = re.compile(
    r"^(?:execution|planning|source|outline|foundation|chapter|cast|identity|narrative|"
    r"promotion|complete|prefix|canonical|character|world|retained|book|capture|harness)"
    r"_[a-z0-9_]{1,160}(?=:|$)"
)


def _filename(name):
    path = Path(name)
    parts = path.parts
    for anchor in ("src", "tests", "migrations"):
        if anchor in parts:
            return str(Path(*parts[parts.index(anchor) :]))
    return path.name


def safe_exception(error, _seen=None):
    """Tracebacks are traversed directly; traceback formatting can copy source."""
    seen = set() if _seen is None else _seen
    if id(error) in seen:
        return {"error_type": type(error).__name__, "cycle": True}
    seen.add(id(error))
    message = str(error)
    raw = message.encode("utf-8", errors="replace")
    codes = []
    explicit = getattr(error, "code", None)
    if isinstance(explicit, str) and _CODE.fullmatch(explicit):
        codes.append(explicit)
    matched = _MESSAGE_CODE.match(message)
    if matched and matched.group() not in codes:
        codes.append(matched.group())
    frames = []
    current = error.__traceback__
    while current is not None:
        code = current.tb_frame.f_code
        frames.append(
            {
                "file": _filename(code.co_filename),
                "function": code.co_name,
                "line": current.tb_lineno,
            }
        )
        current = current.tb_next
    original = getattr(error, "orig", error)
    state = getattr(original, "sqlstate", None)
    result = {
        "error_type": type(error).__name__,
        "error_sha256": hashlib.sha256(raw).hexdigest(),
        "error_message_chars": len(message),
        "error_message_bytes": len(raw),
        "error_codes": codes,
        "sqlstate": state
        if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state)
        else None,
        "safe_stack": frames,
    }
    diagnostic = getattr(error, "credential_rejection", None)
    if (
        isinstance(diagnostic, dict)
        and set(diagnostic) == {"field_category", "field_sha256", "path_sha256", "path_depth"}
        and diagnostic["field_category"]
        in {
            "api_key_env",
            "rate_limit_fallback_api_key_env",
            "api_key_header",
            "secret",
            "password",
            "authorization",
            "access_token",
            "api_key",
            "other",
        }
        and type(diagnostic["path_depth"]) is int
        and 0 < diagnostic["path_depth"] < 1000
        and all(
            isinstance(diagnostic[key], str) and re.fullmatch(r"[a-f0-9]{64}", diagnostic[key])
            for key in ("field_sha256", "path_sha256")
        )
    ):
        result["credential_rejection"] = diagnostic.copy()
    cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    if cause is not None:
        result["causes"] = [safe_exception(cause, seen)]
    return result
