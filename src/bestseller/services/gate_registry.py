from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

RepairStrategy = Literal["auto", "rewrite_task", "human_review"]
GateTier = Literal["core", "advanced"]
#: Whether a gate failure on chapter *N* affects the ability to write *future*
#: chapters. ``"structural"`` failures corrupt the canon/continuity snapshot
#: that later chapters build on (wrong fact, dead-character regression,
#: timeline drift, broken material referential integrity) — continuation must
#: wait for the repair. ``"local"`` failures are confined to chapter *N*'s own
#: prose surface (opening tension, length, dialogue pairing, AI-flavor, style
#: signature); repairing them never changes anything a later chapter depends
#: on, so new-chapter writing may proceed in parallel with the repair. This is
#: the single source of truth consumed by ``services.repair_impact`` to decide
#: continuation readiness — see 青囊不语问阴阳 (book stuck looping ch1 opening
#: repair while never advancing) for the motivating regression.
ContinuationImpact = Literal["local", "structural"]


@dataclass(frozen=True)
class GateRegistration:
    name: str
    metadata_keys: tuple[str, ...]
    repair_strategy: RepairStrategy
    terminal_project_keys: tuple[str, ...] = ()
    #: ``"core"`` gates are structural safety nets — a hit blocks the chapter
    #: and triggers a rewrite. ``"advanced"`` gates are polish/signature/style
    #: checks that should warn but never block, so a single weak-model
    #: regression in style cannot loop a chapter through ``machine_repair_required``
    #: indefinitely. See WS-C of docs/质量回归修复-开发计划-20260602.md.
    tier: GateTier = "core"
    #: Whether this gate's failure blocks *forward* writing (``"structural"``)
    #: or is confined to the failing chapter's own prose (``"local"``). Defaults
    #: to ``"structural"`` so an unclassified gate stays conservative (blocks
    #: continuation) rather than silently letting later chapters build on a
    #: possibly-broken predecessor. ``tier`` and ``continuation_impact`` are
    #: ORTHOGONAL: a gate can be ``core`` (blocks its own chapter) yet ``local``
    #: (does not block later chapters) — e.g. ``qimao_opening_gate``.
    continuation_impact: ContinuationImpact = "structural"


