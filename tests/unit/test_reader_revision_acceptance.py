from types import SimpleNamespace
import json
from uuid import uuid4

import pytest
from bestseller.services import chapter_revision as revision
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_explicit_length_band_allows_intentional_shortening():
    assert revision._issue_length_band(
        "目标字数：3200-3800字\n兑现冲突", SimpleNamespace(metadata_json={}), load_settings()
    ) == (3200, 3800)


def test_prose_numbers_do_not_override_project_policy():
    project = SimpleNamespace(
        metadata_json={"words_per_chapter": {"min": 2000, "target": 2500, "max": 3000}}
    )
    assert revision._issue_length_band(
        "原文提及3200-3800字，修改人物动机。", project, load_settings()
    ) == (2000, 3000)


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        '{"checks":[]}',
        "not json",
        '{"checks":[{"requirement":"兑现冲突","pass":true,"evidence":"不存在","reason":"完成"}]}',
    ],
)
def test_acceptance_fails_closed_on_missing_or_fabricated_evidence(raw):
    result = revision._parse_issue_acceptance(raw, ["兑现冲突"], "真实正文")
    assert result["passed"] is False


def test_acceptance_requires_every_requested_item():
    raw = json.dumps(
        {
            "checks": [
                {"requirement": "找回证据", "pass": True, "evidence": "信封", "reason": "找回"}
            ]
        }
    )
    assert not revision._parse_issue_acceptance(raw, ["找回证据", "保护证人"], "她找回信封。")[
        "passed"
    ]


@pytest.mark.parametrize(
    "requirement,prose",
    [("凶手交代动机", "凶手说出了复仇的缘由。"), ("主角承受法术代价", "他的旧伤重新裂开。")],
)
def test_acceptance_uses_evidence_across_genres(requirement, prose):
    raw = json.dumps(
        {
            "checks": [
                {
                    "requirement": requirement,
                    "pass": True,
                    "evidence": prose,
                    "reason": "正文有明确行为",
                }
            ]
        }
    )
    assert revision._parse_issue_acceptance(raw, [requirement], prose)["passed"]


class Session:
    def __init__(self, task, current, latest=None):
        self.task, self.current = task, current
        self.latest = latest or current.id
        self.added, self.executed = [], []
        self.calls = 0

    async def get(self, *args):
        return self.task

    async def scalar(self, stmt):
        self.calls += 1
        return [self.current.chapter_id, self.latest, self.current.version_no, self.current.id][
            self.calls - 1
        ]

    async def execute(self, stmt):
        self.executed.append(stmt)

    def add(self, value):
        value.id = uuid4()
        self.added.append(value)

    async def flush(self):
        pass


