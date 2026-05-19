"""Quantifiable quality detectors shared by writer + critic + dashboard.

Pure-function, regex/keyword-based detectors. They consume chapter
text and the relevant loader output (pulse-words, banned-patterns,
signature-words, scene-type sensory requirements …) and return
typed result dataclasses the pipeline can persist alongside the
critic LLM output.

By design these detectors are deterministic and do not call any LLM
— they exist precisely so the framework can fail-fast on the
mechanically detectable rules before the expensive LLM pass.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from bestseller.services.quality_levers.platform_profiles import (
    load_platform_profiles,
)
from bestseller.services.quality_levers.prose_style_anchors import (
    BannedPattern,
    get_anti_ai_banned_patterns,
)
from bestseller.services.quality_levers.sensory_inventory import (
    get_scene_requirement,
    load_sensory_inventory,
)

_CJK_RE = re.compile(r"[一-鿿]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:['\u2019-][A-Za-z]+)?")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_DEFAULT_DUMPING_MARKERS = (
    "十五年前",
    "师父",
    "当年",
    "原来",
    "其实",
    "回忆",
    "昨夜",
)
_FRAMEWORK_WORD_COUNT_PLATFORMS = {"framework", "project", "bestseller", "generation"}
_ENGLISH_PULSE_WORDS = (
    "attack",
    "blood",
    "break",
    "burn",
    "chase",
    "crack",
    "crash",
    "danger",
    "deadline",
    "die",
    "dying",
    "explosion",
    "fear",
    "forced",
    "grab",
    "hit",
    "kill",
    "knife",
    "locked",
    "panic",
    "pain",
    "pressure",
    "risk",
    "run",
    "scream",
    "shock",
    "threat",
    "trapped",
    "urgent",
    "warning",
)


# ---------------------------------------------------------------------------
# Basic counters
# ---------------------------------------------------------------------------


def count_cjk_chars(text: str) -> int:
    """Count CJK (Chinese) characters in ``text``."""

    return len(_CJK_RE.findall(text or ""))


def count_latin_words(text: str) -> int:
    """Count Latin-script prose words in ``text``."""

    return len(_LATIN_WORD_RE.findall(text or ""))


def _is_english_language(language: str | None) -> bool:
    return (language or "").strip().lower().startswith("en")


def keyword_total(text: str, keywords: tuple[str, ...] | list[str]) -> int:
    """Sum non-overlapping occurrences of each keyword in ``text``."""

    if not text or not keywords:
        return 0
    return sum(text.count(word) for word in keywords if word)


def keyword_hits(
    text: str, keywords: tuple[str, ...] | list[str]
) -> tuple[tuple[str, int], ...]:
    """Return ``((word, count), ...)`` for keywords that appear at least once."""

    if not text or not keywords:
        return ()
    counts = [(word, text.count(word)) for word in keywords if word]
    return tuple((word, count) for word, count in counts if count > 0)


# ---------------------------------------------------------------------------
# Pulse / heart-rate density
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PulseDensityResult:
    """Result of the pulse-words density check."""

    pulse_count: int
    cjk_chars: int
    density_per_300_chars: float
    threshold: float
    passed: bool
    unit: str = "cjk_chars"
    applicable: bool = True
    reason: str = "ok"


def compute_pulse_density(
    text: str,
    *,
    threshold_per_300: float = 1.0,
    language: str | None = None,
) -> PulseDensityResult:
    """Measure how often heart-rate / pulse words appear in ``text``.

    Chinese pulse vocabulary is loaded from ``platform_profiles.yaml``.
    English projects use a small deterministic pressure-word pack so that
    CJK-only counters do not turn every English chapter into a hard failure.
    The threshold defaults to ``1 hit per 300 language-appropriate words``.
    """

    if _is_english_language(language):
        word_count = count_latin_words(text)
        lowered = (text or "").lower()
        pulse_count = sum(
            len(re.findall(rf"\b{re.escape(word)}\b", lowered))
            for word in _ENGLISH_PULSE_WORDS
        )
        density = pulse_count / (word_count / 300) if word_count else 0.0
        return PulseDensityResult(
            pulse_count=pulse_count,
            cjk_chars=word_count,
            density_per_300_chars=density,
            threshold=threshold_per_300,
            passed=density >= threshold_per_300,
            unit="english_words",
        )

    config = load_platform_profiles()
    pulse_words = config.pulse_words.all_words
    pulse_count = keyword_total(text, pulse_words)
    chars = count_cjk_chars(text)
    density = pulse_count / (chars / 300) if chars else 0.0
    return PulseDensityResult(
        pulse_count=pulse_count,
        cjk_chars=chars,
        density_per_300_chars=density,
        threshold=threshold_per_300,
        passed=density >= threshold_per_300,
    )


# ---------------------------------------------------------------------------
# AI-voice banned patterns
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BannedPatternHit:
    pattern_id: str
    count: int


@dataclass(frozen=True)
class BannedPatternsResult:
    total_hits: int
    threshold: int
    passed: bool
    hits: tuple[BannedPatternHit, ...]


# These regex shortcuts mirror the YAML ``pattern`` shorthand. The
# YAML stores human-readable descriptions; for deterministic scanning
# we keep the actual regex bodies here keyed by ``pattern_id``.
_BANNED_PATTERN_REGEX: dict[str, str] = {
    "parallel_action": r"一边[^\n]{0,40}一边",
    "not_only_but_also": r"不仅[^\n]{0,40}还",
    "looks_like_actually": r"看似[^\n]{0,40}实则",
    "smooth_transition": (
        r"那不是最要命的"
        r"|最要命的是"
        r"|更要命的是"
        r"|更糟的是"
        r"|更重要的是"
    ),
    "emotion_label": r"他感到|她感到|他心想|她心想|他意识到|她意识到|忽然明白",
    "explanatory_dialogue": r"这意味着|这是因为|原来是|原来如此",
    "weak_verbs": r"做了一个|做出了|进行了一次|实施了|实现了",
    "cliched_metaphor": r"像[^\n，。；！？]{1,10}一样[^\n，。；！？]{0,15}",
}
_BANNED_PATTERN_ALLOWANCE: dict[str, int] = {
    # A small number of concrete similes is normal prose. Treat this as an
    # AI-flavor problem only when it clusters in one chapter.
    "cliched_metaphor": 2,
}


def scan_banned_patterns(
    text: str,
    *,
    threshold: int = 0,
    banned_patterns: tuple[BannedPattern, ...] | None = None,
) -> BannedPatternsResult:
    """Scan ``text`` for AI-voice banned patterns.

    Each ``pattern_id`` declared in :func:`get_anti_ai_banned_patterns`
    contributes its ``count`` of regex matches (when a regex body is
    registered for that id). The default ``threshold`` is 0 — any hit
    is a violation.
    """

    if not text:
        return BannedPatternsResult(0, threshold, True, ())
    patterns = banned_patterns or get_anti_ai_banned_patterns()
    hits: list[BannedPatternHit] = []
    total = 0
    for bp in patterns:
        regex = _BANNED_PATTERN_REGEX.get(bp.pattern_id)
        if not regex:
            continue
        raw_count = len(re.findall(regex, text))
        count = max(0, raw_count - _BANNED_PATTERN_ALLOWANCE.get(bp.pattern_id, 0))
        if count > 0:
            hits.append(BannedPatternHit(pattern_id=bp.pattern_id, count=count))
            total += count
    return BannedPatternsResult(
        total_hits=total,
        threshold=threshold,
        passed=total <= threshold,
        hits=tuple(hits),
    )


# ---------------------------------------------------------------------------
# Abstract sensory term violation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AbstractSensoryResult:
    total_hits: int
    threshold: int
    passed: bool
    hits: tuple[tuple[str, int], ...]


def scan_abstract_sensory_terms(
    text: str,
    *,
    threshold: int = 0,
) -> AbstractSensoryResult:
    """Count occurrences of banned abstract sensory adjectives in ``text``.

    Banned vocabulary comes from
    ``sensory_inventory.yaml::critic_checks.abstraction_violation``.
    Object terms in dialogue (between Chinese quotation marks) are
    excluded — the YAML rule says the ban applies to narration only.
    """

    if not text:
        return AbstractSensoryResult(0, threshold, True, ())
    config = load_sensory_inventory()
    banned = config.banned_abstract_terms
    # Strip quoted dialogue so the detector only inspects narration.
    # Chinese left/right double quotation marks are U+201C / U+201D.
    narration = re.sub(
        r'[“"][^“”"]*[”"]',
        "",
        text,
    )
    hits = keyword_hits(narration, banned)
    total = sum(count for _, count in hits)
    return AbstractSensoryResult(
        total_hits=total,
        threshold=threshold,
        passed=total <= threshold,
        hits=hits,
    )


# ---------------------------------------------------------------------------
# Psychological dumping (long-internal-monologue scan)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DumpingHit:
    paragraph_index: int
    cjk_length: int
    background_marker_count: int
    snippet: str


@dataclass(frozen=True)
class DumpingResult:
    total_hits: int
    threshold: int
    passed: bool
    hits: tuple[DumpingHit, ...]


def detect_psychological_dumping(
    text: str,
    *,
    min_paragraph_chars: int = 150,
    min_background_markers: int = 2,
    threshold: int = 0,
    background_markers: tuple[str, ...] = _DEFAULT_DUMPING_MARKERS,
) -> DumpingResult:
    """Flag paragraphs that combine length + multiple background hooks.

    Mirrors the heuristic used in the multi-persona audits. The
    pipeline can persist :class:`DumpingHit` records so the editor
    knows which paragraphs to compress.
    """

    if not text:
        return DumpingResult(0, threshold, True, ())
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]
    hits: list[DumpingHit] = []
    for index, paragraph in enumerate(paragraphs, start=1):
        chars = count_cjk_chars(paragraph)
        if chars < min_paragraph_chars:
            continue
        marker_count = sum(
            1 for marker in background_markers if marker in paragraph
        )
        if marker_count < min_background_markers:
            continue
        hits.append(
            DumpingHit(
                paragraph_index=index,
                cjk_length=chars,
                background_marker_count=marker_count,
                snippet=paragraph[:60],
            )
        )
    return DumpingResult(
        total_hits=len(hits),
        threshold=threshold,
        passed=len(hits) <= threshold,
        hits=tuple(hits),
    )


# ---------------------------------------------------------------------------
# Signature density (character voice presence)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignatureDensityResult:
    total_hits: int
    threshold: int
    passed: bool
    hits: tuple[tuple[str, int], ...]


def measure_signature_density(
    text: str,
    *,
    signature_words: tuple[str, ...],
    threshold: int = 10,
) -> SignatureDensityResult:
    """Sum the occurrences of a character's signature vocabulary in ``text``."""

    hits = keyword_hits(text, signature_words)
    total = sum(count for _, count in hits)
    return SignatureDensityResult(
        total_hits=total,
        threshold=threshold,
        passed=total >= threshold,
        hits=hits,
    )


