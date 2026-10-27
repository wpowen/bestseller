from copy import deepcopy
import hashlib
from types import SimpleNamespace
import sys
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import (
    ChapterDraftVersionModel, ChapterModel, ProjectModel, QualityScoreModel,
    ReviewReportModel, RewriteTaskModel, SceneCardModel, WorkflowRunModel,
)
from bestseller.services import chapter_review_reconciliation as reconcile
from bestseller.services import pipelines


OLD_SOURCE = '''
def chapter_review_recovery_source_hash():
    policy_files = ("pipelines.py", "reviews.py")
async def _promote_best_scoring_chapter_draft_on_stall(session):
    return "old selection"
async def review(session, settings):
    return await _promote_best_scoring_chapter_draft_on_stall(session)
'''
NEW_SOURCE = OLD_SOURCE.replace('return "old selection"', 'return "verified selection"').replace(
    '_on_stall(session)\n', '_on_stall(session, settings=settings)\n')


@pytest.mark.parametrize("change", ["canonical", "other_function", "other_input", "guard_changed", "assignment_only"])
def test_only_exact_effective_profile_canonicalization_is_compatible(change):
    source = '''
async def _chapter_critic_semantic_dependencies(session, settings, chapter):
    """Keep the same source and config fence."""
    return settings.llm.critic.model
'''
    added = ('    from bestseller.settings import apply_runtime_llm_profile\n'
             '    settings = apply_runtime_llm_profile(settings)\n')
    current = source.replace('    return', added + '    return')
    if change == "other_function":
        current = current.replace('apply_runtime_llm_profile(settings)', 'other_profile(settings)')
    elif change == "other_input":
        current = current.replace('apply_runtime_llm_profile(settings)', 'apply_runtime_llm_profile(other)')
    elif change == "guard_changed":
        current = current.replace('return settings.llm.critic.model', 'return "ignored config"')
    elif change == "assignment_only":
        current = current.replace('    from bestseller.settings import apply_runtime_llm_profile\n', '')
    assert (reconcile._normalized_module(source) == reconcile._normalized_module(current)) == (change == "canonical")


@pytest.mark.parametrize("change", ["guard", "different_condition", "different_input", "other_statement"])
def test_only_exact_prepaid_retained_cycle_guard_is_control_compatible(change):
    source = 'async def run_chapter_pipeline(session, settings, chapter):\n    await paid_backfill()\n'
    guard = (
        '    if use_chapter_first and not resume_existing_draft_only and not should_supersede_pending_rewrites:\n'
        '        from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing\n'
        '        await validate_retained_review_before_processing(session, settings, chapter)\n'
    )
    current = source.replace('    await paid_backfill()', guard + '    await paid_backfill()')
    if change == "different_condition":
        current = current.replace('not resume_existing_draft_only', 'resume_existing_draft_only')
    elif change == "different_input":
        current = current.replace('(session, settings, chapter)', '(session, other, chapter)')
    elif change == "other_statement":
        current = current.replace('await paid_backfill()', 'await another_evaluator()')
    assert (reconcile._normalized_module(source) == reconcile._normalized_module(current)) == (change == "guard")