def fixtures():
    project = SimpleNamespace(id=uuid4(), slug="detective-story", metadata_json={})
    chapter = SimpleNamespace(
        id=uuid4(), chapter_number=20, current_word_count=6047, production_state="quality_debt"
    )
    current = SimpleNamespace(
        id=uuid4(),
        chapter_id=chapter.id,
        content_md="文" * 6047,
        word_count=6047,
        version_no=4,
        assembled_from_scene_draft_ids=[],
    )
    task = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        instructions="目标字数：3200-3800字\n凶手交代动机",
        attempts=0,
        status="queued",
        metadata_json={"source_chapter_draft_id": str(current.id)},
    )
    return project, chapter, current, task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "size,passed,reason",
    [
        (6047, True, "candidate_word_count_out_of_range"),
        (3500, False, "issue_acceptance_failed"),
        (3100, True, "candidate_word_count_out_of_range"),
        (3500, True, None),
    ],
)
async def test_issue_promotes_only_accepted_in_band_candidate(monkeypatch, size, passed, reason):
    project, chapter, current, task = fixtures()

    async def load(*args):
        return project, chapter, current

    async def candidate(*args, **kwargs):
        return "改" * size, {}, None

    async def accept(*args, **kwargs):
        return {
            "passed": passed,
            "checks": [
                {
                    "requirement": "凶手交代动机",
                    "pass": passed,
                    "reason": "未解释" if not passed else "已解释",
                    "evidence": "",
                }
            ],
        }

    monkeypatch.setattr(revision, "_load_current_chapter_draft", load)
    monkeypatch.setattr(revision, "_build_revision_candidate", candidate)
    monkeypatch.setattr(revision, "_accept_issue_candidate", accept)
    session = Session(task, current)
    result = await revision.apply_chapter_revision_task(
        session, load_settings(), project.slug, 20, rewrite_task_id=task.id, operation="issue"
    )
    assert result["ok"] is (reason is None)
    assert chapter.production_state == "quality_debt"
    if reason:
        assert result["rejected_reason"] == reason
        assert not session.added and not session.executed
        assert current.content_md == "文" * 6047
        assert chapter.current_word_count == 6047
        assert task.status == "failed"
    else:
        assert result["result_version_no"] == 5
        assert len(session.added) == 1
        assert session.added[0].is_current
        assert chapter.current_word_count == size


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before", "after"])
async def test_stale_source_never_overwrites_current(monkeypatch, when):
    project, chapter, current, task = fixtures()
    if when == "before":
        task.metadata_json["source_chapter_draft_id"] = str(uuid4())

    async def load(*args):
        return project, chapter, current

    async def candidate(*args, **kwargs):
        assert when == "after", "stale task must not call model"
        return "改" * 3500, {}, None

    async def accept(*args, **kwargs):
        return {"passed": True, "checks": []}

    monkeypatch.setattr(revision, "_load_current_chapter_draft", load)
    monkeypatch.setattr(revision, "_build_revision_candidate", candidate)
    monkeypatch.setattr(revision, "_accept_issue_candidate", accept)
    session = Session(task, current, latest=uuid4())
    result = await revision.apply_chapter_revision_task(
        session, load_settings(), project.slug, 20, rewrite_task_id=task.id, operation="issue"
    )
    assert result["rejected_reason"] == "stale_source_draft"
    assert not session.added and not session.executed


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["{}", "outage"])
async def test_checker_failure_persists_rejection_and_keeps_source(monkeypatch, response):
    project, chapter, current, task = fixtures()

    async def load(*args):
        return project, chapter, current

    async def candidate(*args, **kwargs):
        return "改" * 3500, {}, None

    async def complete(session, settings, request):
        assert request.project_id == project.id
        assert request.prompt_template == "reader_chapter_issue_acceptance"
        if response == "outage":
            raise RuntimeError("offline")
        return SimpleNamespace(content=response, llm_run_id=uuid4())

    monkeypatch.setattr(revision, "_load_current_chapter_draft", load)
    monkeypatch.setattr(revision, "_build_revision_candidate", candidate)
    monkeypatch.setattr(revision, "complete_text", complete)
    session = Session(task, current)
    result = await revision.apply_chapter_revision_task(
        session, load_settings(), project.slug, 20, rewrite_task_id=task.id, operation="issue"
    )
    assert result["ok"] is False and task.status == "failed"
    assert task.metadata_json["acceptance"]["error"] in {
        "invalid_acceptance_response",
        "acceptance_unavailable",
    }
    assert not session.added and not session.executed


