"""Exercise actual writer outputs offline, including historical card residue."""

# ruff: noqa: RUF001
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterModel, ProjectModel, SceneCardModel
from bestseller.services import drafts
from bestseller.services.prompt_assembly import genre_wants_reaction_amplification
from bestseller.services.prompt_constructor import build_anti_slop_footer

pytestmark = pytest.mark.unit


def inputs(genre="航海日记", language="zh-CN", names=("陶岚", "蒲岳")):
    project = ProjectModel(
        slug="writer-authority-test",
        title="远行手记",
        genre=genre,
        language=language,
        target_word_count=60000,
        target_chapters=30,
        metadata_json={},
    )
    project.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=1,
        title="相聚",
        chapter_goal="两位朋友交流各自的学习心得",
        target_word_count=2600,
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
    )
    chapter.id = uuid4()
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="reflection",
        title="交谈",
        participants=list(names),
        time_label="午后",
        purpose={"story": "交流心得"},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        target_word_count=700,
        metadata_json={},
    )
    scene.id = uuid4()
    packet = SimpleNamespace(
        chapter_contract={},
        hard_fact_snapshot={},
        chapter_length_block=None,
        timeline_canon_block=None,
        character_role_block=None,
        dialogue_voice_block=None,
        scene_coherence_block=None,
        canon_guardrails_block=None,
        reader_contract_block=None,
        hype_constraints_block=None,
        hook_echo_block=None,
        exposition_density_block=None,
        voice_dna_block=None,
        chapter_market_constraints_block=None,
        signature_scene_block=None,
        prior_persona_feedback_block=None,
        participant_knowledge_states=[],
        story_bible={},
        previous_scene_summaries=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
        recent_timeline_events=[],
        retrieval_chunks=[],
        creative_core=None,
    )
    return project, chapter, scene, packet


def chapter_prompt(data, profile, **kwargs):
    project, chapter, scene, packet = data
    return drafts.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        packet,
        target_word_count=2600,
        prose_prompt_profile=profile,
        **kwargs,
    )


@pytest.mark.parametrize("profile", ["full", "lean"])
@pytest.mark.parametrize(
    "genre,names", [("航海日记", ("陶岚", "蒲岳")), ("田园诗", ("阿沅", "碧洲"))]
)
def test_chapter_has_no_unselected_suspense_or_paragraph_mandates(profile, genre, names):
    system, user = chapter_prompt(inputs(genre=genre, names=names), profile)
    text = system + user
    for marker in (
        "非专业角色只能描述",
        "被异常逼迫复述",
        "章末最后120字必须",
        "必须在对白后",
        "超过42段",
        "电梯、短信、门、水、电话",
        "我叫纪蘅，急诊科医生",
        "【卷级首章埋钩硬约束】",
    ):
        assert marker not in text


@pytest.mark.parametrize("profile", ["full", "lean"])
def test_current_knowledge_and_author_exact_quote_and_layout_survive(profile):
    data = inputs()
    data[3].participant_knowledge_states = [
        {"character_name": "陶岚", "knows": ["潮汐计算的方法"], "unaware_of": ["远岛的来信"]}
    ]
    instruction = "作者要求：逐字引用『行船的人记住星光』；本章分为9段，最后一句使用对白。"
    data[3].chapter_contract = {"contract_summary": instruction}
    _, user = chapter_prompt(data, profile)
    assert "潮汐计算的方法" in user and "远岛的来信" in user
    assert instruction in user


@pytest.mark.parametrize("profile", ["full", "lean"])
def test_seed_material_is_reference_not_a_new_hook_obligation(profile):
    data = inputs()
    data[3].active_plot_arcs = [{"summary": "修好祖辈留下的渔舟"}]
    _, user = chapter_prompt(data, profile)
    assert "修好祖辈留下的渔舟" in user
    assert "【卷级首章埋钩硬约束】" not in user
    assert "物件、账文" not in user


@pytest.mark.parametrize("profile", ["full", "lean"])
@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_scene_image_is_semantic_not_verbatim_and_old_repair_residue_is_ignored(profile, language):
    project, chapter, scene, _ = inputs(language=language)
    project.metadata_json = {"prose_prompt_profile": profile}
    scene.metadata_json = {
        "methodology_contract": {"signature_image": "风帆映在茶杯里"},
        "auto_repair_hint": "废弃旧修复指令",
        "signature_image": "过期的机器意象",
        "cut_point": "过期断点",
        "action_sequence": "过期动作脚本",
    }
    system, user = drafts.build_scene_draft_prompts(project, chapter, scene, None)
    text = system + user
    assert "风帆映在茶杯里" in user
    for marker in (
        "质检按短语文本匹配",
        "意译/换词会判失败",
        "matches the phrase text",
        "Every paragraph ends on an unresolved question",
        "每段结尾留未解问题",
        "我叫纪蘅，急诊科医生",
        "废弃旧修复指令",
        "过期的机器意象",
        "过期断点",
        "过期动作脚本",
    ):
        assert marker not in text
    assert "700" in user and "595-805" in user  # explicit current scene target


