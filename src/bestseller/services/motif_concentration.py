"""Detect a single motif that has eaten every design axis of a book.

**The failure this exists for.** 2026-08-09, 《灵根废我用烂账翻盘》: the approved
tournament champion mentioned 「账本」 exactly twice, in one clause about a
bookkeeping senior sister docking the protagonist's wages — ordinary, grounded,
and in-world. The finalize call then wrote the whole writing profile around it:
金手指=「账本嗅觉」, 感情线=「互相记账」, 力量体系=「新把柄入账」, 章尾钩=「账本上多出
一笔」, plus a serialization rule ordering 「每3章必须完成一次…新把柄入账」. That
profile block ships inside every chapter prompt (17 occurrences of 账 in 3019
characters), so chapter 7 came back titled 《对账》 with 31 of them in 2960
characters.

Nothing caught it. The debt/ledger detectors had been retired to ``return False``
shims on 2026-08-02 (correctly — they were vetoing books for containing the costs
the framework itself ordered), and the replacement they promised on the output
side was never built.

**Why this is not the motif police returning.** The retired police carried a
vocabulary: 债/账本/欠条/讨账 were illegal *words*, so a book whose premise was
honestly about a debt died for writing its own premise. This module has no
vocabulary at all and cannot acquire one. It asks a structural question instead:

    Did ONE token, which the approved concept did not lean on, end up occupying
    EVERY independent design axis of the book?

A book that is genuinely about a ledger says so in its approved concept, so the
lift term is ~1 and nothing fires. A framework artifact that amplified an
incidental noun into the golden finger, the romance mode, the power system and
the per-chapter law scores a high lift across all axes and does fire. On the
live book above the two amplified tokens (账 4.45, 柄 4.71) sit more than 3x
above the highest legitimate token (每 1.42) with nothing in between.

**It never blocks a book.** The caller uses it to earn ONE regeneration round.
An unresolved concentration is recorded, not fatal — see
``conception._motif_amplification_hits``.

The stop-list is empirical, not authored: the 400 most frequent characters in
444M characters of real published Chinese web fiction (``config/
zh_char_frequency_baseline.json``), which cover 72.6% of running text and
therefore cannot be anyone's distinctive motif. It is a *detector* input and
never enters a prompt.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
from typing import Any

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[一-鿿]")
_NON_CJK_RE = re.compile(r"[^一-鿿]+")

# Framework meta-language that the framework itself writes into profile prose.
# Stripped before counting so 「主角」「读者」「章节」 cannot look like the book's
# own motif. These are OUR words, not the book's, which is exactly why removing
# them is safe: no book can lose its identity by having them discounted.
_FRAMEWORK_META_TERMS: tuple[str, ...] = (
    "主角", "读者", "本书", "全书", "章节", "故事", "设定", "世界观", "节奏",
    "作者", "小说", "题材", "黄金三章", "反派", "人物", "情节", "角色", "卖点",
    "爽点", "钩子", "开局", "结局", "伏笔", "悬念", "配角", "篇幅", "追读",
)

# --- Thresholds (calibrated 2026-08-09 on the live 账本 book; see module docstring)
_MIN_AXES = 4              # fewer than this and "every axis" means nothing
_MIN_LIFT = 2.5            # target rate / approved-source rate
_MIN_RATE_PER_1K = 4.0     # occurrences per 1000 CJK chars of profile text
_MIN_COUNT = 6             # absolute floor, so short profiles cannot trip it
_MIN_TERM_COUNT = 3        # the motif must be a recurring TERM…
_MIN_TERM_SHARE = 0.40     # …carrying most of the character's occurrences
# Without enough approved material there is no baseline, so every lift would be
# infinite and every strong element would look like an artifact. Paths that skip
# the tournament (concept lab, seedless resume) can land here, so refuse to judge
# rather than judge blind.
_MIN_SOURCE_CHARS = 120


@dataclass(frozen=True)
class AmplifiedMotif:
    """One token that occupies every design axis without source support."""

    token: str
    term: str
    axes: tuple[str, ...]
    count: int
    rate_per_1k: float
    source_count: int
    lift: float

    @property
    def label(self) -> str:
        return self.term or self.token

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "term": self.term,
            "axes": list(self.axes),
            "count": self.count,
            "rate_per_1k": round(self.rate_per_1k, 2),
            "source_count": self.source_count,
            "lift": (None if self.lift == float("inf") else round(self.lift, 2)),
        }


def _baseline_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "config"
        / "zh_char_frequency_baseline.json"
    )


@lru_cache(maxsize=1)
def load_common_chars() -> frozenset[str]:
    """Load the empirical high-frequency stop-list. Empty set if unavailable."""

    path = _baseline_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return frozenset(str(payload.get("common_chars") or ""))
    except Exception:
        # fail-open: without the baseline the detector simply becomes noisier,
        # and every other condition still has to hold before anything fires.
        logger.warning("zh char baseline unavailable at %s", path, exc_info=True)
        return frozenset()


def flatten_text(value: Any) -> str:
    """Flatten an arbitrary JSON-ish payload into searchable prose."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(flatten_text(item) for item in value)
    if isinstance(value, bool):
        return ""
    return str(value)


