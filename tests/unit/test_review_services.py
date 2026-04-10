from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneDraftVersionModel,
    StyleGuideModel,
)
from bestseller.services import reviews as review_services
from bestseller.services.reviews import (
    build_chapter_review_prompts,
    build_chapter_rewrite_prompts,
    build_scene_rewrite_prompts,
    evaluate_chapter_draft,
    evaluate_scene_draft,
    render_chapter_review_summary,
    render_rewritten_chapter_markdown,
    render_rewritten_scene_markdown,
)
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        *,
        scalar_results: list[object | None] | None = None,
        scalars_results: list[list[object]] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalars_results = list(scalars_results or [])
        self.added: list[object] = []
        self.executed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    async def get(self, model: object, key: object) -> object | None:
        return None

    async def execute(self, stmt: object) -> None:
        self.executed.append(stmt)


def build_settings():
    return load_settings(env={})


def test_evaluate_scene_draft_marks_short_template_for_rewrite() -> None:
    scene = SimpleNamespace(
        target_word_count=1000,
        scene_type="setup",
        participants=["沈砚", "港务官"],
        purpose={"emotion": "压迫感和抗拒"},
        scene_number=1,
    )
    chapter = SimpleNamespace(chapter_number=1, chapter_goal="展示主线冲突")
    draft = SimpleNamespace(
        content_md=(
            "## 场景 1：封港命令\n\n这一刻，沈砚、港务官被推入核心冲突。"
            "整体语气保持 克制、紧张。场景推进过程中，人物围绕任务发生碰撞，并把悬念留到结尾。"
        ),
        word_count=220,
    )

    result = evaluate_scene_draft(
        scene=scene,
        chapter=chapter,
        draft=draft,
        settings=build_settings(),
    )

    assert result.verdict == "rewrite"
    assert result.scores.overall < 0.7
    assert result.scores.hook_strength >= 0
    assert result.scores.voice_consistency >= 0
    assert any(finding.category == "goal" for finding in result.findings)


def test_evaluate_scene_draft_flags_contract_deviation() -> None:
    scene = SimpleNamespace(
        target_word_count=1000,
        scene_type="reveal",
        participants=["林夜", "苏禾"],
        purpose={"emotion": "不安和试探"},
        scene_number=2,
    )
    chapter = SimpleNamespace(chapter_number=1, chapter_goal="抛出未来物资渠道")
    draft = SimpleNamespace(
        content_md=(
            "## 场景 2：仓库碰头\n\n"
            "林夜和苏禾在仓库里简短碰头，交换了几句模糊判断。"
            "两人确认局势危险，但没有真正触及新的交易规则。"
        ),
        word_count=260,
    )
    scene_contract = SimpleNamespace(
        contract_summary="本场必须抛出未来物资渠道并建立互不信任。",
        core_conflict="林夜拒绝公开 app 来源，苏禾要求立即验货。",
        emotional_shift="从试探合作转向互相提防。",
        information_release="未来物资 app 可以提前购买末日资源。",
        tail_hook="苏禾发现林夜拿出的药剂批次来自未来。",
    )

    result = evaluate_scene_draft(
        scene=scene,
        chapter=chapter,
        draft=draft,
        settings=build_settings(),
        scene_contract=scene_contract,
    )

    assert result.verdict == "rewrite"
    assert result.scores.contract_alignment < 0.7
    assert result.scores.payoff_density < 0.8
    assert any(finding.category == "contract_alignment" for finding in result.findings)
    assert "contract_missing_labels" in result.evidence_summary


def test_evaluate_scene_draft_recognizes_narrative_contract_delivery() -> None:
    scene = SimpleNamespace(
        target_word_count=300,
        scene_type="confrontation",
        participants=["沈砚", "顾临"],
        purpose={"story": "逼出黑匣子真相", "emotion": "警觉转冷怒"},
        scene_number=3,
    )
    chapter = SimpleNamespace(chapter_number=1, chapter_goal="逼近黑匣子真相")
    draft = SimpleNamespace(
        content_md=(
            "顾临把黑匣子摔在桌上，铁壳震得茶杯轻响。\n\n"
            "“缺的那几页呢？”沈砚盯着他，手背青筋绷起。\n\n"
            "顾临没回答，只把舱门反锁，冷冷问他昨夜到底把消息递给了谁。\n\n"
            "桌上的航图被翻开，撕裂口整整齐齐，最关键的禁航航线记录果然被人提前取走。\n\n"
            "沈砚本来还想压着火气谈条件，这一刻却彻底沉了脸。"
            "两个人谁也不肯先退，空气像被绞紧。\n\n"
            "就在顾临伸手去摸腰后的配枪时，门外忽然响起了不属于他们的脚步声。"
        ),
        word_count=320,
    )
    scene_contract = SimpleNamespace(
        contract_summary="本场要逼出黑匣子缺页真相，并让合作关系转向互相戒备。",
        core_conflict="沈砚要求顾临交出缺失页，顾临坚持先确认谁在泄密。",
        emotional_shift="从试探配合转向冷硬对峙。",
        information_release="黑匣子被人为拆走了记录禁航航线的几页。",
        tail_hook="门外突然传来第三个人的脚步声。",
    )

    result = evaluate_scene_draft(
        scene=scene,
        chapter=chapter,
        draft=draft,
        settings=build_settings(),
        scene_contract=scene_contract,
    )

    assert result.scores.dialogue >= 0.7
    assert result.scores.hook_strength >= 0.45
    assert result.scores.contract_alignment >= 0.45
    assert result.evidence_summary["contract_matched_count"] >= 2
    assert result.evidence_summary["contract_alignment_breakdown"]["information_release"] >= 0.55
    assert all(finding.category != "dialogue" for finding in result.findings)


