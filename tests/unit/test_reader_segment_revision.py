from types import SimpleNamespace
from uuid import uuid4
import json
import pytest
from bestseller.services import chapter_revision as revision
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def plan(budgets=(1000, 1000, 1000)):
    count = len(budgets)
    return {
        "prior_facts": ["证据此前由证人保存"],
        "editable_events": ["待改事件"],
        "segments": [
            {
                "number": i + 1,
                "budget": budgets[i],
                "goal": f"推进{i}",
                "entry": "前态" if i == 0 else "后态",
                "exit": "后态",
                "requirement_indexes": [i] if i < 2 else [],
            }
            for i in range(count)
        ],
    }


def test_segment_plan_budget_and_requirement_coverage():
    assert revision._parse_segment_plan(json.dumps(plan()), (1000, 1000, 1000), 2)["segments"]
    bad = plan()
    bad["segments"][1]["requirement_indexes"] = []
    with pytest.raises(ValueError):
        revision._parse_segment_plan(json.dumps(bad), (1000, 1000, 1000), 2)
    bad = plan()
    bad["segments"][0]["budget"] = 2000
    with pytest.raises(ValueError):
        revision._parse_segment_plan(json.dumps(bad), (1000, 1000, 1000), 2)


@pytest.mark.parametrize(
    "text,count,wanted",
    [
        ("目标字数：2800-3200字", 6047, True),
        ("目标字数：2800-6200字", 6047, False),
        ("把6047字压缩到3000字", 6047, False),
    ],
)
def test_only_explicit_shortening_uses_segments(text, count, wanted):
    current = SimpleNamespace(assembled_from_scene_draft_ids=[uuid4(), uuid4()])
    assert revision._use_segmented_issue(text, count, current) is wanted


@pytest.mark.parametrize("scene_count,wanted", [(0, 1), (1, 1), (2, 2), (8, 6)])
def test_segment_count_is_bounded_and_derived_from_scene_structure(scene_count, wanted):
    current = SimpleNamespace(assembled_from_scene_draft_ids=[uuid4() for _ in range(scene_count)])
    assert revision._segment_count_for_revision(current) == wanted


def test_segment_plan_allows_time_jump_without_forcing_verbatim_seams():
    value = plan()
    value["opening_anchor"] = "前章停在城门。"
    value["segments"][0]["entry"] = "三日后，审讯开始。"
    value["segments"][1]["entry"] = "倒叙回到昨夜。"
    revision._parse_segment_plan(json.dumps(value), (1000, 1000, 1000), 2, value["opening_anchor"])


class Session:
    def __init__(self, task, current, latest=None):
        self.task, self.current = task, current
        self.latest = latest or current.id
        self.calls = 0
        self.added = []

    async def get(self, *args):
        return self.task

    async def scalar(self, stmt):
        self.calls += 1
        return [self.current.chapter_id, self.latest, 4, self.current.id][self.calls - 1]

    async def execute(self, stmt):
        raise AssertionError("unexpected write or query")

    def add(self, value):
        value.id = uuid4()
        self.added.append(value)

    async def flush(self):
        pass