@pytest.mark.parametrize("change", ["exact", "different_current", "retry_credit", "skip_checkpoint", "guard_inverted", "other_evaluator"])
def test_only_exact_rejected_editor_checkpoint_is_compatible(change):
    import textwrap
    source = ("async def run_chapter_pipeline(session):\n"
              "    while True:\n"
              "        await existing_review()\n"
              "        await existing_rewrite()\n"
              "        step_order += 1\n")
    guard = textwrap.indent(reconcile._FAILED_REWRITE_GUARD.strip() + "\n", "        ")
    checkpoint = textwrap.indent(reconcile._REWRITE_RETURN_CHECKPOINT.strip() + "\n", "        ")
    current = source.replace("        await existing_rewrite()\n", guard + "        await existing_rewrite()\n").replace("        step_order += 1\n", checkpoint)
    if change == "different_current":
        current = current.replace("current_draft=chapter_draft", "current_draft=unverified_draft")
    elif change == "retry_credit":
        current = current.replace("    requires_human_review = True", "    rewrite_task.attempts = 0")
    elif change == "skip_checkpoint":
        current = current.replace("await _checkpoint_commit(session)", "await unrelated_commit(session)")
    elif change == "guard_inverted":
        current = current.replace('chapter_rewrite_task.status == "failed"', 'chapter_rewrite_task.status != "failed"')
    elif change == "other_evaluator":
        current = current.replace("await existing_review()", "await changed_review()")
    assert (reconcile._normalized_module(source) == reconcile._normalized_module(current)) == (change == "exact")


def policy_hash(root, old=None):
    digest = hashlib.sha256()
    for name in ("pipelines.py", "reviews.py"):
        path = root / "src/bestseller/services" / name
        data = old.encode() if old is not None and name == "pipelines.py" else path.read_bytes()
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


@pytest.mark.parametrize("change", ["rewriter_only", "reviewer_changed", "wrong_old_bytes", "rewriter_missing"])
def test_rewriter_only_fix_preserves_reviewer_ast_and_requires_exact_old_policy(tmp_path, change):
    services = tmp_path / "src/bestseller/services"
    services.mkdir(parents=True)
    (services / "pipelines.py").write_text(OLD_SOURCE)
    old = ('async def rewrite_chapter_from_task(session):\n    return None\n'
           'def build_chapter_rewrite_prompts():\n    return "editor prompt"\n'
           'def evaluate_review():\n    return "same review"\n')
    (services / "reviews.py").write_text(old)
    old_hash = policy_hash(tmp_path)
    current = old.replace('return None', 'return "preserve prior state"')
    if change == "reviewer_changed":
        current = current.replace('same review', 'different review')
    elif change == "rewriter_missing":
        current = current.replace('rewrite_chapter_from_task', 'other_rewriter')
    (services / "reviews.py").write_text(current)
    snapshot = old.replace('return None', 'return "unverified old bytes"') if change == "wrong_old_bytes" else old
    kwargs = dict(root=tmp_path, previous_pipeline_source=OLD_SOURCE.encode(),
                  previous_review_source=snapshot.encode(), expected_previous_policy_hash=old_hash,
                  expected_current_policy_hash=policy_hash(tmp_path))
    if change == "rewriter_only":
        result = reconcile.candidate_selection_policy_compatibility(**kwargs)
        assert result['unchanged_review_evaluator_ast']
        assert result['previous_review_sha256'] != result['current_review_sha256']
    else:
        with pytest.raises(ValueError):
            reconcile.candidate_selection_policy_compatibility(**kwargs)