def test_evaluate_scene_draft_penalizes_meta_scaffolding_language() -> None:
    scene = SimpleNamespace(
        target_word_count=240,
        scene_type="setup",
        participants=["沈砚"],
        purpose={"story": "推进调查", "emotion": "警觉"},
        scene_number=1,
    )
    chapter = SimpleNamespace(chapter_number=1, chapter_goal="推进调查")
    clean_draft = SimpleNamespace(
        content_md=(
            "沈砚贴着墙听外面的脚步，指尖慢慢压住了口袋里的钥匙。"
            "门外的人没有说话，只在第三次停顿时轻轻敲了敲门。"
        ),
        word_count=240,
    )
    meta_draft = SimpleNamespace(
        content_md=(
            "整体语气保持克制、紧张。本场景的情绪任务是警觉。"
            "沈砚贴着墙听外面的脚步，指尖慢慢压住了口袋里的钥匙。"
            "门外的人没有说话，只在第三次停顿时轻轻敲了敲门。"
        ),
        word_count=240,
    )

    clean_result = evaluate_scene_draft(
        scene=scene,
        chapter=chapter,
        draft=clean_draft,
        settings=build_settings(),
    )
    meta_result = evaluate_scene_draft(
        scene=scene,
        chapter=chapter,
        draft=meta_draft,
        settings=build_settings(),
    )

    assert clean_result.scores.style > meta_result.scores.style
    assert clean_result.scores.voice_consistency > meta_result.scores.voice_consistency
    assert clean_result.evidence_summary["meta_leak_detected"] is False
    assert meta_result.evidence_summary["meta_leak_detected"] is True


def test_scene_rewrite_prompts_switch_to_english_for_english_projects() -> None:
    project = ProjectModel(
        slug="storm-ledger",
        title="Storm Ledger",
        genre="Fantasy",
        sub_genre="Epic Fantasy",
        language="en-US",
        target_word_count=90000,
        target_chapters=24,
        audience="KU readers",
        metadata_json={
            "writing_profile": {
                "market": {
                    "platform_target": "Kindle Unlimited",
                    "content_mode": "English-language commercial fantasy serial",
                    "reader_promise": "Fast-moving fantasy with escalating political danger.",
                    "selling_points": ["storm magic", "buried dynasty", "betrayal"],
                    "trope_keywords": ["chosen family", "forbidden archive"],
                    "hook_keywords": ["sealed letter", "execution order"],
                    "opening_strategy": "Open with the order and the stolen key in the same scene.",
                    "chapter_hook_strategy": "End every chapter with a fresh threat or reveal.",
                    "payoff_rhythm": "Short payoff every chapter, major payoff every 5-7 chapters",
                },
                "style": {
                    "tone_keywords": ["taut", "ominous", "fast"],
                },
                "serialization": {
                    "opening_mandate": "Hook the reader in the first scene with concrete danger.",
                    "first_three_chapter_goal": "Lock in the central conflict, edge, and reversal.",
                    "scene_drive_rule": "Every scene must create a gain, a loss, or a sharper choice.",
                    "chapter_ending_rule": "Every chapter must end on a question, a threat, or a costly next move.",
                },
            }
        },
    )
    project.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=1,
        title="Storm Wake",
        chapter_goal="Force Elara to steal the sealed archive ledger",
        target_word_count=5000,
    )
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=uuid4(),
        scene_number=1,
        title="The Order Arrives",
        scene_type="hook",
        purpose={"story": "Trigger the execution order", "emotion": "panic turning into resolve"},
        participants=["Elara", "Captain Vale"],
        entry_state={},
        exit_state={},
        target_word_count=1500,
    )
    draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=uuid4(),
        version_no=1,
        content_md="The execution order arrived before dawn.",
        word_count=120,
        is_current=True,
        generation_params={},
    )
    rewrite_task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="scene_review",
        trigger_source_id=uuid4(),
        rewrite_strategy="scene_dialogue_conflict_expansion",
        priority=3,
        status="pending",
        instructions="Strengthen the confrontation and sharpen the cliffhanger.",
        context_required=[],
        metadata_json={},
    )
    style_guide = StyleGuideModel(
        project_id=project.id,
        pov_type="third-limited",
        tense="present",
        tone_keywords=["taut", "ominous", "fast"],
        prose_style="commercial-genre",
        sentence_style="mixed",
        info_density="lean",
        dialogue_ratio=0.42,
        taboo_words=[],
        taboo_topics=[],
        reference_works=[],
        custom_rules=[],
    )

    system_prompt, user_prompt = build_scene_rewrite_prompts(
        project,
        chapter,
        scene,
        draft,
        rewrite_task,
        style_guide,
    )
    combined = system_prompt + "\n" + user_prompt

    assert "English-language fiction rewriting editor" in system_prompt
    assert "Rewrite the current scene in English only" in user_prompt
    assert "Project: Storm Ledger" in user_prompt
    assert "Chapter 1" in user_prompt
    assert "长篇中文小说" not in combined


