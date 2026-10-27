"""Scene Grounding loader + detectors (``config/scene_grounding.yaml``).

The framework already covers *vivid* single paragraphs (``visual_writing``),
*memorable* single lines (``prose_craft_techniques``), *emotion-label* discipline
(``emotion_choreography``) and *suspense* pacing (``information_choreography``).
What it lacked is a **whole-chapter camera discipline**: keep the prose anchored
to the protagonist's immediate, plot-relevant experience instead of collapsing
into essay-like authorial summary, floating jump-cuts and name/number floods —
the failure mode the 2026-06《借运成神》pilot exposed (see
``docs/scene-grounding-cinematic-narration-2026-06.md``).

This module distils that craft into **transferable technique skeletons** and
renders a compact, genre-aware, rotation-varied writer block — exactly mirroring
the ``prose_craft_techniques`` pattern (soft, anti-purple, genre-routed, never a
gate). It also ships three **deterministic detectors** (no LLM) so the same axis
can be *measured* — used both as the A/B scoreboard and, later, as soft critic
findings:

* :func:`detect_authorial_intrusion` — commentary/exposition connective density
  (病灶 A：作者旁白 / 议论文式告知)
* :func:`measure_grounding_coverage` — fraction of narrative paragraphs carrying a
  concrete spatial/body/time anchor (病灶 B：场景悬空跳切)
* :func:`detect_proper_noun_flood` — new-name and number density (病灶 C：人名/数字洪流)

Design guarantees:

* **Soft only.** Nothing here feeds a gate / floor / ``must_rewrite``. Detectors
  return scores; the pipeline decides (body chapters: soft, advisory).
* **Techniques, not phrases.** ``micro_examples`` are synthetic and cross-genre;
  never copy them into prose (avoids the template-override regression).
* **Genre-adaptive, anti-purple.** ``genre_emphasis`` routes per genre family and
  an ``authorial_intrusion_guard`` names the "像作文" failure mode explicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)

_CONFIG_FILENAME = "scene_grounding.yaml"
_DEFAULT_EMPHASIS_KEY = "default"


# ---------------------------------------------------------------------------
# Typed view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CraftExample:
    """One ``micro_examples`` entry: a synthetic, genre-tagged line."""

    tag: str
    line: str


@dataclass(frozen=True)
class GroundingTechnique:
    """One ``techniques.<id>`` entry — a camera-discipline skeleton."""

    technique_id: str
    display_name: str
    category: str
    principle: str
    structure: str
    purple_risk: str
    genre_good: tuple[str, ...]
    genre_strong: tuple[str, ...]
    genre_careful: tuple[str, ...]
    genre_avoid: tuple[str, ...]
    examples: tuple[CraftExample, ...]

    def example_for(self, genre_terms: tuple[str, ...]) -> CraftExample | None:
        """Prefer an example whose tag matches one of the genre terms."""

        if not self.examples:
            return None
        for example in self.examples:
            if example.tag and any(example.tag in term for term in genre_terms):
                return example
        return self.examples[0]


@dataclass(frozen=True)
class IntrusionGuardMove:
    """One ``authorial_intrusion_guard.banned_moves`` entry."""

    move_id: str
    bad: str
    why: str
    fix: str


@dataclass(frozen=True)
class DetectorThresholds:
    """Numeric thresholds for the deterministic detectors (single source)."""

    authorial_intrusion_per_kchars: float
    grounding_coverage_floor: float
    max_new_names_per_paragraph: int
    number_tokens_per_kchars: float


@dataclass(frozen=True)
class SceneGroundingConfig:
    """Typed view over the YAML."""

    version: str
    techniques: dict[str, GroundingTechnique]
    intrusion_guard: tuple[IntrusionGuardMove, ...]
    genre_emphasis: dict[str, tuple[str, ...]]
    techniques_per_scene: int
    example_per_technique: int
    rotate_by_chapter: bool
    position_hint: str
    hard_rule: str
    thresholds: DetectorThresholds


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_examples(raw: object) -> tuple[CraftExample, ...]:
    examples: list[CraftExample] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            data = as_dict(entry)
            line = as_str(data.get("line"))
            if not line:
                continue
            examples.append(CraftExample(tag=as_str(data.get("tag")), line=line))
    return tuple(examples)


def _parse_technique(technique_id: str, raw: object) -> GroundingTechnique:
    data = as_dict(raw)
    genre_fit = as_dict(data.get("genre_fit"))
    return GroundingTechnique(
        technique_id=technique_id,
        display_name=as_str(data.get("display_name"), default=technique_id),
        category=as_str(data.get("category")),
        principle=as_str(data.get("principle")),
        structure=as_str(data.get("structure")),
        purple_risk=as_str(data.get("purple_risk")),
        genre_good=as_str_tuple(genre_fit.get("good")),
        genre_strong=as_str_tuple(genre_fit.get("strong")),
        genre_careful=as_str_tuple(genre_fit.get("careful")),
        genre_avoid=as_str_tuple(genre_fit.get("avoid")),
        examples=_parse_examples(data.get("micro_examples")),
    )


def _parse_intrusion_guard(raw: object) -> tuple[IntrusionGuardMove, ...]:
    data = as_dict(raw)
    moves_raw = data.get("banned_moves")
    moves: list[IntrusionGuardMove] = []
    if isinstance(moves_raw, (list, tuple)):
        for entry in moves_raw:
            entry_data = as_dict(entry)
            move_id = as_str(entry_data.get("id"))
            bad = as_str(entry_data.get("bad"))
            if not (move_id or bad):
                continue
            moves.append(
                IntrusionGuardMove(
                    move_id=move_id,
                    bad=bad,
                    why=as_str(entry_data.get("why")),
                    fix=as_str(entry_data.get("fix")),
                )
            )
    return tuple(moves)


def _parse_genre_emphasis(raw: object) -> dict[str, tuple[str, ...]]:
    data = as_dict(raw)
    emphasis: dict[str, tuple[str, ...]] = {}
    for key, value in data.items():
        key_str = as_str(key)
        ids = as_str_tuple(value)
        if key_str and ids:
            emphasis[key_str] = ids
    return emphasis


def _parse_thresholds(raw: object) -> DetectorThresholds:
    data = as_dict(raw)

    def _flt(key: str, default: float) -> float:
        try:
            return float(data.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    return DetectorThresholds(
        authorial_intrusion_per_kchars=_flt("authorial_intrusion_per_kchars", 3.0),
        grounding_coverage_floor=_flt("grounding_coverage_floor", 0.55),
        max_new_names_per_paragraph=as_int(data.get("max_new_names_per_paragraph"), default=3),
        number_tokens_per_kchars=_flt("number_tokens_per_kchars", 6.0),
    )


@lru_cache(maxsize=1)
def load_scene_grounding() -> SceneGroundingConfig:
    """Return the typed view over ``scene_grounding.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    techniques_raw = as_dict(raw.get("techniques"))
    techniques: dict[str, GroundingTechnique] = {}
    for technique_id, technique_raw in techniques_raw.items():
        canonical = as_str(technique_id)
        if not canonical:
            continue
        techniques[canonical] = _parse_technique(canonical, technique_raw)

    policy = as_dict(raw.get("injection_policy"))
    return SceneGroundingConfig(
        version=as_str(raw.get("version")),
        techniques=techniques,
        intrusion_guard=_parse_intrusion_guard(raw.get("authorial_intrusion_guard")),
        genre_emphasis=_parse_genre_emphasis(raw.get("genre_emphasis")),
        techniques_per_scene=max(1, as_int(policy.get("techniques_per_scene"), default=3)),
        example_per_technique=max(1, as_int(policy.get("example_per_technique"), default=1)),
        rotate_by_chapter=bool(policy.get("rotate_by_chapter", True)),
        position_hint=as_str(policy.get("position_hint")),
        hard_rule=as_str(policy.get("hard_rule")),
        thresholds=_parse_thresholds(raw.get("detector_thresholds")),
    )