@dataclass(frozen=True)
class ForbiddenVoiceResult:
    total_hits: int
    threshold: int
    passed: bool
    hits: tuple[tuple[str, int], ...]


def scan_forbidden_voice_words(
    text: str,
    *,
    forbidden_words: tuple[str, ...],
    threshold: int = 0,
) -> ForbiddenVoiceResult:
    """Detect words explicitly forbidden by participating characters' voice DNA."""

    hits = keyword_hits(text, forbidden_words)
    total = sum(count for _, count in hits)
    return ForbiddenVoiceResult(
        total_hits=total,
        threshold=threshold,
        passed=total <= threshold,
        hits=hits,
    )


# ---------------------------------------------------------------------------
# Sensory coverage per scene
# ---------------------------------------------------------------------------


# Lightweight keyword sets per axis used to estimate coverage. These
# are intentionally small + neutral — they exist to detect the absence
# of an axis (so the writer / critic can ask "did we forget olfactory?"),
# not to enforce specific phrasing.
_AXIS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "visual": ("看", "见", "瞧", "盯", "瞥", "影子", "光", "颜色", "亮", "黑暗"),
    "auditory": ("听", "声音", "响", "敲", "喊", "叫", "笑", "哭", "脚步"),
    "olfactory": ("味", "香", "腥", "臭", "焦", "气味", "气息"),
    "tactile": ("触", "摸", "拂", "握", "捏", "黏", "滑", "粗", "潮", "湿", "干"),
    "thermal": ("冷", "热", "凉", "烫", "暖", "寒", "温度"),
    "weight_and_density": ("重", "轻", "厚", "薄", "硬", "软", "沉"),
    "spatial": (
        "前", "后", "左", "右", "上", "下", "身后", "身前",
        "三寸", "半步", "门外", "门内", "越过", "对面",
    ),
    "temporal": ("一拍", "一瞬", "三息", "半盏", "时辰", "刻钟", "片刻", "停顿"),
}


