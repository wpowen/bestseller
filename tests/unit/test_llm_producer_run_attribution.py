"""Check run attribution at the producer and its chapter-first call boundary.

Only the external completion is replaced. The boundary harness executes the
actual call expression without running chapter generation or touching a DB.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import chapter_continuity_critic as continuity
from bestseller.services import opening_revise as opening
from bestseller.services.writing_profile import (
    get_project_runtime_writing_profile, render_writer_voice_data,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("has_step", [True, False])
def test_continuity_request_preserves_current_run(monkeypatch, has_step):
    project_id, workflow_id = uuid4(), uuid4()
    step_id = uuid4() if has_step else None
    requests = []

    async def complete(_session, _settings, request):
        requests.append(request)
        return SimpleNamespace(content='{"findings": []}')

    monkeypatch.setattr(continuity, "complete_text", complete)
    report = asyncio.run(continuity.audit_chapter_continuity(
        None, object(), chapter_text="门外有人敲门。", chapter_number=2,
        project_id=project_id, workflow_run_id=workflow_id, step_run_id=step_id,
    ))
    assert report.passed
    assert len(requests) == 1
    request = requests[0]
    assert (request.project_id, request.workflow_run_id, request.step_run_id) == (
        project_id, workflow_id, step_id,
    )


def test_chapter_first_continuity_call_preserves_current_run(monkeypatch):
    source = Path(continuity.__file__).with_name("drafts.py")
    tree = ast.parse(source.read_text())
    producer = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "generate_chapter_draft_once")
    call = next(node for node in ast.walk(producer) if isinstance(node, ast.Await)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "audit_chapter_continuity")
    harness = ast.parse("async def run():\n    return None\n")
    harness.body[0].body[0].value = call
    ast.fix_missing_locations(harness)
    project_id, workflow_id, step_id = uuid4(), uuid4(), uuid4()
    requests = []

    async def complete(_session, _settings, request):
        requests.append(request)
        return SimpleNamespace(content='{"findings": []}')

    monkeypatch.setattr(continuity, "complete_text", complete)
    namespace = {
        "audit_chapter_continuity": continuity.audit_chapter_continuity,
        "session": None, "effective_settings": object(),
        "content_md": "门外有人敲门。", "chapter_number": 2,
        "project": SimpleNamespace(id=project_id),
        "workflow_run_id": workflow_id, "step_run_id": step_id,
        "scenes": [], "_chapter_participant_names": lambda _scenes: (),
    }
    exec(compile(harness, str(source), "exec"), namespace)
    asyncio.run(namespace["run"]())
    assert len(requests) == 1
    request = requests[0]
    assert (request.project_id, request.workflow_run_id, request.step_run_id) == (
        project_id, workflow_id, step_id,
    )


def test_opening_request_preserves_current_run(monkeypatch):
    project_id, workflow_id, step_id = uuid4(), uuid4(), uuid4()
    requests = []
    content = "门外有人敲门。他攥着钥匙没有开门。"

    async def complete(_session, _settings, request):
        requests.append(request)
        return SimpleNamespace(content=content)

    monkeypatch.setattr(opening, "complete_text", complete)
    assert asyncio.run(opening.revise_opening_qimao(
        None, object(), content=content, instructions="保留人物与事件。",
        project_id=project_id, workflow_run_id=workflow_id, step_run_id=step_id,
    )) == content
    request = requests[0]
    assert (request.project_id, request.workflow_run_id, request.step_run_id) == (
        project_id, workflow_id, step_id,
    )


@pytest.mark.parametrize("service", ["continuity", "opening"])
def test_standalone_calls_do_not_invent_run_lineage(monkeypatch, service):
    requests = []

    async def complete(_session, _settings, request):
        requests.append(request)
        return SimpleNamespace(content='{"findings": []}' if service == "continuity"
                               else "门外有人敲门。")

    if service == "continuity":
        monkeypatch.setattr(continuity, "complete_text", complete)
        asyncio.run(continuity.audit_chapter_continuity(
            None, object(), chapter_text="门外有人敲门。", chapter_number=2,
        ))
    else:
        monkeypatch.setattr(opening, "complete_text", complete)
        asyncio.run(opening.revise_opening_qimao(
            None, object(), content="门外有人敲门。", instructions="保留事件。",
        ))
    request = requests[0]
    assert (request.project_id, request.workflow_run_id, request.step_run_id) == (
        None, None, None,
    )


@pytest.mark.parametrize("name", ["revise_opening_qimao", "revise_prose_deslop"])
def test_pipeline_rewrite_call_preserves_actual_workflow(name):
    source = Path(continuity.__file__).with_name("pipelines.py")
    tree = ast.parse(source.read_text())
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Await)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == name)
    harness = ast.parse("async def run():\n    return None\n")
    harness.body[0].body[0].value = call
    ast.fix_missing_locations(harness)
    project_id, workflow_id = uuid4(), uuid4()
    received = []

    async def rewrite(*_args, **kwargs):
        received.append(kwargs)
        return kwargs['content']

    namespace = {
        name: rewrite, "session": None, "settings": object(),
        "chapter_draft": SimpleNamespace(content_md="本轮正文"),
        "_instructions": "只修本轮发现的问题", "_af_lang": "zh-CN", "_af_target": 2000,
        "project": SimpleNamespace(id=project_id), "chapter_number": 2,
        "workflow_run": SimpleNamespace(id=workflow_id), "_observe_deslop_candidate": None,
        "chapter": SimpleNamespace(), "render_hype_preservation_block": lambda *args, **kwargs: "",
        "get_project_runtime_writing_profile": get_project_runtime_writing_profile,
        "render_writer_voice_data": render_writer_voice_data,
    }
    exec(compile(harness, str(source), "exec"), namespace)
    asyncio.run(namespace['run']())
    assert received[0]['project_id'] == project_id
    assert received[0]['workflow_run_id'] == workflow_id
    assert received[0].get('step_run_id') is None
