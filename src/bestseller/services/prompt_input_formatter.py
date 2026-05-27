"""Standardized prompt-input formatting helpers.

Goal: stop service code from f-string-injecting raw Python dicts / lists /
SQLAlchemy models into LLM prompts. Python's ``repr`` style (single quotes,
nested braces) confuses LLMs into treating the payload as code rather than
content.

All long-list / nested-dict / large-text injections into prompts MUST go
through one of the helpers in this module:

* :func:`dict_to_markdown` — render Mapping as yaml-like markdown list
* :func:`list_to_markdown` — render Sequence as numbered/bulleted markdown
* :func:`group_facts_by_type` — bucket facts by an attribute + cap each bucket
* :func:`render_evidence_block` — wrap a long string in a labelled fenced block
* :func:`render_task_header` — emit a stable ``## 任务参数`` section
* :func:`render_json_schema_block` — describe expected JSON schema to LLM

Design choices:

* Markdown over JSON repr: easier for LLM tokenizers, clearer hierarchy.
* Soft truncation, never silent: every truncation appends an explicit
  "已截断" note so the LLM knows it didn't see everything.
* Stable ordering: dict keys are emitted in insertion order (Py 3.7+),
  enabling Anthropic prompt cache hits when callers reuse the same data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def dict_to_markdown(
    data: Mapping[str, Any],
    *,
    indent: int = 0,
    list_preview: int = 8,
    truncate_long_values: int | None = 200,
) -> str:
    """Render a Mapping as a yaml-like markdown bullet list.

    Args:
        data: mapping to render.
        indent: starting indentation level (each level adds two spaces).
        list_preview: when a value is a list, show first N items inline.
        truncate_long_values: if a scalar value exceeds this many chars,
            truncate with an explicit ``…(已截断, 原长 N)`` marker.
            Pass ``None`` to disable truncation.
    """
    if not isinstance(data, Mapping):
        return ""
    prefix = "  " * indent
    lines: list[str] = []
    for key, value in data.items():
        key_str = str(key)
        if isinstance(value, Mapping) and value:
            lines.append(f"{prefix}- **{key_str}**:")
            lines.append(
                dict_to_markdown(
                    value,
                    indent=indent + 1,
                    list_preview=list_preview,
                    truncate_long_values=truncate_long_values,
                )
            )
        elif isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{prefix}- **{key_str}**: _（空）_")
                continue
            shown = list(value)[:list_preview]
            preview = ", ".join(_format_scalar(x, truncate_long_values) for x in shown)
            if len(value) > list_preview:
                preview += f" …（仅显示前 {list_preview} / 共 {len(value)} 项）"
            lines.append(f"{prefix}- **{key_str}**: {preview}")
        else:
            lines.append(f"{prefix}- **{key_str}**: {_format_scalar(value, truncate_long_values)}")
    return "\n".join(lines)


def list_to_markdown(
    items: Sequence[Any],
    *,
    numbered: bool = False,
    cap: int | None = 20,
    item_renderer: Any = None,
) -> str:
    """Render a list as a markdown bullet/numbered list with cap.

    Args:
        items: sequence to render.
        numbered: use 1./2./3. style instead of bullets.
        cap: max items to show; pass ``None`` to disable cap.
        item_renderer: optional callable ``(item) -> str``; defaults to ``str``.
    """
    if not items:
        return "_（空）_"
    renderer = item_renderer or (lambda x: str(x))
    shown = list(items)[: cap] if cap is not None else list(items)
    if numbered:
        body = "\n".join(f"{i + 1}. {renderer(it)}" for i, it in enumerate(shown))
    else:
        body = "\n".join(f"- {renderer(it)}" for it in shown)
    if cap is not None and len(items) > cap:
        body += f"\n\n_（仅显示前 {cap} / 共 {len(items)} 项）_"
    return body


def group_facts_by_type(
    facts: Sequence[Any],
    *,
    type_attr: str = "fact_type",
    fallback_bucket: str = "misc",
    max_per_group: int = 30,
    item_renderer: Any = None,
) -> str:
    """Bucket sequence of objects by an attribute, render each bucket as
    ``### {bucket}（{kept}/{total} 条）`` block.

    Caps each bucket individually so a single chatty bucket can't drown
    out the others.
    """
    if not facts:
        return "_（无）_"

    renderer = item_renderer or _default_fact_line_renderer
    buckets: dict[str, list[Any]] = {}
    for fact in facts:
        ftype = getattr(fact, type_attr, None) or fallback_bucket
        buckets.setdefault(str(ftype), []).append(fact)

    sections: list[str] = []
    for bucket_name in sorted(buckets):
        bucket = buckets[bucket_name]
        kept = bucket[:max_per_group]
        body = "\n".join(renderer(f) for f in kept)
        truncated_note = (
            f"\n\n_（仅显示前 {max_per_group} / 共 {len(bucket)} 条）_"
            if len(bucket) > max_per_group
            else ""
        )
        sections.append(
            f"### {bucket_name}（{len(kept)}/{len(bucket)} 条）\n{body}{truncated_note}"
        )
    return "\n\n".join(sections)


def render_evidence_block(
    text: str,
    *,
    title: str = "原文",
    max_chars: int = 6000,
    fence_lang: str = "",
) -> str:
    """Wrap a long string in a labelled fenced block with explicit boundaries.

    Args:
        text: the raw text (e.g. chapter prose).
        title: section title — appears as ``## {title}（{N} 字）``.
        max_chars: truncate body to this many chars.
        fence_lang: optional code-fence language tag (e.g. ``"yaml"``).
    """
    text = text or ""
    body = text[:max_chars]
    fence_open = f"```{fence_lang}" if fence_lang else "```"
    truncated = (
        f"\n\n_（已截断至 {max_chars} 字 / 原长 {len(text)} 字）_"
        if len(text) > max_chars
        else ""
    )
    return f"## {title}（{len(body)} 字）\n{fence_open}\n{body}\n```{truncated}"


def render_task_header(**params: Any) -> str:
    """Emit a stable ``## 任务参数`` section listing all non-None params.

    Example::

        render_task_header(
            chapter_no=42,
            scene_no=3,
            language="zh-CN",
            target_word_count=2200,
        )

    produces::

        ## 任务参数
        - chapter_no：42
        - scene_no：3
        - language：zh-CN
        - target_word_count：2200
    """
    lines = [f"- {k}：{v}" for k, v in params.items() if v is not None]
    if not lines:
        return ""
    return "## 任务参数\n" + "\n".join(lines)


def render_json_schema_block(
    schema_hint: Mapping[str, str],
    *,
    title: str = "输出格式（严格 JSON）",
) -> str:
    """Render a field-name → description map as a JSON schema hint block.

    Example::

        render_json_schema_block({
            "drift_score": "0.0-1.0 小数",
            "drifted_dimensions": "list[str]",
            "evidence": "list of {dim, quote}",
        })
    """
    lines = ["{"]
    items = list(schema_hint.items())
    for i, (field, desc) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f'  "{field}": <{desc}>{comma}')
    lines.append("}")
    body = "\n".join(lines)
    return f"## {title}\n```json\n{body}\n```"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _format_scalar(value: Any, truncate: int | None) -> str:
    """Render a scalar; truncate strings beyond the limit."""
    if value is None:
        return "_（空）_"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if truncate is not None and len(text) > truncate:
        return f"{text[:truncate]}…（已截断, 原长 {len(text)}）"
    return text


def _default_fact_line_renderer(fact: Any) -> str:
    """Default renderer for objects in :func:`group_facts_by_type`.

    Tries common attributes seen in this codebase:
    ``subject_label`` / ``predicate`` / ``value_json``.
    Falls back to ``str(fact)``.
    """
    subject = getattr(fact, "subject_label", None)
    predicate = getattr(fact, "predicate", None)
    value = getattr(fact, "value_json", None)
    if subject is None and predicate is None and value is None:
        return f"- {fact}"
    value_str: str
    if isinstance(value, Mapping):
        value_str = ", ".join(
            f"{k}={v}" for k, v in list(value.items())[:5]
        )
    elif isinstance(value, (list, tuple)):
        value_str = ", ".join(str(x) for x in list(value)[:5])
    elif value is None:
        value_str = "_（空）_"
    else:
        value_str = str(value)[:120]
    return f"- **{subject}** · {predicate} → {value_str}"


__all__ = [
    "dict_to_markdown",
    "list_to_markdown",
    "group_facts_by_type",
    "render_evidence_block",
    "render_task_header",
    "render_json_schema_block",
]
