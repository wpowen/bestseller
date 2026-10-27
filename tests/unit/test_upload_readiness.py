# ruff: noqa: ANN001, ANN002, ANN003, ANN201, ANN202
"""Fixture evidence only; readiness flags never constitute platform acceptance."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy.orm.attributes import flag_modified
from test_fanqie_segment_resume import sqlite_only as sqlite_only
from test_fanqie_source_export_integrity import (
    configured as configured,
)
from test_fanqie_source_export_integrity import (
    db as db,
)
from test_fanqie_source_export_integrity import (
    integrated_modules as integrated_modules,
)
from test_fanqie_source_export_integrity import (
    offline as offline,
)
from test_fanqie_source_export_integrity import (
    old_evidence,
    publish,
    seed_prior_outputs,
)
from test_fanqie_source_export_integrity import (
    source_case as source_case,
)

from bestseller.infra.db.models import RewriteTaskModel
from bestseller.services.book_delivery_readiness import read_book_delivery_closure
from bestseller.services.fanqie_short_export import build_signing_readiness_report

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def isolate_derived_bible_projection(monkeypatch):
    from bestseller.services import story_bible

    async def retained(session, settings, project_id, outcome):
        # Adoption/knowledge stays real; this upload test excludes the unrelated
        # PG advisory lock and embedding-backed derived Bible projection.
        assert session.bind.dialect.name == "sqlite"
        return outcome

    monkeypatch.setattr(story_bible, "_ensure_adopted_bible_read_views", retained)


async def set_target(case, target):
    case.project.target_word_count = target
    await case.session.commit()


async def evaluate(helper, case):
    return await helper.publish_adopted_assembly(
        case.session,
        project_id=case.project.id,
        output_dir=case.output,
        expected=await helper.read_adopted_assembly(case.session, case.project.id),
        unlock_line_ratio=0.3,
        protagonist_name="我",
        export_markdown=False,
    )


async def test_exact_10719_8000_package_retains_false(integrated_modules, tmp_path):
    from bestseller.services.drafts import count_words
    from bestseller.services.fanqie_short_export import export_fanqie_short_markdown

    helper, _, _ = integrated_modules
    # Synthetic arithmetic/package fixture; no original book or literary claim.
    body = "甲" * 10719
    assert count_words(body) == 10719
    manifest = {"project": {"title": "R19 arithmetic fixture", "target_word_count": 8000}}
    current = helper.AdoptedAssembly(body, helper._json(manifest))
    paths = export_fanqie_short_markdown(
        tmp_path,
        title=manifest["project"]["title"],
        genre="fixture",
        full_text=body,
        target_word_count=8000,
    )
    source = tmp_path / "exports/adopted-source.json"
    source.write_text(helper._json(manifest))
    paths["source_receipt_path"] = str(source.resolve())
    inputs = {"unlock_line_ratio": 0.3, "protagonist_name": None}
    verified = helper._verify_delivery_files(tmp_path, current, paths, readiness_inputs=inputs)
    assert verified["ready_for_upload"] is False
    report = json.loads(Path(paths["readiness_path"]).read_text())
    assert (report["total_words"], report["target_word_count"]) == (10719, 8000)
    assert abs(10719 - 8000) / 8000 > 0.15
    report.update(
        ready_for_upload=True,
        opening_gate_passed=True,
        ranking_gate_passed=True,
        short_v2_gate_passed=True,
        taboo_signals=[],
    )
    Path(paths["readiness_path"]).write_text(json.dumps(report))
    with pytest.raises(ValueError, match="readiness_mismatch"):
        helper._verify_delivery_files(tmp_path, current, paths, readiness_inputs=inputs)


async def test_delivery_complete_preserves_false_upload_readiness(integrated_modules, source_case):
    helper, _, _ = integrated_modules
    case = source_case
    # Same over-target failure class as 10719/8000; no prose/target repair.
    await set_target(case, 400)
    before = await old_evidence(case)
    prior = seed_prior_outputs(case.output)
    result = await publish(helper, case)
    report = json.loads(Path(result["export_paths"]["readiness_path"]).read_text())
    assert report["ready_for_upload"] is False
    assert report["target_word_count"] == 400
    assert result["whole_book_complete"] is True
    await case.session.refresh(case.project)
    assert case.project.status == "completed"
    assert case.project.target_word_count == 400
    assert case.project.metadata_json["fanqie_short_ready_for_upload"] is False
    assert result["ready_for_upload"] is False
    receipt = case.project.metadata_json["fanqie_whole_export_receipt"]
    assert receipt["whole_book_complete"] is True
    assert receipt["delivery_verification"]["ready_for_upload"] is False
    verification = await helper.read_fanqie_delivery_evidence(case.session, case.project.id)
    assert verification["ready_for_upload"] is False
    readback = await read_book_delivery_closure(case.session, case.project)
    assert readback.verdict.is_complete, readback.blockers
    retained = deepcopy(case.project.metadata_json)
    files = {key: Path(value).read_bytes() for key, value in result["export_paths"].items()}
    evaluation = await evaluate(helper, case)
    assert evaluation["whole_book_complete"] is True
    assert evaluation["ready_for_upload"] is False
    await case.session.refresh(case.project)
    assert case.project.metadata_json == retained
    assert files == {key: Path(value).read_bytes() for key, value in result["export_paths"].items()}
    assert await old_evidence(case) == before
    archive = Path(result["previous_outputs_archive"]["directory"]) / "files"
    assert all((archive / name).read_bytes() == data for name, data in prior.items())


async def test_metadata_matches_verified_package_even_with_pending_work(
    integrated_modules, source_case
):
    helper, _, _ = integrated_modules
    case = source_case
    # Establish the fixture's readiness with the actual pure rules first.
    report = build_signing_readiness_report(
        case.draft.content_md,
        title=case.project.title,
        protagonist_name="我",
        target_word_count=700,
    )
    assert report["ready_for_upload"] is True
    task = RewriteTaskModel(
        project_id=case.project.id,
        trigger_type="fanqie_ranking_gate",
        trigger_source_id=case.chapter.id,
        rewrite_strategy="fix_opening",
        status="pending",
        instructions="Retain pending source work",
    )
    case.session.add(task)
    await case.session.commit()
    result = await publish(helper, case)
    await case.session.refresh(case.project)
    assert result["whole_book_complete"] is False
    assert result["ready_for_upload"] is True
    assert case.project.metadata_json["fanqie_short_ready_for_upload"] is True
    assert case.project.status == "writing" and task.status == "pending"
    retained = deepcopy(case.project.metadata_json)
    files = {key: Path(value).read_bytes() for key, value in result["export_paths"].items()}
    evaluation = await evaluate(helper, case)
    assert evaluation["whole_book_complete"] is False
    assert evaluation["ready_for_upload"] is True
    await case.session.refresh(case.project)
    assert case.project.metadata_json == retained
    assert files == {key: Path(value).read_bytes() for key, value in result["export_paths"].items()}
    with pytest.raises(ValueError, match="pending_rewrite"):
        await helper.read_fanqie_delivery_evidence(case.session, case.project.id)


@pytest.mark.parametrize("target,expected", [(700, True), (400, False)])
async def test_readback_uses_package_not_metadata_flags(
    integrated_modules, source_case, target, expected
):
    helper, _, _ = integrated_modules
    case = source_case
    await set_target(case, target)
    await publish(helper, case)
    await case.session.refresh(case.project)
    case.project.metadata_json = {
        **case.project.metadata_json,
        "fanqie_short_ready_for_upload": not expected,
    }
    await case.session.commit()
    retained = deepcopy(case.project.metadata_json)
    evidence = await helper.read_fanqie_delivery_evidence(case.session, case.project.id)
    assert evidence["ready_for_upload"] is expected
    result = await evaluate(helper, case)
    assert result["whole_book_complete"] is True
    assert result["ready_for_upload"] is expected
    await case.session.refresh(case.project)
    assert case.project.metadata_json == retained  # readback never rewrites old claims


@pytest.mark.parametrize("value", [True, 1, "true", None])
async def test_exporter_flags_cannot_fake_upload_support(
    integrated_modules, source_case, monkeypatch, value
):
    from bestseller.services import fanqie_short_export

    helper, _, _ = integrated_modules
    case = source_case
    await set_target(case, 400)
    original = fanqie_short_export.export_fanqie_short_markdown

    def forged(*args, **kwargs):
        paths = original(*args, **kwargs)
        path = Path(paths["readiness_path"])
        report = json.loads(path.read_text())
        assert report["ready_for_upload"] is False
        report["ready_for_upload"] = value
        path.write_text(json.dumps(report))
        return paths

    monkeypatch.setattr(fanqie_short_export, "export_fanqie_short_markdown", forged)
    with pytest.raises(ValueError, match=r"readiness_(invalid|mismatch)"):
        await publish(helper, case)
    await case.session.refresh(case.project)
    assert case.project.status == "writing"
    assert not case.project.metadata_json.get("fanqie_whole_export_receipt")
    assert case.project.target_word_count == 400


@pytest.mark.parametrize("field", ["ready_for_upload", "ranking_gate_passed", "target_word_count"])
async def test_checksum_rewrite_cannot_launder_readiness(integrated_modules, source_case, field):
    helper, _, _ = integrated_modules
    case = source_case
    await set_target(case, 400)
    result = await publish(helper, case)
    await case.session.refresh(case.project)
    path = Path(result["export_paths"]["readiness_path"])
    report = json.loads(path.read_text())
    report[field] = report["total_words"] if field == "target_word_count" else not report[field]
    path.write_text(json.dumps(report))
    meta = deepcopy(case.project.metadata_json)
    verification = meta["fanqie_whole_export_receipt"]["delivery_verification"]
    data = path.read_bytes()
    verification["files"]["readiness_path"].update(
        sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)
    )
    verification["ready_for_upload"] = report["ready_for_upload"]
    meta["fanqie_short_ready_for_upload"] = True
    case.project.metadata_json = meta
    await case.session.commit()
    with pytest.raises(ValueError, match=r"readiness_(invalid|mismatch)"):
        await helper.read_fanqie_delivery_evidence(case.session, case.project.id)
    assert (await evaluate(helper, case))["whole_book_complete"] is False


@pytest.mark.parametrize("legacy_ready", [True, False])
async def test_legacy_receipt_requires_normal_retry_and_is_preserved(
    integrated_modules, source_case, legacy_ready
):
    helper, _, _ = integrated_modules
    case = source_case
    await set_target(case, 400)
    result = await publish(helper, case)
    await case.session.refresh(case.project)
    meta = deepcopy(case.project.metadata_json)
    verification = meta["fanqie_whole_export_receipt"]["delivery_verification"]
    verification["schema"] = "fanqie-delivery.v1"
    verification.pop("readiness_inputs", None)
    verification.pop("ready_for_upload", None)
    meta["fanqie_short_ready_for_upload"] = legacy_ready
    case.project.metadata_json = meta
    await case.session.commit()
    previous = deepcopy(meta["fanqie_whole_export_receipt"])
    old_files = {key: Path(value).read_bytes() for key, value in result["export_paths"].items()}
    with pytest.raises(ValueError, match="receipt_unverified"):
        await helper.read_fanqie_delivery_evidence(case.session, case.project.id)
    evaluation = await evaluate(helper, case)
    assert evaluation["whole_book_complete"] is False
    assert evaluation["ready_for_upload"] is False
    await case.session.refresh(case.project)
    assert case.project.metadata_json == meta
    retry = await publish(helper, case)
    await case.session.refresh(case.project)
    assert retry["whole_book_complete"] is True
    assert retry["ready_for_upload"] is False
    assert case.project.metadata_json["fanqie_short_ready_for_upload"] is False
    assert case.project.metadata_json["fanqie_whole_export_history"][-1] == previous
    archive = Path(retry["previous_outputs_archive"]["directory"]) / "files/exports"
    assert all(
        (archive / Path(result["export_paths"][key]).name).read_bytes() == data
        for key, data in old_files.items()
    )


@pytest.mark.parametrize("fault", ["numeric_bool", "missing_inputs", "missing_receipt"])
async def test_v2_evidence_cannot_be_invented_from_flags(integrated_modules, source_case, fault):
    helper, _, _ = integrated_modules
    case = source_case
    await publish(helper, case)
    await case.session.refresh(case.project)
    meta = deepcopy(case.project.metadata_json)
    meta["fanqie_short_ready_for_upload"] = True
    verification = meta["fanqie_whole_export_receipt"]["delivery_verification"]
    if fault == "numeric_bool":
        verification["ready_for_upload"] = 1
    elif fault == "missing_inputs":
        verification.pop("readiness_inputs")
    else:
        meta.pop("fanqie_whole_export_receipt")
    case.project.metadata_json = meta
    flag_modified(case.project, "metadata_json")
    await case.session.commit()
    with pytest.raises(ValueError):
        await helper.read_fanqie_delivery_evidence(case.session, case.project.id)
    result = await evaluate(helper, case)
    assert result["whole_book_complete"] is False
    assert result["ready_for_upload"] is False
    await case.session.refresh(case.project)
    assert case.project.metadata_json == meta
