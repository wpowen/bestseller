from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services import pipelines


@pytest.mark.parametrize(
    "metadata,has_body,allowed",
    [
        ({"blocked_by_fanqie_long_ranking_gate": True}, True, True),
        ({"blocked_by_fanqie_long_ranking_gate": True}, False, False),
        (
            {
                "chapter_quality_debt": True,
                "chapter_quality_debt_reason": "chapter_promotion_ineligible",
            },
            True,
            False,
        ),
        (
            {"blocked_by_fanqie_long_ranking_gate": True, "blocked_by_unknown_gate": True},
            True,
            False,
        ),
        ({"blocked_by_material_referential_integrity_gate": True}, True, False),
    ],
)
async def test_three_sequence_consumers_agree_on_current_local_debt(metadata, has_body, allowed):
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=1,
        status="revision",
        production_state="quality_debt",
        metadata_json=metadata,
    )
    result = SimpleNamespace(requires_human_review=True, chapter_draft_id=uuid4())
    settings = SimpleNamespace(quality=SimpleNamespace(draft_mode=False))
    assert (
        pipelines._chapter_requires_sequence_stop(
            chapter, result, settings, has_current_draft=has_body
        )
        is not allowed
    )
    session = SimpleNamespace(scalars=AsyncMock(return_value=[chapter.id] if has_body else []))
    pending, _ = await pipelines._select_pending_chapters_for_resume(
        session, [chapter], resume_enabled=True, accept_on_stall=False
    )
    assert (pending == []) is allowed
    query = str(session.scalars.call_args.args[0])
    assert "is_current IS true" in query
    assert "regexp_replace" in query
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [(1, "revision", "quality_debt", metadata, int(has_body))]
            )
        )
    )
    incomplete = await pipelines._load_prior_incomplete_chapter_numbers(
        session, project_id=uuid4(), before_chapter_number=2
    )
    assert (incomplete == []) is allowed
    assert "regexp_replace" in str(session.execute.call_args.args[0])


def test_draft_id_alone_cannot_certify_current_body():
    chapter = SimpleNamespace(
        status="revision",
        production_state="quality_debt",
        metadata_json={"blocked_by_fanqie_long_ranking_gate": True},
    )
    result = SimpleNamespace(requires_human_review=True, chapter_draft_id=uuid4())
    settings = SimpleNamespace(quality=SimpleNamespace(draft_mode=False))
    assert pipelines._chapter_requires_sequence_stop(chapter, result, settings)


@pytest.mark.parametrize("current_match", [False, True])
async def test_project_loop_binds_local_debt_to_result_current_nonempty_body(current_match):
    import inspect
    import textwrap

    source = inspect.getsource(pipelines.run_project_pipeline)
    start = source.index("            _numeric_debt_bound = await")
    end = source.index(
        "            if chapter_result.requires_human_review or _chapter_sequence_stop:", start
    )
    branch = textwrap.dedent(source[start:end])
    namespace = dict(vars(pipelines))
    exec(
        "async def probe(session, chapter, chapter_result, settings):\n"
        + textwrap.indent(branch, "    ")
        + "\n    return _chapter_sequence_stop\n",
        namespace,
    )
    chapter = SimpleNamespace(
        id=uuid4(),
        status="revision",
        production_state="quality_debt",
        metadata_json={"blocked_by_fanqie_long_ranking_gate": True},
    )
    draft_id = uuid4()
    result = SimpleNamespace(chapter_draft_id=draft_id, requires_human_review=True)
    session = SimpleNamespace(scalar=AsyncMock(return_value=draft_id if current_match else None))
    settings = SimpleNamespace(quality=SimpleNamespace(draft_mode=False))
    assert await namespace["probe"](session, chapter, result, settings) is not current_match
    query = session.scalar.call_args.args[0]
    sql = str(query)
    assert "is_current IS true" in sql and "regexp_replace" in sql
    params = query.compile().params
    assert draft_id in params.values() and chapter.id in params.values()
