"""Multi-line narrative architecture gate — validate that a novel plan
carries the four canonical narrative layers (明线 / 暗线 / 隐藏线 / 核心轴).

Root cause this module addresses
--------------------------------

Across the 6-book audit we observed three cascading failure modes:

  1. **Single-layer plots**: Every volume reduced to one "current boss"
     with no cross-volume structure (道种破虚: 25 volumes × "元婴老者
     威压" → identical template).
  2. **Antagonist rotation without lineage**: Fixing the "one enemy
     per volume" rule by naming different enemies per volume still
     produces a flat narrative where every enemy is independent —
     readers get no "everything is connected" payoff.
  3. **No thematic invariant**: Without a ``core_axis``, the theme layer
     has no through-line, so motifs float without grounding.

The fix is not "one enemy per volume" — it is **a graph of narrative
lines** where:

  * **overt_line (明线)** — stage-specific antagonists / missions that
    rotate every 2-3 volumes. This is what the reader sees chapter-to-
    chapter.
  * **undercurrent_line (暗线)** — a multi-volume manipulator / faction
    conflict that is hinted at early and revealed mid-book. Past
    antagonists from the overt line are often puppets of this line.
  * **hidden_thread (隐藏线)** — the book-spanning secret. Seeded in
    V1-V3, paid off in the final volumes; what makes a reader want to
    re-read the book after finishing.
  * **core_axis (核心轴 / 底层逻辑)** — the thematic invariant. Not a
    plot arc; it is the moral / philosophical question the whole book
    interrogates. Every volume's theme_statement should echo it.

This module provides the schema, scaling contract, and scan; wiring
into the volume-plan prompt + post-gen repair happens at call sites.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables — per-line volume span targets. Calibrated against the 6-book
# audit: what separates "deep" vs "shallow" narrative feel is the
# presence of a non-collapsing multi-line structure.
# ---------------------------------------------------------------------------

# overt_line is the per-volume surface layer. Each overt arc should cover
# between 1 and 4 volumes (≥ 2 is typical; single-volume arcs are fine
# for short novellas).
OVERT_LINE_MIN_ARCS_FLOOR: int = 3  # at least 3 rotating overt arcs per book
OVERT_LINE_MAX_VOLUMES_PER_ARC: int = 5

# undercurrent must span a meaningful fraction of the book — if it
# collapses to 1-2 volumes it's really just another overt arc.
UNDERCURRENT_MIN_VOLUME_SPAN: int = 4

# hidden_thread must reach from the first quarter into the final
# quarter — anything shorter is a subplot, not a hidden thread.
HIDDEN_THREAD_MIN_VOLUME_SPAN_RATIO: float = 0.6

# core_axis must be referenced in most volumes. This is the "theme
# invariant" — a volume that doesn't touch the core axis feels like
# it belongs to a different book.
CORE_AXIS_MIN_VOLUME_REFERENCE_RATIO: float = 0.7

# Canonical line ids. Keeping them namespaced so we can cross-reference
# antagonists and other entities by line id.
LINE_OVERT: str = "overt"
LINE_UNDERCURRENT: str = "undercurrent"
LINE_HIDDEN: str = "hidden"
LINE_CORE_AXIS: str = "core_axis"

CANONICAL_LINES: tuple[str, ...] = (
    LINE_OVERT,
    LINE_UNDERCURRENT,
    LINE_HIDDEN,
    LINE_CORE_AXIS,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NarrativeLinesFinding:
    """One audit finding against the narrative lines spec."""

    code: str              # stable short identifier
    severity: str          # "critical" | "warning"
    message: str           # human-readable (zh or en)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NarrativeLineSummary:
    """Per-line aggregate used for observability."""

    line_id: str
    arc_count: int
    volume_span: tuple[int, int] | None  # (first_vol, last_vol) inclusive


@dataclass(frozen=True)
class NarrativeLinesReport:
    """Aggregate scan of a volume plan's narrative lines spec."""

    total_chapters: int
    volume_count: int
    has_overt: bool
    has_undercurrent: bool
    has_hidden_thread: bool
    has_core_axis: bool
    line_summaries: tuple[NarrativeLineSummary, ...]
    core_axis_reference_ratio: float
    findings: tuple[NarrativeLinesFinding, ...]

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def is_critical(self) -> bool:
        return self.critical_count > 0

    def to_prompt_block(self, *, language: str = "zh-CN") -> str:
        """Render the report into a repair prompt block."""

        if not self.findings:
            return ""

        is_en = _is_english(language)
        lines: list[str] = []
        if is_en:
            lines.append("[NARRATIVE LINES REPAIR — hard requirements]")
            lines.append(
                "- Every full-length novel MUST have all four narrative "
                "layers: `overt_line` (stage-specific), `undercurrent_line` "
                "(multi-volume shadow conflict), `hidden_thread` (book-wide "
                "secret), `core_axis` (thematic invariant)."
            )
            lines.append(
                f"- `overt_line` MUST contain ≥ {OVERT_LINE_MIN_ARCS_FLOOR} "
                "distinct arcs; no single arc may exceed "
                f"{OVERT_LINE_MAX_VOLUMES_PER_ARC} volumes."
            )
            lines.append(
                f"- `undercurrent_line` MUST span ≥ "
                f"{UNDERCURRENT_MIN_VOLUME_SPAN} volumes."
            )
            lines.append(
                f"- `hidden_thread` MUST span ≥ "
                f"{int(HIDDEN_THREAD_MIN_VOLUME_SPAN_RATIO * 100)}% of the "
                "book (seeded in the first quarter, resolved in the last "
                "quarter)."
            )
            lines.append(
                f"- `core_axis` MUST be referenced in ≥ "
                f"{int(CORE_AXIS_MIN_VOLUME_REFERENCE_RATIO * 100)}% of "
                "volumes (via `theme_statement` or `core_axis_reference`)."
            )
            lines.append("")
            lines.append("Current findings (fix ALL critical):")
        else:
            lines.append("【叙事多线架构修复 — 硬性要求】")
            lines.append(
                "- 全书必须同时具备四层叙事：`overt_line`（明线，卷级）、"
                "`undercurrent_line`（暗线，跨卷）、`hidden_thread`（隐藏线，"
                "贯穿全书）、`core_axis`（核心轴，主题不变量）。"
            )
            lines.append(
                f"- `overt_line` 必须包含 ≥ {OVERT_LINE_MIN_ARCS_FLOOR} 条"
                f"不同的弧，单弧最多跨 {OVERT_LINE_MAX_VOLUMES_PER_ARC} 卷。"
            )
            lines.append(
                f"- `undercurrent_line` 必须至少跨 {UNDERCURRENT_MIN_VOLUME_SPAN} 卷。"
            )
            lines.append(
                f"- `hidden_thread` 必须跨全书 ≥ "
                f"{int(HIDDEN_THREAD_MIN_VOLUME_SPAN_RATIO * 100)}% 的卷数"
                "（首 1/4 埋下，末 1/4 回收）。"
            )
            lines.append(
                f"- `core_axis` 必须在 ≥ "
                f"{int(CORE_AXIS_MIN_VOLUME_REFERENCE_RATIO * 100)}% 的卷中被引用"
                "（通过 theme_statement 或 core_axis_reference 字段）。"
            )
            lines.append("")
            lines.append("当前审查结果（所有 critical 项必须修复）：")

        for finding in self.findings:
            bullet = "×" if finding.severity == "critical" else "!"
            lines.append(f"  {bullet} [{finding.severity}] {finding.code}: {finding.message}")

        return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_english(language: str | None) -> bool:
    if not language:
        return False
    return language.lower().startswith("en")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
        except Exception:
            return {}
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
    return {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []
    return [_mapping(item) for item in value if item is not None]


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _volume_span(arc: dict[str, Any]) -> tuple[int, int] | None:
    """Derive (first_volume, last_volume) from an arc spec.

    Accepts either:
      * {"volumes": [3, 4, 5, 6]}  → (3, 6)
      * {"start_volume": 3, "end_volume": 6} → (3, 6)
      * {"start_volume": 3} → (3, 3)
    """

    vols = arc.get("volumes")
    if isinstance(vols, list) and vols:
        ints = [int(v) for v in vols if isinstance(v, (int, float, str)) and str(v).strip().isdigit()]
        if ints:
            return (min(ints), max(ints))
    start = arc.get("start_volume")
    end = arc.get("end_volume") or start
    try:
        start_int = int(start)
        end_int = int(end)
        if end_int < start_int:
            start_int, end_int = end_int, start_int
        return (start_int, end_int)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_narrative_lines(
    narrative_lines: Any,
    *,
    total_chapters: int,
    volume_count: int,
    volume_plan: Any = None,
    language: str = "zh-CN",
) -> NarrativeLinesReport:
    """Audit a narrative_lines spec for the four-layer contract.

    Parameters
    ----------
    narrative_lines
        A dict / pydantic model shaped like::

            {
              "overt_line": [{"name": "...", "volumes": [1,2], "antagonist_ref": "..."}],
              "undercurrent_line": [{"name": "...", "start_volume": 3, "end_volume": 10, ...}],
              "hidden_thread": {"statement": "...", "seed_volumes": [1,2], "payoff_volumes": [22,23,24]},
              "core_axis": {"statement": "...", "phrasing_tokens": [...]}
            }
    volume_plan
        Optional — the volume plan used to detect core_axis references
        inside ``volume_theme`` / ``core_axis_reference``.
    total_chapters, volume_count
        Used for scaling decisions (hidden_thread span ratio, etc).
    """

    is_en = _is_english(language)

    spec = _mapping(narrative_lines)
    volume_count = max(int(volume_count or 0), 1)
    findings: list[NarrativeLinesFinding] = []

    overt = _mapping_list(spec.get("overt_line"))
    undercurrent = _mapping_list(spec.get("undercurrent_line"))
    hidden_raw = spec.get("hidden_thread")
    hidden = _mapping(hidden_raw) if hidden_raw else {}
    core_axis_raw = spec.get("core_axis")
    core_axis = _mapping(core_axis_raw) if core_axis_raw else {}

    has_overt = bool(overt)
    has_undercurrent = bool(undercurrent)
    has_hidden = bool(_as_str(hidden.get("statement")))
    has_core_axis = bool(_as_str(core_axis.get("statement")))

    line_summaries: list[NarrativeLineSummary] = []

    # ── Overt line ───────────────────────────────────────────────────
    if not has_overt:
        findings.append(
            NarrativeLinesFinding(
                code="missing_overt_line",
                severity="critical",
                message=(
                    "narrative_lines.overt_line is empty. Every book needs "
                    "stage-specific rotating arcs."
                    if is_en
                    else "narrative_lines.overt_line 缺失，书中必须有卷级轮换的明线弧。"
                ),
            )
        )
        line_summaries.append(NarrativeLineSummary(line_id=LINE_OVERT, arc_count=0, volume_span=None))
    else:
        spans = [s for s in (_volume_span(a) for a in overt) if s is not None]
        first = min(s[0] for s in spans) if spans else 0
        last = max(s[1] for s in spans) if spans else 0
        line_summaries.append(
            NarrativeLineSummary(
                line_id=LINE_OVERT,
                arc_count=len(overt),
                volume_span=(first, last) if spans else None,
            )
        )
        if len(overt) < OVERT_LINE_MIN_ARCS_FLOOR and volume_count >= OVERT_LINE_MIN_ARCS_FLOOR:
            findings.append(
                NarrativeLinesFinding(
                    code="starved_overt_line_arcs",
                    severity="critical",
                    message=(
                        f"overt_line has only {len(overt)} arcs; need ≥ "
                        f"{OVERT_LINE_MIN_ARCS_FLOOR} for a "
                        f"{volume_count}-volume plan (otherwise the surface "
                        "layer will feel static)."
                        if is_en
                        else f"overt_line 只有 {len(overt)} 条弧，"
                        f"{volume_count} 卷的书至少需要 {OVERT_LINE_MIN_ARCS_FLOOR} 条不同弧，"
                        "否则表层叙事会显得停滞。"
                    ),
                    payload={"count": len(overt), "floor": OVERT_LINE_MIN_ARCS_FLOOR},
                )
            )
        # Check single-arc volume spans
        for arc in overt:
            span = _volume_span(arc)
            if span is None:
                continue
            width = span[1] - span[0] + 1
            if width > OVERT_LINE_MAX_VOLUMES_PER_ARC:
                findings.append(
                    NarrativeLinesFinding(
                        code="overt_arc_too_wide",
                        severity="warning",
                        message=(
                            f"overt arc '{_as_str(arc.get('name')) or '?'}' "
                            f"spans {width} volumes (>"
                            f"{OVERT_LINE_MAX_VOLUMES_PER_ARC}); it should "
                            "rotate into a new arc sooner."
                            if is_en
                            else f"明线弧 '{_as_str(arc.get('name')) or '?'}' "
                            f"跨了 {width} 卷（> {OVERT_LINE_MAX_VOLUMES_PER_ARC}），"
                            "应当更早切换为新弧。"
                        ),
                        payload={"name": _as_str(arc.get("name")), "width": width},
                    )
                )

    # ── Undercurrent line ────────────────────────────────────────────
    if not has_undercurrent:
        findings.append(
            NarrativeLinesFinding(
                code="missing_undercurrent_line",
                severity="critical",
                message=(
                    "narrative_lines.undercurrent_line is empty. Without a "
                    "multi-volume shadow conflict, the overt arcs feel "
                    "disconnected."
                    if is_en
                    else "narrative_lines.undercurrent_line 缺失，"
                    "缺少跨卷暗线会让明线弧彼此失去联系。"
                ),
            )
        )
        line_summaries.append(
            NarrativeLineSummary(line_id=LINE_UNDERCURRENT, arc_count=0, volume_span=None)
        )
    else:
        spans = [s for s in (_volume_span(a) for a in undercurrent) if s is not None]
        widest_span: tuple[int, int] | None = None
        widest = 0
        for s in spans:
            w = s[1] - s[0] + 1
            if w > widest:
                widest = w
                widest_span = s
        line_summaries.append(
            NarrativeLineSummary(
                line_id=LINE_UNDERCURRENT,
                arc_count=len(undercurrent),
                volume_span=widest_span,
            )
        )
        if widest < UNDERCURRENT_MIN_VOLUME_SPAN and volume_count >= UNDERCURRENT_MIN_VOLUME_SPAN:
            findings.append(
                NarrativeLinesFinding(
                    code="shallow_undercurrent_line",
                    severity="critical",
                    message=(
                        f"Undercurrent arc widest span is {widest} volumes; "
                        f"need ≥ {UNDERCURRENT_MIN_VOLUME_SPAN} to distinguish "
                        "it from an overt arc."
                        if is_en
                        else f"暗线最宽弧仅跨 {widest} 卷，"
                        f"至少要 {UNDERCURRENT_MIN_VOLUME_SPAN} 卷，"
                        "否则与明线无区别。"
                    ),
                    payload={"widest_span": widest, "floor": UNDERCURRENT_MIN_VOLUME_SPAN},
                )
            )

    # ── Hidden thread ────────────────────────────────────────────────
    if not has_hidden:
        findings.append(
            NarrativeLinesFinding(
                code="missing_hidden_thread",
                severity="critical",
                message=(
                    "narrative_lines.hidden_thread is empty. Without a "
                    "book-spanning secret, the ending has no reveal."
                    if is_en
                    else "narrative_lines.hidden_thread 缺失，"
                    "缺少贯穿全书的隐藏线会让结尾失去揭示感。"
                ),
            )
        )
        line_summaries.append(
            NarrativeLineSummary(line_id=LINE_HIDDEN, arc_count=0, volume_span=None)
        )
    else:
        seed_volumes: list[int] = [
            int(v) for v in (hidden.get("seed_volumes") or [])
            if isinstance(v, (int, float, str)) and str(v).strip().isdigit()
        ]
        payoff_volumes: list[int] = [
            int(v) for v in (hidden.get("payoff_volumes") or [])
            if isinstance(v, (int, float, str)) and str(v).strip().isdigit()
        ]
        span = None
        if seed_volumes and payoff_volumes:
            span = (min(seed_volumes), max(payoff_volumes))
        elif seed_volumes or payoff_volumes:
            all_v = seed_volumes + payoff_volumes
            span = (min(all_v), max(all_v))
        line_summaries.append(
            NarrativeLineSummary(
                line_id=LINE_HIDDEN, arc_count=1, volume_span=span,
            )
        )
        min_span_needed = max(2, int(round(volume_count * HIDDEN_THREAD_MIN_VOLUME_SPAN_RATIO)))
        observed_span = (span[1] - span[0] + 1) if span else 0
        if observed_span < min_span_needed:
            findings.append(
                NarrativeLinesFinding(
                    code="shallow_hidden_thread",
                    severity="critical",
                    message=(
                        f"hidden_thread spans {observed_span} volumes; need ≥ "
                        f"{min_span_needed} for a {volume_count}-volume book "
                        f"(seed in V1-V{max(1, volume_count // 4)}, payoff in "
                        f"V{max(1, volume_count - volume_count // 4 + 1)}-"
                        f"V{volume_count})."
                        if is_en
                        else f"hidden_thread 只跨 {observed_span} 卷，"
                        f"{volume_count} 卷的书至少需要跨 {min_span_needed} 卷"
                        f"（首 V1-V{max(1, volume_count // 4)} 埋下，"
                        f"末 V{max(1, volume_count - volume_count // 4 + 1)}-"
                        f"V{volume_count} 回收）。"
                    ),
                    payload={
                        "seed_volumes": seed_volumes,
                        "payoff_volumes": payoff_volumes,
                        "observed_span": observed_span,
                        "floor": min_span_needed,
                    },
                )
            )

    # ── Core axis ────────────────────────────────────────────────────
    core_axis_reference_ratio = 0.0
    if not has_core_axis:
        findings.append(
            NarrativeLinesFinding(
                code="missing_core_axis",
                severity="critical",
                message=(
                    "narrative_lines.core_axis is empty. Without a thematic "
                    "invariant, volume themes float without grounding."
                    if is_en
                    else "narrative_lines.core_axis 缺失，"
                    "没有核心轴会让各卷主题失去锚点。"
                ),
            )
        )
        line_summaries.append(
            NarrativeLineSummary(line_id=LINE_CORE_AXIS, arc_count=0, volume_span=None)
        )
    else:
        # Walk the volume plan (when supplied) and count how many volumes
        # reference the core_axis statement or any of its phrasing tokens.
        vp_volumes: list[dict[str, Any]] = []
        if volume_plan is not None:
            if isinstance(volume_plan, list):
                vp_volumes = _mapping_list(volume_plan)
            else:
                vp_dict = _mapping(volume_plan)
                if isinstance(vp_dict.get("volumes"), list):
                    vp_volumes = _mapping_list(vp_dict["volumes"])

        axis_text = _as_str(core_axis.get("statement")).lower()
        axis_tokens = [
            _as_str(t).lower() for t in (core_axis.get("phrasing_tokens") or [])
            if _as_str(t)
        ]

        def _vol_references_axis(vol: dict[str, Any]) -> bool:
            if _as_str(vol.get("core_axis_reference")):
                return True
            vt = _as_str(vol.get("volume_theme")).lower()
            if axis_text and axis_text[:12] and axis_text[:12] in vt:
                return True
            for tok in axis_tokens:
                if tok and tok in vt:
                    return True
            return False

        if vp_volumes:
            refs = sum(1 for v in vp_volumes if _vol_references_axis(v))
            core_axis_reference_ratio = refs / max(len(vp_volumes), 1)
        else:
            core_axis_reference_ratio = 0.0

        line_summaries.append(
            NarrativeLineSummary(
                line_id=LINE_CORE_AXIS,
                arc_count=1,
                volume_span=(1, volume_count),
            )
        )

        if vp_volumes and core_axis_reference_ratio < CORE_AXIS_MIN_VOLUME_REFERENCE_RATIO:
            findings.append(
                NarrativeLinesFinding(
                    code="weak_core_axis_threading",
                    severity="critical",
                    message=(
                        f"core_axis is referenced in only "
                        f"{int(core_axis_reference_ratio * 100)}% of volumes; "
                        f"need ≥ {int(CORE_AXIS_MIN_VOLUME_REFERENCE_RATIO * 100)}%. "
                        "Volumes without a core_axis reference feel off-theme."
                        if is_en
                        else f"core_axis 仅在 "
                        f"{int(core_axis_reference_ratio * 100)}% 的卷中被引用，"
                        f"至少要 {int(CORE_AXIS_MIN_VOLUME_REFERENCE_RATIO * 100)}%；"
                        "没有 core_axis 引用的卷会让读者觉得脱离主题。"
                    ),
                    payload={
                        "observed_ratio": round(core_axis_reference_ratio, 3),
                        "floor_ratio": CORE_AXIS_MIN_VOLUME_REFERENCE_RATIO,
                    },
                )
            )

    return NarrativeLinesReport(
        total_chapters=max(int(total_chapters or 0), 1),
        volume_count=volume_count,
        has_overt=has_overt,
        has_undercurrent=has_undercurrent,
        has_hidden_thread=has_hidden,
        has_core_axis=has_core_axis,
        line_summaries=tuple(line_summaries),
        core_axis_reference_ratio=round(core_axis_reference_ratio, 3),
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Prompt-block renderer for the upstream volume-plan prompt
# ---------------------------------------------------------------------------

def render_narrative_lines_constraints_block(
    *,
    total_chapters: int,
    volume_count: int,
    language: str = "zh-CN",
) -> str:
    """Render the up-front narrative-line constraints.

    Injected into the volume-plan (and book-spec) prompts so the LLM
    generates the four-layer structure on the first pass rather than
    producing a flat single-layer outline.
    """

    volume_count = max(int(volume_count or 0), 1)
    hidden_span_floor = max(2, int(round(volume_count * HIDDEN_THREAD_MIN_VOLUME_SPAN_RATIO)))
    core_ref_floor_pct = int(CORE_AXIS_MIN_VOLUME_REFERENCE_RATIO * 100)

    if _is_english(language):
        return (
            "[NARRATIVE LINES HARD CONSTRAINTS]\n"
            f"- Target plan: {total_chapters} chapters across "
            f"{volume_count} volumes.\n"
            "- EVERY book must carry four narrative layers simultaneously:\n"
            f"  1. `overt_line` (明线): ≥ {OVERT_LINE_MIN_ARCS_FLOOR} "
            "stage-specific arcs that rotate every 2-3 volumes. Each arc "
            "names the current antagonist, mission, or pressure. No single "
            f"arc may exceed {OVERT_LINE_MAX_VOLUMES_PER_ARC} volumes.\n"
            f"  2. `undercurrent_line` (暗线): ≥ 1 arc spanning ≥ "
            f"{UNDERCURRENT_MIN_VOLUME_SPAN} volumes — the shadow "
            "manipulator / faction that earlier overt antagonists turn "
            "out to have served.\n"
            f"  3. `hidden_thread` (隐藏线): one book-spanning secret. "
            f"Seed it in V1-V{max(1, volume_count // 4)}, pay it off in "
            f"V{max(1, volume_count - volume_count // 4 + 1)}-"
            f"V{volume_count}. Span must be ≥ {hidden_span_floor} volumes.\n"
            "  4. `core_axis` (核心轴): the thematic invariant — the "
            "moral / philosophical question the whole book interrogates. "
            f"It MUST be referenced in ≥ {core_ref_floor_pct}% of volumes "
            "(via `volume_theme` or an explicit `core_axis_reference`).\n"
            "- Each overt arc should declare `line_role: overt`, each "
            "undercurrent arc `line_role: undercurrent`, etc. Antagonists "
            "reference their serving line by id.\n"
            "- Volume themes MUST NOT all reduce to the same template "
            "(e.g. 'survive the pressure of X'). Every volume's surface "
            "conflict belongs to one overt arc; the undercurrent and "
            "hidden threads provide the cross-volume continuity.\n"
        )

    return (
        "【叙事多线架构硬性要求】\n"
        f"- 全书规划：{total_chapters} 章，共 {volume_count} 卷。\n"
        "- 每本书必须同时构建四层叙事：\n"
        f"  1. `overt_line`（明线）：≥ {OVERT_LINE_MIN_ARCS_FLOOR} 条"
        f"阶段性弧，每 2-3 卷轮换。每条弧明确当下敌人/任务/压力，"
        f"单弧跨度不得 > {OVERT_LINE_MAX_VOLUMES_PER_ARC} 卷。\n"
        f"  2. `undercurrent_line`（暗线）：≥ 1 条跨 ≥ "
        f"{UNDERCURRENT_MIN_VOLUME_SPAN} 卷的弧——前期明线敌人其实"
        "服务于这条暗线的幕后势力/操盘者。\n"
        f"  3. `hidden_thread`（隐藏线）：一条贯穿全书的秘密。"
        f"第 V1-V{max(1, volume_count // 4)} 埋下，第 "
        f"V{max(1, volume_count - volume_count // 4 + 1)}-"
        f"V{volume_count} 回收，总跨度 ≥ {hidden_span_floor} 卷。\n"
        f"  4. `core_axis`（核心轴）：主题不变量——全书追问的"
        "哲学/道德问题。必须在 ≥ "
        f"{core_ref_floor_pct}% 的卷中被引用"
        "（通过 volume_theme 或 core_axis_reference 字段）。\n"
        "- 每条明线弧要标注 `line_role: overt`，暗线弧标注 "
        "`line_role: undercurrent`，以此类推；敌人条目通过 line_id "
        "反向引用所属线。\n"
        "- 各卷主题禁止坍缩为同一模板（如『抵抗 X 的压迫』）。"
        "每卷表层冲突归属于明线弧，跨卷连续性由暗线和隐藏线承担。\n"
    )