def fixtures():
    project = SimpleNamespace(id=uuid4(), slug="other-genre", metadata_json={})
    chapter = SimpleNamespace(
        id=uuid4(), chapter_number=4, current_word_count=6047, production_state="quality_debt"
    )
    current = SimpleNamespace(
        id=uuid4(),
        chapter_id=chapter.id,
        version_no=4,
        content_md="原" * 6047,
        word_count=6047,
        assembled_from_scene_draft_ids=[uuid4(), uuid4()],
    )
    task = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        status="queued",
        attempts=0,
        instructions="目标字数：2800-3200字\n取回证据\n保护证人",
        metadata_json={"source_chapter_draft_id": str(current.id)},
    )
    return project, chapter, current, task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "bad_plan",
        "second_segment",
        "length",
        "fallback",
        "rejected",
        "oversize",
        "stale",
        "compressed",
        "compression_length",
    ],
)
async def test_bounded_segments_keep_rejected_material_and_only_publish_after_acceptance(
    monkeypatch, failure
):
    project, chapter, current, task = fixtures()
    requests = []

    async def load(*args):
        return project, chapter, current

    async def context(*args):
        return {
            "story_context": {"concept_seed": "调查失踪信件"},
            "prior_facts_context": None,
            "following_continuity_constraints": None,
        }

    async def complete(session, settings, request):
        requests.append(request)
        assert request.project_id == project.id
        data = json.loads(request.user_prompt)
        template = request.prompt_template
        if template == "reader_issue_segment_plan":
            assert data["editable_draft"] == current.content_md
            budgets = tuple(data["budgets"])
            response = plan(budgets)
            response["segments"][0]["budget"] = 999 if failure == "bad_plan" else budgets[0]
            content = json.dumps(response)
        elif template == "reader_issue_segment_writer":
            assert current.content_md not in request.user_prompt
            assert "plan" not in data and "editable_events" not in data
            assert len(data["requirements"]) <= 1
            n = data["current_segment"]["number"]
            assert bool(data["written_so_far"]) is (n > 1)
            if n == 2 and failure == "second_segment":
                raise RuntimeError("offline")
            content = (
                "\n\n".join(chr(0x4E10 + n) * count for count in [600, 600, 600, 600, 600])
                if failure in {"oversize", "compressed", "compression_length"}
                else chr(0x4E10 + n) * data["current_segment"]["budget"]
            )
        elif template == "reader_issue_segment_compress":
            assert "source_content_md" in data and "editable_segment" not in data
            content = (
                "\n\n".join("改写" * 150 for _ in range(5))
                if failure != "oversize"
                else "\n\n".join("超长" * 600 for _ in range(5))
            )
        else:
            assert template == "reader_chapter_issue_acceptance"
            content = json.dumps(
                {
                    "checks": [
                        {
                            "requirement": r,
                            "pass": failure != "rejected",
                            "reason": "已兑现" if failure != "rejected" else "未兑现",
                            "evidence": data["candidate"][:10] if failure != "rejected" else "",
                        }
                        for r in data["requirements"]
                    ]
                }
            )
        return SimpleNamespace(
            content=content,
            llm_run_id=uuid4(),
            provider="test",
            model_name="test",
            fallback_used=failure == "fallback" and template == "reader_issue_segment_writer",
            finish_reason="length"
            if (failure == "length" and template == "reader_issue_segment_writer")
            or (failure == "compression_length" and template == "reader_issue_segment_compress")
            else "stop",
        )

    monkeypatch.setattr(revision, "_load_current_chapter_draft", load)
    monkeypatch.setattr(revision, "_issue_context", context)
    monkeypatch.setattr(revision, "complete_text", complete)
    session = Session(task, current, latest=uuid4() if failure == "stale" else None)
    result = await revision.apply_chapter_revision_task(
        session, load_settings(), project.slug, 4, rewrite_task_id=task.id, operation="issue"
    )
    assert len(requests) <= 8
    assert result["ok"] is (failure in {None, "compressed"})
    review = task.metadata_json["candidate_review"]
    assert review["source_draft_id"] == str(current.id)
    assert review["plan_llm_run_id"]
    if failure not in {None, "compressed"}:
        assert not session.added and chapter.current_word_count == 6047
        assert review["status"] == "rejected"
        if failure == "second_segment":
            assert review["content_md"] and len(review["segments"]) == 2
            assert review["segments"][0]["llm_run_id"]
        if failure in {"rejected", "stale"}:
            assert review["content_md"] and review["partial"] is False
        if failure in {"length", "fallback"}:
            assert review["segments"][0]["raw_content"]
    else:
        assert len(requests) == (6 if failure == "compressed" else 4) and len(session.added) == 1
        assert chapter.current_word_count == 3000
        assert sum(review["budgets"]) == 3000
        assert review["partial"] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["segments"].pop(),
        lambda p: p["segments"][0].update(goal=""),
    ],
)
def test_plan_fails_closed_on_missing_structure(mutate):
    value = plan()
    mutate(value)
    with pytest.raises(ValueError):
        revision._parse_segment_plan(json.dumps(value), (1000, 1000, 1000), 2)


def test_plan_must_copy_previous_opening_anchor_and_preserve_seams():
    value = plan()
    anchor = "前章最后打开铁门。"
    value["segments"][0]["entry"] = "三日后，审讯开始。"
    revision._parse_segment_plan(json.dumps(value), (1000, 1000, 1000), 2, anchor)
    value = plan()
    value["segments"][1]["entry"] = "新地点"
    revision._parse_segment_plan(json.dumps(value), (1000, 1000, 1000), 2)


def test_plan_does_not_assign_same_requirement_to_every_segment():
    value = plan()
    value["segments"][2]["requirement_indexes"] = [0]
    with pytest.raises(ValueError, match="duplicate requirement ownership"):
        revision._parse_segment_plan(json.dumps(value), (1000, 1000, 1000), 2)


def test_bare_segments_with_verified_opening_anchor_normalize_without_invented_facts():
    anchor = "前章人物推开铁门。"
    segments = plan()["segments"]
    segments[0]["entry"] = anchor
    result = revision._parse_segment_plan(json.dumps(segments), (1000, 1000, 1000), 2, anchor)
    assert result["prior_facts"] == [anchor]
    assert result["editable_events"] == []
    assert result["normalization"]["facts_source"] == "caller_opening_anchor"
    assert result["segments"] == segments
    with pytest.raises(ValueError, match="invalid plan object"):
        revision._parse_segment_plan(json.dumps(segments), (1000, 1000, 1000), 2)
    segments[0]["budget"] = 900
    with pytest.raises(ValueError, match="budget mismatch"):
        revision._parse_segment_plan(json.dumps(segments), (1000, 1000, 1000), 2, anchor)
