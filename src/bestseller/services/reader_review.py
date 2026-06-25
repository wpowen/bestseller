"""Pre-write reader review — translate engineering artifacts into readable story.

The quality blind spot the autonomous pipeline keeps hitting: structural gates
go green once fields are *filled* (often by deterministic enrichment), LLM judge
absolute scores are admitted to be advisory-only, and the one trustworthy signal
(``premise_appeal_arena`` blind pairwise) only ever judged the 200-char blurb —
never the outline, scene cards, or prose. So a hollow / generic / homogenized
book sails through with nothing a human can *read* to catch it.

This module closes that loop **before** prose is committed. It is pure,
deterministic, zero-token: it reads artifacts that already exist (chapters,
scene cards, volume plan, cross-book skeletons) and renders them as a story a
human can skim in reader-view — a 5-screen "审稿台":

  ① logline 对照台      —— build_logline_compare
  ② 引擎体检            —— detect_genericness / classify_golden_finger_type
  ③ 黄金三章分镜        —— render_chapter_storyboard
  ④ 节奏曲线            —— build_rhythm_curve
  ⑤ 跨书同质化表        —— build_sameness_table

``build_reader_review`` is the top-level assembler. The web layer loads DB data
and hands it in; this module owns only the JSON→人话 translation, so every
function here is unit-testable without a database or an LLM.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# ruff: noqa: ANN401, RUF001, RUF002 — Chinese punctuation + Any dict values are intentional.

SCHEMA_VERSION = "reader-review.v1"

# ---------------------------------------------------------------------------
# Genericness heuristics (screen ②) — conservative, marker-gated to avoid the
# false-positive harm seen with bare-taxonomy title detection.
# ---------------------------------------------------------------------------

# Explicit "system-shell" markers: a golden finger is flagged as 系统流 only when
# it names the literal game-UI scaffolding, not merely because it grants power.
_SYSTEM_SHELL_MARKERS: tuple[str, ...] = (
    "系统",
    "面板",
    "属性栏",
    "属性面板",
    "签到",
    "抽奖",
    "商城",
    "兑换",
    "任务奖励",
    "新手大礼包",
    "金币兑换",
    "经验值",
    "升级提示",
    "叮——",
    "宿主",
    "绑定系统",
)

# Stock cultivation ladder — a power system made only of these reads as a
# 境界流水账 (generic progression with no distinctive law).
_STOCK_CULTIVATION_TIERS: tuple[str, ...] = (
    "练气",
    "筑基",
    "金丹",
    "元婴",
    "化神",
    "炼虚",
    "合体",
    "大乘",
    "渡劫",
    "凡人",
    "武者",
    "武师",
    "大武师",
    "宗师",
    "武王",
    "武皇",
    "武尊",
    "武圣",
    "武帝",
)

# Golden-finger archetype catalogue — single source for screen ② label AND
# screen ⑤ sameness column, so the two never drift.
_GOLDEN_FINGER_ARCHETYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("系统流", _SYSTEM_SHELL_MARKERS),
    ("重生流", ("重生", "重活", "回到过去", "前世记忆", "重来一世")),
    ("穿越流", ("穿越", "穿书", "魂穿", "异世界")),
    ("老爷爷流", ("老爷爷", "戒指里", "传承", "上古大能", "神秘老者")),
    ("血脉觉醒流", ("血脉觉醒", "觉醒血脉", "上古血脉", "神血", "返祖")),
    ("读心/预知流", ("读心", "心声", "预知", "未来", "天机", "因果之眼", "命运")),
    ("签到/打卡流", ("签到", "打卡", "日常奖励")),
    ("模拟器流", ("模拟器", "推演", "人生模拟", "无限轮回")),
)


def classify_golden_finger_type(text: str | None) -> str:
    """Categorize a golden finger into a stock archetype, else '原创'.

    Used by both the engine-health screen and the cross-book sameness column.
    """

    s = (text or "").strip()
    if not s:
        return "未知"
    for label, markers in _GOLDEN_FINGER_ARCHETYPES:
        if any(m in s for m in markers):
            return label
    return "原创"


def _power_tiers(power_system: Mapping[str, Any] | str | None) -> list[str]:
    if isinstance(power_system, str):
        return [t.strip() for t in power_system.replace("、", " ").split() if t.strip()]
    if isinstance(power_system, Mapping):
        tiers = power_system.get("tiers")
        if isinstance(tiers, Sequence) and not isinstance(tiers, str):
            return [str(t).strip() for t in tiers if str(t).strip()]
    return []


def detect_genericness(
    golden_finger: str | None,
    power_system: Mapping[str, Any] | str | None = None,
    *,
    concept_text: str | None = None,
) -> dict[str, Any]:
    """Screen ② — smell-test the book's engine for default-套路.

    Returns flags + human-readable reasons. Soft/advisory: this *surfaces* the
    smell for a human to judge; it never blocks.

    ``concept_text`` (premise / logline) is a fallback classification source:
    real books often leave the structured ``golden_finger`` field empty while
    the system/套路 lives in the premise prose (e.g. a "福报结算系统" stated only
    in the logline). When the golden-finger field is blank we classify off the
    concept text so the system flag still fires.
    """

    gf_text = (golden_finger or "").strip()
    inferred_from_concept = not gf_text and bool((concept_text or "").strip())
    classify_source = gf_text or (concept_text or "")
    gf_type = classify_golden_finger_type(classify_source)
    flags: list[dict[str, str]] = []

    if gf_type == "系统流":
        flags.append(
            {
                "code": "GOLDEN_FINGER_IS_SYSTEM",
                "severity": "high",
                "reason": "金手指本质是「系统/面板」——最烂大街的形态，去同质化的头号红线。",
            }
        )
    elif gf_type in ("重生流", "穿越流", "签到/打卡流"):
        flags.append(
            {
                "code": "GOLDEN_FINGER_STOCK_ARCHETYPE",
                "severity": "medium",
                "reason": f"金手指落在常见套路「{gf_type}」，需确认有没有真正的差异化新意。",
            }
        )

    tiers = _power_tiers(power_system)
    if tiers:
        stock_hits = [t for t in tiers if any(s in t for s in _STOCK_CULTIVATION_TIERS)]
        if len(stock_hits) >= 3:
            flags.append(
                {
                    "code": "POWER_SYSTEM_STOCK_LADDER",
                    "severity": "medium",
                    "reason": (
                        "力量体系是「练气筑基金丹…」式的境界流水账，"
                        "没有从世界规律长出的独特法则。"
                    ),
                }
            )

    return {
        "golden_finger_type": gf_type,
        "golden_finger_text": gf_text,
        "inferred_from_concept": inferred_from_concept,
        "power_tiers": tiers,
        "flags": flags,
        "is_generic": bool(flags),
    }


# ---------------------------------------------------------------------------
# Scene-card → readable storyboard (screen ③)
# ---------------------------------------------------------------------------


def _stringify_dict(value: Any, *, sep: str = "；", limit: int = 3) -> str:
    """Render a {dimension: desc} / state dict as readable 'k：v' pairs."""

    if isinstance(value, Mapping):
        parts = [
            f"{str(k).strip()}：{str(v).strip()}"
            for k, v in value.items()
            if str(v).strip() and str(v).strip().lower() not in ("none", "null")
        ]
        return sep.join(parts[:limit])
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence):
        parts = [str(v).strip() for v in value if str(v).strip()]
        return sep.join(parts[:limit])
    return ""


def render_scene_beat(scene: Mapping[str, Any], index: int) -> dict[str, Any]:
    """One scene card → a 2–3 sentence 'this scene happens, then this hook'.

    Deterministic templating from existing fields — no LLM. Empty scene cards
    render as an explicit '空壳场景卡' marker so hollowness is visible, not hidden.
    """

    title = str(scene.get("title") or "").strip()
    time_label = str(scene.get("time_label") or "").strip()
    participants = [str(p).strip() for p in (scene.get("participants") or []) if str(p).strip()]
    purpose = _stringify_dict(scene.get("purpose"))
    dialogue = [str(b).strip() for b in (scene.get("key_dialogue_beats") or []) if str(b).strip()]
    exit_state = _stringify_dict(scene.get("exit_state"))
    hook = str(scene.get("hook_requirement") or "").strip()

    cast = "、".join(participants) if participants else "（未指定出场人物）"
    where_when = f"{time_label}，" if time_label else ""
    body = purpose or "（场景目的为空）"

    sentences = [f"{where_when}{cast}。{body}。"]
    if dialogue:
        sentences.append(f"对话焦点：{dialogue[0]}。")
    if exit_state:
        sentences.append(f"收场后：{exit_state}。")

    hollow = not (purpose or dialogue or exit_state)
    return {
        "scene_number": scene.get("scene_number", index + 1),
        "title": title or f"场景 {index + 1}",
        "scene_type": str(scene.get("scene_type") or "").strip(),
        "readable": " ".join(sentences),
        "hook": hook,
        "is_hollow": hollow,
        "participants": participants,
    }


def render_chapter_storyboard(
    chapter: Mapping[str, Any],
    scenes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """A chapter → ordered readable beats + a reader-facing question."""

    beats = [render_scene_beat(s, i) for i, s in enumerate(scenes)]
    hook_desc = str(chapter.get("hook_description") or "").strip()
    cliff = str(chapter.get("ending_cliff_type") or "").strip()
    hollow_count = sum(1 for b in beats if b["is_hollow"])

    if not hook_desc and not cliff:
        reader_question = "这一章读完没有任何结尾钩子——你会想翻下一章吗？"
    else:
        reader_question = "读到这一章结尾，你想翻下一页吗？"

    return {
        "chapter_number": chapter.get("chapter_number"),
        "title": str(chapter.get("title") or "").strip(),
        "goal": str(chapter.get("chapter_goal") or "").strip(),
        "opening_situation": str(chapter.get("opening_situation") or "").strip(),
        "opening_archetype": str(chapter.get("opening_archetype") or "").strip(),
        "main_conflict": str(chapter.get("main_conflict") or "").strip(),
        "ending_hook": hook_desc,
        "ending_cliff_type": cliff,
        "primary_emotion": str(chapter.get("primary_emotion") or "").strip(),
        "hype_type": str(chapter.get("hype_type") or "").strip(),
        "beats": beats,
        "hollow_scene_count": hollow_count,
        "scene_count": len(beats),
        "reader_question": reader_question,
    }


# ---------------------------------------------------------------------------
# Cross-book sameness (screen ⑤)
# ---------------------------------------------------------------------------

# Column key → human label. Stable order = table column order.
_SAMENESS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("protagonist_archetype", "主角原型"),
    ("golden_finger_type", "金手指类型"),
    ("opening_archetype", "开篇原型"),
    ("power_system_signature", "力量体系"),
    ("ch1_hook_type", "首章钩子"),
)


def _book_skeleton_row(book: Mapping[str, Any]) -> dict[str, Any]:
    gf_type = book.get("golden_finger_type") or classify_golden_finger_type(
        book.get("golden_finger")
    )
    return {
        "slug": str(book.get("slug") or "").strip(),
        "title": str(book.get("title") or "").strip(),
        "protagonist_archetype": str(book.get("protagonist_archetype") or "").strip() or "（空）",
        "golden_finger_type": str(gf_type or "").strip() or "（空）",
        "opening_archetype": str(book.get("opening_archetype") or "").strip() or "（空）",
        "power_system_signature": str(book.get("power_system_signature") or "").strip() or "（空）",
        "ch1_hook_type": str(book.get("ch1_hook_type") or "").strip() or "（空）",
    }


def build_sameness_table(
    books: Sequence[Mapping[str, Any]],
    *,
    current_slug: str | None = None,
) -> dict[str, Any]:
    """Screen ⑤ — put this book's 套路骨架 next to recent books; flag duplicates.

    A column value is 'repeated' (highlighted) when ≥2 of the books share it
    (ignoring the '（空）' placeholder). Makes structural homogenization a table
    you can see, not a feeling.
    """

    rows = [_book_skeleton_row(b) for b in books]
    columns = [{"key": k, "label": label} for k, label in _SAMENESS_COLUMNS]

    repeated: dict[str, list[str]] = {}
    for key, _label in _SAMENESS_COLUMNS:
        counts: dict[str, int] = {}
        for row in rows:
            val = row[key]
            if val and val != "（空）":
                counts[val] = counts.get(val, 0) + 1
        repeated[key] = sorted(v for v, n in counts.items() if n >= 2)

    # Per-cell highlight flag + per-current-book sameness score.
    for row in rows:
        row["repeated_cells"] = [
            key
            for key, _ in _SAMENESS_COLUMNS
            if row[key] in repeated[key] and row[key] != "（空）"
        ]
        row["is_current"] = bool(current_slug) and row["slug"] == current_slug

    current_row = next((r for r in rows if r.get("is_current")), None)
    sameness_score = (
        round(len(current_row["repeated_cells"]) / len(_SAMENESS_COLUMNS), 3)
        if current_row
        else 0.0
    )

    return {
        "columns": columns,
        "rows": rows,
        "repeated_values": repeated,
        "current_sameness_score": sameness_score,
        "book_count": len(rows),
    }


# ---------------------------------------------------------------------------
# Logline compare (screen ①) & rhythm curve (screen ④)
# ---------------------------------------------------------------------------


def build_logline_compare(
    *,
    premise: str | None,
    synopsis: str | None,
    reference_blurbs: Sequence[Mapping[str, Any]] | None = None,
    arena_win_rate: float | None = None,
) -> dict[str, Any]:
    refs = [
        {"title": str(r.get("title") or "").strip(), "blurb": str(r.get("blurb") or "").strip()}
        for r in (reference_blurbs or [])
        if str(r.get("blurb") or "").strip()
    ]
    return {
        "premise": (premise or "").strip(),
        "synopsis": (synopsis or "").strip(),
        "reference_blurbs": refs[:3],
        "arena_win_rate": arena_win_rate,
        "arena_verdict": (
            None
            if arena_win_rate is None
            else ("competitive" if arena_win_rate >= 0.45 else "below_bar")
        ),
    }


def build_rhythm_curve(chapters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Screen ④ — per-chapter hype / emotion timeline so塌方/无爽点 is visible."""

    points = []
    hype_present = 0
    for ch in chapters:
        intensity = ch.get("hype_intensity")
        try:
            intensity_val = float(intensity) if intensity is not None else None
        except (TypeError, ValueError):
            intensity_val = None
        if intensity_val:
            hype_present += 1
        points.append(
            {
                "chapter_number": ch.get("chapter_number"),
                "hype_type": str(ch.get("hype_type") or "").strip(),
                "hype_intensity": intensity_val,
                "primary_emotion": str(ch.get("primary_emotion") or "").strip(),
            }
        )
    total = len(points)
    return {
        "points": points,
        "chapter_count": total,
        "hype_coverage": round(hype_present / total, 3) if total else 0.0,
    }