def get_grounding_technique(technique_id: str) -> GroundingTechnique | None:
    """Look up one technique."""

    if not technique_id:
        return None
    return load_scene_grounding().techniques.get(technique_id)


# ---------------------------------------------------------------------------
# Genre routing + rotation (mirrors prose_craft_techniques)
# ---------------------------------------------------------------------------


def resolve_genre_emphasis_key(
    genre_terms: tuple[str, ...] | list[str],
    config: SceneGroundingConfig | None = None,
) -> str:
    """Pick the ``genre_emphasis`` key best matching the supplied genre terms.

    Matching is substring-based and order-stable: the first emphasis key (other
    than ``default``) that appears inside any genre term wins. Falls back to
    ``default``.
    """

    config = config or load_scene_grounding()
    terms = tuple(as_str(term) for term in genre_terms if as_str(term))
    if not terms:
        return _DEFAULT_EMPHASIS_KEY
    # Prefer the longest matching key so "都市异能" beats "都市" when both exist.
    best: str | None = None
    for key in config.genre_emphasis:
        if key == _DEFAULT_EMPHASIS_KEY:
            continue
        if any(key in term for term in terms):
            if best is None or len(key) > len(best):
                best = key
    return best or _DEFAULT_EMPHASIS_KEY


def select_techniques(
    *,
    genre_terms: tuple[str, ...] | list[str] = (),
    chapter_number: int = 1,
    config: SceneGroundingConfig | None = None,
) -> tuple[GroundingTechnique, ...]:
    """Return the genre-appropriate technique subset for this chapter.

    The emphasis list is a *superset*; we expose ``techniques_per_scene`` of
    them, rotating the window by chapter number so consecutive chapters do not
    keep recommending the same skeletons (an anti-homogenisation guard).
    """

    config = config or load_scene_grounding()
    key = resolve_genre_emphasis_key(genre_terms, config)
    ordered_ids = config.genre_emphasis.get(key) or config.genre_emphasis.get(
        _DEFAULT_EMPHASIS_KEY, ()
    )
    resolved = [config.techniques[tid] for tid in ordered_ids if tid in config.techniques]
    if not resolved:
        return ()

    want = min(config.techniques_per_scene, len(resolved))
    if config.rotate_by_chapter and len(resolved) > want:
        start = (max(1, int(chapter_number)) - 1) % len(resolved)
        window = [resolved[(start + offset) % len(resolved)] for offset in range(want)]
        return tuple(window)
    return tuple(resolved[:want])


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def _render_intrusion_guard_line(config: SceneGroundingConfig) -> str:
    if not config.intrusion_guard:
        return ""
    bads = "；".join(move.bad for move in config.intrusion_guard if move.bad)
    if not bads:
        return ""
    return f"⚠ 镜头化≠作者旁白，忌：{bads}"


