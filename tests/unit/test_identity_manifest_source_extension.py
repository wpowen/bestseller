"""Pure migration checks and real isolated SQLite persistence/readback."""

import copy
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel
from bestseller.services.identity_manifest_migration import (
    apply_identity_manifest_extension, identity_manifest_hash, propose_identity_manifest_extension,
)
from bestseller.services.workflows import ensure_project_identity_manifest

pytestmark = pytest.mark.unit


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


def _source():
    return {"id": str(uuid4()), "project_id": str(uuid4()), "artifact_type": "cast_spec",
            "version_no": 3, "status": "approved", "content": {
                "protagonist": {"name": "Mara", "role": "protagonist", "gender": "female",
                                "social_network": {"superiors": [{"name": "The watchkeeper"}]}},
                "conflict_map": [{"character_a": "Mara", "character_b": "The watchkeeper",
                                  "conflict_type": "control of the register"}],
            }}


def _metadata():
    return {"identity_manifest_status": "locked", "unrelated": {"keep": [1, 2]},
            "identity_manifest": [{"name": "Mara", "role": "protagonist", "gender": "female",
                                   "aliases": [], "lock_evidence": "original-cast"}]}


def _proposal(metadata, source):
    return propose_identity_manifest_extension(
        metadata["identity_manifest"], project_id=source["project_id"], cast_source=source,
    )


def _apply(metadata, proposal, source, **changes):
    kwargs = {"cast_source": source, "expected_source_hash": proposal["source"]["content_hash"],
              "actor": "unit-operator", "evidence": {"inspection": "fixture source checked"}}
    kwargs.update(changes)
    return apply_identity_manifest_extension(metadata, proposal, **kwargs)


def test_proposal_apply_archives_exact_original_and_is_idempotent():
    source, metadata = _source(), _metadata()
    before = copy.deepcopy((source, metadata))
    proposal = _proposal(metadata, source)
    assert proposal["candidate_manifest"][0] == metadata["identity_manifest"][0]
    assert [entry["name"] for entry in proposal["added_entries"]] == ["The watchkeeper"]
    result = _apply(metadata, proposal, source)
    assert result["identity_manifest_status"] == "locked"
    assert result["unrelated"] == metadata["unrelated"]
    receipt = result["identity_manifest_migration_receipts"][0]
    assert receipt["original_manifest"] == metadata["identity_manifest"]
    assert receipt["source"]["artifact_id"] == source["id"]
    assert receipt["source"]["content_hash"] == identity_manifest_hash(source["content"])
    assert _apply(result, proposal, source) == result
    assert _proposal(result, source)["status"] == "unchanged"
    assert (source, metadata) == before


@pytest.mark.parametrize("field,value", [
    ("status", "needs_review"), ("project_id", "another-project"),
    ("artifact_type", "world_spec"), ("id", ""), ("version_no", True),
])
def test_proposal_refuses_unapproved_or_unbound_sources(field, value):
    source, metadata = _source(), _metadata()
    project_id = source["project_id"]
    source[field] = value
    with pytest.raises(ValueError, match="approved Cast artifact"):
        propose_identity_manifest_extension(metadata["identity_manifest"], project_id=project_id, cast_source=source)


@pytest.mark.parametrize("changed", ["source", "manifest", "candidate", "actor", "evidence"])
def test_apply_rejects_stale_source_tampering_and_missing_operator_evidence(changed):
    source, metadata = _source(), _metadata()
    proposal = _proposal(metadata, source)
    if changed == "source":
        source["content"]["protagonist"]["social_network"]["superiors"][0]["name"] = "Other role"
    elif changed == "manifest":
        metadata["identity_manifest"][0]["gender"] = "unknown"
    elif changed == "candidate":
        proposal["candidate_manifest"][0]["name"] = "Renamed person"
    overrides = {"actor": ""} if changed == "actor" else {"evidence": {}} if changed == "evidence" else {}
    before = copy.deepcopy(metadata)
    with pytest.raises(ValueError):
        _apply(metadata, proposal, source, **overrides)
    assert metadata == before


def test_source_alias_collision_never_renames_or_updates_existing_locks():
    source, metadata = _source(), _metadata()
    source["content"]["protagonist"]["name"] = "Another spelling"
    source["content"]["protagonist"]["aliases"] = ["Mara"]
    proposal = _proposal(metadata, source)
    assert proposal["candidate_manifest"][0] == metadata["identity_manifest"][0]
    assert "Another spelling" not in [e["name"] for e in proposal["added_entries"]]


class _AsyncSQLiteFacade:
    """Real SQLite SQL/ORM with an async facade; no external connections."""

    def __init__(self, session):
        self.session = session

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def flush(self):
        self.session.flush()


@pytest.mark.asyncio
async def test_entry_guard_persists_only_approved_names_and_readback_keeps_old_source(tmp_path):
    source, metadata = _source(), _metadata()
    original = copy.deepcopy(metadata)
    engine = create_engine(f"sqlite:///{tmp_path / 'identity_extension_test.sqlite3'}")
    try:
        with engine.begin() as connection:
            for model in (ProjectModel, PlanningArtifactVersionModel):
                ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
                connection.exec_driver_sql(ddl.replace("'{}'::jsonb", "'{}'"))
        with Session(engine, expire_on_commit=False) as db:
            project = ProjectModel(id=uuid4(), slug="identity-extension-test", title="Test",
                                   genre="space opera", target_chapters=9, target_word_count=20000,
                                   metadata_json=metadata)
            db.add(project)
            approved = PlanningArtifactVersionModel(
                id=uuid4(), project_id=project.id, artifact_type="cast_spec", version_no=3,
                status="approved", schema_version="1.0", content=source["content"], created_by="fixture",
            )
            draft_body = copy.deepcopy(source["content"])
            draft_body["protagonist"]["social_network"]["superiors"][0]["name"] = "Unapproved rival"
            draft = PlanningArtifactVersionModel(
                id=uuid4(), project_id=project.id, artifact_type="cast_spec", version_no=4,
                status="needs_review", schema_version="1.0", content=draft_body, created_by="fixture",
            )
            db.add_all([approved, draft])
            db.commit()
            facade = _AsyncSQLiteFacade(db)
            expected = identity_manifest_hash(approved.content)
            with pytest.raises(ValueError, match="source hash changed"):
                await ensure_project_identity_manifest(
                    facade, project, project_slug=project.slug, expected_cast_source_hash="stale",
                )
            assert project.metadata_json == original
            first = await ensure_project_identity_manifest(
                facade, project, project_slug=project.slug, expected_cast_source_hash=expected,
                migration_actor="fixture-operator", migration_evidence={"inspection": "fixture only"},
            )
            db.commit()
            db.expire_all()
            saved = db.scalar(select(ProjectModel).where(ProjectModel.id == project.id))
            assert saved.metadata_json["identity_manifest"] == first
            assert [entry["name"] for entry in first] == ["Mara", "The watchkeeper"]
            assert saved.metadata_json["identity_manifest_migration_receipts"][0]["original_manifest"] == original["identity_manifest"]
            snapshot = copy.deepcopy(saved.metadata_json)
            assert await ensure_project_identity_manifest(facade, saved, project_slug=saved.slug) == first
            db.commit()
            db.expire_all()
            assert db.get(ProjectModel, saved.id).metadata_json == snapshot
            assert db.get(PlanningArtifactVersionModel, approved.id).content == source["content"]
            assert db.get(PlanningArtifactVersionModel, draft.id).content == draft_body
    finally:
        engine.dispose()
