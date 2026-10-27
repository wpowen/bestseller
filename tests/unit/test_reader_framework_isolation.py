"""Exercise project and temporal boundaries without provider calls or book writes."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.chapter_revision import _issue_context


@pytest.mark.asyncio
@pytest.mark.parametrize("chapter_number", [1, 4, 20])
async def test_interleaved_books_keep_future_events_out_of_prior_facts(chapter_number):
    projects = [
        SimpleNamespace(
            id=uuid4(),
            metadata_json={"concept_seed": seed},
            invariants_json={"rule": rule},
            reader_contract_json={},
        )
        for seed, rule in [
            ("修复海底观测站", "设备遵守物理规律"),
            ("经营乡村面包坊", "人物没有超自然能力"),
        ]
    ]
    stored = {}
    for index, project in enumerate(projects):
        rows = []
        for number in [chapter_number - 1, chapter_number + 1]:
            if 1 <= number <= 20:
                rows.append(
                    (
                        number,
                        SimpleNamespace(
                            id=uuid4(),
                            version_no=1,
                            content_md=f"book-{index}-chapter-{number}-unique-event",
                        ),
                    )
                )
        stored[project.id] = rows

    class Session:
        async def execute(self, statement):
            # Resolve records from the actual SQL predicate, not from a caller
            # selected fixture. Dropping project_id makes this check fail.
            parameters = statement.compile().params
            matches = [key for key in stored if key in parameters.values()]
            assert len(matches) == 1
            await asyncio.sleep(0)
            return SimpleNamespace(all=lambda: stored[matches[0]])

    contexts = await asyncio.gather(
        *[
            _issue_context(
                Session(),
                project,
                SimpleNamespace(
                    chapter_number=chapter_number,
                    chapter_goal="推进当前事件",
                ),
            )
            for project in projects
        ]
    )
    for index, context in enumerate(contexts):
        assert f"book-{1 - index}-" not in str(context)
        assert "adjacent_chapters" not in context
        prior = context["prior_facts_context"]
        following = context["following_continuity_constraints"]
        assert (prior is None) == (chapter_number == 1)
        assert (following is None) == (chapter_number == 20)
        if following:
            assert following["status"] == "future_continuity_only"
            assert following["excerpt"] not in str(prior)
