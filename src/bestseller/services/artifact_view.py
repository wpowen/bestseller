"""Per-type readable view-models for planning artifacts (design dossier).

The design dossier used to dump each artifact as raw JSON; a generic key/value
renderer was still an overwhelming wall (a cast protagonist alone carries ~38
fields). This module turns each artifact type into a **curated, methodology-
grounded view-model** — a list of typed "blocks" (prose / callout / fields /
cards / chips / ladder) that foreground the few dimensions that matter for that
artifact's role in the framework, and leave the rest to a raw-JSON fold on the
frontend.

The field selection IS the methodology: which dimensions of a world_spec /
cast_spec / book_spec / kernel a human should read to judge the design. Unknown
types fall back to a curated generic view (scalars as fields, object-lists as
cards, skipping provenance noise) so coverage is comprehensive.

Pure / deterministic / zero-token — unit-testable without a DB.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

# ruff: noqa: ANN401, E501, RUF001 — CJK-dense label specs (wide by display width) + Any content.

SCHEMA_VERSION = "artifact-view.v1"


# --- block builders ---------------------------------------------------------


def _s(v: Any) -> str:
    if v is None or isinstance(v, (dict, list)):
        return "" if v in (None, {}, []) else str(v)
    s = str(v).strip()
    return "" if s.lower() in ("none", "null") else s


def _prose(label: str, text: Any) -> dict[str, Any] | None:
    t = _s(text)
    return {"kind": "prose", "label": label, "text": t} if t else None


def _callout(label: str, text: Any) -> dict[str, Any] | None:
    t = _s(text)
    return {"kind": "callout", "label": label, "text": t} if t else None


def _fields(label: str, obj: Any, specs: Sequence[tuple[str, str]]) -> dict[str, Any] | None:
    if not isinstance(obj, Mapping):
        return None
    items = [{"label": lbl, "value": _s(obj.get(k))} for k, lbl in specs if _s(obj.get(k))]
    return {"kind": "fields", "label": label, "items": items} if items else None


def _chips(label: str, values: Any) -> dict[str, Any] | None:
    if not isinstance(values, Sequence) or isinstance(values, str):
        return None
    chips = []
    for x in values:
        if isinstance(x, Mapping):
            chips.append(_s(x.get("label") or x.get("name") or x.get("type")))
        elif _s(x):
            chips.append(_s(x))
    chips = [c for c in chips if c]
    return {"kind": "chips", "label": label, "chips": chips} if chips else None


def _card_of(
    obj: Any,
    *,
    title_key: str,
    subtitle_key: str | None = None,
    badge: str = "",
    field_specs: Sequence[tuple[str, str]] = (),
    line_keys: Sequence[str] = (),
) -> dict[str, Any] | None:
    if not isinstance(obj, Mapping):
        return {"title": _s(obj), "subtitle": "", "badge": badge, "items": [], "lines": []} if _s(obj) else None
    return {
        "title": _s(obj.get(title_key)) or "—",
        "subtitle": _s(obj.get(subtitle_key)) if subtitle_key else "",
        "badge": badge,
        "items": [{"label": lbl, "value": _s(obj.get(k))} for k, lbl in field_specs if _s(obj.get(k))],
        "lines": [_s(obj.get(k)) for k in line_keys if _s(obj.get(k))],
    }


def _cards(
    label: str,
    items: Any,
    *,
    title_key: str,
    subtitle_key: str | None = None,
    field_specs: Sequence[tuple[str, str]] = (),
    line_keys: Sequence[str] = (),
    limit: int = 12,
    note: str = "",
) -> dict[str, Any] | None:
    if not isinstance(items, Sequence) or isinstance(items, str):
        return None
    cards = []
    for it in items[:limit]:
        card = _card_of(
            it, title_key=title_key, subtitle_key=subtitle_key,
            field_specs=field_specs, line_keys=line_keys,
        )
        if card:
            cards.append(card)
    if not cards:
        return None
    if len(items) > limit:
        note = (note + f"（共 {len(items)} 项，展示前 {limit}）").strip()
    return {"kind": "cards", "label": label, "note": note, "cards": cards}


def _ladder(label: str, ps: Any) -> dict[str, Any] | None:
    if not isinstance(ps, Mapping):
        return None
    tiers = [_s(t) for t in (ps.get("tiers") or []) if _s(t)]
    if not tiers:
        return None
    start = _s(ps.get("protagonist_starting_tier"))
    name = _s(ps.get("name"))
    return {
        "kind": "ladder",
        "label": label + (f" · {name}" if name else ""),
        "steps": [{"name": t, "tag": ("起点" if t == start else "")} for t in tiers],
    }


def _char_card(obj: Any, badge: str, specs: Sequence[tuple[str, str]]) -> dict[str, Any] | None:
    if not isinstance(obj, Mapping):
        return None
    return _card_of(
        obj, title_key="name", subtitle_key="archetype", badge=badge, field_specs=specs
    )


def _wrap_cards(label: str, cards: Sequence[dict[str, Any] | None]) -> dict[str, Any] | None:
    real = [c for c in cards if c]
    return {"kind": "cards", "label": label, "note": "", "cards": real} if real else None


# --- per-type builders ------------------------------------------------------

_PROT_SPECS = [
    ("golden_finger", "金手指"), ("core_motivation", "核心动机"), ("external_goal", "目标"),
    ("core_wound", "核心创伤"), ("fatal_flaw", "致命缺陷"), ("flaw", "缺陷"),
    ("fear", "恐惧"), ("secret", "秘密"), ("arc_trajectory", "人物弧光"),
]
_ANTAG_SPECS = [
    ("goal", "目标"), ("method", "手段"), ("flaw", "软肋"), ("fear", "恐惧"), ("secret", "秘密"),
]


def _b_premise(c: Any) -> list:
    return [_prose("一句话 / 前提", c.get("premise") if isinstance(c, Mapping) else c)]


def _b_book_spec(c: Mapping) -> list:
    se = c.get("series_engine") if isinstance(c.get("series_engine"), Mapping) else {}
    return [
        _prose("一句话", c.get("logline")),
        _callout("核心戏剧问题", c.get("dramatic_question")),
        _prose("独特钩子", c.get("unique_hook")),
        _fields("主角", c.get("protagonist"), [
            ("archetype", "原型"), ("golden_finger", "金手指"), ("core_wound", "核心创伤"),
            ("fatal_flaw", "致命缺陷"), ("external_goal", "外在目标"),
            ("internal_need", "内在需求"), ("contrast", "反差"),
        ]),
        _fields("系列引擎", se, [
            ("core_loop", "核心循环"), ("hook_style", "钩子风格"), ("cost_engine", "代价引擎"),
            ("payoff_rhythm", "爽点节奏"), ("reader_promise", "读者承诺"),
        ]),
        _chips("卖点", se.get("selling_points")),
        _chips("套路标签", se.get("trope_keywords")),
        _fields("叙事线", c.get("narrative_lines"), [
            ("core_axis", "主轴"), ("overt_line", "明线"),
            ("hidden_thread", "暗线"), ("undercurrent_line", "潜流"),
        ]),
        _fields("反派阶梯", c.get("antagonist_ladder"), [
            ("tier_1_local_threats", "一阶·地方"), ("tier_2_regional_factions", "二阶·区域"),
            ("tier_3_global_conspirators", "三阶·全局"), ("tier_4_ultimate_boss", "终极反派"),
        ]),
        _chips("主题", c.get("themes")),
        _prose("主题陈述", c.get("theme_statement")),
    ]


def _b_world_spec(c: Mapping) -> list:
    return [
        _prose("世界前提", c.get("world_premise")),
        _ladder("力量体系", c.get("power_system")),
        _fields("体系机制", c.get("power_system"), [
            ("acquisition_method", "获取方式"), ("hard_limits", "硬性限制"),
        ]),
        _cards("世界规则", c.get("rules"), title_key="rule_name",
               field_specs=[("description", "说明"), ("story_consequence", "剧情后果")], limit=14),
        _cards("势力", c.get("factions"), title_key="name", line_keys=["description"], limit=10),
        _cards("地点", c.get("locations"), title_key="name", line_keys=["description"], limit=10),
        _prose("权力结构", c.get("power_structure")),
        _prose("禁区", c.get("forbidden_zones")),
        _cards("关键历史", c.get("history_key_events"), title_key="event",
               line_keys=["relevance"], limit=10),
    ]


def _b_cast_spec(c: Mapping) -> list:
    return [
        _wrap_cards("主角", [_char_card(c.get("protagonist"), "主角", _PROT_SPECS)]),
        _wrap_cards("反派", [_char_card(c.get("antagonist"), "反派", _ANTAG_SPECS)]),
        _cards("配角", c.get("supporting_cast"), title_key="name", subtitle_key="role",
               field_specs=[("goal", "目标"), ("flaw", "缺陷")], limit=12),
        _cards("反派势力", c.get("antagonist_forces"), title_key="name",
               field_specs=[("threat_description", "威胁"), ("escalation_path", "升级路径")], limit=8),
        _cards("核心冲突", c.get("conflict_map"), title_key="conflict_type",
               field_specs=[("character_a", "一方"), ("character_b", "另一方"), ("trigger_condition", "触发")], limit=8),
    ]


def _b_chapter_outline(c: Mapping) -> list:
    return [
        _cards("章节", c.get("chapters"), title_key="title",
               field_specs=[("goal", "本章目标"), ("hook_type", "钩子类型"), ("tail_hook", "结尾钩子")],
               line_keys=["key_reveals"], limit=30, note=_s(c.get("batch_name"))),
    ]


def _b_volume_plan(c: Any) -> list:
    vols = c if isinstance(c, list) else (c.get("volumes") if isinstance(c, Mapping) else None)
    return [
        _cards("卷", vols, title_key="volume_title", subtitle_key="volume_theme",
               field_specs=[("volume_goal", "本卷目标"), ("volume_climax", "卷高潮"),
                            ("conflict_phase", "冲突阶段"), ("map_function", "地图功能")],
               line_keys=["key_reveals"], limit=20),
    ]


def _b_emotion_kernel(c: Mapping) -> list:
    return [
        _callout("读者情绪承诺", c.get("reader_emotion_promise")),
        _chips("读者在等什么", c.get("primary_reader_waiting")),
        _cards("情绪链", c.get("emotion_chain"), title_key="target_reader_emotion",
               field_specs=[("chapter_range", "章节"), ("reader_waiting_for", "在等"),
                            ("pressure_source", "压力源"), ("payoff_or_aftereffect", "兑现")], limit=12),
        _cards("炸弹契约", c.get("bomb_contracts"), title_key="bomb_type",
               field_specs=[("danger", "危险"), ("countdown", "倒计时"), ("payoff_window", "兑现窗口")], limit=10),
        _cards("共情契约", c.get("empathy_contracts"), title_key="character_key",
               field_specs=[("situation", "处境"), ("fear_or_loss", "恐惧/损失"), ("sensory_entry", "入戏感官")], limit=10),
        _fields("结局质感", c.get("ending_texture_contract"), [
            ("ending_type", "结局类型"), ("core_wish_fulfilled", "核心愿望"), ("theme_answer", "主题回答"),
        ]),
    ]


def _b_entry_kernel(c: Mapping) -> list:
    return [
        _callout("系统承诺", c.get("system_promise")),
        _cards("能力轴", c.get("capability_axes"), title_key="label",
               field_specs=[("meaning", "含义")], limit=10),
        _cards("等级阶梯", c.get("grade_ladders"), title_key="label",
               field_specs=[("promotion_rule", "晋升规则")], line_keys=["levels"], limit=8),
        _cards("入口分类", c.get("taxonomy"), title_key="label",
               field_specs=[("type", "类型")], limit=10),
        _fields("代价模型", c.get("cost_model"), [("hard_rule", "硬规则")]),
    ]


def _b_public_emotion_kernel(c: Mapping) -> list:
    return [
        _cards("目标人群", c.get("target_segments"), title_key="group_label",
               field_specs=[("public_emotion", "公共情绪"), ("unsaid_sentence", "没说出口的话"),
                            ("desired_compensation", "渴望的补偿")], limit=8),
        _cards("情绪桥", c.get("emotion_bridges"), title_key="bridge_type",
               field_specs=[("public_anchor", "公共锚点"), ("reader_payoff", "读者兑现"),
                            ("story_hook", "故事钩子")], limit=8),
    ]


def _b_compliance_kernel(c: Mapping) -> list:
    return [
        _fields("合规", c, [("platform", "平台"), ("risk_level", "风险等级"), ("jurisdiction", "辖区")]),
        _chips("缓解规则", c.get("mitigation_rules")),
        _chips("禁用译名", c.get("forbidden_translations")),
        _prose("合规说明", c.get("compliance_notes")),
    ]


def _b_promo(c: Mapping) -> list:
    return [
        _prose("书名", c.get("title")),
        _prose("简介", c.get("blurb")),
        _chips("标签", c.get("tags")),
        _fields("主角", c.get("protagonist"), [
            ("tagline", "标语"), ("golden_finger", "金手指"), ("goal", "目标"),
        ]),
    ]


def _b_readiness(c: Mapping) -> list:
    rep = c.get("prewrite_readiness_report") if isinstance(c.get("prewrite_readiness_report"), Mapping) else c
    metrics = c.get("metrics") if isinstance(c.get("metrics"), Mapping) else rep.get("metrics")
    return [
        _fields("就绪度", rep, [("score", "评分"), ("passed", "通过")]),
        _chips("已具备能力", [k for k, v in (rep.get("capability_snapshot") or {}).items() if v is True]),
        _chips("尚缺能力", [k for k, v in (rep.get("capability_snapshot") or {}).items() if v is False]),
        _fields("指标", metrics, [
            ("chapter_count", "章数"), ("hook_chapter_count", "钩子章"),
            ("payoff_chapter_count", "爽点章"), ("cost_chapter_count", "代价章"),
            ("ability_chapter_count", "能力章"),
        ]),
        _cards("修复项", rep.get("recommended_repair_actions") or rep.get("warnings"),
               title_key="message", line_keys=["repair_action"], limit=10),
    ]


def _b_world_disclosure(c: Mapping) -> list:
    return [
        _prose("前沿摘要", c.get("frontier_summary")),
        _chips("新揭示规则", c.get("new_rules_revealed")),
        _chips("新地点", c.get("new_locations")),
        _chips("势力动向", c.get("faction_movements")),
    ]


_GRADE_ZH = {"recommend": "推荐", "consider": "可考虑", "pass": "不推荐", "reject": "不推荐"}


def _scorecard(label: str, report: Any) -> dict[str, Any] | None:
    """A {grade,total,dimensions[]} appeal report → a compact scorecard block."""

    if not isinstance(report, Mapping):
        return None
    rows = []
    for d in report.get("dimensions") or []:
        if not isinstance(d, Mapping):
            continue
        rows.append({
            "label": _s(d.get("label")) or _s(d.get("key")),
            "score": d.get("score") if isinstance(d.get("score"), (int, float)) else None,
            "weight": d.get("weight") if isinstance(d.get("weight"), (int, float)) else None,
            "rationale": _s(d.get("rationale")),
        })
    if not rows:
        return None
    total = report.get("total")
    try:
        total = round(float(total), 1) if total is not None else None
    except (TypeError, ValueError):
        total = None
    grade = _GRADE_ZH.get(str(report.get("grade", "")).strip(), _s(report.get("grade")))
    return {"kind": "scorecard", "label": label, "grade": grade, "total": total, "rows": rows}


def _b_story_appeal(c: Mapping) -> list:
    meets = c.get("meets_bar")
    overall = _GRADE_ZH.get(str(c.get("overall_grade", "")).strip(), _s(c.get("overall_grade")))
    summary = []
    if overall:
        summary.append({"label": "综合评级", "value": overall})
    if isinstance(meets, bool):
        summary.append({"label": "是否达标", "value": "达标" if meets else "未达标"})
    prem = c.get("premise") if isinstance(c.get("premise"), Mapping) else {}
    return [
        {"kind": "fields", "label": "综合", "items": summary} if summary else None,
        _scorecard("简介点击力", c.get("blurb")),
        _scorecard("书名", c.get("title")),
        _scorecard("立意 / 故事", c.get("premise")),
        _chips("立意改进建议", prem.get("suggestions")),
    ]


def _b_hook_candidates(c: Any) -> list:
    items = c if isinstance(c, list) else []
    cards = []
    for i, it in enumerate(items[:8]):
        if not isinstance(it, Mapping):
            continue
        spec = it.get("spec") if isinstance(it.get("spec"), Mapping) else {}
        score = it.get("score") if isinstance(it.get("score"), Mapping) else {}
        rank = it.get("combined_rank")
        cards.append({
            "title": _s(spec.get("mechanism_key")) or f"候选 {i + 1}",
            "subtitle": (f"综合排名 {round(float(rank), 3)}" if isinstance(rank, (int, float)) else ""),
            "badge": _s(score.get("verdict")),
            "items": [
                {"label": lbl, "value": _s(spec.get(k))}
                for k, lbl in (("hook_type", "钩子类型"), ("reversal", "核心反转"), ("base_desire", "底层欲望"))
                if _s(spec.get(k))
            ],
            "lines": [_s(spec.get("one_liner"))] if _s(spec.get("one_liner")) else [],
        })
    if not cards:
        return []
    note = f"共 {len(items)} 个候选" if len(items) > 8 else ""
    return [{"kind": "cards", "label": "脑洞钩子候选", "note": note, "cards": cards}]


def _b_commercial_brief(c: Mapping) -> list:
    taboo = list(c.get("taboo_words") or []) + list(c.get("taboo_topics") or [])
    return [
        _callout("读者承诺", c.get("reader_promise")),
        _fields("商业定位", c, [
            ("content_mode", "内容模式"), ("pacing_profile", "节奏"), ("payoff_rhythm", "回报节奏"),
            ("power_system", "力量体系"), ("rival_factions", "对抗势力"),
        ]),
        _chips("卖点", c.get("selling_points")),
        _chips("钩子关键词", c.get("hook_keywords")),
        _chips("假设前提", c.get("assumptions")),
        _chips("敏感词 / 禁忌", taboo),
    ]


def _b_concept_methodology(c: Mapping) -> list:
    return [
        _prose("方法论思路", c.get("mindset")),
        _prose("理由", c.get("rationale")),
        _chips("设计轴", c.get("design_axes")),
        _chips("要避免的套路", c.get("anti_patterns")),
        _chips("市场信号", c.get("market_signals")),
    ]


_BUILDERS: dict[str, Callable[[Any], list]] = {
    "premise": _b_premise,
    "story_appeal": _b_story_appeal,
    "hook_candidates": _b_hook_candidates,
    "commercial_brief": _b_commercial_brief,
    "concept_methodology": _b_concept_methodology,
    "book_spec": _b_book_spec,
    "world_spec": _b_world_spec,
    "cast_spec": _b_cast_spec,
    "chapter_outline_batch": _b_chapter_outline,
    "volume_chapter_outline": _b_chapter_outline,
    "volume_plan": _b_volume_plan,
    "emotion_driven_kernel": _b_emotion_kernel,
    "entry_system_kernel": _b_entry_kernel,
    "public_emotion_kernel": _b_public_emotion_kernel,
    "compliance_boundary_kernel": _b_compliance_kernel,
    "promotional_brief": _b_promo,
    "prewrite_readiness": _b_readiness,
    "fanqie_long_ranking_readiness": _b_readiness,
    "volume_world_disclosure": _b_world_disclosure,
}

_VIEW_META: dict[str, tuple[str, str]] = {
    "premise": ("一句话 & 前提", "这一句能不能让你忍不住点开？"),
    "story_appeal": ("故事吸引力评分", "简介/书名/立意各维度得分与评级，看是否达标、哪一维弱。"),
    "hook_candidates": ("脑洞钩子候选", "候选钩子的反转与排名，看有没有真正新鲜不套路的。"),
    "commercial_brief": ("商业定位简报", "读者承诺/卖点/节奏是否清晰、可执行？"),
    "concept_methodology": ("脑洞方法论", "立意背后的设计思路，以及要避免的套路。"),
    "book_spec": ("设计核 · 书目蓝图", "钩子是否独特、戏剧问题是否成立、爽点引擎是否可持续？"),
    "world_spec": ("世界观", "世界规则是否独特、能否长出剧情？力量体系是不是境界流水账？"),
    "cast_spec": ("人物", "金手指是不是又是系统？主角有没有记忆点与真正软肋？"),
    "chapter_outline_batch": ("章纲", "每章是否有明确目标与结尾钩子？前几章够不够抓人？"),
    "volume_chapter_outline": ("卷 · 章纲", "每章是否有明确目标与结尾钩子？"),
    "volume_plan": ("卷纲", "每卷目标/高潮是否清晰？节奏是否有起伏，别每卷都在赢？"),
    "emotion_driven_kernel": ("情绪驱动内核", "读者在等什么、炸弹与共情契约是否落到具体章节？"),
    "entry_system_kernel": ("金手指 / 入口体系", "能力轴与代价是否清晰、是否拒绝死板面板？"),
    "public_emotion_kernel": ("公共情绪内核", "目标人群'没说出口的话'是否精准、能否引发共鸣？"),
    "compliance_boundary_kernel": ("合规边界", "风险等级与缓解规则是否到位？"),
    "promotional_brief": ("上架 / 推广简报", "书名与简介能否一眼抓人？"),
    "prewrite_readiness": ("开写前就绪度", "是否达标、有无阻断项？"),
    "fanqie_long_ranking_readiness": ("番茄上榜就绪", "钩子/爽点/代价/能力章数是否达标？"),
    "volume_world_disclosure": ("卷 · 世界揭示", "本卷新揭示的规则/地点/势力动向。"),
}

# Provenance / low-signal keys hidden from the curated fallback.
_NOISE_KEYS = frozenset({
    "_meta", "version", "schema_version", "input_hash", "workflow_run_id",
    "reused_artifact_id", "source_step", "finish_reason", "kernel_key",
    "policy_pack_key", "project_slug", "validation_type", "metadata",
    "name_reasoning", "pronoun_set_en", "pronoun_set_zh", "ip_anchor",
    "methodology_lineage", "repair_attempts", "policy_versions",
})


def _humanize(key: str) -> str:
    return key.replace("_", " ").strip()


def _fallback(content: Any) -> list:
    """Curated generic view for unknown types: scalars→fields, dict-lists→cards."""

    blocks: list = []
    if isinstance(content, Mapping):
        scalar_items = []
        for k, v in content.items():
            if k in _NOISE_KEYS:
                continue
            if isinstance(v, Mapping):
                fb = _fields(_humanize(k), v, [(sk, _humanize(sk)) for sk in list(v.keys())[:8]])
                if fb:
                    blocks.append(fb)
            elif isinstance(v, list) and v and isinstance(v[0], Mapping):
                tk = next((sk for sk in ("name", "title", "label", "rule_name", "event") if sk in v[0]), None)
                if tk:
                    cb = _cards(_humanize(k), v, title_key=tk,
                                field_specs=[(sk, _humanize(sk)) for sk in list(v[0].keys())[:4] if sk != tk],
                                limit=12)
                    if cb:
                        blocks.append(cb)
            elif isinstance(v, list):
                cb = _chips(_humanize(k), v)
                if cb:
                    blocks.append(cb)
            elif _s(v):
                scalar_items.append({"label": _humanize(k), "value": _s(v)})
        if scalar_items:
            blocks.insert(0, {"kind": "fields", "label": "概览", "items": scalar_items})
    elif isinstance(content, list) and content and isinstance(content[0], Mapping):
        tk = next((sk for sk in ("name", "title", "label", "volume_title") if sk in content[0]), None)
        if tk:
            cb = _cards("条目", content, title_key=tk,
                        field_specs=[(sk, _humanize(sk)) for sk in list(content[0].keys())[:4] if sk != tk],
                        limit=20)
            if cb:
                blocks.append(cb)
    return [b for b in blocks if b]


def build_artifact_view(artifact_type: str, content: Any) -> dict[str, Any]:
    """Build a curated, methodology-grounded view-model for one artifact.

    Returns ``{schema_version, title, hint, has_spec, sections[]}``. Sections is
    a list of typed blocks the frontend paints uniformly. Falls back to a
    curated generic view for unknown types or when a spec yields nothing, so the
    raw JSON is never the only option.
    """

    meta = _VIEW_META.get(artifact_type)
    title = meta[0] if meta else _humanize(artifact_type)
    hint = meta[1] if meta else ""
    builder = _BUILDERS.get(artifact_type)
    sections: list = []
    if builder is not None:
        try:
            sections = [b for b in builder(content) if b]
        except Exception:
            sections = []
    if not sections:
        sections = _fallback(content)
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": artifact_type,
        "title": title,
        "hint": hint,
        "has_spec": builder is not None,
        "sections": sections,
    }


__all__ = ["SCHEMA_VERSION", "build_artifact_view"]