_GATES: tuple[GateRegistration, ...] = (
    # ---- core: structural safety nets (block + rewrite) -----------------
    GateRegistration(
        name="write_safety_gate",
        metadata_keys=("blocked_by_write_safety_gate", "write_safety_block_code"),
        repair_strategy="rewrite_task",
        tier="core",
    ),
    GateRegistration(
        name="l2_bible_gate",
        metadata_keys=("blocked_by_l2_bible_gate",),
        repair_strategy="rewrite_task",
        tier="core",
    ),
    GateRegistration(
        name="fanqie_long_ranking_gate",
        metadata_keys=("blocked_by_fanqie_long_ranking_gate",),
        repair_strategy="rewrite_task",
        tier="core",
        # Opening-ranking readiness is a prose/hook judgement on this chapter's
        # own first page; later chapters do not depend on it.
        continuation_impact="local",
    ),
    GateRegistration(
        name="anti_meta_gate",
        metadata_keys=("blocked_by_anti_meta_gate",),
        repair_strategy="rewrite_task",
        tier="core",
        # Meta-language leakage ("the author", "this chapter") is a surface
        # prose defect — scrubbing it changes wording, not canon.
        continuation_impact="local",
    ),
    GateRegistration(
        name="chapter_splice_coherence_gate",
        metadata_keys=(
            "blocked_by_chapter_splice_coherence_gate",
            "chapter_splice_coherence_block_codes",
        ),
        repair_strategy="rewrite_task",
        tier="core",
    ),
    GateRegistration(
        # All-underscore identifier — matches the actual file
        # ``material_referential_integrity_gate.py`` and the underscore
        # convention used everywhere else in the codebase (see
        # ``book_lifecycle_quality_gate.py`` and the ``blocked_by_*_gate``
        # writers in pipelines.py). A prior change corrupted this to a
        # space-separated variant; the space form only ever existed inside
        # this registry, so any writer using the underscore convention would
        # never match the predicate and this core integrity gate would go
        # blind. Restored to underscores (2026-06-03, CD1/F8).
        name="material_referential_integrity_gate",
        metadata_keys=(
            "blocked_by_material_referential_integrity_gate",
            "material_referential_integrity_block_codes",
        ),
        repair_strategy="auto",
        tier="core",
    ),
    GateRegistration(
        name="chapter_outline_readiness_gate",
        metadata_keys=(
            "blocked_by_chapter_outline_readiness_gate",
            "chapter_outline_readiness_block_codes",
            "chapter_outline_readiness_hint",
            "chapter_outline_readiness_report",
        ),
        repair_strategy="auto",
        tier="core",
    ),
    GateRegistration(
        name="chapter_predraft_quality_gate",
        metadata_keys=("blocked_by_chapter_predraft_quality_gate",),
        repair_strategy="human_review",
        tier="core",
    ),
    GateRegistration(
        name="qimao_opening_gate",
        metadata_keys=("qimao_opening_gate_blocked", "opening_quality_gate_blocked"),
        repair_strategy="rewrite_task",
        terminal_project_keys=("qimao_opening_gate_exhausted",),
        tier="core",
        # Opening tension/hook is local to this chapter's first page. It is the
        # exact gate that looped 青囊不语问阴阳 ch1 forever while later chapters
        # waited — a local prose judgement must never stall forward writing.
        continuation_impact="local",
    ),
    # ---- advanced: polish / signature / style (warn only) ----------------
    # These gates can never block the chapter. A hit stamps a warning on the
    # scene/chapter metadata so downstream reviewers see the signal, but the
    # pipeline continues. WS-C classification:
    #   * ``ai_flavor`` / ``show_dont_tell`` / ``signature_audit`` are
    #     genuine prose polish — a weak-model style regression should
    #     never block a structurally sound chapter.
    # ``phase_d_time_gate`` and ``material_advancement_gate`` stay in
    # ``core`` — they enforce timeline arithmetic (D3 countdown consistency
    # and time-regression) and story-contract delivery (the reveal/rule/
    # evidence promised by the outline must land).  These are correctness
    # gates; a hit means the chapter contradicts the canon, not just
    # that the prose is rough.  Demoting them to warn-only would let
    # contradictions slip into the published text — directly worsening
    # the "逻辑不清晰" complaint WS-C is supposed to fix.
    GateRegistration(
        name="phase_d_time_gate",
        metadata_keys=("blocked_by_phase_d_time_gate",),
        repair_strategy="rewrite_task",
        tier="core",
    ),
    GateRegistration(
        name="ai_flavor_gate",
        metadata_keys=("blocked_by_ai_flavor_gate",),
        repair_strategy="rewrite_task",
        tier="advanced",
        continuation_impact="local",
    ),
    GateRegistration(
        name="show_dont_tell_gate",
        metadata_keys=("blocked_by_show_dont_tell_gate",),
        repair_strategy="rewrite_task",
        tier="advanced",
        continuation_impact="local",
    ),
    GateRegistration(
        name="material_advancement_gate",
        metadata_keys=("blocked_by_material_advancement_gate", "material_advancement_block_codes"),
        repair_strategy="rewrite_task",
        tier="core",
    ),
    GateRegistration(
        name="signature_audit_gate",
        metadata_keys=("blocked_by_signature_audit_gate", "signature_audit_block_codes"),
        repair_strategy="rewrite_task",
        tier="advanced",
        continuation_impact="local",
    ),
)

_REGISTERED_GATE_NAMES = frozenset(gate.name for gate in _GATES)
_BLOCK_METADATA_KEYS = tuple(
    dict.fromkeys(key for gate in _GATES for key in gate.metadata_keys)
)
# Only a *structural* gate may terminally block project resume. A local gate
# (e.g. ``qimao_opening_gate``) confines its failure to one chapter's prose, so
# its terminal key must never freeze the whole book — it would re-create the
# 青囊不语问阴阳 regression where the project paused forever on the opening gate
# while later chapters were never written.
_TERMINAL_PROJECT_KEYS = tuple(
    dict.fromkeys(
        key
        for gate in _GATES
        if gate.continuation_impact == "structural"
        for key in gate.terminal_project_keys
    )
)
_CORE_GATE_NAMES = frozenset(gate.name for gate in _GATES if gate.tier == "core")
_ADVANCED_GATE_NAMES = frozenset(gate.name for gate in _GATES if gate.tier == "advanced")
_CORE_BLOCK_KEYS = tuple(
    dict.fromkeys(key for gate in _GATES if gate.tier == "core" for key in gate.metadata_keys)
)
_ADVANCED_BLOCK_KEYS = tuple(
    dict.fromkeys(
        key for gate in _GATES if gate.tier == "advanced" for key in gate.metadata_keys
    )
)