@dataclass(frozen=True)
class SensoryCoverageResult:
    scene_type: str
    required_min: int
    must_include: tuple[str, ...]
    hit_axes: tuple[str, ...]
    missing_must_include: tuple[str, ...]
    coverage_ratio: float
    passed: bool


def compute_sensory_coverage(
    text: str,
    *,
    scene_type: str,
    threshold_ratio: float = 0.70,
) -> SensoryCoverageResult | None:
    """Score how many sensory axes ``text`` covers vs the scene requirement.

    Returns ``None`` when the ``scene_type`` is unknown so the caller
    can skip the check (rather than blocking the chapter on a
    missing config entry).
    """

    requirement = get_scene_requirement(scene_type)
    if requirement is None:
        return None
    if not text:
        return SensoryCoverageResult(
            scene_type=scene_type,
            required_min=requirement.required_min,
            must_include=requirement.must_include,
            hit_axes=(),
            missing_must_include=requirement.must_include,
            coverage_ratio=0.0,
            passed=False,
        )

    hits: list[str] = []
    for axis, words in _AXIS_KEYWORDS.items():
        if any(word in text for word in words):
            hits.append(axis)

    hits_set = set(hits)
    missing_must = tuple(
        axis for axis in requirement.must_include if axis not in hits_set
    )
    expected = max(requirement.required_min, len(requirement.must_include) or 1)
    ratio = min(1.0, len(hits) / expected) if expected else 0.0
    passed = ratio >= threshold_ratio and not missing_must
    return SensoryCoverageResult(
        scene_type=scene_type,
        required_min=requirement.required_min,
        must_include=requirement.must_include,
        hit_axes=tuple(hits),
        missing_must_include=missing_must,
        coverage_ratio=ratio,
        passed=passed,
    )


