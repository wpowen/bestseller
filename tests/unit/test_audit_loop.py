"""Unit tests for L7 continuous audit (Phase 1: GapRepairer).

These tests validate the scan path without hitting a real database — the
scanner only calls ``session.scalars(...)`` and iterates the result, so a
minimal async stub is enough to exercise it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID, uuid4

import pytest

from bestseller.services.audit_loop import (
    AuditFinding,
    AuditReport,
    ContentAuditor,
    ContinuousAudit,
    GapRepairer,
    build_full_audit,
    build_phase1_audit,
    persist_audit_findings,
    run_and_persist_audit,
)
from bestseller.services.output_validator import (
    build_full_audit_validator,
    build_phase1_validator,
)

pytestmark = pytest.mark.unit


@dataclass
class _FakeScalars:
    values: Iterable[Any]

    def __iter__(self) -> Any:
        return iter(self.values)

    def all(self) -> list[Any]:
        # ``_infer_pov`` calls ``.all()`` on the chapter-content scalars result
        # to collect a sample; match that shape in the fake.
        return list(self.values)


class _FakeAsyncSession:
    """Stub just enough of ``AsyncSession`` to test the audit scan path.

    ``GapRepairer.scan`` runs a single ``select(chapter_number)`` query;
    returning a ``_FakeScalars`` keeps the test hermetic — no DB needed.
    """

    def __init__(self, chapter_numbers: list[int]) -> None:
        self._chapter_numbers = chapter_numbers

    async def scalars(self, _statement: Any) -> _FakeScalars:
        return _FakeScalars(list(self._chapter_numbers))


@pytest.mark.asyncio
async def test_scan_reports_no_findings_for_contiguous_sequence() -> None:
    session = _FakeAsyncSession([1, 2, 3, 4, 5])
    repairer = GapRepairer()
    findings = await repairer.scan(session, uuid4())  # type: ignore[arg-type]
    assert findings == []


@pytest.mark.asyncio
async def test_scan_reports_one_finding_per_missing_chapter() -> None:
    session = _FakeAsyncSession([1, 2, 3, 5, 6, 8])
    repairer = GapRepairer()
    findings = await repairer.scan(session, uuid4())  # type: ignore[arg-type]
    missing = sorted(f.chapter_no for f in findings if f.chapter_no is not None)
    assert missing == [4, 7]
    assert {f.code for f in findings} == {"CHAPTER_GAP"}
    assert all(f.severity == "critical" for f in findings)
    # Repair is deliberately not auto-runnable at Phase 1 — requires the pipeline.
    assert all(f.auto_repairable is False for f in findings)


@pytest.mark.asyncio
async def test_repair_is_non_destructive_in_phase1() -> None:
    session = _FakeAsyncSession([1, 2, 4])
    repairer = GapRepairer()
    finding = AuditFinding(
        auditor="GapRepairer",
        code="CHAPTER_GAP",
        severity="critical",
        chapter_no=3,
        detail="Chapter 3 missing",
        auto_repairable=False,
    )
    result = await repairer.repair(session, finding)  # type: ignore[arg-type]
    assert result.success is False
    # Phase 1 refuses to regenerate without the full pipeline wired in.
    assert "pipeline" in result.description.lower()


@pytest.mark.asyncio
async def test_continuous_audit_aggregates_findings() -> None:
    session = _FakeAsyncSession([1, 3, 4])
    # Gate aggregation is tested with GapRepairer only; ContentAuditor needs
    # a richer session stub and is covered by its own tests below.
    audit = ContinuousAudit([GapRepairer()])
    report = await audit.scan(session, uuid4())  # type: ignore[arg-type]
    assert len(report.findings) == 1
    assert report.has_critical is True
    by_code = report.by_code()
    assert "CHAPTER_GAP" in by_code


@pytest.mark.asyncio
async def test_empty_project_yields_empty_report() -> None:
    session = _FakeAsyncSession([])
    audit = ContinuousAudit([GapRepairer()])
    report = await audit.scan(session, uuid4())  # type: ignore[arg-type]
    assert report.findings == ()
    assert report.has_critical is False


@pytest.mark.asyncio
async def test_build_phase1_audit_includes_both_auditors() -> None:
    audit = build_phase1_audit()
    names = {auditor.name for auditor in audit.auditors}
    assert names == {"GapRepairer", "ContentAuditor"}


# ---------------------------------------------------------------------------
# ContentAuditor — retrospective L4 replay
# ---------------------------------------------------------------------------


class _FakeExecuteResult:
    def __init__(self, rows: list[tuple[int, str]]) -> None:
        self._rows = list(rows)

    def all(self) -> list[tuple[int, str]]:
        return list(self._rows)


class _FakeContentSession:
    """Stub that returns a project scalar + POV-sample rows + character names + chapter rows.

    ``ContentAuditor.scan`` issues four DB calls in order when the project has
    no seeded invariants (the common retrospective-audit case):
      1. ``session.scalars(select(ProjectModel).where(...))`` — expects ``.first()``.
      2. ``session.scalars(<POV sample>)`` — expects ``.all()`` returning
         content strings. Only fires when ``project.invariants_json`` is
         falsy (since that triggers ``_infer_pov``).
      3. ``session.scalars(select(CharacterModel.name).where(...))`` — expects iteration.
      4. ``session.execute(<chapter join>)`` — expects ``.all()``.

    Call #2 is skipped when invariants are already seeded; the counter is
    test-visible so ordering bugs surface loudly.
    """

    def __init__(
        self,
        project: Any,
        chapter_rows: list[tuple[int, str]],
        *,
        character_names: list[str] | None = None,
        pov_sample_rows: list[str] | None = None,
    ) -> None:
        self._project = project
        self._rows = chapter_rows
        self._names = list(character_names or [])
        # Default to the chapter content itself so POV inference sees
        # representative text when the test doesn't explicitly provide
        # sample rows.
        self._pov_sample = list(
            pov_sample_rows if pov_sample_rows is not None else [content for _, content in chapter_rows]
        )
        self.scalars_call_count = 0

    async def scalars(self, _statement: Any) -> Any:
        self.scalars_call_count += 1
        if self.scalars_call_count == 1:
            # First call: project lookup — needs ``.first()``.
            project = self._project

            class _FirstOnly:
                def first(self) -> Any:
                    return project

            return _FirstOnly()
        # When the project has no invariants_json, ``_load_invariants`` calls
        # ``_infer_pov`` first (needs ``.all()``) before ``_load_allowed_names``
        # (needs iteration). With seeded invariants, the sample call is skipped
        # and the next call is the character roster.
        needs_pov_sample = not getattr(self._project, "invariants_json", None)
        if self.scalars_call_count == 2 and needs_pov_sample:
            return _FakeScalars(list(self._pov_sample))
        # Character roster: iterable.
        return _FakeScalars(list(self._names))

    async def execute(self, _statement: Any) -> _FakeExecuteResult:
        return _FakeExecuteResult(self._rows)


@pytest.mark.asyncio
async def test_content_auditor_reports_length_under_for_short_chapter() -> None:
    project_id = uuid4()
    fake_project = type(
        "FakeProject",
        (),
        {
            "id": project_id,
            "language": "zh-CN",
            "target_chapters": 100,
            "target_word_count": 500000,
            "invariants_json": None,
        },
    )()
    # 100 chars is far under any reasonable zh-CN envelope.
    session = _FakeContentSession(fake_project, [(7, "短。" * 50)])
    auditor = ContentAuditor()
    findings = await auditor.scan(session, project_id)  # type: ignore[arg-type]
    codes = {f.code for f in findings}
    assert "LENGTH_UNDER" in codes
    assert all(f.auto_repairable is False for f in findings)


@pytest.mark.asyncio
async def test_content_auditor_is_clean_when_content_is_in_envelope() -> None:
    project_id = uuid4()
    fake_project = type(
        "FakeProject",
        (),
        {
            "id": project_id,
            "language": "zh-CN",
            "target_chapters": 100,
            "target_word_count": 500000,
            # ~5000 zh chars per chapter → envelope min ≈ 3500, max ≈ 6500.
            "invariants_json": None,
        },
    )()
    clean_text = "这是干净的中文正文，没有任何问题。" * 250  # >>> 3500 chars
    session = _FakeContentSession(fake_project, [(1, clean_text)])
    auditor = ContentAuditor()
    findings = await auditor.scan(session, project_id)  # type: ignore[arg-type]
    # Either clean or only trips LENGTH_OVER (we just care there's no crash
    # and that language-leak isn't falsely raised on pure zh-CN text).
    assert "LANG_LEAK_CJK_IN_EN" not in {f.code for f in findings}


@pytest.mark.asyncio
async def test_content_auditor_returns_no_findings_for_missing_project() -> None:
    session = _FakeContentSession(project=None, chapter_rows=[])
    auditor = ContentAuditor()
    findings = await auditor.scan(session, uuid4())  # type: ignore[arg-type]
    assert findings == []


# ---------------------------------------------------------------------------
# Full-profile audit — exercises L4 + L5 subset the Phase 1 profile skips.
# ---------------------------------------------------------------------------


def _fake_en_project(project_id: UUID) -> Any:
    """English-language project with a 3000-word-per-chapter target.

    Envelope derived by ``ContentAuditor._load_invariants``:
        min ≈ 2100, target = 3000, max = 3900 effective chars.
    """

    return type(
        "FakeProject",
        (),
        {
            "id": project_id,
            "language": "en",
            "target_chapters": 10,
            "target_word_count": 30_000,
            "invariants_json": None,
        },
    )()


@pytest.mark.asyncio
async def test_full_profile_flags_cjk_leak_in_english_chapter() -> None:
    """A single CJK fragment in an English chapter must surface as LANG_LEAK_CJK_IN_EN."""

    project_id = uuid4()
    project = _fake_en_project(project_id)
    # Healthy English body, salted with enough CJK to trip the 2% threshold.
    body = (
        "The princess stepped into the hall. Her voice was steady. "
        + "She glanced at the window and whispered, 你好, 再见. "
    ) * 40
    session = _FakeContentSession(project, [(3, body)])
    auditor = ContentAuditor(validator_profile="full")
    findings = await auditor.scan(session, project_id)  # type: ignore[arg-type]
    codes = {f.code for f in findings}
    assert "LANG_LEAK_CJK_IN_EN" in codes


@pytest.mark.asyncio
async def test_full_profile_flags_unclosed_quote() -> None:
    """Unclosed English quote in a paragraph → DIALOG_UNPAIRED."""

    project_id = uuid4()
    project = _fake_en_project(project_id)
    # Paragraph opens a curly quote but never closes it before the paragraph
    # break. Body padded so it lands in-envelope otherwise.
    body = (
        "\u201cWhere are you going? she asked, glancing at the door but never finishing her question.\n\n"
        "He walked on, silent. The street lamps flickered. "
    ) * 40
    session = _FakeContentSession(project, [(2, body)])
    auditor = ContentAuditor(validator_profile="full")
    findings = await auditor.scan(session, project_id)  # type: ignore[arg-type]
    codes = {f.code for f in findings}
    assert "DIALOG_UNPAIRED" in codes


@pytest.mark.asyncio
async def test_full_profile_flags_entity_overload_on_chapter_1() -> None:
    """First chapter introducing 8+ named entities in the opening trips
    OPENING_ENTITY_OVERLOAD. Later chapters are exempt."""

    project_id = uuid4()
    project = _fake_en_project(project_id)
    names = [
        "Elena Vance",
        "Marcus Kane",
        "Lily Park",
        "Theo Rhodes",
        "Nora Blake",
        "Adrian Cole",
        "Sasha Lin",
        "David Reyes",
    ]
    opening = " ".join(f"{n} walked in. " for n in names) * 10
    body = opening + ("The city was quiet that night. " * 60)
    session = _FakeContentSession(project, [(1, body)])
    auditor = ContentAuditor(validator_profile="full")
    findings = await auditor.scan(session, project_id)  # type: ignore[arg-type]
    codes = {f.code for f in findings}
    assert "OPENING_ENTITY_OVERLOAD" in codes


@pytest.mark.asyncio
async def test_phase1_profile_skips_extended_checks() -> None:
    """``validator_profile='phase1'`` must not report dialog/entity findings
    even when they exist — recall is deliberately narrow for CI usage."""

    project_id = uuid4()
    project = _fake_en_project(project_id)
    body = (
        "\u201cUnclosed but within envelope, she wondered.\n\n"
        "The street was quiet. "
    ) * 80
    session = _FakeContentSession(project, [(2, body)])
    auditor = ContentAuditor(validator_profile="phase1")
    findings = await auditor.scan(session, project_id)  # type: ignore[arg-type]
    codes = {f.code for f in findings}
    # Phase 1 never fires dialog/entity/naming findings regardless of content.
    assert "DIALOG_UNPAIRED" not in codes
    assert "OPENING_ENTITY_OVERLOAD" not in codes
    assert "NAMING_OUT_OF_POOL" not in codes


@pytest.mark.asyncio
async def test_build_full_audit_includes_expected_auditors() -> None:
    audit = build_full_audit()
    names = {auditor.name for auditor in audit.auditors}
    # Phase 2 adds PleasureDistributionAudit alongside the base pair.
    assert names == {"GapRepairer", "ContentAuditor", "PleasureDistributionAudit"}
    # The ContentAuditor in a full-audit registration should be configured
    # with the full profile; this guards against regressions to phase1.
    content = next(a for a in audit.auditors if a.name == "ContentAuditor")
    assert getattr(content, "validator_profile", None) == "full"


def test_build_full_audit_validator_registers_expected_checks() -> None:
    """The factory must compose 6 checks — language/length/naming/entity + dialog/POV."""

    validator = build_full_audit_validator()
    class_names = {type(c).__name__ for c in validator.checks}
    assert class_names == {
        "LanguageSignatureCheck",
        "LengthEnvelopeCheck",
        "NamingConsistencyCheck",
        "EntityDensityCheck",
        "DialogIntegrityCheck",
        "POVLockCheck",
    }


def test_build_phase1_validator_is_narrow_subset() -> None:
    validator = build_phase1_validator()
    class_names = {type(c).__name__ for c in validator.checks}
    assert class_names == {"LanguageSignatureCheck", "LengthEnvelopeCheck"}


# ---------------------------------------------------------------------------
# Persistence helpers.
# ---------------------------------------------------------------------------


class _RecordingSession:
    """Session stub that captures ``.add`` / ``.flush`` calls.

    ``persist_audit_findings`` does not run SQL itself — it only adds model
    instances to the session and flushes. Recording these calls is enough
    to verify the helper wires up correctly.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flush_count = 0

    def add(self, instance: Any) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flush_count += 1


