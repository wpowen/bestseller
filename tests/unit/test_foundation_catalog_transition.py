"""Closed three-leaf compatibility, not a source/route/grade hash waiver."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
import yaml
from bestseller.services import foundation_catalog_transition as transition
from bestseller.services.prompt_source_policy import content_hash

pytestmark = pytest.mark.unit


def case():
    old = yaml.safe_dump(
        {
            "models": [
                {
                    "id": "old",
                    "display_name": "Retained old route",
                    "vendor": "test",
                    "model": "openai/old",
                    "api_base": "https://old.invalid/v1",
                }
            ]
        }
    )
    data = yaml.safe_load(old)
    data["models"].append(
        {
            "id": "new",
            "display_name": "Unrelated additional route",
            "vendor": "test",
            "model": "openai/new",
            "api_base": "https://new.invalid/v1",
        }
    )
    current = yaml.safe_dump(data)
    components = {
        "schema": "verified-volume-source-components.v1",
        "project_id": str(uuid4()),
        "revision_id": str(uuid4()),
        "sources": {"book_spec": {"body": "另一部日常故事"}},
        "legacy_source_reviews": {
            "world": {
                "catalog_sha256": transition.sha(current),
                "effective_input_sha256": "world-original",
            },
            "cast": {
                "catalog_sha256": transition.sha(current),
                "effective_input_sha256": "cast-original",
            },
        },
        "unchanged_component_axes": {
            "catalog_sha256": transition.sha(current),
            "effective_input_sha256": "delta-original",
        },
    }
    original = deepcopy(components)
    for path in transition.PATHS:
        node = original
        for key in path:
            node = node[key]
        node["catalog_sha256"] = transition.sha(old)
    return old, current, components, content_hash(original)


def test_exact_addition_preserves_all_old_component_identities():
    old, current, components, original_hash = case()
    before = deepcopy(components)
    proof = transition.prove_catalog_component_transition(
        components=components,
        original_component_sha256=original_hash,
        original_catalog_text=old,
        current_catalog_text=current,
    )
    assert proof["original_component_sha256"] == original_hash
    assert proof["current_component_sha256"] == content_hash(components)
    assert proof["added_model_ids"] == ["new"] and len(proof["changed_paths"]) == 3
    assert components == before and proof["new_grade_or_credit_granted"] is False


@pytest.mark.parametrize(
    "bad",
    [
        "source",
        "world_route",
        "cast_result",
        "grade",
        "leaf",
        "missing_leaf",
        "catalog_edit",
        "catalog_remove",
        "catalog_duplicate",
        "policy",
    ],
)
def test_any_other_change_fails_closed(bad):
    old, current, components, original_hash = case()
    if bad == "source":
        components["sources"]["book_spec"]["body"] += "changed"
    if bad == "world_route":
        components["legacy_source_reviews"]["world"]["effective_input_sha256"] = "other"
    if bad == "cast_result":
        components["legacy_source_reviews"]["cast"]["raw_sha256"] = "new"
    if bad == "grade":
        components["unchanged_component_axes"]["grade"] = "pass"
    if bad == "leaf":
        components["unchanged_component_axes"]["catalog_sha256"] = "unknown"
    if bad == "missing_leaf":
        del components["legacy_source_reviews"]["cast"]
    data = yaml.safe_load(current)
    if bad == "catalog_edit":
        data["models"][0]["api_base"] = "https://other.invalid/v1"
    if bad == "catalog_remove":
        data["models"] = data["models"][1:]
    if bad == "catalog_duplicate":
        data["models"].append(data["models"][0])
    if bad == "policy":
        data["fallback"] = "new"
    if bad.startswith("catalog") or bad == "policy":
        current = yaml.safe_dump(data)
    with pytest.raises(ValueError):
        transition.prove_catalog_component_transition(
            components=components,
            original_component_sha256=original_hash,
            original_catalog_text=old,
            current_catalog_text=current,
        )


@pytest.mark.asyncio
async def test_missing_transition_never_accepts_hash_difference():
    _, _, components, original_hash = case()

    class Session:
        async def get(self, *args, **kwargs):
            return None

    with pytest.raises(ValueError, match="receipt_missing_or_scope"):
        await transition.require_catalog_component_transition(
            Session(),
            project=SimpleNamespace(id=uuid4()),
            review=SimpleNamespace(
                id=uuid4(), metadata_json={"source_component_receipt_hash": original_hash}
            ),
            components=components,
        )


from test_autonomous_continuation import db as db  # noqa: E402,F401


@pytest.mark.asyncio
async def test_explicit_transition_reopens_in_real_sqlite_without_mutating_admission(
    db, tmp_path, monkeypatch
):
    from bestseller.infra.db.models import WorkflowRunModel
    from bestseller.services import model_catalog

    old, current, components, original_hash = case()
    path = tmp_path / "catalog.yaml"
    path.write_text(current)
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", path)
    pid = UUID(components["project_id"])
    rid = uuid4()
    async with db() as session:
        review = WorkflowRunModel(
            id=rid,
            project_id=pid,
            workflow_type="review_volume_source_admission",
            scope_type="project",
            scope_id=pid,
            status="completed",
            metadata_json={
                "source_dependency_revision_id": components["revision_id"],
                "source_component_receipt_hash": original_hash,
                "original_grade": "unchanged",
            },
        )
        session.add(review)
        await session.commit()
        before = deepcopy(review.metadata_json)
        with pytest.raises(ValueError, match="explicit_authority_required"):
            await transition.record_catalog_component_transition(
                session,
                project_id=pid,
                review_id=rid,
                components=components,
                original_catalog_text=old,
                actor="test-operator",
                authorization_ref="explicit fixture instruction",
            )
        result = await transition.record_catalog_component_transition(
            session,
            project_id=pid,
            review_id=rid,
            components=components,
            original_catalog_text=old,
            actor="test-operator",
            authorization_ref="explicit fixture instruction",
            explicit_user_approval=True,
        )
        await session.commit()
        assert review.metadata_json == before
    async with db() as reopened:
        review = await reopened.get(WorkflowRunModel, rid)
        assert (
            await transition.require_catalog_component_transition(
                reopened, project=SimpleNamespace(id=pid), review=review, components=components
            )
            == original_hash
        )
        same = await transition.record_catalog_component_transition(
            reopened,
            project_id=pid,
            review_id=rid,
            components=components,
            original_catalog_text=old,
            actor="test-operator",
            authorization_ref="explicit fixture instruction",
            explicit_user_approval=True,
        )
        assert same == result and review.metadata_json == before
        approval = await reopened.get(WorkflowRunModel, UUID(result["approval_id"]))
        approval.status = "cancelled"
        await reopened.commit()
        with pytest.raises(ValueError, match="approval_scope"):
            await transition.require_catalog_component_transition(
                reopened, project=SimpleNamespace(id=pid), review=review, components=components
            )


@pytest.mark.asyncio
async def test_real_volume_input_keeps_exact_paid_view_after_approved_catalog_addition(
    db, tmp_path, monkeypatch
):
    """Physical proof construction is fixture-only; adapter and DB approval are real."""
    from sqlalchemy import MetaData, JSON, ForeignKeyConstraint
    from sqlalchemy.dialects.postgresql import JSONB
    from bestseller.infra.db.models import (
        PlanningArtifactVersionModel as Artifact,
        WorkflowRunModel,
    )
    from bestseller.services import (
        volume_source_admission_input as adapter,
        foundation_revision_selection as sources,
        model_catalog,
    )
    from test_autonomous_continuation import seed

    table = Artifact.__table__.to_metadata(MetaData())
    for col in table.columns:
        if isinstance(col.type, JSONB):
            col.type = JSON()
            col.server_default = None
    for constraint in list(table.constraints):
        if isinstance(constraint, ForeignKeyConstraint):
            table.constraints.remove(constraint)
    async with db.kw["bind"].begin() as connection:
        await connection.run_sync(table.create)
    old, current, _, _ = case()
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(old)
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog)
    async with db() as session:
        project, _, _ = await seed(session)
        project.metadata_json = {**project.metadata_json, "book_design_snapshot": {}}
        root = WorkflowRunModel(
            id=uuid4(),
            project_id=project.id,
            workflow_type="creation_source_revision",
            scope_type="project",
            scope_id=project.id,
            status="completed",
            metadata_json={"existing_prior_unknown_retained": {"unresolved": "Unknown"}},
        )
        revision = WorkflowRunModel(
            id=uuid4(),
            project_id=project.id,
            workflow_type="creation_source_dependency_revision",
            scope_type="project",
            scope_id=project.id,
            status="completed",
            metadata_json={"candidate_origin_revision_ids": {"cast_spec": str(root.id)}},
        )
        canonical = Artifact(
            id=uuid4(),
            project_id=project.id,
            artifact_type="book_spec",
            version_no=9,
            status="approved",
            schema_version="fixture",
            content={"retained_planning_clauses": []},
        )
        assets = {
            kind: Artifact(
                id=uuid4(),
                project_id=project.id,
                artifact_type=kind,
                version_no=1,
                status="candidate",
                schema_version="fixture",
                content={"kind": kind},
            )
            for kind in ("book_spec", "world_spec", "cast_spec")
        }
        volume = Artifact(
            id=uuid4(),
            project_id=project.id,
            artifact_type="volume_plan",
            version_no=1,
            status="candidate",
            schema_version="fixture",
            content=[{"volume_number": 1, "title": "无关故事的完整结尾"}],
        )
        components = {
            "schema": "verified-volume-source-components.v1",
            "project_id": str(project.id),
            "revision_id": str(revision.id),
            "canonical_snapshot_id": str(canonical.id),
            "canonical_sha256": content_hash(canonical.content),
            "sources": {
                kind: {"artifact_id": str(row.id), "content_hash": content_hash(row.content)}
                for kind, row in assets.items()
            },
            "legacy_source_reviews": {
                kind: {
                    "workflow_id": str(uuid4()),
                    "llm_run_id": str(uuid4()),
                    "report_sha256": "retained-report",
                    "catalog_sha256": transition.sha(old),
                }
                for kind in ("world", "cast")
            },
            "unchanged_component_axes": {
                "report_id": str(uuid4()),
                "run_id": str(uuid4()),
                "passed_axes": ["unchanged"],
                "original_decision": "revise",
                "report_sha256": "original-negative",
                "catalog_sha256": transition.sha(old),
            },
        }
        review = WorkflowRunModel(
            id=uuid4(),
            project_id=project.id,
            workflow_type="review_volume_source_admission",
            scope_type="project",
            scope_id=project.id,
            status="completed",
            metadata_json={
                "source_boundary_policy": "reference-only.v1",
                "single_dispatch_authorization_id": "fixture-original",
                "source_dependency_revision_id": str(revision.id),
                "volume_candidate_id": str(volume.id),
                "source_component_receipt_hash": content_hash(components),
                "volume_content_hash": content_hash(volume.content),
                "prior_diagnostics_hash": content_hash(None),
            },
        )
        session.add_all([project, root, revision, canonical, volume, review, *assets.values()])
        await session.commit()

        async def proved(*args, **kwargs):
            return deepcopy(components)

        monkeypatch.setattr(sources, "validate_volume_foundation_components", proved)
        kwargs = dict(
            project=project,
            workflow_run_id=review.id,
            candidate_payload={"volumes": volume.content},
            commercial_report=None,
            settings=object(),
            **{kind: row.content for kind, row in assets.items()},
        )
        baseline = await adapter.build_volume_admission_input(session, **kwargs)
        old_meta = deepcopy(review.metadata_json)
        catalog.write_text(current)
        for path in transition.PATHS:
            node = components
            for key in path:
                node = node[key]
            node["catalog_sha256"] = transition.sha(current)
        with pytest.raises(ValueError, match="receipt_missing_or_scope"):
            await adapter.build_volume_admission_input(session, **kwargs)
        await transition.record_catalog_component_transition(
            session,
            project_id=project.id,
            review_id=review.id,
            components=components,
            original_catalog_text=old,
            actor="fixture-operator",
            authorization_ref="explicit fixture instruction",
            explicit_user_approval=True,
        )
        await session.commit()
        new = await adapter.build_volume_admission_input(session, **kwargs)
        assert new == baseline and content_hash(new) == content_hash(baseline)
        assert review.metadata_json == old_meta
        components["sources"]["book_spec"]["content_hash"] = "other-body"
        with pytest.raises(ValueError):
            await adapter.build_volume_admission_input(session, **kwargs)