def render_scene_grounding_block(
    *,
    genre_terms: tuple[str, ...] | list[str] = (),
    chapter_number: int = 1,
) -> str:
    """Render the compact, writer-facing 镜头化场景锚定 fragment.

    Returns ``""`` when the config has no techniques, so callers can treat it as
    an optional section that degrades gracefully.
    """

    config = load_scene_grounding()
    techniques = select_techniques(
        genre_terms=genre_terms, chapter_number=chapter_number, config=config
    )
    if not techniques:
        return ""

    terms = tuple(as_str(term) for term in genre_terms if as_str(term))
    lines: list[str] = [
        "【场景锚定 · 镜头化叙事（站在主角立场写，每处描写服务剧情）】",
        "本场按下列骨架落地：定场/转场要有具体锚点，设定靠演不靠讲，"
        "禁止作者跳出来用议论解说剧情——读者要「看到」而不是「被告知」：",
    ]
    for technique in techniques:
        example = technique.example_for(terms)
        line = f"- {technique.display_name}：{technique.structure}"
        if example and example.line:
            line += f"｜例：{example.line}"
        lines.append(line)
    if config.position_hint:
        lines.append(f"位置：{config.position_hint}")
    guard = _render_intrusion_guard_line(config)
    if guard:
        lines.append(guard)
    return "\n".join(lines)


