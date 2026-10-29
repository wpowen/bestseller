"""Consumer RED matrix; transport fixtures make no native paid-quality claim."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from bestseller import settings as settings_module
from bestseller.services import chapter_writer_delivery as delivery
from bestseller.services import draft_continuity_supplement as supplement
from bestseller.services import drafts
from bestseller.services.chapter_writer_sources import verify_reader_first_delivery

from .conftest import build_block, rereview

pytestmark = pytest.mark.unit


@pytest.fixture
def consumer_settings(writer_settings, monkeypatch):
    monkeypatch.setattr(settings_module, "load_settings", lambda *a, **kw: writer_settings)
    monkeypatch.setattr(drafts, "load_settings", lambda *a, **kw: writer_settings)
    monkeypatch.setattr(drafts, "get_settings", lambda: writer_settings)
    return writer_settings


async def consume(case, block, consumer, settings, monkeypatch):
    if consumer == "writer":
        return delivery.reader_first_writer_blocks(
            case.project,
            case.chapter,
            [block],
            language=case.project.language,
        )
    if consumer == "supplement":
        # Only the transport boundary is isolated; native SQLite tests are separate.
        async def selected_blocks(*args, **kwargs):
            return [block]

        async def scenes(*args, **kwargs):
            return []

        monkeypatch.setattr(delivery, "load_delivery_blocks", selected_blocks)
        return await supplement.applicable_plan(
            SimpleNamespace(scalars=scenes),
            settings,
            project=case.project,
            chapter=case.chapter,
        )
    from test_chapter_first_prompt_compiler_integration import _inputs

    project, chapter, scene, packet = _inputs(case.project.language, case.chapter.chapter_number)
    project.id = case.project.id
    project.target_chapters = case.project.target_chapters
    project.target_word_count = project.target_chapters * 2600
    project.metadata_json = deepcopy(case.project.metadata_json)
    chapter.id = case.chapter.id
    chapter.project_id = project.id
    scene.project_id, scene.chapter_id = project.id, chapter.id
    packet.story_bible = {}
    packet.previous_scene_summaries = []
    packet.retrieval_chunks = []
    if consumer == "scene":
        return drafts.build_scene_draft_prompts(
            project,
            chapter,
            scene,
            None,
            prompt_mode="compiled",
            chapter_contract=packet.chapter_contract,
            compiler_additional_blocks=(block,),
            total_input_budget_tokens=30000,
        )
    assert consumer == "chapter"
    return drafts.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        packet,
        target_word_count=2600,
        prompt_mode="compiled",
        compiler_additional_blocks=(block,),
        total_input_budget_tokens=30000,
    )


@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("consumer", ["writer", "scene", "chapter", "supplement"])
@pytest.mark.parametrize(
    "fault",
    [
        "prefix",
        "duplicate_chapter",
        "scene_gap",
        "role_missing",
        "role_unknown",
        "other_binding",
    ],
)
async def test_all_v3_consumers_recheck_full_census_after_rehashed_review(
    full_case,
    consumer_settings,
    monkeypatch,
    language,
    consumer,
    fault,
):
    case = full_case(count=12, number=7, language=language)
    valid = build_block(case)
    await consume(case, valid, consumer, consumer_settings, monkeypatch)
    chapters = case.artifact.content["chapters"]
    if fault == "prefix":
        chapters.pop()
    elif fault == "duplicate_chapter":
        chapters[-1]["chapter_number"] = 11
    elif fault == "scene_gap":
        chapters[-1]["scenes"][0]["scene_number"] = 2
    elif fault == "role_missing":
        chapters[-1]["_meta"]["reader_first_field_roles"].pop("/goal")
    elif fault == "role_unknown":
        chapters[-1]["_meta"]["reader_first_field_roles"]["/goal"] = "ignored"
    else:
        chapters[-1]["_meta"]["reader_first_planning"]["user_input_sha256"] = "0" * 64
    # The selected chapter and a freshly rehashed synthetic raw review remain valid.
    rereview(case)
    invalid = build_block(case)
    with pytest.raises(ValueError, match="reader_first"):
        verify_reader_first_delivery(project=case.project, chapter=case.chapter, block=invalid)
    with pytest.raises(ValueError, match="reader_first"):
        await consume(case, invalid, consumer, consumer_settings, monkeypatch)
