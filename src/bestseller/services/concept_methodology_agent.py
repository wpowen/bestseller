"""Agent ①: heat-search → 脑洞/爽点 *methodology* selection.

The framework used to inject a *baked concrete result* (a default creative
direction + an auto-generated concept-lab bundle + a derived hook_spec) into
planning, which pinned the model to a preset that frequently mismatched the
genre.  This agent replaces that with a **methodology selection**: given the
genre + premise (+ optional reader-orientation), it consults market-heat
signals (live search when available, otherwise a static market profile) and
asks the planner LLM to choose *which brainstorm mindset and 爽点 mechanism
types* fit best — never a concrete instance.  The model then grows the actual
hook/world/rules itself from that methodology.

Design mirrors :func:`derive_ideology_kernel`: fallback-safe (any failure
returns a deterministic methodology built from the genre preset + static
profile), so it never blocks an autonomous run.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.progress_context import emit_activity, emit_milestone
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)


# ── Contract ──────────────────────────────────────────────────────────────


class ConceptMethodology(BaseModel, frozen=True):
    """A *methodology selection* — not a concrete brainstorm result.

    Everything here is a direction/type the model must transform into a
    genre-fitting, original mechanism; nothing is a ready-made instance.
    """

    audience_orientation: str = "neutral"  # male | female | neutral
    mindset: str = ""  # the chosen 脑洞思维, e.g. "规则面板生存"
    mechanism_types: list[str] = Field(default_factory=list)  # 爽点/脑洞 *types*
    reader_promise_axis: str = ""  # the reader-promise direction
    shuangdian_cadence: list[str] = Field(default_factory=list)  # 爽点节奏 (beats)
    design_axes: list[str] = Field(default_factory=list)  # escalation/pressure axes
    anti_patterns: list[str] = Field(default_factory=list)
    market_signals: list[str] = Field(default_factory=list)  # heat/static evidence
    rationale: str = ""
    source: str = "static_fallback"  # llm | static_fallback


# ── Static market-heat fallback ─────────────────────────────────────────────

# Heuristic genre → seed market profile (config/market_profiles/fanqie/*.yaml).
# Keyed on substrings found in genre / sub_genre text. Best-effort only; when
# nothing matches we fall back to the genre preset's own trend signals.
_PROFILE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("xuanhuan-brain", ("玄幻", "仙侠", "修真", "修仙", "奇幻", "fantasy", "cultivation")),
    ("urban-high-martial", ("高武", "都市异能", "异能", "martial")),
    ("urban-brain", ("都市", "职场", "现实", "urban", "city")),
    ("suspense-brain", ("悬疑", "推理", "探案", "惊悚", "无限", "suspense", "mystery", "thriller")),
    ("modern-romance-brain", ("言情", "恋爱", "romance", "甜宠", "虐恋", "情感", "女频")),
)


def _match_market_profile_key(genre: str, sub_genre: str, genre_key: str) -> str | None:
    haystack = " ".join((genre or "", sub_genre or "", genre_key or "")).lower()
    for profile_key, needles in _PROFILE_KEYWORDS:
        for needle in needles:
            if needle.lower() in haystack:
                return profile_key
    return None


# Signals matching these markers are the very micro-trends the system prompt
# bans as oversaturated ("卡天道/系统漏洞/规则面板/钻规则空子"). Injecting them
# as market-heat signals — or worse, baking them into the deterministic
# fallback's reader_promise_axis/design_axes — contradicts the
# anti-homogenization red line, so they are filtered at the source.
_OVERSATURATED_SIGNAL_MARKERS: tuple[str, ...] = (
    "规则面板",
    "系统面板",
    "系统漏洞",
    "规则漏洞",
    "天道漏洞",
    "天道bug",
    "卡bug",
    "钻规则",
    "规则空子",
)


def _filter_oversaturated_signals(signals: list[str]) -> list[str]:
    return [s for s in signals if not any(m in s for m in _OVERSATURATED_SIGNAL_MARKERS)]


def _static_market_signals(*, genre: str, sub_genre: str, genre_key: str) -> list[str]:
    """Offline market-heat signals from the seed market profile (best-effort)."""

    profile_key = _match_market_profile_key(genre, sub_genre, genre_key)
    if not profile_key:
        return []
    try:
        from bestseller.services.fanqie_seed_profiles import load_fanqie_seed_profile

        payload = load_fanqie_seed_profile(profile_key)
    except Exception:  # pragma: no cover - defensive; never block on profile I/O
        logger.debug("static market profile load failed for %s", profile_key, exc_info=True)
        return []
    signals: list[str] = []
    for field in ("reader_promise", "advantage_patterns", "chapter_loop", "entry_pressure_patterns"):
        for item in payload.get(field) or []:
            text = str(item).strip()
            if text and text not in signals:
                signals.append(text)
    return _filter_oversaturated_signals(signals)[:10]


async def _gather_market_heat(
    *,
    settings: AppSettings,
    genre: str,
    sub_genre: str,
    trend_keywords: list[str],
    search_client: Any | None,
) -> list[str]:
    """Live market-heat search, gated + fallback-safe.

    Returns search-derived signal strings, or [] if disabled/unavailable/failed
    (the caller then uses static signals). Never raises.
    """

    if not getattr(settings.pipeline, "concept_methodology_heat_search", True):
        return []
    own_client = False
    client = search_client
    try:
        if client is None:
            from bestseller.services.search_client import build_search_client

            client = build_search_client()
            own_client = True
        # A Noop client (no API key) returns nothing — skip straight to static.
        if getattr(client, "provider", "noop") == "noop":
            return []
        query_terms = [genre, sub_genre, *(trend_keywords or [])]
        query = " ".join(t for t in query_terms if t).strip() or genre
        query = f"{query} 网文 爽点 热门 趋势"
        response = await client.search(query, max_results=6)
        signals: list[str] = []
        for hit in getattr(response, "hits", ()):  # SearchHit
            text = (getattr(hit, "title", "") or "").strip()
            snippet = (getattr(hit, "snippet", "") or "").strip()
            combined = f"{text}：{snippet[:80]}" if snippet else text
            if combined and combined not in signals:
                signals.append(combined)
        return signals[:8]
    except Exception:  # pragma: no cover - network/provider variance
        logger.debug("market-heat search failed; using static fallback", exc_info=True)
        return []
    finally:
        if own_client and client is not None:
            try:
                await client.close()
            except Exception:  # pragma: no cover
                pass


# ── Deterministic fallback ──────────────────────────────────────────────────


def _orientation_from_audiences(audiences: list[str]) -> str:
    joined = " ".join(audiences or [])
    has_male = "男频" in joined or "男生" in joined or "male" in joined.lower()
    has_female = "女频" in joined or "女生" in joined or "female" in joined.lower()
    if has_male and not has_female:
        return "male"
    if has_female and not has_male:
        return "female"
    return "neutral"


def fallback_concept_methodology(
    *,
    genre: str,
    sub_genre: str,
    genre_key: str,
    audience_orientation: str,
    recommended_audiences: list[str] | None = None,
    trend_keywords: list[str] | None = None,
    market_signals: list[str] | None = None,
) -> ConceptMethodology:
    """Deterministic methodology from the genre preset + static profile."""

    orientation = (audience_orientation or "").strip().lower()
    if orientation not in {"male", "female", "neutral"}:
        orientation = _orientation_from_audiences(recommended_audiences or [])
    signals = _filter_oversaturated_signals(
        list(market_signals or [])
    ) or _static_market_signals(genre=genre, sub_genre=sub_genre, genre_key=genre_key)
    keywords = [k for k in (trend_keywords or []) if k][:6]
    mechanism_types = keywords[:4] or ["反差/反转", "信息差", "代价绑定回报", "递进升级"]
    return ConceptMethodology(
        audience_orientation=orientation,
        mindset=f"{genre}核心爽感引擎（按题材自然生长）",
        mechanism_types=mechanism_types,
        reader_promise_axis=(signals[0] if signals else f"{genre}读者的核心期待"),
        shuangdian_cadence=[
            "开篇即建立反常识/反差压力",
            "每章给一次可见回报并同时付出代价",
            "章末抛出更高一层的钩子",
        ],
        design_axes=signals[:4] or ["压力升级", "信息揭示", "关系/实力跃迁"],
        anti_patterns=["大段世界观开篇", "回报无代价", "套路化金手指"],
        market_signals=signals,
        rationale="无可用模型输出，按题材趋势关键词 + 静态市场画像生成的兜底方法论。",
        source="static_fallback",
    )


# ── LLM prompts ─────────────────────────────────────────────────────────────


def build_methodology_system_prompt(*, language: str = "zh") -> str:
    if str(language or "").startswith("en"):
        return (
            "You are a web-fiction market analyst + story methodologist. "
            "Given a genre and market-heat signals, choose a brainstorm *mindset* "
            "and *types* of reader-reward mechanisms that are BOTH popular AND "
            "differentiated for this genre. "
            "CRITICAL anti-homogenization rule: do NOT default to the single most "
            "oversaturated micro-trend the genre is currently flooded with — that only "
            "makes your book collide with dozens of others. (E.g. if you find yourself "
            "again writing 'exploit-the-Heavenly-Dao / system-loophole / rule-panel' for "
            "xianxia/xuanhuan, switch to a still-hot but un-saturated angle — emotional, "
            "relational, vocational, world-texture, thematic, anti-trope, etc.) Your pick "
            "should look visibly different from recent new books in this genre. "
            "Output a methodology, NOT a concrete plot. "
            "Never name a specific gimmick or borrow a source work's setting — only "
            "directions the writer must transform into an original, cost-bearing, "
            "escalating mechanism. Respond with a single valid JSON object, no prose."
        )
    return (
        "你是网文市场分析师 + 故事方法论专家。给定题材与市场热度信号，"
        "你要为该题材选出一个【既有市场热度、又有差异化空间】的「脑洞思维」和「爽点机制类型」。"
        "⚠️同质化红线（最重要）：绝不要默认选该题材当前最泛滥、已被大量新书反复套用、"
        "读者已审美疲劳的那一个『最热』微创新——那只会让你这本书和市面上一堆书撞车。"
        "举例：一旦你发现自己又想给 玄幻/修仙 写『卡天道/天道bug/系统漏洞/规则面板/钻规则空子』"
        "这类已被写烂的方向，必须主动换一个仍有热度但尚未同质化的角度（情感、关系、职业、"
        "世界质感、母题、反类型…都可以）。判断标准：把你选的方向和该题材近期新书放一起，"
        "应当有肉眼可见的差异，而不是又一本同款。"
        "你输出的是方法论（思维方向 + 机制类型 + 节奏轴），不是具体剧情，"
        "更不是某个固定金手指。绝不照搬任何源作品的设定或专属名词；"
        "你给的每一项都必须是写手可以在该题材里重新长出「可执行、可付代价、可升级」"
        "原创机制的方向。只输出一个合法 JSON 对象，不要解释。"
    )


def build_methodology_user_prompt(
    *,
    genre: str,
    sub_genre: str,
    description: str,
    premise: str,
    audience_orientation: str,
    market_signals: list[str],
    trend_keywords: list[str],
    fallback: ConceptMethodology,
    language: str = "zh",
) -> str:
    orientation_label = {
        "male": "男频（男性向）",
        "female": "女频（女性向）",
        "neutral": "未指定（请你根据题材+热度自行判定男频/女频/通杀）",
    }.get((audience_orientation or "neutral").lower(), "未指定")
    signals_block = "\n".join(f"- {s}" for s in market_signals[:10]) or "（无联网信号，依据题材常识判断）"
    schema = {
        "audience_orientation": "male | female | neutral",
        "mindset": "一句话：该题材当前【吃香且有差异化空间】的脑洞思维方向（避开已被写烂的最热微创新，别和市面同题材新书撞款）",
        "mechanism_types": ["爽点/脑洞机制类型（类型词，非具体实例）", "..."],
        "reader_promise_axis": "读者承诺主轴（方向）",
        "shuangdian_cadence": ["爽点节奏：每个节拍一句方法", "..."],
        "design_axes": ["递进/压力设计轴", "..."],
        "anti_patterns": ["该题材要避免的套路", "..."],
        "rationale": "为什么这套方法论吻合题材+市场热度",
    }
    if str(language or "").startswith("en"):
        return (
            f"Genre: {genre} ({sub_genre})\n"
            f"Description: {description}\n"
            f"Premise: {premise}\n"
            f"Reader orientation: {orientation_label}\n\n"
            f"Market-heat signals:\n{signals_block}\n\n"
            "Return JSON with exactly these keys (values illustrate intent):\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )
    return (
        f"题材：{genre}（{sub_genre}）\n"
        f"简介：{description}\n"
        f"前提：{premise}\n"
        f"读者取向：{orientation_label}\n\n"
        f"市场热度信号：\n{signals_block}\n\n"
        "请输出 JSON，键固定如下（值仅示意，请替换为贴合本题材的内容）：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S).strip()
    candidates = [stripped, unfenced]
    match = re.search(r"\{.*\}", unfenced, flags=re.S)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return {}


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def parse_concept_methodology(
    content: str, *, fallback: ConceptMethodology
) -> ConceptMethodology:
    data = _parse_json_object(content)
    if not data:
        return fallback
    orientation = str(data.get("audience_orientation") or fallback.audience_orientation).strip().lower()
    if orientation not in {"male", "female", "neutral"}:
        orientation = fallback.audience_orientation
    try:
        return ConceptMethodology(
            audience_orientation=orientation,
            mindset=str(data.get("mindset") or fallback.mindset).strip(),
            mechanism_types=_as_str_list(data.get("mechanism_types")) or fallback.mechanism_types,
            reader_promise_axis=str(
                data.get("reader_promise_axis") or fallback.reader_promise_axis
            ).strip(),
            shuangdian_cadence=_as_str_list(data.get("shuangdian_cadence"))
            or fallback.shuangdian_cadence,
            design_axes=_as_str_list(data.get("design_axes")) or fallback.design_axes,
            anti_patterns=_as_str_list(data.get("anti_patterns")) or fallback.anti_patterns,
            market_signals=fallback.market_signals,
            rationale=str(data.get("rationale") or fallback.rationale).strip(),
            source="llm",
        )
    except Exception:  # pragma: no cover - validation guard
        logger.debug("concept methodology validation failed; using fallback", exc_info=True)
        return fallback


# ── Orchestrator ────────────────────────────────────────────────────────────


async def select_concept_methodology(
    session: AsyncSession,
    settings: AppSettings,
    *,
    genre: str,
    sub_genre: str = "",
    genre_key: str = "",
    description: str = "",
    premise: str = "",
    audience_orientation: str = "",
    recommended_audiences: list[str] | None = None,
    trend_keywords: list[str] | None = None,
    language: str = "zh",
    search_client: Any | None = None,
    project_id: Any | None = None,
    workflow_run_id: Any | None = None,
) -> ConceptMethodology:
    """Choose a 脑洞/爽点 methodology for this genre (fallback-safe)."""

    emit_activity(
        "heat_search_started",
        {"genre": genre, "sub_genre": sub_genre},
    )
    heat_signals = _filter_oversaturated_signals(
        await _gather_market_heat(
            settings=settings,
            genre=genre,
            sub_genre=sub_genre,
            trend_keywords=trend_keywords or [],
            search_client=search_client,
        )
    )
    heat_source = "search"
    if not heat_signals:
        heat_signals = _static_market_signals(
            genre=genre, sub_genre=sub_genre, genre_key=genre_key
        )
        heat_source = "static"
    emit_milestone(
        "heat_search_completed",
        {"count": len(heat_signals or []), "source": heat_source},
    )
    fallback = fallback_concept_methodology(
        genre=genre,
        sub_genre=sub_genre,
        genre_key=genre_key,
        audience_orientation=audience_orientation,
        recommended_audiences=recommended_audiences,
        trend_keywords=trend_keywords,
        market_signals=heat_signals,
    )
    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="planner",
                system_prompt=build_methodology_system_prompt(language=language),
                user_prompt=build_methodology_user_prompt(
                    genre=genre,
                    sub_genre=sub_genre,
                    description=description,
                    premise=premise,
                    audience_orientation=fallback.audience_orientation,
                    market_signals=heat_signals,
                    trend_keywords=trend_keywords or [],
                    fallback=fallback,
                    language=language,
                ),
                fallback_response=json.dumps(
                    fallback.model_dump(mode="json"), ensure_ascii=False
                ),
                prompt_template="concept_methodology",
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                metadata={"artifact": "concept_methodology", "genre": genre},
                max_tokens_override=1800,
            ),
        )
    except Exception:  # pragma: no cover - LLM failure → deterministic fallback
        logger.warning("concept methodology LLM failed; using fallback", exc_info=True)
        return fallback
    return parse_concept_methodology(completion.content, fallback=fallback)


# ── Soft-advisory render ────────────────────────────────────────────────────


def render_concept_methodology_block(
    methodology: ConceptMethodology | None, *, language: str = "zh"
) -> str:
    """Render as a soft methodology framework — never a hard contract."""

    if methodology is None:
        return ""
    is_en = str(language or "").startswith("en")
    payload = {
        "audience_orientation": methodology.audience_orientation,
        "mindset": methodology.mindset,
        "mechanism_types": methodology.mechanism_types,
        "reader_promise_axis": methodology.reader_promise_axis,
        "shuangdian_cadence": methodology.shuangdian_cadence,
        "design_axes": methodology.design_axes,
        "anti_patterns": methodology.anti_patterns,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if is_en:
        return (
            "[Concept Methodology — soft framework]\n"
            "Use this as a *direction*, not a template. Grow your own original, "
            "cost-bearing, escalating mechanisms that fit this genre and protagonist; "
            "do NOT copy these type-words verbatim.\n"
            f"{body}\n"
        )
    return (
        "【脑洞/爽点方法论 — 软框架（参考，非硬合同）】\n"
        "把它当作思维方向，而不是套路模板。请基于本题材与主角，长出你自己的"
        "「可执行、可付代价、可升级」的原创机制；不要照抄这些类型词，更不要稀释成普通题材说明。\n"
        f"{body}\n"
    )


__all__ = [
    "ConceptMethodology",
    "build_methodology_system_prompt",
    "build_methodology_user_prompt",
    "fallback_concept_methodology",
    "parse_concept_methodology",
    "render_concept_methodology_block",
    "select_concept_methodology",
]
