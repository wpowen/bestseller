"""Offline only. Run python -B -m unittest discover -s <this directory> -v.

Synthetic gateway returns exercise receipt validation; they are NOT real verdicts.
No shared pytest/conftest/config loading, no database, no real provider dispatch.
"""

from __future__ import annotations

import ast
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src/bestseller").is_dir())
HERE = ROOT / "reports/project-reliability-review-2026-10-24/implementation/whole-semantic-v4-stage"
from bestseller.services import fanqie_short_semantic_review as stage
from bestseller.domain.book_design_snapshot import build_book_design_snapshot
from bestseller.services.budget import ExecutionBudgetError, ExecutionOperationBusyError
from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story
from bestseller.services.fanqie_short_ranking_gate import _cjk_slice

PROJECT = str(uuid4())
WORKFLOW = str(uuid4())
SOURCE = (
    "今晚房东将收回我租的门面，我得带走工具。\n"
    "椅子的断腿挡住了我的去路。我卸下断裂的椅腿。我装好新腿后坐稳了。\n"
    "柜门卡住了我的衣裳。我磨平柜门边缘。我取出了完好的衣裳。\n"
    "台灯的灯丝断了。我换上一枚新的灯泡。灯光照亮了我的工作台。\n"
    "行李无处安放。我付清寄存费用。店主把我的箱子收入了柜台。\n"
)
TITLE = "搬离旧铺前，我修好了最后一张木椅"


def contract(*, include_strategy=True, seed_source="explicit"):
    # Reuse the native validated intent shape, but all literary fixtures below
    # are synthetic. The immutable actual readback is never rewritten.
    original = json.loads(
        (HERE.parent / "original-creative-contract-readback.json").read_text()
    )
    intent = deepcopy(original["selected_contracts"]["creation_intent_contract"])
    intent.update(
        concept_seed="搬铺前修复旧家具并保管自己的物件，保留债务与告别。",
        chapter_count=1,
    )
    intent["field_sources"]["concept_seed"] = seed_source
    common = {
        "input_hash": "a" * 64,
        "champion_id": "synthetic-concept",
        "target_chapters": 1,
    }
    promise = "跟随维修者在搬铺前修复旧家具，看到付出行动后能够带走物件的具体结果。"
    hook = {
        **common,
        "approved": True,
        "one_liner": promise,
        "title_strategy": "标题以搬铺期限、亲手修复和具体物件呈现已批准的可读承诺。",
    }
    if not include_strategy:
        hook.pop("title_strategy")
    concept = {
        **common,
        "hook_card": hook,
        "story_spine": {**common},
        "seriality_proof": {**common},
    }
    design = {
        "source_contract_hash": common["input_hash"],
        "source_champion_id": common["champion_id"],
        "source_target_chapters": 1,
        **{k: concept[k] for k in ("hook_card", "story_spine", "seriality_proof")},
    }
    book = build_book_design_snapshot(
        creation_intent=intent,
        protagonist="林青",
        tone="现实克制与明确告别",
        target_words=8000,
        chapter_count=1,
        reader_promise=promise,
        approved_story_design=design,
    ).model_dump(mode="json")
    c = {
        "creation_intent_contract": intent,
        "concept_seed": intent["concept_seed"],
        "pov": intent["pov"],
        "book_design_snapshot": book,
        "concept_contract": concept,
        "reader_promise": promise,
    }
    sid, iid = str(uuid4()), str(uuid4())
    content = {
        "concept_contract": concept,
        "creation_intent_hash": stage.fingerprint(intent),
    }
    content.update(
        title=TITLE,
        premise=intent["concept_seed"],
        synopsis="维修者搬铺前修复家具、带走物件，在清场期限中保留自己的决定。",
        writing_profile={"market": {"reader_promise": promise}},
    )
    digest = stage.fingerprint(content)
    c["approval_evidence"] = {
        "project_id": PROJECT,
        "title_revision_status": {
            "blocked": "title_revision_call_failed",
            "retryable": True,
            "error_type": "DBAPIError",
            "quality_status": "needs_revision",
            "readability_issue": "",
        },
        "conception_approved": True,
        "planning_status": "conception_approved",
        "canonical_snapshot": {
            "id": sid,
            "project_id": PROJECT,
            "artifact_type": "conception_snapshot",
            "status": "canonical",
            "content": content,
        },
        "creation_canonical_snapshot_id": sid,
        "creation_canonical_snapshot_hash": digest,
        "book_design_adoption_source": {
            "kind": "adopted_conception",
            "snapshot_id": sid,
            "content_hash": digest,
        },
        "book_design_snapshot_id": book["snapshot_id"],
        "book_design_snapshot_hash": book["source_hash"],
        "book_design_snapshot_status": "locked",
        "creation_intent_snapshot_id": iid,
        "creation_intent_contract_hash": stage.fingerprint(intent),
        "intent_snapshot": {
            "id": iid,
            "project_id": PROJECT,
            "artifact_type": "creation_intent",
            "status": "validated",
            "content": intent,
        },
    }
    return c


def finding(code, text=SOURCE):
    if code == "payoff_density_too_low":
        return stage.evaluate_payoff_density_gate(text).findings[0].to_dict()
    return {
        "code": code,
        "severity": "critical",
        "message": "Synthetic lexical proxy finding",
        "evidence": "Detector evidence preserved verbatim",
        "phase": "opening",
        "target": stage.ALLOWLIST.get(code, "whole_story"),
    }


def kwargs(codes=("opening_pressure_missing",), text=SOURCE):
    rows = [finding(code, text) for code in codes]
    return dict(
        project_id=PROJECT,
        workflow_run_id=WORKFLOW,
        operation_namespace="whole-short-review",
        execution_scope_id="synthetic-execution-scope",
        current_scope="synthetic-whole-assembly",
        full_text=text,
        title=TITLE,
        protagonist="我",
        manifest={
            "assembly_sha256": stage._sha(text),
            "sources": [
                {
                    "chapter": 1,
                    "draft_id": str(uuid4()),
                    "body_sha256": stage._sha(text),
                    "promotion_state": "candidate",
                }
            ],
        },
        approved_contract=contract(),
        original_report={
            "passed": False,
            "taboo_signals": [],
            "ranking_report": {"passed": False, "findings": rows},
            "short_v2_report": {"passed": False, "findings": deepcopy(rows)},
        },
    )


def span(p, quote, start=0, source="full_text"):
    a = p[source].index(quote, start)
    return dict(source=source, start=a, end=a + len(quote), quote=quote)


def events(p):
    triples = [
        ("椅子的断腿挡住了我的去路。", "我卸下断裂的椅腿。", "我装好新腿后坐稳了。"),
        ("柜门卡住了我的衣裳。", "我磨平柜门边缘。", "我取出了完好的衣裳。"),
        ("台灯的灯丝断了。", "我换上一枚新的灯泡。", "灯光照亮了我的工作台。"),
        ("行李无处安放。", "我付清寄存费用。", "店主把我的箱子收入了柜台。"),
    ]
    return [
        {
            "goal": span(p, g),
            "action": span(p, a),
            "result": span(p, r),
            "causal_link": "主角针对此前具体阻碍实施处置，该动作使目标获得了新的完成状态。",
            "status": "completed",
        }
        for g, a, r in triples
    ]


def row(p, item, verdict="not_supported"):
    scope = item["scope"]
    evidence = {**scope, "quote": p[scope["source"]][scope["start"] : scope["end"]]}
    r = {k: item[k] for k in ("id", "code", "target")}
    r.update(
        verdict=verdict,
        reason={
            "confirmed": "缺失成立：窗口内只有背景，未产生符合要求的实际变化。",
            "not_supported": "缺失不成立：指定窗口内已经给出主角的期限和当前损失。",
            "uncertain": "无法判断：现有来源不足以确定是否满足实际读者承诺。",
        }[verdict],
        evidence=[evidence],
        events=[],
        contract_evidence=[],
        readable_promise="",
        contract_resolution="",
        title_review={},
    )
    return r


def title_row(p, item):
    r = row(p, item)
    paths = {
        "/reader_promise",
        "/creation_intent_contract/concept_seed",
        "/approval_evidence/canonical_snapshot/content/title",
        "/approval_evidence/canonical_snapshot/content/premise",
        "/approval_evidence/canonical_snapshot/content/writing_profile/market/reader_promise",
        "/book_design_snapshot/reader_promise",
        "/book_design_snapshot/approved_story_design/hook_card/one_liner",
    }
    paths |= {
        path
        for path, _ in stage._strings(p["approved_contract"])
        if path.rsplit("/", 1)[-1] in {"title_pattern", "title_strategy"}
    }
    strings = dict(stage._strings(p["approved_contract"]))
    r["contract_evidence"] = [
        {"path": path, "start": 0, "end": len(strings[path]), "quote": strings[path]}
        for path in sorted(paths)
        if path in strings
    ]
    r["readable_promise"] = "搬铺期限内完成家具修复，让读者看到具体物件重新可用。"
    r["contract_resolution"] = (
        "默认标题合同为“"
        + p["default_title_contract"]
        + "”；此处已批准的具体标题策略用期限、修复行动与结果承诺满足可读性，来源逐项引用。"
    )
    r["title_review"] = {
        "readability": "supported",
        "creator_alignment": "supported",
        "default_basis": "explicit_creator_and_canonical_content",
        "reason": "题目提供搬铺期限、修复动作和家具可用的具体期待；明确创作种子与已批准叙事都支持这个选择，不能强加反派翻车。",
        "body_evidence": [span(p, "我卸下断裂的椅腿。我装好新腿后坐稳了。")],
        "body_reason": "正文先给出断腿阻碍，再呈现卸下和换好后的坐稳结果，支持题目所说的具体修复承诺。",
    }
    return r


