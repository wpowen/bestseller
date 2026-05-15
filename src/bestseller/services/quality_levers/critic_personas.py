"""Critic Personas loader and aggregator (``config/critic_personas.yaml``).

Provides:

* ``CriticPersona`` — one of the four reader-personas used by the
  multi-persona review pass (``platform_editor`` / ``new_reader`` /
  ``loyal_reader`` / ``peer_author``)
* :func:`load_critic_personas` — typed view over the YAML
* :func:`render_persona_system_prompt` — system prompt fragment per
  persona, ready to be appended to the critic LLM call
* :func:`aggregate_persona_results` — pure aggregator function that
  collapses 4 ``PersonaResult`` payloads into a single
  ``AggregatedCritique`` plus a list of consensus issues

The pipeline integration layer is expected to perform the actual
4 LLM round-trips and feed the parsed JSON back into
:func:`aggregate_persona_results`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)


_CONFIG_FILENAME = "critic_personas.yaml"


@dataclass(frozen=True)
class ScoringDimension:
    """One weighted scoring dimension inside a persona."""

    dimension_id: str
    weight: float
    rubric: str


@dataclass(frozen=True)
class CriticPersona:
    """One of the four reader-personas."""

    persona_id: str
    display_name: str
    role_description: str
    care_about: tuple[str, ...]
    do_not_care_about: tuple[str, ...]
    typical_questions_to_self: tuple[str, ...]
    scoring_dimensions: tuple[ScoringDimension, ...]
    must_rewrite_triggers: tuple[str, ...]


@dataclass(frozen=True)
class AggregationPolicy:
    """Global aggregation thresholds for the multi-persona pass."""

    hard_floor: float
    soft_floor: float
    soft_floor_min_count: int
    consensus_threshold: int
    max_issues_per_rewrite: int


@dataclass(frozen=True)
class CriticPersonasConfig:
    """Typed view of the YAML."""

    version: str
    aggregation: AggregationPolicy
    personas: dict[str, CriticPersona]


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


def _parse_scoring_dimensions(raw: object) -> tuple[ScoringDimension, ...]:
    data = as_dict(raw)
    if not data:
        return ()
    dims: list[ScoringDimension] = []
    for key, value in data.items():
        body = as_dict(value)
        weight_raw = body.get("weight")
        try:
            weight = float(weight_raw) if weight_raw is not None else 0.0
        except (TypeError, ValueError):
            weight = 0.0
        dims.append(
            ScoringDimension(
                dimension_id=as_str(key),
                weight=weight,
                rubric=as_str(body.get("rubric")),
            )
        )
    return tuple(dims)


def _parse_persona(persona_id: str, raw: object) -> CriticPersona:
    data = as_dict(raw)
    return CriticPersona(
        persona_id=persona_id,
        display_name=as_str(data.get("display_name"), default=persona_id),
        role_description=as_str(data.get("role_description")),
        care_about=as_str_tuple(data.get("care_about")),
        do_not_care_about=as_str_tuple(data.get("do_not_care_about")),
        typical_questions_to_self=as_str_tuple(data.get("typical_questions_to_self")),
        scoring_dimensions=_parse_scoring_dimensions(data.get("scoring_dimensions")),
        must_rewrite_triggers=as_str_tuple(data.get("must_rewrite_triggers")),
    )


def _parse_aggregation_policy(raw: object) -> AggregationPolicy:
    data = as_dict(raw)
    def _as_float(value: object, default: float) -> float:
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    return AggregationPolicy(
        hard_floor=_as_float(data.get("hard_floor"), 0.65),
        soft_floor=_as_float(data.get("soft_floor"), 0.75),
        soft_floor_min_count=as_int(data.get("soft_floor_min_count"), default=2),
        consensus_threshold=as_int(data.get("consensus_threshold"), default=2),
        max_issues_per_rewrite=as_int(data.get("max_issues_per_rewrite"), default=5),
    )


@lru_cache(maxsize=1)
def load_critic_personas() -> CriticPersonasConfig:
    """Return the typed view over ``critic_personas.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    personas_raw = as_dict(raw.get("personas"))
    personas: dict[str, CriticPersona] = {}
    for persona_id, persona_raw in personas_raw.items():
        canonical = as_str(persona_id)
        if not canonical:
            continue
        personas[canonical] = _parse_persona(canonical, persona_raw)
    return CriticPersonasConfig(
        version=as_str(raw.get("version")),
        aggregation=_parse_aggregation_policy(raw.get("aggregation")),
        personas=personas,
    )


def get_persona(persona_id: str) -> CriticPersona | None:
    """Look up one persona by id."""

    if not persona_id:
        return None
    return load_critic_personas().personas.get(persona_id)


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def render_persona_system_prompt(persona_id: str) -> str:
    """Render the system prompt fragment for one persona.

    Returns an empty string when the persona is unknown so the caller
    can fall back to the generic critic prompt.
    """

    persona = get_persona(persona_id)
    if persona is None:
        return ""

    lines: list[str] = [
        f"你现在扮演 critic persona: {persona.display_name} ({persona.persona_id})。",
        f"角色: {persona.role_description}".strip(),
    ]
    if persona.care_about:
        lines.append("你关心: " + "; ".join(persona.care_about))
    if persona.do_not_care_about:
        lines.append("你不在乎: " + "; ".join(persona.do_not_care_about))
    if persona.scoring_dimensions:
        dim_lines = [
            f"  {dim.dimension_id} (权重 {dim.weight:.2f}): {dim.rubric}"
            for dim in persona.scoring_dimensions
        ]
        lines.append("评分维度:\n" + "\n".join(dim_lines))
    if persona.must_rewrite_triggers:
        lines.append("强制重写触发条件: " + "; ".join(persona.must_rewrite_triggers))
    lines.append(
        "严格用本 persona 的眼光打分，不参考其他 persona；输出 JSON 含 overall_score / scoring_breakdown / top_3_strengths / top_3_issues / must_rewrite / verdict / one_line_takeaway。"
    )
    return "\n".join(lines)