# ===========================================================================
# Deterministic detectors (no LLM) — the measurement instrument
# ===========================================================================

# Dialogue stripper. Chinese web novels mix full-width double quotes (“”),
# ASCII quotes ("), and corner brackets (「」『』) for spoken lines / system
# text — strip all of them so a character arguing causally inside dialogue is
# never miscounted as the *narrator* explaining the plot.
_DIALOGUE_RE = re.compile(r'[“"「『][^”"」』]*[”"」』]')
_CJK_RE = re.compile(r"[一-鿿]")

# --- A. authorial intrusion ------------------------------------------------
# Commentary / exposition connectives that signal the *author* explaining plot
# causation or theme, rather than the protagonist *experiencing* the scene.
# Multi-char markers chosen to minimise false positives from ordinary prose.
_COMMENTARY_MARKERS: tuple[str, ...] = (
    "之所以",
    "是因为",
    "这意味着",
    "意味着",
    "换句话说",
    "换言之",
    "也就是说",
    "也就是",
    "说到底",
    "归根结底",
    "归根到底",
    "本质上",
    "实质上",
    "究其原因",
    "原因在于",
    "正是因为",
    "不是因为",
    "而是因为",
    "不难看出",
    "由此可见",
    "某种意义上",
    "严格来说",
    "被当成",
    "被当作",
    "这一切都",
    "道理很简单",
    # expansion validated on 6 real samples — these caught ch5's causal
    # author-summary without firing on the cinematic GOOD versions (which stay 0).
    "所以他",
    "所以她",
    "因为他",
    "因为她",
    "知道他",
    "知道她",
    "其实是",
    "等于",
    "无非是",
    "不过是",
    "这就是为什么",
)


@dataclass(frozen=True)
class AuthorialIntrusionResult:
    """病灶 A：作者旁白 / 议论文式告知。"""

    passed: bool
    hits: int
    density_per_kchars: float
    threshold: float
    examples: tuple[str, ...]


def _strip_dialogue(text: str) -> str:
    return _DIALOGUE_RE.sub("", text or "")


def _cjk_len(text: str) -> int:
    return len(_CJK_RE.findall(text or ""))


def detect_authorial_intrusion(
    text: str,
    *,
    threshold_per_kchars: float | None = None,
) -> AuthorialIntrusionResult:
    """Measure commentary-connective density in the narration layer.

    High density ⇒ the chapter is *explaining* rather than *dramatising* — the
    "像作文" failure mode. Dialogue is stripped first (characters may legitimately
    argue causally; the ban is on the narrator doing it).
    """

    config = load_scene_grounding()
    threshold = (
        config.thresholds.authorial_intrusion_per_kchars
        if threshold_per_kchars is None
        else threshold_per_kchars
    )
    narration = _strip_dialogue(text)
    cjk = _cjk_len(narration)
    hits = 0
    examples: list[str] = []
    for marker in _COMMENTARY_MARKERS:
        count = narration.count(marker)
        if count > 0:
            hits += count
            if len(examples) < 6:
                examples.append(marker)
    density = (hits / cjk * 1000.0) if cjk else 0.0
    return AuthorialIntrusionResult(
        passed=density <= threshold,
        hits=hits,
        density_per_kchars=round(density, 2),
        threshold=threshold,
        examples=tuple(examples),
    )


