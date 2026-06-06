"""LitStyle-100R config loader + deterministic AI腔 detector.

This is the *config / detector* layer for the advisory 文采 (literary-craft) judge.
It is intentionally dependency-light (no LLM, no DB) so it can be imported by the
judge, the writer-side levers, unit tests, and standalone scripts alike.

Two responsibilities:

* :func:`load_litstyle_config` — a cached, typed view over
  ``config/litstyle_prose.yaml`` (the single source of truth for the 9 positive
  dimensions, the AI腔 penalty markers, level thresholds, writer-tier targets and
  the three calibration anchors).
* :func:`detect_ai_tone` — a *deterministic* AI腔 detector. Following the
  ``scene_grounding`` honesty principle, it only claims the markers it can
  reliably catch (symmetric syntax, abstract-value density, emotion-label
  density); the remaining markers (套路金句结尾 / 人物声音同质 / 逻辑自动补全) are
  semantic and are left to the LLM judge. The deterministic penalty is a *soft
  prior* fed to the judge — it never gates anything.
"""

# Dominated by Chinese rubric strings (fullwidth punctuation, long lines).
# ruff: noqa: RUF001, RUF002

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)

_CONFIG_FILENAME = "litstyle_prose.yaml"

# ---------------------------------------------------------------------------
# Prose-lever framing (anti-regression for the文采 writer levers)
# ---------------------------------------------------------------------------
# A/B root-cause (2026-06-06): stacking the文采 levers made a budget writer read
# the 留白/克制/别堆砌 guards as "write less" → it cut ~30% length, losing concrete
# development and the ending hook → LitStyle score DROPPED. This framing is
# prepended to the文采 lever group to correct the misread: 文采 = more concrete,
# not shorter; pick 1-2 techniques, not all; 留白 deletes author-narration, not plot.
_PROSE_LEVER_FRAMING_ZH = (
    "【文采注入总则 · 先读这条】\n"
    "下面给你几条文采技法，但记住三点，否则会越写越差：\n"
    "1. 文采靠把场景写得更具体、更准、更有画面，不是写得更短或更克制到没内容。"
    "先把本场该有的篇幅写够，再求精——绝不能为了文采把篇幅写短。\n"
    "2. 技法是可选项，不是清单：本场只挑 1-2 个真正用得上的，别为了凑技法而堆砌。\n"
    "3. 留白是删掉「作者解说/情绪标签」，不是删掉剧情、动作、对话和细节；"
    "网文要的是「具体 + 爽」，钩子和爽点一个都不能少。"
)


def render_prose_lever_framing(language: str = "zh") -> str:
    """Framing prepended to the文采 lever group. Empty for English (zh-only)."""

    if str(language or "").lower().startswith("en"):
        return ""
    return _PROSE_LEVER_FRAMING_ZH


# Mirror of chapter_llm_quality_judge._PREMIUM_WRITER_MODEL_TAGS. Duplicated here
# (rather than imported) to keep this config/detector layer free of the heavy
# judge module's import graph. Kept in sync by a unit test.
_PREMIUM_WRITER_MODEL_TAGS: tuple[str, ...] = (
    "claude", "opus", "sonnet", "gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "gemini-2",
)


# ---------------------------------------------------------------------------
# Typed view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LitStyleDimension:
    """One ``dimensions.<n>`` entry — a scored positive dimension."""

    key: str
    display_name: str
    max: int
    definition: str
    observable: str
    scoring_rule: str
    pos_example: str
    neg_example: str


@dataclass(frozen=True)
class AiToneMarker:
    """One ``ai_tone.markers`` entry."""

    marker_id: str
    problem: str
    penalty_max: int
    deterministic: bool


@dataclass(frozen=True)
class LitStyleLevel:
    """One ``levels`` entry (descending ``min`` thresholds)."""

    min: int
    level: str
    desc: str


@dataclass(frozen=True)
class CalibrationAnchor:
    """One ``calibration_anchors`` entry — a worked LitStyle judgement."""

    anchor_id: str
    final: int
    level: str
    excerpt: str
    scores: dict[str, int]
    note: str


