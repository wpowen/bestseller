"""Story-enhancer → PROSE wiring.

User feedback (this session): selected story enhancers (脑洞/喜剧/爽点) reached the
OUTLINE but never the prose — the writer only knew the genre label, and the
chapter LLM's cashed beats (brainhole_contract / selected_effect_skills) were
dropped at persistence. These tests pin the three repairs:

  A. ``render_story_enhancer_writer_block`` — book-level mandate + this chapter's
     planned cashing, soft/advisory.
  B. ``workflows._sync_chapter_causality_metadata`` persists the cashed fields
     into ``chapter.metadata_json`` (so they survive to the writer).
  C. ``drafts.build_scene_draft_prompts`` injects the block — and stays
     byte-identical when the book opted into nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.services.story_enhancers import (
    STORY_ENHANCERS_METADATA_KEY,
    render_story_enhancer_writer_block,
)

# ── A. writer-block renderer ──────────────────────────────────────────────────


def test_writer_block_empty_when_nothing_opted_in() -> None:
    assert render_story_enhancer_writer_block(None, None) == ""
    assert render_story_enhancer_writer_block({}, {}) == ""
    # An unrelated metadata payload must not synthesise a block.
    assert render_story_enhancer_writer_block({"target_platform": "番茄"}, None) == ""


def test_writer_block_carries_book_level_contract() -> None:
    meta = {
        STORY_ENHANCERS_METADATA_KEY: {
            "brainhole": True,
            "effect_skills": ["comedy_engine"],
        }
    }
    block = render_story_enhancer_writer_block(meta, None)
    assert block
    # Book-level hard contract header + the comedy tone-anchor floor reach prose.
    assert "故事增强" in block
    assert "喜剧" in block
    assert "脑洞" in block


def test_writer_block_carries_chapter_cashed_brainhole() -> None:
    chapter_meta = {
        "brainhole_contract": {
            "one_sentence_sell": "招来雷震子当外卖骑手",
            "visible_comedy": "雷公嘴叼着电动车钥匙找不到充电桩",
        }
    }
    block = render_story_enhancer_writer_block(None, chapter_meta)
    assert "本章已规划的脑洞兑现点" in block
    assert "招来雷震子当外卖骑手" in block
    # Field keys are rendered with human labels, not raw snake_case.
    assert "一句话卖点" in block
    assert "可见喜剧落点" in block


def test_writer_block_renders_selected_effect_expected_contracts() -> None:
    chapter_meta = {
        "selected_effect_skills": {
            "primary": "comedy_engine",
            "secondary": "hype_satisfaction_engine",
            "expected_contracts": {
                "comic_effect_contract": "神仙用现代规则报错引发连锁笑点",
            },
        }
    }
    block = render_story_enhancer_writer_block(None, chapter_meta)
    assert "本章主推的故事效果兑现点" in block
    assert "主效果" in block and "comedy_engine" in block
    assert "comic_effect_contract" in block
    assert "神仙用现代规则报错引发连锁笑点" in block


def test_writer_block_handles_list_expected_contracts() -> None:
    chapter_meta = {
        "selected_effect_skills": {
            "primary": "brainhole_engine",
            "expected_contracts": ["brainhole_contract", "comic_effect_contract"],
        }
    }
    block = render_story_enhancer_writer_block(None, chapter_meta)
    assert "需兑现合同" in block
    assert "brainhole_contract" in block


def test_writer_block_caps_long_field_text() -> None:
    long_text = "炸" * 1000
    chapter_meta = {"brainhole_contract": {"one_sentence_sell": long_text}}
    block = render_story_enhancer_writer_block(None, chapter_meta)
    # Per-field cap (240) prevents a verbose contract from blowing up the prompt.
    assert block.count("炸") <= 240


# ── B. persistence: cashed fields survive into chapter.metadata_json ───────────


def _sync(chapter_outline: SimpleNamespace, *, existing: dict | None = None) -> dict:
    from bestseller.services.workflows import _sync_chapter_causality_metadata

    chapter = SimpleNamespace(metadata_json=dict(existing or {}))
    _sync_chapter_causality_metadata(chapter, chapter_outline, None)
    return chapter.metadata_json


def test_sync_persists_effect_fields_into_chapter_metadata() -> None:
    outline = SimpleNamespace(
        brainhole_contract={"one_sentence_sell": "招神改写现实"},
        selected_effect_skills={"primary": "comedy_engine"},
    )
    meta = _sync(outline)
    assert meta["brainhole_contract"] == {"one_sentence_sell": "招神改写现实"}
    assert meta["selected_effect_skills"] == {"primary": "comedy_engine"}


def test_sync_pops_stale_effect_fields_when_absent() -> None:
    # A re-sync where the new outline carries no enhancer fields must not leave
    # a previous chapter's stale contract behind.
    outline = SimpleNamespace(brainhole_contract={}, selected_effect_skills={})
    meta = _sync(
        outline,
        existing={
            "brainhole_contract": {"one_sentence_sell": "旧的"},
            "selected_effect_skills": {"primary": "twist_reversal_engine"},
        },
    )
    assert "brainhole_contract" not in meta
    assert "selected_effect_skills" not in meta


# ── C. prose-prompt injection (end to end through build_scene_draft_prompts) ───


def _scene_fixtures(*, project_meta: dict | None, chapter_meta: dict | None):
    project = SimpleNamespace(
        title="神仙都是我招的",
        slug="zhaoshen-hr",
        genre="urban-power-reversal",
        metadata_json=project_meta,
    )
    chapter = SimpleNamespace(
        chapter_number=1,
        chapter_goal="入职第一天就要给雷震子排班",
        title="入职",
        metadata_json=chapter_meta,
    )
    scene = SimpleNamespace(
        scene_number=1,
        title="排班",
        participants=["陈default"],
        purpose={"story": "抛出招神任务", "emotion": "荒诞"},
        time_label="上午",
        entry_state={},
        exit_state={},
        scene_type="setup",
        target_word_count=1000,
    )
    return project, chapter, scene


def _user_prompt(*, project_meta=None, chapter_meta=None) -> str:
    from bestseller.services.drafts import build_scene_draft_prompts

    project, chapter, scene = _scene_fixtures(project_meta=project_meta, chapter_meta=chapter_meta)
    _, user_prompt = build_scene_draft_prompts(project, chapter, scene, None)
    return user_prompt


def test_prose_prompt_injects_enhancer_when_opted_in() -> None:
    prompt = _user_prompt(
        project_meta={
            STORY_ENHANCERS_METADATA_KEY: {
                "brainhole": True,
                "effect_skills": ["comedy_engine"],
            }
        },
        chapter_meta={"brainhole_contract": {"one_sentence_sell": "招来雷震子当骑手"}},
    )
    # Book-level mandate AND this chapter's cashed beat both reach the writer.
    assert "故事增强" in prompt
    assert "招来雷震子当骑手" in prompt


def test_prose_prompt_byte_identical_when_not_opted_in() -> None:
    # No story_enhancers selected + no cashed chapter contract → the injected
    # line must be empty, leaving the prompt identical to the legacy path.
    without = _user_prompt(project_meta={}, chapter_meta={})
    legacy = _user_prompt(project_meta=None, chapter_meta=None)
    assert without == legacy
    assert "故事增强" not in without