@pytest.mark.parametrize("change", ["runtime_only", "authored_removed", "policy_removed", "builder_changed", "missing_runtime", "wrong_old_bytes"])
def test_only_seven_known_runtime_projection_additions_are_compatible(tmp_path, change):
    services = tmp_path / "src/bestseller/services"
    services.mkdir(parents=True)
    source = OLD_SOURCE.replace('"pipelines.py", "reviews.py"',
                                '"pipelines.py", "reviews.py", "chapter_generation_input_builder.py"')
    (services / "pipelines.py").write_text(source)
    (services / "reviews.py").write_text('"same evaluator"\n')
    path = services / "chapter_generation_input_builder.py"
    old = ('_CHAPTER_RUNTIME_EVIDENCE_KEYS = frozenset({"existing_runtime"})\n'
           'def project_data(data):\n    return data\n')
    path.write_text(old)
    def digest():
        value = hashlib.sha256()
        for p in sorted(services.glob('*.py')):
            value.update(str(p.relative_to(tmp_path)).encode() + b'\0')
            value.update(hashlib.sha256(p.read_bytes()).digest())
        return value.hexdigest()
    old_hash = digest()
    keys = ['chapter_critic_cycles', 'chapter_review_active_cycle', 'chapter_review_recovery_cycles',
            'chapter_review_attempts_active', 'chapter_first_resume_existing_draft_id',
            'chapter_review_evidence_status', 'chapter_review_evidence_report_id']
    if change == "missing_runtime":
        keys.pop()
    if change == "authored_removed":
        keys.append('authored_clause')
    if change == "policy_removed":
        keys.append('chapter_review_warn_only')
    current = old.replace('"existing_runtime"', '"existing_runtime", ' + ', '.join(repr(k) for k in keys))
    if change == "builder_changed":
        current = current.replace('return data', 'return None')
    path.write_text(current)
    snapshot = old.replace('existing_runtime', 'unverified_snapshot') if change == "wrong_old_bytes" else old
    kwargs = dict(root=tmp_path, previous_pipeline_source=source.encode(),
                  previous_generation_source=snapshot.encode(), expected_previous_policy_hash=old_hash,
                  expected_current_policy_hash=digest())
    if change == "runtime_only":
        result = reconcile.candidate_selection_policy_compatibility(**kwargs)
        assert result['previous_generation_sha256'] != result['current_generation_sha256']
    else:
        with pytest.raises(ValueError):
            reconcile.candidate_selection_policy_compatibility(**kwargs)


@pytest.mark.parametrize("change", ["selection_only", "review_changed", "external_changed",
                                   "wrong_snapshot", "wrong_inventory", "wrong_settings"])
def test_real_policy_bytes_and_evaluation_ast_are_required(tmp_path, change):
    services = tmp_path / "src/bestseller/services"
    services.mkdir(parents=True)
    (services / "reviews.py").write_text('"unchanged evaluator"\n')
    (services / "pipelines.py").write_text(NEW_SOURCE)
    old_hash = policy_hash(tmp_path, OLD_SOURCE)
    current_hash = policy_hash(tmp_path)
    snapshot = OLD_SOURCE
    if change == "review_changed":
        (services / "pipelines.py").write_text(NEW_SOURCE + '\ndef unrelated_review(): return 1\n')
    elif change == "external_changed":
        (services / "reviews.py").write_text('"different evaluator"\n')
        current_hash = policy_hash(tmp_path)
    elif change == "wrong_snapshot":
        snapshot = OLD_SOURCE.replace("old selection", "unproven selection")
    elif change == "wrong_inventory":
        current_hash = "f" * 64
    elif change == "wrong_settings":
        (services / "pipelines.py").write_text(NEW_SOURCE.replace("settings=settings", "settings=other"))
    args = dict(root=tmp_path, previous_pipeline_source=snapshot.encode(),
                expected_previous_policy_hash=old_hash, expected_current_policy_hash=current_hash)
    if change == "selection_only":
        receipt = reconcile.candidate_selection_policy_compatibility(**args)
        assert receipt["unchanged_evaluation_ast"] and receipt["unchanged_other_policy_files"]
    else:
        with pytest.raises(ValueError):
            reconcile.candidate_selection_policy_compatibility(**args)


def test_policy_inventory_can_add_only_selection_and_model_routing(tmp_path):
    services = tmp_path / "src/bestseller/services"
    services.mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (services / "reviews.py").write_text('"same reviewer"\n')
    (services / "chapter_draft_candidate_eligibility.py").write_text('POLICY = "source-bound"\n')
    (tmp_path / "config/model_catalog.yaml").write_text('models: []\n')
    source = NEW_SOURCE.replace('("pipelines.py", "reviews.py")',
        '("pipelines.py", "chapter_draft_candidate_eligibility.py", "reviews.py")\n'
        '    policy_files.add(root / "config/model_catalog.yaml")')
    (services / "pipelines.py").write_text(source)
    old_hash = policy_hash(tmp_path, OLD_SOURCE)
    paths = [services / name for name in ("pipelines.py", "reviews.py", "chapter_draft_candidate_eligibility.py")]
    paths.append(tmp_path / "config/model_catalog.yaml")
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(tmp_path)).encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    receipt = reconcile.candidate_selection_policy_compatibility(
        root=tmp_path, previous_pipeline_source=OLD_SOURCE.encode(),
        expected_previous_policy_hash=old_hash, expected_current_policy_hash=digest.hexdigest())
    assert receipt["policy_file_count"] == 4