def test_review_summaries_switch_to_english_for_english_projects() -> None:
    scores = SimpleNamespace(overall=0.62)
    findings = [SimpleNamespace(category="hook", severity="medium", message="Tail hook lands too softly.")]
    scene_summary = review_services.render_scene_review_summary(
        SimpleNamespace(
            verdict="rewrite",
            scores=scores,
            severity_max="medium",
            findings=findings,
            rewrite_instructions="Sharpen the final reveal.",
        ),
        language="en-US",
    )
    chapter_summary = review_services.render_chapter_review_summary(
        SimpleNamespace(
            verdict="attention",
            scores=scores,
            severity_max="medium",
            findings=findings,
            rewrite_instructions="Tighten transitions between scenes.",
        ),
        language="en-US",
    )

    assert "Verdict：" in scene_summary
    assert "Rewrite instructions：" in scene_summary
    assert "结论" not in scene_summary
    assert "Verdict：" in chapter_summary
    assert "Findings:" in chapter_summary
    assert "问题列表" not in chapter_summary


def test_chapter_context_section_switches_to_english_for_english_projects() -> None:
    packet = SimpleNamespace(
        hard_fact_snapshot=SimpleNamespace(chapter_number=3, facts=[]),
        active_plot_arcs=[SimpleNamespace(arc_type="main_plot", name="Crown Hunt", promise="Find the ledger.")],
        active_arc_beats=[SimpleNamespace(arc_code="main", beat_kind="turn", summary="Elara burns her cover.")],
        unresolved_clues=[SimpleNamespace(clue_code="clue-1", label="Missing seal")],
        planned_payoffs=[],
        active_emotion_tracks=[],
        active_antagonist_plans=[],
        chapter_contract=SimpleNamespace(contract_summary="Push Elara into open rebellion."),
        tree_context_nodes=[],
        previous_scene_summaries=[],
        chapter_scenes=[SimpleNamespace(scene_number=1, title="Storm Wake", scene_type="hook", story_purpose="Force the theft", emotion_purpose="panic to resolve")],
        recent_timeline_events=[],
        retrieval_chunks=[],
    )

    rendered = review_services._render_chapter_context_section(packet, language="en-US")

    assert "Active narrative lines:" in rendered
    assert "Chapter contract：" in rendered
    assert "Chapter scene plan:" in rendered
    assert "激活叙事线" not in rendered
    assert "本章场景计划" not in rendered


