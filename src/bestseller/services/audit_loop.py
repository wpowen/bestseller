"""L7 Continuous Audit Loop.

Post-generation sweep that detects and optionally repairs residual quality
issues that slipped past the write-gate — either because the check is too
expensive to run per-chapter, because the issue only manifests across
multiple chapters, or because the project was produced before L1-L6 were in
place. Phase 1 ships only ``GapRepairer`` (chapter-sequence holes); later
phases absorb ``scripts/`` one auditor at a time.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import logging
import re
from types import SimpleNamespace
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterAuditFindingModel,
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    ProjectModel,
    RewriteTaskModel,
)
from bestseller.services.chapter_validator import (
    CliffhangerRotationCheck,
    classify_cliffhanger,
)
from bestseller.services.consistency import detect_chapter_sequence_gaps
from bestseller.services.hype_engine import (
    HypeType,
    classify_hype,
)
from bestseller.services.invariants import (
    CliffhangerType,
    InvariantSeedError,
    ProjectInvariants,
    infer_pov_from_sample,
    invariants_from_dict,
    seed_invariants,
)
from bestseller.services.output_validator import (
    OutputValidator,
    ValidationContext,
    build_full_audit_validator,
    build_phase1_validator,
)
from bestseller.services.setup_payoff_tracker import (
    DEFAULT_HUMILIATION_KEYWORDS,
    DEFAULT_PAYOFF_HYPE_TYPES,
    analyze_setup_payoff,
)

logger = logging.getLogger(__name__)


Severity = Literal["info", "warn", "critical"]


# ---------------------------------------------------------------------------
# Data structures.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditFinding:
    """Single issue an auditor reports.

    ``auto_repairable`` tells the orchestrator whether it may call
    ``Auditor.repair`` without human confirmation. Destructive repairs
    (rewriting existing chapters, moving numbering) must always set this
    ``False`` so they surface in a dry-run review first.
    """

    auditor: str
    code: str
    severity: Severity
    chapter_no: int | None
    detail: str
    auto_repairable: bool


@dataclass(frozen=True)
class RepairResult:
    success: bool
    description: str


class Auditor(Protocol):
    """Common interface for every post-generation check.

    ``scan`` is pure-ish (reads DB / filesystem but makes no mutations);
    ``repair`` is the only method allowed to write.
    """

    name: str

    async def scan(self, session: AsyncSession, project_id: UUID) -> list[AuditFinding]:  # pragma: no cover - protocol
        ...

    async def repair(self, session: AsyncSession, finding: AuditFinding) -> RepairResult:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# GapRepairer — fixes chapter-sequence holes (problem #3).
# ---------------------------------------------------------------------------


class GapRepairer:
    """Detect chapters that were skipped in the numeric sequence.

    The scanner is a thin wrapper around
    ``consistency.detect_chapter_sequence_gaps`` so both the pipeline's
    tail-stage audit and the offline CLI share the same detection logic.

    Repair is **not** ``auto_repairable``: generating a missing chapter
    requires the full L1→L6 pipeline (and typically costs LLM budget). The
    CLI's ``--auto-repair`` flag explicitly opts in and is the only caller
    that should invoke ``repair``.
    """

    name = "GapRepairer"
    code_gap = "CHAPTER_GAP"

    async def scan(
        self, session: AsyncSession, project_id: UUID
    ) -> list[AuditFinding]:
        result = await session.scalars(
            select(ChapterModel.chapter_number).where(
                ChapterModel.project_id == project_id
            )
        )
        chapter_numbers = list(result)
        return [
            AuditFinding(
                auditor=self.name,
                code=self.code_gap,
                severity="critical",
                chapter_no=gap,
                detail=f"Chapter {gap} missing from sequence",
                auto_repairable=False,
            )
            for gap in detect_chapter_sequence_gaps(chapter_numbers)
        ]

    async def repair(
        self, session: AsyncSession, finding: AuditFinding
    ) -> RepairResult:
        # Phase 1: repair wiring is deferred to the CLI layer, which orchestrates
        # the full L3 prompt build → L4 validation → L6 write-gate cycle needed
        # to regenerate the missing chapter. The auditor itself stays pure so
        # unit tests don't pull in the pipeline.
        return RepairResult(
            success=False,
            description=(
                f"GapRepairer.repair is not self-contained; invoke "
                f"`bestseller audit <slug> --auto-repair` to regenerate "
                f"chapter {finding.chapter_no} via the full pipeline."
            ),
        )


# ---------------------------------------------------------------------------
# ContentAuditor — retrospective L4 scan over current chapter draft versions.
# ---------------------------------------------------------------------------


_SEVERITY_BY_L4_SEVERITY: dict[str, Severity] = {
    "block": "critical",
    "warn": "warn",
    "info": "info",
}


class ContentAuditor:
    """Replay L4 + L5 checks over the current ``chapter_draft_versions`` of a project.

    Phase 1's L4 runs inline in ``drafts.assemble_chapter_draft`` — so it only
    catches issues in *new* generations. This auditor lets the offline CLI
    surface the same findings on already-persisted novels, which is how we
    triage the four existing productions without rewriting them first.

    The auditor seeds ``ProjectInvariants`` on the fly when the project has
    none persisted yet: the defaults derived from ``projects.language`` and
    ``projects.target_word_count`` match what the pipeline would pick for a
    fresh run, so retrospective findings are comparable to future runs.

    Validator scope is configurable:

    * ``validator_profile="phase1"`` — CJK + Length only. Same behavior the
      audit had prior to expansion; useful for CI gates where false-positive
      cost is high.
    * ``validator_profile="full"`` (default) — adds NamingConsistency,
      EntityDensity, DialogIntegrity, POVLock, plus per-chapter
      CliffhangerRotationCheck seeded from the trailing-N detected
      cliffhangers in this project's own draft history.

    ``recent_cliffhangers_window`` lets callers widen/shrink the
    no-repeat-within window independently of the invariant (the invariant
    default is 3). Passing 0 disables the rotation check for audits even in
    full mode.
    """

    name = "ContentAuditor"

    def __init__(
        self,
        *,
        validator_profile: Literal["phase1", "full"] = "full",
        recent_cliffhangers_window: int | None = None,
    ) -> None:
        self.validator_profile = validator_profile
        self.recent_cliffhangers_window = recent_cliffhangers_window

    async def _load_invariants(
        self, session: AsyncSession, project: ProjectModel
    ) -> ProjectInvariants:
        payload = project.invariants_json
        if payload:
            try:
                return invariants_from_dict(payload)
            except InvariantSeedError:
                logger.warning(
                    "Invariants payload for project %s is malformed — reseeding "
                    "from project defaults for audit",
                    project.id,
                )
        target_chapters = max(int(project.target_chapters or 0), 1)
        target_words_total = max(int(project.target_word_count or 0), 0)
        per_chapter = target_words_total // target_chapters if target_words_total else 2200
        words = SimpleNamespace(
            min=1800,
            target=min(max(per_chapter or 2200, 1801), 2999),
            max=3000,
        )
        # Auto-detect POV from actual chapter content so projects produced
        # before L1 was in place (no invariants_json) don't default to the
        # wrong POV and flag every chapter as "POV drift". We sample the
        # first few chapters' content, strip dialogue, and count pronouns.
        inferred_pov = await self._infer_pov(session, project.id, project.language or "en")
        return seed_invariants(
            project_id=project.id,
            language=project.language or "en",
            words_per_chapter=words,
            pov=inferred_pov,
        )

    async def _infer_pov(
        self, session: AsyncSession, project_id: UUID, language: str
    ) -> str:
        """Sample up to 3 early chapters and infer first vs close_third POV.

        Uses ``ChapterDraftVersionModel`` content (the same store the audit
        reads from) so the inference matches what will be validated. Falls
        back to ``close_third`` when no drafts are available.
        """

        sample_rows = (
            await session.scalars(
                select(ChapterDraftVersionModel.content_md)
                .join(ChapterModel, ChapterModel.id == ChapterDraftVersionModel.chapter_id)
                .where(
                    ChapterModel.project_id == project_id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
                .order_by(ChapterModel.chapter_number.asc())
                .limit(3)
            )
        ).all()
        joined = "\n\n".join(s for s in sample_rows if s)
        if not joined:
            return "close_third"
        return infer_pov_from_sample(joined, language=language)

    async def _load_allowed_names(
        self, session: AsyncSession, project_id: UUID
    ) -> frozenset[str]:
        """Fetch the project's canonical character names for NamingConsistencyCheck.

        Names are pulled from the persisted ``characters`` table so the audit
        sees exactly the roster the pipeline will also see. When there are no
        characters (very early project state) we return an empty frozenset
        and ``NamingConsistencyCheck`` gracefully no-ops.
        """

        characters = await session.scalars(
            select(CharacterModel).where(CharacterModel.project_id == project_id)
        )
        names: set[str] = set()
        for character in characters:
            if isinstance(character, str):
                if character.strip():
                    names.add(character.strip())
                continue
            if character.name and character.name.strip():
                names.add(character.name.strip())
            metadata = character.metadata_json or {}
            if not isinstance(metadata, dict):
                continue
            aliases = metadata.get("aliases")
            if isinstance(aliases, str) and aliases.strip():
                names.add(aliases.strip())
            elif isinstance(aliases, list):
                names.update(
                    alias.strip()
                    for alias in aliases
                    if isinstance(alias, str) and alias.strip()
                )
            cast_entry = metadata.get("cast_entry")
            if isinstance(cast_entry, dict):
                cast_aliases = cast_entry.get("aliases")
                if isinstance(cast_aliases, str) and cast_aliases.strip():
                    names.add(cast_aliases.strip())
                elif isinstance(cast_aliases, list):
                    names.update(
                        alias.strip()
                        for alias in cast_aliases
                        if isinstance(alias, str) and alias.strip()
                    )
        return frozenset(names)

    def _build_validator(self) -> OutputValidator:
        if self.validator_profile == "phase1":
            return build_phase1_validator()
        return build_full_audit_validator()

    async def scan(
        self, session: AsyncSession, project_id: UUID
    ) -> list[AuditFinding]:
        project = (
            await session.scalars(
                select(ProjectModel).where(ProjectModel.id == project_id)
            )
        ).first()
        if project is None:
            return []

        invariants = await self._load_invariants(session, project)
        allowed_names = await self._load_allowed_names(session, project_id)
        validator = self._build_validator()

        # Cliffhanger rotation window — default to the invariant policy but
        # allow per-call override, with 0 disabling the check entirely.
        cliff_window = self.recent_cliffhangers_window
        if cliff_window is None:
            cliff_window = invariants.cliffhanger_policy.no_repeat_within
        cliff_rotation = (
            CliffhangerRotationCheck()
            if self.validator_profile == "full" and cliff_window > 0
            else None
        )
        cliff_history: list[CliffhangerType] = []

        stmt = (
            select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
            .join(
                ChapterDraftVersionModel,
                ChapterDraftVersionModel.chapter_id == ChapterModel.id,
            )
            .where(
                ChapterModel.project_id == project_id,
                ChapterDraftVersionModel.is_current.is_(True),
                ChapterDraftVersionModel.content_md.is_not(None),
            )
            .order_by(ChapterModel.chapter_number)
        )
        rows = (await session.execute(stmt)).all()

        findings: list[AuditFinding] = []
        for chapter_no, content_md in rows:
            if not content_md:
                continue
            chapter_no_int = int(chapter_no) if chapter_no is not None else 0
            recent = (
                tuple(cliff_history[-cliff_window:]) if cliff_window else ()
            )
            ctx = ValidationContext(
                invariants=invariants,
                chapter_no=chapter_no_int,
                scope="chapter",
                allowed_names=allowed_names,
                recent_cliffhangers=recent,
            )
            report = validator.validate(content_md, ctx)
            # Also run the rotation check with the live trailing window.
            if cliff_rotation is not None:
                for violation in cliff_rotation.run(content_md, ctx) or []:
                    findings.append(
                        AuditFinding(
                            auditor=self.name,
                            code=violation.code,
                            severity=_SEVERITY_BY_L4_SEVERITY.get(
                                violation.severity, "warn"
                            ),
                            chapter_no=chapter_no_int,
                            detail=violation.detail,
                            auto_repairable=False,
                        )
                    )
            for violation in report.violations:
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code=violation.code,
                        severity=_SEVERITY_BY_L4_SEVERITY.get(
                            violation.severity, "warn"
                        ),
                        chapter_no=chapter_no_int,
                        detail=violation.detail,
                        auto_repairable=False,
                    )
                )
            # Update rolling cliffhanger history *after* scoring this chapter
            # so chapter N is compared against chapters N-1..N-W, not itself.
            detected = classify_cliffhanger(
                content_md, invariants.language
            )
            if detected is not None:
                cliff_history.append(detected)
        return findings

    async def repair(
        self, session: AsyncSession, finding: AuditFinding
    ) -> RepairResult:
        return RepairResult(
            success=False,
            description=(
                f"ContentAuditor.repair is not self-contained; regenerate chapter "
                f"{finding.chapter_no} via the pipeline CLI to resolve "
                f"{finding.code}."
            ),
        )


# ---------------------------------------------------------------------------
# PleasureDistributionAudit — Hype Engine Phase 2 book-level audit.
# ---------------------------------------------------------------------------


class PleasureDistributionAudit:
    """Scan persisted hype metadata + raw draft text for aggregate issues.

    Three book-level findings the per-chapter L5 checks cannot detect:

    * ``PLEASURE_HYPE_GAP`` — a run of consecutive chapters with no
      detectable hype (classifier returns None AND persisted ``hype_type``
      is null) that exceeds ``max_consecutive_gaps``. Signals the book has
      drifted into long low-intensity stretches.
    * ``PLEASURE_COMEDIC_BEAT_STARVED`` — observed ratio of chapters with
      a COMEDIC_BEAT (classifier OR persisted) falls below the preset's
      ``comedic_beat_density_target`` by more than ``starvation_slack``.
    * ``PLEASURE_HYPE_HOGS_ENDING`` — classifier-detected hype for the
      final ``tail_chars`` of the chapter dominates vs the chapter-wide
      classification for more than half of the sampled chapters — means
      the LLM keeps writing the hype peak into the last paragraph and
      starving the cliffhanger.

    All findings are informational by default: the Phase 2 gate config
    maps ``PLEASURE_*`` codes to ``audit_only``. Repair is never
    auto-applied — pacing this is the writer's call.
    """

    name = "PleasureDistributionAudit"
    code_gap = "PLEASURE_HYPE_GAP"
    code_comedic = "PLEASURE_COMEDIC_BEAT_STARVED"
    code_hogs_ending = "PLEASURE_HYPE_HOGS_ENDING"

    def __init__(
        self,
        *,
        max_consecutive_gaps: int = 3,
        starvation_slack: float = 0.5,
        hogs_ending_threshold: float = 0.5,
        tail_chars: int = 1500,
    ) -> None:
        """
        ``max_consecutive_gaps`` — strictly greater-than count of hype-free
        chapters in a row before the gap finding fires.
        ``starvation_slack`` — observed comedic density must fall below
        ``target * (1 - slack)`` to qualify as starvation.
        ``hogs_ending_threshold`` — proportion of sampled chapters whose
        tail-classified hype matches the full-text classification above
        which the "hype hogging the ending" finding fires.
        """

        self.max_consecutive_gaps = max_consecutive_gaps
        self.starvation_slack = starvation_slack
        self.hogs_ending_threshold = hogs_ending_threshold
        self.tail_chars = tail_chars

    async def scan(
        self, session: AsyncSession, project_id: UUID
    ) -> list[AuditFinding]:
        project = (
            await session.scalars(
                select(ProjectModel).where(ProjectModel.id == project_id)
            )
        ).first()
        if project is None:
            return []

        try:
            invariants = (
                invariants_from_dict(project.invariants_json)
                if project.invariants_json
                else None
            )
        except InvariantSeedError:
            invariants = None
        scheme = invariants.hype_scheme if invariants is not None else None
        if scheme is None or scheme.is_empty:
            return []

        language = (project.language or "zh-CN").strip() or "zh-CN"
        rows = (
            await session.execute(
                select(
                    ChapterModel.chapter_number,
                    ChapterModel.hype_type,
                    ChapterDraftVersionModel.content_md,
                )
                .join(
                    ChapterDraftVersionModel,
                    ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                )
                .where(
                    ChapterModel.project_id == project_id,
                    ChapterDraftVersionModel.is_current.is_(True),
                    ChapterDraftVersionModel.content_md.is_not(None),
                )
                .order_by(ChapterModel.chapter_number)
            )
        ).all()
        if not rows:
            return []

        findings: list[AuditFinding] = []
        comedic_chapters = 0
        total_chapters = 0
        gap_run = 0
        gap_start: int | None = None
        hogs_ending_matches = 0
        hogs_ending_total = 0

        def _flush_gap(end_chapter: int) -> None:
            """Emit a gap finding covering the closed run (if big enough)."""
            nonlocal gap_run, gap_start
            if gap_run > self.max_consecutive_gaps and gap_start is not None:
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code=self.code_gap,
                        severity="warn",
                        chapter_no=gap_start,
                        detail=(
                            f"{gap_run} consecutive chapters without hype "
                            f"(chapters {gap_start}-{end_chapter})"
                        ),
                        auto_repairable=False,
                    )
                )
            gap_run = 0
            gap_start = None

        for chapter_number, persisted_hype_type, content_md in rows:
            total_chapters += 1
            chapter_no = int(chapter_number) if chapter_number is not None else 0

            classified = classify_hype(content_md or "", language)
            classified_type = classified[0] if classified else None

            # Normalise the persisted value (enum string) for comparison.
            persisted_type: HypeType | None = None
            if persisted_hype_type:
                try:
                    persisted_type = HypeType(str(persisted_hype_type))
                except ValueError:
                    persisted_type = None

            effective = persisted_type or classified_type
            if effective is None:
                if gap_run == 0:
                    gap_start = chapter_no
                gap_run += 1
            else:
                _flush_gap(end_chapter=chapter_no - 1)

            if effective == HypeType.COMEDIC_BEAT:
                comedic_chapters += 1

            if content_md and classified_type is not None:
                tail_classified = classify_hype(
                    content_md, language, segment="tail", tail_chars=self.tail_chars
                )
                if (
                    tail_classified is not None
                    and tail_classified[0] == classified_type
                ):
                    hogs_ending_matches += 1
                hogs_ending_total += 1

        _flush_gap(end_chapter=int(rows[-1][0]) if rows else 0)

        # Comedic beat starvation.
        target = max(scheme.comedic_beat_density_target, 0.0)
        if total_chapters >= 5 and target > 0.0:
            observed = comedic_chapters / total_chapters
            floor = target * (1.0 - self.starvation_slack)
            if observed < floor:
                # F11 — escalate to critical when the violation is chronic:
                # the project has had at least 10 chapters and the observed
                # density is less than half the floor (i.e. the gap is
                # severe, not borderline). Lighter violations remain warn.
                chronic = total_chapters >= 10 and observed < (floor * 0.5)
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code=self.code_comedic,
                        severity="critical" if chronic else "warn",
                        chapter_no=None,
                        detail=(
                            f"Comedic beat density {observed:.2%} below "
                            f"target {target:.2%} by more than "
                            f"{self.starvation_slack:.0%} slack "
                            f"({comedic_chapters}/{total_chapters} chapters)"
                            + (
                                "; CHRONIC (≥10 chapters and < ½ of floor)"
                                if chronic
                                else ""
                            )
                        ),
                        auto_repairable=False,
                    )
                )

        # Hype hogging the ending.
        if hogs_ending_total >= 5:
            ratio = hogs_ending_matches / hogs_ending_total
            if ratio >= self.hogs_ending_threshold:
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code=self.code_hogs_ending,
                        severity="info",
                        chapter_no=None,
                        detail=(
                            f"Hype peak collides with chapter ending in "
                            f"{hogs_ending_matches}/{hogs_ending_total} sampled "
                            f"chapters (≥ {self.hogs_ending_threshold:.0%} threshold); "
                            f"consider moving hype to mid-chapter and reserving "
                            f"the final paragraph for a cliffhanger."
                        ),
                        auto_repairable=False,
                    )
                )

        return findings

    async def repair(
        self, session: AsyncSession, finding: AuditFinding
    ) -> RepairResult:
        return RepairResult(
            success=False,
            description=(
                f"PleasureDistributionAudit.repair is not self-contained; "
                f"regenerate affected chapters via the pipeline CLI to fix "
                f"{finding.code}."
            ),
        )


# ---------------------------------------------------------------------------
# SetupPayoffTrackerAudit — humiliation → counterattack debt (Phase 3).
# ---------------------------------------------------------------------------


class SetupPayoffTrackerAudit:
    """Emit ``PLEASURE_SETUP_PAYOFF_DEBT`` for unpaid humiliation setups.

    Delegates the detection to ``setup_payoff_tracker.analyze_setup_payoff``
    — a pure-function primitive — and wraps each returned ``SetupPayoffDebt``
    as a finding anchored at the setup chapter so reviewers can jump
    straight to it. The ``payoff_window_chapters`` knob is read from the
    project's ``HypeScheme`` when present (preset-declared per plan); we
    fall back to ``DEFAULT_PAYOFF_WINDOW_CHAPTERS`` when no scheme is set
    so legacy projects still get the signal.

    Repair is intentionally manual — fixing a missing face-slap means
    rewriting chapters, which belongs in the pipeline, not here.
    """

    name = "SetupPayoffTrackerAudit"
    code_debt = "PLEASURE_SETUP_PAYOFF_DEBT"

    def __init__(
        self,
        *,
        humiliation_keywords: tuple[str, ...] = DEFAULT_HUMILIATION_KEYWORDS,
        payoff_hype_types: frozenset[HypeType] = DEFAULT_PAYOFF_HYPE_TYPES,
        classify_when_missing: bool = True,
    ) -> None:
        self.humiliation_keywords = humiliation_keywords
        self.payoff_hype_types = payoff_hype_types
        self.classify_when_missing = classify_when_missing

    async def scan(
        self, session: AsyncSession, project_id: UUID
    ) -> list[AuditFinding]:
        project = (
            await session.scalars(
                select(ProjectModel).where(ProjectModel.id == project_id)
            )
        ).first()
        if project is None:
            return []

        # Read payoff_window_chapters from the project's hype scheme when
        # available; legacy projects with no scheme fall back to the
        # module default (5) so the audit is not a no-op for them.
        try:
            invariants = (
                invariants_from_dict(project.invariants_json)
                if project.invariants_json
                else None
            )
        except InvariantSeedError:
            invariants = None
        if invariants is not None and invariants.hype_scheme is not None:
            payoff_window = invariants.hype_scheme.payoff_window_chapters
        else:
            payoff_window = 5

        language = (project.language or "zh-CN").strip() or "zh-CN"

        rows = (
            await session.execute(
                select(
                    ChapterModel.chapter_number,
                    ChapterModel.hype_type,
                    ChapterDraftVersionModel.content_md,
                )
                .join(
                    ChapterDraftVersionModel,
                    ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                )
                .where(
                    ChapterModel.project_id == project_id,
                    ChapterDraftVersionModel.is_current.is_(True),
                    ChapterDraftVersionModel.content_md.is_not(None),
                )
                .order_by(ChapterModel.chapter_number)
            )
        ).all()
        if not rows:
            return []

        chapter_texts: list[tuple[int, str]] = []
        chapter_hype: list[tuple[int, HypeType | None]] = []
        for chapter_number, persisted_hype_type, content_md in rows:
            ch_no = int(chapter_number) if chapter_number is not None else 0
            chapter_texts.append((ch_no, content_md or ""))
            persisted_type: HypeType | None = None
            if persisted_hype_type:
                try:
                    persisted_type = HypeType(str(persisted_hype_type))
                except ValueError:
                    persisted_type = None
            chapter_hype.append((ch_no, persisted_type))

        report = analyze_setup_payoff(
            chapter_texts=chapter_texts,
            chapter_hype=chapter_hype,
            humiliation_keywords=self.humiliation_keywords,
            payoff_hype_types=self.payoff_hype_types,
            payoff_window_chapters=payoff_window,
            language=language,
            classify_when_missing=self.classify_when_missing,
        )

        return [
            AuditFinding(
                auditor=self.name,
                code=self.code_debt,
                severity="warn",
                chapter_no=debt.setup_chapter,
                detail=(
                    f"Humiliation setup at chapter {debt.setup_chapter} "
                    f"(keywords: {', '.join(debt.matched_keywords)}) went "
                    f"unpaid through chapter {debt.window_end_chapter}; "
                    f"expected COUNTERATTACK / FACE_SLAP / REVENGE_CLOSURE "
                    f"/ UNDERDOG_WIN within {report.payoff_window_chapters} "
                    "chapters."
                ),
                auto_repairable=False,
            )
            for debt in report.debts
        ]

    async def repair(
        self, session: AsyncSession, finding: AuditFinding
    ) -> RepairResult:
        return RepairResult(
            success=False,
            description=(
                "SetupPayoffTrackerAudit.repair is not self-contained; "
                "regenerate the debt's payoff chapter via the pipeline "
                "CLI with an explicit COUNTERATTACK/FACE_SLAP hype hint."
            ),
        )


# ---------------------------------------------------------------------------
# Orchestrator.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditReport:
    project_id: UUID
    findings: tuple[AuditFinding, ...]

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)

    def by_code(self) -> dict[str, list[AuditFinding]]:
        bucket: dict[str, list[AuditFinding]] = {}
        for finding in self.findings:
            bucket.setdefault(finding.code, []).append(finding)
        return bucket


# ---------------------------------------------------------------------------
# OpeningTerminologyAudit — F8: enforce ``terminology_budget`` in opening chapters.
#
# The opening_quality_contract may declare a per-chapter budget for first-time
# private terminology + a list of deferred_terms that must NOT appear in early
# chapters. AI long-form output frequently violates this by dumping all coined
# nouns into Ch1, suffocating new-reader attention. This audit detects such
# overload and emits one ``TERM_OVERLOAD_OPENING`` finding per offending early
# chapter, severity=critical so the rewrite_task pipeline picks it up.
# ---------------------------------------------------------------------------


class OpeningTerminologyAudit:
    """Detect private-terminology overload in the first N chapters.

    Reads ``opening_quality_contract.terminology_budget`` from project
    metadata:

    * ``chapter_1_max`` — max distinct kept terms in Ch1 (default 5)
    * ``chapter_2_max_new`` — max NEW (not seen in Ch1) terms in Ch2 (default 3)
    * ``chapter_3_max_new`` — max NEW (not seen in Ch1-2) terms in Ch3 (default 3)
    * ``kept_terms_ch1`` — list of approved kept terms (these count toward Ch1 budget)
    * ``deferred_terms`` — list of terms that must NOT appear in chapters 1-N

    The audit scans Ch1-3 (or Ch1-10 if extended) and emits a critical
    finding for each chapter that violates either the count budget or the
    deferred_terms list.
    """

    name = "OpeningTerminologyAudit"
    code_overload = "TERM_OVERLOAD_OPENING"
    code_deferred = "TERM_DEFERRED_LEAKED"

    DEFAULT_OPENING_CHAPTERS = 3
    DEFAULT_BUDGET_CH1 = 5
    DEFAULT_BUDGET_NEW_PER_CHAPTER = 3

    def __init__(self, *, opening_chapter_window: int | None = None) -> None:
        self.opening_chapter_window = opening_chapter_window

    async def scan(
        self, session: AsyncSession, project_id: UUID
    ) -> list[AuditFinding]:
        from collections.abc import Mapping as _Mapping

        project = (
            await session.scalars(
                select(ProjectModel).where(ProjectModel.id == project_id)
            )
        ).first()
        if project is None:
            return []

        metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
        contract = metadata.get("opening_quality_contract") or metadata.get(
            "qimao_opening_contract"
        )
        if not isinstance(contract, _Mapping):
            return []

        budget = contract.get("terminology_budget")
        if not isinstance(budget, _Mapping):
            return []

        kept_terms: tuple[str, ...] = tuple(
            str(t).strip() for t in (budget.get("kept_terms_ch1") or ()) if str(t).strip()
        )
        deferred_terms: tuple[str, ...] = tuple(
            str(t).strip() for t in (budget.get("deferred_terms") or ()) if str(t).strip()
        )
        if not kept_terms and not deferred_terms:
            return []

        ch1_max = int(budget.get("chapter_1_max") or self.DEFAULT_BUDGET_CH1)
        ch2_new_max = int(
            budget.get("chapter_2_max_new") or self.DEFAULT_BUDGET_NEW_PER_CHAPTER
        )
        ch3_new_max = int(
            budget.get("chapter_3_max_new") or self.DEFAULT_BUDGET_NEW_PER_CHAPTER
        )
        window = int(
            self.opening_chapter_window
            or budget.get("opening_chapter_window")
            or self.DEFAULT_OPENING_CHAPTERS
        )

        rows = (
            await session.execute(
                select(
                    ChapterModel.chapter_number,
                    ChapterDraftVersionModel.content_md,
                )
                .join(
                    ChapterDraftVersionModel,
                    ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                )
                .where(
                    ChapterModel.project_id == project_id,
                    ChapterDraftVersionModel.is_current.is_(True),
                    ChapterModel.chapter_number <= window,
                )
                .order_by(ChapterModel.chapter_number.asc())
            )
        ).all()
        if not rows:
            return []

        findings: list[AuditFinding] = []
        seen_kept: set[str] = set()

        for chapter_number, content in rows:
            content = content or ""
            chapter_kept_new = [
                t for t in kept_terms
                if t in content and t not in seen_kept
            ]
            chapter_deferred = [t for t in deferred_terms if t in content]

            if chapter_deferred:
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code=self.code_deferred,
                        severity="critical",
                        chapter_no=chapter_number,
                        detail=(
                            f"前 {window} 章禁用术语在第 {chapter_number} 章"
                            f"出现 {len(chapter_deferred)} 个：{', '.join(chapter_deferred)}。"
                            "应推迟到稍后章节首次出现。"
                        ),
                        auto_repairable=False,
                    )
                )

            if chapter_number == 1:
                limit = ch1_max
            elif chapter_number == 2:
                limit = ch2_new_max
            else:
                limit = ch3_new_max

            if len(chapter_kept_new) > limit:
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code=self.code_overload,
                        severity="critical",
                        chapter_no=chapter_number,
                        detail=(
                            f"第 {chapter_number} 章首次出现私设术语 "
                            f"{len(chapter_kept_new)} 个，超出预算 {limit}。"
                            f"出现的术语：{', '.join(chapter_kept_new)}。"
                        ),
                        auto_repairable=False,
                    )
                )

            seen_kept.update(chapter_kept_new)

        return findings

    async def repair(self, session: AsyncSession, finding: AuditFinding) -> RepairResult:
        # Term overload is never auto-repairable — the rewrite requires
        # coordinated content + plot decisions that only the editor / planner
        # role can make.
        return RepairResult(
            success=False,
            description=(
                "OpeningTerminologyAudit findings require manual rewrite via "
                "the editor pipeline; auto-repair is not supported."
            ),
        )


# ---------------------------------------------------------------------------
# ProtagonistNameDriftAudit — F10: enforce ProjectInvariants.protagonist_name.
# Catches metadata fields and chapter titles that reference a different
# given name in the same surname family (e.g. dramatic_question saying
# "林逸" while invariants and prose use "林渊").
# ---------------------------------------------------------------------------


class ProtagonistNameDriftAudit:
    """Detect literal protagonist-name drift via an explicit forbidden list.

    Auto-detecting name drift via "surname + N CJK chars" yields too many
    false positives in Chinese where the surname commonly precedes verbs
    (e.g. ``林渊能否`` matches "林渊能" → false positive). Instead, this
    audit consumes an *explicit* list of forbidden alias names from
    ``project.metadata_json.protagonist_forbidden_names`` (or, when absent,
    no findings are produced).

    Suggested workflow: when an author renames a protagonist mid-project,
    add the old name to ``protagonist_forbidden_names`` and this audit
    will surface every metadata field that still mentions the old name.

    Locked name source order (first match wins):
      1. ``ProjectInvariants.protagonist_name``
      2. ``project.metadata_json.protagonist_name``
    """

    name = "ProtagonistNameDriftAudit"
    code_drift = "PROTAGONIST_NAME_DRIFT"

    _METADATA_TEXT_KEYS: tuple[str, ...] = (
        "logline",
        "synopsis",
        "premise",
        "reader_promise",
        "opening_strategy",
        "world_premise",
    )

    async def scan(
        self, session: AsyncSession, project_id: UUID
    ) -> list[AuditFinding]:
        project = (
            await session.scalars(
                select(ProjectModel).where(ProjectModel.id == project_id)
            )
        ).first()
        if project is None:
            return []

        try:
            invariants = (
                invariants_from_dict(project.invariants_json)
                if project.invariants_json
                else None
            )
        except InvariantSeedError:
            invariants = None
        metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}

        locked_name: str | None = (
            invariants.protagonist_name if invariants is not None else None
        )
        if not locked_name:
            locked_name = metadata.get("protagonist_name")
        if not isinstance(locked_name, str):
            return []
        locked_name = locked_name.strip()
        if not locked_name:
            return []

        forbidden_raw = metadata.get("protagonist_forbidden_names") or ()
        forbidden: tuple[str, ...] = tuple(
            str(n).strip()
            for n in forbidden_raw
            if isinstance(n, str) and str(n).strip() and str(n).strip() != locked_name
        )
        if not forbidden:
            return []

        scanned: list[tuple[str, str]] = [
            ("title", project.title or ""),
            ("theme_statement", project.theme_statement or ""),
            ("dramatic_question", project.dramatic_question or ""),
        ]
        for key in self._METADATA_TEXT_KEYS:
            value = metadata.get(key)
            if isinstance(value, str):
                scanned.append((f"metadata.{key}", value))

        findings: list[AuditFinding] = []
        for field_name, text in scanned:
            if not text:
                continue
            hits = [name for name in forbidden if name in text]
            if hits:
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code=self.code_drift,
                        severity="critical",
                        chapter_no=None,
                        detail=(
                            f"主角姓名锁定为「{locked_name}」，"
                            f"但字段 {field_name} 出现禁用别名：{', '.join(sorted(set(hits)))}。"
                            "请统一为锁定名。"
                        ),
                        auto_repairable=False,
                    )
                )

        return findings

    async def repair(self, session: AsyncSession, finding: AuditFinding) -> RepairResult:
        return RepairResult(
            success=False,
            description=(
                "ProtagonistNameDriftAudit findings require explicit confirmation; "
                "use an editor pass or direct metadata UPDATE to reconcile."
            ),
        )


# ---------------------------------------------------------------------------
# ChapterSignatureAudit — F12: detect chapters lacking any "signature 段"
# (a memorable, quotable passage). Heuristic counterpart to
# ``config/chapter_signature_audit.yaml``.
#
# This is the audit that would have caught the 2026-05 Ch1 rewrite mistake
# (replaced iconic visuals with prose that passed the qimao opening gate
# but contained zero quotable moments). Now any chapter shipped without
# a signature 段 surfaces a finding so the editor can re-inject one.
# ---------------------------------------------------------------------------


class ChapterSignatureAudit:
    """Detect chapters with zero "signature 段" (memorable quotable passage).

    The audit scans each chapter's current draft and counts paragraphs that
    look like one of the six signature types from
    ``chapter_signature_audit.yaml``. The detection is heuristic — false
    negatives are preferred over false positives:

    * **golden_line** — a short standalone paragraph (4-30 CJK chars), not
      pure dialogue, containing declarative force (是/不是, 必须/不能,
      要/不能/只), a sharp metaphor anchor (像/如), or terminal punctuation
      that lands a beat.
    * **micro_detail_punch** — a short paragraph (5-40 chars) anchored on a
      concrete physical detail with specific numeric / directional /
      material vocabulary (寸/角/缺/三/纹/钱/血/铜/灰...).
    * **scene_climax_moment / surgical_description** — a paragraph
      100-300 chars that combines ≥ 2 sensory channels (sound + sight,
      touch + temperature, etc.) and ≥ 1 specific object reference.
    * **twist_with_foreshadow_landing** — a paragraph with the pattern
      "不是 X，是 Y" or "X——和 Y 一模一样" (recognition of a hidden link).

    A chapter passes if ≥ 1 paragraph hits any rule.

    Emits ``SIGNATURE_SEGMENT_MISSING`` (severity=warn) when zero hits.
    Severity stays warn rather than critical because the detection is
    fuzzy; the chapter pipeline surfaces it as an editorial hint, not a
    hard block. A chronic violation (5+ consecutive chapters with zero
    signature) escalates to ``SIGNATURE_SEGMENT_CHRONIC`` at critical.
    """

    name = "ChapterSignatureAudit"
    code_missing = "SIGNATURE_SEGMENT_MISSING"
    code_chronic = "SIGNATURE_SEGMENT_CHRONIC"

    CHRONIC_WINDOW = 5

    _METAPHOR_MARKERS: tuple[str, ...] = ("像", "如同", "似的", "仿佛", "犹如")
    # Twist comparative anchors: "X——和 Y 一模一样" type reveal.
    _TWIST_ANCHORS: tuple[str, ...] = (
        "一模一样", "同源", "正是", "竟是", "原来是",
    )
    # Numeric specificity — a concrete physical count is a strong signature
    # signal (五个小孔, 七张脸, 三圈灰线, 十根手指 等). One Chinese numeral
    # alone is not enough; it must be followed by a measure word + body /
    # object marker within a short distance.
    _NUMERIC_PATTERN = re.compile(
        r"[一二三四五六七八九十百千两]"
        r"[只个根道圈条颗张片块面把片粒堆口寸尺米]"
        r"[一-鿿]{0,6}"
        r"(?:孔|脸|线|手|指|脚|目|眼|耳|齿|发|血|"
        r"骨|皮|肉|心|脏|眶|颗|头|身|步|滴|缕|节)?"
    )
    # Physical anomaly markers — strong signal that something concrete and
    # unusual is happening on the body / object.
    _ANOMALY_MARKERS: tuple[str, ...] = (
        "剜", "缺", "裂", "孔", "痕", "印", "疤", "锈", "蜕", "枯",
        "皱", "翻", "崩", "塌", "碎",
    )
    # A verdict-style golden line is a short standalone paragraph that
    # states a PROPOSITION rather than an action. Three positive markers:
    #   • Proposition pattern: "X 是 Y" / "X 不是 Y" / "不是 X，是 Y"
    #   • Negation rule: "X 不/没 Y"
    #   • Bare noun statement (no agent verb)
    _VERDICT_PROP_PATTERNS: tuple[re.Pattern, ...] = (
        # 不是X，是Y / X不是Y / X是Y（命题）
        re.compile(r"^[一-鿿]{1,8}(?:不是|是)[一-鿿]{1,12}[。！]?$"),
        re.compile(r"^不是[一-鿿]{1,10}[，,。][\s]*是[一-鿿]{1,10}[。！]?$"),
        # "X的Y，不是Z的" / "X不Y" / "X 才 Y"
        re.compile(r"^[一-鿿]{1,15}(?:才|只|不能|必须|得)[一-鿿]{1,10}[。！]?$"),
    )
    # Agent-verb patterns to EXCLUDE (these are action sentences, not
    # verdicts).
    _AGENT_ACTION_RE = re.compile(
        r"^(?:他|她|它|我|你|林渊|王老板|孙九斤|苏婉宁|陈默|小雨|周雪|老张|张建军)"
        r"(?:[一-鿿]{0,4}着|[一-鿿]{0,2}了)?"
    )

    @classmethod
    def _has_any(cls, text: str, markers: tuple[str, ...]) -> bool:
        return any(m in text for m in markers)

    @classmethod
    def _is_dialogue(cls, paragraph: str) -> bool:
        stripped = paragraph.strip()
        for opener, closer in (("“", "”"), ("「", "」"), ('"', '"')):
            if stripped.startswith(opener) and stripped.endswith(closer):
                return True
        return False

    @classmethod
    def _paragraph_signature_kind(cls, paragraph: str) -> str | None:
        """Return signature type id (if any) the paragraph likely satisfies.

        Detection is conservative: a paragraph must show a STRONG visual /
        verdict / twist anchor — generic concrete-object naming alone does
        not qualify. Empirically calibrated against the 青囊 Ch2 (4
        signatures expected) vs a stripped-narration baseline (0 expected).
        """
        text = paragraph.strip()
        if not text:
            return None
        cjk_len = sum(1 for c in text if "一" <= c <= "鿿")
        if cjk_len < 4:
            return None

        # twist_with_foreshadow_landing: strongest signal — recognition of
        # a hidden link via a comparative anchor.
        if cls._has_any(text, cls._TWIST_ANCHORS):
            return "twist_with_foreshadow_landing"

        # micro_detail_punch: numeric specificity + body/anomaly anchor.
        if cls._NUMERIC_PATTERN.search(text) and cls._has_any(
            text, cls._ANOMALY_MARKERS + ("孔", "脸", "线", "手", "指")
        ):
            return "micro_detail_punch"

        # golden_line: short paragraph (≤ 25 chars), not dialogue, NOT a
        # simple agent-verb action, and matching at least one of:
        #   (a) a proposition pattern (X 是/不是 Y, 不是 X，是 Y, 只/必须...)
        #   (b) a metaphor anchor (像/如同/仿佛)
        if 4 <= cjk_len <= 25 and not cls._is_dialogue(text):
            # Reject narrative action sentences ("他把铜钱压在卷上。").
            if cls._AGENT_ACTION_RE.match(text):
                return None
            for pat in cls._VERDICT_PROP_PATTERNS:
                if pat.match(text):
                    return "golden_line"
            if cls._has_any(text, cls._METAPHOR_MARKERS):
                return "golden_line"

        return None

    @classmethod
    def count_signatures(cls, content_md: str) -> dict[str, int]:
        """Return a histogram of detected signature types in a chapter."""
        histogram: dict[str, int] = {}
        for paragraph in (content_md or "").split("\n"):
            kind = cls._paragraph_signature_kind(paragraph)
            if kind is not None:
                histogram[kind] = histogram.get(kind, 0) + 1
        return histogram

    async def scan(
        self, session: AsyncSession, project_id: UUID
    ) -> list[AuditFinding]:
        rows = (
            await session.execute(
                select(
                    ChapterModel.chapter_number,
                    ChapterDraftVersionModel.content_md,
                )
                .join(
                    ChapterDraftVersionModel,
                    ChapterDraftVersionModel.chapter_id == ChapterModel.id,
                )
                .where(
                    ChapterModel.project_id == project_id,
                    ChapterDraftVersionModel.is_current.is_(True),
                )
                .order_by(ChapterModel.chapter_number.asc())
            )
        ).all()
        if not rows:
            return []

        findings: list[AuditFinding] = []
        consecutive_zero: list[int] = []

        for chapter_number, content in rows:
            histogram = self.count_signatures(content or "")
            total = sum(histogram.values())

            if total == 0:
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code=self.code_missing,
                        severity="warn",
                        chapter_no=chapter_number,
                        detail=(
                            f"第 {chapter_number} 章未检测到截图段（golden_line / "
                            "micro_detail_punch / surgical_description / "
                            "twist_with_foreshadow_landing 任一种）。"
                            "建议在 60-80% 位置注入 1 段值得读者摘抄的金句或神描写。"
                        ),
                        auto_repairable=False,
                    )
                )
                consecutive_zero.append(chapter_number)
                if len(consecutive_zero) >= self.CHRONIC_WINDOW:
                    findings.append(
                        AuditFinding(
                            auditor=self.name,
                            code=self.code_chronic,
                            severity="critical",
                            chapter_no=chapter_number,
                            detail=(
                                f"连续 {len(consecutive_zero)} 章无截图段："
                                f"{consecutive_zero}。书的'记忆点密度'已跌至危险线，"
                                "必须为这批章节集中补充金句或神描写。"
                            ),
                            auto_repairable=False,
                        )
                    )
            else:
                consecutive_zero.clear()

        return findings

    async def repair(self, session: AsyncSession, finding: AuditFinding) -> RepairResult:
        return RepairResult(
            success=False,
            description=(
                "ChapterSignatureAudit findings require editorial re-injection "
                "of a signature 段; auto-repair would dilute prose voice."
            ),
        )


class ContinuousAudit:
    """Runs every registered auditor against a project and aggregates findings."""

    def __init__(self, auditors: Iterable[Auditor]) -> None:
        self.auditors = list(auditors)

    async def scan(
        self, session: AsyncSession, project_id: UUID
    ) -> AuditReport:
        findings: list[AuditFinding] = []
        for auditor in self.auditors:
            findings.extend(await auditor.scan(session, project_id))
        return AuditReport(project_id=project_id, findings=tuple(findings))


def build_phase1_audit() -> ContinuousAudit:
    """Factory: Phase 1 default — gap + L4 length/language only.

    Kept for tests and for the CI gate where we want near-zero false
    positives. ``GapRepairer`` catches numbering holes; ``ContentAuditor``
    configured with ``validator_profile="phase1"`` replays only the L4
    length / language checks against the current draft versions so
    pre-gate productions still surface findings with the old profile.
    """

    return ContinuousAudit(
        [GapRepairer(), ContentAuditor(validator_profile="phase1")]
    )


def build_per_chapter_audit() -> ContinuousAudit:
    """Factory: lightweight per-chapter audit for the pipeline's chapter loop.

    Runs *only* the book-level pleasure/setup-payoff auditors so per-chapter
    review surfaces the aggregate "hype gap" / "face-slap debt" signals
    without replaying the full L4 stack (which already ran inline in
    ``assemble_chapter_draft``). Callers filter the returned findings by
    ``chapter_no`` so only current-chapter signals get persisted.

    Expected latency: +2-5s per chapter. Failure is non-fatal at the call
    site (pipelines.py wraps the invocation in try/except so the chapter
    still advances).
    """

    return ContinuousAudit(
        [
            PleasureDistributionAudit(),
            SetupPayoffTrackerAudit(),
        ]
    )


def build_full_audit() -> ContinuousAudit:
    """Factory: full L4 + L5 retrospective audit + hype distribution audit.

    Adds naming consistency, opening entity density, dialog integrity, POV
    lock, per-chapter cliffhanger rotation, the Phase 2 hype distribution
    audit, and the Phase 3 setup-payoff tracker on top of the Phase 1
    subset. This is what the offline CLI should use so that "all novels
    pass through the latest framework capabilities" (user directive
    2026-04-22) actually runs the full validator stack.
    ``PleasureDistributionAudit`` no-ops on projects with an empty
    ``hype_scheme`` (legacy pre-0019 projects) so it's safe to include
    unconditionally; ``SetupPayoffTrackerAudit`` uses a text-only
    humiliation scan so it still fires on legacy projects without hype
    metadata — its window falls back to 5 chapters when no scheme is set.
    """

    return ContinuousAudit(
        [
            GapRepairer(),
            ContentAuditor(validator_profile="full"),
            PleasureDistributionAudit(),
            SetupPayoffTrackerAudit(),
            OpeningTerminologyAudit(),
            ProtagonistNameDriftAudit(),
            ChapterSignatureAudit(),
        ]
    )


# ---------------------------------------------------------------------------
# Persistence helpers — called by pipeline Stage 10 and the offline CLI.
# ---------------------------------------------------------------------------


async def persist_audit_findings(
    session: AsyncSession, report: AuditReport
) -> int:
    """Insert every finding in ``report`` as a ``ChapterAuditFindingModel`` row.

    Returns the number of rows added. Phase 1 keeps this append-only — no
    deduplication against earlier runs. The L8 scorecard groups by
    ``(project_id, code, created_at::date)`` when charting trends, so
    duplicate rows across runs are fine and actually useful for trending.
    """

    if not report.findings:
        return 0

    for finding in report.findings:
        row = ChapterAuditFindingModel(
            project_id=report.project_id,
            chapter_no=finding.chapter_no,
            auditor=finding.auditor,
            code=finding.code,
            severity=finding.severity,
            detail=finding.detail,
            auto_repairable=finding.auto_repairable,
        )
        session.add(row)
    await session.flush()
    return len(report.findings)


async def run_and_persist_audit(
    session: AsyncSession,
    project_id: UUID,
    audit: ContinuousAudit | None = None,
    chapter_number: int | None = None,
) -> AuditReport:
    """One-shot helper: scan, persist findings, return the report.

    Pipeline Stage 10 and the offline CLI use this so the caller doesn't
    have to know about the `ChapterAuditFindingModel` schema.

    When ``chapter_number`` is provided, findings are filtered down to
    those whose ``chapter_no`` matches (or is ``None`` — i.e. book-level
    findings always pass through so gap-detection still surfaces). The
    per-chapter path in pipelines.py uses this to avoid re-persisting
    findings on earlier chapters every time a new chapter lands.
    """

    runner = audit or build_phase1_audit()
    report = await runner.scan(session, project_id)
    if chapter_number is not None:
        filtered = tuple(
            f
            for f in report.findings
            if f.chapter_no is None or f.chapter_no == chapter_number
        )
        report = AuditReport(project_id=project_id, findings=filtered)
    await persist_audit_findings(session, report)
    return report


async def spawn_rewrite_tasks_from_findings(
    session: AsyncSession,
    report: AuditReport,
) -> int:
    """Convert actionable audit findings into ``RewriteTaskModel`` rows.

    Currently targets ``PLEASURE_SETUP_PAYOFF_DEBT`` only — unpaid
    humiliation setups get a pending rewrite task pinned to the setup
    chapter so the review loop (or a later batch worker) can
    automatically compensate with a counterattack beat. Other finding
    codes are left untouched: resurrection/stance_flip are blocked at
    L2 earlier in the pipeline, and hype gaps are book-level signals
    without a single "rewrite this chapter" target.

    Returns the number of tasks created so callers can log it.
    """

    created = 0
    for finding in report.findings:
        if finding.code != "PLEASURE_SETUP_PAYOFF_DEBT":
            continue
        if finding.chapter_no is None:
            continue
        # Dedupe: skip if an open setup-payoff task for this chapter
        # already exists (avoid spamming on every per-chapter audit).
        existing = (
            await session.scalars(
                select(RewriteTaskModel.id).where(
                    RewriteTaskModel.project_id == report.project_id,
                    RewriteTaskModel.trigger_type == "setup_payoff_debt",
                    RewriteTaskModel.status.in_(("pending", "queued")),
                    RewriteTaskModel.metadata_json["setup_chapter"].astext
                    == str(finding.chapter_no),
                )
            )
        ).first()
        if existing is not None:
            continue
        session.add(
            RewriteTaskModel(
                project_id=report.project_id,
                trigger_type="setup_payoff_debt",
                rewrite_strategy="inject_counterattack_payoff",
                priority=4,
                status="pending",
                instructions=(
                    "检测到角色羞辱/压制设定尚未在窗口内偿还。请在后续章节"
                    "安排 COUNTERATTACK / FACE_SLAP / REVENGE_CLOSURE / "
                    "UNDERDOG_WIN 类爽点予以回应，具体章节与情节自行裁定。"
                    f"\n\n触发细节：{finding.detail}"
                ),
                context_required=[
                    "chapter_context",
                    "setup_chapter_content",
                    "hype_scheme",
                ],
                metadata_json={
                    "setup_chapter": finding.chapter_no,
                    "audit_code": finding.code,
                    "auditor": finding.auditor,
                },
            )
        )
        created += 1
    if created:
        await session.flush()
    return created
