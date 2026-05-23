"""Sanitize audit text before it is embedded in writer prompts."""

from __future__ import annotations

import re

_CONTROL_TAG_RE = re.compile(
    r"</?(?:system|system-reminder|assistant|developer|tool|instruction)[^>]*>",
    re.IGNORECASE,
)
_INST_RE = re.compile(
    r"\[/?INST\]|\<\|/?(?:system|assistant|user)\|?\>",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_CONTROL_LINE_PATTERNS = (
    re.compile(
        r"\b(?:ignore|disregard|override)\b.*\b(?:previous|above|system)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:as|you are)\s+(?:chatgpt|claude|an ai|the assistant)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:use|do not use|never use|must use)\b.*\b(?:tool|browser|shell|python|web)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(忽略|覆盖|无视).*(之前|以上|系统|开发者).*(指令|要求|消息)"),
    re.compile(r"(你是|作为)\s*(ChatGPT|Claude|AI|助手)"),
    re.compile(r"(使用|不要使用|必须使用|禁止使用).*(工具|浏览器|shell|python|网络|网页)"),
)


def sanitize_audit_input(text: str | None) -> str:
    """Return prompt-safe audit text.

    The sanitizer is intentionally conservative: it removes control tags,
    URLs, and whole lines that look like model/tool instructions while
    preserving ordinary audit evidence and narrative wording.
    """

    if not text:
        return ""
    cleaned = _CONTROL_TAG_RE.sub("", str(text))
    cleaned = _INST_RE.sub("", cleaned)
    cleaned = _URL_RE.sub("[url-removed]", cleaned)

    safe_lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            safe_lines.append("")
            continue
        if any(pattern.search(stripped) for pattern in _CONTROL_LINE_PATTERNS):
            continue
        safe_lines.append(line.rstrip())

    return "\n".join(safe_lines).strip()


def sanitize_audit_block(label: str, text: str | None) -> str:
    """Render a labeled audit block after sanitization."""

    sanitized = sanitize_audit_input(text)
    if not sanitized:
        return ""
    return f"{label.strip()}\n{sanitized}"
