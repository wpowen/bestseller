"""Research Agent — supplies the :mod:`material_library` with entries.

Plan context
------------

Part of Batch 1 (``twinkly-rolling-pnueli.md``).  This agent is what
actually *puts data into* the multi-dimensional material library so that
Forges (Batch 2) and Planners (Batch 2/3) can retrieve it.

Pipeline per ``run_research`` invocation::

    skills (load_skills_for_genre)
      └─► methodology prompt block
      └─► taboo patterns

    tools                                             (registered in a ToolSpec set)
      ├─ search_web(query)           → HTTP search (Tavily/Serper)
      ├─ search_library(query)       → pgvector recall of existing entries
      ├─ search_project_retrieval(…) → per-project canon facts
      ├─ mcp__*                      → optional: exa / wikipedia / local-knowledge
      └─ emit_entry(…)               → persists one MaterialEntry (non-terminal)

    run_tool_loop drives the conversation; the model chooses tools per
    round until it has enough material and stops emitting.

The emitted entries are deduplicated + upserted by ``(dimension, slug)``
so running research multiple times enriches rather than replaces.
Tabooed slugs / names are rejected at the emit step — this is the
first line of defence against the "everyone writes 方域" cross-project
cloning that motivated the whole refactor.

This module is *fire-and-forget* — the caller provides an AsyncSession
and awaits the terminal :class:`ResearchOutcome`.  Telemetry lives on
:attr:`ResearchOutcome.tool_trace` for inspection.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from textwrap import dedent
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest
from bestseller.services.llm_tool_runtime import (
    ToolCallRecord,
    ToolRegistry,
    ToolSpec,
    run_tool_loop,
)
from bestseller.services.material_library import (
    MaterialEntry,
    insert_entry,
    query_library,
)
from bestseller.services.search_client import (
    NoopSearchClient,
    SearchResponse,
    WebSearchClient,
)
from bestseller.services.skills_loader import (
    ResearchSkill,
    load_skills_for_genre,
    render_skills_prompt_block,
)
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


# ── Slug / taboo helpers ───────────────────────────────────────────────


# Allow 1..160 chars. First and last char must be alphanumeric; internal
# chars may include hyphens.  Accepts ``x`` as well as ``qingluo-sect``.
_SLUG_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9\-]{0,158}[a-z0-9])?$"
)


def _slug_looks_valid(slug: str) -> bool:
    return bool(slug) and _SLUG_PATTERN.match(slug) is not None


def _collect_taboos(skills: Sequence[ResearchSkill]) -> tuple[str, ...]:
    seen: list[str] = []
    for skill in skills:
        for pattern in skill.taboo_patterns:
            stripped = pattern.strip()
            if stripped and stripped not in seen:
                seen.append(stripped)
    return tuple(seen)


def _entry_hits_taboo(entry: MaterialEntry, taboos: Sequence[str]) -> str | None:
    """Return the first taboo that matches this entry, or ``None``."""

    if not taboos:
        return None
    haystack_parts = [
        entry.name or "",
        entry.slug or "",
        entry.narrative_summary or "",
    ]
    haystack = " ".join(haystack_parts).lower()
    for pattern in taboos:
        if pattern.lower() in haystack:
            return pattern
    return None


# ── Outcome DTO ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResearchOutcome:
    """Summarises a ``run_research`` call for the caller / telemetry."""

    dimension: str
    genre: str | None
    sub_genre: str | None
    target_count: int
    emitted: tuple[MaterialEntry, ...] = field(default_factory=tuple)
    rejected_taboos: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    tool_trace: tuple[ToolCallRecord, ...] = field(default_factory=tuple)
    rounds: int = 0
    exit_reason: str = "text"

    @property
    def emitted_count(self) -> int:
        return len(self.emitted)


# ── Prompt construction ────────────────────────────────────────────────


_SYSTEM_INSTRUCTIONS = dedent(
    """
    你是「物料库研究员」。你的任务是为多维度物料库补充高质量、可复用、
    可被跨项目引用的条目。

    工作原则：
    1. 每条条目必须足够具体、可直接进入小说创作；拒绝空泛。
    2. 可用工具：
       - ``search_web``：检索公开资料、典籍、学术/百科；
       - ``search_library``：先检查库中已有条目，避免重复；
       - ``emit_entry``：把最终结果提交入库（这是你最重要的工具）。
    3. 首选策略：先调用 ``search_library`` 了解库中已有内容（避免重复），
       然后尝试一次 ``search_web`` 获取外部参考。
    4. **重要**：无论 ``search_web`` 是否返回结果，**都必须尽快开始调用**
       ``emit_entry`` 产出条目，每轮至少产出一条。若 ``search_web`` 多轮
       返回空/无 hits（例如当前环境未配置 Web 搜索 key），允许基于通用
       知识与题材常识直接 ``emit_entry``，此时 ``source_citations`` 可以
       为空数组 ``[]``，``confidence`` 取 0.4–0.6。
    5. 触发任一 taboo 模式的条目一律不要提交；若检索结果或自拟命名含
       taboo，必须变形命名后再提交。
    6. 提交的 ``slug`` 必须是 kebab-case，只能包含 [a-z0-9-]。
    7. 持续调用 ``emit_entry`` 直到达到 ``target_count``；之后再用简短的
       自然语言总结收尾（不再调工具）。严禁"只研究不提交"。
    """
).strip()


def _build_user_prompt(
    *,
    dimension: str,
    genre: str | None,
    sub_genre: str | None,
    target_count: int,
    skills: Sequence[ResearchSkill],
    taboos: Sequence[str],
) -> str:
    skill_block = render_skills_prompt_block(list(skills))
    taboo_block = (
        "**全局禁用模式 (taboos)**:\n- " + "\n- ".join(taboos) if taboos else ""
    )
    genre_label = genre or "（通用/跨题材）"
    sub_label = sub_genre or "—"
    return dedent(
        f"""
        # 任务
        为物料库补充 **{target_count}** 条以下维度的条目：

        - dimension: `{dimension}`
        - genre: {genre_label}
        - sub_genre: {sub_label}

        每条必须包含：
        - name：条目名（中文，避免雷同，避免 taboo）
        - slug：kebab-case 唯一 ID，例如 ``qingyun-sect`` / ``five-qi-law``
        - narrative_summary：一句到三句话概括条目，供创作 Agent 检索时判断是否相关
        - content_json：结构化内容（按维度自行设计字段）
        - source_citations：检索到的 URL / 知识来源数组；无外部检索时可为 ``[]``
        - confidence：0~1 的自评置信度（无外部源时建议 0.4–0.6）

        ## 工具
        - `search_library(query)` 先查已有条目
        - `search_web(query)` 访问公开资料
        - `emit_entry(...)` 正式提交一条条目

        {skill_block}

        {taboo_block}
        """
    ).strip()


# ── Tool registry wiring ───────────────────────────────────────────────


def _build_search_web_tool(
    search_client: WebSearchClient,
) -> ToolSpec:
    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        max_results = int(arguments.get("max_results") or 5)
        if not query:
            return {"error": "empty_query"}
        response: SearchResponse = await search_client.search(
            query, max_results=max_results
        )
        return {
            "query": response.query,
            "provider": response.provider,
            "cached": response.cached,
            "error": response.error,
            "hits": [
                {
                    "title": h.title,
                    "url": h.url,
                    "snippet": h.snippet,
                    "source": h.source,
                    "published_at": h.published_at,
                    "score": h.score,
                }
                for h in response.hits
            ],
        }

    return ToolSpec(
        name="search_web",
        description=(
            "Search the open web (Tavily/Serper/multi) for references and "
            "authoritative sources. Returns a list of {title,url,snippet} hits."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-form web query."},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        handler=handler,
    )


def _build_search_library_tool(
    session: AsyncSession,
    *,
    dimension: str,
    genre: str | None,
    sub_genre: str | None,
) -> ToolSpec:
    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"error": "empty_query"}
        top_k = int(arguments.get("top_k") or 5)
        filter_dimension = str(arguments.get("dimension") or dimension)
        entries = await query_library(
            session,
            dimension=filter_dimension,
            query=query,
            genre=genre,
            sub_genre=sub_genre,
            top_k=top_k,
        )
        return {
            "query": query,
            "count": len(entries),
            "entries": [_entry_to_preview(e) for e in entries],
        }

    return ToolSpec(
        name="search_library",
        description=(
            "Semantic recall against the existing material library so the "
            "researcher can avoid duplicating entries the library already has."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "dimension": {
                    "type": "string",
                    "description": (
                        "Override the dimension to search. Defaults to the "
                        "dimension being populated."
                    ),
                },
            },
            "required": ["query"],
        },
        handler=handler,
    )


def _entry_to_preview(entry: MaterialEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "dimension": entry.dimension,
        "slug": entry.slug,
        "name": entry.name,
        "narrative_summary": entry.narrative_summary,
        "genre": entry.genre,
        "sub_genre": entry.sub_genre,
        "usage_count": entry.usage_count,
    }


def _build_emit_tool(
    session: AsyncSession,
    *,
    dimension: str,
    genre: str | None,
    sub_genre: str | None,
    taboos: Sequence[str],
    outcome_box: list[MaterialEntry],
    rejected_box: list[tuple[str, str]],
    target_count: int,
) -> ToolSpec:
    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            entry = _coerce_emit_arguments(
                arguments,
                dimension=dimension,
                default_genre=genre,
                default_sub_genre=sub_genre,
            )
        except ValueError as exc:
            return {"error": f"validation:{exc}"}

        taboo_hit = _entry_hits_taboo(entry, taboos)
        if taboo_hit:
            rejected_box.append((entry.slug, taboo_hit))
            return {
                "error": "taboo_hit",
                "pattern": taboo_hit,
                "hint": "Rename/rephrase the entry and resubmit.",
            }

        saved = await insert_entry(session, entry)
        outcome_box.append(saved)
        remaining = max(target_count - len(outcome_box), 0)
        return {
            "status": "ok",
            "entry_id": saved.id,
            "dimension": saved.dimension,
            "slug": saved.slug,
            "remaining_to_emit": remaining,
        }

    return ToolSpec(
        name="emit_entry",
        description=(
            "Persist one material-library entry. Call this once per concrete "
            "result you want in the library. Non-terminal: keep calling until "
            "you reach target_count."
        ),
        parameters={
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "name": {"type": "string"},
                "narrative_summary": {"type": "string"},
                "content_json": {"type": "object"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source_citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "title": {"type": "string"},
                        },
                    },
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "coverage_score": {"type": "number", "minimum": 0, "maximum": 1},
                "dimension": {"type": "string"},
                "genre": {"type": "string"},
                "sub_genre": {"type": "string"},
            },
            "required": ["slug", "name", "narrative_summary", "content_json"],
        },
        handler=handler,
    )


def _coerce_emit_arguments(
    raw: dict[str, Any],
    *,
    dimension: str,
    default_genre: str | None,
    default_sub_genre: str | None,
) -> MaterialEntry:
    """Turn an ``emit_entry`` tool-call payload into a :class:`MaterialEntry`.

    Raises ``ValueError`` on schema violations so the handler can surface
    a ``{"error": "validation:..."}`` payload back to the model.
    """

    slug = str(raw.get("slug") or "").strip().lower()
    if not _slug_looks_valid(slug):
        raise ValueError(f"invalid_slug:{slug!r}")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("missing_name")
    summary = str(raw.get("narrative_summary") or "").strip()
    if not summary:
        raise ValueError("missing_narrative_summary")
    content_raw = raw.get("content_json")
    if content_raw is None:
        raise ValueError("missing_content_json")
    if isinstance(content_raw, str):
        try:
            content_json = json.loads(content_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"content_json_not_json:{exc}") from exc
    else:
        content_json = dict(content_raw)

    tags_raw = raw.get("tags") or []
    if not isinstance(tags_raw, list):
        raise ValueError("tags_not_list")
    tags = [str(t) for t in tags_raw]

    citations_raw = raw.get("source_citations") or []
    if not isinstance(citations_raw, list):
        raise ValueError("source_citations_not_list")
    citations: list[dict[str, Any]] = []
    for item in citations_raw:
        if isinstance(item, dict):
            citations.append({k: str(v) for k, v in item.items()})
        elif isinstance(item, str):
            citations.append({"url": item})

    confidence = _clamp_float(raw.get("confidence"), 0.0)
    coverage_score = _clamp_float(raw.get("coverage_score"), None)

    return MaterialEntry(
        dimension=str(raw.get("dimension") or dimension),
        slug=slug,
        name=name,
        narrative_summary=summary,
        content_json=content_json,
        genre=str(raw.get("genre")) if raw.get("genre") else default_genre,
        sub_genre=(
            str(raw.get("sub_genre")) if raw.get("sub_genre") else default_sub_genre
        ),
        tags=tags,
        source_type="research_agent",
        source_citations=citations,
        confidence=confidence or 0.0,
        coverage_score=coverage_score,
    )


def _clamp_float(value: Any, default: float | None) -> float | None:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


# ── Entry point ────────────────────────────────────────────────────────


async def run_research(
    session: AsyncSession,
    settings: AppSettings,
    *,
    dimension: str,
    genre: str | None,
    sub_genre: str | None = None,
    target_count: int = 10,
    skills: Sequence[ResearchSkill] | None = None,
    search_client: WebSearchClient | None = None,
    extra_tools: Sequence[ToolSpec] | None = None,
    max_rounds: int = 10,
    tool_choice: str | dict[str, Any] | None = "auto",
    logical_role: str = "planner",
) -> ResearchOutcome:
    """Run the tool-use loop that fills the library for a bucket.

    Parameters
    ----------
    session:
        Active async session used for ``material_library`` reads/writes.
    settings:
        Global :class:`AppSettings` (drives LLM role choice + telemetry).
    dimension / genre / sub_genre:
        Bucket being populated.
    target_count:
        How many entries the agent is nominally aiming for.  Soft budget —
        the loop may emit fewer on a sparse genre.
    skills:
        Pre-resolved skills; if ``None`` they are loaded via
        :func:`load_skills_for_genre`.
    search_client:
        Web-search client.  Defaults to :class:`NoopSearchClient` — the
        pipeline still runs (useful for tests + offline dev).
    extra_tools:
        Extra :class:`ToolSpec` to register, typically MCP tools emitted
        by :meth:`MCPConnectionPool.as_tool_specs`.
    max_rounds:
        Hard cap on LLM round-trips.
    logical_role:
        Role passed to :class:`LLMCompletionRequest` — ``"planner"`` by
        default (research is a planning-shaped task in this app's LLM
        role taxonomy).
    """

    resolved_skills = list(
        skills
        if skills is not None
        else load_skills_for_genre(genre or "", sub_genre)
    )
    taboos = _collect_taboos(resolved_skills)
    search = search_client or NoopSearchClient()

    outcome_box: list[MaterialEntry] = []
    rejected_box: list[tuple[str, str]] = []

    registry = ToolRegistry(
        [
            _build_search_library_tool(
                session,
                dimension=dimension,
                genre=genre,
                sub_genre=sub_genre,
            ),
            _build_search_web_tool(search),
            _build_emit_tool(
                session,
                dimension=dimension,
                genre=genre,
                sub_genre=sub_genre,
                taboos=taboos,
                outcome_box=outcome_box,
                rejected_box=rejected_box,
                target_count=target_count,
            ),
        ]
    )
    for extra in extra_tools or ():
        registry.register(extra)

    base_request = LLMCompletionRequest(
        logical_role=logical_role,  # type: ignore[arg-type]
        system_prompt=_SYSTEM_INSTRUCTIONS,
        user_prompt=_build_user_prompt(
            dimension=dimension,
            genre=genre,
            sub_genre=sub_genre,
            target_count=target_count,
            skills=resolved_skills,
            taboos=taboos,
        ),
        fallback_response="{}",
        prompt_template="research_agent",
        prompt_version="1.0",
        metadata={
            "agent": "research_agent",
            "dimension": dimension,
            "genre": genre or "",
            "sub_genre": sub_genre or "",
            "target_count": target_count,
        },
    )

    loop_result = await run_tool_loop(
        session,
        settings,
        base_request=base_request,
        registry=registry,
        max_rounds=max_rounds,
        tool_choice=tool_choice,
    )

    return ResearchOutcome(
        dimension=dimension,
        genre=genre,
        sub_genre=sub_genre,
        target_count=target_count,
        emitted=tuple(outcome_box),
        rejected_taboos=tuple(rejected_box),
        tool_trace=tuple(loop_result.trace),
        rounds=loop_result.rounds,
        exit_reason=loop_result.exit_reason,
    )


__all__ = [
    "ResearchOutcome",
    "run_research",
]
