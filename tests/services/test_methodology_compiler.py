from bestseller.services.methodology_compiler import (
    ChapterPosition,
    CompiledMethodology,
    MethodologyStage,
    compile_methodology,
)


def test_compile_methodology_prose_scene_suspense_mystery():
    result = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="suspense-mystery",
        chapter_no=42,
        chapter_position=ChapterPosition.MIDGAME,
        token_budget=1500,
    )
    assert "线索" in result.text or "推理" in result.text
    assert "prompt_packs/suspense-mystery.yaml" in result.used_sources
    assert result.estimated_tokens <= 1500


def test_compile_methodology_english_returns_empty():
    result = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="suspense-mystery",
        language="en-US",
        token_budget=1500,
    )
    assert result.text == ""
    assert result.estimated_tokens == 0


def test_compile_methodology_strict_budget():
    result = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="suspense-mystery",
        chapter_no=42,
        token_budget=200,
    )
    assert result.estimated_tokens <= 200


def test_compile_methodology_missing_pack_returns_partial():
    result = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="nonexistent-pack",
        chapter_no=42,
    )
    assert isinstance(result, CompiledMethodology)
    assert result.estimated_tokens > 0


def test_compile_methodology_all_stages_no_exception():
    for stage in MethodologyStage:
        for position in ChapterPosition:
            result = compile_methodology(
                stage=stage,
                prompt_pack_key="suspense-mystery",
                chapter_no=10,
                chapter_position=position,
            )
            assert isinstance(result, CompiledMethodology)