def registered_gate_names() -> frozenset[str]:
    return _REGISTERED_GATE_NAMES


def core_gate_names() -> frozenset[str]:
    """Names of ``core`` tier gates — structural safety nets that may block."""

    return _CORE_GATE_NAMES


def advanced_gate_names() -> frozenset[str]:
    """Names of ``advanced`` tier gates — polish/signature/style warnings only.

    A hit on one of these must NEVER cause ``machine_repair_required``. See
    WS-C2 of docs/质量回归修复-开发计划-20260602.md.
    """

    return _ADVANCED_GATE_NAMES


def registered_block_metadata_keys() -> tuple[str, ...]:
    return _BLOCK_METADATA_KEYS


def core_block_metadata_keys() -> tuple[str, ...]:
    """Block-metadata keys contributed by ``core`` tier gates.

    Use this in the runtime blocking check so advanced-tier findings cannot
    block the chapter. ``registered_block_metadata_keys()`` remains available
    for documentation and overview schemas that want to surface every gate.
    """

    return _CORE_BLOCK_KEYS


def advanced_block_metadata_keys() -> tuple[str, ...]:
    """Block-metadata keys contributed by ``advanced`` tier gates.

    These are stamped as warnings by their respective gate runners and
    surfaced through project review reports, but they do not feed the
    runtime blocking predicate.
    """

    return _ADVANCED_BLOCK_KEYS


def project_resume_is_terminally_blocked(metadata: Mapping[str, object] | None) -> bool:
    data = metadata if isinstance(metadata, Mapping) else {}
    return any(bool(data.get(key)) for key in _TERMINAL_PROJECT_KEYS)


# ---------------------------------------------------------------------------
# Continuation-impact classification
#
# Single source of truth for "does repairing this chapter block forward
# writing?". Consumed by ``services.repair_impact`` (continuation readiness)
# and the pipeline write-gate. ``structural`` is the conservative default so an
# unclassified or unrecognized block keeps continuation paused rather than
# letting later chapters build on a possibly-broken predecessor.
# ---------------------------------------------------------------------------

_WRITE_SAFETY_GATE_NAME = "write_safety_gate"

_LOCAL_GATE_NAMES = frozenset(
    gate.name for gate in _GATES if gate.continuation_impact == "local"
)
_STRUCTURAL_GATE_NAMES = frozenset(
    gate.name for gate in _GATES if gate.continuation_impact == "structural"
)

# ``write_safety_gate`` is structural *by default* but its specific block code
# decides per hit: length / dialogue / repetition findings are local prose
# defects, whereas dead-character / pronoun / canon regressions corrupt facts
# that later chapters inherit. These mirror the auto-repairable code list in
# ``worker.self_heal`` but are split along the continuation-impact axis.
LOCAL_WRITE_SAFETY_BLOCK_CODES = frozenset(
    {
        "block_low",
        "block_high",
        "dialog_unpaired",
        "ending_sentence_weak",
        "intra_chapter_repetition",
        "cross_chapter_repetition",
    }
)

# Block-metadata keys grouped by continuation impact. ``write_safety_gate`` is
# excluded from both groups because it is resolved by block code, not by the
# mere presence of its metadata key.
_LOCAL_BLOCK_METADATA_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        key
        for gate in _GATES
        if gate.continuation_impact == "local"
        for key in gate.metadata_keys
    )
)
_STRUCTURAL_BLOCK_METADATA_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        key
        for gate in _GATES
        if gate.continuation_impact == "structural"
        and gate.name != _WRITE_SAFETY_GATE_NAME
        for key in gate.metadata_keys
    )
)


