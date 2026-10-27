from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import conception_checkpoints as checkpoints, conception, concept_tournament
from bestseller.services.budget import ExecutionBudgetError
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def record():
    return checkpoints.build_replay_context({"genre": "科幻", "avoid_names": ["retained"],
        "_market_competitor_rows": [{"title": "frozen competitor"}],
        "user_hints": {"production_profile": "complete"}},
        input_hash="original-input", genre_key="apocalypse-supply", chapter_count=4)


def test_context_hash_and_input_target_are_strict_and_return_is_detached():
    saved = record()
    restored = checkpoints.validate_replay_context(saved, input_hash="original-input", chapter_count=4)
    restored["avoid_names"].append("new")
    assert saved["context"]["avoid_names"] == ["retained"]
    for mutation in ({"input_hash": "different"}, {"chapter_count": 5}, {"context_sha256": "wrong"}):
        corrupted = {**saved, **mutation}
        with pytest.raises(ValueError):
            checkpoints.validate_replay_context(corrupted, input_hash="original-input", chapter_count=4)
    corrupted = deepcopy(saved)
    corrupted["context"]["avoid_names"] = ["rerolled"]
    with pytest.raises(ValueError):
        checkpoints.validate_replay_context(corrupted, input_hash="original-input")


@pytest.mark.asyncio
async def test_save_preserves_original_metadata_and_refuses_overwrite_or_wrong_owner():
    run = SimpleNamespace(workflow_type="conception_initial", project_id=None,
        metadata_json={"keep": {"audit": "original"}, "conception_checkpoint_input_hash": "original-input"})

    class Session:
        async def scalar(self, statement):
            return run
        async def flush(self):
            pass
    session = Session()
    await checkpoints.save_replay_context(session, workflow_run_id=uuid4(), record=record())
    assert run.metadata_json["keep"] == {"audit": "original"}
    loaded = await checkpoints.load_replay_context(session, workflow_run_id=uuid4(), input_hash="original-input")
    assert loaded == record()
    changed = checkpoints.build_replay_context({"different": "context"}, input_hash="original-input",
        genre_key="apocalypse-supply", chapter_count=4)
    with pytest.raises(ValueError, match="already frozen"):
        await checkpoints.save_replay_context(session, workflow_run_id=uuid4(), record=changed)
    run.project_id = uuid4()
    with pytest.raises(ValueError, match="project-less"):
        await checkpoints.load_replay_context(session, workflow_run_id=uuid4(), input_hash="original-input")


@pytest.mark.asyncio
async def test_pipeline_resume_keeps_dynamic_inputs_and_does_not_recompute_them(monkeypatch):
    saved = record()
    observed = []

    async def forbidden(*args, **kwargs):
        raise AssertionError("dynamic context was recalculated during resume")

    async def tournament(*args, **kwargs):
        observed.append(kwargs)
        # A real unknown outcome remains a hard boundary; this fixture never
        # calls a provider and does not fabricate a winner or approval.
        raise ExecutionBudgetError("execution_result_unknown")

    for name in ("_attach_concept_methodology", "_recent_cast_names", "_attach_mechanism_dedup", "_prefetch_market_competitors"):
        monkeypatch.setattr(conception, name, forbidden)
    monkeypatch.setattr(concept_tournament, "run_concept_tournament", tournament)
    with pytest.raises(ExecutionBudgetError, match="execution_result_unknown"):
        await conception.run_conception_pipeline(None, load_settings(env={}),
            genre_key="apocalypse-supply", chapter_count=4,
            user_hints={"production_profile": "complete"},
            resume_replay_context=saved, checkpoint_input_hash="original-input")
    assert len(observed) == 1
    assert observed[0]["market_competitors"] == ({"title": "frozen competitor"},)
    assert observed[0]["config"]["n_candidates"] == 2
