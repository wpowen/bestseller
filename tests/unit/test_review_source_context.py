import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_adopted_knowledge_lineage import db as db

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services.prompt_compiler import PromptCompilerError
from bestseller.services.review_source_context import (
    load_review_source_context,
    prepare_source_complete_prompt,
)


def test_required_source_endings_and_task_survive_whole_compilation():
    ending = "门已从内锁死。信件已经销毁。"
    prior = "旅人等了很久。\n\n" * 350 + ending
    ctx = {
        "adopted_continuity": {"recent_adopted_prose": [{"chapter_number": 1, "body": prior}]},
        "world_rules": {"rule": "The bridge is closed."},
        "fact": "No new deadline.",
    }
    sys, user, receipt = prepare_source_complete_prompt(
        system_prompt="Editor protocol.",
        user_prompt="Repair only cited paragraph.",
        source_context=ctx,
        output_tokens=16000,
    )
    assert json.dumps(prior, ensure_ascii=False) in user
    assert ending in user and "Repair only cited paragraph." in user
    assert "Never replay or undo an occurred event" in sys
    assert receipt["source_complete"] and receipt["compiler"]["required_complete"]
    assert not receipt["compiler"]["truncated"] and not receipt["compiler"]["dropped"]


def test_oversized_required_source_stops_instead_of_hiding_tail_or_paying():
    with pytest.raises(PromptCompilerError):
        prepare_source_complete_prompt(
            system_prompt="Protocol.",
            user_prompt="Task.",
            source_context={"adopted": "不可丢弃的历史。" * 35000},
            output_tokens=16000,
        )


@pytest.mark.asyncio
async def test_real_adopted_selector_excludes_other_book_current_candidate_and_future(db):
    session, project, first, accepted = db
    accepted.content_md = "门已经锁死。信已销毁。"
    accepted.is_current = False
    other = ProjectModel(
        slug=uuid4().hex,
        title="another",
        genre="science fiction",
        target_word_count=6000,
        target_chapters=3,
    )
    session.add(other)
    await session.flush()
    chapter = ChapterModel(
        project_id=project.id, chapter_number=2, chapter_goal="处理后果", target_word_count=2000
    )
    foreign = ChapterModel(project_id=other.id, chapter_number=1, chapter_goal="other")
    session.add_all([chapter, foreign])
    await session.flush()
    candidate = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=first.id,
        version_no=2,
        content_md="未采用候选暗示又开门。",
        promotion_state="candidate",
        is_current=True,
    )
    session.add_all(
        [
            candidate,
            ChapterDraftVersionModel(
                project_id=other.id,
                chapter_id=foreign.id,
                version_no=1,
                content_md="外书不可读到的飞船。",
                promotion_state="promoted",
                is_current=True,
            ),
        ]
    )
    await session.commit()
    source = await load_review_source_context(
        session,
        project=project,
        chapter=chapter,
        scenes=[],
        context_packet=SimpleNamespace(),
        content_md="守门人处理余烬。",
    )
    history = source["adopted_continuity"]
    assert len(history["recent_adopted_prose"]) == 1
    assert history["recent_adopted_prose"][0]["body"] == accepted.content_md
    assert history["recent_adopted_prose"][0]["draft_id"] == str(accepted.id)
    assert "未采用候选" not in str(source) and "飞船" not in str(source)
    assert not session.dirty
    chapter.chapter_number = 3
    await session.flush()
    with pytest.raises(ValueError, match="prior_chapter_gap"):
        await load_review_source_context(
            session,
            project=project,
            chapter=chapter,
            scenes=[],
            context_packet=SimpleNamespace(),
            content_md="后续稿",
        )