def test_render_rewritten_scene_markdown_is_non_prose_fallback() -> None:
    """Rewrite fallback must preserve the existing draft verbatim, never invent prose.

    Historically this function returned six paragraphs of template Chinese
    ("XX 重新被推回《...》第 N 章的核心冲突。叙事仍采用 third-limited
    视角…", "这一版重写围绕…", "金属舱壁传来的冷意…"). Those sentences
    ended up stored as the scene's final content_md whenever the rewriter
    LLM timed out, and produced the meta-text seen across multiple
    chapters of ``output/apocalypse-supply-1775626373``.
    """
    project = SimpleNamespace(title="长夜巡航", slug="chang-ye-xun-hang")
    chapter = SimpleNamespace(chapter_number=1, chapter_goal="展示主线冲突")
    scene = SimpleNamespace(
        title="封港命令",
        scene_number=1,
        time_label="深夜",
        participants=["沈砚", "港务官"],
        purpose={"story": "抛出禁令任务", "emotion": "压迫感和抗拒"},
        target_word_count=1000,
    )
    current_draft = SimpleNamespace(content_md="旧版本草稿：沈砚站在封港通告前。", id=uuid4())
    rewrite_task = SimpleNamespace(
        rewrite_strategy="scene_dialogue_conflict_expansion",
        instructions="补强冲突和对话",
    )
    style_guide = SimpleNamespace(pov_type="third-limited", tone_keywords=["冷峻", "紧张"])

    content = render_rewritten_scene_markdown(
        project,
        chapter,
        scene,
        current_draft,
        rewrite_task,
        style_guide,
    )

    # Fallback must preserve the existing draft content verbatim.
    assert "旧版本草稿：沈砚站在封港通告前。" in content
    # A non-prose HTML marker identifies the failed rewrite.
    assert "<!--" in content
    assert "rewrite-scene-fallback" in content
    assert f"chapter={chapter.chapter_number}" in content
    # None of the legacy template sentences may appear.
    assert "重新被推回《" not in content
    assert "叙事仍采用" not in content
    assert "third-limited" not in content
    assert "这一版重写围绕" not in content
    assert "金属舱壁传来的冷意" not in content


def test_evaluate_chapter_draft_marks_sparse_chapter_for_rewrite() -> None:
    chapter = SimpleNamespace(
        chapter_number=3,
        title="静默航道",
        chapter_goal="让主角确认航图被篡改",
        target_word_count=3200,
    )
    scenes = [
        SimpleNamespace(scene_number=1, title="旧搭档回舰"),
        SimpleNamespace(scene_number=2, title="黑匣子缺页"),
    ]
    draft = SimpleNamespace(
        content_md="# 第3章 静默航道\n\n## 场景 1：旧搭档回舰\n\n很短的章节草稿。",
        word_count=420,
    )

    result = evaluate_chapter_draft(
        chapter=chapter,
        scenes=scenes,
        draft=draft,
        settings=build_settings(),
    )

    assert result.verdict == "rewrite"
    assert result.scores.coverage < 0.8
    assert result.scores.main_plot_progression < 0.8
    assert any(finding.category == "coverage" for finding in result.findings)
    assert result.rewrite_instructions is not None


def test_evaluate_chapter_draft_flags_contract_deviation() -> None:
    chapter = SimpleNamespace(
        chapter_number=5,
        title="第一次囤货",
        chapter_goal="主角第一次验证未来物资 app 的真实性",
        target_word_count=2800,
    )
    scenes = [
        SimpleNamespace(scene_number=1, title="仓库试单"),
        SimpleNamespace(scene_number=2, title="异常到账"),
    ]
    draft = SimpleNamespace(
        content_md=(
            "# 第5章 第一次囤货\n\n"
            "## 场景 1：仓库试单\n\n"
            "主角简单测试了一个订单，确认东西能到。"
        ),
        word_count=550,
    )
    chapter_contract = SimpleNamespace(
        contract_summary="本章要完成第一次试单、建立代价规则并在结尾抛出更大的囤货欲望。",
        core_conflict="主角必须决定是否相信一个明显违背常识的购买入口。",
        emotional_shift="从谨慎试探转向压抑不住的贪念和兴奋。",
        information_release="未来物资 app 只在末日前三天可用且每次交易都会消耗寿命。",
        closing_hook="主角发现更高阶的基因进化药也能购买。",
    )

    result = evaluate_chapter_draft(
        chapter=chapter,
        scenes=scenes,
        draft=draft,
        settings=build_settings(),
        chapter_contract=chapter_contract,
    )

    assert result.verdict == "rewrite"
    assert result.scores.contract_alignment < 0.7
    assert result.scores.ending_hook_effectiveness < 0.8
    assert any(finding.category == "contract_alignment" for finding in result.findings)
    assert result.evidence_summary["contract_expectation_count"] >= 4


