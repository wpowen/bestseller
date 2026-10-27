"""Offline DB and actual owned-block regressions; no models/Redis/production."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import textwrap
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import ChapterDraftVersionModel as Draft
from bestseller.infra.db.models import LlmRunModel as Run
from bestseller.infra.db.models import WorkflowStepRunModel as Step
from bestseller.services import ai_flavor_gate, deslop_revise, pipelines
from bestseller.services import style_phase_receipts as receipts
from bestseller.services.ai_flavor_gate import AiFlavorGateConfig, AiFlavorGateOutcome
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@compiles(JSONB, "sqlite")
def _jsonb(_type, _compiler, **kwargs):
    return "JSON"


class AsyncFacade:
    def __init__(self, sync):
        self.sync = sync
        self.commits = 0

    def add(self, row):
        self.sync.add(row)

    async def get(self, cls, key):
        return self.sync.get(cls, key)

    async def scalar(self, query):
        return self.sync.scalar(query)

    async def execute(self, query):
        return self.sync.execute(query)

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.sync.commit()
        self.commits += 1


@contextmanager
def database(path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for cls in (Draft, Step, Run):
            connection.execute(CreateTable(cls.__table__))
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX test_current ON chapter_draft_versions(chapter_id) "
            "WHERE is_current = 1"
        )
    with Session(engine, expire_on_commit=False) as sync:
        yield AsyncFacade(sync), sync
    engine.dispose()


def original(sync, text="船工把桨递给她。岸边有人喊了一声。", number=2):
    draft = Draft(
        project_id=uuid4(),
        chapter_id=uuid4(),
        version_no=1,
        content_md=text,
        word_count=len(text),
        is_current=True,
        promotion_metadata={"origin": "chapter_first_writer"},
    )
    sync.add(draft)
    sync.flush()
    chapter = SimpleNamespace(
        id=draft.chapter_id,
        chapter_number=number,
        target_word_count=2600,
        current_word_count=len(text),
        metadata_json={"debt_ledger": {"retained": True}},
        status="drafting",
        production_state="drafting",
    )
    project = SimpleNamespace(
        id=draft.project_id, slug="offline-case", language="zh-CN", metadata_json={}
    )
    return draft, chapter, project


def observed_phase(content, source, workflow_id, project_id, attempted=2):
    return deslop_revise._phase_result(
        content,
        source=source,
        state="completed",
        reason="round_limit_reached",
        attempted=attempted,
        rounds=2,
        project_id=project_id,
        workflow_run_id=workflow_id,
    )


def outcome(score=20, decision="pass"):
    return AiFlavorGateOutcome(
        enabled=True,
        language="zh-CN",
        chapter_number=2,
        before_score=score,
        after_score=score,
        patched_text=None,
        decision=decision,
    )


def step(sync, workflow_id=None, order=1):
    row = Step(
        workflow_run_id=workflow_id or uuid4(),
        step_name="ai_flavor_gate",
        step_order=order,
        status="completed",
        output_ref={},
    )
    sync.add(row)
    sync.flush()
    return row


async def close(session, sync, draft, policy="policy", complete=True, decision="pass"):
    row = step(sync)
    await receipts.close_style_phase(
        session,
        draft=draft,
        policy=policy,
        step=row,
        input_binding=await receipts.draft_application_binding(session, draft),
        outcome=outcome(decision=decision),
        complete=complete,
    )
    await session.commit()
    return row


async def test_receipt_survives_reopen_and_requires_durable_step(tmp_path):
    path = tmp_path / "receipt-test.sqlite3"
    with database(path) as (session, sync):
        draft, _, _ = original(sync)
        draft_id = draft.id
        await close(session, sync, draft)
        sync.expunge_all()
        draft = sync.get(Draft, draft_id)
        assert await receipts.completed_style_phase(session, draft=draft, policy="policy")
        row = sync.scalar(select(Step))
        row.status = "failed"
        assert await receipts.completed_style_phase(session, draft=draft, policy="policy") is None
    # Read through a different session/connection to prove commit, not identity-map reuse.
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as sync:
        draft = sync.get(Draft, draft_id)
        assert await receipts.completed_style_phase(AsyncFacade(sync), draft=draft, policy="policy")
    engine.dispose()


@pytest.mark.parametrize(
    "change", ["body", "policy", "draft_id", "parent", "scenes", "resurrection"]
)
async def test_exact_identity_invalidation(tmp_path, change):
    with database(tmp_path / "identity-test.sqlite3") as (session, sync):
        draft, chapter, _ = original(sync)
        # Bind a genuine persisted child to a parent, including source receipt.
        child = await pipelines._persist_deslop_candidate(
            session,
            chapter=chapter,
            parent_draft=draft,
            content="她接过船桨。推船离岸。",
            llm_run_id=None,
            round_no=0,
            transformation="local_patch",
            make_current=True,
        )
        await close(session, sync, child)
        policy = "policy"
        if change == "body":
            child.content_md += "有人追了出来。"
        elif change == "policy":
            policy = "new-policy"
        elif change == "draft_id":
            child.id = uuid4()
        elif change == "parent":
            draft.content_md += "父稿修改。"
        elif change == "scenes":
            child.assembled_from_scene_draft_ids = [str(uuid4())]
        elif change == "resurrection":
            # Actual selection helper: A -> original B -> exact same A row.
            await pipelines._select_deslop_candidate(
                session, chapter=chapter, candidate=draft, selected_reason="back"
            )
            await pipelines._select_deslop_candidate(
                session, chapter=chapter, candidate=child, selected_reason="again"
            )
        assert await receipts.completed_style_phase(session, draft=child, policy=policy) is None


@pytest.mark.parametrize("complete,decision", [(False, "pass"), (True, "block")])
async def test_partial_or_blocked_is_never_complete(tmp_path, complete, decision):
    with database(tmp_path / "partial-test.sqlite3") as (session, sync):
        draft, _, _ = original(sync)
        row = await close(session, sync, draft, complete=complete, decision=decision)
        assert row.output_ref[receipts.RECEIPT_KEY]["state"] == "incomplete"
        assert await receipts.completed_style_phase(session, draft=draft, policy="policy") is None


async def test_legacy_origin_and_aggregate_scores_do_not_fake_receipt(tmp_path):
    with database(tmp_path / "legacy-test.sqlite3") as (session, sync):
        draft, chapter, _ = original(sync)
        child = await pipelines._persist_deslop_candidate(
            session,
            chapter=chapter,
            parent_draft=draft,
            content="赵师傅收起工具。走出机房。",
            llm_run_id=uuid4(),
            round_no=2,
            make_current=True,
        )
        row = step(sync)
        row.output_ref = {"decision": "pass", "before_score": 20, "after_score": 10}
        result = await receipts.reconcile_legacy_style_phase(
            session, draft=child, policy="policy", step_id=row.id, apply=True
        )
        assert not result["eligible"] and not result["applied"]
        assert "missing_contemporaneous_policy_evidence" in result["reasons"]
        assert receipts.RECEIPT_KEY not in child.promotion_metadata
        assert chapter.metadata_json["debt_ledger"] == {"retained": True}


async def test_policy_tracks_config_model_language_contract_and_assets(tmp_path):
    with database(tmp_path / "policy-test.sqlite3") as (_, sync):
        _, chapter, project = original(sync)
        settings = load_settings()
        config = AiFlavorGateConfig(data_dir=str(tmp_path / "assets"))
        assets = Path(config.data_dir)
        assets.mkdir()
        asset = assets / "patterns_zh.json"
        asset.write_text("{}")

        def fingerprint():
            return receipts.style_policy_fingerprint(
                settings=settings, project=project, chapter=chapter, config=config
            )

        baseline = fingerprint()
        assert baseline == fingerprint()
        config = replace(config, warn_score_cn=99)
        assert fingerprint() != baseline
        config = replace(config, warn_score_cn=25)
        assert fingerprint() == baseline
        settings.llm.writer.model = "different-model"
        assert fingerprint() != baseline
        settings = load_settings().model_copy(deep=True)
        baseline = fingerprint()
        chapter.target_word_count += 1
        assert fingerprint() != baseline
        chapter.target_word_count -= 1
        project.language = "en"
        assert fingerprint() != baseline
        project.language = "zh-CN"
        project.metadata_json = {"llm_model_id": "another-selection"}
        assert fingerprint() != baseline
        project.metadata_json = {}
        asset.write_text('{"changed":true}')
        assert fingerprint() != baseline


def owned_block():
    """Compile the actual owned production block, avoiding unrelated chapter phases."""
    source = Path(pipelines.__file__).read_text()
    start = source.index("        # ── AI-flavor gate")
    end = source.index("        # ── 文采 advisory", start)
    body = textwrap.indent(textwrap.dedent(source[start:end]), "    ")
    signature = (
        "async def run(session, settings, project, chapter, chapter_draft, workflow_run, "
        "chapter_number, resume_existing_draft_only=False, step_order=1, "
        "scene_requires_human_review=False):\n"
    )
    namespace = dict(vars(pipelines))
    exec(
        compile(
            signature
            + body
            + "    return chapter_draft, ai_flavor_outcome, scene_requires_human_review\n",
            "<actual-style-block>",
            "exec",
        ),
        namespace,
    )
    return namespace


@pytest.mark.parametrize("number,text", [(2, "船工递来船桨。"), (3, "林护士关好药柜。")])
async def test_actual_block_commits_then_resume_skips_paid_style_and_new_candidates(
    tmp_path, monkeypatch, number, text
):
    with database(tmp_path / "block-test.sqlite3") as (session, sync):
        draft, chapter, project = original(sync, text, number)
        namespace = owned_block()
        config = AiFlavorGateConfig()
        namespace["get_quality_gates_config"] = lambda: SimpleNamespace(ai_flavor=config)
        namespace["_evaluate_chapter_ai_flavor_without_edits"] = lambda **kwargs: outcome()
        monkeypatch.setattr(ai_flavor_gate, "run_ai_flavor_gate", lambda **kwargs: outcome())
        monkeypatch.setattr(ai_flavor_gate, "needs_deslop_revise", lambda result: True)
        paid = []
        workflow = SimpleNamespace(id=uuid4(), metadata_json={})

        async def revise(session, settings, **kwargs):
            parent = kwargs["content"]
            for round_no in (1, 2):
                content = parent + f"第{round_no}次动作落地。"
                run = Run(
                    project_id=project.id,
                    workflow_run_id=kwargs["workflow_run_id"],
                    provider="offline-fixture",
                    logical_role="writer",
                    model_name="fixture",
                    prompt_template="scene_writer",
                    prompt_version="deslop_revise",
                    finish_reason="stop",
                    metadata_json={},
                )
                session.add(run)
                await session.flush()
                paid.append(run.id)
                await kwargs["observe_candidate"](
                    {
                        "content": content,
                        "parent_content": parent,
                        "llm_run_id": run.id,
                        "round": round_no,
                    }
                )
                parent = content
            return observed_phase(parent, kwargs["content"], kwargs["workflow_run_id"], project.id)

        monkeypatch.setattr(deslop_revise, "revise_prose_deslop", revise)
        settings = load_settings()
        selected, _, blocked = await namespace["run"](
            session, settings, project, chapter, draft, workflow, number
        )
        assert len(paid) == 2 and session.commits == 1 and not blocked
        saved_id = selected.id
        policy = receipts.style_policy_fingerprint(
            settings=settings, project=project, chapter=chapter, config=config
        )
        assert await receipts.completed_style_phase(session, draft=selected, policy=policy)
        # A downstream paid phase failing/rolling back cannot erase style completion.
        sync.rollback()
        sync.expunge_all()
        selected = sync.get(Draft, saved_id)
        workflow = SimpleNamespace(id=uuid4(), metadata_json={})
        selected_again, _, _ = await namespace["run"](
            session, settings, project, chapter, selected, workflow, number
        )
        assert selected_again.id == saved_id and len(paid) == 2
        assert len(list(sync.scalars(select(Draft)))) == 3
        assert chapter.metadata_json["debt_ledger"] == {"retained": True}
        # Re-evaluate residual hard gate even on reuse; no new style spend.
        namespace["_evaluate_chapter_ai_flavor_without_edits"] = lambda **kwargs: outcome(
            50, "block"
        )
        workflow = SimpleNamespace(id=uuid4(), metadata_json={})
        _, _, blocked = await namespace["run"](
            session, settings, project, chapter, selected, workflow, number
        )
        assert blocked and chapter.production_state == "blocked" and len(paid) == 2


async def test_actual_block_checkpoint_failure_stops_before_next_phase(tmp_path, monkeypatch):
    with database(tmp_path / "failure-test.sqlite3") as (session, sync):
        draft, chapter, project = original(sync)
        namespace = owned_block()
        namespace["get_quality_gates_config"] = lambda: SimpleNamespace(
            ai_flavor=AiFlavorGateConfig(deslop_revise_enabled=False)
        )
        monkeypatch.setattr(ai_flavor_gate, "run_ai_flavor_gate", lambda **kwargs: outcome())

        async def fail_commit(_session):
            raise RuntimeError("offline simulated disk failure")

        namespace["_checkpoint_commit"] = fail_commit
        with pytest.raises(receipts.StylePhaseReceiptError, match="checkpoint failed"):
            await namespace["run"](
                session,
                load_settings(),
                project,
                chapter,
                draft,
                SimpleNamespace(id=uuid4(), metadata_json={}),
                2,
            )
        assert session.commits == 0
        sync.rollback()
        assert sync.scalar(select(Step)) is None


@pytest.mark.parametrize(
    "failure",
    ["one_round", "missing_run", "fallback", "truncated", "other_workflow", "duplicate_run"],
)
async def test_partial_or_unproven_model_calls_cannot_close(tmp_path, failure):
    with database(tmp_path / "round-proof-test.sqlite3") as (session, sync):
        draft, chapter, project = original(sync)
        workflow_id = uuid4()
        candidates = []
        for number in (1, 2):
            run = Run(
                project_id=project.id,
                workflow_run_id=workflow_id,
                provider="offline-fixture",
                logical_role="writer",
                model_name="fixture",
                prompt_template="scene_writer",
                prompt_version="deslop_revise",
                finish_reason="stop",
                metadata_json={},
            )
            sync.add(run)
            sync.flush()
            candidate = await pipelines._persist_deslop_candidate(
                session,
                chapter=chapter,
                parent_draft=draft,
                content=f"修订正文{number}",
                llm_run_id=run.id,
                round_no=number,
            )
            candidates.append(candidate)
        assert await receipts.successful_style_rounds(
            session,
            candidates=candidates,
            workflow_run_id=workflow_id,
            phase_result=observed_phase("output", "input", workflow_id, project.id),
        )
        if failure == "one_round":
            candidates.pop()
        elif failure == "missing_run":
            candidates[1].llm_run_id = uuid4()
        elif failure == "fallback":
            run.provider = "fallback"
        elif failure == "truncated":
            run.finish_reason = "length"
        elif failure == "other_workflow":
            run.workflow_run_id = uuid4()
        elif failure == "duplicate_run":
            candidates[1].llm_run_id = candidates[0].llm_run_id
        assert not await receipts.successful_style_rounds(
            session,
            candidates=candidates,
            workflow_run_id=workflow_id,
            phase_result=observed_phase("output", "input", workflow_id, project.id),
        )


async def test_actual_block_failed_postcheck_does_not_close_successful_rounds(
    tmp_path, monkeypatch
):
    with database(tmp_path / "postcheck-test.sqlite3") as (session, sync):
        draft, chapter, project = original(sync)
        namespace = owned_block()
        namespace["get_quality_gates_config"] = lambda: SimpleNamespace(
            ai_flavor=AiFlavorGateConfig()
        )
        monkeypatch.setattr(ai_flavor_gate, "needs_deslop_revise", lambda result: True)
        gate_calls = []

        def gate(**kwargs):
            gate_calls.append(1)
            if len(gate_calls) == 2:
                raise RuntimeError("postcheck unavailable")
            return outcome()

        monkeypatch.setattr(ai_flavor_gate, "run_ai_flavor_gate", gate)

        async def revise(session, settings, **kwargs):
            parent = kwargs["content"]
            for round_no in (1, 2):
                run = Run(
                    project_id=project.id,
                    workflow_run_id=kwargs["workflow_run_id"],
                    provider="offline-fixture",
                    logical_role="writer",
                    model_name="fixture",
                    prompt_template="scene_writer",
                    prompt_version="deslop_revise",
                    finish_reason="stop",
                    metadata_json={},
                )
                session.add(run)
                await session.flush()
                content = parent + f"动作{round_no}。"
                await kwargs["observe_candidate"](
                    {
                        "content": content,
                        "parent_content": parent,
                        "llm_run_id": run.id,
                        "round": round_no,
                    }
                )
                parent = content
            return observed_phase(parent, kwargs["content"], kwargs["workflow_run_id"], project.id)

        monkeypatch.setattr(deslop_revise, "revise_prose_deslop", revise)
        selected, _, _ = await namespace["run"](
            session,
            load_settings(),
            project,
            chapter,
            draft,
            SimpleNamespace(id=uuid4(), metadata_json={}),
            2,
        )
        assert selected.id == draft.id
        row = sync.scalar(select(Step))
        assert row.output_ref[receipts.RECEIPT_KEY]["state"] == "incomplete"
        assert len(list(sync.scalars(select(Draft)))) == 3


async def test_policy_is_stable_across_deployment_and_configured_asset_locations(
    tmp_path, monkeypatch
):
    import shutil

    source_root = Path(receipts.__file__).resolve().parents[3]
    with database(tmp_path / "deployment-test.sqlite3") as (_, sync):
        _, chapter, project = original(sync)
        settings = load_settings()
        fingerprints = []
        # Recreate identical tracked assets at host-checkout and container roots.
        for root in (tmp_path / "host-checkout", tmp_path / "app"):
            for relative in ("src/bestseller/services", "config", "data/ai_flavor"):
                destination = root / relative
                destination.mkdir(parents=True)
                if relative == "src/bestseller/services":
                    for filename in ("deslop_revise.py", "ai_flavor_gate.py", "hype_engine.py"):
                        shutil.copyfile(source_root / relative / filename, destination / filename)
                    shutil.copytree(source_root / relative / "ai_flavor", destination / "ai_flavor")
                elif relative == "config":
                    for filename in ("model_catalog.yaml", "cinematic_pov.yaml"):
                        shutil.copyfile(source_root / relative / filename, destination / filename)
                else:
                    for asset in (source_root / relative).glob("*.json"):
                        shutil.copyfile(asset, destination / asset.name)
            monkeypatch.setattr(
                receipts, "__file__", str(root / "src/bestseller/services/style_phase_receipts.py")
            )
            monkeypatch.chdir(root)
            # Relative/absolute/default/external paths all locate the same asset class.
            external = root / "mounted-flavor-assets"
            shutil.copytree(root / "data/ai_flavor", external)
            for location in ("data/ai_flavor", str(root / "data/ai_flavor"), str(external)):
                fingerprints.append(
                    receipts.style_policy_fingerprint(
                        settings=settings,
                        project=project,
                        chapter=chapter,
                        config=AiFlavorGateConfig(data_dir=location),
                    )
                )
        assert len(set(fingerprints)) == 1
        (external / "patterns_zh.json").write_text('{"changed":true}')
        assert (
            receipts.style_policy_fingerprint(
                settings=settings,
                project=project,
                chapter=chapter,
                config=AiFlavorGateConfig(data_dir=str(external)),
            )
            != fingerprints[0]
        )


@pytest.mark.parametrize(
    "field",
    [
        "model",
        "temperature",
        "max_tokens",
        "reasoning_effort",
        "writer_budget",
        "writer_margin",
        "retry",
        "mock",
    ],
)
async def test_writer_style_dependencies_invalidate_policy(tmp_path, field):
    with database(tmp_path / "writer-policy-test.sqlite3") as (_, sync):
        _, chapter, project = original(sync)
        settings = load_settings().model_copy(deep=True)

        def fingerprint():
            return receipts.style_policy_fingerprint(
                settings=settings,
                project=project,
                chapter=chapter,
                config=AiFlavorGateConfig(),
            )

        baseline = fingerprint()
        if field == "model":
            settings.llm.writer.model += "-changed"
        elif field == "temperature":
            settings.llm.writer.temperature += 0.1
        elif field == "max_tokens":
            settings.llm.writer.max_tokens += 1
        elif field == "reasoning_effort":
            settings.llm.writer.reasoning_effort = "changed"
        elif field == "writer_budget":
            settings.llm.writer_total_input_budget_tokens += 1
        elif field == "writer_margin":
            settings.llm.writer_prompt_safety_margin += 0.01
        elif field == "retry":
            settings.llm.retry.max_attempts += 1
        elif field == "mock":
            settings.llm.mock = not settings.llm.mock
        assert fingerprint() != baseline


async def test_unrelated_roles_retrieval_and_credentials_do_not_invalidate_style(tmp_path):
    with database(tmp_path / "unrelated-policy-test.sqlite3") as (_, sync):
        _, chapter, project = original(sync)
        settings = load_settings().model_copy(deep=True)

        def fingerprint():
            return receipts.style_policy_fingerprint(
                settings=settings,
                project=project,
                chapter=chapter,
                config=AiFlavorGateConfig(),
            )

        baseline = fingerprint()
        for role in (
            settings.llm.critic,
            settings.llm.planner,
            settings.llm.editor,
            settings.llm.summarizer,
        ):
            role.model += "-changed"
            role.temperature += 0.1
            role.max_tokens += 1
        settings.llm.independent_judge_mode = (
            "off" if settings.llm.independent_judge_mode != "off" else "shadow"
        )
        settings.retrieval.embedding_model += "-changed"
        settings.llm.writer.api_key_env = "OFFLINE_OTHER_CREDENTIAL_LABEL"
        assert fingerprint() == baseline


@pytest.mark.parametrize(
    "mode",
    [
        "early_clean",
        "bounded",
        "second_failed",
        "zero_clean",
        "zero_gate",
        "truncated",
        "wrong_workflow",
        "blocked_postcheck",
        "short_rewrite",
        "fallback",
    ],
)
async def test_actual_deslop_termination_controls_durable_closure(tmp_path, monkeypatch, mode):
    from bestseller.services.llm import LLMCompletionResult

    dirty = "他翻开旧册。没人看见他三年来每夜的功课。这不是寻常的册子，是命册。"  # noqa: RUF001
    clean = "他翻开旧册，指腹压在卷边那道折痕上，压了三年，折痕里还嵌着干墨。"  # noqa: RUF001
    with database(tmp_path / "actual-termination-test.sqlite3") as (session, sync):
        draft, chapter, project = original(sync, clean if mode.startswith("zero") else dirty)
        chapter.target_word_count = 40
        namespace = owned_block()
        config = AiFlavorGateConfig()
        namespace["get_quality_gates_config"] = lambda: SimpleNamespace(ai_flavor=config)
        namespace["_evaluate_chapter_ai_flavor_without_edits"] = lambda **kwargs: outcome(0)
        gate_calls = []

        def gate(**kwargs):
            gate_calls.append(1)
            if mode == "blocked_postcheck":
                return outcome(50 if len(gate_calls) == 1 else 40, "block")
            return outcome(0 if mode == "zero_gate" else 20)

        monkeypatch.setattr(ai_flavor_gate, "run_ai_flavor_gate", gate)
        monkeypatch.setattr(
            ai_flavor_gate, "needs_deslop_revise", lambda result: mode != "zero_gate"
        )
        calls = []

        async def complete(session, settings, request):
            calls.append(request)
            if mode == "second_failed" and len(calls) == 2:
                raise RuntimeError("second request failed after a real candidate")
            content = (
                dirty
                if mode in {"second_failed", "bounded"}
                else "太短"
                if mode == "short_rewrite"
                else clean
            )
            run = Run(
                project_id=project.id,
                workflow_run_id=uuid4() if mode == "wrong_workflow" else request.workflow_run_id,
                provider="fallback" if mode == "fallback" else "offline-fixture",
                logical_role="writer",
                model_name="fixture",
                prompt_template="scene_writer",
                prompt_version="deslop_revise",
                finish_reason="length" if mode == "truncated" else "stop",
                metadata_json={},
            )
            session.add(run)
            await session.flush()
            return LLMCompletionResult(
                content=content,
                llm_run_id=run.id,
                provider=run.provider,
                model_name=run.model_name,
                finish_reason=run.finish_reason,
            )

        monkeypatch.setattr(deslop_revise, "complete_text", complete)
        settings = load_settings()
        workflow = SimpleNamespace(id=uuid4(), metadata_json={})
        selected, _, blocked = await namespace["run"](
            session, settings, project, chapter, draft, workflow, 2
        )
        row = sync.scalar(select(Step))
        receipt = row.output_ref[receipts.RECEIPT_KEY]
        expected = mode in {"early_clean", "bounded", "zero_clean", "zero_gate"}
        assert (receipt["state"] == "completed") == expected
        policy = receipts.style_policy_fingerprint(
            settings=settings, project=project, chapter=chapter, config=config
        )
        assert (
            bool(await receipts.completed_style_phase(session, draft=selected, policy=policy))
            == expected
        )
        assert len(calls) <= 2
        if mode == "second_failed":
            assert len(calls) == 2 and receipt["phase"]["state"] == "failed"
            assert receipt["phase"]["attempted_rounds"] == 2
            assert len(list(sync.scalars(select(Draft)))) == 2
        if mode == "blocked_postcheck":
            assert blocked and chapter.production_state == "blocked"
        if expected:
            assert len(calls) == (2 if mode == "bounded" else 1 if mode == "early_clean" else 0)
            assert receipt["phase"]["attempted_rounds"] == len(calls)
            assert receipt["phase"]["stop_reason"] == (
                "gate_evaluated"
                if mode == "zero_gate"
                else "round_limit_reached"
                if mode == "bounded"
                else "clean"
            )
            count = len(list(sync.scalars(select(Draft))))
            saved_id = selected.id
            sync.rollback()
            sync.expunge_all()
            selected = sync.get(Draft, saved_id)
            initial_calls = len(calls)
            selected_again, _, _ = await namespace["run"](
                session,
                settings,
                project,
                chapter,
                selected,
                SimpleNamespace(id=uuid4(), metadata_json={}),
                2,
            )
            assert selected_again.id == saved_id
            assert len(calls) == initial_calls
            assert len(list(sync.scalars(select(Draft)))) == count
        assert chapter.metadata_json["debt_ledger"] == {"retained": True}


async def test_phase_result_retains_string_copy_and_json_compatibility():
    import copy
    import json

    result = observed_phase("prose", "source", uuid4(), uuid4())
    assert isinstance(result, str) and json.loads(json.dumps(result)) == "prose"
    copied = copy.deepcopy(result)
    assert copied == result and copied.phase_status == result.phase_status


@pytest.mark.parametrize("error_kind", ["terminal", "busy", "not_sent"])
@pytest.mark.parametrize("site", ["gate", "deslop", "checkpoint"])
async def test_style_budget_boundaries_propagate_without_next_phase(
    tmp_path, monkeypatch, error_kind, site
):
    from bestseller.services.budget import (
        ExecutionBudgetError,
        ExecutionOperationBusyError,
        ExecutionProviderNotSentError,
    )
    from bestseller.services.llm import LLMCompletionResult

    error_class = {
        "terminal": ExecutionBudgetError,
        "busy": ExecutionOperationBusyError,
        "not_sent": ExecutionProviderNotSentError,
    }[error_kind]
    error = error_class("offline_boundary_stop")
    dirty = "他翻开旧册。没人看见他三年来每夜的功课。这不是寻常的册子，是命册。"  # noqa: RUF001
    with database(tmp_path / "budget-boundary-test.sqlite3") as (session, sync):
        draft, chapter, project = original(sync, dirty)
        chapter.target_word_count = 40
        namespace = owned_block()
        namespace["get_quality_gates_config"] = lambda: SimpleNamespace(
            ai_flavor=AiFlavorGateConfig()
        )

        def gate(**kwargs):
            if site == "gate":
                raise error
            return outcome(0 if site == "checkpoint" else 20)

        monkeypatch.setattr(ai_flavor_gate, "run_ai_flavor_gate", gate)
        monkeypatch.setattr(ai_flavor_gate, "needs_deslop_revise", lambda result: site == "deslop")
        calls = []

        async def complete(session, settings, request):
            calls.append(request)
            if len(calls) == 2:
                raise error
            run = Run(
                project_id=project.id,
                workflow_run_id=request.workflow_run_id,
                provider="offline-fixture",
                logical_role="writer",
                model_name="fixture",
                prompt_template="scene_writer",
                prompt_version="deslop_revise",
                finish_reason="stop",
                metadata_json={},
            )
            session.add(run)
            await session.flush()
            return LLMCompletionResult(
                content=dirty,
                llm_run_id=run.id,
                provider=run.provider,
                model_name=run.model_name,
                finish_reason="stop",
            )

        monkeypatch.setattr(deslop_revise, "complete_text", complete)

        async def checkpoint(_session):
            raise error

        if site == "checkpoint":
            namespace["_checkpoint_commit"] = checkpoint
        next_phase = []
        with pytest.raises(ExecutionBudgetError) as caught:
            await namespace["run"](
                session,
                load_settings(),
                project,
                chapter,
                draft,
                SimpleNamespace(id=uuid4(), metadata_json={}),
                2,
            )
            next_phase.append("must not execute")
        assert caught.value is error
        assert next_phase == [] and session.commits == 0
        if site == "deslop":
            assert len(calls) == 2
            assert len(list(sync.scalars(select(Draft)))) == 2
            assert sync.scalar(select(Step)) is None
        else:
            assert calls == []
        sync.rollback()
        assert sync.scalar(select(Step)) is None
