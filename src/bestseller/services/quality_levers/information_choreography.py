"""Information Choreography loader (``config/information_choreography.yaml``).

Provides typed access to:

* ``ReaderBelief`` — entries in the global ``belief_audit``
* ``ReaderCuriosity`` — entries in the ``reader_curiosity_ledger``
* ``InformationMode`` — the 4 information-control modes
* ``InformationFlowState`` — per-chapter accumulated state

The pipeline glue is expected to maintain :class:`InformationFlowState`
in the project database (Step E) so the orchestrator can:

#. detect ``belief_overdue`` violations chapter-by-chapter
#. enforce the ``open_question_ceiling`` (≤ 8)
#. require ``new_curiosity_added >= 1`` per chapter (unless climax)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)


_CONFIG_FILENAME = "information_choreography.yaml"


@dataclass(frozen=True)
class ReaderBelief:
    """One ``sample_belief_audit.<id>`` entry."""

    belief_id: str
    planted_in_chapter: int
    reader_belief: str
    truth: str
    payoff_chapter: int
    payoff_method: str
    misdirection_type: str
    curiosity_level: int


@dataclass(frozen=True)
class InformationMode:
    """One of the 4 ``information_modes.<id>`` entries."""

    mode_id: str
    display: str
    effect: str
    usage: str


@dataclass(frozen=True)
class HardIndicator:
    indicator_id: str
    rule: str
    rationale: str


@dataclass(frozen=True)
class InformationChoreographyConfig:
    """Typed view over the YAML."""

    version: str
    hard_indicators: tuple[HardIndicator, ...]
    information_modes: dict[str, InformationMode]
    sample_belief_audit: dict[str, ReaderBelief]
    belief_max_distance: int
    open_question_ceiling: int


def _parse_belief(belief_id: str, raw: object) -> ReaderBelief:
    data = as_dict(raw)
    return ReaderBelief(
        belief_id=belief_id,
        planted_in_chapter=as_int(data.get("planted_in_chapter"), default=0),
        reader_belief=as_str(data.get("reader_belief")),
        truth=as_str(data.get("truth")),
        payoff_chapter=as_int(data.get("payoff_chapter"), default=0),
        payoff_method=as_str(data.get("payoff_method")),
        misdirection_type=as_str(data.get("misdirection_type")),
        curiosity_level=as_int(data.get("curiosity_level"), default=0),
    )


def _parse_information_modes(raw: object) -> dict[str, InformationMode]:
    if not isinstance(raw, list):
        return {}
    modes: dict[str, InformationMode] = {}
    for entry in raw:
        body = as_dict(entry)
        mode_id = as_str(body.get("mode"))
        if not mode_id:
            continue
        modes[mode_id] = InformationMode(
            mode_id=mode_id,
            display=as_str(body.get("display"), default=mode_id),
            effect=as_str(body.get("effect")),
            usage=as_str(body.get("usage")),
        )
    return modes


def _parse_hard_indicators(raw: object) -> tuple[HardIndicator, ...]:
    data = as_dict(raw)
    out: list[HardIndicator] = []
    for indicator_id, body in data.items():
        body_dict = as_dict(body)
        out.append(
            HardIndicator(
                indicator_id=as_str(indicator_id),
                rule=as_str(body_dict.get("rule")),
                rationale=as_str(body_dict.get("rationale")),
            )
        )
    return tuple(out)


def _extract_belief_max_distance(text: str) -> int:
    """Pull the first integer out of the ``belief_payoff_distance.rule`` text."""

    digits = "".join(ch if ch.isdigit() else " " for ch in text).split()
    if not digits:
        return 5
    return int(digits[0])


def _extract_open_question_ceiling(text: str) -> int:
    digits = "".join(ch if ch.isdigit() else " " for ch in text).split()
    if not digits:
        return 8
    return int(digits[0])


@lru_cache(maxsize=1)
def load_information_choreography() -> InformationChoreographyConfig:
    """Return the typed view over ``information_choreography.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    hard = _parse_hard_indicators(raw.get("per_chapter_hard_indicators"))

    # Pull the magic numbers out of the human-readable rule strings so
    # the detector module doesn't have to re-parse them at runtime.
    belief_distance = 5
    open_question = 8
    for indicator in hard:
        if indicator.indicator_id == "belief_payoff_distance":
            belief_distance = _extract_belief_max_distance(indicator.rule)
        elif indicator.indicator_id == "open_question_ceiling":
            open_question = _extract_open_question_ceiling(indicator.rule)

    modes = _parse_information_modes(raw.get("information_modes"))

    beliefs_raw = as_dict(raw.get("sample_belief_audit"))
    beliefs: dict[str, ReaderBelief] = {}
    for belief_id, body in beliefs_raw.items():
        canonical = as_str(belief_id)
        if not canonical:
            continue
        beliefs[canonical] = _parse_belief(canonical, body)

    return InformationChoreographyConfig(
        version=as_str(raw.get("version")),
        hard_indicators=hard,
        information_modes=modes,
        sample_belief_audit=beliefs,
        belief_max_distance=belief_distance,
        open_question_ceiling=open_question,
    )