def test_evaluate_chapter_draft_no_longer_rewards_goal_scaffold() -> None:
    chapter = SimpleNamespace(
        chapter_number=1,
        title="静默航道",
        chapter_goal="确认航图被篡改并暴露自己已经被盯上",
        target_word_count=420,
    )
    scenes = [
        SimpleNamespace(scene_number=1, title="旧搭档回舰"),
        SimpleNamespace(scene_number=2, title="黑匣子缺页"),
    ]
    chapter_contract = SimpleNamespace(
        contract_summary="本章要确认航图被篡改，并在结尾抛出更危险的追踪者。",
        core_conflict="主角必须判断是谁先一步动了黑匣子。",
        emotional_shift="从克制调查转向知道自己已经暴露。",
        information_release="缺失页记录了禁航航线和内鬼代号。",
        closing_hook="章节结尾有人在门外报出主角昨夜的假身份。",
    )
    clean_body = (
        "# 第1章 静默航道\n\n"
        "## 场景 1：旧搭档回舰\n\n"
        "顾临深夜回舰，把旧黑匣子扔到桌上。沈砚刚问第一句，便发现壳体边缘少了几页记录。"
        "昨夜为了掩护调查，他用过一套临时身份，此刻却不敢把这个细节说出口。\n\n"
        "## 场景 2：黑匣子缺页\n\n"
        "两人顺着缺口往下翻，确认被拆走的正是禁航航线和内鬼代号。"
        "顾临判断有人比他们更早摸到黑匣子，沈砚这才意识到自己昨夜的路线早就暴露。"
        "门外却在这时响起三下敲门声，外面的人隔着门板，准确报出了他昨夜伪造的名字。"
    )
    scaffold_body = (
        "# 第1章 静默航道\n\n"
        "> 本章目标：确认航图被篡改并暴露自己已经被盯上\n\n"
        + clean_body.split("\n\n", 1)[1]
    )
    chapter_context = SimpleNamespace(
        previous_scene_summaries=[SimpleNamespace(summary="上一场里沈砚昨夜改用了假身份上岸。")],
        recent_timeline_events=[
            SimpleNamespace(summary="昨夜有人提前动过黑匣子。", event_name="黑匣子被动手脚")
        ],
        active_arc_beats=[],
    )

    clean_result = evaluate_chapter_draft(
        chapter=chapter,
        scenes=scenes,
        draft=SimpleNamespace(content_md=clean_body, word_count=430),
        settings=build_settings(),
        chapter_contract=chapter_contract,
        chapter_context=chapter_context,
    )
    scaffold_result = evaluate_chapter_draft(
        chapter=chapter,
        scenes=scenes,
        draft=SimpleNamespace(content_md=scaffold_body, word_count=430),
        settings=build_settings(),
        chapter_contract=chapter_contract,
        chapter_context=chapter_context,
    )

    assert clean_result.scores.coverage >= 0.8
    assert clean_result.scores.coherence >= 0.75
    assert clean_result.scores.style > scaffold_result.scores.style
    assert clean_result.evidence_summary["meta_leak_detected"] is False
    assert all(
        finding.category not in {"coverage", "coherence"}
        for finding in clean_result.findings
    )


def test_evaluate_chapter_draft_allows_low_severity_polish_findings() -> None:
    chapter = SimpleNamespace(
        chapter_number=4,
        title="反咬",
        chapter_goal="推进调查并逼近真相",
        target_word_count=2400,
    )
    scenes = [
        SimpleNamespace(scene_number=1, title="开场压力"),
        SimpleNamespace(scene_number=2, title="关键碰撞"),
    ]
    body = (
        "# 第4章 反咬\n\n"
        "> 本章目标：推进调查并逼近真相\n\n"
        "上一阶段留下的异常信号还压在众人心头，局势继续升高。\n\n"
        "## 场景 1：开场压力\n\n"
        "推进调查并逼近真相。" + ("真相逼近，" * 220) + "\n\n"
        "## 场景 2：关键碰撞\n\n"
        "下一步必须立刻反击，新的不确定性已经出现。"
    )
    draft = SimpleNamespace(
        content_md=body,
        word_count=2200,
    )

    result = evaluate_chapter_draft(
        chapter=chapter,
        scenes=scenes,
        draft=draft,
        settings=build_settings(),
    )

    assert result.verdict == "pass"
    assert any(finding.category == "continuity" for finding in result.findings)
    assert all(finding.severity == "low" for finding in result.findings)