def _strip_meta(text: str) -> str:
    for term in _FRAMEWORK_META_TERMS:
        text = text.replace(term, " ")
    return text


def _cjk_len(text: str) -> int:
    return len(_CJK_RE.findall(text))


def _bigram_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for segment in _NON_CJK_RE.split(text):
        for index in range(len(segment) - 1):
            counts[segment[index : index + 2]] += 1
    return counts


def detect_amplified_motifs(
    axis_texts: Mapping[str, Any],
    *,
    source_text: Any = "",
    limit: int = 3,
) -> tuple[AmplifiedMotif, ...]:
    """Return tokens that occupy every design axis without source support.

    ``axis_texts`` maps an axis name (hook / edge / relation / world / serial)
    to that axis's text. ``source_text`` is the APPROVED material — the user's
    own seed plus the tournament champion — against which amplification is
    measured. A token the approved concept already leaned on has lift ~1 and is
    never reported, which is what keeps a book that is honestly about a ledger
    from being punished for its own premise.

    Returns at most ``limit`` motifs, strongest lift first. Empty tuple is the
    normal, expected result.
    """

    axes = {
        name: _strip_meta(flatten_text(value))
        for name, value in axis_texts.items()
    }
    axes = {name: text for name, text in axes.items() if _cjk_len(text) > 0}
    if len(axes) < _MIN_AXES:
        return ()

    target = " ".join(axes.values())
    target_len = _cjk_len(target)
    if target_len <= 0:
        return ()

    source = _strip_meta(flatten_text(source_text))
    source_len = _cjk_len(source)
    if source_len < _MIN_SOURCE_CHARS:
        return ()

    common = load_common_chars()
    bigrams = _bigram_counts(target)
    axis_names = tuple(axes)

    found: list[AmplifiedMotif] = []
    for token, count in Counter(_CJK_RE.findall(target)).items():
        if token in common or count < _MIN_COUNT:
            continue
        # "Every design axis" is the whole claim — a motif in 4 of 5 axes is a
        # strong element, not an artifact that ate the book.
        hit_axes = tuple(name for name in axis_names if token in axes[name])
        if len(hit_axes) < len(axis_names):
            continue
        rate = count / target_len * 1000
        if rate < _MIN_RATE_PER_1K:
            continue
        source_count = source.count(token)
        source_rate = source_count / source_len * 1000
        lift = (rate / source_rate) if source_rate > 0 else float("inf")
        if lift < _MIN_LIFT:
            continue
        # A motif is a thing, not a character: 账本/把柄 recur as one term, while
        # a stray verb ("靠…") scatters across unrelated bigrams.
        candidates = [(gram, n) for gram, n in bigrams.items() if token in gram]
        if not candidates:
            continue
        term, term_count = max(candidates, key=lambda item: item[1])
        if term_count < _MIN_TERM_COUNT or term_count / count < _MIN_TERM_SHARE:
            continue
        found.append(
            AmplifiedMotif(
                token=token,
                term=term,
                axes=hit_axes,
                count=count,
                rate_per_1k=rate,
                source_count=source_count,
                lift=lift,
            )
        )

    found.sort(key=lambda motif: (-motif.lift, -motif.rate_per_1k))
    # One motif, one entry. Both characters of a two-character term qualify
    # independently (live 2026-08-09: 「代价」 was reported twice, as 代 and as
    # 价), which double-counts a single takeover — and the retry-adoption rule
    # compares these counts, so the duplicate distorts the decision. Keep the
    # strongest character per term.
    deduped: dict[str, AmplifiedMotif] = {}
    for motif in found:
        if motif.term not in deduped:
            deduped[motif.term] = motif
    return tuple(deduped.values())[:limit]


