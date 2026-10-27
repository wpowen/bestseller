from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.project import InteractiveFictionConfig, _IF_ALL_VALID_GENRES

pytestmark = pytest.mark.unit


def test_disabled_interactive_config_has_no_genre_or_tone_seed():
    cfg = InteractiveFictionConfig()
    assert not cfg.enabled and cfg.if_genre == '' and cfg.tone == ''
    with pytest.raises(ValueError, match='Select if_genre explicitly'):
        InteractiveFictionConfig(enabled=True)


@pytest.mark.parametrize('genre', sorted(_IF_ALL_VALID_GENRES))
def test_all_explicit_interactive_choices_and_existing_tone_are_preserved(genre):
    cfg = InteractiveFictionConfig(enabled=True, if_genre=genre, tone='克制、幽默')
    assert cfg.if_genre == genre and cfg.tone == '克制、幽默'


@pytest.mark.asyncio
async def test_archived_outbox_never_dispatches_any_recovery_schema(monkeypatch):
    from bestseller.services import autonomous_dispatch, book_run, pending_manuscript_continuation
    project = SimpleNamespace(status='archived', metadata_json={'library_archived': True})
    rows = [SimpleNamespace(id=uuid4(), project_id=uuid4(), status='awaiting_authority',
                            metadata_json={'schema': schema})
            for schema in ('source-bound-book-run.v1', 'pending-manuscript-continuation.v1', 'other')]
    class Session:
        async def scalars(self, query):
            return rows
        async def get(self, model, identity):
            return project
    async def forbidden(*args, **kwargs):
        pytest.fail('Archived validation may not enqueue or send')
    monkeypatch.setattr(book_run, 'enqueue', forbidden)
    monkeypatch.setattr(pending_manuscript_continuation, 'enqueue', forbidden)
    monkeypatch.setattr(autonomous_dispatch, 'enqueue_checkpoint', forbidden)
    result = await autonomous_dispatch.recover_pending_checkpoints(Session(), object())
    assert len(result) == 3 and all(item['dispatched'] is False for item in result)