@pytest.fixture
async def migration(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'reconcile_test.sqlite3'}")
    async with engine.begin() as connection:
        for model in (ProjectModel, ChapterModel, ChapterDraftVersionModel,
                      ReviewReportModel, QualityScoreModel, RewriteTaskModel, SceneCardModel, WorkflowRunModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        project = ProjectModel(slug="unrelated-sailing-test", title="Sailing", genre="adventure",
                               target_chapters=5, target_word_count=12000, metadata_json={})
        session.add(project)
        await session.flush()
        chapter = ChapterModel(project_id=project.id, chapter_number=2, chapter_goal="return",
                               metadata_json={}, current_word_count=150)
        session.add(chapter)
        await session.flush()
        source = ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
            version_no=3, content_md="The navigator returns the sealed chart.", word_count=230,
            is_current=False, promotion_state="candidate")
        wrong = ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
            version_no=1, content_md="The old chart is still in the cabin.", word_count=150,
            is_current=True, promotion_state="candidate")
        session.add_all([source, wrong])
        await session.flush()
        result = ChapterReviewResult(verdict="rewrite", severity_max="major",
            scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, .7)))
        report = ReviewReportModel(project_id=project.id, target_type="chapter", target_id=chapter.id,
            reviewer_type="synthetic-no-provider", verdict="rewrite", structured_output={
                "draft_id": str(source.id), "reviewed_body_fingerprint": pipelines.chapter_review_body_fingerprint(source.content_md),
                "critic_evidence": {"status": "verified"}})
        session.add(report)
        await session.flush()
        quality = QualityScoreModel(project_id=project.id, target_type="chapter", target_id=chapter.id,
            chapter_draft_version_id=source.id, review_report_id=report.id, score_overall=.7, is_current=False)
        session.add(quality)
        await session.flush()
        attempt = {"state": "completed", "outcome": "available", "review_report_id": str(report.id),
                   "quality_score_id": str(quality.id), "review_result": result.model_dump(mode="json")}
        cycle = {"cycle_id": "cycle-one", "state": "completed", "source_draft_id": str(source.id),
                 "source_body_hash": pipelines.chapter_review_body_fingerprint(source.content_md)["digest"],
                 "policy_source_hash": "old-policy", "semantic_dependencies_sha256": "same-data",
                 "attempts": [attempt]}
        chapter.metadata_json = {"chapter_review_active_cycle": deepcopy(cycle),
                                 "chapter_critic_cycles": {"cycle-one": deepcopy(cycle)}}
        await session.commit()
        monkeypatch.setattr(pipelines, "chapter_review_recovery_source_hash", lambda: "current-policy")
        async def semantic(*args):
            return "same-data"
        monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies", semantic)
        monkeypatch.setattr(reconcile, "candidate_selection_policy_compatibility", lambda **kw: {
            "previous_policy_hash": kw["expected_previous_policy_hash"], "current_policy_hash": "current-policy",
            "unchanged_evaluation_ast": True, "unchanged_other_policy_files": True})
        async def input_proof(*args, **kwargs):
            return {"synthetic_no_provider": True, "effective_input_unchanged": True}
        monkeypatch.setitem(sys.modules, "bestseller.services.retained_review_input_proof",
                            SimpleNamespace(verify_retained_review_inputs=input_proof))
        yield SimpleNamespace(session=session, project=project, chapter=chapter, source=source,
                              wrong=wrong, report=report, quality=quality, original_cycle=deepcopy(cycle))
    await engine.dispose()


