"""Real PostgreSQL contract tests for the conception approval boundary."""

from __future__ import annotations

from http.client import HTTPConnection
import json
import os
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel
from bestseller.services.concept_contract import build_concept_contract
from bestseller.services.conception_approval_bridge import (
    approve_pending_conception,
    build_conception_options,
)
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.creation_intent_contract import CreationIntentContract
from bestseller.services.genre_intent_contract import GenreIntentContract
import bestseller.web.server as web_server
from bestseller.web.server import _load_project_conception_options_payload, _rebuild_payload_from_db

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _url() -> str:
    value = os.environ.get("BESTSELLER__DATABASE__URL", "")
    if not value or "test" not in value.lower():
        pytest.skip("requires BESTSELLER__DATABASE__URL pointing at bestseller_test")
    return value


def _contract() -> dict[str, object]:
    return build_concept_contract(
        winner={
            "concept": "电话谜案",
            "mechanism": "接到未来电话",
            "hook_question": "谁会死",
            "protagonist_identity": "接线员",
            "protagonist_private_desire": "查明真相",
            "opening_crisis": "死者明日来电",
            "opponent_system": "时间限制",
            "decision_proof": "公开追查",
            "emotional_promise": "逆转命运",
            "repeatable_story_unit": "追查未来来电",
            "unit_families": ["案件"],
            "unit_frequency": "每卷",
            "unit_count_estimate": 10,
            "renewal_sources": ["线索"],
            "accumulation_tracks": ["证据"],
            "phase_transitions": ["升级"],
            "opposing_ecology": ["幕后者"],
            "question_ladder": ["谁来电"],
            "endgame_direction": "阻止死亡",
            "core_abnormality": "未来电话",
            "opening_event": "接到电话",
        },
        story_spine={
            "who": "接线员",
            "wants": "查真相",
            "why_now": "接到电话",
            "against": "时间",
            "question": "谁会死",
        },
        target_chapters=12,
        genre="玄幻",
        sub_genre="悬疑",
    )


@pytest_asyncio.fixture
async def approval_db():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid4()
    other_id = uuid4()
    snapshot_id = uuid4()
    other_snapshot_id = uuid4()
    intent_id = uuid4()
    contract = _contract()
    creation_intent = CreationIntentContract(
        genre_intent=GenreIntentContract(
            genre_key="xuanhuan",
            genre_label="玄幻",
            prompt_pack_key="xuanhuan-power-fantasy",
        ),
        concept_seed="原始用户种子",
        chapter_count=12,
    ).model_dump(mode="json")
    creation_intent_hash = snapshot_hash(creation_intent)
    content = {
        "schema_version": "conception-snapshot.v1",
        "attempt_id": "integration:initial",
        "premise": "接线员追查明日来电",
        "authoritative_seed": "接线员接到死者明天才会拨出的电话",
        "creation_intent_hash": creation_intent_hash,
        "concept_contract": contract,
        "story_spine": {"reader_promise": "阻止尚未发生的死亡"},
    }
    async with factory() as session:
        project = ProjectModel(
            id=project_id,
            slug=f"conception-bridge-{project_id.hex[:12]}",
            title="电话谜案",
            language="zh-CN",
            genre="玄幻",
            target_word_count=30000,
            target_chapters=12,
            status="planning",
            metadata_json={
                "conception_only": True,
                "planning_status": "awaiting_concept_approval",
                "conception_snapshot_id": str(snapshot_id),
                "creation_intent_snapshot_id": str(intent_id),
                "creation_intent_contract_hash": creation_intent_hash,
            },
        )
        other = ProjectModel(
            id=other_id,
            slug=f"conception-other-{other_id.hex[:12]}",
            title="另一案",
            language="zh-CN",
            genre="玄幻",
            target_word_count=30000,
            target_chapters=12,
            status="planning",
            metadata_json={
                "conception_only": True,
                "planning_status": "awaiting_concept_approval",
                "conception_snapshot_id": str(other_snapshot_id),
            },
        )
        snapshot = PlanningArtifactVersionModel(
            id=snapshot_id,
            project_id=project_id,
            artifact_type="conception_snapshot",
            version_no=1,
            status="pending_user_approval",
            schema_version="conception-snapshot.v1",
            content=content,
            idempotency_key=f"{snapshot_id}:snapshot",
            created_by="test",
        )
        intent = PlanningArtifactVersionModel(
            id=intent_id,
            project_id=project_id,
            artifact_type="creation_intent",
            version_no=1,
            status="validated",
            schema_version="creation-intent.v1",
            content=creation_intent,
            idempotency_key=f"{intent_id}:intent",
            created_by="test",
        )
        other_snapshot = PlanningArtifactVersionModel(
            id=other_snapshot_id,
            project_id=other_id,
            artifact_type="conception_snapshot",
            version_no=1,
            status="pending_user_approval",
            schema_version="conception-snapshot.v1",
            content=content,
            idempotency_key=f"{other_snapshot_id}:snapshot",
            created_by="test",
        )
        session.add_all([project, other])
        await session.flush()
        session.add_all([intent, snapshot, other_snapshot])
        await session.commit()
    try:
        yield factory, project_id, snapshot_id, other_id, other_snapshot_id, content, project.slug
    finally:
        async with factory() as session:
            await session.execute(
                delete(ProjectModel).where(ProjectModel.id.in_([project_id, other_id]))
            )
            await session.commit()
        await engine.dispose()


