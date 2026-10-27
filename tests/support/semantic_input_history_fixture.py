"""Fake persisted SELECT results for factory history tests; no validation stubs."""
from types import SimpleNamespace
from uuid import UUID

from bestseller.infra.db.models import PlanningArtifactVersionModel


async def history_select(self, statement):
    state = self.__dict__
    assert state.get("autoflush_disabled", state.get("disabled", 0)) > 0
    assert statement.is_select and statement.get_execution_options()["autoflush"] is False
    assert all(d["entity"] is PlanningArtifactVersionModel for d in statement.column_descriptions)
    sql = str(statement)
    assert "WHERE" in sql and "FOR UPDATE" not in sql and "ORDER BY" not in sql and "LIMIT" not in sql
    params = statement.compile().params
    assert "status" not in str(statement.whereclause) and "schema_version" not in str(statement.whereclause)
    project_id = str(params["semantic_parent_project_id"])
    artifact_type = params["semantic_parent_artifact_type"]
    full_text, title = params["semantic_parent_full_text"], params["semantic_parent_title"]
    selected = []
    for row in state.get("semantic_history_fixture", []):
        payload = (row.get("content") or {}).get("request_payload") or {}
        if (str(row["project_id"]) == project_id and row["artifact_type"] == artifact_type
                and payload.get("full_text") == full_text and payload.get("title") == title):
            selected.append(row)
    state["reads"].append(("PlanningArtifactVersionModel", "SELECT_SAME_SOURCE_HISTORY"))
    return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: selected))


def parent_row(project, assembly, *, artifact_id=None, status="failed", error="invalid_quote"):
    """Synthetic failed-v4 content; not a valid paid/native recovery proof."""
    from uuid import uuid4
    return {"id": UUID(str(artifact_id)) if artifact_id else uuid4(), "project_id": project.id,
            "source_run_id": uuid4(),
            "artifact_type": "fanqie_whole_semantic_review", "status": status,
            "schema_version": "fanqie-short-semantic-stage.v4", "content": {
                "schema": "fanqie-short-semantic-stage.v4", "error": error,
                "raw_response": "original invalid quote, never rewritten",
                "request_payload": {"schema": "fanqie-short-semantic-stage.v4",
                    "project_id": str(project.id), "full_text": assembly.full_text, "title": project.title},
                "retained_payloads": {"fixture_original_nested": [1, {"keep": "all"}]},
            }}