async def apply(rig, current_id=None):
    return await reconcile.reconcile_retained_chapter_review(rig.session, SimpleNamespace(),
        project_id=rig.project.id, chapter_id=rig.chapter.id, expected_cycle_id="cycle-one",
        expected_current_draft_id=current_id or rig.wrong.id, previous_pipeline_source=b"trusted snapshot",
        actor="authorized test repair", evidence_ref="source-selected-repair")


@pytest.mark.asyncio
async def test_working_pointer_and_policy_alias_migrate_twice_without_regrading(migration):
    rig = migration
    old_bodies = {d.id: d.content_md for d in (rig.source, rig.wrong)}
    old_report, old_score = deepcopy(rig.report.structured_output), rig.quality.score_overall
    first = await apply(rig)
    await rig.session.commit()
    second = await apply(rig, rig.source.id)
    await rig.session.commit()
    assert second == first and not first["adoption_created"]
    assert rig.source.is_current and not rig.wrong.is_current
    assert rig.chapter.current_word_count == 230
    cycle = rig.chapter.metadata_json["chapter_critic_cycles"]["cycle-one"]
    assert cycle["attempts"] == rig.original_cycle["attempts"]
    assert len(cycle["candidate_selection_reconciliations"]) == 1
    assert rig.report.structured_output == old_report and rig.quality.score_overall == old_score
    assert {d.id: d.content_md for d in (rig.source, rig.wrong)} == old_bodies


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["unknown", "body_changed", "foreign_draft", "semantic_changed", "pointer_race", "ambiguous_adoption", "active_writer", "effective_changed", "during_validation"])
async def test_invalid_source_reconciliation_rolls_back_without_new_attempts(migration, monkeypatch, invalid):
    rig = migration
    metadata = deepcopy(rig.chapter.metadata_json)
    if invalid == "unknown":
        metadata["chapter_critic_cycles"]["cycle-one"]["attempts"][0]["outcome"] = "unknown"
    elif invalid == "body_changed":
        rig.source.content_md += " Changed."
    elif invalid == "foreign_draft":
        metadata["chapter_critic_cycles"]["cycle-one"]["source_draft_id"] = str(uuid4())
    elif invalid == "semantic_changed":
        async def different(*args): return "changed-data"
        monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies", different)
    elif invalid == "ambiguous_adoption":
        metadata["numeric_promotion_debt"] = {}
    elif invalid == "active_writer":
        rig.session.add(WorkflowRunModel(project_id=rig.project.id, workflow_type="chapter_pipeline",
            scope_type="chapter", scope_id=rig.chapter.id, status="running", metadata_json={}))
    elif invalid == "effective_changed":
        async def changed(*args, **kwargs):
            raise ValueError("retained effective model/source changed")
        monkeypatch.setattr(sys.modules["bestseller.services.retained_review_input_proof"],
                            "verify_retained_review_inputs", changed)
    elif invalid == "during_validation":
        seen = []
        async def changed_during_validation(*args):
            seen.append(True)
            return "same-data" if len(seen) == 1 else "changed-data"
        monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies", changed_during_validation)
    rig.chapter.metadata_json = metadata
    await rig.session.commit()
    source_id, wrong_id, chapter_id = rig.source.id, rig.wrong.id, rig.chapter.id
    with pytest.raises(ValueError):
        async with rig.session.begin_nested():
            await apply(rig, uuid4() if invalid == "pointer_race" else wrong_id)
    # Independent SQL scalar readback after the savepoint, not stale ORM fields.
    current_id = await rig.session.scalar(select(ChapterDraftVersionModel.id).where(
        ChapterDraftVersionModel.chapter_id == chapter_id,
        ChapterDraftVersionModel.is_current.is_(True)))
    assert current_id == wrong_id and current_id != source_id