def gate_continuation_impact(name: str) -> ContinuationImpact:
    """Return the continuation impact for a registered gate name.

    Unknown gate names resolve to ``"structural"`` (conservative).
    """

    for gate in _GATES:
        if gate.name == name:
            return gate.continuation_impact
    return "structural"


def local_quality_gate_names() -> frozenset[str]:
    """Gates whose failure is confined to the failing chapter's own prose.

    Repairing one of these never changes canon/continuity that a later chapter
    depends on, so new-chapter writing may proceed in parallel with the repair.
    """

    return _LOCAL_GATE_NAMES


def structural_gate_names() -> frozenset[str]:
    """Gates whose failure corrupts the snapshot later chapters build on."""

    return _STRUCTURAL_GATE_NAMES


def _normalize_block_codes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(
            code.strip().lower()
            for code in value.replace(";", ",").split(",")
            if code.strip()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        codes: list[str] = []
        for item in value:
            codes.extend(_normalize_block_codes(item))
        return tuple(codes)
    return ()


def write_safety_block_is_structural(block_code: object) -> bool:
    """Decide whether a ``write_safety_gate`` hit is structural.

    A hit with no resolvable code is treated as structural (conservative). A
    hit is local only when *every* code present is a known local prose defect.
    """

    codes = _normalize_block_codes(block_code)
    if not codes:
        return True
    return any(code not in LOCAL_WRITE_SAFETY_BLOCK_CODES for code in codes)


def chapter_block_is_structural(metadata: Mapping[str, object] | None) -> bool:
    """Whether a blocked chapter's failure blocks forward writing.

    Returns ``True`` when the block corrupts the canon/continuity snapshot that
    later chapters inherit (continuation must wait for repair), ``False`` when
    the block is confined to this chapter's own prose (continuation may proceed
    in parallel). Designed to be called on chapters whose ``production_state``
    is ``"blocked"``; an unrecognized block resolves to ``True`` (conservative).
    """

    data = metadata if isinstance(metadata, Mapping) else {}

    # 1. Any non-write-safety structural gate hit dominates.
    if any(bool(data.get(key)) for key in _STRUCTURAL_BLOCK_METADATA_KEYS):
        return True

    # 2. write_safety_gate is decided by its block code.
    recognized_local = False
    if bool(data.get("blocked_by_write_safety_gate")) or data.get(
        "write_safety_block_code"
    ):
        if write_safety_block_is_structural(data.get("write_safety_block_code")):
            return True
        recognized_local = True

    # 3. Any local gate hit → confined to this chapter's prose.
    if recognized_local or any(
        bool(data.get(key)) for key in _LOCAL_BLOCK_METADATA_KEYS
    ):
        return False

    # 4. Blocked for an unrecognized reason → stay conservative.
    return True


# Map a project-level ``production_pause_reason`` / ``last_generation_gate_reason``
# back to the gate that produced it, so a pause caused by a *local* gate does
# not masquerade as a structural-repair block. Keyed by both the gate name and
# any terminal project key the gate stamps.
_PAUSE_REASON_IMPACT: dict[str, ContinuationImpact] = {}
for _gate in _GATES:
    _PAUSE_REASON_IMPACT[_gate.name] = _gate.continuation_impact
    for _key in _gate.terminal_project_keys:
        _PAUSE_REASON_IMPACT[_key] = _gate.continuation_impact
del _gate


def pause_reason_continuation_impact(reason: str | None) -> ContinuationImpact:
    """Classify a pause reason by the impact of the gate that produced it.

    Unknown / empty reasons resolve to ``"structural"`` (conservative). The
    reason is matched against gate names and their terminal project keys, and a
    ``"<reason>:<detail>"`` form is matched on its base segment.
    """

    if not reason:
        return "structural"
    base = str(reason).strip()
    if base in _PAUSE_REASON_IMPACT:
        return _PAUSE_REASON_IMPACT[base]
    head = base.split(":", 1)[0]
    return _PAUSE_REASON_IMPACT.get(head, "structural")


def pause_reason_is_structural(reason: str | None) -> bool:
    """True when a pause reason corresponds to a structural (downstream) gate."""

    return pause_reason_continuation_impact(reason) == "structural"