@pytest.mark.asyncio
async def test_length_only_request_still_runs_story_preservation_acceptance(monkeypatch):
    project, chapter, current, task = fixtures()
    task.instructions = "目标字数：3200-3800字"
    seen = {}

    async def complete(session, settings, request):
        payload = json.loads(request.user_prompt)
        seen.update(payload)
        return SimpleNamespace(
            content=json.dumps(
                {
                    "checks": [
                        {
                            "requirement": revision._STORY_PRESERVATION_REQUIREMENT,
                            "pass": True,
                            "evidence": "保留主线",
                            "reason": "候选保留主要人物和因果",
                        }
                    ]
                }
            ),
            llm_run_id=uuid4(),
            provider="test",
        )

    monkeypatch.setattr(revision, "complete_text", complete)
    result = await revision._accept_issue_candidate(
        object(),
        load_settings(),
        project=project,
        task=task,
        candidate="候选正文，保留主线",
        context={"source_draft": "原稿主线"},
    )
    assert result["passed"] is True
    assert seen["requirements"] == [revision._STORY_PRESERVATION_REQUIREMENT]
    assert seen["context"]["source_draft"] == "原稿主线"


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [{"fallback_used": True}, {"provider": "mock"}, {"finish_reason": "length"}])
async def test_acceptance_provider_failure_is_fail_closed(monkeypatch, flags):
    project, _chapter, _current, task = fixtures()

    async def complete(session, settings, request):
        payload = {"content": "{}", "llm_run_id": uuid4(), "provider": "test"}
        payload.update(flags)
        return SimpleNamespace(**payload)

    monkeypatch.setattr(revision, "complete_text", complete)
    result = await revision._accept_issue_candidate(
        object(),
        load_settings(),
        project=project,
        task=task,
        candidate="候选正文",
        context={"source_draft": "原稿"},
    )
    assert result["passed"] is False
    assert result["error"] == "acceptance_generation_unavailable"


@pytest.mark.asyncio
async def test_issue_prompt_carries_bounded_source_and_neighbor_versions(monkeypatch):
    project, chapter, current, task = fixtures()
    current.content_md = "文" * 3000  # ordinary issue path, not explicit shortening
    project.title = "侦探故事"
    chapter.title = "真相"
    chapter.chapter_goal = "解释动机"
    project.metadata_json = {
        "creation_intent_contract": {
            "concept_seed": "侦探要找回失踪的信",
            "hook_spec": {"promise": "信件揭示真相"},
        }
    }
    project.invariants_json = {
        "rule": "没有魔法",
        "hype_scheme": {"recipe_deck": ["血脉觉醒"]},
        "project_id": "internal",
    }
    project.reader_contract_json = {}
    previous = SimpleNamespace(id=uuid4(), version_no=2, content_md="前" * 4000)
    following = SimpleNamespace(id=uuid4(), version_no=3, content_md="后" * 4000)

    class ContextSession:
        async def execute(self, stmt):
            return SimpleNamespace(all=lambda: [(19, previous), (21, following)])

    async def complete(session, settings, request):
        assert "侦探要找回失踪的信" in request.user_prompt
        assert "recipe_deck" not in request.user_prompt and "血脉觉醒" not in request.user_prompt
        assert "没有魔法" in request.user_prompt
        assert "解释动机" in request.user_prompt
        assert str(previous.id) in request.user_prompt and str(following.id) in request.user_prompt
        assert "prior_facts_context" in request.user_prompt
        assert "following_continuity_constraints" in request.user_prompt
        assert request.user_prompt.index("prior_facts_context") < request.user_prompt.index(
            "following_continuity_constraints"
        )
        assert "目标字数：3200-3800字" in request.user_prompt
        assert "<editable_draft>" in request.user_prompt
        assert "</editable_draft>" in request.user_prompt
        assert request.user_prompt.index(current.content_md) < request.user_prompt.rindex(
            task.instructions
        )
        assert request.user_prompt.index("</editable_draft>") < request.user_prompt.rindex(
            "目标字数：3200-3800字"
        )
        assert "本章是可修改的草稿" in request.system_prompt
        assert "旧章节目标仅作参考" in request.system_prompt
        assert "不能复制原文" in request.system_prompt
        return SimpleNamespace(
            content="改" * 3500, llm_run_id=uuid4(), provider="test", model_name="test"
        )

    monkeypatch.setattr(revision, "complete_text", complete)
    _, metadata, _ = await revision._build_revision_candidate(
        ContextSession(),
        load_settings(),
        project=project,
        chapter=chapter,
        current=current,
        task=task,
        operation="issue",
    )
    context = metadata["revision_context"]
    assert "adjacent_chapters" not in context
    assert context["prior_facts_context"]["chapter_number"] == 19
    assert context["following_continuity_constraints"]["chapter_number"] == 21
    assert context["following_continuity_constraints"]["status"] == "future_continuity_only"


