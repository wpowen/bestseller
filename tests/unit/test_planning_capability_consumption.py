"""Selection authority at builder, readiness, and real SQLite review boundaries."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_planning_kernel import (
    _book_spec,
    _cast_spec,
    _emotion_driven_kernel,
    _project,
    _public_emotion_kernel,
    _story_design_kernel,
    _volume_plan,
    _world_spec,
)

from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel, RewriteTaskModel
from bestseller.services import book_creation_readiness_gate as book_gate
from bestseller.services import planning_kernel as kernel_service
from bestseller.services import prewrite_review as review
from bestseller.services.planning_capability_selection import (
    CAPABILITIES,
    PlanningCapabilitySelectionError,
)

pytestmark = pytest.mark.unit


def metadata(*selected):
    return {
        "creation_intent_contract": {
            "field_sources": {"hook_spec": "explicit"},
            "hook_spec": {
                "planning_capabilities": {
                    "schema": "planning-capabilities.v1",
                    "selected": {key: key in selected for key in CAPABILITIES},
                }
            },
        }
    }


def build(data):
    return kernel_service.build_project_planning_kernel(
        _project(**data),
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )


def findings(report):
    return {finding.code for finding in (*report.blocking_findings, *report.warnings)}


def test_unselected_legacy_cache_is_never_consumed_and_is_retained(monkeypatch):
    data = metadata()
    data.update({key: {"invalid": "retained legacy bytes"} for key in CAPABILITIES})
    data["planning_capabilities"] = {
        key: {"selected": True, "status": "selected"} for key in CAPABILITIES
    }
    before = deepcopy(data)

    def forbidden(*args, **kwargs):
        raise AssertionError("An unselected planning cache was consumed")

    for name in (
        "_story_design_kernel_from",
        "_emotion_driven_kernel_from",
        "_public_emotion_kernel_from",
        "_entry_system_kernel_from",
    ):
        monkeypatch.setattr(kernel_service, name, forbidden)
    project = _project(**data)
    result = kernel_service.persist_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    for key in CAPABILITIES:
        assert project.metadata_json[key] == before[key]
        assert result["planning_kernel"]["planning_capabilities"][key]["status"] == "not_applicable"
    assert "story_design" not in " ".join(result["prewrite_repair_directives"])
    optional_codes = {
        "story_design_kernel_missing",
        "emotion_driven_kernel_missing",
        "public_emotion_kernel_missing",
        "entry_system_kernel_invalid",
    }
    assert not optional_codes & {f["code"] for f in result["prewrite_readiness_report"]["warnings"]}


@pytest.mark.parametrize(
    "capability,code",
    [
        ("story_design_kernel", "story_design_contract_invalid"),
        ("emotion_driven_kernel", "empathy_contract_missing"),
        ("public_emotion_kernel", "public_emotion_bridge_missing"),
        ("entry_system_kernel", "entry_system_kernel_invalid"),
    ],
)
def test_explicit_selected_invalid_kernel_still_blocks(capability, code):
    data = metadata(capability)
    data[capability] = {"invalid": "actual malformed kernel"}
    report = kernel_service.evaluate_prewrite_readiness(build(data), project_metadata=data)
    assert code in {f.code for f in report.blocking_findings}
    assert report.passed is False


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_explicit_selected_missing_kernel_blocks(capability):
    data = metadata(capability)
    report = kernel_service.evaluate_prewrite_readiness(build(data), project_metadata=data)
    assert report.blocking_findings and not report.passed
    assert any(
        f.path in {"story_design", "emotion_driven", "public_emotion", "entry_system"}
        for f in report.blocking_findings
    )


def test_selected_valid_kernels_use_current_author_source_and_persist_passes_metadata():
    data = metadata("story_design_kernel", "emotion_driven_kernel", "public_emotion_kernel")
    data.update(
        story_design_kernel=_story_design_kernel(),
        emotion_driven_kernel=_emotion_driven_kernel(),
        public_emotion_kernel=_public_emotion_kernel(),
    )
    project = _project(**data)
    result = kernel_service.persist_project_planning_kernel(
        project,
        book_spec=_book_spec(),
        world_spec=_world_spec(),
        cast_spec=_cast_spec(),
        volume_plan=_volume_plan(),
    )
    for key in ("story_design", "emotion_driven", "public_emotion"):
        assert result["planning_kernel"][key]["valid"] is True
    blocked = {f["code"] for f in result["prewrite_readiness_report"]["blocking_findings"]}
    assert (
        not {
            "story_design_contract_invalid",
            "emotion_driven_kernel_missing",
            "public_emotion_kernel_missing",
        }
        & blocked
    )
    selection = result["prewrite_readiness_report"]["capability_snapshot"]["planning_capabilities"]
    assert selection["story_design_kernel"]["selected"] is True


def test_cached_self_claimed_selection_cannot_authorize_without_metadata():
    data = metadata("story_design_kernel")
    data["story_design_kernel"] = _story_design_kernel()
    kernel = build(data)
    report = kernel_service.evaluate_prewrite_readiness(kernel)
    receipt = report.capability_snapshot["planning_capabilities"]["story_design_kernel"]
    assert receipt["status"] == "not_applicable"
    assert report.capability_snapshot["story_design_kernel"] is None
    # A changed author contract cannot reuse a cached selection hash.
    changed = metadata("story_design_kernel", "public_emotion_kernel")
    report = kernel_service.evaluate_prewrite_readiness(kernel, project_metadata=changed)
    assert "story_design_kernel_missing" in {f.code for f in report.blocking_findings}


def test_unselected_summary_is_not_even_read_and_real_hard_failures_remain():
    class GuardedKernel(dict):
        def get(self, key, default=None):
            if key in {
                "story_design",
                "emotion_driven",
                "public_emotion",
                "entry_system",
                "planning_capabilities",
            }:
                raise AssertionError("Unselected cached projection was read")
            return super().get(key, default)

    source = build(metadata())
    source["concept_contract"] = {
        "declared": True,
        "present": True,
        "valid": False,
        "violations": ["real source-lineage conflict"],
        "volume_mapping_report": {"passed": False},
    }
    source["story_viability"] = {"passed": False, "blocking_codes": ["REAL_CAPACITY_FAILURE"]}
    report = kernel_service.evaluate_prewrite_readiness(
        GuardedKernel(source), project_metadata=metadata()
    )
    assert {
        "concept_contract_invalid",
        "seriality_volume_mapping_invalid",
        "story_capacity_insufficient",
    } <= findings(report)
    assert report.passed is False


@pytest.mark.parametrize("entry", ["builder", "prewrite", "book"])
def test_malformed_explicit_selection_cannot_downgrade_to_na(entry):
    data = metadata()
    data["creation_intent_contract"]["hook_spec"]["planning_capabilities"]["selected"][
        "story_design_kernel"
    ] = "false"
    with pytest.raises(PlanningCapabilitySelectionError):
        if entry == "builder":
            build(data)
        elif entry == "prewrite":
            review.required_prewrite_artifact_types(target_chapters=30, project_metadata=data)
        else:
            evaluate_book(data)


def evaluate_book(data, **kwargs):
    return book_gate.evaluate_book_creation_readiness(
        project_slug="generic-capability-book",
        project_metadata=data,
        target_chapters=30,
        planned_chapters=30,
        volume_plan_report={"passed": True},
        prewrite_report={"passed": True},
        forward_state_report={"passed": True},
        reveal_alignment_report={"passed": True},
        **kwargs,
    )


def test_book_readiness_marks_unselected_story_na_but_keeps_other_hard_checks():
    class Unreadable(dict):
        def __iter__(self):
            raise AssertionError("Unselected raw story cache read")

        def keys(self):
            raise AssertionError("Unselected raw story cache read")

    data = {**metadata(), "methodology_contract_mode": "strict", "category_key": "fantasy"}
    report = evaluate_book(data, story_design_kernel=Unreadable(invalid=True))
    assert report.passed is True
    assert report.domain_statuses[1].metrics["story_design_kernel_valid"] is None
    report = evaluate_book({**data, "category_key": ""})
    assert "category_key_missing" in {f.code for f in report.findings}
    selected = {**data, **metadata("story_design_kernel")}
    report = evaluate_book(selected, story_design_kernel={"valid": False})
    assert {"story_design_grammar_key_missing", "story_design_kernel_not_verified"} <= {
        f.code for f in report.findings
    }


def test_optional_only_cached_failure_is_na_not_a_new_pass_and_other_failures_block():
    old = {
        "passed": False,
        "blocking_findings": [{"code": "story_design_contract_invalid"}],
        "warnings": [],
    }
    before = deepcopy(old)
    report = evaluate_book(metadata(), planning_kernel_report=old)
    assert report.passed is True and old == before
    assert report.domain_statuses[1].metrics["planning_kernel_passed"] is None
    for extra in ({"code": "concept_contract_invalid"}, {"code": "UNKNOWN_HARD_FACT"}):
        current = {**old, "blocking_findings": [*old["blocking_findings"], extra]}
        report = evaluate_book(metadata(), planning_kernel_report=current)
        assert "planning_kernel_not_passed" in {f.code for f in report.findings}
    assert not evaluate_book(metadata(), planning_kernel_report={"passed": False}).passed


def test_selected_public_emotion_real_boundary_failure_still_blocks():
    from bestseller.services.compliance_boundary_kernel import build_compliance_boundary_kernel_seed

    data = metadata("public_emotion_kernel")
    raw = _public_emotion_kernel()
    raw["emotion_bridges"][0]["title_hook"] = "被真实学校欺负后，我要报复现实所有人"  # noqa: RUF001
    data.update(
        public_emotion_kernel=raw,
        compliance_boundary_kernel=build_compliance_boundary_kernel_seed(platform="番茄小说"),
    )
    report = kernel_service.evaluate_prewrite_readiness(build(data), project_metadata=data)
    assert "compliance_boundary_high_risk" in {f.code for f in report.blocking_findings}
    assert report.capability_snapshot["compliance_boundary"] is False


@pytest.fixture
async def database(tmp_path):
    path = tmp_path / "planning_capability_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    for model in (ProjectModel, PlanningArtifactVersionModel, RewriteTaskModel):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_unselected_optional_staleness_does_not_invalidate_or_approve_old_rows(
    database,
):
    project = ProjectModel(
        id=uuid4(),
        slug="capability-review",
        title="Isolated review",
        genre="mystery",
        language="en",
        target_chapters=30,
        target_word_count=78000,
        hype_scheme_json={},
        metadata_json=metadata(),
    )
    async with database() as session:
        session.add(project)
        for kind in review.required_prewrite_artifact_types(
            target_chapters=30, project_metadata=metadata()
        ):
            session.add(
                PlanningArtifactVersionModel(
                    id=uuid4(),
                    project_id=project.id,
                    artifact_type=kind,
                    version_no=1,
                    status="approved",
                    schema_version="1.0",
                    content={"fact": kind},
                )
            )
        for kind in CAPABILITIES:
            session.add(
                PlanningArtifactVersionModel(
                    id=uuid4(),
                    project_id=project.id,
                    artifact_type=kind,
                    version_no=1,
                    status="needs_review",
                    schema_version="1.0",
                    content={"old_raw": kind},
                )
            )
        await session.commit()
        first = await review.load_prewrite_review_payload(session, project.slug)
        assert not set(CAPABILITIES) & first["current_snapshot"].keys()
        assert first["missing_required_artifacts"] == []
        project.metadata_json = {
            **metadata(),
            review.PREWRITE_REVIEW_METADATA_KEY: {
                "status": "approved",
                "approved_snapshot": {
                    **first["current_snapshot"],
                    **{key: {"artifact_id": "historical-unselected"} for key in CAPABILITIES},
                },
            },
        }
        for kind in CAPABILITIES:
            session.add(
                PlanningArtifactVersionModel(
                    id=uuid4(),
                    project_id=project.id,
                    artifact_type=kind,
                    version_no=2,
                    status="needs_review",
                    schema_version="1.0",
                    content={"new_raw": kind},
                )
            )
        await session.commit()
        old_rows = list(
            await session.scalars(
                select(PlanningArtifactVersionModel).where(
                    PlanningArtifactVersionModel.artifact_type.in_(CAPABILITIES)
                )
            )
        )
        before = {row.id: (deepcopy(row.content), row.status) for row in old_rows}
        current = await review.load_prewrite_review_payload(session, project.slug)
        assert current["is_approved"] and current["stale_artifact_types"] == []
        for item in current["materials"]:
            if item["artifact_type"] in CAPABILITIES:
                assert (
                    item["applicability"] == "not_applicable"
                    and item["approved_in_snapshot"] is False
                )
        await review.approve_prewrite_materials(
            session, project.slug, approved_by="offline-fixture"
        )
        await session.commit()
        for row in old_rows:
            await session.refresh(row)
            assert (row.content, row.status) == before[row.id]
        project.metadata_json = {**project.metadata_json, **metadata("story_design_kernel")}
        await session.commit()
        current = await review.load_prewrite_review_payload(session, project.slug)
        assert (
            not current["is_approved"] and "story_design_kernel" in current["stale_artifact_types"]
        )


@pytest.mark.asyncio
async def test_sqlite_selected_missing_material_does_not_claim_approval(database):
    project = ProjectModel(
        id=uuid4(),
        slug="missing-selected",
        title="Selected",
        genre="fantasy",
        language="zh-CN",
        target_chapters=30,
        target_word_count=78000,
        hype_scheme_json={},
        metadata_json={
            **metadata("story_design_kernel"),
            review.PREWRITE_REVIEW_METADATA_KEY: {"status": "approved", "approved_snapshot": {}},
        },
    )
    async with database() as session:
        session.add(project)
        await session.commit()
        current = await review.load_prewrite_review_payload(session, project.slug)
        assert not current["is_approved"] and current["status"] == "needs_review"
        assert "story_design_kernel" in current["missing_required_artifacts"]