# --- B. grounding coverage -------------------------------------------------
# Concrete spatial / body / time / sensory-object anchors. A genuinely
# scene-grounded paragraph hits ≥1; a pure abstract-commentary paragraph
# ("合同编号触发了追踪模型，三个词凑齐，自动打标签") hits none.
_ANCHOR_TOKENS: tuple[str, ...] = (
    # spatial / motion / objects
    "站", "坐", "蹲", "躺", "推开", "走进", "走出", "走到", "迈", "退",
    "转身", "抬头", "低头", "回头", "弯腰", "靠", "扑", "倒",
    "门", "窗", "墙", "桌", "椅", "床", "灯", "街", "巷", "路", "车",
    "楼", "屋", "房", "地面", "台阶", "电梯", "楼道", "檐", "桥", "梯",
    # body
    "手", "指", "掌", "脚", "腿", "头", "眼", "脸", "嘴", "喉", "肩",
    "背", "胸", "膝", "腕", "拳", "呼吸", "牙", "唇", "眉",
    # time
    "点", "分", "秒", "早", "晨", "午", "夜", "晚", "凌晨", "黄昏",
    "傍晚", "半夜", "这时", "此刻", "片刻", "一瞬",
    # weather / sensory objects
    "雨", "风", "雪", "烟", "光", "火", "血", "水", "声", "味",
)


@dataclass(frozen=True)
class GroundingCoverageResult:
    """病灶 B：场景悬空 / 无锚跳切。"""

    passed: bool
    coverage: float
    floor: float
    narrative_paragraphs: int
    anchored_paragraphs: int
    floating_examples: tuple[str, ...]


def _split_paragraphs(text: str) -> list[str]:
    """Split a chapter into paragraph units.

    The house format puts one short paragraph per line; markdown blank lines
    separate them. We treat each non-empty, non-heading line as a unit.
    """

    units: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        units.append(line)
    return units


def measure_grounding_coverage(
    text: str,
    *,
    floor: float | None = None,
) -> GroundingCoverageResult:
    """Fraction of narrative paragraphs carrying a concrete anchor.

    A paragraph is considered *anchored* when it contains dialogue (a spoken
    line grounds the immediate moment) or at least one concrete spatial / body /
    time / sensory anchor token. Floating, abstract paragraphs drag the
    coverage down — the deterministic signal for "essay-like" prose.
    """

    config = load_scene_grounding()
    floor_value = config.thresholds.grounding_coverage_floor if floor is None else floor
    paragraphs = _split_paragraphs(text)
    narrative = 0
    anchored = 0
    floating: list[str] = []
    for para in paragraphs:
        if _cjk_len(para) < 6:
            # Very short lines (interjections, 「嗯。」) are not penalised.
            continue
        narrative += 1
        has_dialogue = bool(_DIALOGUE_RE.search(para))
        has_anchor = any(token in para for token in _ANCHOR_TOKENS)
        if has_dialogue or has_anchor:
            anchored += 1
        elif len(floating) < 6:
            floating.append(para[:40])
    coverage = (anchored / narrative) if narrative else 1.0
    return GroundingCoverageResult(
        passed=coverage >= floor_value,
        coverage=round(coverage, 3),
        floor=floor_value,
        narrative_paragraphs=narrative,
        anchored_paragraphs=anchored,
        floating_examples=tuple(floating),
    )


# --- C. proper-noun / number flood -----------------------------------------
_COMMON_SURNAMES = (
    "王李张刘陈杨赵黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹"
    "袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏锺汪田任姜范方石姚"
    "谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔"
)
_NAME_RE = re.compile(rf"[{_COMMON_SURNAMES}][一-鿿]{{1,2}}")
# Numbers: arabic, or Chinese numeral + measure word, or 百分之X.
_NUMBER_RE = re.compile(
    r"[0-9]+(?:\.[0-9]+)?%?"
    r"|百分之[零一二三四五六七八九十百千0-9点]+"
    r"|[一二三四五六七八九十百千万两]+(?:个|人|名|次|年|天|块|层|分|点|岁|条|起|笔|位|成)"
)


@dataclass(frozen=True)
class ProperNounFloodResult:
    """病灶 C：人名 / 数字洪流（启发式，soft）。"""

    passed: bool
    max_names_per_paragraph: int
    name_cap: int
    number_tokens: int
    number_density_per_kchars: float
    number_threshold: float


