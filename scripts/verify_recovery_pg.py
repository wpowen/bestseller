#!/usr/bin/env python3
"""Bounded, non-LLM PostgreSQL recovery probe; retained fixtures are audit evidence.

Run with .venv/bin/python -B scripts/verify_recovery_pg.py --database
bestseller_repair_test_20261015. No schema creation, deletion, production connection,
environment mutation, or credential output. Only the database component of the
load_settings URL changes. Each physical connection verifies current_database()
before the application can use it. Reports are written only after that guard.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
import traceback
from uuid import UUID, uuid4

# Also prevent bytecode writes when invoked without -B.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
logging.disable(logging.CRITICAL)

from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db.models import (
    ChapterDraftVersionModel, ChapterModel, ExecutionAttemptModel,
    ExecutionBudgetModel, ProjectModel,
)
from bestseller.services.budget import (
    ExecutionBudgetError, ExecutionLedger, ExecutionReconciliation, bind_execution_scope,
)
from bestseller.services.content_origin_policy import content_hash, origin_record
from bestseller.services.framework_state_migration import (
    migrate_detector_insertions, migrate_project_prompt_state,
)
from bestseller.services.invariants import invariants_to_dict, seed_invariants
from bestseller.services.writing_profile import get_project_writing_profile
from bestseller.settings import BudgetSettings, load_settings

TARGET = "bestseller_repair_test_20261015"
ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports/production-recovery-2026-10-15"
MODEL = "pg-recovery-probe-no-network"


class ProbeFailure(RuntimeError):
    """Messages in this class are fixed probe diagnostics, never DB exception text."""

    def __init__(self, diagnostic, evidence=None):
        super().__init__(diagnostic)
        self.evidence = evidence


def require(condition, diagnostic):
    if not condition:
        raise ProbeFailure(diagnostic)


def safe_error(exc):
    result = {"type": type(exc).__name__, "frames": [
        {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
        for f in traceback.extract_tb(exc.__traceback__)
    ]}
    if isinstance(exc, (ProbeFailure, ExecutionBudgetError)):
        result["code"] = str(exc)
    if isinstance(exc, ProbeFailure) and exc.evidence is not None:
        result["evidence"] = exc.evidence
    cause = getattr(exc, "orig", None) or exc.__cause__
    if cause is not None:
        result["cause_type"] = type(cause).__name__
        result["sqlstate"] = getattr(cause, "sqlstate", None)
    return result


async def expect_code(awaitable, expected):
    try:
        await awaitable
    except ExecutionBudgetError as exc:
        require(exc.code == expected, f"expected_{expected}_received_{exc.code}")
        return exc.code
    raise ProbeFailure(f"missing_boundary_{expected}")


def response(content="synthetic transport return; no provider contacted"):
    return {"content": content, "provider": "recovery_probe", "model_name": MODEL,
            "input_tokens": 5, "output_tokens": 8, "finish_reason": "stop",
            "metadata": {"synthetic": True, "real_llm_called": False}}


def draft_snapshot(row):
    return json.loads(json.dumps(
        {c.key: getattr(row, c.key) for c in row.__table__.columns}, default=str))


class Probe:
    def __init__(self, engine, settings, run_id):
        self.engine, self.settings, self.run_id = engine, settings, run_id
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.scopes = []
        self.fixture_projects = []
        self.detector_receipts = []

    def ledger(self, suffix, **limits):
        scope = f"pg-recovery:{self.run_id}:{suffix}"
        if scope not in self.scopes:
            self.scopes.append(scope)
        return ExecutionLedger(self.engine, scope, BudgetSettings(**{
            "execution_max_calls": 20, "execution_max_tokens": 10000,
            "execution_deadline_seconds": 600, **limits,
        }))

    def project(self, suffix, **values):
        return ProjectModel(id=uuid4(), slug=f"pgprobe-{self.run_id}-{suffix}",
                            title="PostgreSQL recovery probe fixture", genre="现实生活",
                            language="zh-CN", target_word_count=2200, target_chapters=1,
                            metadata_json={"pg_recovery_probe": self.run_id}, **values)

    async def state(self, ledger):
        async with self.sessions() as s:
            row = await s.get(ExecutionBudgetModel, ledger.scope_id)
            attempts = list(await s.scalars(select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.scope_id == ledger.scope_id)))
            return {"calls": row.calls_used, "tokens": row.tokens_charged,
                    "status": row.status, "deadline": row.deadline_at.isoformat(),
                    "attempts": len(attempts),
                    "attempt_states": dict(Counter(a.status for a in attempts))}

    async def concurrent(self, suffix, max_calls, max_tokens, winners):
        # Parallel specialists share one dispatcher epoch, as in the real API.
        primary = self.ledger(suffix, execution_max_calls=max_calls,
                              execution_max_tokens=max_tokens)
        with bind_execution_scope(primary.scope_id):
            workers = [self.ledger(suffix, execution_max_calls=max_calls,
                                   execution_max_tokens=max_tokens) for _ in range(8)]
            claims = await asyncio.gather(*(w.claim(f"op-{i}", "h")
                                            for i, w in enumerate(workers)))
            gate = asyncio.Event()

            async def reserve(w, claim):
                await gate.wait()
                return await w.reserve(claim, MODEL, "h", 100)

            tasks = [asyncio.create_task(reserve(w, c)) for w, c in zip(workers, claims)]
            gate.set()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        successful = [v for v in outcomes if isinstance(v, str)]
        failures = [v for v in outcomes if isinstance(v, BaseException)]
        require(len(successful) == winners, "concurrent_reservation_winner_count")
        require(all(isinstance(v, ExecutionBudgetError) and
                    v.code == "execution_budget_exhausted" for v in failures),
                "concurrent_reservation_unexpected_error")
        state = await self.state(primary)
        require((state["calls"], state["tokens"], state["attempts"]) ==
                (winners, winners * 100, winners), "concurrent_reservation_overdraw")
        return {"contenders": 8, "winners": winners, "rejections": len(failures), **state}

    async def concurrent_claim(self):
        owner = self.ledger("claim")
        with bind_execution_scope(owner.scope_id):
            workers = [self.ledger("claim") for _ in range(8)]
            outcomes = await asyncio.gather(*(w.claim("same", "h") for w in workers),
                                            return_exceptions=True)
        errors = [v for v in outcomes if isinstance(v, BaseException)]
        require(len(errors) == 7 and all(isinstance(v, ExecutionBudgetError) and
                v.code == "execution_operation_inflight" for v in errors),
                "concurrent_claim_not_exclusive")
        return {"contenders": 8, "owners": 1, "inflight_rejections": 7}

    async def rollback_and_replay(self):
        ledger = self.ledger("rollback")
        business = self.project("rollback")
        async with self.sessions() as outer:
            outer.add(business)
            await outer.flush()  # A real uncommitted business write, not a fake session.
            claim = await ledger.claim("paid", "h")
            attempt = await ledger.reserve(claim, MODEL, "h", 100)
            require(await ledger.settle(attempt, "completed", actual_tokens=13,
                    claim=claim, result=response()) is None, "settlement_blocked")
            await outer.rollback()  # No finish: emulate crash before business adoption.
        async with self.sessions() as s:
            require(await s.get(ProjectModel, business.id) is None, "business_rollback_failed")
        # Drop pooled connections: replays must read PG, not local identity maps.
        await self.engine.dispose()
        for _ in range(2):
            resumed = self.ledger("rollback")
            cached = await resumed.claim("paid", "h")
            require(cached.cached_result == response(), "paid_result_not_replayed")
            await resumed.assert_result_adoptable(cached, cached=True)
        await ledger.finish(claim, None)
        require((await ledger.transport_result(attempt))["result"] == response(),
                "transport_receipt_lost")
        await expect_code(ledger.settle(attempt, "completed", actual_tokens=13,
                          claim=claim, result=response("changed")),
                          "execution_settlement_conflict")
        await expect_code(ledger.claim("paid", "different"), "execution_operation_input_changed")
        state = await self.state(ledger)
        require((state["calls"], state["tokens"], state["attempts"]) == (1, 13, 1),
                "replay_charged_again")
        return {"business_insert_rolled_back": True, "fresh_connection_replays": 2,
                "immutable_transport_receipt": True, **state}

    async def freeze_cancel_reconcile(self):
        owner = self.ledger("unknown")
        claim = await owner.claim("paid", "h")
        attempt = await owner.reserve(claim, MODEL, "h", 100)
        restored = self.ledger("unknown")  # A different dispatcher owns no old reservation.
        await expect_code(restored.claim("different", "h2"), "execution_result_unknown")
        await expect_code(restored.claim("paid", "h"), "execution_result_unknown")
        frozen = await self.state(owner)
        require(frozen["tokens"] == 100 and frozen["calls"] == 1, "freeze_released_credit")
        await owner.cancel()
        require((await self.state(owner))["status"] == "unknown", "cancel_unfroze_unknown")
        blocked = await owner.settle(attempt, "completed", actual_tokens=13,
                                     claim=claim, result=response())
        require(blocked == "execution_result_unknown", "late_result_unfroze_scope")
        require((await owner.transport_result(attempt))["result"] == response(),
                "late_result_not_archived")
        await expect_code(owner.assert_result_adoptable(claim), "execution_result_unknown")
        # Synthetic transport evidence only; this makes no real billing claim.
        evidence = ExecutionReconciliation(
            reconciliation_id="probe-manual-recovery", attempt_id=attempt,
            expected_input_hash="h", decision="recover_completed",
            evidence_kind="transport_log", evidence_sha256=content_hash(response()),
            actor="pg-recovery-probe-synthetic-evidence", remote_operation_closed=True,
            result=response())
        receipt = await restored.reconcile(evidence)
        require(receipt == await self.ledger("unknown").reconcile(evidence),
                "reconciliation_not_idempotent")
        require(receipt["status"] == "cancelled" and not receipt["credit_released"] and
                not receipt["automatic_resume"], "reconciliation_resurrected_cancelled_scope")
        cached = await restored.claim("paid", "h")  # Evidence is readable; adoption is fenced.
        # Reconciliation validates and normalizes LLMCompletionResult defaults.
        require(cached.cached_result == evidence.result, "cancelled_evidence_lost")
        await expect_code(restored.assert_result_adoptable(cached, cached=True),
                          "execution_cancelled")
        await expect_code(restored.claim("new", "h3"), "execution_cancelled")
        state = await self.state(owner)
        require((state["calls"], state["tokens"], state["attempts"]) == (1, 13, 1),
                "reconciliation_replenished_credit")
        return {"cross_owner_freeze": frozen, "late_result_retained": True,
                "reconciliation_receipt": receipt, **state}

    async def direct_cancel(self):
        owner = self.ledger("cancel")
        claim = await owner.claim("paid", "h")
        attempt = await owner.reserve(claim, MODEL, "h", 100)
        await owner.cancel()
        require(await owner.settle(attempt, "completed", actual_tokens=13,
                claim=claim, result=response()) == "execution_cancelled", "late_cancel_bypassed")
        cached = await self.ledger("cancel").claim("paid", "h")
        await expect_code(owner.assert_result_adoptable(cached, cached=True), "execution_cancelled")
        await expect_code(owner.reserve(claim, MODEL, "h", 1), "execution_cancelled")
        require((await owner.transport_result(attempt))["result"] == response(), "cancel_lost_return")
        return await self.state(owner)

    async def expired_paid(self):
        owner = self.ledger("deadline")
        claim = await owner.claim("paid", "h")
        attempt = await owner.reserve(claim, MODEL, "h", 100)
        await owner.settle(attempt, "completed", actual_tokens=13, claim=claim, result=response())
        # Clock fixture: update only this probe's own ledger row.
        async with self.sessions() as s, s.begin():
            row = await s.get(ExecutionBudgetModel, owner.scope_id)
            row.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        restored = self.ledger("deadline")
        cached = await restored.claim("paid", "h")
        require(cached.cached_result == response(), "deadline_destroyed_paid_result")
        await restored.assert_result_adoptable(cached, cached=True)
        await expect_code(restored.claim("new", "h2"), "execution_deadline_exhausted")
        return await self.state(owner)

    async def source_migration(self):
        ids, before = [], {}
        for kind in ("framework_default", "explicit", "unknown"):
            p = self.project(kind)
            value = f"独立来源选择-{kind}"
            raw = {"character": {"romance_mode": value}}
            invariant = invariants_to_dict(seed_invariants(project_id=p.id, language=p.language,
                words_per_chapter=self.settings.generation.words_per_chapter))
            invariant["opening_archetype_pool"] = ["humiliation"]
            p.invariants_json = deepcopy(invariant)
            p.metadata_json = {**p.metadata_json, "writing_profile": deepcopy(raw),
                "writing_profile_origins": {"character.romance_mode": origin_record(value, kind=kind)},
                "distilled_strategy_blocks": {"world": "unverified historical source"},
                "growth_curve": "unadopted historical projection", "unrelated": {"keep": [1, "原件"]}}
            before[p.id] = {"profile": deepcopy(raw), "invariants": deepcopy(invariant),
                            "value": value, "kind": kind}
            async with self.sessions() as s, s.begin():
                s.add(p)
            ids.append(p.id)
            self.fixture_projects.append(str(p.id))
        first, snapshot = [], {}
        for pid in ids:
            async with self.sessions() as s, s.begin():
                p = await s.get(ProjectModel, pid)
                original = deepcopy([p.metadata_json, p.invariants_json])
                dry = migrate_project_prompt_state(p, apply=False)
                require(original == [p.metadata_json, p.invariants_json], "dry_run_mutated_project")
                applied = migrate_project_prompt_state(p, apply=True)
                require(dry == applied and applied["changed"], "dry_apply_disagreement")
                first.append({"project_id": str(pid), "source_kind": before[pid]["kind"], **applied})
            async with self.sessions() as s:
                p = await s.get(ProjectModel, pid)
                old = before[pid]
                require(p.metadata_json["unrelated"] == {"keep": [1, "原件"]}, "unrelated_source_changed")
                archives = list(p.metadata_json["framework_state_migrations"].values())
                require(any(a["original_invariants"] == old["invariants"] for a in archives),
                        "original_invariants_not_retained")
                source_values = [a["source_values"] for a in p.metadata_json["superseded_prompt_sources"].values()]
                require(any(a.get("distilled_strategy_blocks") == {"world": "unverified historical source"}
                            for a in source_values), "retired_reference_not_retained")
                require(any(a.get("growth_curve") == "unadopted historical projection"
                            for a in source_values), "growth_projection_not_retained")
                require("distilled_strategy_blocks" not in p.metadata_json and
                        "growth_curve" not in p.metadata_json, "retired_source_still_active")
                effective = get_project_writing_profile(p).character.romance_mode
                if old["kind"] == "framework_default":
                    require(effective != old["value"] and any(
                        a["receipts"].get("writing_profile", {}).get("original_payload") == old["profile"]
                        for a in archives), "generated_source_original_lost")
                else:
                    require(p.metadata_json["writing_profile"] == old["profile"], "author_or_unknown_original_changed")
                    require((effective == old["value"]) == (old["kind"] == "explicit"),
                            "unknown_source_gained_author_authority")
                snapshot[pid] = content_hash([p.metadata_json, p.invariants_json])
        for pid in ids:
            async with self.sessions() as s, s.begin():
                p = await s.get(ProjectModel, pid)
                again = migrate_project_prompt_state(p, apply=True)
                require(not again["changed"], "second_migration_changed_project")
            async with self.sessions() as s:
                p = await s.get(ProjectModel, pid)
                require(content_hash([p.metadata_json, p.invariants_json]) == snapshot[pid],
                        "second_migration_pg_readback_drift")
        return {"committed_passes": 2, "separate_session_readbacks": True,
                "originals_preserved": True, "first_pass": first,
                "persisted_hashes": {str(k): v for k, v in snapshot.items()}}

    async def detector_migration(self):
        p = self.project("draft")
        self.fixture_projects.append(str(p.id))
        bridge = "检测器历史插入段。"
        parents = {}
        async with self.sessions() as s, s.begin():
            s.add(p)
            await s.flush()
            for number, body in enumerate((f"# 原稿\n\n{bridge}\n\n作者保留正文。", "# 原稿\n\n作者已改写的正文。"), 1):
                chapter = ChapterModel(id=uuid4(), project_id=p.id, chapter_number=number,
                    chapter_goal="probe", metadata_json={"deterministic_hook_echo_bridge": {"bridge": bridge}})
                s.add(chapter)
                await s.flush()
                parent = ChapterDraftVersionModel(id=uuid4(), project_id=p.id, chapter_id=chapter.id,
                    version_no=1, content_md=body, word_count=len(body), is_current=True,
                    promotion_state="legacy_unverified", assembled_from_scene_draft_ids=[])
                s.add(parent)
                parents[parent.id] = body
        async with self.sessions() as s:
            originals = {pid: content_hash(draft_snapshot(await s.get(ChapterDraftVersionModel, pid)))
                         for pid in parents}
        receipts, hashes = [], []
        for _ in range(2):
            async with self.sessions() as s, s.begin():
                project = await s.get(ProjectModel, p.id)
                dry = await migrate_detector_insertions(s, project, apply=False)
                result = await migrate_detector_insertions(s, project, apply=True)
                require(dry == result, "detector_dry_apply_disagreement")
                receipts.append(result)
            async with self.sessions() as s:
                rows = list(await s.scalars(select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.project_id == p.id).order_by(ChapterDraftVersionModel.id)))
                require(len(rows) == 3, "detector_candidate_not_unique")
                for row in rows:
                    if row.id in parents:
                        require(content_hash(draft_snapshot(row)) == originals[row.id], "original_draft_changed")
                    else:
                        require(not row.is_current and row.promotion_state == "candidate" and
                                row.content_md == "# 原稿\n\n作者保留正文。", "candidate_auto_adopted_or_wrong")
                        require(str(row.id) in [r["candidate_id"] for r in result
                                if r["status"] == "candidate_requires_review"],
                                "candidate_not_identified_by_api_receipt")
                chapters = list(await s.scalars(select(ChapterModel).where(
                    ChapterModel.project_id == p.id).order_by(ChapterModel.id)))
                hashes.append(content_hash([[draft_snapshot(r) for r in rows],
                                            [c.metadata_json for c in chapters]]))
        require(receipts[0] == receipts[1] and hashes[0] == hashes[1], "detector_second_pass_not_idempotent")
        require(sorted(r["status"] for r in receipts[0]) ==
                ["candidate_requires_review", "needs_source_review"], "mismatched_source_not_fenced")
        self.detector_receipts = receipts[0]
        return {"committed_passes": 2, "original_drafts_unchanged": 2,
                "new_candidates": 1, "automatically_promoted": 0,
                "receipts": receipts[0], "persisted_hash": hashes[0]}

    async def detector_receipt_integrity(self):
        require(bool(self.detector_receipts), "detector_migration_prerequisite_failed")
        mismatches = []
        async with self.sessions() as s:
            for receipt in self.detector_receipts:
                # Use the ID actually returned by the existing service API.
                reported_id = receipt["candidate_id"]
                candidate = await s.get(ChapterDraftVersionModel, UUID(reported_id)) if reported_id else None
                chapter = await s.get(ChapterModel, UUID(receipt["chapter_id"]))
                stored = chapter.metadata_json["framework_injection_repair"]["candidate_id"]
                if (reported_id != stored or (reported_id is not None and candidate is None)
                    or (receipt["status"] == "needs_source_review" and reported_id is not None)
                    or (receipt["status"] == "candidate_requires_review" and candidate is None)):
                    mismatches.append({"api_receipt": receipt, "candidate_exists_in_pg": candidate is not None,
                        "chapter_record_candidate_id": stored})
        if mismatches:
            raise ProbeFailure("detector_receipt_points_to_uncreated_candidate", mismatches)
        return {"all_returned_candidate_ids_match_pg": True}

    async def detector_changed_parent(self):
        from bestseller.services.rewrite_patch import text_hash

        p = self.project("changed-parent")
        self.fixture_projects.append(str(p.id))
        bridge = "旧生产器插入的段落。"
        body_one = f"# 同一稿号\n\n{bridge}\n\n她在院中修桌。"
        body_two = f"# 同一稿号\n\n{bridge}\n\n他在剧场调琴。"
        candidate_bodies = ["# 同一稿号\n\n她在院中修桌。", "# 同一稿号\n\n他在剧场调琴。"]
        chapter = ChapterModel(id=uuid4(), project_id=p.id, chapter_number=1, chapter_goal="probe",
            metadata_json={"deterministic_hook_echo_bridge": {"bridge": bridge}, "unrelated": "保留"})
        parent = ChapterDraftVersionModel(id=uuid4(), project_id=p.id, chapter_id=chapter.id,
            version_no=1, content_md=body_one, word_count=len(body_one), is_current=True,
            promotion_state="legacy_unverified", assembled_from_scene_draft_ids=[])
        async with self.sessions() as s, s.begin():
            s.add(p)
            await s.flush()
            s.add(chapter)
            await s.flush()
            s.add(parent)
        async with self.sessions() as s:
            initial_parent_hash = content_hash(draft_snapshot(await s.get(ChapterDraftVersionModel, parent.id)))
        async with self.sessions() as s, s.begin():
            first = await migrate_detector_insertions(s, await s.get(ProjectModel, p.id), apply=True)
        require(len(first) == 1 and first[0]["status"] == "candidate_requires_review", "first_candidate_missing")
        first_id = UUID(first[0]["candidate_id"])
        async with self.sessions() as s:
            saved_parent = await s.get(ChapterDraftVersionModel, parent.id)
            require(content_hash(draft_snapshot(saved_parent)) == initial_parent_hash, "first_migration_changed_parent")
            old_candidate = await s.get(ChapterDraftVersionModel, first_id)
            require(old_candidate is not None and old_candidate.content_md == candidate_bodies[0],
                    "first_candidate_wrong_body")
            old_candidate_hash = content_hash(draft_snapshot(old_candidate))
            old_receipt = deepcopy((await s.get(ChapterModel, chapter.id)).metadata_json["framework_injection_repair"])
        # Deliberately emulate the historical producer defect on THIS fixture only.
        # The migration itself must not modify either parent edition or old candidate.
        async with self.sessions() as s, s.begin():
            same_parent = await s.get(ChapterDraftVersionModel, parent.id)
            same_parent.content_md = body_two
            same_parent.word_count = len(body_two)
        async with self.sessions() as s:
            edited_parent_hash = content_hash(draft_snapshot(await s.get(ChapterDraftVersionModel, parent.id)))
        async with self.sessions() as s, s.begin():
            second = await migrate_detector_insertions(s, await s.get(ProjectModel, p.id), apply=True)
        require(len(second) == 1 and second[0]["status"] == "candidate_requires_review", "second_candidate_missing")
        second_id = UUID(second[0]["candidate_id"])
        require(first_id != second_id, "changed_parent_reused_old_candidate")
        async with self.sessions() as s:
            rows = list(await s.scalars(select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.project_id == p.id).order_by(ChapterDraftVersionModel.id)))
            require(len(rows) == 3, "changed_parent_candidate_count")
            by_id = {row.id: row for row in rows}
            require(content_hash(draft_snapshot(by_id[parent.id])) == edited_parent_hash,
                    "second_migration_changed_parent")
            require(content_hash(draft_snapshot(by_id[first_id])) == old_candidate_hash,
                    "old_candidate_rewritten")
            require(by_id[first_id].content_md == candidate_bodies[0] and
                    by_id[second_id].content_md == candidate_bodies[1], "candidate_editions_not_independent")
            require(by_id[parent.id].is_current and all(not by_id[cid].is_current and
                    by_id[cid].promotion_state == "candidate" for cid in (first_id, second_id)),
                    "changed_parent_candidate_auto_adopted")
            metadata = deepcopy((await s.get(ChapterModel, chapter.id)).metadata_json)
            require(metadata["unrelated"] == "保留", "changed_parent_unrelated_metadata_changed")
            archive = metadata.get("framework_injection_repair_history", {})
            require(len(archive) == 1 and archive.get(content_hash(old_receipt)) == old_receipt,
                    "old_receipt_not_content_addressed_and_preserved")
            current = metadata["framework_injection_repair"]
            require(old_receipt["parent_id"] == current["parent_id"] == str(parent.id) and
                    old_receipt["parent_hash"] == text_hash(body_one) and
                    current["parent_hash"] == text_hash(body_two) and
                    old_receipt["candidate_hash"] == text_hash(candidate_bodies[0]) and
                    current["candidate_hash"] == text_hash(candidate_bodies[1]) and
                    old_receipt["candidate_id"] == str(first_id) and current["candidate_id"] == str(second_id),
                    "candidate_receipts_not_bound_to_actual_parent_and_body")
            before_repeat = content_hash([[draft_snapshot(r) for r in rows], metadata])
        async with self.sessions() as s, s.begin():
            third = await migrate_detector_insertions(s, await s.get(ProjectModel, p.id), apply=True)
        async with self.sessions() as s:
            rows = list(await s.scalars(select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.project_id == p.id).order_by(ChapterDraftVersionModel.id)))
            repeated_metadata = (await s.get(ChapterModel, chapter.id)).metadata_json
            require(third == second and content_hash([[draft_snapshot(r) for r in rows], repeated_metadata])
                    == before_repeat, "changed_parent_repeat_not_idempotent")
        return {"project_id": str(p.id), "parent_id": str(parent.id), "parent_identity_unchanged": True,
            "producer_body_change_injected": True, "distinct_api_candidate_ids": [str(first_id), str(second_id)],
            "old_candidate_full_row_unchanged": True, "migration_did_not_edit_parent": True,
            "old_receipt_archive_key": content_hash(old_receipt), "old_receipt": old_receipt,
            "current_receipt": current, "committed_migration_passes": 3,
            "final_repeat_idempotent": True, "automatically_promoted": 0,
            "persisted_hash": before_repeat}

    async def execution_authorization(self):
        """Exercise operator authorization only on new probe scopes, never real work."""
        from bestseller.services.execution_authorization import authorize_execution_phase

        async def snapshot(ledger):
            async with self.sessions() as s:
                row = await s.get(ExecutionBudgetModel, ledger.scope_id)
                return {"max_calls": row.max_calls, "max_tokens": row.max_tokens,
                    "calls_used": row.calls_used, "tokens_charged": row.tokens_charged,
                    "deadline_at": row.deadline_at.isoformat(), "status": row.status,
                    "metadata": deepcopy(row.metadata_json)}

        def arguments(ledger, key="phase", **updates):
            return dict(scope_id=ledger.scope_id, idempotency_key=key,
                actor="codex-operator", reason="isolated PG authorization review; synthetic scope only",
                authorization_ref=f"pg-probe:{self.run_id}", expected_max_calls=1,
                expected_max_tokens=1000, additional_calls=3, additional_tokens=1000,
                additional_seconds=60, **updates)

        async def exhausted(suffix):
            ledger = self.ledger(suffix, execution_max_calls=1, execution_max_tokens=1000)
            claim = await ledger.claim("paid", "h")
            attempt = await ledger.reserve(claim, MODEL, "h", 100)
            await ledger.settle(attempt, "completed", actual_tokens=13, claim=claim, result=response())
            pending = await ledger.claim("over-cap", "h2")
            await expect_code(ledger.reserve(pending, MODEL, "h2", 1), "execution_budget_exhausted")
            await ledger.finish(pending, None)
            return ledger, attempt

        async def authorize(ledger, key="phase"):
            return await authorize_execution_phase(self.engine, **arguments(ledger, key))

        evidence = {}
        ledger, attempt = await exhausted("auth-same-key")
        before = await snapshot(ledger)
        calls = [asyncio.create_task(authorize(ledger)) for _ in range(8)]
        outcomes = await asyncio.gather(*calls, return_exceptions=True)
        require(all(isinstance(v, dict) and v == outcomes[0] for v in outcomes),
                "concurrent_authorization_same_key_not_idempotent")
        after = await snapshot(ledger)
        require((after["max_calls"], after["max_tokens"], after["calls_used"], after["tokens_charged"])
                == (4, 2000, 1, 13), "authorization_changed_usage_or_duplicated_credit")
        require(datetime.fromisoformat(after["deadline_at"]) == datetime.fromisoformat(before["deadline_at"])
                + timedelta(seconds=60), "authorization_deadline_not_cumulative")
        require(outcomes[0]["before"] == {k: before[k] for k in outcomes[0]["before"]}
                and len(after["metadata"]["operator_authorizations"]) == 1,
                "authorization_before_audit_or_receipt_lost")
        require((await ledger.transport_result(attempt))["result"] == response(), "authorization_deleted_paid_result")
        evidence["eight_same_key_requests"] = {"before": before, "after": after,
                                                "one_allowance_only": True}

        ledger, _ = await exhausted("auth-different-keys")
        outcomes = await asyncio.gather(authorize(ledger, "phase-a"), authorize(ledger, "phase-b"),
                                        return_exceptions=True)
        require(sum(isinstance(v, dict) for v in outcomes) == 1 and
                sum(isinstance(v, ExecutionBudgetError) and v.code == "execution_authorization_stale"
                    for v in outcomes) == 1, "concurrent_authorization_cas_failed")
        evidence["different_keys_same_expected_caps"] = {"one_success_one_stale": True,
                                                          "after": await snapshot(ledger)}

        rejected = []
        for state in ("unknown", "cancelled", "pending"):
            ledger = self.ledger(f"auth-reject-{state}", execution_max_calls=1, execution_max_tokens=1000)
            claim = await ledger.claim("old", "h")
            if state == "unknown":
                attempt = await ledger.reserve(claim, MODEL, "h", 100)
                await ledger.settle(attempt, "unknown")
            elif state == "cancelled":
                await ledger.cancel()
            before = await snapshot(ledger)
            code = "execution_authorization_inflight" if state == "pending" else "execution_authorization_scope_frozen"
            await expect_code(authorize(ledger), code)
            require(await snapshot(ledger) == before, "rejected_authorization_mutated_ledger")
            rejected.append({"state": state, "error": code, "full_budget_row_unchanged": True})
        evidence["frozen_and_inflight_rejections"] = rejected

        ledger, _ = await exhausted("auth-replay-after-cancel")
        receipt = await authorize(ledger)
        # Use a caller config that will not itself shrink the newly approved caps.
        live = self.ledger("auth-replay-after-cancel", execution_max_calls=20, execution_max_tokens=10000)
        await live.cancel()
        cancelled = await snapshot(live)
        replay = await authorize(ledger)
        require(replay == receipt and await snapshot(live) == cancelled and cancelled["status"] == "cancelled",
                "authorization_replay_unfroze_cancelled_scope")
        await expect_code(live.check(), "execution_cancelled")
        evidence["idempotent_receipt_replay_after_cancel"] = {"ledger_still_cancelled": True,
            "historical_receipt_after_status": replay["after"]["status"], "not_a_live_state_read": True}

        races = []
        for i in range(4):
            ledger, _ = await exhausted(f"auth-cancel-race-{i}")
            cancel = self.ledger(f"auth-cancel-race-{i}", execution_max_calls=20, execution_max_tokens=10000)
            jobs = [authorize(ledger), cancel.cancel()]
            if i % 2:
                jobs.reverse()
            results = await asyncio.gather(*jobs, return_exceptions=True)
            require(all(v is None or isinstance(v, dict) or (isinstance(v, ExecutionBudgetError)
                    and v.code == "execution_authorization_scope_frozen") for v in results),
                    "authorization_cancel_race_unexpected_error")
            row = await snapshot(ledger)
            require(row["status"] == "cancelled" and (row["calls_used"], row["tokens_charged"]) == (1, 13),
                    "authorization_cancel_race_unfroze_or_lost_usage")
            races.append({"status": row["status"], "authorizations": len(row["metadata"].get("operator_authorizations", {}))})
        evidence["cancel_authorization_races"] = races

        ledger, _ = await exhausted("auth-live-config-tighten")
        receipt = await authorize(ledger)
        old_config = self.ledger("auth-live-config-tighten", execution_max_calls=1, execution_max_tokens=1000)
        await old_config.check()
        tightened = await snapshot(old_config)
        require((tightened["max_calls"], tightened["max_tokens"]) == (1, 1000), "live_config_did_not_tighten")
        await authorize(ledger)
        require(await snapshot(ledger) == tightened, "idempotent_replay_regranted_tightened_budget")
        evidence["live_config_still_tightens"] = {"approved_caps": receipt["after"],
            "effective_caps_after_old_config_read": [tightened["max_calls"], tightened["max_tokens"]],
            "replay_does_not_restore_caps": True}
        return evidence


async def history_snapshot(engine):
    # Full-row hashes, ordered by ID. No source text or prompts leave the server.
    result = {}
    async with engine.connect() as c:
        for table in ("workflow_runs", "llm_runs"):
            row = (await c.execute(text(f"SELECT count(*) AS count, "
                f"md5(coalesce(string_agg(md5(to_jsonb(t)::text), '' ORDER BY id), '')) AS fingerprint "
                f"FROM {table} t"))).mappings().one()
            result[table] = dict(row)
    return result


async def run(target, authorization_only=False):
    settings = load_settings()
    url = make_url(settings.database.url).set(database=target)
    require(target == TARGET and url.database == target and "test" in url.database.lower(),
            "refused_non_allowlisted_test_database")
    require(url.drivername == "postgresql+asyncpg" and not url.query, "refused_driver_or_url_query_override")
    engine = create_async_engine(url, echo=False, hide_parameters=True, pool_size=10,
        max_overflow=0, connect_args={"timeout": 5, "server_settings": {
            "application_name": "bestseller_pg_recovery_probe", "statement_timeout": "30000",
            "lock_timeout": "3000"}})
    guarded_pids = set()

    @event.listens_for(engine.sync_engine, "connect")
    def verify_every_connection(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT current_database(), pg_backend_pid()")
            actual, pid = cursor.fetchone()
            require(actual == target and "test" in actual.lower(), "sql_database_guard_rejected")
            guarded_pids.add(pid)
        finally:
            cursor.close()

    report = None
    try:
        async with engine.connect() as c:
            actual = await c.scalar(text("SELECT current_database()"))
            require(actual == target and "test" in actual.lower(), "sql_database_guard_rejected")
            version = await c.scalar(text("SHOW server_version"))
            revision = list(await c.scalars(text("SELECT version_num FROM alembic_version")))
            projects_before = await c.scalar(text("SELECT count(*) FROM projects"))
        run_id = datetime.now(timezone.utc).strftime("%H%M%S") + "-" + uuid4().hex[:10]
        report = {"run_id": run_id, "database": actual, "server_version": version,
            "alembic_revision": revision, "projects_before": projects_before,
            "started_at": datetime.now(timezone.utc).isoformat(), "checks": [],
            "boundaries": ["Only the explicit allowlisted isolated database is connected.",
                "No DDL, table/database deletion, or historical workflow/LLM row changes.",
                "Ledger transport results and reconciliation evidence are synthetic; zero real LLM calls.",
                "Project/source and draft migration cases are probe-created PG fixtures, not restored manuscripts.",
                "Concurrent tasks use real independent PG transactions and a shared dispatcher epoch.",
                "Fresh engine connections and owners simulate recovery; no OS process kill is injected.",
                "No worker, API deployment, production consumer restart, or human/reader acceptance is tested.",
                "Probe rows are retained under the run ID; no cleanup DELETE is performed."]}
        # Reports and ALL DB writes occur only after both guards.
        before = await history_snapshot(engine)
        report["history_before"] = before
        require(revision == ["0043_execution_budget_ledger"], "unexpected_schema_revision")
        require([before[t]["count"] for t in ("workflow_runs", "llm_runs")] == [158, 149151],
                "unexpected_restored_history_count")
        probe = Probe(engine, settings, run_id)
        checks = [
            ("concurrent_operation_claim", probe.concurrent_claim),
            ("concurrent_call_reservation", lambda: probe.concurrent("calls", 3, 10000, 3)),
            ("concurrent_token_reservation", lambda: probe.concurrent("tokens", 20, 250, 2)),
            ("paid_result_survives_business_rollback_and_two_replays", probe.rollback_and_replay),
            ("cross_owner_freeze_cancel_and_idempotent_reconciliation", probe.freeze_cancel_reconcile),
            ("cancelled_late_return_retained_but_not_adoptable", probe.direct_cancel),
            ("deadline_blocks_send_but_preserves_paid_cache", probe.expired_paid),
            ("generic_source_migration_two_pg_commits", probe.source_migration),
            ("detector_source_migration_two_pg_commits", probe.detector_migration),
            ("detector_receipt_candidate_matches_pg", probe.detector_receipt_integrity),
            ("same_parent_changed_body_preserves_two_candidates_and_receipt_history", probe.detector_changed_parent),
        ]
        if authorization_only:
            checks = [("operator_execution_phase_authorization_pg", probe.execution_authorization)]
        for name, check in checks:
            try:
                detail = await check()
                report["checks"].append({"name": name, "status": "PASS", "evidence": detail})
            except Exception as exc:
                report["checks"].append({"name": name, "status": "FAIL", "error": safe_error(exc)})
            print(name + ": " + report["checks"][-1]["status"], flush=True)
        report["retained_scopes"] = probe.scopes
        report["retained_fixture_projects"] = probe.fixture_projects
        after = await history_snapshot(engine)
        report["history_after"] = after
        report["checks"].append({"name": "restored_history_full_row_fingerprints_unchanged",
            "status": "PASS" if before == after else "FAIL", "evidence": {"unchanged": before == after}})
        report["guarded_backend_pids"] = sorted(guarded_pids)
        report["status"] = "PASS" if all(c["status"] == "PASS" for c in report["checks"]) else "FAIL"
    except Exception as exc:
        if report is None:
            print(json.dumps({"status": "REFUSED", "error": safe_error(exc)}))
            return 2
        report["status"], report["fatal_error"] = "FAIL", safe_error(exc)
    finally:
        await engine.dispose()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    prefix = REPORT_DIR / f"PG-RECOVERY-{report['run_id']}"
    # Existing report directory is deliberately required; never create arbitrary paths.
    with prefix.with_suffix(".json").open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    lines = [f"PostgreSQL recovery probe: {report['status']}", "",
             f"Database: {report['database']}; PostgreSQL: {report['server_version']}",
             f"Run: {report['run_id']}", "", "Checks:"]
    for check in report["checks"]:
        lines.append(f"- {check['status']}: {check['name']}")
        if check["status"] == "FAIL":
            lines.append("  " + json.dumps(check.get("error", {}), ensure_ascii=False))
    lines.extend(["", "Evidence boundary:", *[f"- {b}" for b in report["boundaries"]],
                  "", f"Detailed evidence: {prefix.name}.json", "",
                  "Reproduce with the same explicit --database argument. Each run retains new UUID-scoped evidence."])
    with prefix.with_suffix(".md").open("x", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps({"status": report["status"], "report": str(prefix.with_suffix('.json'))}))
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, choices=[TARGET])
    parser.add_argument("--authorization-only", action="store_true",
                        help="Review operator allowance on synthetic isolated scopes only")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.database, authorization_only=args.authorization_only))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": safe_error(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
