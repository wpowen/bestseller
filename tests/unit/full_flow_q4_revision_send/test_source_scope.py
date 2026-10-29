"""Only ID propagation; none of these scopes grant send/adoption permission."""

import asyncio
from functools import partial
import inspect
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def identities():
    return dict(zip(("project_id", "chapter_id", "source_draft_id"), (uuid4() for _ in range(3))))


def selector(ids):
    return {key: str(ids[key]) for key in ("chapter_id", "source_draft_id")}


async def test_scope_entry_empty_finally_restores_and_preserves_signature(scope_api):
    ids = identities()

    @scope_api.revision_source_scope
    async def inner(value, *, fail=False):
        assert scope_api.current_revision_source(ids["project_id"]) is None
        scope_api.set_revision_source(**identities())
        if fail:
            raise LookupError("ordinary failure")
        return value

    @scope_api.revision_source_scope
    async def outer():
        scope_api.set_revision_source(**ids)
        assert await inner(7) == 7
        assert scope_api.current_revision_source(ids["project_id"]) == selector(ids)
        with pytest.raises(LookupError):
            await inner(8, fail=True)
        assert scope_api.current_revision_source(ids["project_id"]) == selector(ids)

    assert inner.__name__ == "inner"
    assert str(inspect.signature(inner)) == "(value, *, fail=False)"
    await outer()
    assert scope_api.current_revision_source(ids["project_id"]) is None


async def test_nested_same_task_helper_reads_detached_selector_for_matching_project(scope_api):
    ids = identities()

    async def helper():
        found = scope_api.current_revision_source(str(ids["project_id"]))
        assert found == selector(ids)
        found["chapter_id"] = str(uuid4())
        assert scope_api.current_revision_source(ids["project_id"]) == selector(ids)
        assert scope_api.current_revision_source(uuid4()) is None

    @scope_api.revision_source_scope
    async def parent():
        scope_api.set_revision_source(**{key: str(value) for key, value in ids.items()})
        await helper()

    await parent()


async def test_setter_rejects_outside_decorated_scope(scope_api):
    with pytest.raises(scope_api.AutomaticRevisionBudgetError, match="source_scope_missing"):
        scope_api.set_revision_source(**identities())


async def test_copied_child_task_cannot_read_or_mutate_parent_scope(scope_api):
    ids = identities()

    async def child():
        assert scope_api.current_revision_source(ids["project_id"]) is None
        with pytest.raises(scope_api.AutomaticRevisionBudgetError,
                           match="source_scope_task_mismatch"):
            scope_api.set_revision_source(**ids)

    @scope_api.revision_source_scope
    async def parent():
        scope_api.set_revision_source(**ids)
        await asyncio.create_task(child())
        assert scope_api.current_revision_source(ids["project_id"]) == selector(ids)

    await parent()


async def test_child_with_new_explicit_scope_is_isolated_from_parent(scope_api):
    outer_ids, inner_ids = identities(), identities()

    @scope_api.revision_source_scope
    async def child():
        assert scope_api.current_revision_source(outer_ids["project_id"]) is None
        scope_api.set_revision_source(**inner_ids)
        await asyncio.sleep(0)
        assert scope_api.current_revision_source(inner_ids["project_id"]) == selector(inner_ids)

    @scope_api.revision_source_scope
    async def parent():
        scope_api.set_revision_source(**outer_ids)
        await asyncio.create_task(child())
        assert scope_api.current_revision_source(outer_ids["project_id"]) == selector(outer_ids)

    await parent()


async def test_concurrent_tasks_and_cancellation_restore_scopes(scope_api):
    seen = []

    @scope_api.revision_source_scope
    async def worker(ids):
        scope_api.set_revision_source(**ids)
        await asyncio.sleep(0)
        seen.append(scope_api.current_revision_source(ids["project_id"]))
        raise asyncio.CancelledError

    left, right = identities(), identities()
    results = await asyncio.gather(worker(left), worker(right), return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert seen == [selector(left), selector(right)]
    assert scope_api.current_revision_source(left["project_id"]) is None


@pytest.mark.parametrize("field", ["project_id", "chapter_id", "source_draft_id"])
@pytest.mark.parametrize("bad", [None, True, 1, "", "not-a-native-id", {"approved": True}])
async def test_setter_rejects_non_native_ids_without_changing_scope(scope_api, field, bad):
    ids = identities()

    @scope_api.revision_source_scope
    async def parent():
        scope_api.set_revision_source(**ids)
        with pytest.raises(scope_api.AutomaticRevisionBudgetError):
            scope_api.set_revision_source(**{**ids, field: bad})
        assert scope_api.current_revision_source(ids["project_id"]) == selector(ids)

    await parent()


async def test_scope_api_has_no_authority_event_or_session_arguments(scope_api):
    expected = {"project_id", "chapter_id", "source_draft_id"}
    params = inspect.signature(scope_api.set_revision_source).parameters
    assert set(params) == expected
    assert all(param.kind == inspect.Parameter.KEYWORD_ONLY for param in params.values())
    assert list(inspect.signature(scope_api.current_revision_source).parameters) == ["project_id"]
    ids = identities()

    @scope_api.revision_source_scope
    async def parent():
        for name in ("event_id", "approved", "session", "operation_key", "source_body"):
            with pytest.raises(TypeError):
                partial(scope_api.set_revision_source, **ids)(**{name: "forbidden"})

    await parent()