# ---------------------------------------------------------------------------
# Word-count gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WordCountGateResult:
    chars: int
    min_chars: int
    max_chars: int
    passed: bool
    reason: str
    unit: str = "cjk_chars"


def evaluate_word_count(
    text: str,
    *,
    platform: str | None,
    language: str | None = None,
) -> WordCountGateResult:
    """Evaluate ``text`` against the platform-specific chapter word range."""

    unit = "english_words" if _is_english_language(language) else "cjk_chars"
    chars = (
        count_latin_words(text)
        if unit == "english_words"
        else count_cjk_chars(text)
    )
    profile = None
    platform_key = str(platform or "").strip().lower()
    if platform_key in _FRAMEWORK_WORD_COUNT_PLATFORMS:
        min_chars, max_chars = _framework_word_count_range()
    else:
        config = load_platform_profiles()
        from bestseller.services.quality_levers.platform_profiles import (
            resolve_platform_id,
        )

        platform_id = resolve_platform_id(platform)
        if platform_id is not None:
            profile = config.platforms.get(platform_id)

        if profile is None or profile.pacing_preference.chapter_word_min == 0:
            # Fall back to the legacy quality-levers default.
            min_chars = 5000
            max_chars = 0
        else:
            min_chars = profile.pacing_preference.chapter_word_min
            max_chars = profile.pacing_preference.chapter_word_max

    if chars < min_chars:
        return WordCountGateResult(
            chars=chars,
            min_chars=min_chars,
            max_chars=max_chars,
            passed=False,
            reason=f"underflow: {chars} < {min_chars}",
            unit=unit,
        )
    if max_chars and chars > max_chars:
        return WordCountGateResult(
            chars=chars,
            min_chars=min_chars,
            max_chars=max_chars,
            passed=False,
            reason=f"overflow: {chars} > {max_chars}",
            unit=unit,
        )
    return WordCountGateResult(
        chars=chars,
        min_chars=min_chars,
        max_chars=max_chars,
        passed=True,
        reason="ok",
        unit=unit,
    )


def _framework_word_count_range() -> tuple[int, int]:
    try:
        from bestseller.settings import load_settings

        budget = load_settings().generation.words_per_chapter
        min_chars = int(getattr(budget, "min", 0) or 0)
        max_chars = int(getattr(budget, "max", 0) or 0)
    except Exception:
        return 5000, 0
    if min_chars <= 0:
        return 5000, 0
    return min_chars, max(0, max_chars)


# ---------------------------------------------------------------------------
# Convenience top-level fan-out
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuantitativeChapterAudit:
    """Bundle every quantitative detector into a single result."""

    word_count: WordCountGateResult
    pulse: PulseDensityResult
    banned_patterns: BannedPatternsResult
    abstract_sensory: AbstractSensoryResult
    dumping: DumpingResult
    signature_density: SignatureDensityResult | None = None
    forbidden_voice: ForbiddenVoiceResult | None = None


def audit_chapter(
    text: str,
    *,
    platform: str | None,
    language: str | None = None,
    signature_words: tuple[str, ...] = (),
    signature_threshold: int = 10,
    forbidden_words: tuple[str, ...] = (),
    forbidden_threshold: int = 0,
) -> QuantitativeChapterAudit:
    """Run every deterministic detector on a chapter text in one pass."""

    signature_density = (
        measure_signature_density(
            text,
            signature_words=signature_words,
            threshold=signature_threshold,
        )
        if signature_words
        else None
    )
    forbidden_voice = (
        scan_forbidden_voice_words(
            text,
            forbidden_words=forbidden_words,
            threshold=forbidden_threshold,
        )
        if forbidden_words
        else None
    )
    return QuantitativeChapterAudit(
        word_count=evaluate_word_count(text, platform=platform, language=language),
        pulse=compute_pulse_density(text, language=language),
        banned_patterns=scan_banned_patterns(text),
        abstract_sensory=scan_abstract_sensory_terms(text),
        dumping=detect_psychological_dumping(text),
        signature_density=signature_density,
        forbidden_voice=forbidden_voice,
    )
