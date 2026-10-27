"""R19 regression: saved/adopted prose is not formal book delivery.

Offline only. Execute the real pure Python/JS projection helpers extracted from
current sources, without importing the web server, booting an app, connecting a
database, making HTTP requests, writing state or invoking a provider. Fixtures
are synthetic input shapes based on the observed original-four API response;
they are not delivery certificates. No positive certificate is fabricated.
"""
from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime, UTC

import pytest

ROOT = Path(__file__).resolve().parents[2]
SLUG = "realistic-life-1792514984"
TASK_ID = "796682cb-9685-54ff-af4a-c6d1ae6340fe"


def python_projections():
    """Load existing helper bodies and their literal status sets, with no I/O."""
    path = ROOT / "src/bestseller/web/server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = {"_library_book_state", "_task_project_slug", "_settle_task_cards_from_project_status"}
    constants = {"_BOOK_CLOSED_STATUSES", "_BOOK_RUNNING_STATUSES", "_BOOK_ATTENTION_STATUSES"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in constants for target in node.targets
        ):
            selected.append(node)
    assert {n.name for n in selected if isinstance(n, ast.FunctionDef)} == functions
    namespace = {"Mapping": Mapping}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def javascript_function(name):
    text = (ROOT / "src/bestseller/web/novel_quickstart.html").read_text(encoding="utf-8")
    match = re.search(r"(?m)^([ \t]*)function " + re.escape(name) + r"\(", text)
    assert match is not None, name
    # Top-level helpers close at their own indentation; nested blocks are deeper.
    end = re.search(r"(?m)^" + re.escape(match.group(1)) + r"\}", text[match.end():])
    assert end is not None, name
    return text[match.start():match.end() + end.end()]