# ---------------------------------------------------------------------------
# Per-chapter state evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InformationFlowState:
    """In-memory snapshot for one chapter's information state.

    The persistence layer (Step E) will materialise this into rows of
    ``information_state_per_chapter``.
    """

    chapter_number: int
    new_facts: tuple[str, ...] = ()
    new_questions: tuple[str, ...] = ()
    new_beliefs_planted: tuple[str, ...] = ()
    beliefs_paid_off: tuple[str, ...] = ()
    open_questions_count: int = 0


@dataclass(frozen=True)
class InformationFlowVerdict:
    chapter_number: int
    new_curiosity_ok: bool
    open_question_ok: bool
    overdue_beliefs: tuple[str, ...]
    must_rewrite: bool
    reasons: tuple[str, ...]


def evaluate_information_state(
    state: InformationFlowState,
    *,
    active_beliefs: dict[str, ReaderBelief] | None = None,
) -> InformationFlowVerdict:
    """Run the deterministic checks on a chapter's information state."""

    config = load_information_choreography()
    reasons: list[str] = []

    new_curiosity_ok = (
        len(state.new_questions) >= 1 or len(state.new_beliefs_planted) >= 1
    )
    if not new_curiosity_ok:
        reasons.append(
            "no_new_curiosity_added: every chapter must add ≥ 1 new question"
        )

    open_question_ok = state.open_questions_count <= config.open_question_ceiling
    if not open_question_ok:
        reasons.append(
            f"open_question_ceiling: {state.open_questions_count} "
            f"> {config.open_question_ceiling}"
        )

    overdue: list[str] = []
    if active_beliefs:
        for belief_id, belief in active_beliefs.items():
            distance = state.chapter_number - belief.planted_in_chapter
            if (
                distance > config.belief_max_distance
                and belief_id not in state.beliefs_paid_off
            ):
                overdue.append(belief_id)
    if overdue:
        reasons.append("belief_overdue: " + ", ".join(overdue))

    must_rewrite = bool(reasons)

    return InformationFlowVerdict(
        chapter_number=state.chapter_number,
        new_curiosity_ok=new_curiosity_ok,
        open_question_ok=open_question_ok,
        overdue_beliefs=tuple(overdue),
        must_rewrite=must_rewrite,
        reasons=tuple(reasons),
    )


def render_information_choreography_block(*, chapter_number: int) -> str:
    """Render the writer-facing fragment with the hard indicators."""

    config = load_information_choreography()
    if not config.hard_indicators:
        return ""
    lines: list[str] = ["【information_choreography · 悬念工程契约】"]
    for indicator in config.hard_indicators:
        lines.append(f"- {indicator.indicator_id}: {indicator.rule}")
    lines.append(
        f"- 本章 (#{chapter_number}) 必须新增 ≥ 1 个 curiosity 条目，"
        f"open_questions 不超过 {config.open_question_ceiling}，"
        f"任何 belief 必须在 {config.belief_max_distance} 章内开始偿付。"
    )
    return "\n".join(lines)
