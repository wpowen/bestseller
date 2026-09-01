"""Deterministic story-engine contracts and receipt replay.

The module is deliberately isolated from persistence and generation services.
It provides the fail-closed domain rules needed for shadow evaluation before a
story engine is allowed to influence the production writing path.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import Enum, StrEnum
import hashlib
from itertools import pairwise
import json
from typing import Any, cast

from bestseller.domain.story_state import (
    MonotonicPolicy,
    StateCategory,
    StoryState,
    StoryStateTransition,
    apply_story_state_transitions,
    validate_story_state_transition,
)


class StoryEngineMaturity(StrEnum):
    """Evidence maturity for deciding whether an engine may drive production."""

    UNKNOWN = "unknown"
    INSUFFICIENT_DATA = "unknown"
    STRUCTURE_ONLY = "structure_only"
    SHADOW_VALIDATED = "shadow_validated"
    CANARY_VALIDATED = "canary_validated"
    READER_VALIDATED = "reader_validated"
    CANONICAL = "canonical"


def _jsonable(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        serialized = [_jsonable(item) for item in value]
        return sorted(serialized, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return value


def canonical_json_hash(value: Any) -> str:  # noqa: ANN401
    """Return a stable SHA-256 hash independent of mapping insertion order."""

    encoded = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _transition_from_value(
    value: StoryStateTransition | Mapping[str, Any],
) -> StoryStateTransition:
    if isinstance(value, StoryStateTransition):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("story state transition must be a mapping")
    payload = dict(value)
    evidence = payload.get("evidence")
    if isinstance(evidence, list):
        payload["evidence"] = tuple(str(item) for item in evidence)
    return StoryStateTransition(**payload)


@dataclass(frozen=True)
class ChoiceOption:
    """A real option whose result must be distinguishable from its siblings."""

    choice_id: str
    label: str
    reachable_state_hash: str

    def __post_init__(self) -> None:
        if not self.choice_id.strip():
            raise ValueError("choice option id must not be empty")
        if not self.label.strip():
            raise ValueError("choice option label must not be empty")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ChoiceOption:
        return cls(
            choice_id=str(value.get("choice_id") or value.get("option_id") or ""),
            label=str(value.get("label") or value.get("action") or ""),
            reachable_state_hash=str(
                value.get("reachable_state_hash")
                or value.get("expected_post_state_hash")
                or ""
            ),
        )


def _story_state_to_mapping(
    state: StoryState | Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    normalized = state if isinstance(state, StoryState) else StoryState.from_mapping(state)
    return {
        key: {
            "category": item.category.value if item.category is not None else None,
            "value": deepcopy(item.value),
        }
        for key, item in normalized.values.items()
    }


@dataclass
class ChapterCreativeProjection:
    """Current-chapter-only creative packet derived from one Engine choice row."""

    chapter_number: int
    choice_id: str
    pre_state: StoryState | Mapping[str, Any]
    pressure: str
    options: Sequence[ChoiceOption | Mapping[str, Any]]
    chosen_option_id: str
    chosen_path: str
    opponent_strategy: str
    expected_transitions: Sequence[StoryStateTransition | Mapping[str, Any]]
    known_facts: Sequence[str] = ()
    alternative_costs: Sequence[str] = ()
    due_obligations: Sequence[str] = ()
    pre_state_hash: str = ""
    expected_post_state_hash: str = ""
    fingerprint: str = ""

    def __post_init__(self) -> None:
        self.chapter_number = int(self.chapter_number)
        self.choice_id = str(self.choice_id).strip()
        self.pressure = str(self.pressure).strip()
        self.chosen_option_id = str(self.chosen_option_id).strip()
        self.chosen_path = str(self.chosen_path).strip()
        self.opponent_strategy = str(self.opponent_strategy).strip()
        self.pre_state = (
            deepcopy(self.pre_state)
            if isinstance(self.pre_state, StoryState)
            else StoryState.from_mapping(self.pre_state)
        )
        self.options = tuple(
            option if isinstance(option, ChoiceOption) else ChoiceOption.from_mapping(option)
            for option in self.options
        )
        self.expected_transitions = tuple(
            _transition_from_value(transition) for transition in self.expected_transitions
        )
        self.known_facts = tuple(
            str(item).strip() for item in self.known_facts if str(item).strip()
        )
        self.alternative_costs = tuple(
            str(item).strip() for item in self.alternative_costs if str(item).strip()
        )
        self.due_obligations = tuple(
            str(item).strip() for item in self.due_obligations if str(item).strip()
        )
        self.pre_state_hash = str(self.pre_state_hash).strip()
        self.expected_post_state_hash = str(self.expected_post_state_hash).strip()
        self.fingerprint = str(self.fingerprint).strip()
        self.validate()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ChapterCreativeProjection:
        prohibited = {"future_facts", "future_chapters", "window_projections"}
        leaked = prohibited.intersection(value)
        if leaked:
            raise ValueError(
                "chapter creative projection contains future-only fields: "
                + ", ".join(sorted(leaked))
            )
        return cls(
            chapter_number=int(value.get("chapter_number", value.get("chapter", 0))),
            choice_id=str(value.get("choice_id") or value.get("chosen_option_id") or ""),
            pre_state=value.get("pre_state", {}),
            pressure=str(value.get("pressure") or ""),
            options=value.get("options", ()),
            chosen_option_id=str(value.get("chosen_option_id") or value.get("choice_id") or ""),
            chosen_path=str(value.get("chosen_path") or value.get("immediate_result") or ""),
            opponent_strategy=str(
                value.get("opponent_strategy")
                or value.get("opponent_counteraction")
                or ""
            ),
            expected_transitions=value.get(
                "required_state_changes",
                value.get("expected_transitions", ()),
            ),
            known_facts=value.get("known_facts", ()),
            alternative_costs=value.get("alternative_costs", ()),
            due_obligations=value.get(
                "due_obligations",
                value.get("future_obligations", ()),
            ),
            pre_state_hash=str(value.get("pre_state_hash") or ""),
            expected_post_state_hash=str(value.get("expected_post_state_hash") or ""),
            fingerprint=str(value.get("fingerprint") or ""),
        )

    def validate(self) -> None:
        if self.chapter_number < 1:
            raise ValueError("chapter creative projection requires a positive chapter")
        if not self.choice_id or not self.chosen_option_id:
            raise ValueError("chapter creative projection requires a choice id")
        if self.choice_id != self.chosen_option_id:
            raise ValueError("choice_id must equal chosen_option_id")
        if not self.pressure or not self.chosen_path:
            raise ValueError("chapter creative projection requires pressure and chosen path")
        if not self.opponent_strategy and not self.due_obligations:
            raise ValueError("chapter projection requires opponent strategy or due obligation")
        normalized_options = cast(tuple[ChoiceOption, ...], self.options)
        if len(normalized_options) < 2:
            raise ValueError("chapter projection requires at least two real options")
        option_ids = [option.choice_id for option in normalized_options]
        if len(set(option_ids)) != len(option_ids):
            raise ValueError("chapter projection option ids must be unique")
        reachable_hashes = [option.reachable_state_hash for option in normalized_options]
        if any(not value for value in reachable_hashes):
            raise ValueError("chapter projection options require reachable-state hashes")
        if len(set(reachable_hashes)) != len(reachable_hashes):
            raise ValueError("chapter projection options must not converge to one state")
        chosen = next(
            (option for option in normalized_options if option.choice_id == self.chosen_option_id),
            None,
        )
        if chosen is None:
            raise ValueError("chosen option is missing from chapter projection options")
        transitions = cast(tuple[StoryStateTransition, ...], self.expected_transitions)
        if not transitions:
            raise ValueError("chapter creative projection requires state changes")
        current_state = deepcopy(cast(StoryState, self.pre_state))
        computed_pre_hash = canonical_json_hash(_story_state_to_mapping(current_state))
        if self.pre_state_hash and self.pre_state_hash != computed_pre_hash:
            raise ValueError("chapter projection pre-state hash mismatch")
        apply_story_state_transitions(current_state, transitions)
        computed_post_hash = canonical_json_hash(_story_state_to_mapping(current_state))
        if self.expected_post_state_hash and (
            self.expected_post_state_hash != computed_post_hash
        ):
            raise ValueError("chapter projection expected post-state hash mismatch")
        if chosen.reachable_state_hash != computed_post_hash:
            raise ValueError("chosen option reachable-state hash mismatch")
        self.pre_state_hash = computed_pre_hash
        self.expected_post_state_hash = computed_post_hash
        if not self.fingerprint:
            self.fingerprint = canonical_json_hash(
                {
                    "choice_id": self.choice_id,
                    "chosen_path": self.chosen_path,
                    "transitions": self.expected_transitions,
                }
            )


def chapter_creative_projection_to_mapping(
    projection: ChapterCreativeProjection,
) -> dict[str, Any]:
    options = cast(tuple[ChoiceOption, ...], projection.options)
    transitions = cast(tuple[StoryStateTransition, ...], projection.expected_transitions)
    payload: dict[str, Any] = {
        "chapter_number": projection.chapter_number,
        "choice_id": projection.choice_id,
        "pre_state": _story_state_to_mapping(
            cast(StoryState, projection.pre_state)
        ),
        "pre_state_hash": projection.pre_state_hash,
        "known_facts": list(projection.known_facts),
        "pressure": projection.pressure,
        "options": [
            {
                "choice_id": option.choice_id,
                "label": option.label,
                "reachable_state_hash": option.reachable_state_hash,
            }
            for option in options
        ],
        "chosen_option_id": projection.chosen_option_id,
        "chosen_path": projection.chosen_path,
        "alternative_costs": list(projection.alternative_costs),
        "opponent_strategy": projection.opponent_strategy,
        "due_obligations": list(projection.due_obligations),
        "required_state_changes": [
            {
                "key": transition.key,
                "category": StateCategory(transition.category).value,
                "before": deepcopy(transition.before),
                "operator": transition.operator,
                "after": deepcopy(transition.after),
                "evidence": _jsonable(transition.evidence),
                "monotonic": MonotonicPolicy(transition.monotonic).value,
            }
            for transition in transitions
        ],
        "expected_post_state_hash": projection.expected_post_state_hash,
        "fingerprint": projection.fingerprint,
    }
    return {**payload, "projection_hash": canonical_json_hash(payload)}


@dataclass
class StoryEngineWindow:
    """Bounded, contiguous future window; never an exponential story tree."""

    window_id: str
    engine_id: str
    engine_version: int
    engine_artifact_id: str
    source_engine_hash: str
    projections: Sequence[ChapterCreativeProjection | Mapping[str, Any]]

    def __post_init__(self) -> None:
        self.window_id = str(self.window_id).strip()
        self.engine_id = str(self.engine_id).strip()
        self.engine_version = int(self.engine_version)
        self.engine_artifact_id = str(self.engine_artifact_id).strip()
        self.source_engine_hash = str(self.source_engine_hash).strip()
        self.projections = tuple(
            item
            if isinstance(item, ChapterCreativeProjection)
            else ChapterCreativeProjection.from_mapping(item)
            for item in self.projections
        )
        self.validate()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> StoryEngineWindow:
        window = cls(
            window_id=str(value.get("window_id") or ""),
            engine_id=str(value.get("engine_id") or ""),
            engine_version=int(value.get("engine_version", 0)),
            engine_artifact_id=str(value.get("engine_artifact_id") or ""),
            source_engine_hash=str(value.get("source_engine_hash") or ""),
            projections=value.get("projections", ()),
        )
        if value.get("start_chapter") not in {None, window.start_chapter}:
            raise ValueError("story engine window start chapter mismatch")
        if value.get("end_chapter") not in {None, window.end_chapter}:
            raise ValueError("story engine window end chapter mismatch")
        return window

    @property
    def start_chapter(self) -> int:
        projections = cast(tuple[ChapterCreativeProjection, ...], self.projections)
        return projections[0].chapter_number

    @property
    def end_chapter(self) -> int:
        projections = cast(tuple[ChapterCreativeProjection, ...], self.projections)
        return projections[-1].chapter_number

    def validate(self) -> None:
        if not all(
            (
                self.window_id,
                self.engine_id,
                self.engine_artifact_id,
                self.source_engine_hash,
            )
        ):
            raise ValueError("story engine window lineage is incomplete")
        if self.engine_version < 1:
            raise ValueError("story engine window requires a positive engine version")
        projections = cast(tuple[ChapterCreativeProjection, ...], self.projections)
        if not projections or len(projections) > 10:
            raise ValueError("story engine window must contain one to ten chapters")
        chapter_numbers = [projection.chapter_number for projection in projections]
        if chapter_numbers != list(
            range(chapter_numbers[0], chapter_numbers[0] + len(chapter_numbers))
        ):
            raise ValueError("story engine window chapters must be contiguous")
        for previous, current in pairwise(projections):
            if previous.expected_post_state_hash != current.pre_state_hash:
                raise ValueError("story engine window state hash chain mismatch")


def story_engine_window_to_mapping(window: StoryEngineWindow) -> dict[str, Any]:
    projections = cast(tuple[ChapterCreativeProjection, ...], window.projections)
    return {
        "window_id": window.window_id,
        "engine_id": window.engine_id,
        "engine_version": window.engine_version,
        "engine_artifact_id": window.engine_artifact_id,
        "source_engine_hash": window.source_engine_hash,
        "start_chapter": window.start_chapter,
        "end_chapter": window.end_chapter,
        "projections": [
            chapter_creative_projection_to_mapping(projection)
            for projection in projections
        ],
    }


@dataclass
class ChoiceConsequenceRow:
    """One chosen action and the auditable consequences it creates."""

    choice_id: str
    transitions: Sequence[StoryStateTransition | Mapping[str, Any]]
    opponent_counteraction: str | None = None
    future_obligations: Sequence[str] = ()
    fingerprint: str | None = None
    chapter: int | None = None
    receipt_id: str | None = None
    verification_status: str = "verified"

    def __post_init__(self) -> None:
        self.choice_id = str(self.choice_id)
        self.transitions = tuple(_transition_from_value(item) for item in self.transitions)
        self.opponent_counteraction = (
            str(self.opponent_counteraction).strip() if self.opponent_counteraction else None
        )
        self.future_obligations = tuple(
            str(item).strip() for item in self.future_obligations if str(item).strip()
        )
        self.fingerprint = str(self.fingerprint).strip() if self.fingerprint else None
        self.receipt_id = str(self.receipt_id).strip() if self.receipt_id else None
        self.verification_status = str(self.verification_status).strip().lower()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ChoiceConsequenceRow:
        raw_chapter = value.get("chapter", value.get("chapter_number"))
        chapter = int(raw_chapter) if raw_chapter is not None else None
        choice_id = str(value.get("choice_id") or value.get("chosen_option_id") or "")
        receipt_id = value.get("receipt_id")
        if not receipt_id and chapter is not None and choice_id:
            receipt_id = f"chapter:{chapter}:choice:{choice_id}"
        return cls(
            choice_id=choice_id,
            transitions=value.get("transitions", value.get("observed_transitions", ())),
            opponent_counteraction=value.get("opponent_counteraction"),
            future_obligations=value.get(
                "future_obligations", value.get("new_obligations", ())
            ),
            fingerprint=value.get("fingerprint", value.get("choice_fingerprint")),
            chapter=chapter,
            receipt_id=str(receipt_id) if receipt_id else None,
            verification_status=str(value.get("verification_status", "verified")),
        )

    def validate(self) -> None:
        if not self.choice_id.strip():
            raise ValueError("choice consequence requires choice_id")
        if not self.transitions and self.verification_status != "unverified":
            raise ValueError("choice consequence requires a transition or unverified status")
        normalized_transitions = cast(tuple[StoryStateTransition, ...], self.transitions)
        for transition in normalized_transitions:
            validate_story_state_transition(transition)
        if not self.opponent_counteraction and not self.future_obligations:
            raise ValueError(
                "choice consequence requires opponent counteraction or future obligation"
            )

    def stable_receipt_id(self) -> str:
        if self.receipt_id:
            return self.receipt_id
        if self.chapter is not None:
            return f"chapter:{self.chapter}:choice:{self.choice_id}"
        return canonical_json_hash(self)


@dataclass
class ChapterTransitionReceipt(ChoiceConsequenceRow):
    """Observed chapter result that can be folded into canonical story state."""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ChapterTransitionReceipt:
        row = ChoiceConsequenceRow.from_mapping(value)
        return cls(**asdict(row))


@dataclass
class StoryEngineDefinition:
    """Immutable-by-convention input required for deterministic replay."""

    engine_id: str
    initial_state: StoryState | Mapping[str, Any]
    choices: Sequence[ChoiceOption | Mapping[str, Any]] = ()
    chapters: Sequence[ChapterTransitionReceipt | Mapping[str, Any]] = ()
    version: int = 1
    reader_promise: str = ""
    change_vectors: Sequence[str] = ()
    engine_invariants: Sequence[str] = ()

    def __post_init__(self) -> None:
        self.engine_id = str(self.engine_id).strip()
        if not isinstance(self.initial_state, StoryState):
            self.initial_state = StoryState.from_mapping(self.initial_state)
        else:
            self.initial_state = deepcopy(self.initial_state)
        self.choices = tuple(
            item if isinstance(item, ChoiceOption) else ChoiceOption.from_mapping(item)
            for item in self.choices
        )
        self.chapters = tuple(
            item
            if isinstance(item, ChapterTransitionReceipt)
            else ChapterTransitionReceipt.from_mapping(item)
            for item in self.chapters
        )
        self.version = int(self.version)
        self.reader_promise = str(self.reader_promise).strip()
        self.change_vectors = tuple(
            str(item).strip() for item in self.change_vectors if str(item).strip()
        )
        self.engine_invariants = tuple(
            str(item).strip() for item in self.engine_invariants if str(item).strip()
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> StoryEngineDefinition:
        return cls(
            engine_id=str(value.get("engine_id") or ""),
            version=int(value.get("version", 1)),
            initial_state=value.get("initial_state", {}),
            choices=value.get("choices", ()),
            chapters=value.get("chapters", ()),
            reader_promise=str(value.get("reader_promise") or ""),
            change_vectors=value.get("change_vectors", ()),
            engine_invariants=value.get("engine_invariants", ()),
        )


def story_engine_definition_to_mapping(
    engine: StoryEngineDefinition,
) -> dict[str, Any]:
    """Serialize a normalized definition without leaking dataclass internals."""

    state = cast(StoryState, engine.initial_state)
    choices = cast(tuple[ChoiceOption, ...], engine.choices)
    chapters = cast(tuple[ChapterTransitionReceipt, ...], engine.chapters)
    return {
        "engine_id": engine.engine_id,
        "version": engine.version,
        "initial_state": _story_state_to_mapping(state),
        "choices": [
            {
                "choice_id": choice.choice_id,
                "label": choice.label,
                "reachable_state_hash": choice.reachable_state_hash,
            }
            for choice in choices
        ],
        "chapters": [
            {
                "chapter": receipt.chapter,
                "choice_id": receipt.choice_id,
                "fingerprint": receipt.fingerprint,
                "receipt_id": receipt.receipt_id,
                "verification_status": receipt.verification_status,
                "transitions": [
                    {
                        "key": transition.key,
                        "category": StateCategory(transition.category).value,
                        "before": deepcopy(transition.before),
                        "operator": transition.operator,
                        "after": deepcopy(transition.after),
                        "evidence": _jsonable(transition.evidence),
                        "monotonic": MonotonicPolicy(transition.monotonic).value,
                    }
                    for transition in cast(
                        tuple[StoryStateTransition, ...], receipt.transitions
                    )
                ],
                "opponent_counteraction": receipt.opponent_counteraction,
                "future_obligations": list(receipt.future_obligations),
            }
            for receipt in chapters
        ],
        "reader_promise": engine.reader_promise,
        "change_vectors": list(engine.change_vectors),
        "engine_invariants": list(engine.engine_invariants),
    }


@dataclass(frozen=True)
class ReplayResult:
    state: StoryState
    applied_count: int
    duplicate_count: int
    receipt_hashes: tuple[str, ...]
    outstanding_obligations: tuple[str, ...]


@dataclass(frozen=True)
class StoryEngineBaselineReport:
    """Genre-neutral structural measurements for a ten-chapter sample."""

    chapter_count: int
    repeated_fingerprint_count: int
    duplicate_transition_pattern_count: int
    state_reset_count: int
    opponent_response_coverage: float
    obligation_coverage: float
    transition_evidence_coverage: float
    blocking_codes: list[str]
    structure_passed: bool
    source_hash: str


def count_choice_fingerprints(fingerprints: Sequence[str]) -> dict[str, int]:
    """Count normalized choice fingerprints while preserving first-seen order."""

    normalized = (str(item).strip() for item in fingerprints if str(item).strip())
    return dict(Counter(normalized))


def _state_scalar(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, Mapping) and "value" in value:
        return value["value"]
    return value


def _non_empty_sequence(value: Any) -> bool:  # noqa: ANN401
    return isinstance(value, (list, tuple, set, frozenset)) and any(
        str(item).strip() for item in value
    )


def evaluate_story_engine_baseline(
    payload: Mapping[str, Any],
) -> StoryEngineBaselineReport:
    """Audit a raw chapter sample without requiring it to pass engine validation.

    Baseline evaluation must be able to describe legacy failures, including
    receipts that cannot be replayed.  It therefore observes raw transitions
    instead of calling :func:`validate_engine_definition` first.
    """

    raw_chapters = payload.get("chapters", ())
    chapters = list(raw_chapters) if isinstance(raw_chapters, (list, tuple)) else []
    initial_state = payload.get("initial_state", {})
    current_values = {
        str(key): deepcopy(_state_scalar(value))
        for key, value in initial_state.items()
    } if isinstance(initial_state, Mapping) else {}
    starting_values = deepcopy(current_values)

    fingerprints: list[str] = []
    transition_patterns: list[str] = []
    seen_transition_patterns: set[str] = set()
    state_reset_count = 0
    opponent_response_count = 0
    obligation_count = 0
    transition_count = 0
    evidenced_transition_count = 0

    for raw_chapter in chapters:
        chapter = dict(raw_chapter) if isinstance(raw_chapter, Mapping) else {}
        fingerprint = str(chapter.get("fingerprint") or "").strip()
        if fingerprint:
            fingerprints.append(fingerprint)
        if str(chapter.get("opponent_counteraction") or "").strip():
            opponent_response_count += 1
        if _non_empty_sequence(chapter.get("future_obligations")):
            obligation_count += 1

        raw_transitions = chapter.get("transitions", ())
        transitions = (
            list(raw_transitions) if isinstance(raw_transitions, (list, tuple)) else []
        )
        for raw_transition in transitions:
            transition = (
                dict(raw_transition) if isinstance(raw_transition, Mapping) else {}
            )
            transition_count += 1
            if str(transition.get("evidence") or "").strip():
                evidenced_transition_count += 1
            transition_pattern = canonical_json_hash(
                {
                    "key": transition.get("key"),
                    "category": transition.get("category"),
                    "before": transition.get("before"),
                    "operator": transition.get("operator"),
                    "after": transition.get("after"),
                }
            )
            repeated_transition_pattern = transition_pattern in seen_transition_patterns
            transition_patterns.append(transition_pattern)
            seen_transition_patterns.add(transition_pattern)
            key = str(transition.get("key") or "").strip()
            if not key:
                continue
            before = transition.get("before")
            after = transition.get("after")
            before_mismatch = key in current_values and current_values[key] != before
            returned_to_initial = (
                key in starting_values
                and key in current_values
                and current_values[key] != starting_values[key]
                and after == starting_values[key]
                and repeated_transition_pattern
            )
            if before_mismatch or returned_to_initial:
                state_reset_count += 1
            current_values[key] = deepcopy(after)

    fingerprint_counts = count_choice_fingerprints(fingerprints)
    repeated_fingerprint_count = sum(
        count - 1 for count in fingerprint_counts.values() if count > 1
    )
    pattern_counts = Counter(transition_patterns)
    duplicate_transition_pattern_count = sum(
        count - 1 for count in pattern_counts.values() if count > 1
    )
    chapter_count = len(chapters)
    opponent_response_coverage = (
        opponent_response_count / chapter_count if chapter_count else 0.0
    )
    obligation_coverage = obligation_count / chapter_count if chapter_count else 0.0
    transition_evidence_coverage = (
        evidenced_transition_count / transition_count if transition_count else 0.0
    )

    blocking_codes: list[str] = []
    if chapter_count != 10:
        blocking_codes.append("TEN_CHAPTER_SAMPLE_REQUIRED")
    if repeated_fingerprint_count:
        blocking_codes.append("REPEATED_FINGERPRINT")
    if duplicate_transition_pattern_count:
        blocking_codes.append("DUPLICATE_TRANSITION_PATTERN")
    if state_reset_count:
        blocking_codes.append("STATE_RESET")
    if opponent_response_coverage < 0.8:
        blocking_codes.append("LOW_OPPONENT_RESPONSE_COVERAGE")
    if obligation_coverage < 0.8:
        blocking_codes.append("LOW_OBLIGATION_COVERAGE")
    if transition_evidence_coverage < 1.0:
        blocking_codes.append("TRANSITION_EVIDENCE_INCOMPLETE")

    return StoryEngineBaselineReport(
        chapter_count=chapter_count,
        repeated_fingerprint_count=repeated_fingerprint_count,
        duplicate_transition_pattern_count=duplicate_transition_pattern_count,
        state_reset_count=state_reset_count,
        opponent_response_coverage=opponent_response_coverage,
        obligation_coverage=obligation_coverage,
        transition_evidence_coverage=transition_evidence_coverage,
        blocking_codes=blocking_codes,
        structure_passed=not blocking_codes,
        source_hash=canonical_json_hash(payload),
    )


def validate_engine_definition(engine: StoryEngineDefinition) -> None:
    """Fail closed when a definition cannot support meaningful replay."""

    if not engine.engine_id:
        raise ValueError("story engine id must not be empty")
    if engine.version < 1:
        raise ValueError("story engine version must be positive")

    if engine.choices:
        if len(engine.choices) < 2:
            raise ValueError("story engine requires at least two reachable real options")
        normalized_choices = cast(tuple[ChoiceOption, ...], engine.choices)
        choice_ids = [choice.choice_id for choice in normalized_choices]
        if len(set(choice_ids)) != len(choice_ids):
            raise ValueError("story engine choice ids must be unique")
        reachable_hashes = [
            choice.reachable_state_hash.strip() for choice in normalized_choices
        ]
        if any(not value for value in reachable_hashes):
            raise ValueError("every choice requires a reachable-state hash")
        if len(set(reachable_hashes)) != len(reachable_hashes):
            raise ValueError("choices must have distinct reachable-state hashes")

    seen_chapters: set[int] = set()
    normalized_chapters = cast(tuple[ChapterTransitionReceipt, ...], engine.chapters)
    for receipt in normalized_chapters:
        receipt.validate()
        if receipt.chapter is None:
            continue
        if receipt.chapter < 1:
            raise ValueError("chapter number must be positive")
        if receipt.chapter in seen_chapters:
            raise ValueError(f"duplicate chapter receipt: {receipt.chapter}")
        seen_chapters.add(receipt.chapter)


def replay_receipts(
    engine: StoryEngineDefinition,
    receipts: Sequence[ChapterTransitionReceipt | Mapping[str, Any]],
) -> ReplayResult:
    """Replay unique receipts from initial state without mutating ``engine``."""

    validate_engine_definition(engine)
    state = deepcopy(cast(StoryState, engine.initial_state))
    parsed = tuple(
        item
        if isinstance(item, ChapterTransitionReceipt)
        else ChapterTransitionReceipt.from_mapping(item)
        for item in receipts
    )
    seen: dict[str, str] = {}
    applied_hashes: list[str] = []
    outstanding_obligations: list[str] = []
    known_obligations: set[str] = set()
    applied_count = 0
    duplicate_count = 0

    for receipt in parsed:
        receipt.validate()
        receipt_id = receipt.stable_receipt_id()
        receipt_hash = canonical_json_hash(receipt)
        previous_hash = seen.get(receipt_id)
        if previous_hash is not None:
            if previous_hash != receipt_hash:
                raise ValueError(f"conflicting duplicate receipt: {receipt_id}")
            duplicate_count += 1
            continue
        normalized_transitions = cast(tuple[StoryStateTransition, ...], receipt.transitions)
        apply_story_state_transitions(state, normalized_transitions)
        seen[receipt_id] = receipt_hash
        applied_hashes.append(receipt_hash)
        for obligation in receipt.future_obligations:
            if obligation not in known_obligations:
                outstanding_obligations.append(obligation)
                known_obligations.add(obligation)
        applied_count += 1

    return ReplayResult(
        state=state,
        applied_count=applied_count,
        duplicate_count=duplicate_count,
        receipt_hashes=tuple(applied_hashes),
        outstanding_obligations=tuple(outstanding_obligations),
    )


def _validation_passed(value: Any) -> bool:  # noqa: ANN401
    return isinstance(value, Mapping) and str(value.get("status", "")).lower() in {
        "pass",
        "passed",
        "validated",
    }


def assess_maturity(payload: Mapping[str, Any]) -> StoryEngineMaturity:
    """Derive maturity from evidence, never from a requested label alone."""

    reader_validation = payload.get("reader_validation")
    if _validation_passed(reader_validation):
        requested = str(payload.get("maturity", "")).lower()
        if requested == StoryEngineMaturity.CANONICAL:
            return StoryEngineMaturity.CANONICAL
        return StoryEngineMaturity.READER_VALIDATED
    if _validation_passed(payload.get("canary_validation")):
        return StoryEngineMaturity.CANARY_VALIDATED
    if _validation_passed(payload.get("shadow_validation")):
        return StoryEngineMaturity.SHADOW_VALIDATED
    return StoryEngineMaturity.INSUFFICIENT_DATA