async def test_real_pending_approval_promotes_and_rejects_stale_or_cross_project(approval_db):
    factory, project_id, snapshot_id, other_id, other_snapshot_id, content, _ = approval_db
    async with factory() as session:
        snapshot = await session.get(PlanningArtifactVersionModel, snapshot_id)
        options = build_conception_options(
            project={"slug": "book", "lock_version": 0, "target_chapters": 12},
            snapshot={"id": str(snapshot_id), "status": snapshot.status, "content": content},
        )
        with pytest.raises(ValueError, match="not found for project"):
            await approve_pending_conception(
                session,
                project_id=other_id,
                snapshot_id=snapshot_id,
                expected_lock_version=0,
                source_hash=options["canonical"]["source"]["snapshot_hash"],
                option_id=options["canonical"]["option_id"],
                idempotency_key="cross-project",
                actor="test",
                reason="reject",
            )
        await session.rollback()
        other_snapshot = await session.get(PlanningArtifactVersionModel, other_snapshot_id)
        other_snapshot.status = "validated"
        await session.commit()
        with pytest.raises(ValueError, match="not pending user approval"):
            await approve_pending_conception(
                session,
                project_id=other_id,
                snapshot_id=other_snapshot_id,
                expected_lock_version=0,
                source_hash=options["canonical"]["source"]["snapshot_hash"],
                option_id=options["canonical"]["option_id"],
                idempotency_key="old-snapshot",
                actor="test",
                reason="reject old",
            )
        await session.rollback()
        other_snapshot.status = "pending_user_approval"
        other_snapshot.content = {
            **content,
            "concept_contract": {"schema_version": "concept-contract.v2"},
        }
        other_project = await session.get(ProjectModel, other_id)
        other_project.metadata_json = {
            **other_project.metadata_json,
            "conception_snapshot_id": str(other_snapshot_id),
        }
        await session.commit()
        with pytest.raises(ValueError, match="complete concept contract"):
            incomplete_hash = snapshot_hash(other_snapshot.content)
            await approve_pending_conception(
                session,
                project_id=other_id,
                snapshot_id=other_snapshot_id,
                expected_lock_version=0,
                source_hash=incomplete_hash,
                option_id=f"canonical:{other_snapshot_id}:{incomplete_hash[:16]}",
                idempotency_key="incomplete-v2",
                actor="test",
                reason="reject incomplete",
            )
        await session.rollback()
        result = await approve_pending_conception(
            session,
            project_id=project_id,
            snapshot_id=snapshot_id,
            expected_lock_version=0,
            source_hash=options["canonical"]["source"]["snapshot_hash"],
            option_id=options["canonical"]["option_id"],
            idempotency_key="approve-1",
            actor="test",
            reason="approve",
        )
        await session.commit()
        assert result["status"] == "approved"
        approved = await session.get(PlanningArtifactVersionModel, snapshot_id)
        project = await session.get(ProjectModel, project_id)
        assert approved.status == "canonical"
        assert project.metadata_json["creation_canonical_snapshot_id"] == str(snapshot_id)
        assert project.metadata_json["concept_contract"] == content["concept_contract"]
        assert project.metadata_json["story_spine"] == content["concept_contract"]["story_spine"]
        assert project.metadata_json["creation_intent_contract"]["concept_seed"] == "原始用户种子"

        with pytest.raises(ValueError, match="different approval request"):
            await approve_pending_conception(
                session,
                project_id=project_id,
                snapshot_id=snapshot_id,
                expected_lock_version=1,
                source_hash="changed",
                option_id="changed",
                idempotency_key="approve-1",
                actor="test",
                reason="changed",
            )