def test_render_chapter_review_summary_and_prompts_include_context() -> None:
    project = SimpleNamespace(title="长夜巡航", genre="末日科幻", sub_genre="重生囤货", language="zh-CN", metadata_json={})
    chapter = SimpleNamespace(chapter_number=3, title="静默航道", chapter_goal="推进调查")
    draft = SimpleNamespace(content_md="# 第3章 静默航道\n\n## 场景 1：旧搭档回舰")
    chapter_context = SimpleNamespace(
        previous_scene_summaries=[SimpleNamespace(chapter_number=2, scene_number=2, scene_title="偏移的航标", summary="上一章发现异常。")],
        chapter_scenes=[SimpleNamespace(scene_number=1, title="旧搭档回舰", scene_type="setup", story_purpose="推进调查", emotion_purpose="警觉")],
        recent_timeline_events=[SimpleNamespace(story_time_label="昨夜", event_name="发现异常", consequences=["调查升级"], summary="发现异常")],
        active_emotion_tracks=[SimpleNamespace(track_type="bond", title="沈砚 / 顾临 关系线", summary="双方暂时合作但信任未恢复。", trust_level=0.42, conflict_level=0.7)],
        active_antagonist_plans=[SimpleNamespace(threat_type="volume_pressure", title="第1卷反派升级", goal="封锁调查路径", current_move="切断证据链", next_countermove="围堵主角")],
        retrieval_chunks=[SimpleNamespace(source_type="scene_draft", chunk_text="过去场景片段")],
    )
    review_result = SimpleNamespace(
        verdict="rewrite",
        severity_max="medium",
        scores=SimpleNamespace(model_dump=lambda mode="json": {"overall": 0.62}),
        findings=[SimpleNamespace(model_dump=lambda mode="json": {"category": "coverage", "severity": "medium"})],
        rewrite_instructions="补强场景衔接",
    )
    rewrite_task = SimpleNamespace(
        instructions="补强场景衔接",
        rewrite_strategy="chapter_coherence_bridge_rewrite",
    )

    summary = render_chapter_review_summary(
        review_services.ChapterReviewResult(
            verdict="rewrite",
            severity_max="medium",
            scores=review_services.ChapterReviewScores(
                overall=0.62,
                goal=0.7,
                coverage=0.5,
                coherence=0.6,
                continuity=0.58,
                main_plot_progression=0.64,
                subplot_progression=0.55,
                style=0.85,
                hook=0.48,
                ending_hook_effectiveness=0.52,
                volume_mission_alignment=0.66,
                contract_alignment=0.66,
                pacing_rhythm=0.60,
                character_voice_distinction=0.55,
                thematic_resonance=0.58,
            ),
            findings=[
                review_services.ChapterReviewFinding(
                    category="coverage",
                    severity="medium",
                    message="章节没有充分覆盖当前场景计划。",
                )
            ],
            rewrite_instructions="补强场景衔接",
        )
    )
    system_prompt, user_prompt = build_chapter_review_prompts(
        project,
        chapter,
        draft,
        chapter_context,
        review_result,
    )
    rewrite_system_prompt, rewrite_user_prompt = build_chapter_rewrite_prompts(
        project,
        chapter,
        draft,
        rewrite_task,
        chapter_context,
    )

    assert "结论：rewrite" in summary
    assert "章节评论者" in system_prompt
    assert "本章场景计划" in user_prompt
    assert "上一章发现异常" in user_prompt
    assert "关系与情绪线" in user_prompt
    assert "反派推进" in user_prompt
    assert "章节重写编辑" in rewrite_system_prompt
    assert "补强场景衔接" in rewrite_user_prompt


def test_render_rewritten_chapter_markdown_preserves_existing_body_verbatim() -> None:
    """Chapter rewrite fallback must not invent wrapper prose around the body."""
    project = SimpleNamespace(title="长夜巡航", slug="chang-ye-xun-hang")
    chapter = SimpleNamespace(
        chapter_number=3,
        title="静默航道",
        chapter_goal="推进调查",
        target_word_count=2000,
    )
    current_draft = SimpleNamespace(
        content_md=(
            "# 第3章 静默航道\n\n"
            "## 场景 1：旧搭档回舰\n\n"
            "章节旧稿：顾临把旧证件按在桌上。"
        ),
    )
    rewrite_task = SimpleNamespace(
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        instructions="补强承接与收尾",
    )
    chapter_context = SimpleNamespace(
        previous_scene_summaries=[SimpleNamespace(summary="上一阶段发现异常。")],
        recent_timeline_events=[SimpleNamespace(summary="真相开始浮出水面")],
    )

    content = render_rewritten_chapter_markdown(
        project,
        chapter,
        current_draft,
        rewrite_task,
        chapter_context,
    )

    # Canonical heading uses colon and appears exactly once.
    assert "# 第3章：静默航道" in content
    assert content.count("第3章") == 1
    # Original body is preserved verbatim.
    assert "章节旧稿：顾临把旧证件按在桌上" in content
    # Fallback marker identifies the failed rewrite.
    assert "<!--" in content
    assert "rewrite-chapter-fallback" in content
    # None of the legacy template wrappers may appear.
    assert "上一阶段留下的局势仍压在众人心头" not in content
    assert "这一章不再只是承接" not in content
    assert "章节收束时" not in content


def test_render_rewritten_chapter_markdown_handles_empty_draft() -> None:
    """Empty / missing draft must return a valid heading without crashing."""
    project = SimpleNamespace(title="长夜巡航", slug="chang-ye-xun-hang")
    chapter = SimpleNamespace(
        chapter_number=7, title=None, chapter_goal="推进调查", target_word_count=2000
    )
    current_draft = SimpleNamespace(content_md="")
    rewrite_task = SimpleNamespace(
        rewrite_strategy="chapter_coherence_bridge_rewrite", instructions=""
    )

    content = render_rewritten_chapter_markdown(
        project,
        chapter,
        current_draft,
        rewrite_task,
        None,
    )

    assert "<!-- rewrite-chapter-fallback" in content
    assert "# 第7章" in content
    assert "上一阶段留下的局势" not in content


