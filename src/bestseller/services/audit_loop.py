"""L7 Continuous Audit Loop.

Post-generation sweep that detects and optionally repairs residual quality
issues that slipped past the write-gate — either because the check is too
expensive to run per-chapter, because the issue only manifests across
multiple chapters, or because the project was produced before L1-L6 were in
place. Phase 1 ships only ``GapRepairer`` (chapter-sequence holes); later
phases absorb ``scripts/`` one auditor at a time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Iterable, Literal, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterAuditFindingModel,
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    ProjectModel,
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
from bestseller.services.setup_payoff_tracker import (
    DEFAULT_HUMILIATION_KEYWORDS,
    DEFAULT_PAYOFF_HYPE_TYPES,
    analyze_setup_payoff,
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
        per_chapter = target_words_total // target_chapters if target_words_total else 6400
        words = SimpleNamespace(
            min=int(per_chapter * 0.7) or 2000,
            target=per_chapter or 6400,
            max=int(per_chapter * 1.3) or 8000,
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

        rows = await session.scalars(
            select(CharacterModel.name).where(CharacterModel.project_id == project_id)
        )
        return frozenset(name.strip() for name in rows if name and name.strip())

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
                tuple(cliff_history[-cliff_window:]) if cliff_window else tuple()
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
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code=self.code_comedic,
                        severity="warn",
                        chapter_no=None,
                        detail=(
                            f"Comedic beat density {observed:.2%} below "
                            f"target {target:.2%} by more than "
                            f"{self.starvation_slack:.0%} slack "
                            f"({comedic_chapters}/{total_chapters} chapters)"
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
) -> AuditReport:
    """One-shot helper: scan, persist findings, return the report.

    Pipeline Stage 10 and the offline CLI use this so the caller doesn't
    have to know about the `ChapterAuditFindingModel` schema.
    """

    runner = audit or build_phase1_audit()
    report = await runner.scan(session, project_id)
    await persist_audit_findings(session, report)
    return report