# ---------------------------------------------------------------------------
# Top-level assembler
# ---------------------------------------------------------------------------


def build_reader_review(
    *,
    project: Mapping[str, Any] | None = None,
    golden_finger: str | None = None,
    concept_text: str | None = None,
    power_system: Mapping[str, Any] | str | None = None,
    golden_three_chapters: (
        Sequence[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]] | None
    ) = None,
    rhythm_chapters: Sequence[Mapping[str, Any]] | None = None,
    reference_blurbs: Sequence[Mapping[str, Any]] | None = None,
    arena_win_rate: float | None = None,
    cross_book_skeletons: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the 5-screen reader-review payload from pre-loaded inputs.

    Every argument is optional; missing inputs render as an empty-but-stable
    screen (no-op safety), so the page degrades gracefully on a half-planned
    book and an empty call is byte-stable.
    """

    proj = dict(project or {})
    screen3 = [
        render_chapter_storyboard(ch, scenes) for ch, scenes in (golden_three_chapters or [])
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {
            "slug": str(proj.get("slug") or "").strip(),
            "title": str(proj.get("title") or "").strip(),
            "genre": str(proj.get("genre") or "").strip(),
            "sub_genre": str(proj.get("sub_genre") or "").strip(),
        },
        "screen1_logline": build_logline_compare(
            premise=proj.get("premise"),
            synopsis=proj.get("synopsis"),
            reference_blurbs=reference_blurbs,
            arena_win_rate=arena_win_rate,
        ),
        "screen2_engine": detect_genericness(
            golden_finger, power_system, concept_text=concept_text
        ),
        "screen3_storyboard": screen3,
        "screen4_rhythm": build_rhythm_curve(rhythm_chapters or []),
        "screen5_sameness": build_sameness_table(
            cross_book_skeletons or [], current_slug=proj.get("slug")
        ),
    }


__all__ = [
    "SCHEMA_VERSION",
    "build_logline_compare",
    "build_reader_review",
    "build_rhythm_curve",
    "build_sameness_table",
    "classify_golden_finger_type",
    "detect_genericness",
    "render_chapter_storyboard",
    "render_scene_beat",
]
