"""Whole-objective control wiring via public entry, adapters, SQL and export.
Planning/admission/writer/qualification boundaries are faked; this is not a full
normal-service integration or financial gateway acceptance.

Not a real novel, paid grade, or production acceptance. All sockets forbidden.
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
import json
import pytest
import jsonschema
from sqlalchemy import MetaData, JSON, ForeignKeyConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from test_pending_manuscript_continuation import db as db, seed, Queue
from bestseller.infra.db import models as m
from bestseller.services import book_run as goal, book_run_handlers, draft_continuity as dc
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings, LLMRoleSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket

    def no(*a, **kw):
        raise AssertionError("Whole-book offline rehearsal forbids network")

    monkeypatch.setattr(socket.socket, "connect", no)
    monkeypatch.setattr(socket, "create_connection", no)


@pytest.fixture
async def full(db, monkeypatch, tmp_path):
    from bestseller.services import (
        chapter_outline_readiness as ready,
        planning_continuity_context as context,
        prefix_source_proposal as proposal,
        workflows,
        creation_configuration as config,
        prefix_window_activation as activation,
        drafts,
        planner,
        foundation_selection,
        book_native,
        draft_continuity_applicable_review as app,
        draft_continuity_supplement as supplement,
    )
    from bestseller.services.rolling_outline import (
        build_macro_plan,
        build_rolling_outline_plan,
        rolling_window_schedule_hash,
    )
    from bestseller.services.quality_report_claim import graded_text_fingerprint

    async with db() as s:
        for model in (
            m.PlanningArtifactVersionModel,
            m.ChapterQualityReportModel,
            m.ExportArtifactModel,
        ):
            table = model.__table__.to_metadata(MetaData())
            for c in table.columns:
                if isinstance(c.type, JSONB):
                    c.type = JSON()
                    c.server_default = None
            for c in list(table.constraints):
                if isinstance(c, ForeignKeyConstraint):
                    table.constraints.remove(c)
            async with s.bind.begin() as conn:
                await conn.run_sync(table.create)
        p = await seed(s, False)
        p.target_chapters = 20
        p.target_word_count = 40000
        macro = build_macro_plan(
            [{"chapter_number": n, "anchor": f"独立测试锚点{n}"} for n in range(1, 21)]
        )
        rolling = build_rolling_outline_plan(
            macro,
            current_state_snapshot={"current_chapter": 0},
            next_macro_anchor=macro.slots[6].to_dict(),
            source_snapshot_hash="a" * 64,
            window_start=1,
            window_end=6,
            window_size=6,
        )
        windows = [
            {"volume_number": 1, "window_index": i, "window_start": a, "window_end": b}
            for i, (a, b) in enumerate([(1, 6), (7, 12), (13, 20)], 1)
        ]
        p.metadata_json = {
            **p.metadata_json,
            "macro_outline_plan": macro.to_dict(),
            "rolling_outline_plan": rolling.to_dict(),
            "rolling_outline_windows": windows,
            "rolling_outline_windows_hash": rolling_window_schedule_hash(windows),
            "book_design_snapshot_hash": "a" * 64,
        }
        source = {
            "chapters": [
                {"chapter_number": n, "volume_number": 1, "title": f"测试章{n}"}
                for n in range(1, 7)
            ]
        }
        artifact = m.PlanningArtifactVersionModel(
            id=uuid4(),
            project_id=p.id,
            artifact_type="chapter_outline_batch",
            version_no=1,
            status="approved",
            schema_version="1",
            content=source,
            created_by="fixture",
        )
        parent = m.WorkflowRunModel(
            id=uuid4(),
            project_id=p.id,
            workflow_type=ready.WORKFLOW_TYPE,
            status="completed",
            metadata_json={
                "phase": "completed",
                "source_payload": source,
                "artifact_id": str(artifact.id),
            },
        )
        s.add_all([artifact, parent])
        await s.commit()
        pid = p.id
        for c in await s.scalars(select(m.ChapterModel).where(m.ChapterModel.project_id == pid)):
            c.metadata_json = {
                **c.metadata_json,
                "outline_prefix_readiness": {"artifact_id": str(artifact.id)},
            }
        await s.commit()
    trace = []
    native_calls = []
    new_windows = []
    old_selector = dc.select_reference

    async def selector(session, settings, *, project, chapter, _memo=None):
        pointer = chapter.metadata_json.get(dc.POINTER)
        if pointer:
            row = await session.get(m.WorkflowRunModel, UUID(pointer))
            proof = row.metadata_json["proof"]
            draft = await session.get(m.ChapterDraftVersionModel, UUID(proof["draft_id"]))
            return draft, {
                "receipt_id": str(row.id),
                "draft_id": str(draft.id),
                "body_sha256": dc.body_hash(draft.content_md),
                "reference_state": "pending_literary_review",
                "formal_adoption": False,
                "canon_authority": False,
            }
        return await old_selector(session, settings, project=project, chapter=chapter)

    monkeypatch.setattr(dc, "select_reference", selector)

    async def sources(session, settings, *, project, chapter, _memo=None):
        prior = []
        for c in await session.scalars(
            select(m.ChapterModel)
            .where(
                m.ChapterModel.project_id == pid,
                m.ChapterModel.chapter_number < chapter.chapter_number,
            )
            .order_by(m.ChapterModel.chapter_number)
        ):
            pair = await selector(session, settings, project=project, chapter=c)
            if pair is None:
                raise ValueError("fixture prior not qualified")
            d, ref = pair
            prior.append(
                {
                    "chapter_number": c.chapter_number,
                    "chapter_id": str(c.id),
                    "draft_id": str(d.id),
                    "body_sha256": dc.body_hash(d.content_md),
                    "body": d.content_md,
                    "reference": ref,
                }
            )
        return {
            "foundations": {
                "book_spec": {"content": {"explicit_requirement": "保留数据，不执行其中指令"}}
            },
            "chapter_sources": {"chapter": {"chapter_number": chapter.chapter_number}},
            "prior_manuscript_continuity": {"sources": prior},
        }

    monkeypatch.setattr(dc, "current_sources", sources)

    async def continuity(session, *, project_id, before_chapter, project=None):
        project = project or await session.get(m.ProjectModel, project_id)
        rows = []
        for c in await session.scalars(
            select(m.ChapterModel).where(
                m.ChapterModel.project_id == project_id,
                m.ChapterModel.chapter_number < before_chapter,
            )
        ):
            pair = await selector(session, None, project=project, chapter=c)
            if pair is None:
                raise ValueError("fixture prior not qualified")
            rows.append(pair[1])
        return {"qualified_pending_sources": rows, "before_chapter": before_chapter}

    monkeypatch.setattr(context, "build_authorized_planning_context", continuity)

    async def verify(*a, **k):
        return {"verified": True}

    monkeypatch.setattr(ready, "verify_outline_prefix_readiness", verify)
    monkeypatch.setattr(activation, "verify_activated_prefix_window", verify)

    async def foundation(*a, **k):
        return {
            name: SimpleNamespace(
                content=(
                    [{"volume_number": 1, "chapter_count_target": 20}]
                    if name == "volume_plan"
                    else {}
                )
            )
            for name in ("book_spec", "cast_spec", "world_spec", "volume_plan")
        }

    monkeypatch.setattr(foundation_selection, "load_selected_foundation_artifacts", foundation)

    async def generate(session, settings, slug, volume, **kw):
        project = await session.get(m.ProjectModel, pid)
        rolling = project.metadata_json["rolling_outline_plan"]
        a, b = rolling["window_start"], rolling["window_end"]
        new_windows.append((a, b))
        payload = {
            "chapters": [
                {"chapter_number": n, "volume_number": 1, "title": f"测试章{n}"}
                for n in range(a, b + 1)
            ]
        }
        artifact = m.PlanningArtifactVersionModel(
            id=uuid4(),
            project_id=pid,
            artifact_type="chapter_outline_batch",
            version_no=a,
            status="approved",
            schema_version="1",
            content=payload,
            created_by="fixture",
        )
        session.add(artifact)
        await session.flush()
        # The real producer retains the exact pre-enrichment candidate and its
        # matching verdict. Exercise that handoff contract, not a fabricated
        # paid pass or a metadata-free replacement of the original candidate.
        wid = uuid4()
        session.add(m.WorkflowRunModel(id=wid,project_id=pid,workflow_type="volume_plan",
            status="completed",metadata_json={"fixture_only":True}))
        from bestseller.services.outline_recovery_checkpoint import checkpoint_outline_candidate, recovery_binding
        report={"passed":False,"overall_score":0.55,"graded_outline_hash":content_hash(payload),
            "outline_repair_dependency_hash":recovery_binding(project.metadata_json,1,[a,b])["dependency_hash"]}
        await checkpoint_outline_candidate(session,project=project,workflow_run_id=wid,
            volume_number=1,candidate=payload,report=report)
        return SimpleNamespace(
            artifacts=[
                SimpleNamespace(artifact_type="chapter_outline_batch", artifact_id=artifact.id)
            ],
            workflow_run_id=wid,
        )

    monkeypatch.setattr(planner, "generate_volume_plan", generate)

    async def register(*a, **k):
        return {}

    monkeypatch.setattr(proposal, "register_prefix_source_proposal", register)

    async def admit(
        session, settings, *, project, source_payload, from_chapter, through_chapter, **kw
    ):
        trace.append(("admit", from_chapter))
        artifact = m.PlanningArtifactVersionModel(
            id=uuid4(),
            project_id=pid,
            artifact_type="chapter_outline_batch",
            version_no=100 + from_chapter,
            status="approved",
            schema_version="1",
            content={
                "chapters": [
                    c
                    for c in source_payload["chapters"]
                    if from_chapter <= c["chapter_number"] <= through_chapter
                ]
            },
            created_by="fixture",
        )
        session.add(artifact)
        await session.flush()
        # The real admission producer retains its complete source_payload, including
        # the unreviewed tail. Later chapters reuse it rather than regenerating a window.
        session.add(
            m.WorkflowRunModel(
                id=uuid4(),
                project_id=pid,
                workflow_type=ready.WORKFLOW_TYPE,
                status="completed",
                metadata_json={
                    "phase": "completed",
                    "source_payload": deepcopy(source_payload),
                    "artifact_id": str(artifact.id),
                },
            )
        )
        await session.flush()
        return {
            "draft_eligible": True,
            "artifact_id": str(artifact.id),
            "chapter_numbers": list(range(from_chapter, through_chapter + 1)),
        }

    monkeypatch.setattr(ready, "approve_chapter_outline_prefix", admit)

    async def materialize(session, slug, batch, **kw):
        for outline in batch.chapters:
            session.add(
                m.ChapterModel(
                    id=uuid4(),
                    project_id=pid,
                    volume_id=uuid4(),
                    chapter_number=outline.chapter_number,
                    title=outline.title or "合成测试章",
                    chapter_goal="目标",
                    status="planned",
                    metadata_json={
                        "outline_prefix_readiness": {"artifact_id": str(kw["source_artifact_id"])}
                    },
                )
            )
        await session.flush()
        return SimpleNamespace(model_dump=lambda **k: {"materialized": True})

    monkeypatch.setattr(workflows, "materialize_chapter_outline_batch", materialize)
    monkeypatch.setattr(
        config,
        "configuration_view",
        lambda p: {"configuration": {}, "configuration_hash": "a", "execution_window_hash": "b"},
    )

    async def save_config(*a, **k):
        return {"status": "saved"}

    monkeypatch.setattr(config, "save_project_configuration", save_config)

    async def activate(*a, **k):
        return {"activated": True}

    monkeypatch.setattr(activation, "activate_verified_prefix_window", activate)

    async def writer(session, slug, n, **kw):
        trace.append(("write", n))
        c = await session.scalar(
            select(m.ChapterModel).where(
                m.ChapterModel.project_id == pid, m.ChapterModel.chapter_number == n
            )
        )
        text = (
            f"# 合成流程测试第{n}章\n\n"
            + ("河桥边的测试事件持续向前，每段只为离线控制链验证。" * 80)
            + ("主要冲突结束，全书完。" if n == 20 else "")
        )
        d = m.ChapterDraftVersionModel(
            id=uuid4(),
            project_id=pid,
            chapter_id=c.id,
            version_no=1,
            content_md=text,
            word_count=len(text),
            is_current=True,
            promotion_state="candidate",
            assembled_from_scene_draft_ids=[],
        )
        session.add(d)
        await session.flush()
        return d

    monkeypatch.setattr(drafts, "generate_chapter_draft_once", writer)

    async def gate(session, project, chapter_number, content, **kw):
        c = await session.scalar(
            select(m.ChapterModel).where(
                m.ChapterModel.project_id == pid, m.ChapterModel.chapter_number == chapter_number
            )
        )
        qr = m.ChapterQualityReportModel(
            id=uuid4(),
            chapter_id=c.id,
            report_json={
                "graded": graded_text_fingerprint(content),
                **kw["extra_report_payload"],
                "blocking_codes": [],
            },
            blocks_write=False,
        )
        session.add(qr)
        await session.flush()
        return "ok"

    async def duplicate(*a, **k):
        return []

    monkeypatch.setattr(drafts, "_evaluate_chapter_quality_gate", gate)
    monkeypatch.setattr(drafts, "_collect_post_assembly_duplicate_findings", duplicate)

    async def plan_for(session, settings, *, project, chapter):
        return {"artifact_id": chapter.metadata_json["outline_prefix_readiness"]["artifact_id"]}

    monkeypatch.setattr(supplement, "applicable_plan", plan_for)

    def prepare(settings, *, project, draft, sources, manifest):
        from bestseller.services import llm

        wid = uuid5(NAMESPACE_URL, str(draft.id) + content_hash(manifest))
        req = app.request_for(
            project=project, workflow_id=wid, draft=draft, sources=sources, manifest=manifest
        )
        route = LLMRoleSettings(
            model="openai/MiniMax-M3.1-Flash",
            api_base="https://api.minimaxi.com/v1",
            temperature=0.25,
            max_tokens=6500,
            timeout_seconds=20,
        )
        digest = llm._effective_input_hash(req, route)
        return wid, req, route, digest

    monkeypatch.setattr(app, "prepare_request", prepare)
    from bestseller.services import llm

    async def completion(session, settings, request):
        # This gateway return is synthetic; other service fakes are explicit above.
        native_calls.append(request.prompt_template)
        return SimpleNamespace(llm_run_id=uuid4())

    monkeypatch.setattr(llm, "complete_text", completion)

    async def paid(*a, **kw):
        body = kw["draft"].content_md
        src = kw["sources"]
        bids = [a["id"] for a in dc.build_critic_body_anchors(body)]
        sid = dc.source_catalog(src)[0]["id"]
        report = {
            "schema": dc.POLICY,
            "body_sha256": dc.body_hash(body),
            "source_sha256": content_hash(src),
            "checked_body_ids": bids,
            "checks": [
                {
                    "axis": axis,
                    "status": "pass",
                    "coverage": "complete",
                    "body_ids": bids[:1],
                    "source_ids": [sid],
                    "explanation": "Synthetic offline model boundary, not real safety acceptance.",
                }
                for axis in dc.AXES
            ],
            "blocking_issues": [],
            "unknown_issues": [],
            "submission_complete": "all_applicable_checks_examined",
        }
        dc.parse_safety_response(json.dumps(report), body=body, sources=src)
        return report, {"fixture_only": True}, content_hash(report)

    monkeypatch.setattr(app, "verify_paid", paid)

    async def native(
        session, settings, *, project, template, payload, schema, role="critic", allow_generate=True
    ):
        wid = uuid5(NAMESPACE_URL, template + content_hash(payload))
        saved = await session.get(m.WorkflowRunModel, wid)
        if saved is None:
            if not allow_generate:
                raise ValueError("book_native_existing_evidence_required")
            native_calls.append(template)
            if template == "book_provisional_knowledge":
                data = {
                    "body_sha256": payload["body_sha256"],
                    "complete": True,
                    "checked_body_ids": [a["id"] for a in payload["catalog"]],
                    "claims": [
                        {"text": "合成来源摘要", "start": 0, "end": min(16, len(payload["body"]))}
                    ],
                }
            else:
                data = {
                    "manifest_sha256": payload["manifest_sha256"],
                    "ending_complete": True,
                    "repair_targets": [],
                    "hard_blockers": [],
                    "unknown": [],
                    "debts": ["offline literary debt retained"],
                }
            jsonschema.validate(data, schema)
            saved = m.WorkflowRunModel(
                id=wid,
                project_id=pid,
                workflow_type="book_native_evidence",
                status="completed",
                metadata_json={"fixture_only": True, "data": data},
            )
            session.add(saved)
            await session.commit()
        return saved.metadata_json["data"], {"workflow_id": str(wid), "fixture_only": True}

    monkeypatch.setattr(book_native, "call", native)
    # The exporter itself is real; deterministic style gates are outside this control rehearsal.
    import bestseller.services.exports as ex

    monkeypatch.setattr(ex, "_closure_draft_blockers", lambda *a, **kw: [])
    settings = load_settings()
    settings.output.base_dir = str(tmp_path / "output")
    yield SimpleNamespace(
        db=db, pid=pid, settings=settings, trace=trace, calls=native_calls, windows=new_windows
    )


async def permit(*args):
    return True, None


async def test_normal_entry_twenty_chapter_goal_exports_and_new_session_reopens(full):
    from bestseller.api.routers.pipelines import start_autowrite
    from bestseller.api.schemas.tasks import AutowriteRequest

    async with full.db() as s:
        p = await s.get(m.ProjectModel, full.pid)
        # Real public API route records the durable objective without SEND authority.
        response = await start_autowrite(
            p.slug,
            AutowriteRequest(autonomous_book=True, requested_model="openai/MiniMax-M3.1-Flash"),
            s,
            full.settings,
            Queue(),
            "fixture-key",
            None,
        )
        assert response.status == "awaiting_authority"
        rid = UUID(response.task_id)
        row = await s.get(m.WorkflowRunModel, rid)
        # Synthetic authorization enables only this offline controller; no API egress.
        row.status = "pending"
        await s.commit()
        from bestseller.services.minimax_book_cost_contract import CONTRACT_SHA256
        from bestseller.services.finite_book_budget import install

        b = await s.get(m.ExecutionBudgetModel, "scope-untouched")
        b.metadata_json = install(
            b.metadata_json,
            run_id=rid,
            project_id=p.id,
            model="openai/MiniMax-M3.1-Flash",
            endpoint="https://api.minimaxi.com/v1",
            caps={
                "calls": 160,
                "money_micro_cny": 50000000,
                "primary": 130,
                "hard_repair": 20,
                "transport_retry": 10,
            },
            counter_sha256=CONTRACT_SHA256,
            authorization_ref="offline fixture only",
            explicit_approval=True,
        )
        await s.commit()
        result = await goal.execute(
            s,
            rid,
            settings=full.settings,
            handlers=book_run_handlers.handlers(full.settings),
            permit=permit,
            owner_id="first",
        )
        assert result["whole_book_complete"] and not result["publication_ready"]
    async with full.db() as fresh:
        row = await fresh.get(m.WorkflowRunModel, rid)
        assert len(row.metadata_json["selected"]) == 20
        first = row.metadata_json["selected"]["1"]
        d = await fresh.get(m.ChapterDraftVersionModel, UUID(first["draft_id"]))
        assert d.version_no == 9 and not d.is_current
        result = await goal.execute(
            fresh,
            rid,
            settings=full.settings,
            handlers=book_run_handlers.handlers(full.settings),
            permit=permit,
            owner_id="reopen",
        )
        assert result["verified"] and result["chapters"] == 20
        assert full.windows == [(7, 12), (13, 20)]
        assert [n for phase, n in full.trace if phase == "write"] == list(range(5, 21))
        assert full.calls.count("book_provisional_knowledge") == 0
        assert full.calls.count("book_ending") == 1
        # Fresh-session negative readbacks use the real version-bound verifier.
        # A correct file from another run, a partial source list, current v10
        # substituted for selected v9, and altered bytes must each be rejected.
        from bestseller.services.book_run_export import verify_export
        from pathlib import Path
        import hashlib

        project = await fresh.get(m.ProjectModel, full.pid)
        artifact = await fresh.get(m.ExportArtifactModel, UUID(result["artifact_id"]))
        original_meta = deepcopy(artifact.metadata_json)
        original_creator = artifact.created_by_run_id
        original_checksum = artifact.checksum
        path = Path(artifact.storage_uri)
        original_bytes = path.read_bytes()
        selected = deepcopy(row.metadata_json["selected"])

        async def check():
            await fresh.commit()
            return await verify_export(
                fresh,
                full.settings,
                project=project,
                manifest=selected,
                artifact_id=artifact.id,
                book_run_id=rid,
            )

        artifact.created_by_run_id = uuid4()
        with pytest.raises(ValueError, match="book_export_artifact_scope"):
            await check()
        artifact.created_by_run_id = original_creator
        artifact.metadata_json = {
            **original_meta,
            "source_drafts": original_meta["source_drafts"][:-1],
        }
        with pytest.raises(ValueError, match="book_export_artifact_sources_changed"):
            await check()
        changed = deepcopy(original_meta)
        current = await fresh.scalar(
            select(m.ChapterDraftVersionModel).where(
                m.ChapterDraftVersionModel.chapter_id == UUID(first["chapter_id"]),
                m.ChapterDraftVersionModel.is_current.is_(True),
            )
        )
        changed["source_drafts"][0]["draft_id"] = str(current.id)
        artifact.metadata_json = changed
        with pytest.raises(ValueError, match="book_export_artifact_sources_changed"):
            await check()
        artifact.metadata_json = deepcopy(original_meta)
        altered = original_bytes + b"\nchanged"
        path.write_bytes(altered)
        artifact.checksum = hashlib.sha256(altered).hexdigest()
        try:
            with pytest.raises(
                ValueError, match="book_export_bytes_do_not_match_selected_manuscript"
            ):
                await check()
        finally:
            path.write_bytes(original_bytes)
            artifact.checksum = original_checksum
            await fresh.commit()
        assert (await check())["verified"]
