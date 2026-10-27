# ruff: noqa: E501, UP037
from __future__ import annotations

import argparse
import importlib.util
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_prepare_source_phase_persists_bound_running_then_pass(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    spec = importlib.util.spec_from_file_location("repair_derived_inputs_test", "scripts/repair_derived_inputs.py")
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    project_id = uuid4()
    artifact_ids = {"cast": uuid4(), "book": uuid4(), "world": uuid4(), "premise": uuid4()}
    project = SimpleNamespace(
        id=project_id, slug="demo", status="paused", language="zh-CN",
        metadata_json={"derived_input_revision": {"status": "applied", "report_id": "rev-1", "book_artifact_id": str(artifact_ids["book"]), "world_artifact_id": str(artifact_ids["world"])}},
    )
    artifacts = {
        "cast": SimpleNamespace(id=artifact_ids["cast"], version_no=1, status="approved", content={"protagonist": {"name": "纪年"}}),
        "book": SimpleNamespace(id=artifact_ids["book"], status="approved", content={"book_spec": {"protagonist": {"name": "纪年"}}}),
        "world": SimpleNamespace(id=artifact_ids["world"], status="approved", content={"world_spec": {"rules": ["灵脉"]}}),
        "premise": SimpleNamespace(id=artifact_ids["premise"], status="approved", content={"premise": "纪年寻找灵脉"}),
    }
    applied = SimpleNamespace(structured_output={"valid": True})

    class FakeSession:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.running_budget: object = None
            self.commits = 0
            self.values = [project, applied, None, True, None]

        async def scalar(self, _query: object) -> object:
            return self.values.pop(0)

        async def scalars(self, _query: object) -> object:
            return SimpleNamespace(all=lambda: [])

        def add(self, value: object) -> None:
            self.added.append(value)
            self.running_budget = dict(value.structured_output["budget"])

        async def commit(self) -> None:
            self.commits += 1

    session = FakeSession()

    class FakeScope:
        async def __aenter__(self) -> FakeSession:
            return session

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakeColumn:
        def __eq__(self, _other: object) -> "FakeColumn":
            return self

        def __getitem__(self, _key: object) -> "FakeColumn":
            return self

        @property
        def astext(self) -> "FakeColumn":
            return self

    class FakeReview:
        id = FakeColumn()
        project_id = FakeColumn()
        reviewer_type = FakeColumn()
        verdict = FakeColumn()
        structured_output = FakeColumn()
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)
            self.id = uuid4()

    class FakeQuery:
        def where(self, *_args: object, **_kwargs: object) -> "FakeQuery":
            return self

        def limit(self, *_args: object, **_kwargs: object) -> "FakeQuery":
            return self

        def order_by(self, *_args: object, **_kwargs: object) -> "FakeQuery":
            return self

    monkeypatch.setattr(runner, "session_scope", lambda _settings: FakeScope())
    monkeypatch.setattr(runner, "load_settings", lambda: object())
    monkeypatch.setattr(runner, "ReviewReportModel", FakeReview)
    monkeypatch.setattr(runner, "select", lambda *_args, **_kwargs: FakeQuery())
    async def fake_latest(*_a: object, **kw: object) -> object:
        return artifacts[kw["artifact_type"].value.replace("_spec", "")]
    monkeypatch.setattr(runner, "get_latest_planning_artifact", fake_latest)
    monkeypatch.setattr(runner, "source_content_hash", lambda value: "attempt-hash")
    async def fake_control(*_a: object) -> object:
        return SimpleNamespace(halted=True)
    monkeypatch.setattr(runner, "load_control_state", fake_control)
    monkeypatch.setattr("bestseller.services.derived_revision.verify_derived_revision_report", lambda _report: None)
    monkeypatch.setattr("bestseller.services.planner._compile_source_bound_cast_spec", lambda *args: {"protagonist": {"name": "纪年"}})
    monkeypatch.setattr("bestseller.services.planner._enforce_premise_locked_cast", lambda cast, *_args, **_kw: cast)
    async def fake_prepare(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"status": "review_passed", "budget": {"max_calls": 3, "calls": 3}, "reviews": ["r1", "r2"], "candidate_cast": {}, "audit": {}}
    monkeypatch.setattr("bestseller.services.derived_cast_source_review.prepare_cast_source_review", fake_prepare)
    monkeypatch.setattr("bestseller.services.derived_cast_source_review.build_cast_recovery_candidate", lambda *args, **kwargs: ({"protagonist": {"name": "纪年"}}, {"review_input_hash": "input", "candidate_cast_hash": "candidate"}))

    args = argparse.Namespace(slug="demo", phase="prepare-cast-source-review", report_id="rev-1", issues=None, advisory_candidate=None, cast_report_id=None, cast_source_review_id=None, lock_version=None)
    result = await runner.run(args)
    assert result == 0
    output = capsys.readouterr().out
    assert '"phase": "prepare-cast-source-review"' in output
    assert session.commits == 2
    assert session.running_budget == {"max_calls": 3, "calls": 0}