# --- Adapter for the conception writing profile -----------------------------

# Each entry is one INDEPENDENT design axis. They are independent by construction:
# what the reader is promised, what makes the protagonist special, how the
# relationships work, how the world works, and what every chapter must do. A
# token present in all of them is not a theme, it is a takeover.
_PROFILE_AXES: dict[str, tuple[tuple[str, str], ...]] = {
    "hook": (
        ("market", "reader_promise"),
        ("market", "selling_points"),
        ("market", "trope_keywords"),
        ("market", "hook_keywords"),
        ("market", "opening_strategy"),
        ("market", "chapter_hook_strategy"),
    ),
    "edge": (
        ("character", "protagonist_archetype"),
        ("character", "protagonist_core_drive"),
        ("character", "golden_finger"),
        ("character", "growth_curve"),
    ),
    "relation": (
        ("character", "romance_mode"),
        ("character", "relationship_tension"),
        ("character", "antagonist_mode"),
    ),
    "world": (
        ("world", "worldbuilding_density"),
        ("world", "info_reveal_strategy"),
        ("world", "rule_hardness"),
        ("world", "power_system_style"),
        ("world", "setting_tags"),
    ),
    "serial": (
        ("serialization", "opening_mandate"),
        ("serialization", "first_three_chapter_goal"),
        ("serialization", "scene_drive_rule"),
        ("serialization", "chapter_ending_rule"),
    ),
}


def writing_profile_axis_texts(profile: Any) -> dict[str, str]:
    """Split a writing profile into its independent design axes."""

    if not isinstance(profile, Mapping):
        return {}
    out: dict[str, str] = {}
    for axis, fields in _PROFILE_AXES.items():
        parts: list[str] = []
        for section, field in fields:
            block = profile.get(section)
            if isinstance(block, Mapping):
                parts.append(flatten_text(block.get(field)))
        out[axis] = " ".join(part for part in parts if part)
    return out


def detect_profile_motif_amplification(
    profile: Any,
    *,
    source_text: Any = "",
) -> tuple[AmplifiedMotif, ...]:
    """Convenience wrapper: writing profile in, amplified motifs out."""

    return detect_amplified_motifs(
        writing_profile_axis_texts(profile), source_text=source_text
    )


def render_motif_amplification_feedback(
    motifs: Sequence[AmplifiedMotif], *, is_en: bool
) -> str:
    """Repair instruction for the finalize retry.

    Naming the offending term is unavoidable and — unlike a guardrail block —
    safe here: this text is built from THIS book's own output for THIS one
    repair call. It never reaches a generation prompt, so it cannot seed the
    motif into other books. The instruction targets the structural defect (one
    term owning every axis) and deliberately does not say what to write instead.
    """

    if not motifs:
        return ""
    labels = "、".join(dict.fromkeys(motif.label for motif in motifs))
    if is_en:
        return (
            "\n\n[Rewrite required — one term owns every design axis]\n"
            f"“{labels}” carries the reader promise, the protagonist's edge, the "
            "relationship mode, the power system AND the per-chapter rule at once, "
            "while the approved concept barely used it. Keep the approved concept's "
            "own facts and let each axis stand on a different one of them: the edge, "
            "the relationships, the world's rules and the per-chapter obligation must "
            "each be describable without that term."
        )
    return (
        "\n\n【重写要求 · 一个词占满了全部设计轴】\n"
        f"「{labels}」同时充当读者承诺、主角差异化优势、关系模式、力量体系和每章硬性规则，"
        "而已批准构思几乎没有用到它——这是放大产物，不是本书的核心。"
        "请回到已批准构思里已有的事实，让这几条各自落在不同的事实上："
        "差异化优势、关系张力、世界规则、每章必做的事，四者都要能不依赖那个词说清楚。"
    )


__all__ = [
    "AmplifiedMotif",
    "detect_amplified_motifs",
    "detect_profile_motif_amplification",
    "flatten_text",
    "load_common_chars",
    "render_motif_amplification_feedback",
    "writing_profile_axis_texts",
]
