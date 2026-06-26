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


def test_writing_methodology_bridge_can_be_dropped_keeping_proven_levers():
    """C1-rules trim (prompt-ablation ladder 2026-06-10): dropping the
    writing_methodology·scene bridge removes the abstract说教 group while the
    budget-managed proven craft levers (物料具体化/场景锚定/金句/意象) survive —
    the freed budget refills with them rather than shrinking the block."""
    common = {
        "stage": MethodologyStage.PROSE_SCENE,
        "prompt_pack_key": "suspense-mystery",
        "language": "zh-CN",
        "chapter_no": 3,
        "token_budget": 3200,
    }
    on = compile_methodology(**common, include_writing_methodology_bridge=True)
    off = compile_methodology(**common, include_writing_methodology_bridge=False)

    assert "writing_methodology · scene" in on.text
    assert "writing_methodology · scene" not in off.text
    # proven levers are NOT collateral damage
    for lever in ("物料具体化", "场景锚定"):
        assert lever in off.text, f"proven lever {lever} dropped by the C1 trim"
    # default keeps the bridge (planner/review callers rely on it)
    default = compile_methodology(**common)
    assert "writing_methodology · scene" in default.text


def test_prose_scene_includes_arena_fusion_actions_without_abstract_bridge():
    result = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="suspense-mystery",
        language="zh-CN",
        chapter_no=100,
        chapter_position=ChapterPosition.MIDGAME,
        token_budget=2200,
        include_writing_methodology_bridge=False,
    )

    assert "横测胜出融合写法" in result.text
    assert "不可逆代价或倒计时" in result.text
    assert "每 300-500 字制造一个来自行动结果的具体问题" in result.text
    assert "压迫 → 选择 → 执行 → 反馈" in result.text
    assert "writing_methodology · scene" not in result.text