def detect_proper_noun_flood(
    text: str,
    *,
    name_cap: int | None = None,
    number_threshold_per_kchars: float | None = None,
) -> ProperNounFloodResult:
    """Heuristic name/number-flood detector.

    ``max_names_per_paragraph`` uses a surname-based candidate match (noisy by
    design — only the *flood* signal, ≥ cap distinct candidates in one
    paragraph, is meaningful). Number density counts arabic + Chinese numeral
    expressions across the whole chapter.
    """

    config = load_scene_grounding()
    cap = config.thresholds.max_new_names_per_paragraph if name_cap is None else name_cap
    num_threshold = (
        config.thresholds.number_tokens_per_kchars
        if number_threshold_per_kchars is None
        else number_threshold_per_kchars
    )
    paragraphs = _split_paragraphs(text)
    max_names = 0
    for para in paragraphs:
        distinct = {m.group(0) for m in _NAME_RE.finditer(para)}
        max_names = max(max_names, len(distinct))
    number_tokens = len(_NUMBER_RE.findall(text or ""))
    cjk = _cjk_len(text)
    density = (number_tokens / cjk * 1000.0) if cjk else 0.0
    passed = max_names <= cap and density <= num_threshold
    return ProperNounFloodResult(
        passed=passed,
        max_names_per_paragraph=max_names,
        name_cap=cap,
        number_tokens=number_tokens,
        number_density_per_kchars=round(density, 2),
        number_threshold=num_threshold,
    )


# --- combined audit --------------------------------------------------------


@dataclass(frozen=True)
class SceneGroundingAudit:
    """All three deterministic scene-grounding signals for one chapter."""

    passed: bool
    intrusion: AuthorialIntrusionResult
    coverage: GroundingCoverageResult
    flood: ProperNounFloodResult

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "authorial_intrusion": {
                "passed": self.intrusion.passed,
                "density_per_kchars": self.intrusion.density_per_kchars,
                "threshold": self.intrusion.threshold,
                "hits": self.intrusion.hits,
                "examples": list(self.intrusion.examples),
            },
            "grounding_coverage": {
                "passed": self.coverage.passed,
                "coverage": self.coverage.coverage,
                "floor": self.coverage.floor,
                "narrative_paragraphs": self.coverage.narrative_paragraphs,
                "anchored_paragraphs": self.coverage.anchored_paragraphs,
            },
            "proper_noun_flood": {
                "passed": self.flood.passed,
                "max_names_per_paragraph": self.flood.max_names_per_paragraph,
                "name_cap": self.flood.name_cap,
                "number_density_per_kchars": self.flood.number_density_per_kchars,
                "number_threshold": self.flood.number_threshold,
            },
        }


def audit_scene_grounding(text: str) -> SceneGroundingAudit:
    """Run all three deterministic detectors and aggregate (soft) pass/fail.

    The aggregate verdict is driven by the two validated discriminators —
    authorial intrusion (A) and grounding coverage (B). The proper-noun/number
    flood (C) is **advisory only**: number density is genre-confounded (debt /
    cultivation / ranking stories are inherently number-heavy, so good prose
    can score as high as bad), and the name heuristic is noisy. We therefore
    report C for diagnostics but never let it flip the verdict — A/B experiments
    showed C produced false "fail"s on grounded, number-rich treatment prose.
    """

    intrusion = detect_authorial_intrusion(text)
    coverage = measure_grounding_coverage(text)
    flood = detect_proper_noun_flood(text)
    return SceneGroundingAudit(
        passed=intrusion.passed and coverage.passed,
        intrusion=intrusion,
        coverage=coverage,
        flood=flood,
    )


__all__ = [
    "AuthorialIntrusionResult",
    "CraftExample",
    "DetectorThresholds",
    "GroundingCoverageResult",
    "GroundingTechnique",
    "IntrusionGuardMove",
    "ProperNounFloodResult",
    "SceneGroundingAudit",
    "SceneGroundingConfig",
    "audit_scene_grounding",
    "detect_authorial_intrusion",
    "detect_proper_noun_flood",
    "get_grounding_technique",
    "load_scene_grounding",
    "measure_grounding_coverage",
    "render_scene_grounding_block",
    "resolve_genre_emphasis_key",
    "select_techniques",
]