@pytest.mark.asyncio
async def test_review_chapter_draft_creates_rewrite_task_for_low_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        metadata_json={},
    )
    project.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=3,
        title="静默航道",
        chapter_goal="推进调查",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    scenes = [
        SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=1,
            scene_type="setup",
            title="旧搭档回舰",
            participants=["沈砚"],
            purpose={"story": "推进调查", "emotion": "警觉"},
            entry_state={},
            exit_state={},
            metadata_json={},
            target_word_count=1000,
        ),
        SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=2,
            scene_type="reveal",
            title="黑匣子缺页",
            participants=["沈砚", "顾临"],
            purpose={"story": "发现证据缺失", "emotion": "不安"},
            entry_state={},
            exit_state={},
            metadata_json={},
            target_word_count=1000,
        ),
    ]
    for scene in scenes:
        scene.id = uuid4()
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第3章 静默航道\n\n## 场景 1：旧搭档回舰\n\n很短的章节草稿。",
        word_count=420,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_build_chapter_writer_context(session, settings, project_slug, chapter_number):
        return SimpleNamespace(
            previous_scene_summaries=[],
            chapter_scenes=[],
            recent_timeline_events=[],
            retrieval_chunks=[],
        )

    async def fake_complete_text(session, settings, request):
        return SimpleNamespace(content="需要重写这一章。", model_name="mock-critic", llm_run_id=uuid4())

    monkeypatch.setattr(review_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        review_services,
        "build_chapter_writer_context",
        fake_build_chapter_writer_context,
    )
    monkeypatch.setattr(review_services, "complete_text", fake_complete_text)

    session = FakeSession(
        scalar_results=[chapter, draft],
        scalars_results=[scenes],
    )

    result, report, quality, rewrite_task = await review_services.review_chapter_draft(
        session,
        build_settings(),
        "my-story",
        3,
    )

    assert result.verdict == "rewrite"
    assert report.id is not None
    assert quality.id is not None
    assert rewrite_task is not None
    assert rewrite_task.trigger_type == "chapter_review"
    assert chapter.status == "revision"


@pytest.mark.asyncio
async def test_review_scene_draft_skips_llm_commentary_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        metadata_json={},
    )
    project.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=1,
        title="失准星图",
        chapter_goal="展示主线冲突",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3000,
    )
    chapter.id = uuid4()
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="setup",
        title="封港命令",
        participants=["沈砚", "港务官"],
        purpose={"story": "抛出禁令任务", "emotion": "压迫感和抗拒"},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=1000,
    )
    scene.id = uuid4()
    scene_draft = SceneDraftVersionModel(
        project_id=project.id,
        scene_card_id=scene.id,
        version_no=1,
        content_md="短场景草稿。",
        word_count=10,
        is_current=True,
        generation_params={},
    )
    scene_draft.id = uuid4()
    style = StyleGuideModel(
        project_id=project.id,
        pov_type="third-limited",
        tense="present",
        tone_keywords=["冷峻", "紧张"],
        prose_style="baseline",
        sentence_style="mixed",
        info_density="medium",
        dialogue_ratio=0.35,
        taboo_words=[],
        taboo_topics=[],
        reference_works=[],
        custom_rules=[],
    )

    async def fail_complete_text(*args, **kwargs):
        raise AssertionError("scene review should not call critic LLM when commentary is disabled")

    async def fake_load_scene_context(session, project_slug, chapter_number, scene_number):
        return project, chapter, scene, style, scene_draft

    monkeypatch.setattr(review_services, "_load_scene_context", fake_load_scene_context)
    monkeypatch.setattr(review_services, "complete_text", fail_complete_text)
    session = FakeSession()

    result, report, quality, rewrite_task = await review_services.review_scene_draft(
        session,
        build_settings(),
        "my-story",
        1,
        1,
    )

    assert result.verdict == "rewrite"
    assert report.reviewer_type == "rule-based-critic"
    assert report.llm_run_id is None
    assert report.structured_output["critic_response"].startswith("结论：rewrite")
    assert quality.id is not None
    assert rewrite_task is not None


