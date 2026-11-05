"""Pure parsing and paragraph editing helpers."""

import ast
import json
import re


def _drop_extra_closers(text):
    stack = []
    complete = [True]

    def repair(match):
        token = match[0]
        if token.startswith('"'):
            return token
        if token in "{[":
            stack.append(token)
            return token
        if stack and stack[-1] == {"}": "{", "]": "["}[token]:
            stack.pop()
            return token
        if stack:
            complete[0] = False
        return ""

    repaired = re.sub(r'"(?:\\.|[^"\\])*"|[{}\[\]]', repair, text)
    return repaired + ("".join({"{": "}", "[": "]"}[token] for token in reversed(stack)) if complete[0] else "")


def parse_json(text):
    """Accept object JSON, fences, trailing commas, and Python dict literals."""
    if not isinstance(text, str):
        raise ValueError("invalid_json")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    candidates = [text, re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    # Match quoted strings first so comma repair cannot change their contents.
    tokens = r'"(?:\\.|[^"\\])*"|,\s*(?=[}\]])'
    for candidate in candidates:
        repaired = re.sub(tokens, lambda m: m[0] if m[0].startswith('"') else "", candidate)
        for variant in (candidate, repaired, _drop_extra_closers(repaired)):
            try:
                value = json.loads(variant)
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
        try:
            value = ast.literal_eval(candidate)
            if isinstance(value, dict):
                return json.loads(json.dumps(value, ensure_ascii=False))
        except (ValueError, SyntaxError, TypeError):
            pass
    raise ValueError("invalid_json")


def chinese_count(text):
    return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))


def prose(text):
    """Remove a model-added Markdown chapter heading and outer whitespace."""
    return re.sub(r"^\s*#{1,3}[^\n]*\n+", "", text).strip()


def pack(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def normalize_review(value):
    """Accept MiniMax's observed field typo without changing the advice."""
    if not isinstance(value.get("issues"), list):
        return value
    issues = []
    for issue in value["issues"]:
        if isinstance(issue, dict) and "suggestion" not in issue and isinstance(issue.get("suggesting"), str):
            issue = dict(issue, suggestion=issue["suggesting"])
            del issue["suggesting"]
        issues.append(issue)
    return dict(value, issues=issues)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _paragraph_spans(text):
    """Return nonblank paragraph offsets, leaving separators outside spans."""
    _require(isinstance(text, str), "invalid_text")
    separator = r"\r?\n[^\S\r\n]*\r?\n(?:[^\S\r\n]*\r?\n)*"
    spans, start = [], 0
    for match in re.finditer(separator, text):
        if text[start : match.start()].strip():
            spans.append((start, match.start()))
        start = match.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    # A single outer line ending is formatting, not paragraph content.
    result = []
    for start, end in spans:
        while start < end and text[start] in "\r\n":
            start += 1
        while end > start and text[end - 1] in "\r\n":
            end -= 1
        result.append((start, end))
    return result


def _replace_spans(text, replacements):
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def apply_patches(chapters, patches) -> list[str]:
    """Apply whole-paragraph patches by number or a unique original quote.

    Chapter numbers are one-based. Each patch must leave at least one original
    paragraph outside its range; overlapping ranges in one batch are rejected.
    """
    _require(isinstance(chapters, (list, tuple)) and all(isinstance(c, str) for c in chapters), "invalid_chapters")
    _require(isinstance(patches, (list, tuple)), "invalid_patches")
    changes = [[] for _ in chapters]
    for patch in patches:
        _require(isinstance(patch, dict), "invalid_patch")
        chapter, old, new = (patch.get(k) for k in ("chapter", "original", "replacement"))
        _require(type(chapter) is int and 1 <= chapter <= len(chapters), "invalid_chapter")
        _require(isinstance(new, str) and (new == "" or bool(new.strip())), "empty_or_invalid_patch")
        text = chapters[chapter - 1]
        spans = _paragraph_spans(text)
        if "paragraph" in patch:
            paragraph = patch["paragraph"]
            _require(type(paragraph) is int and 1 <= paragraph <= len(spans), "invalid_paragraph")
            start, end = spans[paragraph - 1]
        else:
            _require(isinstance(old, str) and bool(old.strip()), "empty_or_invalid_patch")
            start = text.find(old)
            _require(start >= 0 and text.find(old, start + 1) < 0, "original_not_unique")
            end = start + len(old)
        first = next((i for i, span in enumerate(spans) if span[0] == start), None)
        last = next((i for i, span in enumerate(spans) if span[1] == end), None)
        _require(first is not None and last is not None and first <= last, "original_not_whole_paragraphs")
        _require(last - first + 1 < len(spans), "whole_chapter_patch")
        pending = changes[chapter - 1]
        _require(all(end <= left or start >= right for left, right, _ in pending), "overlapping_patches")
        pending.append((start, end, new))
    return [_replace_spans(text, pending) for text, pending in zip(chapters, changes)]


def apply_paragraph_edits(text, edits, allowed_ids) -> str:
    """Replace allowed one-based paragraphs without rebuilding other text."""
    spans = _paragraph_spans(text)
    _require(isinstance(edits, (list, tuple)), "invalid_edits")
    try:
        allowed = list(allowed_ids)
    except TypeError:
        raise ValueError("invalid_allowed_ids") from None
    _require(all(type(i) is int and 1 <= i <= len(spans) for i in allowed), "invalid_allowed_ids")
    seen, replacements = set(), []
    for edit in edits:
        _require(isinstance(edit, dict), "invalid_edit")
        paragraph, replacement = edit.get("paragraph"), edit.get("text")
        _require(
            type(paragraph) is int and 1 <= paragraph <= len(spans) and paragraph in allowed,
            "invalid_or_disallowed_paragraph",
        )
        _require(paragraph not in seen, "duplicate_paragraph")
        _require(isinstance(replacement, str) and bool(replacement.strip()), "empty_or_invalid_replacement")
        seen.add(paragraph)
        start, end = spans[paragraph - 1]
        replacements.append((start, end, replacement))
    return _replace_spans(text, replacements)


def number_paragraphs(text) -> str:
    """Prefix each nonblank paragraph with [P1], [P2], and so on."""
    insertions = [(start, start, f"[P{i}] ") for i, (start, _) in enumerate(_paragraph_spans(text), 1)]
    return _replace_spans(text, insertions)
