"""Cross-volume plot-convergence detection for the planner.

Root cause this module addresses: even with per-chapter richness gates and
cross-chapter fingerprinting, the *volume-level* plan can still converge —
every volume ends up with the same climax shape, the same antagonist
pressure, and the same reveal rhythm. Readers can guess what happens before
the volume even opens.

This module complements the chapter-level stack:

  * scene_plan_richness — per-scene card richness validation
  * plan_fingerprint — pairwise chapter fingerprint Jaccard scan
  * revealed_ledger — aggregate cross-chapter revealed-facts summary
  * volume_fingerprint (this module) — pairwise **volume** similarity

Volumes are fingerprinted on:

  * volume_theme / volume_goal / volume_obstacle / volume_climax /
    volume_resolution — the macro beats that distinguish one volume from
    another
  * conflict_phase / primary_force_name — two exact-match signals that
    force diversity when repeated

Two entry points:

  * :func:`build_volume_fingerprint` — normalize one VolumePlan entry /
    :class:`VolumeModel` row into a deduplicable fingerprint.
  * :func:`scan_volume_plan_for_convergence` — pairwise Jaccard on a full
    VolumePlan array, returning a :class:`VolumeConvergenceReport`.

The report carries a ``to_prompt_block(language=...)`` renderer suitable
for re-feeding into a planner prompt when the model needs to repair a
converged draft, and a ``to_system_constraint_block`` renderer for the
inverse direction — telling the planner up front which prior volumes
exist so the new batch is forced to diverge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from bestseller.services.deduplication import compute_jaccard_similarity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults / tunables
# ---------------------------------------------------------------------------

# Jaccard thresholds recalibrated 2026-04: production audit of 6 books
# showed the prior 0.45 / 0.65 bar missed structurally-identical volumes
# that used different nouns. A "same-shape, different-words" pair (每卷
# 都是『主角被围→盟友救场→突破境界』) typically scores 0.35-0.55 under
# the CJK 4-char shingle regime — below the old warning line yet readers
# clearly perceive repetition. Lowered bars catch this band.
DEFAULT_WARNING_THRESHOLD = 0.35
DEFAULT_CRITICAL_THRESHOLD = 0.55

# Per-field thresholds — if a single beat (goal / obstacle / climax) is
# highly similar across two volumes, that alone is a critical failure
# even when the *combined* Jaccard is diluted by theme + resolution
# variation. This is the "every volume's goal is 'survive and break
# through'" failure mode.
FIELD_CONVERGENCE_WARNING_THRESHOLD = 0.45
FIELD_CONVERGENCE_CRITICAL_THRESHOLD = 0.70

# Tag-overuse: if ``conflict_phase`` or ``primary_force_name`` repeats
# across this many volumes, promote it from a prompt-only observation
# to a critical finding. Two volumes sharing a phase is expected; three
# is a pattern.
PHASE_OVERUSE_CRITICAL_COUNT = 3
FORCE_OVERUSE_CRITICAL_COUNT = 3

# Minimum characters in the combined text before we bother running Jaccard.
_MIN_FINGERPRINT_LEN = 30

# Minimum characters in a per-field text (goal/obstacle/climax) before
# it's eligible for per-field convergence scoring — prevents short stock
# phrases from spuriously flagging.
_MIN_FIELD_LEN = 12

# Cap volumes displayed in prompt blocks so they don't dominate the prompt.
_PROMPT_BLOCK_VOL_CAP = 12


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VolumeFingerprint:
    """Canonical deduplication payload for a single volume entry."""

    volume_number: int
    volume_title: str
    conflict_phase: str                # exact-match signal
    primary_force_name: str            # exact-match signal
    combined_text: str                 # Jaccard-ready blob
    # Per-field text retained for field-level convergence scoring —
    # catches the "every volume's goal is a rewording of the same
    # template" failure mode even when combined_text Jaccard is diluted.
    goal_text: str = ""
    obstacle_text: str = ""
    climax_text: str = ""


@dataclass(frozen=True)
class VolumeConvergenceFinding:
    """Pair of volumes flagged as too-similar."""

    volume_a: int
    volume_b: int
    similarity: float
    severity: str                      # "warning" | "critical"
    reason: str                        # short human-readable label
    matched_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class VolumeConvergenceReport:
    """Outcome of a cross-volume convergence scan."""

    findings: tuple[VolumeConvergenceFinding, ...] = field(default_factory=tuple)
    conflict_phase_counts: dict[str, int] = field(default_factory=dict)
    force_name_counts: dict[str, int] = field(default_factory=dict)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)

    @property
    def critical_findings(self) -> tuple[VolumeConvergenceFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "critical")

    def to_prompt_block(self, *, language: str = "zh-CN") -> str:
        """Render findings as a prompt block — used when asking the planner
        to repair a converged draft."""
        if not self.findings and not self._overused_tags():
            return ""
        zh = not (language or "").lower().startswith("en")
        lines: list[str] = []
        if zh:
            lines.append("【卷间内容趋同 — 剧情可能重复，需要差异化】")
        else:
            lines.append("[Volume-level convergence — plots may repeat; enforce differentiation]")

        # Pairwise findings. volume_a==0 signals a plan-wide pattern
        # finding (tag overuse) rather than a specific pair.
        for f in self.findings:
            is_pattern = f.volume_a == 0 and f.volume_b == 0
            if zh:
                tag = "❗关键" if f.severity == "critical" else "⚠️提示"
                if is_pattern:
                    lines.append(f"{tag} 全书模式：{f.reason}")
                else:
                    lines.append(
                        f"{tag} 第{f.volume_a}卷 ↔ 第{f.volume_b}卷 "
                        f"(相似度 {f.similarity:.2f}): {f.reason}"
                    )
            else:
                tag = "CRITICAL" if f.severity == "critical" else "WARN"
                if is_pattern:
                    lines.append(f"[{tag}] plan-wide pattern: {f.reason}")
                else:
                    lines.append(
                        f"[{tag}] vol{f.volume_a} ↔ vol{f.volume_b} "
                        f"(sim {f.similarity:.2f}): {f.reason}"
                    )

        # Over-used exact-match tags
        overused = self._overused_tags()
        if overused:
            if zh:
                lines.append("重复使用的卷标签（必须分散）：")
            else:
                lines.append("Over-used volume tags (must be spread across distinct volumes):")
            for label, count in overused:
                lines.append(f"  - {label} ×{count}")
        return "\n".join(lines)

    def _overused_tags(self) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for name, count in self.conflict_phase_counts.items():
            if count >= 2:
                out.append((f"conflict_phase='{name}'", count))
        for name, count in self.force_name_counts.items():
            if count >= 2:
                out.append((f"primary_force_name='{name}'", count))
        return out


# ---------------------------------------------------------------------------
# Fingerprint construction
# ---------------------------------------------------------------------------

def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _get_field(entry: Any, *names: str) -> Any:
    """Read the first non-empty field from ``entry`` by any of ``names``.

    Supports dict-like VolumePlan entries and SQLAlchemy ``VolumeModel`` rows
    (where the fields are ``theme``/``goal``/``obstacle`` without the
    ``volume_`` prefix).
    """
    for name in names:
        if isinstance(entry, dict):
            v = entry.get(name)
        else:
            v = getattr(entry, name, None)
        if v not in (None, "", [], {}):
            return v
    return None


def build_volume_fingerprint(entry: Any) -> VolumeFingerprint:
    """Build a :class:`VolumeFingerprint` from a VolumePlan dict or VolumeModel row.

    Missing fields default to empty strings. The combined text is weighted:
    goal + obstacle + climax + resolution + theme, concatenated. Title and
    force_name are kept separately as exact-match / tag signals.
    """
    volume_number_raw = _get_field(entry, "volume_number")
    try:
        volume_number = int(volume_number_raw) if volume_number_raw is not None else 0
    except (TypeError, ValueError):
        volume_number = 0

    volume_title = _coerce_text(_get_field(entry, "volume_title", "title"))
    conflict_phase = _coerce_text(_get_field(entry, "conflict_phase")).lower()
    primary_force_name = _coerce_text(_get_field(entry, "primary_force_name"))

    theme = _coerce_text(_get_field(entry, "volume_theme", "theme"))
    goal = _coerce_text(_get_field(entry, "volume_goal", "goal"))
    obstacle = _coerce_text(_get_field(entry, "volume_obstacle", "obstacle"))
    climax = _coerce_text(_get_field(entry, "volume_climax"))
    resolution = _coerce_text(_get_field(entry, "volume_resolution"))

    # Weight: goal+obstacle+climax+resolution carry the plot shape; theme is
    # added once for tone. The doubled goal is deliberate — volume_goal is
    # the single strongest convergence signal in the bestseller pipeline.
    combined_parts = [goal, goal, obstacle, climax, resolution, theme]
    combined_text = " | ".join(p for p in combined_parts if p)

    return VolumeFingerprint(
        volume_number=volume_number,
        volume_title=volume_title,
        conflict_phase=conflict_phase,
        primary_force_name=primary_force_name,
        combined_text=combined_text,
        goal_text=goal,
        obstacle_text=obstacle,
        climax_text=climax,
    )


# ---------------------------------------------------------------------------
# Pairwise scan
# ---------------------------------------------------------------------------

def _pair_similarity(a: VolumeFingerprint, b: VolumeFingerprint) -> float:
    if len(a.combined_text) < _MIN_FINGERPRINT_LEN:
        return 0.0
    if len(b.combined_text) < _MIN_FINGERPRINT_LEN:
        return 0.0
    return compute_jaccard_similarity(a.combined_text, b.combined_text)


def _field_similarity(text_a: str, text_b: str) -> float:
    """Compute per-field Jaccard with a length floor.

    Short stock phrases (""、"-"、单词) are ignored to avoid spurious
    matches — the field-level signal is only meaningful when both
    volumes have a non-trivial beat description.
    """

    if len(text_a) < _MIN_FIELD_LEN or len(text_b) < _MIN_FIELD_LEN:
        return 0.0
    return compute_jaccard_similarity(text_a, text_b)


def _match_labels(a: VolumeFingerprint, b: VolumeFingerprint) -> list[str]:
    labels: list[str] = []
    if a.conflict_phase and a.conflict_phase == b.conflict_phase:
        labels.append(f"conflict_phase='{a.conflict_phase}'")
    if a.primary_force_name and a.primary_force_name == b.primary_force_name:
        labels.append(f"primary_force_name='{a.primary_force_name}'")
    return labels


def scan_volume_plan_for_convergence(
    volume_entries: Iterable[Any],
    *,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
    critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD,
    field_warning_threshold: float = FIELD_CONVERGENCE_WARNING_THRESHOLD,
    field_critical_threshold: float = FIELD_CONVERGENCE_CRITICAL_THRESHOLD,
    phase_overuse_critical_count: int = PHASE_OVERUSE_CRITICAL_COUNT,
    force_overuse_critical_count: int = FORCE_OVERUSE_CRITICAL_COUNT,
) -> VolumeConvergenceReport:
    """Scan a VolumePlan JSON array for cross-volume convergence.

    Returns a :class:`VolumeConvergenceReport` combining three signals:

    1. **Combined-text Jaccard** — pairs with Jaccard ≥
       ``critical_threshold`` are critical; between ``warning_threshold``
       and ``critical_threshold`` are warnings.
    2. **Per-field convergence** — separate Jaccard scoring for
       ``goal`` / ``obstacle`` / ``climax`` catches volumes with a
       template beat even when the combined text differs enough to
       dilute the overall similarity. Any single beat at or above
       ``field_critical_threshold`` is itself a critical finding.
    3. **Tag overuse** — ``conflict_phase`` or ``primary_force_name``
       repeating across ≥ ``*_overuse_critical_count`` volumes is
       promoted from a prompt-only observation to a critical finding.

    The prior pairwise-only behaviour (single exact-match on phase or
    force ⇒ warning between any two volumes) is preserved so existing
    warning signals are not lost.
    """
    fingerprints = [build_volume_fingerprint(e) for e in volume_entries]
    findings: list[VolumeConvergenceFinding] = []
    phase_counts: dict[str, int] = {}
    force_counts: dict[str, int] = {}

    for fp in fingerprints:
        if fp.conflict_phase:
            phase_counts[fp.conflict_phase] = phase_counts.get(fp.conflict_phase, 0) + 1
        if fp.primary_force_name:
            force_counts[fp.primary_force_name] = force_counts.get(fp.primary_force_name, 0) + 1

    n = len(fingerprints)
    for i in range(n):
        for j in range(i + 1, n):
            a = fingerprints[i]
            b = fingerprints[j]
            sim = _pair_similarity(a, b)
            labels = _match_labels(a, b)

            # Per-field convergence — the strongest fidelity-preserving
            # signal. If any single beat is near-identical, flag the
            # pair as critical regardless of combined Jaccard.
            field_findings: list[tuple[str, float]] = []
            for field_name, text_a, text_b in (
                ("volume_goal", a.goal_text, b.goal_text),
                ("volume_obstacle", a.obstacle_text, b.obstacle_text),
                ("volume_climax", a.climax_text, b.climax_text),
            ):
                fsim = _field_similarity(text_a, text_b)
                if fsim >= field_warning_threshold:
                    field_findings.append((field_name, fsim))

            if field_findings:
                # Worst field drives severity.
                worst = max(field_findings, key=lambda x: x[1])
                worst_field, worst_sim = worst
                severity = (
                    "critical" if worst_sim >= field_critical_threshold else "warning"
                )
                reason_bits = [
                    f"{fname} Jaccard {fsim:.2f}"
                    for fname, fsim in field_findings
                ]
                findings.append(VolumeConvergenceFinding(
                    volume_a=a.volume_number,
                    volume_b=b.volume_number,
                    similarity=worst_sim,
                    severity=severity,
                    reason="; ".join(reason_bits),
                    matched_fields=tuple(f for f, _ in field_findings),
                ))
                # Skip the separate combined-text finding — the per-field
                # finding is strictly more informative for this pair.
                continue

            # Exact-match on conflict_phase OR primary_force_name is itself
            # a warning, even when Jaccard is low.
            if sim < warning_threshold and not labels:
                continue
            severity = "critical" if sim >= critical_threshold else "warning"
            reason = ", ".join(labels) if labels else f"combined Jaccard {sim:.2f}"
            findings.append(VolumeConvergenceFinding(
                volume_a=a.volume_number,
                volume_b=b.volume_number,
                similarity=sim,
                severity=severity,
                reason=reason,
                matched_fields=tuple(labels),
            ))

    # ── Tag-overuse criticals ───────────────────────────────────────
    # A conflict_phase or primary_force_name appearing across ≥ N
    # volumes is the clearest signal of plan-wide template collapse.
    # Surface it as a synthetic pairwise critical carrying -1 for
    # volume numbers so downstream code can distinguish a "pattern"
    # finding from a genuine pair.
    overused_phases = [
        (phase, cnt) for phase, cnt in phase_counts.items()
        if cnt >= phase_overuse_critical_count
    ]
    for phase, cnt in overused_phases:
        findings.append(VolumeConvergenceFinding(
            volume_a=0,
            volume_b=0,
            similarity=float(cnt),
            severity="critical",
            reason=(
                f"conflict_phase='{phase}' repeats across {cnt} volumes "
                "— plan-wide template collapse"
            ),
            matched_fields=("conflict_phase_overuse",),
        ))

    overused_forces = [
        (force, cnt) for force, cnt in force_counts.items()
        if cnt >= force_overuse_critical_count
    ]
    for force, cnt in overused_forces:
        findings.append(VolumeConvergenceFinding(
            volume_a=0,
            volume_b=0,
            similarity=float(cnt),
            severity="critical",
            reason=(
                f"primary_force_name='{force}' repeats across {cnt} "
                "volumes — same antagonist pressure recycled"
            ),
            matched_fields=("primary_force_name_overuse",),
        ))

    return VolumeConvergenceReport(
        findings=tuple(findings),
        conflict_phase_counts=phase_counts,
        force_name_counts=force_counts,
    )


# ---------------------------------------------------------------------------
# Prior-volume summary block (fed into _volume_outline_prompts)
# ---------------------------------------------------------------------------

def render_prior_volumes_summary_block(
    prior_entries: Iterable[Any],
    *,
    current_volume_number: int,
    language: str = "zh-CN",
    cap: int = _PROMPT_BLOCK_VOL_CAP,
) -> str:
    """Render a compact summary of *prior* volume plan entries so the LLM
    sees explicitly what has come before when planning the next volume's
    chapter outline.

    This is the forward-looking companion to
    :func:`scan_volume_plan_for_convergence`: instead of flagging an
    already-converged plan, it pre-conditions the LLM to diverge.
    """
    entries = [e for e in prior_entries if e is not None]
    # Include only genuinely-prior volumes.
    filtered: list[tuple[int, VolumeFingerprint]] = []
    for e in entries:
        fp = build_volume_fingerprint(e)
        if fp.volume_number == 0:
            continue
        if fp.volume_number >= current_volume_number:
            continue
        filtered.append((fp.volume_number, fp))
    if not filtered:
        return ""
    filtered.sort(key=lambda x: x[0])
    # Keep the most recent ``cap`` volumes — long series cap out at 20+ vols.
    filtered = filtered[-cap:]

    zh = not (language or "").lower().startswith("en")
    lines: list[str] = []
    if zh:
        lines.append("【已写定的前序卷概要 — 新卷必须与以下卷形成明显差异】")
    else:
        lines.append("[Prior volume summary — the new volume MUST differ on axis, force, phase, and payoff]")
    for _, fp in filtered:
        title = fp.volume_title or ("(未命名)" if zh else "(untitled)")
        phase = fp.conflict_phase or ("-" if zh else "-")
        force = fp.primary_force_name or ("-" if zh else "-")
        body = fp.combined_text[:120]
        if zh:
            lines.append(
                f"  - 第{fp.volume_number}卷 《{title}》 conflict_phase={phase} "
                f"primary_force={force}\n    核心 beat: {body}"
            )
        else:
            lines.append(
                f"  - vol{fp.volume_number} \"{title}\" conflict_phase={phase} "
                f"primary_force={force}\n    core beats: {body}"
            )
    if zh:
        lines.append(
            "差异化硬约束：新卷的 conflict_phase、primary_force_name、core payoff、"
            "climax 形态、reveal 类型、节奏弧都不得与上列任何一卷雷同。"
        )
    else:
        lines.append(
            "Differentiation HARD CONSTRAINT: the new volume's conflict_phase, "
            "primary_force_name, core payoff, climax shape, reveal type, and "
            "rhythm arc must ALL differ from every volume listed above."
        )
    return "\n".join(lines)