def browser_predicates(task, project=None):
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node absent: browser helper behavior was not verified")
    names = ("getTaskChapterWordStats", "isConceptionOnlyTask", "isExecutionWindowTask",
             "isCompletedBookTask", "isBodyCompletePendingDelivery", "taskKnowledgeProgressText",
             "dashboardProjectStatus", "dashboardProjectStage", "isProjectClosedBook")
    script = "\n".join(javascript_function(name) for name in names)
    script += "\nconst task=" + json.dumps(task, ensure_ascii=False) + ";"
    script += "\nconst project=" + json.dumps(project or {}, ensure_ascii=False) + ";"
    script += """
console.log(JSON.stringify({
  completed: isCompletedBookTask(task),
  awaiting: isBodyCompletePendingDelivery(task),
  closed: isProjectClosedBook(project),
  project_stage: dashboardProjectStage(project),
  project_status: dashboardProjectStatus(project),
  adopted_count: getTaskChapterWordStats(task).completedChapters,
  word_total: getTaskChapterWordStats(task).wordTotal,
  knowledge_text: taskKnowledgeProgressText(task)
}));
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True,
                            check=True, timeout=10)
    return json.loads(result.stdout)


def adopted_task():
    return {
        "task_id": TASK_ID, "project_slug": SLUG, "status": "completed",
        "current_stage": "project_closed", "project_type": "fanqie_short",
        "chapter_word_stats": {
            "adoption_evidence": "verified", "adopted_chapters": 4,
            "completed_chapters": 4, "target_chapters": 4,
            "adopted_chapter_numbers": [1, 2, 3, 4], "word_count_total": 10940,
            "knowledge": {"adopted_chapters": 4, "closed_chapters": 4, "status": "closed"},
        },
    }


class ClientFormalDeliveryStatusRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.projections = python_projections()

    def state(self, **overrides):
        values = {"status": "writing", "completed_units": 4, "target_units": 4, "has_content": True}
        values.update(overrides)
        return self.projections["_library_book_state"](**values)

    def test_actual_count_route_cannot_certify_formal_delivery(self):
        # Actual defect: project.status=writing but 4/4 became closed_complete.
        self.assertNotEqual(self.state(), "closed_complete",
                            "4/4 adopted chapters prove body completion, not whole review/export")

    def test_status_aliases_without_formal_evidence_cannot_certify_delivery(self):
        for status in ("completed", "complete", "published", "closed", "closed_complete"):
            with self.subTest(status=status):
                self.assertNotEqual(self.state(status=status), "closed_complete",
                                    "Legacy status alone has no fresh formal receipt authority")

    def test_whole_review_pending_cannot_be_hidden_by_complete_body_count(self):
        self.assertNotEqual(self.state(has_active_workflow=True), "closed_complete")

    def test_whole_review_blocked_cannot_be_hidden_by_complete_body_count(self):
        self.assertNotEqual(self.state(repair_status={"is_repairing": True, "phase": "whole_review_invalid"}),
                            "closed_complete")

    def test_status_only_task_settlement_preserves_failure_and_error(self):
        task = {"task_id": TASK_ID, "project_slug": SLUG, "status": "failed",
                "current_stage": "failed", "error": "SYNTHETIC retained failure; no formal receipt"}
        before = deepcopy(task)
        self.projections["_settle_task_cards_from_project_status"]([task], {SLUG: "completed"})
        self.assertEqual(task, before, "Project status cannot erase original failure without delivery proof")

    def test_browser_completed_tag_and_4of4_are_not_formal_delivery(self):
        self.assertFalse(browser_predicates(adopted_task())["completed"],
                         "UI must require verified formal delivery, not a completed flag and counts")

    def test_browser_cannot_reclose_from_legacy_project_state(self):
        project = {"status": "writing", "book_state": "closed_complete",
                   "completed_chapters": 4, "target_chapters": 4}
        self.assertNotEqual(browser_predicates(adopted_task(), project)["project_status"], "completed",
                            "A stale closed_complete string is not a fresh receipt")

    def test_body_and_knowledge_progress_remain_visible_independently(self):
        result = browser_predicates(adopted_task())
        self.assertEqual(result["adopted_count"], 4)
        self.assertEqual(result["word_total"], 10940)
        self.assertEqual(result["knowledge_text"], "知识入库4/4（已闭合）")

    def test_draft_and_archive_display_semantics_remain_available(self):
        self.assertEqual(self.state(status="draft", completed_units=0, has_content=False), "draft")
        self.assertEqual(self.state(archived=True), "archived")

    def test_verified_native_delivery_closes_book_and_browser_without_erasing_failure_history(self):
        # Synthetic reader result for display control flow, not a paid certificate.
        delivery = {"status": "verified", "is_complete": True, "body_complete": True}
        self.assertEqual(self.state(delivery_closure=delivery), "closed_complete")
        task = adopted_task()
        task.update(status="failed", current_stage="failed", error="SYNTHETIC old title failure",
                    created_at="2026-10-20T01:00:00Z")
        before = deepcopy(task)
        self.projections["_settle_task_cards_from_project_status"]([task], {SLUG: "completed"}, {SLUG: delivery})
        self.assertEqual(task["task_id"], before["task_id"])
        self.assertEqual(task["created_at"], before["created_at"])
        self.assertEqual(task["current_stage"], "project_closed")
        self.assertIsNone(task["error"])
        self.assertEqual(task["task_history"][0]["error"], before["error"])
        self.assertEqual(task["task_history"][0]["current_stage"], "failed")
        result = browser_predicates(task, {"status": "completed", "delivery_closure": delivery})
        self.assertTrue(result["completed"])
        self.assertTrue(result["closed"])
        self.assertEqual(result["project_stage"], "project_closed")
        self.assertFalse(result["awaiting"])

    def test_four_KG_closed_without_package_or_with_corrupt_package_is_pending(self):
        for reason in ("completion_delivery_receipt_missing", "package_checksum_mismatch", "delivery_readback_unavailable"):
            delivery = {"status": "pending", "is_complete": False, "body_complete": True, "reason": reason}
            task = adopted_task(); task["delivery_closure"] = delivery
            result = browser_predicates(task, {"status": "completed", "book_state": "delivery_pending",
                                               "delivery_closure": delivery})
            with self.subTest(reason=reason):
                self.assertEqual(self.state(status="completed", delivery_closure=delivery), "delivery_pending")
                self.assertFalse(result["completed"]); self.assertFalse(result["closed"])
                self.assertTrue(result["awaiting"])
                self.assertEqual(result["project_stage"], "delivery_pending")
                self.assertEqual(result["word_total"], 10940)
                self.assertEqual(result["knowledge_text"], "知识入库4/4（已闭合）")

    def test_stored_true_with_unverified_status_cannot_close(self):
        delivery = {"status": "unverified", "is_complete": True}
        self.assertNotEqual(self.state(delivery_closure=delivery), "closed_complete")
        task = adopted_task(); task["delivery_closure"] = delivery
        self.assertFalse(browser_predicates(task, {"delivery_closure": delivery})["completed"])


@pytest.mark.parametrize("complete,body_complete,reason", [
    (True, True, "all_chapters_delivered"),
    (False, True, "delivery_unverified"),  # four exact KG sources, absent/broken package
    (False, False, "adopted_knowledge_incomplete"),
])
def test_display_reader_calls_native_closure_readonly_and_obeys_its_verdict(monkeypatch, complete, body_complete, reason):
    from bestseller.web import server
    from bestseller.services import book_delivery_readiness

    session = object(); project = SimpleNamespace(slug=SLUG)
    calls = []
    async def native_reader(actual_session, actual_project, *, lock):
        assert actual_session is session and actual_project is project
        calls.append(lock)
        return SimpleNamespace(verdict=SimpleNamespace(is_complete=complete, body_complete=body_complete, reason=reason))
    monkeypatch.setattr(book_delivery_readiness, "read_book_delivery_closure", native_reader)
    result = asyncio.run(server._read_project_delivery_status(session, project))
    assert calls == [False]
    assert result["is_complete"] is complete
    assert result["body_complete"] is body_complete
    assert result["reason"] == reason
    assert result["reader"] == "read_book_delivery_closure"


def test_native_reader_exception_never_uses_completed_project_or_metadata(monkeypatch):
    from bestseller.web import server
    from bestseller.services import book_delivery_readiness
    project = SimpleNamespace(slug=SLUG, status="completed", metadata_json={"book_state": "closed_complete"})
    async def broken_reader(*_args, **_kwargs): raise OSError("SYNTHETIC damaged package")
    monkeypatch.setattr(book_delivery_readiness, "read_book_delivery_closure", broken_reader)
    result = asyncio.run(server._read_project_delivery_status(object(), project))
    assert result["is_complete"] is False
    assert result["status"] == "unverified"


def test_title_enrichment_uses_fresh_closure_once_per_project_and_preserves_failed_history(monkeypatch):
    from bestseller.web import server
    from bestseller.services import book_delivery_readiness

    project = SimpleNamespace(slug=SLUG, title="SYNTHETIC actual title", status="completed")
    class Session:
        async def scalars(self, _statement): return [project]
    session = Session()
    class Scope:
        async def __aenter__(self): return session
        async def __aexit__(self, *_args): pass
    monkeypatch.setattr(server, "session_scope", lambda _: Scope())
    calls = []
    async def native_reader(actual_session, actual_project, *, lock):
        calls.append((actual_session, actual_project, lock))
        return SimpleNamespace(verdict=SimpleNamespace(is_complete=True, body_complete=True, reason="all_chapters_delivered"))
    monkeypatch.setattr(book_delivery_readiness, "read_book_delivery_closure", native_reader)
    tasks = [{**adopted_task(), "status": "failed", "current_stage": "failed", "error": "SYNTHETIC prior failure"},
             {**adopted_task(), "task_id": "second-synthetic-task"}]
    result = asyncio.run(server._apply_project_titles_to_tasks(SimpleNamespace(), tasks))
    assert calls == [(session, project, False)]
    assert all(task["status"] == "completed" and task["current_stage"] == "project_closed" for task in result)
    assert result[0]["task_history"][0]["error"] == "SYNTHETIC prior failure"
    progress = server._task_progress_payload(result[0])
    assert progress["delivery_closure"]["is_complete"] is True
    assert progress["current_stage"] == "project_closed"


def test_project_dashboard_card_requires_delivery_proof_even_for_closed_state():
    from bestseller.web import server
    row = {"slug": SLUG, "status": "completed", "book_state": "closed_complete",
           "completed_chapters": 4, "target_chapters": 4}
    assert server._project_row_to_dashboard_task(row)["status"] != "completed"
    row["delivery_closure"] = {"status": "verified", "is_complete": True}
    assert server._project_row_to_dashboard_task(row)["current_stage"] == "project_closed"


def test_unknown_project_cannot_reuse_cached_delivery_proof(monkeypatch):
    from bestseller.web import server
    class Session:
        async def scalars(self, _statement): return []
    class Scope:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *_args): pass
    monkeypatch.setattr(server, "session_scope", lambda _: Scope())
    task = adopted_task(); task["delivery_closure"] = {"status": "verified", "is_complete": True}
    result = asyncio.run(server._apply_project_titles_to_tasks(SimpleNamespace(), [task]))
    assert result[0]["delivery_closure"]["is_complete"] is False


def test_unavailable_readback_revokes_cached_verified_stage_without_erasing_history(monkeypatch):
    from bestseller.web import server
    class Scope:
        async def __aenter__(self): raise OSError("SYNTHETIC unavailable DB")
        async def __aexit__(self, *_args): pass
    monkeypatch.setattr(server, "session_scope", lambda _: Scope())
    task = adopted_task()
    task.update(current_stage="project_closed", book_state="closed_complete",
                current_stage_label="正式交付已核验", delivery_closure={"status": "verified", "is_complete": True})
    result, = asyncio.run(server._apply_project_titles_to_tasks(SimpleNamespace(), [task]))
    assert result["status"] == "incomplete"
    assert result["current_stage"] == "delivery_unverified"
    assert result["book_state"] != "closed_complete"
    assert result["task_history"][0]["current_stage"] == "project_closed"


@pytest.mark.parametrize("endpoint", ["/api/tasks?include_inactive=1", f"/api/tasks/{TASK_ID}",
                                      f"/api/tasks/{TASK_ID}/progress"])
@pytest.mark.parametrize("proof", ["positive", "missing", "negative", "stale"])
@pytest.mark.parametrize("cached_closed", [False, True])
def test_actual_GET_branches_share_delivery_authority_preserve_original_task_and_cache_per_request(monkeypatch, endpoint, proof, cached_closed):
    """Actual handler AST, synthetic reader seam; no server/socket or DB boot."""
    from bestseller.web import server

    delivery = {"status": "verified" if proof == "positive" else "pending", "is_complete": proof == "positive",
                "body_complete": True, "reason": "SYNTHETIC " + proof}
    original = {**adopted_task(), "status": "failed", "current_stage": "failed",
                "created_at": "2026-10-20T00:00:00Z", "updated_at": "2026-10-21T00:00:00Z",
                "error": "SYNTHETIC retained failed title", "task_type": "autowrite",
                # A cached positive must lose to the current missing/negative read.
                "delivery_closure": {"status": "verified", "is_complete": True}}
    if cached_closed:
        original.update(status="completed", current_stage="project_closed", book_state="closed_complete",
                        current_stage_label="正式交付已核验")
    original_before = deepcopy(original)
    row = {"slug": SLUG, "title": "SYNTHETIC current title", "status": "completed",
           "book_state": "closed_complete" if proof == "positive" else "delivery_pending",
           "adoption_evidence": "verified", "completed_chapters": 4, "adopted_chapters": 4,
           "target_chapters": 4, "word_count_total": 10940, "activity_status": "inactive",
           "created_at": original["created_at"], "updated_at": original["created_at"],
           "has_active_workflow": False, "current_workflow": {"status": "failed"}}
    if proof != "missing": row["delivery_closure"] = delivery
    async def projects(*_args, **_kwargs): return [deepcopy(row)]
    async def repairs(*_args, **_kwargs): return []
    monkeypatch.setattr(server, "_load_projects_payload", projects)
    monkeypatch.setattr(server, "_load_worker_task_summaries", lambda _: [])
    monkeypatch.setattr(server, "_load_db_repair_task_summaries", repairs)
    monkeypatch.setattr(server, "_attach_task_chapter_word_stats", lambda _, tasks, **_kw: [
        {**task, "chapter_word_stats": deepcopy(original["chapter_word_stats"])} for task in tasks])
    fresh_reads = []
    async def fresh(*_args):
        fresh_reads.append(True)
        return delivery
    monkeypatch.setattr(server, "_read_project_delivery_status", fresh)
    class Session:
        async def scalars(self, _statement):
            return [SimpleNamespace(slug=SLUG, title=row["title"], status="completed")]
    class Scope:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *_args): pass
    monkeypatch.setattr(server, "session_scope", lambda _: Scope())
    manager = SimpleNamespace(list_tasks=lambda: [deepcopy(original)],
                              get_task=lambda _: deepcopy(original), is_task_executing=lambda _: False)
    tree = ast.parse(Path(server.__file__).read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "do_GET")
    namespace = {**vars(server), "settings": SimpleNamespace(), "task_manager": manager}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(server.__file__), "exec"), namespace)
    responses = []
    def error(exc): raise exc
    handler = SimpleNamespace(path=endpoint, _require_auth=lambda: True, _send_json=responses.append,
                              _route_error=error, _route_not_found=lambda: pytest.fail("Unexpected 404"))
    namespace["do_GET"](handler)
    response = responses[0][0] if isinstance(responses[0], list) else responses[0]
    assert response["delivery_closure"]["is_complete"] is (proof == "positive")
    assert response["chapter_word_stats"]["word_count_total"] == 10940
    if proof == "positive":
        assert response["status"] == "completed"
        assert response["current_stage"] == "project_closed"
        assert any(entry.get("error") == original["error"] for entry in response["task_history"])
    else:
        assert response["current_stage"] != "project_closed"
    if endpoint != "/api/tasks?include_inactive=1":
        assert response["task_id"] == TASK_ID
        if not endpoint.endswith("/progress"):
            assert response["created_at"] == original["created_at"]
    # Missing reader result may require enrichment's first read, but a supplied
    # current-request result must never trigger a second full native read.
    assert len(fresh_reads) == int(proof == "missing")
    assert original == original_before


@pytest.mark.parametrize("surface", ["light", "full", "library"])
@pytest.mark.parametrize("complete", [True, False])
def test_project_surfaces_pass_fresh_closure_to_state_and_preserve_word_source(monkeypatch, tmp_path, surface, complete):
    from bestseller.web import server
    project = SimpleNamespace(id=uuid4(), slug=SLUG, title="SYNTHETIC current title", genre="fiction",
        status="completed", language="zh-CN", metadata_json={}, project_type="linear",
        target_chapters=4, target_word_count=12000, current_chapter_number=4,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    progress = {project.id: {**adopted_task()["chapter_word_stats"], "word_count_total": 10940}}
    class Scope:
        async def __aenter__(self): return SimpleNamespace()
        async def __aexit__(self, *_args): pass
    monkeypatch.setattr(server, "session_scope", lambda _: Scope())
    async def projects(*_args): return [project]
    async def stats(*_args, **_kwargs): return progress
    async def empty(*_args, **_kwargs): return {}
    monkeypatch.setattr(server, "list_projects", projects)
    monkeypatch.setattr(server, "_load_project_content_stats", stats)
    monkeypatch.setattr(server, "_load_adopted_chapter_progress", stats)
    for name in ("_load_workflow_activity_by_project", "_load_project_chapter_state_counts",
                 "_load_project_autonomous_repair_task_counts", "_load_latest_project_repair_workflow_snapshots"):
        monkeypatch.setattr(server, name, empty)
    calls = []
    async def delivery_reader(session, actual_project):
        assert actual_project is project
        calls.append(True)
        return {"status": "verified" if complete else "pending", "is_complete": complete,
                "body_complete": True, "reason": "SYNTHETIC current closure"}
    monkeypatch.setattr(server, "_read_project_delivery_status", delivery_reader)
    settings = SimpleNamespace(output=SimpleNamespace(base_dir=tmp_path), redis=SimpleNamespace(url=None))
    if surface == "library":
        rows = asyncio.run(server._load_library_payload(settings))
    else:
        rows = asyncio.run(server._load_projects_payload(settings, light=surface == "light"))
    assert calls == [True]
    assert rows[0]["book_state"] == ("closed_complete" if complete else "delivery_pending")
    assert rows[0]["delivery_closure"]["is_complete"] is complete
    assert rows[0]["word_count_total"] == 10940
    assert rows[0]["completed_chapters"] == 4


if __name__ == "__main__":
    unittest.main(verbosity=2)