def render_all_persona_briefs() -> str:
    """Render a one-line brief per persona, useful for orchestration logs."""

    config = load_critic_personas()
    lines = ["【multi-persona critique · personas in play】"]
    for persona_id, persona in config.personas.items():
        lines.append(
            f"- {persona_id} ({persona.display_name}): "
            + "; ".join(persona.care_about[:3])
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonaIssue:
    """One ``top_3_issues`` entry returned by a persona."""

    issue: str
    severity: str = "medium"  # "critical" | "high" | "medium" | "low"
    suggested_cause_id: str = ""
    evidence: str = ""
    specific_fix: str = ""


@dataclass(frozen=True)
class PersonaResult:
    """The parsed output of one persona's critique call."""

    persona_id: str
    overall_score: float
    must_rewrite: bool
    issues: tuple[PersonaIssue, ...] = ()
    verdict: str = ""
    one_line_takeaway: str = ""


@dataclass(frozen=True)
class ConsensusIssue:
    """An issue that ≥ ``consensus_threshold`` personas independently raised."""

    issue: str
    votes: tuple[str, ...]
    severity: str
    suggested_cause_ids: tuple[str, ...]
    priority: int


@dataclass(frozen=True)
class AggregatedCritique:
    """The fused multi-persona verdict."""

    personas: tuple[PersonaResult, ...]
    min_score: float
    avg_score: float
    must_rewrite: bool
    rewrite_reason: str
    consensus_issues: tuple[ConsensusIssue, ...]
    merged_cause_ids: tuple[str, ...] = ()


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _normalise_issue_key(issue: str) -> str:
    """Collapse minor differences in issue phrasing to a stable key."""

    return "".join(ch for ch in issue.lower() if ch.isalnum())


def aggregate_persona_results(
    persona_results: Iterable[PersonaResult],
    *,
    policy: AggregationPolicy | None = None,
) -> AggregatedCritique:
    """Fuse independent persona results into a single verdict.

    Pure function; no LLM calls, no I/O. The pipeline glue layer is
    expected to feed already-parsed :class:`PersonaResult` instances.
    """

    pol = policy or load_critic_personas().aggregation
    results = tuple(persona_results)
    if not results:
        return AggregatedCritique(
            personas=(),
            min_score=0.0,
            avg_score=0.0,
            must_rewrite=False,
            rewrite_reason="",
            consensus_issues=(),
        )

    scores = [result.overall_score for result in results]
    min_score = min(scores)
    avg_score = sum(scores) / len(scores)

    rewrite_reason = ""
    must_rewrite = False
    if any(result.must_rewrite for result in results):
        must_rewrite = True
        rewrite_reason = "explicit_persona_must_rewrite"
    if min_score < pol.hard_floor:
        must_rewrite = True
        rewrite_reason = rewrite_reason or "hard_floor_breached"
    below_soft = [r for r in results if r.overall_score < pol.soft_floor]
    if len(below_soft) >= pol.soft_floor_min_count:
        must_rewrite = True
        rewrite_reason = rewrite_reason or "soft_floor_consensus"

    # Build consensus issues: bucket issues across personas by key,
    # keep only buckets reaching the consensus threshold.
    buckets: dict[str, dict[str, object]] = {}
    for result in results:
        for issue in result.issues:
            key = _normalise_issue_key(issue.issue)
            if not key:
                continue
            bucket = buckets.setdefault(
                key,
                {
                    "issue": issue.issue,
                    "votes": [],
                    "severities": [],
                    "causes": [],
                },
            )
            bucket["votes"].append(result.persona_id)
            bucket["severities"].append(issue.severity or "medium")
            if issue.suggested_cause_id:
                bucket["causes"].append(issue.suggested_cause_id)

    consensus: list[ConsensusIssue] = []
    for bucket in buckets.values():
        votes = tuple(dict.fromkeys(bucket["votes"]))  # de-dupe but preserve order
        if len(votes) < pol.consensus_threshold:
            continue
        severities = bucket["severities"]
        # Pick the worst severity any persona used.
        severity = min(
            severities,
            key=lambda s: _SEVERITY_RANK.get(s.lower(), 4),
        )
        priority = 1 if severity.lower() in {"critical", "high"} else 2
        cause_counts = Counter(bucket["causes"])
        ordered_causes = tuple(
            cause for cause, _count in cause_counts.most_common()
        )
        consensus.append(
            ConsensusIssue(
                issue=str(bucket["issue"]),
                votes=votes,
                severity=severity.lower(),
                suggested_cause_ids=ordered_causes,
                priority=priority,
            )
        )
    consensus.sort(key=lambda item: (item.priority, -len(item.votes)))
    consensus = consensus[: pol.max_issues_per_rewrite]

    merged_cause_ids: list[str] = []
    seen_causes: set[str] = set()
    for item in consensus:
        for cause in item.suggested_cause_ids:
            if cause not in seen_causes:
                seen_causes.add(cause)
                merged_cause_ids.append(cause)

    return AggregatedCritique(
        personas=results,
        min_score=min_score,
        avg_score=avg_score,
        must_rewrite=must_rewrite,
        rewrite_reason=rewrite_reason,
        consensus_issues=tuple(consensus),
        merged_cause_ids=tuple(merged_cause_ids),
    )
