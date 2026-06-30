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


def test_prose_scene_enforces_process_first_anti_ai_structure():
    """PROSE_SCENE must carry the anti-conclusion-first (去AI腔) structural rules.

    Root cause of the reported AI flavour was 结论先行/总分总 discourse structure,
    which the page-level action block alone did not address. These rules close it.
    """

    result = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="suspense-mystery",
        language="zh-CN",
        chapter_no=100,
        chapter_position=ChapterPosition.MIDGAME,
        token_budget=2200,
        include_writing_methodology_bridge=False,
    )

    assert "去AI腔铁律" in result.text
    assert "不要结论先行/总分总" in result.text
    assert "当叙事主句" in result.text  # 否定动作主句禁令
    assert "硬造比喻" in result.text
    assert "逐层透出" in result.text
    # 铁律必须排在页面动作之前(最高优先,避免被预算或顺序淹没)
    assert result.text.index("去AI腔铁律") < result.text.index("横测胜出融合写法")


def _prose_text(position: ChapterPosition) -> str:
    return compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="suspense-mystery",
        language="zh-CN",
        chapter_no=100,
        chapter_position=position,
        token_budget=2200,
        include_writing_methodology_bridge=False,
    ).text


def test_prose_scene_opening_gets_blind_validated_hook_block():
    """开篇章必须带'开篇炸点律'(前300字判官 O1/O3=100%)，且不带中段块。"""
    text = _prose_text(ChapterPosition.OPENING)
    assert "开篇炸点律" in text
    assert "前150字内主角立刻登场" in text
    assert "严禁用起床" in text  # 开场禁忌
    assert "中段持续追读律" not in text
    # 开篇炸点律最高优先,排在去AI腔铁律之前
    assert text.index("开篇炸点律") < text.index("去AI腔铁律")


def test_prose_scene_midchapter_gets_blind_validated_retention_block():
    """中段章必须带'中段持续追读律'(第50章老读者判官 M2/M5=100%)+反巧合禁忌，不带开篇块。"""
    for position in (ChapterPosition.EARLY, ChapterPosition.MIDGAME, ChapterPosition.CLIMAX):
        text = _prose_text(position)
        assert "中段持续追读律" in text, position
        assert "单章必须不可逆推进" in text, position
        assert "成长可见" in text, position
        assert "强行开挂" in text, position  # 反巧合堆砌禁忌
        assert "开篇炸点律" not in text, position


def test_prose_scene_unknown_position_is_position_invariant_only():
    """未知位置只给位置无关基底(去AI腔+页面动作)，不误加开篇/中段块。"""
    text = _prose_text(ChapterPosition.UNKNOWN)
    assert "去AI腔铁律" in text
    assert "开篇炸点律" not in text
    assert "中段持续追读律" not in text