def response(p, rows=None):
    rows = (
        rows if rows is not None else [row(p, item) for item in p["reviews_requested"]]
    )
    raw = stage._json({"binding_sha256": p["binding_sha256"], "reviews": rows})
    # Deliberately simulated native receipt shape. No credentials or dispatch.
    result = {
        "content": raw,
        "provider": "openai",
        "model_name": "critic-model",
        "llm_run_id": str(uuid4()),
        "fallback_used": False,
        "fallback_source": None,
        "finish_reason": "stop",
        "tool_calls": None,
        "metadata": {"llm_run_flushed": True},
    }
    run = result["llm_run_id"]
    result["metadata"]["prompt_payload_receipt"] = {
        "enabled": True,
        "status": "stored",
        "replayable": True,
        **{
            k + "_ref": f"runtime/llm_payloads/{run}.{k}.json"
            for k in ("request", "response")
        },
    }

    def block(text):
        return dict(text=text, truncated=False, original_chars=len(text))

    common = {
        "schema_version": "llm-payload.v1",
        "replayable": True,
        "replayability_reasons": [],
        "llm_run_id": run,
        "project_id": p["project_id"],
        "workflow_run_id": p["workflow_run_id"],
    }
    retained = {
        "request": {
            **common,
            "kind": "request",
            "logical_role": "critic",
            "prompt_template": "fanqie_short_semantic_review",
            "prompt_version": stage.PROMPT_VERSION,
            "messages": {
                "system": block(stage.SYSTEM_PROMPT),
                "user": block(stage._user_prompt(p)),
            },
        },
        "response": {
            **common,
            "kind": "response",
            "content": block(raw),
            "fallback_used": False,
            "provider": result["provider"],
            "model": result["model_name"],
            "finish_reason": "stop",
        },
    }
    effective_hash = "b" * 64
    request = stage.native_request_fields(p)
    operation_key = stage._sha(
        p["execution_scope_id"] + "\n" + request["operation_id"] + ":sample:0"
    )
    result["metadata"].update(
        effective_input_receipt={
            "schema": "effective-input.v1",
            "hash": effective_hash,
            "model": result["model_name"],
            "messages_source": "system_user",
            "tools_included": False,
            "legacy_prompt_hash": stage._sha(
                stage.SYSTEM_PROMPT + "\n\n" + stage._user_prompt(p)
            ),
        },
        execution_receipt={
            "scope_id": p["execution_scope_id"],
            "operation_key": operation_key,
            "policy": "execution-budget.v1",
            "attempts": [
                {"effective_input_hash": effective_hash, "model": result["model_name"]}
            ],
        },
        paid_call_origin={
            "schema": "paid-call-origin.v1",
            "status": "recorded",
            "project_id": p["project_id"],
            "workflow_run_id": p["workflow_run_id"],
            "logical_role": "critic",
            "prompt_template": "fanqie_short_semantic_review",
            "prompt_version": stage.PROMPT_VERSION,
            "prompt_hash": effective_hash,
        },
    )
    retained["request"]["effective"] = {
        "provider": result["provider"],
        "model": result["model_name"],
    }
    retained["request"]["requested"] = {
        "max_tokens_override": stage.POLICY["max_output_tokens"],
        "temperature_override": 0,
        "model_tier": "strong",
    }
    return result, retained


def native_payload():
    x = json.loads(
        (HERE.parent / "original-native-creative-approval-readback.json").read_text()
    )
    v = json.loads((HERE.parent / "v8-whole-gate-preview.json").read_text())
    c = {**x["selected_contracts"], "approval_evidence": x["native_approval_evidence"]}
    return stage.prepare_review(
        project_id=c["approval_evidence"]["project_id"],
        workflow_run_id=WORKFLOW,
        current_scope="supplied-native-readback-frozen-v8-not-current-adoption",
        full_text=x["frozen_v8_full_text"],
        title=x["title"],
        protagonist="我",
        manifest={k: v[k] for k in ("assembly_sha256", "sources")},
        approved_contract=c,
        original_report=v["review"],
        operation_namespace="native-approval-offline-replay",
        execution_scope_id="offline-replay-no-active-paid-scope",
        methodology_contract_mode="strict",
    )


def paid_source_payload():
    """Rebuild the exact original-four saved request offline, not a new readback."""
    saved = json.loads((HERE.parent / "four-whole-semantic-save-recovery.json").read_text())
    original = saved["result"]["request_payload"]
    keys = (
        "project_id", "workflow_run_id", "current_scope", "full_text", "title",
        "protagonist", "manifest", "approved_contract", "original_report",
        "operation_namespace", "execution_scope_id", "unlock_line_ratio",
        "methodology_contract_mode",
    )
    return original, stage.prepare_review(**{k: original[k] for k in keys})


class OfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guards = [
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("network forbidden"),
            ),
            patch.object(
                socket.socket,
                "connect_ex",
                side_effect=AssertionError("network forbidden"),
            ),
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network forbidden"),
            ),
        ]
        for guard in cls.guards:
            guard.start()
        cls.original_hashes = {
            p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                HERE.parent / "original-native-creative-approval-readback.json",
                HERE.parent / "whole-semantic-stage/MAIN-REVIEW.md",
                ROOT / "src/bestseller/services/creation_contract_authority.py",
                ROOT / "src/bestseller/services/planning_string_pool.py",
                HERE.parent / "original-creative-contract-readback.json",
                HERE.parent / "v8-whole-gate-preview.json",
                ROOT / "src/bestseller/services/fanqie_short_quality.py",
                ROOT / "src/bestseller/services/fanqie_short_gate_v2.py",
                ROOT / "src/bestseller/services/fanqie_short_ranking_gate.py",
                ROOT / "src/bestseller/services/fanqie_long_semantic_review.py",
            )
        }

    @classmethod
    def tearDownClass(cls):
        for p, digest in cls.original_hashes.items():
            if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                raise AssertionError(f"Source changed during offline run: {p}")
        for guard in cls.guards:
            guard.stop()

    def validate(self, p, result=None, retained=None):
        if result is None:
            result, retained = response(p)
        return stage.validate_response(
            p,
            result,
            current_binding=deepcopy(p["binding"]),
            retained_payloads=retained,
        )

    def closed(self, report):
        self.assertIsNotNone(report["error"])
        self.assertTrue(all(r["verdict"] == "uncertain" for r in report["reviews"]))

    def test_positive_pressure_evidence_and_original_preserved(self):
        k = kwargs()
        before = deepcopy(k)
        p = stage.prepare_review(**k)
        report = self.validate(p)
        self.assertIsNone(report["error"])
        self.assertEqual(report["reviews"][0]["verdict"], "not_supported")
        self.assertEqual(k, before)
        self.assertEqual(report["original_report"], k["original_report"])
        self.assertFalse(report["original_report"]["passed"])
        self.assertNotIn("passed", report)
        self.assertNotIn("dismissed_codes", report)
        self.assertEqual(len(p["reviews_requested"]), 1)
        self.assertEqual(
            p["reviews_requested"][0]["origins"], ["ranking_report", "short_v2_report"]
        )

    def test_confirmed_and_uncertain_are_retained(self):
        for verdict in ("confirmed", "uncertain"):
            with self.subTest(verdict=verdict):
                p = stage.prepare_review(**kwargs())
                r, docs = response(p, [row(p, p["reviews_requested"][0], verdict)])
                self.assertEqual(
                    self.validate(p, r, docs)["reviews"][0]["verdict"], verdict
                )

    def test_response_coverage_atomic(self):
        p = stage.prepare_review(
            **kwargs(("opening_pressure_missing", "first_screen_pressure_missing"))
        )
        good = [row(p, item) for item in p["reviews_requested"]]
        for rows in (
            good[:1],
            good + [good[0]],
            [good[0], good[0]],
            [good[0], {**good[1], "id": "extra"}],
            [good[0], None],
        ):
            with self.subTest(rows=rows):
                r, docs = response(p, rows)
                self.closed(self.validate(p, r, docs))

    def test_json_malformed_duplicate_keys_extra_fields(self):
        p = stage.prepare_review(**kwargs())
        for raw in (
            "{}",
            "```json\n{}\n```",
            '{"binding_sha256":"x","binding_sha256":"y","reviews":[]}',
            "[]",
            "null",
            '{"reviews":NaN}',
            "partial",
        ):
            with self.subTest(raw=raw):
                r, docs = response(p)
                r["content"] = raw
                docs["response"]["content"] = dict(
                    text=raw, truncated=False, original_chars=len(raw)
                )
                self.closed(self.validate(p, r, docs))

    def test_wrong_target_code_reason_evidence(self):
        p = stage.prepare_review(**kwargs())
        for key, value in (
            ("target", "whole_story"),
            ("code", "wordcount"),
            ("verdict", "PASS"),
            ("reason", "缺失成立：但我返回了缺失不成立的结论。"),
            ("evidence", []),
            ("extra", True),
        ):
            with self.subTest(key=key):
                item = row(p, p["reviews_requested"][0])
                item[key] = value
                r, docs = response(p, [item])
                self.closed(self.validate(p, r, docs))

    def test_literal_offsets_and_source_validation(self):
        p = stage.prepare_review(**kwargs(("first_screen_pressure_missing",)))
        for update in (
            {"quote": "不存在的句子"},
            {"source": "title"},
            {"start": -1},
            {"start": True},
            {"end": len(SOURCE) + 1},
        ):
            with self.subTest(update=update):
                item = row(p, p["reviews_requested"][0])
                item["evidence"][0].update(update)
                r, docs = response(p, [item])
                self.closed(self.validate(p, r, docs))

    def test_exact_80_300_and_raw_30_percent_windows(self):
        text = "  " + "甲，" * 400 + "\n" + "z" * 500 + "  "
        p = stage.prepare_review(
            **kwargs(
                (
                    "opening_pressure_missing",
                    "first_screen_pressure_missing",
                    "unlock_payoff_missing",
                ),
                text,
            )
        )
        for item in p["reviews_requested"]:
            sc = item["scope"]
            self.assertEqual(sc["start"], 2)
            limit = 300 if item["target"] == "opening_300" else 80
            expected = (
                text.strip()[: max(300, int(len(text.strip()) * 0.30))]
                if item["target"] == "unlock_30_percent"
                else _cjk_slice(text.strip(), limit)
            )
            self.assertEqual(text[sc["start"] : sc["end"]], expected)
        for code, n in (
            ("opening_pressure_missing", 300),
            ("first_screen_pressure_missing", 80),
        ):
            q = stage.prepare_review(
                **kwargs((code,), "甲" * n + "今晚房东将收回我租的门面。")
            )
            item = row(q, q["reviews_requested"][0])
            item["evidence"] = [span(q, "今晚房东将收回我租的门面。")]
            r, docs = response(q, [item])
            self.closed(self.validate(q, r, docs))

    def test_non_cjk_window_matches_native_helper(self):
        p = stage.prepare_review(
            **kwargs(("first_screen_pressure_missing",), "a" * 600)
        )
        self.assertEqual(p["reviews_requested"][0]["scope"]["end"], 480)

    def test_binding_mutation_stale_current_or_replayed_response(self):
        p = stage.prepare_review(**kwargs())
        r, docs = response(p)
        for field in (
            "full_text",
            "title",
            "protagonist",
            "current_scope",
            "manifest",
            "approved_contract",
            "policy",
            "workflow_run_id",
            "operation_namespace",
            "original_report",
            "unlock_line_ratio",
            "methodology_contract_mode",
            "title_revision_status",
        ):
            with self.subTest(field=field):
                q = deepcopy(p)
                q[field] = "changed"
                self.closed(self.validate(q, r, docs))
        other = deepcopy(p["binding"])
        other["full_text_sha256"] = "0" * 64
        self.closed(
            stage.validate_response(p, r, current_binding=other, retained_payloads=docs)
        )
        rows = [row(p, p["reviews_requested"][0])]
        r, docs = response(p, rows)
        answer = json.loads(r["content"])
        answer["binding_sha256"] = "0" * 64
        r["content"] = stage._json(answer)
        docs["response"]["content"] = dict(
            text=r["content"], truncated=False, original_chars=len(r["content"])
        )
        self.closed(self.validate(p, r, docs))

    def test_missing_identity_ratio_manifest_capacity_are_rejected(self):
        for key, value in (
            ("project_id", None),
            ("workflow_run_id", ""),
            ("current_scope", ""),
            ("unlock_line_ratio", 0.31),
            ("operation_namespace", ""),
            ("protagonist", "陌生人"),
            ("manifest", {}),
            ("full_text", ""),
        ):
            with self.subTest(key=key):
                k = kwargs()
                k[key] = value
                with self.assertRaises((ValueError, TypeError)):
                    stage.prepare_review(**k)
        k = kwargs(text="甲" * 121000)
        with self.assertRaises(stage.ReviewInputError):
            stage.prepare_review(**k)

    def test_contract_and_approval_mismatch_rejected_without_rebuilding(self):
        for change in (
            "hash",
            "id",
            "tone",
            "intent",
            "hook_approved",
            "stored_hash",
            "stored_id",
            "stored_status",
            "adoption",
            "canonical",
            "promise",
            "concept",
            "pov",
        ):
            with self.subTest(change=change):
                k = kwargs()
                c = k["approved_contract"]
                b = c["book_design_snapshot"]
                a = c["approval_evidence"]
                if change == "hash":
                    b["source_hash"] = "0" * 64
                elif change == "id":
                    b["snapshot_id"] = "bad"
                elif change == "tone":
                    b["tone"] = "changed"
                elif change == "intent":
                    c["creation_intent_contract"]["concept_seed"] = "changed"
                elif change == "hook_approved":
                    b["approved_story_design"]["hook_card"]["approved"] = False
                elif change == "stored_hash":
                    a["book_design_snapshot_hash"] = "0" * 64
                elif change == "stored_id":
                    a["book_design_snapshot_id"] = "bad"
                elif change == "stored_status":
                    a["book_design_snapshot_status"] = "unlocked"
                elif change == "adoption":
                    a["book_design_adoption_source"]["snapshot_id"] = str(uuid4())
                elif change == "canonical":
                    a["canonical_snapshot"]["content"]["changed"] = True
                elif change == "promise":
                    c["reader_promise"] = ""
                elif change == "concept":
                    c["concept_contract"]["hook_card"]["one_liner"] = "changed"
                elif change == "pov":
                    c["pov"] = "third_person"
                with self.assertRaises((ValueError, TypeError)):
                    stage.prepare_review(**k)

    def test_provider_receipt_nonreal_fallback_incomplete(self):
        p = stage.prepare_review(**kwargs())
        for key, value in (
            ("provider", "mock"),
            ("model_name", "fixture"),
            ("provider", "offline-synthetic"),
            ("provider", ""),
            ("llm_run_id", ""),
            ("fallback_used", True),
            ("fallback_used", "false"),
            ("fallback_source", "local"),
            ("finish_reason", "length"),
            ("finish_reason", None),
            ("tool_calls", [{}]),
        ):
            with self.subTest(key=key, value=value):
                r, docs = response(p)
                r[key] = value
                self.closed(self.validate(p, r, docs))

    def test_retention_missing_truncated_or_wrong_identity(self):
        p = stage.prepare_review(**kwargs())
        for change in (
            "absent",
            "unflushed",
            "disabled",
            "not_replayable",
            "path",
            "schema",
            "workflow",
            "project",
            "request",
            "response",
            "truncated",
            "reason",
            "role",
            "provider",
        ):
            with self.subTest(change=change):
                r, docs = response(p)
                if change == "absent":
                    docs = {}
                elif change == "unflushed":
                    r["metadata"]["llm_run_flushed"] = False
                elif change == "disabled":
                    r["metadata"]["prompt_payload_receipt"]["enabled"] = False
                elif change == "not_replayable":
                    docs["request"]["replayable"] = False
                elif change == "path":
                    r["metadata"]["prompt_payload_receipt"]["request_ref"] = (
                        "/etc/passwd"
                    )
                elif change == "schema":
                    docs["request"]["schema_version"] = "unknown"
                elif change == "workflow":
                    docs["response"]["workflow_run_id"] = str(uuid4())
                elif change == "project":
                    docs["request"]["project_id"] = str(uuid4())
                elif change == "request":
                    docs["request"]["messages"]["user"]["text"] += "x"
                elif change == "response":
                    docs["response"]["content"]["text"] += "x"
                elif change == "truncated":
                    docs["response"]["content"]["truncated"] = True
                elif change == "reason":
                    docs["response"]["replayability_reasons"] = ["response_truncated"]
                elif change == "role":
                    docs["request"]["logical_role"] = "writer"
                elif change == "provider":
                    docs["response"]["provider"] = "fixture"
                self.closed(self.validate(p, r, docs))

    def test_density_requires_original_N_distinct_completed_events(self):
        p = stage.prepare_review(
            **kwargs(("payoff_density_too_low",), SOURCE + "屋内仍然安静。" * 1800)
        )
        item = p["reviews_requested"][0]
        self.assertEqual(item["expected_payoff_count"], 4)
        rrow = row(p, item)
        rrow["events"] = events(p)
        r, docs = response(p, [rrow])
        self.assertIsNone(self.validate(p, r, docs)["error"])
        for es in ([], events(p)[:1], events(p)[:3], [events(p)[0]] * 4):
            with self.subTest(n=len(es)):
                rr = deepcopy(rrow)
                rr["events"] = es
                r, docs = response(p, [rr])
                self.closed(self.validate(p, r, docs))

    def test_density_threshold_original_boundaries(self):
        for n, expected in ((1999, 2), (2000, 3), (7499, 3), (10000, 4)):
            with self.subTest(n=n):
                p = stage.prepare_review(
                    **kwargs(("payoff_density_too_low",), "甲" * n)
                )
                self.assertEqual(
                    p["reviews_requested"][0]["expected_payoff_count"], expected
                )
        k = kwargs(("payoff_density_too_low",))
        k["original_report"]["ranking_report"]["findings"][0]["message"] = "期望至少1个"
        with self.assertRaises(stage.ReviewInputError):
            stage.prepare_review(**k)

    def test_events_reject_negation_wishes_trimmed_context_and_bad_causality(self):
        for word in ("没有", "尚未", "希望", "打算", "如果", "可能"):
            with self.subTest(word=word):
                text = SOURCE.replace(
                    "我装好新腿后坐稳了。", "我" + word + "装好新腿后坐稳了。"
                )
                p = stage.prepare_review(**kwargs(("payoff_density_too_low",), text))
                es = events(stage.prepare_review(**kwargs()))[:1]
                es[0]["result"] = span(p, "装好新腿后坐稳了。")
                # Need 2 events for short source; second one is genuinely separate.
                second = {
                    "goal": span(p, "柜门卡住了我的衣裳。"),
                    "action": span(p, "我磨平柜门边缘。"),
                    "result": span(p, "我取出了完好的衣裳。"),
                    "status": "completed",
                    "causal_link": "磨平门边让被卡住的衣物能够完整取出来。",
                }
                rr = row(p, p["reviews_requested"][0])
                rr["events"] = [es[0], second]
                r, docs = response(p, [rr])
                self.closed(self.validate(p, r, docs))

    def test_events_reject_duplicate_reprints_even_at_distinct_offsets(self):
        text = SOURCE + SOURCE
        p = stage.prepare_review(**kwargs(("payoff_density_too_low",), text))
        first = events(p)[0]
        second = deepcopy(first)
        for key in ("goal", "action", "result"):
            second[key] = span(p, first[key]["quote"], len(SOURCE))
        rr = row(p, p["reviews_requested"][0])
        rr["events"] = [first, second]
        r, docs = response(p, [rr])
        self.closed(self.validate(p, r, docs))

    def test_payoff_evidence_cannot_use_later_event_outside_unlock(self):
        text = "甲" * 1000 + SOURCE
        p = stage.prepare_review(**kwargs(("unlock_payoff_missing",), text))
        rr = row(p, p["reviews_requested"][0])
        rr["events"] = events(p)[:1]
        r, docs = response(p, [rr])
        self.closed(self.validate(p, r, docs))

    def test_title_positive_requires_literal_snapshot_and_current_promise(self):
        p = stage.prepare_review(**kwargs(("title_click_contract_missing",)))
        rr = title_row(p, p["reviews_requested"][0])
        r, docs = response(p, [rr])
        self.assertIsNone(self.validate(p, r, docs)["error"])
        for change in (
            "genre_only",
            "current_promise",
            "snapshot_promise",
            "strategy",
            "default",
            "literal",
        ):
            with self.subTest(change=change):
                bad = deepcopy(rr)
                if change == "genre_only":
                    bad["contract_evidence"] = []
                    bad["contract_resolution"] = "现实题材不需要复仇。"
                elif change == "current_promise":
                    bad["contract_evidence"] = [
                        s
                        for s in bad["contract_evidence"]
                        if s["path"] != "/reader_promise"
                    ]
                elif change == "snapshot_promise":
                    bad["contract_evidence"] = [
                        s
                        for s in bad["contract_evidence"]
                        if s["path"] != "/book_design_snapshot/reader_promise"
                    ]
                elif change == "strategy":
                    bad["contract_evidence"] = [
                        s
                        for s in bad["contract_evidence"]
                        if not s["path"].endswith("title_strategy")
                    ]
                elif change == "default":
                    bad["contract_resolution"] = (
                        "这个标题提供清楚的期限和具体行动，所以无条件忽略默认规则。"
                    )
                elif change == "literal":
                    bad["contract_evidence"][0]["quote"] = "伪造的批准合同"
                r, docs = response(p, [bad])
                self.closed(self.validate(p, r, docs))

    def test_title_genre_excuse_cannot_replace_native_current_title_provenance(self):
        for change in (
            "missing_canonical_title",
            "wrong_canonical_title",
            "wrong_current_title",
            "promise_mismatch",
            "genre_only",
        ):
            with self.subTest(change=change):
                k = kwargs(("title_click_contract_missing",))
                c = k["approved_contract"]
                a = c["approval_evidence"]
                content = a["canonical_snapshot"]["content"]
                if change == "missing_canonical_title":
                    content.pop("title")
                elif change == "wrong_canonical_title":
                    content["title"] = "另一个题目"
                elif change == "wrong_current_title":
                    k["title"] += "新标题"
                elif change == "promise_mismatch":
                    content["writing_profile"]["market"]["reader_promise"] += "漂移"
                digest = stage.fingerprint(content)
                a["creation_canonical_snapshot_hash"] = digest
                a["book_design_adoption_source"]["content_hash"] = digest
                p = stage.prepare_review(**k)
                rr = title_row(p, p["reviews_requested"][0])
                if change == "genre_only":
                    rr["title_review"]["default_basis"] = "genre_only"
                rr["contract_resolution"] = (
                    p["default_title_contract"]
                    + "；这里只是现实题材，所以默认合同直接不适用。"
                )
                result, docs = response(p, [rr])
                self.closed(self.validate(p, result, docs))

    def test_title_native_schema_needs_no_extra_certificate_or_title_strategy(self):
        k = kwargs(("title_click_contract_missing",))
        k["approved_contract"] = contract(include_strategy=False)
        p = stage.prepare_review(**k)
        rr = title_row(p, p["reviews_requested"][0])
        self.assertNotIn("title_contract", p["approved_contract"]["approval_evidence"])
        self.assertFalse(
            any(
                path.endswith("title_strategy")
                for path, _ in stage._strings(p["approved_contract"])
            )
        )
        result, docs = response(p, [rr])
        report = self.validate(p, result, docs)
        self.assertIsNone(report["error"])
        self.assertEqual(report["reviews"][0]["verdict"], "not_supported")
        self.assertEqual(report["title_revision_status"], p["title_revision_status"])
        self.assertEqual(
            report["title_revision_status"]["quality_status"], "needs_revision"
        )
        self.assertNotIn("passed", report)

    def test_title_approved_metadata_alone_is_not_semantic_or_body_evidence(self):
        p = stage.prepare_review(**kwargs(("title_click_contract_missing",)))
        for change in (
            "no_assessment",
            "no_body",
            "body_forged",
            "body_other_target",
            "uncertain_readability",
            "default_only",
            "source_reason_missing",
        ):
            with self.subTest(change=change):
                rr = title_row(p, p["reviews_requested"][0])
                if change == "no_assessment":
                    rr["title_review"] = {}
                elif change == "no_body":
                    rr["title_review"]["body_evidence"] = []
                elif change == "body_forged":
                    rr["title_review"]["body_evidence"][0]["quote"] = "编造的完成结果"
                elif change == "body_other_target":
                    rr["title_review"]["body_evidence"] = [
                        span(p, p["title"], source="title")
                    ]
                elif change == "uncertain_readability":
                    rr["title_review"]["readability"] = "uncertain"
                elif change == "default_only":
                    rr["title_review"]["default_basis"] = "genre_only"
                elif change == "source_reason_missing":
                    rr["title_review"]["reason"] = ""
                result, docs = response(p, [rr])
                self.closed(self.validate(p, result, docs))

    def test_title_explicit_creator_and_native_canonical_citations_required(self):
        p = stage.prepare_review(**kwargs(("title_click_contract_missing",)))
        for path in (
            "/creation_intent_contract/concept_seed",
            "/approval_evidence/canonical_snapshot/content/title",
            "/approval_evidence/canonical_snapshot/content/premise",
            "/approval_evidence/canonical_snapshot/content/writing_profile/market/reader_promise",
        ):
            with self.subTest(path=path):
                rr = title_row(p, p["reviews_requested"][0])
                rr["contract_evidence"] = [
                    x for x in rr["contract_evidence"] if x["path"] != path
                ]
                result, docs = response(p, [rr])
                self.closed(self.validate(p, result, docs))

    def test_default_creator_seed_is_not_author_authority_in_any_mode(self):
        for mode in ("strict", "warn", "off"):
            with self.subTest(mode=mode):
                k = kwargs(("title_click_contract_missing",))
                k["approved_contract"] = contract(seed_source="default")
                k["methodology_contract_mode"] = mode
                p = stage.prepare_review(**k)
                self.assertFalse(stage._creator_literary_paths(p))
                rr = title_row(p, p["reviews_requested"][0])
                result, docs = response(p, [rr])
                self.closed(self.validate(p, result, docs))
        k = kwargs()
        k["methodology_contract_mode"] = "source_bound"
        with self.assertRaisesRegex(stage.ReviewInputError, "methodology_mode_invalid"):
            stage.prepare_review(**k)

    def test_title_provenance_mismatch_prevents_confirmed_as_well_as_dismissal(self):
        k = kwargs(("title_click_contract_missing",))
        k["title"] += "未经采用的新标题"
        p = stage.prepare_review(**k)
        self.assertFalse(p["title_provenance_verified"])
        result, docs = response(p, [row(p, p["reviews_requested"][0], "confirmed")])
        self.closed(self.validate(p, result, docs))

    def test_native_title_retry_status_is_retained_bound_and_never_cleared(self):
        p = stage.prepare_review(**kwargs(("title_click_contract_missing",)))
        before = deepcopy(p)
        rr = title_row(p, p["reviews_requested"][0])
        result, docs = response(p, [rr])
        report = self.validate(p, result, docs)
        self.assertEqual(p, before)
        self.assertEqual(
            report["title_revision_status"]["blocked"], "title_revision_call_failed"
        )
        self.assertEqual(report["title_revision_status"]["error_type"], "DBAPIError")
        self.assertTrue(report["title_revision_status"]["retryable"])
        q = deepcopy(p)
        q["title_revision_status"]["blocked"] = ""
        self.closed(self.validate(q, result, docs))
        q = deepcopy(p)
        q["approved_contract"]["approval_evidence"]["title_revision_status"] = {}
        self.closed(self.validate(q, result, docs))

    def test_supplied_actual_native_bundle_approval_title_and_strict_source_mapping(
        self,
    ):
        p = native_payload()
        c = p["approved_contract"]
        self.assertTrue(stage._approval_verified(c, p["project_id"]))
        self.assertTrue(p["approval_verified"])
        self.assertTrue(p["title_provenance_verified"])
        self.assertEqual(p["methodology_contract_mode"], "strict")
        self.assertEqual(
            p["title"], c["approval_evidence"]["canonical_snapshot"]["content"]["title"]
        )
        actual_explicit = stage.select_creation_contracts(c, source_bound=False)
        self.assertEqual(
            actual_explicit, stage.select_creation_contracts(c, source_bound=True)
        )
        self.assertEqual(p["explicit_creation_contracts"], actual_explicit)
        self.assertEqual(
            set(actual_explicit["creation_intent_contract"]), {"pov", "concept_seed"}
        )
        self.assertNotIn("genre_intent_contract", actual_explicit)
        self.assertEqual(
            p["title_revision_status"], c["approval_evidence"]["title_revision_status"]
        )
        # No response(p) or synthetic verdict is applied to this real bundle.
        report = stage.validate_response(
            p, {}, current_binding=p["binding"], retained_payloads={}
        )
        self.assertEqual(report["error"], "provider_fallback")
        self.closed(report)
        self.assertTrue(report["title_provenance_verified"])
        self.assertEqual(
            report["title_revision_status"]["quality_status"], "needs_revision"
        )
        self.assertFalse(report["original_report"]["passed"])

    def test_supplied_actual_literal_projection_inside_existing_retention_limit(self):
        p = native_payload()
        wire = stage._user_prompt(p)
        document = json.loads(wire)
        self.assertGreater(len(stage._json(p)), stage.POLICY["max_prompt_chars"])
        self.assertLessEqual(len(wire), stage.POLICY["max_prompt_chars"])
        self.assertEqual(stage.native_request_fields(p)["user_prompt"], wire)
        self.assertEqual(document["full_text"], p["full_text"])
        self.assertEqual(document["reviews_requested"], p["reviews_requested"])
        self.assertEqual(stage._sha(p["full_text"]), p["manifest"]["assembly_sha256"])
        self.assertEqual(next(r["expected_payoff_count"] for r in p["reviews_requested"]
                              if r["code"] == "payoff_density_too_low"), 4)
        for key in ("manifest", "approved_contract", "binding", "project_id", "workflow_run_id", "dictionary"):
            self.assertNotIn(key, document)

    def test_section_signs_are_literal_source_not_reply_identity_codec(self):
        text = SOURCE + "字面符号§0与§§1应保留，不需解释。"
        p = stage.prepare_review(**kwargs(text=text))
        document = json.loads(stage._user_prompt(p))
        self.assertEqual(document["full_text"], text)
        for actual, expected in zip(document["reviews_requested"], p["reviews_requested"]):
            for key in ("id", "code", "target"):
                self.assertEqual(actual[key], expected[key])
                self.assertFalse(actual[key].startswith("§"))
        self.assertNotIn("planning-string-pool", stage.SYSTEM_PROMPT)

    def test_archive_preimage_and_original_36_run_remain_intact(self):
        folder = HERE.parent / "whole-semantic-stage/preimage-before-native-title-refinement"
        expected = json.loads((folder / "SHA256.json").read_text())
        for name, digest in expected.items():
            self.assertEqual(
                hashlib.sha256((folder / name).read_bytes()).hexdigest(), digest
            )
        log = (folder / "verification-36.txt").read_text()
        self.assertIn("Ran 36 tests", log)
        self.assertIn("OK", log)

    def test_density_reprints_cannot_change_quote_boundaries_to_count_twice(self):
        p = stage.prepare_review(**kwargs(("payoff_density_too_low",), SOURCE + SOURCE))
        first = events(p)[0]
        second = deepcopy(first)
        for key in ("goal", "action", "result"):
            original = first[key]["quote"]
            second[key] = span(p, original[1:-1], len(SOURCE))
        rr = row(p, p["reviews_requested"][0])
        rr["events"] = [first, second]
        result, docs = response(p, [rr])
        self.closed(self.validate(p, result, docs))

    def test_completed_disposal_jiang_is_allowed_future_jiang_is_not(self):
        for action, accepted in (
            ("我将断裂的椅腿卸下了。", True),
            ("我将要卸下断裂的椅腿。", False),
            ("我将卸下断裂的椅腿。", False),
        ):
            with self.subTest(action=action):
                text = SOURCE.replace("我卸下断裂的椅腿。", action)
                p = stage.prepare_review(
                    **kwargs(("opening_fast_payoff_missing",), text)
                )
                event = {
                    "goal": span(p, "椅子的断腿挡住了我的去路。"),
                    "action": span(p, action),
                    "result": span(p, "我装好新腿后坐稳了。"),
                    "causal_link": "卸下断腿再换上新腿，使椅子重新能够承受我的重量。",
                    "status": "completed",
                }
                rr = row(p, p["reviews_requested"][0])
                rr["events"] = [event]
                result, docs = response(p, [rr])
                report = self.validate(p, result, docs)
                if accepted:
                    self.assertIsNone(report["error"])
                else:
                    self.closed(report)

    def test_native_effective_execution_origin_receipt_mismatches_fail_closed(self):
        p = stage.prepare_review(**kwargs())
        for change in (
            "effective_missing",
            "effective_model",
            "effective_provider",
            "effective_hash",
            "execution_missing",
            "execution_scope",
            "execution_operation",
            "attempt_hash",
            "origin_missing",
            "origin_workflow",
            "reuse_key",
            "request_max_tokens",
        ):
            with self.subTest(change=change):
                result, docs = response(p)
                meta = result["metadata"]
                if change == "effective_missing":
                    meta.pop("effective_input_receipt")
                elif change == "effective_model":
                    docs["request"]["effective"]["model"] = "wrong-model"
                elif change == "effective_provider":
                    docs["request"]["effective"]["provider"] = "wrong-provider"
                elif change == "effective_hash":
                    meta["effective_input_receipt"]["hash"] = "0" * 64
                elif change == "execution_missing":
                    meta.pop("execution_receipt")
                elif change == "execution_scope":
                    meta["execution_receipt"]["scope_id"] = "other-scope"
                elif change == "execution_operation":
                    meta["execution_receipt"]["operation_key"] = "0" * 64
                elif change == "attempt_hash":
                    meta["execution_receipt"]["attempts"][-1][
                        "effective_input_hash"
                    ] = "0" * 64
                elif change == "origin_missing":
                    meta.pop("paid_call_origin")
                elif change == "origin_workflow":
                    meta["paid_call_origin"]["workflow_run_id"] = str(uuid4())
                elif change == "reuse_key":
                    meta["reused_operation_key"] = "other-key"
                elif change == "request_max_tokens":
                    docs["request"]["requested"]["max_tokens_override"] = 1
                self.closed(self.validate(p, result, docs))

    def test_wrong_active_execution_scope_stops_before_dispatch(self):
        p = stage.prepare_review(**kwargs())
        gateway = AsyncMock()
        fake_module = SimpleNamespace(
            LLMCompletionRequest=lambda **kw: SimpleNamespace(**kw),
            bind_project_prompt_audit=None,
            complete_text=gateway,
        )
        with (
            patch.dict(sys.modules, {"bestseller.services.llm": fake_module}),
            patch(
                "bestseller.services.budget.current_execution_scope",
                return_value="other-scope",
            ),
        ):
            with self.assertRaisesRegex(
                stage.ReviewInputError, "active_execution_scope_mismatch"
            ):
                asyncio.run(
                    stage.review_fanqie_short_semantic_findings(
                        None, None, payload=p, current_binding=p["binding"]
                    )
                )
        gateway.assert_not_awaited()

    def test_forbidden_criticals_excluded_not_reviewable(self):
        forbidden = [
            "canon_conflict",
            "timeline_conflict",
            "budget_exhausted",
            "control_stop",
            "taboo",
            "wordcount",
            "ready_for_upload",
            "longform_contamination",
            "serial_cliffhanger_ending",
            "ability_cost_missing",
            "protagonist_name_missing",
        ]
        k = kwargs()
        k["original_report"]["ranking_report"]["findings"] += [
            finding(code) for code in forbidden
        ]
        p = stage.prepare_review(**k)
        self.assertEqual(
            {x["finding"]["code"] for x in p["excluded_criticals"]}, set(forbidden)
        )
        self.assertEqual(len(p["reviews_requested"]), 1)
        r, docs = response(p)
        self.assertEqual(
            self.validate(p, r, docs)["original_report"], k["original_report"]
        )

    def test_native_request_fields_stable_source_operation_and_fixed_sample(self):
        k = kwargs()
        p = stage.prepare_review(**k)
        a = stage.native_request_fields(p)
        self.assertEqual(a["logical_role"], "critic")
        self.assertEqual(a["sample_slot"], 0)
        self.assertEqual(str(a["project_id"]), PROJECT)
        self.assertEqual(str(a["workflow_run_id"]), WORKFLOW)
        k["workflow_run_id"] = str(uuid4())
        q = stage.prepare_review(**k)
        self.assertEqual(
            stage.native_request_fields(q)["operation_id"], a["operation_id"]
        )
        self.assertNotEqual(
            stage.native_request_fields(q)["user_prompt"], a["user_prompt"]
        )
        k["title"] += "！"
        q = stage.prepare_review(**k)
        self.assertNotEqual(
            stage.native_request_fields(q)["operation_id"], a["operation_id"]
        )
        self.assertEqual(stage.native_request_fields(p), a)

    def test_native_wrapper_one_call_no_retry(self):
        p = stage.prepare_review(**kwargs())
        r, docs = response(p)
        fake = AsyncMock(return_value=(r, docs))
        with patch.object(stage, "_native_complete_text", fake):
            result = asyncio.run(
                stage.review_fanqie_short_semantic_findings(
                    None, None, payload=p, current_binding=p["binding"]
                )
            )
        self.assertEqual(fake.await_count, 1)
        self.assertIsNone(result["error"])

    def test_all_execution_and_control_stops_propagate_exactly(self):
        p = stage.prepare_review(**kwargs())

        class ControlStop(Exception):
            pass

        stops = [
            ExecutionBudgetError("execution_result_unknown", "scope"),
            ExecutionOperationBusyError("scope"),
            ExecutionBudgetError("execution_budget_exhausted", "scope"),
            ControlStop("cancel"),
            asyncio.CancelledError(),
            RuntimeError("ledger unavailable"),
            TimeoutError("transport unknown"),
        ]
        for stop in stops:
            with self.subTest(kind=type(stop).__name__):
                fake = AsyncMock(side_effect=stop)
                with patch.object(stage, "_native_complete_text", fake):
                    with self.assertRaises(type(stop)) as cm:
                        asyncio.run(
                            stage.review_fanqie_short_semantic_findings(
                                None, None, payload=p, current_binding=p["binding"]
                            )
                        )
                    self.assertIs(cm.exception, stop)
                self.assertEqual(fake.await_count, 1)

    def test_native_adapter_with_physical_retention_and_synthetic_gateway(self):
        p = stage.prepare_review(**kwargs())
        result, docs = response(p)
        audit_projects = []

        @contextmanager
        def audit(project, *, reason):
            audit_projects.append((str(project), reason))
            yield

        with tempfile.TemporaryDirectory() as tmp:
            for kind in ("request", "response"):
                target = (
                    Path(tmp)
                    / result["metadata"]["prompt_payload_receipt"][kind + "_ref"]
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(docs[kind]))
            gateway = AsyncMock(return_value=SimpleNamespace(**result))
            fake_module = SimpleNamespace(
                LLMCompletionRequest=lambda **kw: SimpleNamespace(**kw),
                bind_project_prompt_audit=audit,
                complete_text=gateway,
            )
            settings = SimpleNamespace(
                artifact_store=SimpleNamespace(
                    retain_prompt_payloads=True, mode="local", local_dir=tmp
                )
            )
            with (
                patch.dict(sys.modules, {"bestseller.services.llm": fake_module}),
                patch(
                    "bestseller.services.budget.current_execution_scope",
                    return_value=p["execution_scope_id"],
                ),
            ):
                report = asyncio.run(
                    stage.review_fanqie_short_semantic_findings(
                        None, settings, payload=p, current_binding=p["binding"]
                    )
                )
            self.assertIsNone(report["error"])
            self.assertEqual(gateway.await_count, 1)
            request = gateway.await_args.args[2]
            self.assertEqual(str(request.project_id), PROJECT)
            self.assertEqual(str(request.workflow_run_id), WORKFLOW)
            self.assertEqual(request.sample_slot, 0)
            self.assertEqual(request.user_prompt, stage._user_prompt(p))
            self.assertEqual(json.loads(request.user_prompt)["full_text"], p["full_text"])
            self.assertEqual(audit_projects, [(PROJECT, stage.SCHEMA)])

    def test_native_adapter_missing_retention_configuration_stops_without_call(self):
        p = stage.prepare_review(**kwargs())
        gateway = AsyncMock()
        fake_module = SimpleNamespace(
            LLMCompletionRequest=lambda **kw: SimpleNamespace(**kw),
            bind_project_prompt_audit=None,
            complete_text=gateway,
        )
        settings = SimpleNamespace(
            artifact_store=SimpleNamespace(retain_prompt_payloads=False, mode="local")
        )
        with (
            patch.dict(sys.modules, {"bestseller.services.llm": fake_module}),
            patch(
                "bestseller.services.budget.current_execution_scope",
                return_value=p["execution_scope_id"],
            ),
        ):
            with self.assertRaisesRegex(
                stage.ReviewInputError, "local_retention_required"
            ):
                asyncio.run(
                    stage.review_fanqie_short_semantic_findings(
                        None, settings, payload=p, current_binding=p["binding"]
                    )
                )
        gateway.assert_not_awaited()

    def test_native_adapter_missing_physical_retained_file_stops_without_retry(self):
        p = stage.prepare_review(**kwargs())
        result, _ = response(p)

        @contextmanager
        def audit(*args, **kw):
            yield

        gateway = AsyncMock(return_value=SimpleNamespace(**result))
        fake_module = SimpleNamespace(
            LLMCompletionRequest=lambda **kw: SimpleNamespace(**kw),
            bind_project_prompt_audit=audit,
            complete_text=gateway,
        )
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                artifact_store=SimpleNamespace(
                    retain_prompt_payloads=True, mode="local", local_dir=tmp
                )
            )
            with (
                patch.dict(sys.modules, {"bestseller.services.llm": fake_module}),
                patch(
                    "bestseller.services.budget.current_execution_scope",
                    return_value=p["execution_scope_id"],
                ),
            ):
                with self.assertRaises(FileNotFoundError):
                    asyncio.run(
                        stage.review_fanqie_short_semantic_findings(
                            None, settings, payload=p, current_binding=p["binding"]
                        )
                    )
        self.assertEqual(gateway.await_count, 1)

    def test_positive_payoff_with_causal_event_and_density_goal_action_requirements(
        self,
    ):
        p = stage.prepare_review(**kwargs(("opening_fast_payoff_missing",)))
        rr = row(p, p["reviews_requested"][0])
        rr["events"] = events(p)[:1]
        result, docs = response(p, [rr])
        self.assertIsNone(self.validate(p, result, docs)["error"])
        for change in ("goal", "status", "causality", "overlap"):
            with self.subTest(change=change):
                bad = deepcopy(rr)
                if change == "goal":
                    bad["events"][0].pop("goal")
                elif change == "status":
                    bad["events"][0]["status"] = "planned"
                elif change == "causality":
                    bad["events"][0]["causal_link"] = "有回报"
                elif change == "overlap":
                    bad["events"][0]["result"] = bad["events"][0]["action"]
                result, docs = response(p, [bad])
                self.closed(self.validate(p, result, docs))

    def test_missing_approval_blocks_before_dispatch(self):
        k = kwargs()
        k["approved_contract"].pop("approval_evidence")
        p = stage.prepare_review(**k)
        self.closed(self.validate(p))
        with patch.object(stage, "_native_complete_text", AsyncMock()) as fake:
            with self.assertRaisesRegex(stage.ReviewInputError, "approval_unverified"):
                asyncio.run(
                    stage.review_fanqie_short_semantic_findings(
                        None, None, payload=p, current_binding=p["binding"]
                    )
                )
            fake.assert_not_awaited()

    def test_duplicate_detector_input_fails(self):
        k = kwargs()
        k["original_report"]["ranking_report"]["findings"] *= 2
        with self.assertRaises(stage.ReviewInputError):
            stage.prepare_review(**k)

    def test_no_literary_findings_does_not_dispatch_or_grant_pass(self):
        p = stage.prepare_review(**kwargs(()))
        with patch.object(stage, "_native_complete_text", AsyncMock()) as fake:
            result = asyncio.run(
                stage.review_fanqie_short_semantic_findings(
                    None, None, payload=p, current_binding=p["binding"]
                )
            )
            fake.assert_not_awaited()
        self.assertFalse(result["called"])
        self.assertNotIn("passed", result)

    def test_frozen_v8_integrity_original_threshold_and_no_genuine_verdict(self):
        x = json.loads(
            (HERE.parent / "original-creative-contract-readback.json").read_text()
        )
        v = json.loads((HERE.parent / "v8-whole-gate-preview.json").read_text())
        p = stage.prepare_review(
            project_id=PROJECT,
            workflow_run_id=WORKFLOW,
            operation_namespace="frozen-preview",
            execution_scope_id="offline-diagnostic-not-an-active-budget",
            current_scope="captured-v8-not-current-v10",
            full_text=x["frozen_v8_full_text"],
            title=x["title"],
            protagonist="我",
            manifest={k: v[k] for k in ("assembly_sha256", "sources")},
            approved_contract=x["selected_contracts"],
            original_report=v["review"],
        )
        self.assertEqual(stage._sha(p["full_text"]), x["assembly_sha256"])
        self.assertEqual(len(p["reviews_requested"]), 7)
        density = next(
            r for r in p["reviews_requested"] if r["code"] == "payoff_density_too_low"
        )
        self.assertEqual(density["expected_payoff_count"], 4)
        native = review_whole_fanqie_short_story(
            p["full_text"], title=p["title"], protagonist_name="我"
        ).to_dict()
        # Current deterministic replay, no semantic review or fixture victory.
        self.assertFalse(native["passed"])
        report = stage.validate_response(
            p, {}, current_binding=p["binding"], retained_payloads={}
        )
        self.assertEqual(report["error"], "approval_unverified")
        self.closed(report)
        self.assertEqual(report["original_report"], v["review"])



    def test_unique_quote_derivation_preserves_raw_answer_and_model_verdict(self):
        p = stage.prepare_review(**kwargs())
        rr = row(p, p["reviews_requested"][0])
        rr["evidence"] = [{"source": "full_text", "quote": "今晚房东将收回我租的门面，我得带走工具。"}]
        result, docs = response(p, [rr]); raw = result["content"]
        report = self.validate(p, result, docs)
        self.assertIsNone(report["error"])
        self.assertEqual(result["content"], raw)
        self.assertEqual(report["raw_response"], raw)
        self.assertEqual(report["reviews"][0]["verdict"], rr["verdict"])
        self.assertEqual(report["reviews"][0]["evidence"][0], span(p, rr["evidence"][0]["quote"]))
        self.assertFalse(report["quote_location_receipt"]["quotes_modified"])
        self.assertFalse(report["quote_location_receipt"]["verdicts_modified"])

    def test_unique_quote_rejects_absent_ambiguous_or_wrong_scope_without_repair(self):
        for text, quote, source in [(SOURCE, "从未出现的维修经过", "full_text"),
                                   (SOURCE+SOURCE, "我取出了完好的衣裳。", "full_text"),
                                   (SOURCE, "我卸下断裂的椅腿。", "title")]:
            with self.subTest(quote=quote, source=source):
                p = stage.prepare_review(**kwargs(text=text)); rr = row(p, p["reviews_requested"][0])
                rr["evidence"] = [{"source":source,"quote":quote}]
                result,docs=response(p,[rr]); report=self.validate(p,result,docs)
                self.closed(report)
                self.assertIsNone(report["quote_location_receipt"])
                self.assertEqual(report["raw_response"], result["content"])

    def test_unique_quote_resolves_only_inside_exact_window_with_unicode(self):
        quote="今晚房东将收回我租的门面，我得带走工具。"
        text="🌕"+SOURCE+"甲"*400+quote
        p=stage.prepare_review(**kwargs(text=text));rr=row(p,p["reviews_requested"][0])
        rr["evidence"]=[{"source":"full_text","quote":quote}]
        result,docs=response(p,[rr]);report=self.validate(p,result,docs)
        self.assertIsNone(report["error"])
        self.assertEqual(report["reviews"][0]["evidence"][0]["start"],1)
        self.assertLessEqual(report["reviews"][0]["evidence"][0]["end"],p["reviews_requested"][0]["scope"]["end"])

    def test_unique_contract_and_title_quotes_keep_native_authority_requirements(self):
        p=stage.prepare_review(**kwargs(("title_click_contract_missing",)))
        rr=title_row(p,p["reviews_requested"][0])
        for value in rr["evidence"]+rr["contract_evidence"]+rr["title_review"]["body_evidence"]:
            value.pop("start");value.pop("end")
        result,docs=response(p,[rr]);report=self.validate(p,result,docs)
        self.assertIsNone(report["error"])
        self.assertEqual(report["reviews"][0]["verdict"],"not_supported")
        self.assertEqual(report["title_revision_status"],p["title_revision_status"])
        rr["contract_evidence"][0]["path"]="/unknown/path"
        result,docs=response(p,[rr]);self.closed(self.validate(p,result,docs))

    def test_unique_event_quotes_preserve_count_order_and_causality_checks(self):
        p=stage.prepare_review(**kwargs(("payoff_density_too_low",)))
        rr=row(p,p["reviews_requested"][0]);rr["events"]=events(p)[:2]
        for event in rr["events"]:
            for key in ("goal","action","result"):
                event[key].pop("start");event[key].pop("end")
        result,docs=response(p,[rr]);report=self.validate(p,result,docs)
        self.assertIsNone(report["error"])
        self.assertEqual(len(report["reviews"][0]["events"]),2)
        rr["events"][1]=deepcopy(rr["events"][0])
        result,docs=response(p,[rr]);self.closed(self.validate(p,result,docs))


    def test_original_four_paid_request_projection_preserves_all_seven_scopes(self):
        old, p = paid_source_payload()
        wire = json.loads(stage._user_prompt(p))
        for key in ("full_text", "title", "protagonist", "title_revision_status"):
            self.assertEqual(wire[key], old[key])
        for key in ("approved_contract", "manifest", "original_report", "execution_scope_id", "operation_namespace"):
            self.assertEqual(p[key], old[key])
        original = {item["id"]: item for item in old["reviews_requested"]}
        current = {item["id"]: item for item in wire["reviews_requested"]}
        self.assertEqual(len(original), 7)
        self.assertEqual(len(current), 9)
        self.assertEqual(len(wire["reviews_requested"]), len(current))
        for identifier, item in original.items():
            self.assertIn(identifier, current)
            for field, value in item.items():
                self.assertEqual(current[identifier][field], value, (identifier, field))
        added = {item["code"]: item for key, item in current.items() if key not in original}
        self.assertEqual(set(added), {"weak_hook", "first_10k_loop_missing"})
        start = len(p["full_text"]) - len(p["full_text"].lstrip())
        size = min(len(p["full_text"].strip()), max(50, int(len(p["full_text"].strip()) * .30)))
        for code, item in added.items():
            self.assertEqual(item["target"], "opening_30_percent")
            self.assertEqual(item["scope"], {"source": "full_text", "start": start, "end": start + size})
            self.assertEqual(item["origins"], ["ranking_report", "short_v2_report"])
            for layer in item["origins"]:
                matched = [f for f in old["original_report"][layer]["findings"] if f["code"] == code]
                self.assertEqual(matched, [item["detector_finding"]])
        for item in current.values():
            scope = item["scope"]
            self.assertEqual(item["scope_text"], p[scope["source"]][scope["start"]:scope["end"]])
            self.assertIs(item["response_required"], True)
            if item["target"] == "title":
                canonical = "/approval_evidence/canonical_snapshot/content/"
                expected_paths = {"/reader_promise", p["title_source_path"],
                    canonical + "writing_profile/market/reader_promise", "/book_design_snapshot/reader_promise",
                    "/book_design_snapshot/approved_story_design/hook_card/one_liner"}
                expected_paths.update(path for path in stage._projected_contract_strings(p)
                    if path.rsplit("/", 1)[-1] in {"title_pattern", "title_strategy"})
                self.assertEqual(item["required_contract_paths"], sorted(expected_paths))
                self.assertEqual(item["canonical_story_any_of_paths"], [canonical + "premise", canonical + "synopsis"])
                self.assertEqual(item["explicit_creator_any_of_paths"], sorted(stage._creator_literary_paths(p)))
                self.assertIn("/creation_intent_contract/concept_seed", item["explicit_creator_any_of_paths"])
        self.assertEqual(wire["density_thresholds"], old["original_report"]["thresholds"])
        self.assertEqual(wire["explicit_creation_contracts"], old["explicit_creation_contracts"])
        self.assertEqual(stage.native_request_fields(p)["max_tokens_override"], 12000)
        self.assertEqual(stage.native_request_fields(p)["sample_slot"], 0)

    def test_projection_every_leaf_is_equal_to_full_native_source_and_bound(self):
        _, p = paid_source_payload()
        projection, receipt = stage._literary_projection(p)
        self.assertEqual(projection, p["literary_projection"])
        self.assertEqual(receipt, p["projection_receipt"])
        self.assertEqual(p["binding"]["projection_receipt_sha256"], stage.fingerprint(receipt))
        self.assertEqual(p["binding"]["approved_contract_sha256"], stage.fingerprint(p["approved_contract"]))
        self.assertGreater(len(receipt["entries"]), 100)
        for entry in receipt["entries"]:
            actual = stage._pointer(p, entry["source_path"])
            projected = stage._pointer(projection, entry["projection_path"])
            self.assertIs(type(actual), type(projected))
            self.assertEqual(actual, projected)
            self.assertEqual(stage.fingerprint(actual), entry["value_sha256"])

    def test_projection_retains_locked_literary_boundaries_and_empty_taboo(self):
        _, p = paid_source_payload()
        projected = stage._projected_contract_strings(p)
        for path in (
            "/reader_promise", "/book_design_snapshot/reader_promise", "/book_design_snapshot/tone",
            "/book_design_snapshot/approved_story_design/story_spine/terminal_question",
            "/approval_evidence/canonical_snapshot/content/title",
        ):
            self.assertEqual(projected[path], stage._pointer(p["approved_contract"], path))
        values = p["literary_projection"]["selected_literary_contracts"]
        style = values["/approval_evidence/canonical_snapshot/content/writing_profile/style"]
        self.assertEqual(style["taboo_words"], [])
        self.assertEqual(style["taboo_topics"], [])
        self.assertEqual(set(p["explicit_creation_contracts"]["creation_intent_contract"]), {"pov", "concept_seed"})
        self.assertNotIn("tone", p["explicit_creation_contracts"]["creation_intent_contract"])
        self.assertIn("不是新增作者指令", stage.SYSTEM_PROMPT)

    def test_projection_drift_receipt_forgery_or_unseen_contract_quotes_fail_closed(self):
        p = stage.prepare_review(**kwargs())
        for field in ("literary_projection", "projection_receipt"):
            changed = deepcopy(p)
            changed[field]["tampered"] = True
            with self.subTest(field=field), self.assertRaises(stage.ReviewInputError):
                stage._user_prompt(changed)
            r, docs = response(p)
            self.closed(self.validate(changed, r, docs))
        rr = row(p, p["reviews_requested"][0])
        rr["contract_evidence"] = [{
            "path": "/approval_evidence/canonical_snapshot/id",
            "quote": p["approved_contract"]["approval_evidence"]["canonical_snapshot"]["id"],
        }]
        r, docs = response(p, [rr])
        self.closed(self.validate(p, r, docs))

    def test_explicit_projection_refuses_selector_values_missing_in_native_source(self):
        with patch.object(stage, "select_creation_contracts", return_value={
            "creation_intent_contract": {"creative_direction": "没有原始显式字段，不得通过归一化或默认值生成作者授权"}
        }):
            with self.assertRaises(stage.ReviewInputError):
                stage.prepare_review(**kwargs())

    def test_projection_never_clips_long_selected_value_or_promotes_project_title(self):
        k = kwargs()
        # No invented canonical title certificate; a different actual target
        # remains unverified until a native publishing lineage adapter exists.
        k["title"] = "交还钥匙后，我终于听完母亲的磁带"
        p = stage.prepare_review(**k)
        self.assertFalse(p["title_provenance_verified"])
        c = deepcopy(k["approved_contract"])
        c["title_strategy"] = "字面长合同" * 30000
        k["approved_contract"] = c
        with self.assertRaisesRegex(stage.ReviewInputError, "full_payload_capacity"):
            stage.prepare_review(**k)

    def test_nonactual_limitation_negative_adverse_outcome_remains_uncertain(self):
        text = SOURCE.replace("我取出了完好的衣裳。", "柜门连续开合十次，未再卡滞。")
        p = stage.prepare_review(**kwargs(("payoff_density_too_low",), text))
        rr = row(p, p["reviews_requested"][0])
        es = events(stage.prepare_review(**kwargs()))[:2]
        es[1]["result"] = span(p, "柜门连续开合十次，未再卡滞。")
        rr["events"] = es
        result, docs = response(p, [rr])
        report = self.validate(p, result, docs)
        self.assertEqual(report["error"], "nonactual_event")
        self.closed(report)
        # Trimming the negation away still cannot evade sentence-context checks.
        rr["events"][1]["result"] = span(p, "卡滞。")
        result, docs = response(p, [rr])
        self.closed(self.validate(p, result, docs))

    def test_v3_paid_failure_is_preserved_and_cannot_be_reused_as_v4_semantics(self):
        old, p = paid_source_payload()
        paid = json.loads((HERE.parent / "whole-semantic-paid-root-readback.json").read_text())
        completion = paid["result_json"]
        report = stage.validate_response(p, completion, current_binding=p["binding"], retained_payloads={})
        self.closed(report)
        self.assertEqual(report["raw_response"], completion["content"])
        self.assertNotEqual(p["binding_sha256"], old["binding_sha256"])
        self.assertEqual(completion["finish_reason"], "length")

    def test_reply_pool_markers_are_never_decoded_into_ids_codes_or_targets(self):
        p = stage.prepare_review(**kwargs())
        for key in ("id", "code", "target"):
            rr = row(p, p["reviews_requested"][0])
            rr[key] = "§93"
            result, docs = response(p, [rr])
            with self.subTest(key=key):
                report = self.validate(p, result, docs)
                self.closed(report)
                self.assertEqual(report["raw_response"], result["content"])

    def assert_clause_event(self, action_text, action_quote, result_text, result_quote, accepted):
        """Exercise literal location, two-event N, retention and batch verdicts."""
        goal = "当前作业因设备停转受阻。"
        text = (
            goal + "\n" + action_text + "\n" + result_text + "\n"
            "柜门卡住了我的衣裳。我磨平柜门边缘。我取出了完好的衣裳。"
        )
        p = stage.prepare_review(**kwargs(("payoff_density_too_low",), text))
        item = p["reviews_requested"][0]
        self.assertEqual(item["expected_payoff_count"], 2)
        literal = lambda quote: {"source": "full_text", "quote": quote}
        rr = row(p, item)
        rr["events"] = [
            {
                "goal": literal(goal), "action": literal(action_quote),
                "result": literal(result_quote), "status": "completed",
                "causal_link": "对停转设备实施具体处置，随后作业条件产生了可定位的实际变化。",
            },
            {
                "goal": literal("柜门卡住了我的衣裳。"),
                "action": literal("我磨平柜门边缘。"),
                "result": literal("我取出了完好的衣裳。"), "status": "completed",
                "causal_link": "磨平柜门边缘让被卡住的衣裳能够完整取出，解决了另一处独立阻碍。",
            },
        ]
        completion, docs = response(p, [rr])
        raw = completion["content"]
        report = self.validate(p, completion, docs)
        self.assertEqual(report["raw_response"], raw)
        self.assertEqual(report["original_report"], p["original_report"])
        if accepted:
            self.assertIsNone(report["error"])
            self.assertEqual(report["reviews"][0]["verdict"], "not_supported")
            self.assertEqual(len(report["reviews"][0]["events"]), 2)
            self.assertEqual(report["reviews"][0]["events"][0]["result"]["quote"], result_quote)
        else:
            self.assertEqual(report["error"], "nonactual_event")
            self.closed(report)
        return report

    def test_clause_positive_results_ignore_negation_only_in_neighbor_clause(self):
        for delimiter in ("，", ",", "；", ";"):
            for positive_first in (True, False):
                positive, negative = "供电与走带恢复正常", "未再卡滞"
                clauses = (positive, negative) if positive_first else (negative, positive)
                result = delimiter.join(clauses) + "。"
                with self.subTest(delimiter=delimiter, positive_first=positive_first):
                    self.assert_clause_event("我更换了磨损的零件。", "我更换了磨损的零件。", result, positive, True)

    def test_clause_positive_actions_ignore_independent_negative_clause(self):
        for delimiter in ("，", ",", "；", ";"):
            for positive_first in (True, False):
                positive, negative = "我卸下了损坏的零件", "我没有触碰旁边的封条"
                clauses = (positive, negative) if positive_first else (negative, positive)
                action = delimiter.join(clauses) + "。"
                with self.subTest(delimiter=delimiter, positive_first=positive_first):
                    self.assert_clause_event(action, positive, "供电与走带恢复正常。", "供电与走带恢复正常。", True)

    def test_clause_english_positive_quotes_and_contracted_negation(self):
        for delimiter in (",", ";", "，", "；"):
            with self.subTest(delimiter=delimiter):
                self.assert_clause_event(
                    "I removed the damaged part" + delimiter + " I did not open the sealed box.",
                    "I removed the damaged part",
                    "Power and transport returned to normal" + delimiter + " it didn't stall again.",
                    "Power and transport returned to normal", True,
                )
        for result, quote in (
            ("Power did not return to normal.", "return to normal"),
            ("Power never returned to normal.", "returned to normal"),
            ("Power didn't return to normal.", "return to normal"),
            ("Power didn’t return to normal.", "return to normal"),
        ):
            with self.subTest(result=result):
                self.assert_clause_event("I replaced the damaged part.", "I replaced the damaged part.", result, quote, False)

    def test_clause_negation_cannot_be_cropped_out_of_action_or_result(self):
        for action, quote in (
            ("我没有修好设备，旁边的灯亮着。", "修好设备"),
            ("封条仍在；我未卸下损坏的零件。", "卸下损坏的零件"),
            ("我不曾更换旧零件。", "更换旧零件"),
            ("我取消了更换零件。", "更换零件"),
            ("I did not replace the part, the spare is on the table.", "replace the part"),
        ):
            with self.subTest(action=action):
                self.assert_clause_event(action, quote, "供电与走带恢复正常。", "供电与走带恢复正常。", False)
        for result, quote in (
            ("电池仍在，供电没有恢复正常。", "恢复正常"),
            ("供电未恢复正常；封条仍在。", "恢复正常"),
            ("原设备不能继续运转。", "继续运转"),
            ("未再卡滞。", "卡滞"),
        ):
            with self.subTest(result=result):
                self.assert_clause_event("我更换了磨损的零件。", "我更换了磨损的零件。", result, quote, False)

    def test_clause_cross_clause_modality_blocks_apparently_completed_results(self):
        for delimiter in ("，", ",", "；", ";"):
            for modifier in ("如果配件到货", "我希望", "但愿", "我打算", "也许", "将会继续测试"):
                for before in (True, False):
                    positive = "供电与走带恢复正常"
                    clauses = (modifier, positive) if before else (positive, modifier)
                    result = delimiter.join(clauses) + "。"
                    with self.subTest(delimiter=delimiter, modifier=modifier, before=before):
                        self.assert_clause_event("我更换了磨损的零件。", "我更换了磨损的零件。", result, positive, False)
        for modifier in ("If the spare arrives", "I hope", "I wish", "I plan", "I will test it", "It might work"):
            for delimiter in (",", ";"):
                with self.subTest(modifier=modifier, delimiter=delimiter):
                    self.assert_clause_event(
                        "I replaced the damaged part.", "I replaced the damaged part.",
                        modifier + delimiter + " power returned to normal.", "power returned to normal", False,
                    )

    def test_clause_cross_clause_modality_also_blocks_completed_action_quotes(self):
        for action, quote in (
            ("如果配件到货，我修好了设备。", "我修好了设备"),
            ("我希望；我换上了新的零件。", "我换上了新的零件"),
            ("我卸下了零件，我打算继续检修。", "我卸下了零件"),
            ("If the spare arrives, I replaced the part.", "I replaced the part"),
            ("I hope; I replaced the part.", "I replaced the part"),
        ):
            with self.subTest(action=action):
                self.assert_clause_event(action, quote, "供电与走带恢复正常。", "供电与走带恢复正常。", False)

    def test_clause_quotes_crossing_into_negative_clause_remain_uncertain(self):
        for result, quote in (
            ("供电与走带恢复正常，未再卡滞。", "供电与走带恢复正常，未再卡滞。"),
            ("供电恢复正常；走带未恢复正常。", "正常；走带未恢复正常"),
            ("供电未恢复正常，换上零件后恢复了。", "恢复正常，换上零件后恢复了"),
        ):
            with self.subTest(quote=quote):
                self.assert_clause_event("我更换了磨损的零件。", "我更换了磨损的零件。", result, quote, False)

    def test_clause_rule_applies_to_storage_documents_and_contact_without_device_keywords(self):
        for goal, action, positive, negative, delimiter, causal_link in (
            ("箱子挡住了门口。", "我付清了寄存费用。", "店主收起了箱子", "旧欠没有减免", "，",
             "付清寄存费用让柜台接收我的箱子并清出了门口，原有旧欠仍然保留。"),
            ("我还缺一份签收凭证。", "我递交了盖章的申请。", "经办人交给我签收凭证", "没有改变原登记日期", ";",
             "提交盖章申请使经办人交出了签收凭证，填补了此前核验所需的文件。"),
            ("我无法直接联系收件人。", "我当面核对了电话号码。", "收件人接通电话", "不曾让旁人代答", ",",
             "核对电话号码后取得了收件人的直接回应，解决了此前无法联系的阻碍。"),
        ):
            text = goal + action + positive + delimiter + negative + "。"
            p = stage.prepare_review(**kwargs(("opening_fast_payoff_missing",), text))
            for quote, accepted in ((positive, True), (negative, False)):
                with self.subTest(goal=goal, quote=quote):
                    rr = row(p, p["reviews_requested"][0])
                    rr["events"] = [{
                        "goal": {"source": "full_text", "quote": goal},
                        "action": {"source": "full_text", "quote": action},
                        "result": {"source": "full_text", "quote": quote},
                        "status": "completed", "causal_link": causal_link,
                    }]
                    result, docs = response(p, [rr])
                    report = self.validate(p, result, docs)
                    if accepted:
                        self.assertIsNone(report["error"])
                    else:
                        self.assertEqual(report["error"], "nonactual_event")
                        self.closed(report)

    def test_clause_fix_preserves_complete_60_test_preimage_and_original_test_bodies(self):
        archive = HERE / "preimage-before-clause-negation"
        hashes = json.loads((archive / "SHA256.json").read_text())
        self.assertEqual(len(hashes), 17)
        for path, expected in hashes.items():
            self.assertEqual(hashlib.sha256((archive / path).read_bytes()).hexdigest(), expected)
        self.assertIn("Ran 60 tests", (archive / "verification-tests.log").read_text())
        def test_bodies(path):
            return {n.name: ast.dump(n, include_attributes=False)
                    for n in ast.walk(ast.parse(path.read_text()))
                    if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
        old = test_bodies(archive / "test_fanqie_short_semantic_review.py")
        current = test_bodies(HERE / "test_fanqie_short_semantic_review.py")
        self.assertEqual(len(old), 60)
        self.assertTrue(old.keys() <= current.keys())
        for name, body in old.items():
            self.assertEqual(current[name], body, name)


if __name__ == "__main__":
    unittest.main()