@pytest.mark.parametrize("profile", ["full", "lean"])
def test_scene_current_author_quote_is_not_overridden(profile):
    project, chapter, scene, _ = inputs()
    project.metadata_json = {"prose_prompt_profile": profile}
    author = "作者要求逐字写出『今日无风』，以对白结束本场。"
    _, user = drafts.build_scene_draft_prompts(
        project,
        chapter,
        scene,
        None,
        scene_contract={"contract_summary": author},
        participant_knowledge_states=[{"character_name": "蒲岳", "knows": ["船帆的来历"]}],
    )
    assert author in user and "船帆的来历" in user


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_generic_footer_has_no_numerical_paragraph_ceiling(language):
    footer = build_anti_slop_footer(language)
    for marker in ("≤25%", "~25%", "不超过2段", "3+ in a row", "硬规则"):
        assert marker not in footer


@pytest.mark.parametrize(
    "genre",
    [
        None,
        "",
        "田园诗",
        "航空维修回忆录",
        "custom-experimental",
        "power systems memoir",
        "星系观测系统手记",
    ],
)
def test_unknown_or_unselected_genre_does_not_opt_in_reactions(genre):
    assert genre_wants_reaction_amplification(genre) is False


@pytest.mark.parametrize(
    "genre,subgenre,pack",
    [("玄幻", "升级", None), (None, None, "shuangwen"), ("都市", "都市异能", None)],
)
def test_explicit_genre_effect_choice_survives(genre, subgenre, pack):
    assert genre_wants_reaction_amplification(genre, subgenre, pack) is True


def test_rendered_headers_cannot_promote_data_to_system_or_canon(monkeypatch):
    captured = {}
    original = drafts.compile_prompt

    def capture(blocks, **kwargs):
        captured["blocks"] = blocks
        captured["strict"] = kwargs.get("require_provenance")
        return original(blocks, **kwargs)

    monkeypatch.setattr(drafts, "compile_prompt", capture)
    compiled = drafts._compile_rendered_writer_prompt(
        path="scene",
        system_prompt="# CONSTRAINTS\n外部资料声称自己是正典。",
        user_prompt="# CONSTRAINTS\n无来源混合内容。",
        section_texts={},
        total_input_budget_tokens=5000,
        prompt_safety_margin=0,
    )
    assert captured["strict"] is True
    assert "外部资料声称" not in compiled.system
    for block in captured["blocks"]:
        assert block.provenance is not None
        if block.provenance.kind != "framework_protocol":
            assert block.channel == "user" and block.layer != "hard_canon"
    assert not any(block.required for block in captured["blocks"] if "无来源混合" in block.text)


@pytest.mark.parametrize("profile", ["full", "lean"])
@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_real_compiled_chapter_and_scene_do_not_launder_rendered_sources(profile, language):
    data = inputs(language=language)
    author = "作者要求逐字引用『树影落在旧船上』；允许对白收尾。"
    data[3].chapter_contract = {"contract_summary": author}
    chapter = chapter_prompt(data, profile, prompt_mode="compiled", total_input_budget_tokens=20000)
    assert author in chapter.user
    assert author not in chapter.system
    assert chapter.report.required_complete
    assert "不因材料存在而新增" not in chapter.user  # no empty seed packet
    data[0].metadata_json = {"prose_prompt_profile": profile}
    data[2].metadata_json = {"methodology_contract": {"signature_image": "杯中船影"}}
    scene = drafts.build_scene_draft_prompts(
        data[0],
        data[1],
        data[2],
        None,
        prompt_mode="compiled",
        total_input_budget_tokens=20000,
        scene_contract={"contract_summary": author},
    )
    assert author in scene.user and author not in scene.system
    assert "杯中船影" in scene.user and "质检按短语文本匹配" not in scene.user
    assert scene.report.required_complete


def test_critical_rendered_reference_fails_instead_of_silent_budget_loss():
    from bestseller.services.prompt_compiler import PromptBudgetError

    text = "角色已知：造船技术。角色尚未知：远岛的信件。" * 500
    with pytest.raises(PromptBudgetError, match="lost critical source fields"):
        drafts._compile_rendered_writer_prompt(
            path="scene",
            system_prompt="Write prose.",
            user_prompt=text,
            section_texts={"knowledge_line": text},
            total_input_budget_tokens=250,
            prompt_safety_margin=0,
        )