async def test_real_rebuild_payload_uses_approved_snapshot_and_keeps_prewrite_unapproved(
    approval_db,
):
    factory, project_id, snapshot_id, _other_id, _other_snapshot_id, content, slug = approval_db
    async with factory() as session:
        project = await session.get(ProjectModel, project_id)
        snapshot = await session.get(PlanningArtifactVersionModel, snapshot_id)
        options = build_conception_options(
            project={"slug": slug, "lock_version": project.lock_version, "target_chapters": 12},
            snapshot={"id": str(snapshot.id), "status": snapshot.status, "content": content},
        )
        await approve_pending_conception(
            session,
            project_id=project_id,
            snapshot_id=snapshot_id,
            expected_lock_version=project.lock_version,
            source_hash=options["canonical"]["source"]["snapshot_hash"],
            option_id=options["canonical"]["option_id"],
            idempotency_key="api-handoff",
            actor="test",
            reason="handoff",
        )
        await session.commit()
    db_url = os.environ["BESTSELLER__DATABASE__URL"]
    settings = SimpleNamespace(
        database=SimpleNamespace(
            url=db_url,
            echo_sql=False,
            pool_size=2,
            max_overflow=0,
            pool_timeout_seconds=10,
            pool_recycle_seconds=300,
            statement_timeout_ms=60000,
            lock_timeout_ms=5000,
            application_name="bestseller-test",
        ),
        output=SimpleNamespace(base_dir="/tmp/bestseller-conception-approval-p1"),
    )
    payload = await _rebuild_payload_from_db(settings, slug)
    assert payload is not None
    assert payload["premise"] == content["premise"]
    assert payload["metadata"]["concept_contract"] == content["concept_contract"]
    assert payload["metadata"]["concept_seed"] == "原始用户种子"
    assert payload["_run_conception"] is False
    assert payload["planning_handoff"] is True
    assert "prewrite_review_approved" not in payload


async def test_options_reads_metadata_current_snapshot_and_validated_original_seed(approval_db):
    factory, project_id, snapshot_id, _other_id, _other_snapshot_id, content, slug = approval_db
    newer_id = uuid4()
    async with factory() as session:
        session.add(
            PlanningArtifactVersionModel(
                id=newer_id,
                project_id=project_id,
                artifact_type="conception_snapshot",
                version_no=2,
                status="pending_user_approval",
                schema_version="conception-snapshot.v1",
                content={**content, "attempt_id": "newer", "premise": "生成的替代梗概"},
                idempotency_key=f"{newer_id}:snapshot",
                created_by="test",
            )
        )
        await session.commit()
    settings = SimpleNamespace(
        database=SimpleNamespace(
            url=os.environ["BESTSELLER__DATABASE__URL"],
            echo_sql=False,
            pool_size=2,
            max_overflow=0,
            pool_timeout_seconds=10,
            pool_recycle_seconds=300,
            statement_timeout_ms=60000,
            lock_timeout_ms=5000,
            application_name="bestseller-options-test",
        ),
        output=SimpleNamespace(base_dir="/tmp/bestseller-conception-approval-p1"),
    )
    result = await _load_project_conception_options_payload(settings, slug)
    assert result["snapshot"]["id"] == str(snapshot_id)
    assert result["authoritative_seed"]["text"] == "原始用户种子"
    assert result["authoritative_seed"]["text"] != "生成的替代梗概"