@pytest.mark.asyncio
async def test_review_chapter_draft_skips_llm_commentary_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        metadata_json={},
    )
    project.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=3,
        title="静默航道",
        chapter_goal="让主角确认航图被篡改",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=3200,
    )
    chapter.id = uuid4()
    scenes = [
        SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=1,
            scene_type="setup",
            title="旧搭档回舰",
            participants=["沈砚"],
            purpose={"story": "推进调查", "emotion": "警觉"},
            entry_state={},
            exit_state={},
            metadata_json={},
            target_word_count=1000,
        ),
        SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=2,
            scene_type="reveal",
            title="黑匣子缺页",
            participants=["沈砚", "顾临"],
            purpose={"story": "发现证据缺失", "emotion": "不安"},
            entry_state={},
            exit_state={},
            metadata_json={},
            target_word_count=1000,
        ),
    ]
    for scene in scenes:
        scene.id = uuid4()
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第3章 静默航道\n\n## 场景 1：旧搭档回舰\n\n很短的章节草稿。",
        word_count=420,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    draft.id = uuid4()

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_build_chapter_writer_context(session, settings, project_slug, chapter_number):
        return SimpleNamespace(
            previous_scene_summaries=[],
            chapter_scenes=[],
            recent_timeline_events=[],
            retrieval_chunks=[],
        )

    async def fail_complete_text(*args, **kwargs):
        raise AssertionError("chapter review should not call critic LLM when commentary is disabled")

    monkeypatch.setattr(review_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        review_services,
        "build_chapter_writer_context",
        fake_build_chapter_writer_context,
    )
    monkeypatch.setattr(review_services, "complete_text", fail_complete_text)

    session = FakeSession(
        scalar_results=[chapter, draft],
        scalars_results=[scenes],
    )

    result, report, quality, rewrite_task = await review_services.review_chapter_draft(
        session,
        build_settings(),
        "my-story",
        3,
    )

    assert result.verdict == "rewrite"
    assert report.reviewer_type == "rule-based-critic"
    assert report.llm_run_id is None
    assert report.structured_output["critic_response"].startswith("结论：rewrite")
    assert quality.id is not None
    assert rewrite_task is not None


@pytest.mark.asyncio
async def test_rewrite_chapter_from_task_creates_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        metadata_json={},
    )
    project.id = uuid4()
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=3,
        title="静默航道",
        chapter_goal="推进调查",
        information_revealed=[],
        information_withheld=[],
        foreshadowing_actions={},
        metadata_json={},
        target_word_count=2400,
    )
    chapter.id = uuid4()
    scenes = [
        SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=1,
            scene_type="setup",
            title="旧搭档回舰",
            participants=["沈砚"],
            purpose={"story": "推进调查", "emotion": "警觉"},
            entry_state={},
            exit_state={},
            metadata_json={},
            target_word_count=1000,
        )
    ]
    scenes[0].id = uuid4()
    current_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="# 第3章 静默航道\n\n## 场景 1：旧搭档回舰\n\n章节旧稿。",
        word_count=820,
        assembled_from_scene_draft_ids=[],
        is_current=True,
    )
    current_draft.id = uuid4()
    rewrite_task = RewriteTaskModel(
        project_id=project.id,
        trigger_type="chapter_review",
        trigger_source_id=chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        priority=4,
        status="pending",
        instructions="补强场景衔接与章节收尾",
        context_required=[],
        metadata_json={},
    )
    rewrite_task.id = uuid4()
    rewrite_task.attempts = 0

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_build_chapter_writer_context(session, settings, project_slug, chapter_number):
        return SimpleNamespace(
            previous_scene_summaries=[
                SimpleNamespace(
                    chapter_number=2,
                    scene_number=2,
                    scene_title="偏移的航标",
                    summary="上一阶段发现异常。",
                )
            ],
            recent_timeline_events=[
                SimpleNamespace(
                    story_time_label="昨夜",
                    event_name="发现异常",
                    consequences=["调查升级"],
                    summary="真相开始浮出水面",
                )
            ],
            chapter_scenes=[],
            retrieval_chunks=[],
        )

    async def fake_complete_text(session, settings, request):
        rewritten_body = "真相开始浮出水面，" * 120
        return SimpleNamespace(
            content=(
                "# 第3章 静默航道\n\n## 场景 1：旧搭档回舰\n\n"
                f"这是新的章节重写稿，{rewritten_body}"
            ),
            model_name="mock-editor",
            llm_run_id=uuid4(),
            provider="mock",
        )

    monkeypatch.setattr(review_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        review_services,
        "build_chapter_writer_context",
        fake_build_chapter_writer_context,
    )
    monkeypatch.setattr(review_services, "complete_text", fake_complete_text)

    session = FakeSession(
        scalar_results=[chapter, current_draft, rewrite_task, 1],
        scalars_results=[scenes],
    )

    new_draft, completed_task = await review_services.rewrite_chapter_from_task(
        session,
        "my-story",
        3,
        settings=build_settings(),
    )

    assert new_draft.version_no == 2
    assert new_draft.word_count > current_draft.word_count
    assert completed_task.status == "completed"
    assert completed_task.metadata_json["rewritten_chapter_draft_id"] == str(new_draft.id)
    assert chapter.status == "review"
