"""Future source-complete outline JSON syntax boundary; no story validation.

Parse one complete object, optionally inside one complete Markdown json fence.
Never repair, extract fragments, unwrap envelopes, coerce or supply defaults.
The normal LLM layer owns original content/physical payload retention.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

POLICY = "source-complete-outline-strict-json.v1"
PROMPT_VERSION = "3.1-source-complete-strict-json"
FORMAT_INSTRUCTION = (
    "\n\n[STRICT OUTLINE JSON SUBMISSION v1]\n"
    "Return exactly one complete JSON object, optionally enclosed in one complete "
    "```json Markdown fence. Preserve the complete requested chapter range and "
    "all required fields. JSON keys must be unique at every nesting level and "
    "numbers must be finite. Do not emit prose before/after the object, extra "
    "containers, a source/transport envelope, or an incomplete JSON fragment. "
    "This is a serialization protocol, not an additional story requirement."
)
_FENCE = re.compile(r"```json[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```", re.IGNORECASE)


class StrictOutlineJSONError(ValueError):
    """Safe syntax diagnostics; original model text is never embedded here."""

    def __init__(self, code: str, *, position: int | None = None) -> None:
        self.code = f"source_complete_outline_strict_json_{code}"
        self.position = position
        super().__init__(self.code + (f" at character {position}" if position is not None else ""))


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise StrictOutlineJSONError("duplicate_key")
        result[key] = value
    return result


def _nonfinite(_value: str) -> None:
    raise StrictOutlineJSONError("nonfinite_number")


def _float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise StrictOutlineJSONError("nonfinite_number")
    return parsed


def parse_strict_outline_json(text: str) -> dict[str, Any]:
    """Accept whole JSON with JSON whitespace, never a prefix or suffix salvage."""
    if not isinstance(text, str):
        raise StrictOutlineJSONError("text_required")
    document = text.strip(" \t\r\n")
    if not document:
        raise StrictOutlineJSONError("empty")
    if document.startswith("```"):
        fence = _FENCE.fullmatch(document)
        if fence is None:
            raise StrictOutlineJSONError("whole_json_fence_required")
        document = fence.group("body")
    try:
        payload = json.loads(
            document, object_pairs_hook=_unique, parse_constant=_nonfinite, parse_float=_float
        )
    except StrictOutlineJSONError:
        raise
    except json.JSONDecodeError as exc:
        raise StrictOutlineJSONError("syntax", position=exc.pos) from exc
    except (ValueError, OverflowError, RecursionError) as exc:
        raise StrictOutlineJSONError("unsupported_json_value") from exc
    if not isinstance(payload, dict):
        raise StrictOutlineJSONError("object_required")
    return payload