@pytest.mark.asyncio
async def test_source_changed_after_read_is_rejected_by_atomic_compare(monkeypatch):
    project, chapter, current, task = fixtures()

    async def load(*args):
        return project, chapter, current

    async def candidate(*args, **kwargs):
        return "改" * 3500, {}, None

    async def accept(*args, **kwargs):
        return {"passed": True, "checks": []}

    monkeypatch.setattr(revision, "_load_current_chapter_draft", load)
    monkeypatch.setattr(revision, "_build_revision_candidate", candidate)
    monkeypatch.setattr(revision, "_accept_issue_candidate", accept)

    class RaceSession(Session):
        async def scalar(self, stmt):
            result = await super().scalar(stmt)
            return None if self.calls == 4 else result

    session = RaceSession(task, current)
    result = await revision.apply_chapter_revision_task(
        session, load_settings(), project.slug, 20, rewrite_task_id=task.id, operation="issue"
    )
    assert result["rejected_reason"] == "stale_source_draft"
    assert not session.added
    assert chapter.current_word_count == 6047


@pytest.mark.parametrize(
    "instructions",
    ["目标字数：3800-3200字", "目标字数：三千字", "目标字数：2000-3000字\n目标字数：2500-3500字"],
)
def test_invalid_explicit_band_is_not_silently_ignored(instructions):
    with pytest.raises(ValueError):
        revision._issue_length_band(
            instructions, SimpleNamespace(metadata_json={}), load_settings()
        )


@pytest.mark.asyncio
async def test_issue_empty_sanitized_response_rejected_without_source_fallback(monkeypatch):
    project, chapter, current, task = fixtures()
    current.content_md = "文" * 3000  # ordinary issue path, not explicit shortening
    project.title = "调查"
    chapter.title = "真相"
    raw = "### 修订说明\n这是给编辑的说明，不是正文。"

    async def load(*args):
        return project, chapter, current

    async def context(*args):
        return {}

    async def complete(*args):
        return SimpleNamespace(
            content=raw,
            model_name="test",
            provider="test",
            llm_run_id=uuid4(),
            fallback_used=False,
            fallback_source=None,
        )

    monkeypatch.setattr(revision, "_load_current_chapter_draft", load)
    monkeypatch.setattr(revision, "_issue_context", context)
    monkeypatch.setattr(revision, "complete_text", complete)
    session = Session(task, current)
    result = await revision.apply_chapter_revision_task(
        session, load_settings(), project.slug, 20, rewrite_task_id=task.id, operation="issue"
    )
    assert result["rejected_reason"] == "candidate_empty_after_sanitization"
    assert not session.added and not session.executed
    trace = task.metadata_json["candidate_provenance"]
    assert trace["raw"]["sha256"] != trace["source"]["sha256"]
    assert trace["sanitized"]["word_count"] == 0 and trace["final"]["word_count"] == 0
    assert trace["sanitizer_empty"] is True
    assert trace["llm_fallback_used"] is False


@pytest.mark.asyncio
async def test_issue_unchanged_source_rejected_before_length_mismatch(monkeypatch):
    project, chapter, current, task = fixtures()

    async def load(*args):
        return project, chapter, current

    async def candidate(*args, **kwargs):
        return current.content_md, {}, None

    monkeypatch.setattr(revision, "_load_current_chapter_draft", load)
    monkeypatch.setattr(revision, "_build_revision_candidate", candidate)
    session = Session(task, current)
    result = await revision.apply_chapter_revision_task(
        session, load_settings(), project.slug, 20, rewrite_task_id=task.id, operation="issue"
    )
    assert result["rejected_reason"] == "issue_no_change"
    assert not session.added and not session.executed


def test_acceptance_array_keeps_requirement_and_prose_evidence_checks():
    checks = [
        {"requirement": "找回证据", "pass": True, "evidence": "她找回信封", "reason": "动作完成"}
    ]
    assert revision._parse_issue_acceptance(json.dumps(checks), ["找回证据"], "她找回信封。")[
        "passed"
    ]
    checks[0]["evidence"] = "不存在的动作"
    assert not revision._parse_issue_acceptance(json.dumps(checks), ["找回证据"], "她找回信封。")[
        "passed"
    ]
