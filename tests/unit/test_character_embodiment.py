"""Unit tests for the character-embodiment (单人入戏) prose lever."""

# ruff: noqa: RUF001

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.character_embodiment import (
    generate_scene_embodiment,
    resolve_protagonist,
    resolve_situation,
)
from bestseller.services.methodology_compiler import (
    MethodologyStage,
    compile_methodology,
)
from bestseller.services.quality_levers.character_embodiment import (
    EMBODIMENT_KEY,
    build_embodiment_prompt,
    extract_embodiment,
    render_embodiment_block,
)


# ── pure lever: extract / render ───────────────────────────────────────────
def test_extract_embodiment_present_and_absent() -> None:
    assert extract_embodiment({EMBODIMENT_KEY: "  我盯着那扇门  "}) == "我盯着那扇门"
    assert extract_embodiment({}) == ""
    assert extract_embodiment(None) == ""
    assert extract_embodiment({EMBODIMENT_KEY: 123}) == ""


def test_render_embodiment_block_verbatim_and_empty() -> None:
    out = render_embodiment_block("我手心全是汗，钥匙攥得发烫。")
    assert "我手心全是汗，钥匙攥得发烫。" in out  # verbatim, not summarized
    assert "真实内心" in out
    assert render_embodiment_block("") == ""
    assert render_embodiment_block("   ") == ""


def test_render_embodiment_block_truncates_overlong() -> None:
    long = "字" * 5000
    out = render_embodiment_block(long)
    assert len(out) < 2000
    assert out.endswith("————")


def test_build_embodiment_prompt_shape() -> None:
    system, user = build_embodiment_prompt(
        protagonist="陆沉", situation="母亲住院要补三万押金", genre="都市异能"
    )
    assert "就是" in system and "第一人称" in system
    assert "大白话" in system and "状态变量" in system  # mechanism→concrete instruction
    assert "陆沉" in user and "母亲住院" in user
    assert "只输出我的内心" in user


# ── situation / protagonist resolution ─────────────────────────────────────
def test_resolve_situation_assembles_from_scene() -> None:
    chapter = SimpleNamespace(chapter_goal="主角第一次借运救母", chapter_number=3)
    scene = SimpleNamespace(
        title="医院走廊的决定",
        purpose={"story": "主角必须当场决定借不借运凑押金"},
        entry_state={"mood": "焦灼", "money": "只剩四千"},
        key_dialogue_beats=["催缴", "拒绝", "妥协"],
        metadata_json={
            "methodology_contract": {
                "conflict_stakes": "借则债翻番，不借则母亲停药",
                "cut_point": "他伸手的瞬间",
            }
        },
    )
    situation = resolve_situation(chapter, scene)
    assert "借运救母" in situation
    assert "决定" in situation
    assert "债翻番" in situation


def test_resolve_situation_too_thin_returns_empty() -> None:
    chapter = SimpleNamespace(chapter_goal="", chapter_number=1)
    scene = SimpleNamespace(
        title="", purpose={}, entry_state={}, key_dialogue_beats=[], metadata_json={}
    )
    assert resolve_situation(chapter, scene) == ""


def test_resolve_protagonist_prefers_spotlight_then_cast() -> None:
    scene = SimpleNamespace(
        participants=["陆沉", "马军"], metadata_json={"spotlight_character": "陆沉"}
    )
    story_bible = {
        "cast_spec": {
            "characters": [
                {"name": "陆沉", "role": "主角", "background": "县城电工"},
                {"name": "马军", "role": "路人"},
            ]
        }
    }
    name, persona = resolve_protagonist(story_bible, scene)
    assert name == "陆沉"
    assert "电工" in persona


# ── async service: gating / language / happy path ──────────────────────────
def _scene():
    return SimpleNamespace(
        title="走廊决定",
        purpose={"story": "决定借不借运凑押金"},
        entry_state={"money": "只剩四千"},
        key_dialogue_beats=["催缴"],
        participants=["陆沉"],
        metadata_json={"methodology_contract": {"conflict_stakes": "借则债翻番"}},
    )


@pytest.mark.asyncio
async def test_generate_disabled_flag_is_noop() -> None:
    settings = SimpleNamespace(pipeline=SimpleNamespace(enable_character_embodiment=False))
    project = SimpleNamespace(language="zh-CN", genre="都市异能", slug="x")
    out = await generate_scene_embodiment(
        None, settings, project=project,
        chapter=SimpleNamespace(chapter_goal="g", chapter_number=1),
        scene=_scene(), story_bible={},
    )
    assert out == ""


@pytest.mark.asyncio
async def test_generate_english_book_is_noop() -> None:
    settings = SimpleNamespace(pipeline=SimpleNamespace(enable_character_embodiment=True))
    project = SimpleNamespace(language="en", genre="urban", slug="x")
    out = await generate_scene_embodiment(
        None, settings, project=project,
        chapter=SimpleNamespace(chapter_goal="g", chapter_number=1),
        scene=_scene(), story_bible={},
    )
    assert out == ""


@pytest.mark.asyncio
async def test_generate_happy_path_calls_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def fake_complete_text(session, settings, request):
        captured["system"] = request.system_prompt
        captured["user"] = request.user_prompt
        captured["role"] = request.logical_role
        return SimpleNamespace(content="我盯着那张催缴单，手心全是汗。")

    monkeypatch.setattr(
        "bestseller.services.character_embodiment.complete_text", fake_complete_text
    )
    settings = SimpleNamespace(pipeline=SimpleNamespace(enable_character_embodiment=True))
    project = SimpleNamespace(language="zh-CN", genre="都市异能", slug="x")
    out = await generate_scene_embodiment(
        None, settings, project=project,
        chapter=SimpleNamespace(chapter_goal="主角第一次借运救母", chapter_number=3),
        scene=_scene(),
        story_bible={"cast_spec": {"characters": [{"name": "陆沉", "background": "电工"}]}},
    )
    assert "催缴单" in out
    assert captured["role"] == "writer"
    assert "陆沉" in captured["user"]


@pytest.mark.asyncio
async def test_generate_llm_failure_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(session, settings, request):
        raise RuntimeError("api down")

    monkeypatch.setattr(
        "bestseller.services.character_embodiment.complete_text", boom
    )
    settings = SimpleNamespace(pipeline=SimpleNamespace(enable_character_embodiment=True))
    project = SimpleNamespace(language="zh-CN", genre="都市异能", slug="x")
    out = await generate_scene_embodiment(
        None, settings, project=project,
        chapter=SimpleNamespace(chapter_goal="主角第一次借运救母", chapter_number=3),
        scene=_scene(), story_bible={},
    )
    assert out == ""


# ── integration: compile_methodology renders the block when present ─────────
def test_compile_methodology_renders_embodiment_block() -> None:
    bible = {EMBODIMENT_KEY: "我盯着那扇防盗门，铁皮冰得掌心一缩。"}
    compiled = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key=None,
        language="zh-CN",
        chapter_no=3,
        token_budget=3200,
        story_bible=bible,
    )
    assert "我盯着那扇防盗门，铁皮冰得掌心一缩。" in compiled.text
    assert "character_embodiment.py" in compiled.used_sources


def test_compile_methodology_english_renders_nothing() -> None:
    bible = {EMBODIMENT_KEY: "should not appear"}
    compiled = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key=None,
        language="en",
        chapter_no=3,
        token_budget=3200,
        story_bible=bible,
    )
    assert compiled.text == ""