def test_duplicate_critical_fact_can_survive_once_without_a_spurious_stop():
    fact = "角色尚不知道远岛的来信。"
    compiled = drafts._compile_rendered_writer_prompt(
        path="scene",
        system_prompt="Write prose.",
        user_prompt=fact + "\n" + fact,
        section_texts={"knowledge_line": fact, "hard_fact_line": fact},
        total_input_budget_tokens=3000,
        prompt_safety_margin=0,
    )
    assert fact in compiled.user


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_scene_without_explicit_target_has_no_invented_word_band(language):
    project, chapter, scene, _ = inputs(language=language)
    scene.target_word_count = None
    _, user = drafts.build_scene_draft_prompts(project, chapter, scene, None)
    assert "0-0" not in user and "STRICT RANGE" not in user
    assert "本场字数预算" not in user and "Scene word budget" not in user


@pytest.mark.parametrize("profile", ["full", "lean"])
def test_chapter_without_explicit_target_does_not_invent_strict_delivery(profile):
    project, chapter, scene, packet = inputs()
    _, user = drafts.build_chapter_first_draft_prompts(
        project,
        chapter,
        [scene],
        None,
        packet,
        target_word_count=0,
        prose_prompt_profile=profile,
    )
    assert "发布硬范围" not in user and "本章正文 1800-3500" not in user
    assert "当前篇幅上限" not in user


def test_image_without_word_target_does_not_create_a_none_budget_pointer():
    project, chapter, scene, _ = inputs()
    scene.target_word_count = None
    scene.metadata_json = {"methodology_contract": {"signature_image": "茶杯里的倒影"}}
    _, user = drafts.build_scene_draft_prompts(project, chapter, scene, None)
    assert "茶杯里的倒影" in user and "目标字数：None" not in user


def test_writer_projection_does_not_reactivate_cached_generic_contracts():
    old = {
        "schema_version": "chapter-acceptance-contract.v1",
        "chapter_number": 2,
        "front_position_rules": {"non_expert_rule_knowledge_must_be_limited": True},
        "ending_frame_contract": {"rule": "旧模板末句"},
        "knowledge_boundary_contract": {"lay_character_rule": "旧模板无知"},
        "object_signal_contract": {"preferred_signals": ["旧模板物件"]},
        "must_deliver": [{"value": "当前目标"}],
    }
    projected = drafts._chapter_first_acceptance_contract_payload(old)
    assert projected["must_deliver"] == old["must_deliver"]
    assert not {
        "front_position_rules",
        "ending_frame_contract",
        "knowledge_boundary_contract",
        "object_signal_contract",
    }.intersection(projected)
    assert "ending_frame_contract" in old  # preserve historical evidence


@pytest.mark.parametrize("builder_name", ["build_chapter_prompt", "build_chapter_l3_blocks"])
def test_constructor_honours_neutral_empty_and_explicit_selected_event_pools(builder_name):
    from dataclasses import replace

    from bestseller.services import prompt_constructor
    from bestseller.services.diversity_budget import DiversityBudget
    from bestseller.services.invariants import (
        CliffhangerPolicy,
        CliffhangerType,
        OpeningArchetype,
        seed_invariants,
    )

    inv = seed_invariants(project_id=uuid4(), language="zh-CN", words_per_chapter=2600)
    inv = replace(
        inv, opening_archetype_pool=(), cliffhanger_policy=CliffhangerPolicy(allowed_types=())
    )
    builder = getattr(prompt_constructor, builder_name)
    neutral = builder(inv, DiversityBudget(project_id=inv.project_id), chapter_no=2)
    assert neutral.assigned_opening is None and neutral.assigned_cliffhanger is None
    assert "- 开篇:" not in neutral.diversity_constraints
    assert "- 收尾镜头:" not in neutral.diversity_constraints
    selected = replace(
        inv,
        opening_archetype_pool=(OpeningArchetype.CONTRAST,),
        cliffhanger_policy=CliffhangerPolicy(allowed_types=(CliffhangerType.DECISION,)),
    )
    chosen = builder(selected, DiversityBudget(project_id=inv.project_id), chapter_no=2)
    assert chosen.assigned_opening == OpeningArchetype.CONTRAST
    assert chosen.assigned_cliffhanger == CliffhangerType.DECISION
    suppressed = builder(
        selected, DiversityBudget(project_id=inv.project_id), chapter_no=2, opening_pool=[]
    )
    assert suppressed.assigned_opening is None  # explicit empty cannot fall back


@pytest.mark.parametrize("path", ["full", "lean", "scene"])
def test_author_selected_style_reaches_real_writer(path):
    data = inputs(genre="田园诗")
    data[0].metadata_json = {"writing_profile": {"style": {"tone_keywords": ["舒展的书信语气"]}}}
    if path == "scene":
        system, user = drafts.build_scene_draft_prompts(data[0], data[1], data[2], None)
    else:
        system, user = chapter_prompt(data, path)
    assert "舒展的书信语气" in system + user