async def test_real_http_approve_then_enter_writing_enqueues_canonical_payload(
    approval_db, monkeypatch
):
    """Exercise RequestHandler routing against the real DB, without an LLM."""
    _factory, project_id, snapshot_id, _other_id, _other_snapshot_id, content, slug = approval_db

    async def read_options():
        async with _factory() as session:
            project = await session.get(ProjectModel, project_id)
            snapshot = await session.get(PlanningArtifactVersionModel, snapshot_id)
            return build_conception_options(
                project={"slug": slug, "lock_version": project.lock_version, "target_chapters": 12},
                snapshot={"id": str(snapshot.id), "status": snapshot.status, "content": content},
            )

    options = await read_options()
    db_url = os.environ["BESTSELLER__DATABASE__URL"]
    settings = SimpleNamespace(
        database=SimpleNamespace(
            url=db_url,
            echo_sql=False,
            pool_size=2,
            max_overflow=0,
            pool_timeout_seconds=10,
            pool_recycle_seconds=300,
            statement_timeout_ms=60000,
            lock_timeout_ms=5000,
            application_name="bestseller-http-test",
        ),
        output=SimpleNamespace(base_dir="/tmp/bestseller-conception-approval-p1"),
        redis=SimpleNamespace(url="redis://127.0.0.1:63999"),
    )
    captured: dict[str, object] = {}

    class Tasks:
        def __init__(self, *, persist_path):
            self.persist_path = persist_path

        def find_active_task_by_project_slug(self, _slug):
            return None

        def create_autowrite_task(self, payload):
            captured["payload"] = payload
            return {"task_id": "captured-task", "status": "queued", "payload": payload}

        def watchdog_sweep(self, **_kwargs):
            return 0

        def evict_old_tasks(self, **_kwargs):
            return 0

        def sync_progress_from_worker_redis(self, _url):
            return None

    class CapturedServer(web_server._FastThreadingHTTPServer):
        instance = None

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            type(self).instance = self

    monkeypatch.setattr(web_server, "load_settings", lambda: settings)
    monkeypatch.setattr(web_server, "initialize_process_runtime", lambda _settings: None)
    monkeypatch.setattr(web_server, "WebTaskManager", Tasks)
    monkeypatch.setattr(web_server, "_read_reader_html", lambda: "reader")
    monkeypatch.setattr(web_server, "_read_if_reader_html", lambda: "if-reader")
    monkeypatch.setattr(web_server, "_FastThreadingHTTPServer", CapturedServer)
    monkeypatch.setenv("BESTSELLER_AUTO_RESUME_ON_STARTUP", "false")
    monkeypatch.setenv("BESTSELLER_TASK_WATCHDOG_INTERVAL", "3600")
    thread = threading.Thread(
        target=web_server.serve_web_app,
        kwargs={"host": "127.0.0.1", "port": 0, "open_browser": False},
        daemon=True,
    )
    thread.start()
    deadline = time.time() + 10
    while CapturedServer.instance is None and time.time() < deadline:
        time.sleep(0.05)
    assert CapturedServer.instance is not None
    port = CapturedServer.instance.server_port

    def post(path, payload):
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        raw = json.dumps(payload, ensure_ascii=False).encode()
        conn.request("POST", path, body=raw, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        body = json.loads(response.read().decode())
        conn.close()
        return response.status, body

    try:
        status, _body = post(f"/api/projects/{slug}/enter-writing", {})
        assert status == 409
        status, body = post(
            f"/api/projects/{slug}/conception/approve",
            {
                "snapshot_id": str(snapshot_id),
                "expected_lock_version": 0,
                "source_hash": options["canonical"]["source"]["snapshot_hash"],
                "option_id": options["canonical"]["option_id"],
                "idempotency_key": "http-approval",
            },
        )
        assert status == 200 and body["ok"] is True
        status, body = post(f"/api/projects/{slug}/enter-writing", {})
        assert status == 202 and body["task_id"] == "captured-task"
        task_payload = captured["payload"]
        assert task_payload["_run_conception"] is False
        assert task_payload["premise"] == content["premise"]
        assert task_payload["metadata"]["concept_contract"] == content["concept_contract"]
        assert "prewrite_review_approved" not in task_payload
    finally:
        CapturedServer.instance.shutdown()
        CapturedServer.instance.server_close()
        thread.join(timeout=5)