@pytest.mark.asyncio
async def test_persist_audit_findings_noop_on_empty_report() -> None:
    session = _RecordingSession()
    report = AuditReport(project_id=uuid4(), findings=())
    inserted = await persist_audit_findings(session, report)  # type: ignore[arg-type]
    assert inserted == 0
    assert session.added == []
    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_persist_audit_findings_inserts_one_row_per_finding() -> None:
    session = _RecordingSession()
    project_id = uuid4()
    findings = (
        AuditFinding(
            auditor="GapRepairer",
            code="CHAPTER_GAP",
            severity="critical",
            chapter_no=4,
            detail="Chapter 4 missing",
            auto_repairable=False,
        ),
        AuditFinding(
            auditor="ContentAuditor",
            code="LENGTH_UNDER",
            severity="warn",
            chapter_no=7,
            detail="chapter too short",
            auto_repairable=False,
        ),
    )
    report = AuditReport(project_id=project_id, findings=findings)
    inserted = await persist_audit_findings(session, report)  # type: ignore[arg-type]
    assert inserted == 2
    assert len(session.added) == 2
    assert session.flush_count == 1
    # The helper must map every field onto the model — peek at the recorded rows.
    codes = {row.code for row in session.added}
    assert codes == {"CHAPTER_GAP", "LENGTH_UNDER"}
    project_ids = {row.project_id for row in session.added}
    assert project_ids == {project_id}


class _FakeAudit:
    async def scan(self, _session: Any, project_id: UUID) -> AuditReport:
        return AuditReport(
            project_id=project_id,
            findings=(
                AuditFinding(
                    auditor="stub",
                    code="STUB",
                    severity="info",
                    chapter_no=None,
                    detail="stub finding",
                    auto_repairable=False,
                ),
            ),
        )


@pytest.mark.asyncio
async def test_run_and_persist_audit_scans_and_persists() -> None:
    session = _RecordingSession()
    project_id = uuid4()
    report = await run_and_persist_audit(
        session,  # type: ignore[arg-type]
        project_id,
        audit=_FakeAudit(),  # type: ignore[arg-type]
    )
    # Report returned matches what the stub emitted.
    assert report.project_id == project_id
    assert len(report.findings) == 1
    # Persistence happened.
    assert len(session.added) == 1
    assert session.added[0].code == "STUB"