@dataclass(frozen=True)
class DeterministicDetectorParams:
    """Word tables + density thresholds for :func:`detect_ai_tone`."""

    abstract_value_words: tuple[str, ...]
    abstract_value_per_kchars: float
    symmetric_patterns: tuple[str, ...]
    symmetric_per_kchars: float
    emotion_label_words: tuple[str, ...]
    emotion_label_per_kchars: float


@dataclass(frozen=True)
class LitStyleConfig:
    """Typed view over ``litstyle_prose.yaml``."""

    version: str
    dimensions: tuple[LitStyleDimension, ...]
    ai_tone_penalty_max: int
    ai_tone_high_risk_threshold: int
    ai_tone_mature_ceiling: int
    ai_tone_markers: tuple[AiToneMarker, ...]
    levels: tuple[LitStyleLevel, ...]
    target_premium: float
    target_budget: float
    detector: DeterministicDetectorParams
    calibration_anchors: tuple[CalibrationAnchor, ...]

    @property
    def base_score_max(self) -> int:
        """Sum of every positive dimension's ``max`` (should be 100)."""

        return sum(dim.max for dim in self.dimensions)

    @property
    def dimension_keys(self) -> tuple[str, ...]:
        return tuple(dim.key for dim in self.dimensions)

    @property
    def deterministic_penalty_max(self) -> int:
        return sum(m.penalty_max for m in self.ai_tone_markers if m.deterministic)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _flt(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_dimensions(raw: object) -> tuple[LitStyleDimension, ...]:
    out: list[LitStyleDimension] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            key = as_str(data.get("key"))
            if not key:
                continue
            out.append(
                LitStyleDimension(
                    key=key,
                    display_name=as_str(data.get("display_name"), default=key),
                    max=as_int(data.get("max"), default=10),
                    definition=as_str(data.get("definition")),
                    observable=as_str(data.get("observable")),
                    scoring_rule=as_str(data.get("scoring_rule")),
                    pos_example=as_str(data.get("pos_example")),
                    neg_example=as_str(data.get("neg_example")),
                )
            )
    return tuple(out)


def _parse_markers(raw: object) -> tuple[AiToneMarker, ...]:
    out: list[AiToneMarker] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            marker_id = as_str(data.get("id"))
            if not marker_id:
                continue
            out.append(
                AiToneMarker(
                    marker_id=marker_id,
                    problem=as_str(data.get("problem")),
                    penalty_max=as_int(data.get("penalty_max"), default=3),
                    deterministic=bool(data.get("deterministic", False)),
                )
            )
    return tuple(out)


def _parse_levels(raw: object) -> tuple[LitStyleLevel, ...]:
    out: list[LitStyleLevel] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            out.append(
                LitStyleLevel(
                    min=as_int(data.get("min"), default=0),
                    level=as_str(data.get("level"), default="较弱"),
                    desc=as_str(data.get("desc")),
                )
            )
    # Descending so the first match wins in level resolution.
    return tuple(sorted(out, key=lambda lvl: lvl.min, reverse=True))


def _parse_anchors(raw: object) -> tuple[CalibrationAnchor, ...]:
    out: list[CalibrationAnchor] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            scores_raw = as_dict(data.get("scores"))
            scores = {
                str(k): as_int(v, default=0) for k, v in scores_raw.items()
            }
            out.append(
                CalibrationAnchor(
                    anchor_id=as_str(data.get("id")),
                    final=as_int(data.get("final"), default=0),
                    level=as_str(data.get("level")),
                    excerpt=as_str(data.get("excerpt")),
                    scores=scores,
                    note=as_str(data.get("note")),
                )
            )
    return tuple(out)


def _parse_detector(raw: object) -> DeterministicDetectorParams:
    data = as_dict(raw)
    return DeterministicDetectorParams(
        abstract_value_words=as_str_tuple(data.get("abstract_value_words")),
        abstract_value_per_kchars=_flt(data.get("abstract_value_per_kchars"), 6.0),
        symmetric_patterns=as_str_tuple(data.get("symmetric_patterns")),
        symmetric_per_kchars=_flt(data.get("symmetric_per_kchars"), 3.0),
        emotion_label_words=as_str_tuple(data.get("emotion_label_words")),
        emotion_label_per_kchars=_flt(data.get("emotion_label_per_kchars"), 5.0),
    )


@lru_cache(maxsize=1)
def load_litstyle_config() -> LitStyleConfig:
    """Return the cached, typed view over ``litstyle_prose.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    ai_tone = as_dict(raw.get("ai_tone"))
    targets = as_dict(raw.get("targets"))
    return LitStyleConfig(
        version=as_str(raw.get("version")),
        dimensions=_parse_dimensions(raw.get("dimensions")),
        ai_tone_penalty_max=as_int(ai_tone.get("penalty_max"), default=20),
        ai_tone_high_risk_threshold=as_int(ai_tone.get("high_risk_threshold"), default=15),
        ai_tone_mature_ceiling=as_int(ai_tone.get("mature_ceiling"), default=4),
        ai_tone_markers=_parse_markers(ai_tone.get("markers")),
        levels=_parse_levels(raw.get("levels")),
        target_premium=_flt(targets.get("premium_writer"), 80.0),
        target_budget=_flt(targets.get("budget_writer"), 72.0),
        detector=_parse_detector(raw.get("deterministic_detector")),
        calibration_anchors=_parse_anchors(raw.get("calibration_anchors")),
    )


# ---------------------------------------------------------------------------
# Level + target resolution
# ---------------------------------------------------------------------------


def litstyle_level_for_score(
    final_score: float, config: LitStyleConfig | None = None
) -> str:
    """Map a 0-100 FinalScore to its LitStyle level label."""

    config = config or load_litstyle_config()
    for level in config.levels:  # already sorted descending by min
        if final_score >= level.min:
            return level.level
    return config.levels[-1].level if config.levels else "较弱"


def is_premium_writer_model(model: str | None) -> bool:
    """Whether the writer model is a frontier (Claude-tier) model.

    Mirrors ``chapter_llm_quality_judge.is_premium_writer_model`` so the文采
    target tracks the same writer-tier boundary the commercial gate uses.
    """

    if not model:
        return False
    m = str(model).lower()
    return any(tag in m for tag in _PREMIUM_WRITER_MODEL_TAGS)


def litstyle_target_for_writer_model(
    model: str | None, config: LitStyleConfig | None = None
) -> float:
    """Return the文采 *target* (not gate) for the configured writer tier."""

    config = config or load_litstyle_config()
    return config.target_premium if is_premium_writer_model(model) else config.target_budget


# ---------------------------------------------------------------------------
# Deterministic AI腔 detector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AiToneResult:
    """Deterministic AI腔 reading — a *soft prior*, never a gate.

    ``deterministic_penalty`` covers only the three reliably-detectable markers
    (symmetric syntax, abstract-value density, emotion-label density). The
    remaining markers are semantic and contribute 0 here — the LLM judge owns
    them. ``note`` states that limitation honestly.
    """

    char_count: int
    abstract_value_hits: int
    abstract_value_per_kchars: float
    symmetric_hits: int
    symmetric_per_kchars: float
    emotion_label_hits: int
    emotion_label_per_kchars: float
    flagged: tuple[str, ...]
    deterministic_penalty: float
    deterministic_penalty_max: int
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "char_count": self.char_count,
            "abstract_value_hits": self.abstract_value_hits,
            "abstract_value_per_kchars": round(self.abstract_value_per_kchars, 3),
            "symmetric_hits": self.symmetric_hits,
            "symmetric_per_kchars": round(self.symmetric_per_kchars, 3),
            "emotion_label_hits": self.emotion_label_hits,
            "emotion_label_per_kchars": round(self.emotion_label_per_kchars, 3),
            "flagged": list(self.flagged),
            "deterministic_penalty": round(self.deterministic_penalty, 2),
            "deterministic_penalty_max": self.deterministic_penalty_max,
            "note": self.note,
        }


_CJK_RE = re.compile(r"[一-鿿]")
_AI_TONE_HONESTY_NOTE = (
    "确定性仅覆盖对称句式/抽象价值密度/情感标签密度三类；套路金句结尾、人物声音同质、"
    "逻辑自动补全为语义判断，交 LLM 判官评，本结果不含。"
)


def _cjk_len(text: str) -> int:
    return len(_CJK_RE.findall(text or ""))


def _count_words(text: str, words: tuple[str, ...]) -> int:
    return sum(text.count(word) for word in words if word)


def _count_patterns(text: str, patterns: tuple[str, ...]) -> int:
    total = 0
    for pattern in patterns:
        if not pattern:
            continue
        try:
            total += len(re.findall(pattern, text))
        except re.error:
            continue
    return total


def _graded_penalty(density: float, threshold: float, penalty_max: int) -> float:
    """Linear ramp: 0 at/under ``threshold``, full ``penalty_max`` at 2×threshold."""

    if threshold <= 0 or density <= threshold:
        return 0.0
    fraction = min(1.0, (density - threshold) / threshold)
    return round(fraction * penalty_max, 2)


def detect_ai_tone(
    text: str, config: LitStyleConfig | None = None
) -> AiToneResult:
    """Deterministic AI腔 reading over raw prose.

    Returns densities per 1k CJK chars + a graded ``deterministic_penalty``
    (0..deterministic_penalty_max) usable as a prior for the LLM judge's
    ``ai_tone_penalty``. Never blocks anything.
    """

    config = config or load_litstyle_config()
    det = config.detector
    n = _cjk_len(text)
    if n <= 0:
        return AiToneResult(
            char_count=0,
            abstract_value_hits=0, abstract_value_per_kchars=0.0,
            symmetric_hits=0, symmetric_per_kchars=0.0,
            emotion_label_hits=0, emotion_label_per_kchars=0.0,
            flagged=(),
            deterministic_penalty=0.0,
            deterministic_penalty_max=config.deterministic_penalty_max,
            note=_AI_TONE_HONESTY_NOTE,
        )

    scale = 1000.0 / n
    abstract_hits = _count_words(text, det.abstract_value_words)
    symmetric_hits = _count_patterns(text, det.symmetric_patterns)
    emotion_hits = _count_words(text, det.emotion_label_words)

    abstract_density = abstract_hits * scale
    symmetric_density = symmetric_hits * scale
    emotion_density = emotion_hits * scale

    # Per-marker penalty_max from config (deterministic markers only).
    pmax = {m.marker_id: m.penalty_max for m in config.ai_tone_markers if m.deterministic}
    p_abstract = _graded_penalty(
        abstract_density, det.abstract_value_per_kchars, pmax.get("abstract_value_density", 4)
    )
    p_symmetric = _graded_penalty(
        symmetric_density, det.symmetric_per_kchars, pmax.get("symmetric_syntax", 4)
    )
    p_emotion = _graded_penalty(
        emotion_density, det.emotion_label_per_kchars, pmax.get("emotion_label_substitution", 3)
    )

    flagged: list[str] = []
    if p_abstract > 0:
        flagged.append("abstract_value_density")
    if p_symmetric > 0:
        flagged.append("symmetric_syntax")
    if p_emotion > 0:
        flagged.append("emotion_label_substitution")

    total_penalty = min(
        float(config.deterministic_penalty_max), p_abstract + p_symmetric + p_emotion
    )
    return AiToneResult(
        char_count=n,
        abstract_value_hits=abstract_hits,
        abstract_value_per_kchars=abstract_density,
        symmetric_hits=symmetric_hits,
        symmetric_per_kchars=symmetric_density,
        emotion_label_hits=emotion_hits,
        emotion_label_per_kchars=emotion_density,
        flagged=tuple(flagged),
        deterministic_penalty=round(total_penalty, 2),
        deterministic_penalty_max=config.deterministic_penalty_max,
        note=_AI_TONE_HONESTY_NOTE,
    )


__all__ = [
    "AiToneMarker",
    "AiToneResult",
    "CalibrationAnchor",
    "DeterministicDetectorParams",
    "LitStyleConfig",
    "LitStyleDimension",
    "LitStyleLevel",
    "detect_ai_tone",
    "is_premium_writer_model",
    "litstyle_level_for_score",
    "litstyle_target_for_writer_model",
    "load_litstyle_config",
    "render_prose_lever_framing",
]
