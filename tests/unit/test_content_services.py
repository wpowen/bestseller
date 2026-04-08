from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

from bestseller.services.drafts import (
    _format_chapter_heading,
    build_scene_draft_prompts,
    count_words,
    has_meta_leak,
    render_chapter_draft_markdown,
    render_scene_draft_markdown,
    sanitize_novel_markdown_content,
    strip_scaffolding_echoes,
)
from bestseller.services.exports import (
    build_markdown_reading_stats,
    build_docx_bytes,
    build_epub_bytes,
    build_project_markdown,
    build_pdf_bytes,
    markdown_to_html,
    markdown_to_plain_text,
    write_binary_output,
    write_markdown_output,
)


pytestmark = pytest.mark.unit


def test_count_words_handles_mixed_chinese_and_english() -> None:
    assert count_words("你好 world chapter 1") == 5


def test_render_scene_draft_markdown_is_non_prose_placeholder() -> None:
    """The scene-writer fallback must never return narrative prose.

    Historically this function returned six paragraphs of templated Chinese
    prose ("XX 被推入《项目名》第 N 章的核心冲突。叙事采用 third-limited
    视角…") which then leaked into published chapters when the LLM scene
    writer failed. The contract now: return an HTML comment placeholder
    that the sanitizer strips to empty string.
    """
    project = SimpleNamespace(title="长夜巡航", slug="chang-ye-xun-hang")
    chapter = SimpleNamespace(chapter_number=1, chapter_goal="调查失踪舰队", title="失准星图")
    scene = SimpleNamespace(
        scene_number=1,
        title="封港命令",
        participants=["沈砚", "港务官"],
        purpose={"story": "抛出禁令任务", "emotion": "压迫感"},
        time_label="深夜",
        entry_state={"location": "星港"},
        exit_state={"risk": "任务无法拒绝"},
        scene_type="setup",
        target_word_count=1000,
    )
    style_guide = SimpleNamespace(pov_type="third-limited", tone_keywords=["冷峻", "紧张"])

    content = render_scene_draft_markdown(project, chapter, scene, style_guide)

    # Must look like an HTML comment, never prose
    assert content.startswith("<!--")
    assert content.endswith("-->")
    # Must contain the identifying metadata so reviewers can trace failures
    assert "chapter=1" in content
    assert "scene=1" in content
    assert "沈砚、港务官" in content
    # Must NOT contain the legacy template sentences
    assert "被推入" not in content
    assert "third-limited" not in content
    assert "核心冲突" not in content
    assert "剧情任务" not in content


def test_render_scene_draft_markdown_context_flows_via_llm_prompt() -> None:
    """Story-bible context must reach the LLM prompt, not the fallback markdown.

    Previously the render function embedded the story bible into a seed
    paragraph that doubled as a fallback. Now the fallback is a placeholder,
    so all the rich context must flow through ``build_scene_draft_prompts``
    to the LLM user prompt.
    """
    project = SimpleNamespace(title="长夜巡航", slug="chang-ye-xun-hang")
    chapter = SimpleNamespace(chapter_number=1, chapter_goal="调查失踪舰队", title="失准星图")
    scene = SimpleNamespace(
        scene_number=1,
        title="封港命令",
        participants=["沈砚", "港务官"],
        purpose={"story": "抛出禁令任务", "emotion": "压迫感"},
        time_label="深夜",
        entry_state={"location": "星港"},
        exit_state={"risk": "任务无法拒绝"},
        scene_type="setup",
        target_word_count=1000,
    )
    style_guide = SimpleNamespace(pov_type="third-limited", tone_keywords=["冷峻", "紧张"])
    story_bible_context = {
        "logline": "被放逐的导航员调查被篡改的航线。",
        "themes": ["真相", "牺牲"],
        "volume": {"goal": "找到第一份铁证", "obstacle": "封港追捕"},
        "world_rules": [
            {
                "name": "航道记录优先",
                "description": "官方航图高于个人证词",
                "story_consequence": "主角无法直接翻案",
            }
        ],
        "participants": [
            {
                "name": "沈砚",
                "role": "protagonist",
                "goal": "找证据",
                "arc_state": "逃避真相",
                "power_tier": "导航员",
                "emotional_state": "压抑",
            }
        ],
        "relationships": [{"relationship_type": "旧搭档", "tension_summary": "误会仍未解开"}],
    }

    content = render_scene_draft_markdown(project, chapter, scene, style_guide, story_bible_context)
    _, user_prompt = build_scene_draft_prompts(project, chapter, scene, style_guide, story_bible_context)

    # Fallback placeholder is minimal and HTML-commented
    assert content.startswith("<!--")
    # Rich context still reaches the LLM prompt
    assert "故事圣经约束" in user_prompt
    assert "本卷目标：找到第一份铁证" in user_prompt


def test_scene_draft_prompt_includes_recent_context_sections() -> None:
    project = SimpleNamespace(title="长夜巡航", slug="chang-ye-xun-hang")
    chapter = SimpleNamespace(chapter_number=2, chapter_goal="沿线追查巡逻舰", title="静默航道")
    scene = SimpleNamespace(
        scene_number=1,
        title="旧搭档回舰",
        participants=["沈砚", "顾临"],
        purpose={"story": "建立旧关系张力", "emotion": "戒备和未尽之言"},
        time_label="清晨",
        entry_state={"distance": "僵持"},
        exit_state={"alliance": "暂时合作"},
        scene_type="reunion",
        target_word_count=1000,
    )
    style_guide = SimpleNamespace(pov_type="third-limited", tone_keywords=["冷峻", "紧张"])
    story_bible_context = {"logline": "调查被篡改的航线。", "themes": ["真相"]}
    recent_scene_summaries = [
        {
            "chapter_number": 1,
            "scene_number": 2,
            "scene_title": "偏移的航标",
            "summary": "沈砚发现第一处星图异常。",
        }
    ]
    recent_timeline_events = [
        {
            "story_time_label": "昨夜",
            "event_name": "发现异常",
            "consequences": ["航标日志被改写", "调查升级"],
        }
    ]
    participant_facts = [
        {
            "subject_label": "沈砚",
            "predicate": "last_known_state",
            "value": {"state": {"emotion": "警觉"}} ,
        }
    ]

    # render_scene_draft_markdown is the LLM fallback placeholder. Rich
    # context (近期剧情回顾 / 时间线节点 / 角色事实) must instead be verified
    # on the LLM user_prompt produced by build_scene_draft_prompts.
    _, user_prompt = build_scene_draft_prompts(
        project,
        chapter,
        scene,
        style_guide,
        story_bible_context,
        [],
        recent_scene_summaries,
        recent_timeline_events,
        participant_facts,
    )

    assert "近期剧情回顾" in user_prompt
    assert "已知时间线节点" in user_prompt
    assert "参与角色当前可见事实" in user_prompt
    assert "不得泄露未来章节才会揭示的信息" in user_prompt


def test_scene_draft_prompt_includes_narrative_graph_sections() -> None:
    project = SimpleNamespace(title="长夜巡航", slug="chang-ye-xun-hang")
    chapter = SimpleNamespace(chapter_number=2, chapter_goal="推进调查", title="静默航道")
    scene = SimpleNamespace(
        scene_number=1,
        title="旧搭档回舰",
        participants=["沈砚", "顾临"],
        purpose={"story": "推进主线调查", "emotion": "紧绷"},
        time_label="清晨",
        entry_state={"risk": "高"},
        exit_state={"risk": "更高"},
        scene_type="reunion",
        target_word_count=1000,
    )
    style_guide = SimpleNamespace(pov_type="third-limited", tone_keywords=["冷峻", "紧张"])

    # Narrative graph context (arcs / clues / emotion tracks / antagonist
    # plans / contracts) must reach the LLM user prompt. The render helper
    # itself only returns a placeholder after the template-prose fix.
    _, user_prompt = build_scene_draft_prompts(
        project,
        chapter,
        scene,
        style_guide,
        {"logline": "调查被篡改的航线。"},
        [],
        [],
        [],
        [],
        [{"arc_type": "main_plot", "name": "主线推进", "promise": "调查被篡改的航线。"}],
        [{"arc_code": "main_plot", "beat_kind": "scene_push", "summary": "本场推进主线", "emotional_shift": "紧绷"}],
        [{"clue_code": "clue-001", "label": "异常航标", "description": "异常航标暗示有人留信。"}],
        [{"payoff_code": "payoff-001", "label": "求救信号兑现", "description": "求救信号指向日志库入口。"}],
        {"contract_summary": "本章要抛出更大的异常。", "core_conflict": "拿到线索", "closing_hook": "更大风险逼近"},
        {"contract_summary": "本场必须抛出异常航标。", "core_conflict": "交换信息", "tail_hook": "新坐标浮现"},
        [{"node_path": "/chapters/002/contract", "node_type": "chapter_contract", "summary": "本章要抛出更大的异常。"}],
        [{"track_type": "bond", "title": "沈砚 / 顾临 关系线", "summary": "双方暂时合作但信任未恢复。", "trust_level": 0.42, "attraction_level": 0.1, "conflict_level": 0.7, "intimacy_stage": "push_pull"}],
        [{"threat_type": "volume_pressure", "title": "第1卷反派升级", "goal": "封锁调查路径", "current_move": "切断证据链", "next_countermove": "围堵主角"}],
    )

    assert "当前叙事线与节拍" in user_prompt
    assert "伏笔与兑现约束" in user_prompt
    assert "chapter/scene contract" in user_prompt
    assert "关系与情绪推进约束" in user_prompt
    assert "反派推进约束" in user_prompt
    assert "deterministic path retrieval" in user_prompt
    assert "必须覆盖 scene contract" in user_prompt


def test_scene_draft_prompt_includes_writing_profile_and_serial_rules() -> None:
    project = SimpleNamespace(
        title="末日零点仓库",
        metadata_json={
            "writing_profile": {
                "market": {
                    "platform_target": "番茄小说",
                    "prompt_pack_key": "apocalypse-supply-chain",
                    "reader_promise": "开篇即进入末日倒计时与囤货优势展示，前三章连续给出危机升级与短回报。",
                    "selling_points": ["重生回档", "未来商城", "资源碾压"],
                    "trope_keywords": ["末日", "囤货", "系统", "打脸反杀"],
                    "opening_strategy": "第一屏直接抛出末日倒计时、主角知道未来、资源窗口马上关闭。",
                    "chapter_hook_strategy": "每章末给新的资源机会、规则异变或敌人反压。",
                },
                "character": {
                    "protagonist_archetype": "先知型求生者",
                    "golden_finger": "未来拼单商城",
                    "growth_curve": "资源碾压 -> 势力扩张 -> 真相破局",
                },
                "world": {
                    "worldbuilding_density": "medium",
                    "info_reveal_strategy": "先冲突后解释，背景信息掺在行动和交易里释放。",
                },
            }
        },
    )
    chapter = SimpleNamespace(chapter_number=1, chapter_goal="建立主角抢占先机的优势", title="零点前夜")
    scene = SimpleNamespace(
        scene_number=1,
        title="未来订单",
        participants=["林昼"],
        purpose={"story": "展示主角提前囤货并察觉末日倒计时", "emotion": "紧迫与隐秘兴奋"},
        time_label="末日前三天",
        entry_state={"stock": "紧缺"},
        exit_state={"advantage": "建立第一批资源优势"},
        scene_type="hook",
        target_word_count=1400,
    )
    style_guide = SimpleNamespace(pov_type="third-limited", tone_keywords=["狠", "快", "压迫感"])

    _, user_prompt = build_scene_draft_prompts(project, chapter, scene, style_guide)

    assert "平台与读者承诺" in user_prompt
    assert "番茄小说" in user_prompt
    assert "Prompt Pack" in user_prompt
    assert "末日囤货升级流" in user_prompt
    assert "重生回档" in user_prompt
    assert "未来拼单商城" in user_prompt
    assert "章节尾部必须留下强迫读者继续阅读的问题、威胁或利益诱因" in user_prompt
    assert "开篇要尽快亮出主角差异化优势、核心异变、短期利益与即时危险" in user_prompt
    assert "抢资源" in user_prompt or "资源差" in user_prompt


def test_render_chapter_draft_markdown_combines_scene_drafts() -> None:
    """Chapter header uses canonical '第N章：子标题' format (single prefix)."""
    chapter = SimpleNamespace(chapter_number=3, title="静默航道", chapter_goal="追查失踪巡逻舰")
    scene_drafts = [
        SimpleNamespace(
            content_md=(
                "## 场景 1：旧搭档回舰\n\n"
                "顾临把旧证件按在桌上，一言不发。"
            )
        ),
        SimpleNamespace(
            content_md=(
                "## 场景 2：信号残响\n\n"
                "舱壁深处传来断续的回波。"
            )
        ),
    ]

    content = render_chapter_draft_markdown(chapter, scene_drafts)

    # Canonical heading: exactly one "第3章" prefix, colon separator, subtitle.
    assert "# 第3章：静默航道" in content
    assert "# 第3章 第3章" not in content  # no double prefix
    assert "# 第3章 静默航道" not in content  # old (space-separated) format retired
    # Scene body text preserved, scaffold headings dropped by sanitizer.
    assert "顾临把旧证件按在桌上" in content
    assert "舱壁深处传来断续的回波" in content
    assert "## 场景 1" not in content
    assert "## 场景 2" not in content


def test_render_chapter_draft_markdown_handles_prefixed_chapter_title() -> None:
    """If ``chapter.title`` already contains '第N章：' the renderer must not double-prefix."""
    chapter = SimpleNamespace(
        chapter_number=1,
        title="第1章：零点前的抢购",  # pre-prefixed form
        chapter_goal="抢占第一批物资",
    )
    scene_drafts = [
        SimpleNamespace(content_md="程彻猛地从床上弹起来。"),
    ]

    content = render_chapter_draft_markdown(chapter, scene_drafts)

    assert content.count("第1章") == 1
    assert "# 第1章：零点前的抢购" in content
    assert "# 第1章 第1章" not in content
    assert "程彻猛地从床上弹起来" in content


def test_format_chapter_heading_handles_all_title_variants() -> None:
    """Regression tests for the '第N章 第N章' double-prefix bug.

    Covers every title shape we have observed in historical project data.
    """
    # Already prefixed with colon — use as-is.
    assert _format_chapter_heading(1, "第1章：零点前的抢购") == "# 第1章：零点前的抢购"
    # Already prefixed with a space separator — normalize to colon.
    assert _format_chapter_heading(1, "第1章 零点前的抢购") == "# 第1章：零点前的抢购"
    # Bare subtitle — renderer attaches the prefix exactly once.
    assert _format_chapter_heading(1, "零点前的抢购") == "# 第1章：零点前的抢购"
    # Only the chapter number with no subtitle.
    assert _format_chapter_heading(1, "第1章") == "# 第1章"
    # Empty / None fall back to the bare chapter number.
    assert _format_chapter_heading(1, None) == "# 第1章"
    assert _format_chapter_heading(1, "") == "# 第1章"
    assert _format_chapter_heading(1, "   ") == "# 第1章"
    # Stale numbering in the stored title must not survive.
    assert _format_chapter_heading(3, "第15章：无关") == "# 第3章：无关"


def test_sanitize_novel_markdown_content_strips_structured_metadata_lines() -> None:
    content = """## 场景 3：陷阱合拢

**scene_summary:** 主角发现真相，但也意识到自己已掉进更大的陷阱。

**core_conflict:** 主角要带走证据，反派要切断他的退路。

**emotional_shift:** 从谨慎试探 -> 短暂兴奋 -> 巨大寒意

祁镇的声音从废弃机柜后面传来，像一根冰冷的针，缓慢刺进末日零的后颈。
"""

    cleaned = sanitize_novel_markdown_content(content)

    assert "scene_summary" not in cleaned
    assert "core_conflict" not in cleaned
    assert "emotional_shift" not in cleaned
    assert "祁镇的声音从废弃机柜后面传来" in cleaned


def test_strip_scaffolding_echoes_removes_duplicate_chapter_markers() -> None:
    content = (
        "第1章 第2场\n\n"
        "沈砚在封港通告亮起前就察觉到了不对。\n\n"
        "第3章 第3章：碰撞\n\n"
        "他把匕首收进袖口。"
    )

    cleaned = strip_scaffolding_echoes(content)

    assert "第1章 第2场" not in cleaned
    assert "第3章 第3章" not in cleaned
    assert "沈砚在封港通告亮起前就察觉到了不对。" in cleaned
    assert "他把匕首收进袖口。" in cleaned


def test_strip_scaffolding_echoes_removes_prose_wrapped_rewrite_plan() -> None:
    content = (
        "第15章开场，程彻、周远重新被推回《用倒计时、资源窗口、重生信息差和末日商城》"
        "第15章的核心冲突。叙事仍采用 third-limited 视角，但会更强调 狠、快、压迫感 的压迫感。\n"
        "这一版重写围绕\u201c承接上章后果并给出当前行动目标\u201d展开，"
        "并把\u201c持续拉高压力和不确定性\u201d"
        "真正落实到动作、停顿、呼吸和目光变化里。\n\n"
        "程彻把步话机拧到最低档，手指死死压住按键，等周远的呼吸频率慢下来。"
    )

    cleaned = strip_scaffolding_echoes(content)

    assert "第15章开场" not in cleaned
    assert "third-limited" not in cleaned
    assert "这一版重写" not in cleaned
    assert "叙事仍采用" not in cleaned
    assert "程彻把步话机拧到最低档" in cleaned


def test_strip_scaffolding_echoes_preserves_legitimate_chapter_reference() -> None:
    # A character legitimately mentions a chapter inside dialogue — that
    # shouldn't be erased. Only standalone scaffolding gets stripped.
    content = (
        "沈砚翻到书签夹住的位置，低声说：\"我刚刚才看完第三章，还没喘过气来。\"\n\n"
        "顾临笑了一下，没接话。"
    )

    cleaned = strip_scaffolding_echoes(content)

    assert "第三章" in cleaned
    assert "沈砚翻到书签夹住的位置" in cleaned
    assert "顾临笑了一下" in cleaned


def test_has_meta_leak_detects_rewrite_plan_phrases() -> None:
    leaked = (
        "这一版重写围绕承接上章后果展开。"
        "叙事仍采用 third-limited 视角，但会更强调压迫感。"
    )
    clean = "沈砚在封港通告亮起前就察觉到了不对。他抬头看了一眼霓虹。"

    assert has_meta_leak(leaked) is True
    assert has_meta_leak(clean) is False


# ── continuity.py: hard-fact snapshot extraction ──────────────────────────────


def test_continuity_parse_extraction_payload_accepts_bare_json() -> None:
    from bestseller.services.continuity import _parse_extraction_payload

    raw = (
        '{"facts": [{"name": "末日倒计时", "value": "20", "unit": "小时",'
        ' "kind": "countdown", "notes": "本章消耗 4 小时"}]}'
    )
    facts, error = _parse_extraction_payload(raw)
    assert error is None
    assert len(facts) == 1
    assert facts[0].name == "末日倒计时"
    assert facts[0].value == "20"
    assert facts[0].unit == "小时"
    assert facts[0].kind == "countdown"


def test_continuity_parse_extraction_payload_accepts_fenced_json() -> None:
    from bestseller.services.continuity import _parse_extraction_payload

    raw = (
        "以下是本章的硬事实抽取结果：\n\n"
        "```json\n"
        '{"facts": [{"name": "主角等级", "value": "3", "kind": "level"}]}\n'
        "```\n"
    )
    facts, error = _parse_extraction_payload(raw)
    assert error is None
    assert len(facts) == 1
    assert facts[0].name == "主角等级"
    assert facts[0].kind == "level"


def test_continuity_parse_extraction_payload_coerces_unknown_kind_to_other() -> None:
    from bestseller.services.continuity import _parse_extraction_payload

    raw = '{"facts": [{"name": "氛围", "value": "紧张", "kind": "atmosphere"}]}'
    facts, error = _parse_extraction_payload(raw)
    assert error is None
    assert len(facts) == 1
    assert facts[0].kind == "other"


def test_continuity_parse_extraction_payload_rejects_non_json_response() -> None:
    from bestseller.services.continuity import _parse_extraction_payload

    facts, error = _parse_extraction_payload("抱歉，我无法完成这个任务。")
    assert facts == []
    assert error is not None


def test_continuity_parse_extraction_payload_skips_malformed_fact_entries() -> None:
    from bestseller.services.continuity import _parse_extraction_payload

    raw = (
        '{"facts": ['
        '{"name": "", "value": "x", "kind": "countdown"},'
        '{"name": "位置", "kind": "location"},'
        '{"name": "背包饼干", "value": "5", "kind": "inventory_count"}'
        "]}"
    )
    facts, error = _parse_extraction_payload(raw)
    assert error is None
    assert len(facts) == 1
    assert facts[0].name == "背包饼干"


def test_continuity_render_hard_fact_snapshot_block_is_empty_for_none() -> None:
    from bestseller.services.continuity import render_hard_fact_snapshot_block

    assert render_hard_fact_snapshot_block(None) == ""


def test_continuity_render_hard_fact_snapshot_block_formats_all_fields() -> None:
    from bestseller.domain.context import (
        ChapterStateSnapshotContext,
        HardFactContext,
    )
    from bestseller.services.continuity import render_hard_fact_snapshot_block

    snapshot = ChapterStateSnapshotContext(
        chapter_number=14,
        facts=[
            HardFactContext(
                name="末日倒计时",
                value="20",
                unit="小时",
                kind="countdown",
                notes="本章消耗 4 小时",
            ),
            HardFactContext(
                name="背包饼干",
                value="5",
                kind="inventory_count",
                subject="主角",
            ),
        ],
    )
    block = render_hard_fact_snapshot_block(snapshot)
    assert "第 14 章末" in block
    assert "末日倒计时: 20 小时" in block
    assert "// 本章消耗 4 小时" in block
    assert "[主角] 背包饼干: 5" in block
    assert "任何数值/位置/物品变化都必须在本章正文里给出读者可见的触发事件" in block


def test_drafts_render_hard_fact_snapshot_section_handles_empty_input() -> None:
    from bestseller.services.drafts import _render_hard_fact_snapshot_section

    assert _render_hard_fact_snapshot_section(None) == ""
    assert _render_hard_fact_snapshot_section({}) == ""
    assert _render_hard_fact_snapshot_section({"facts": []}) == ""


def test_drafts_render_hard_fact_snapshot_section_skips_invalid_fact_entries() -> None:
    from bestseller.services.drafts import _render_hard_fact_snapshot_section

    snapshot = {
        "chapter_number": 2,
        "facts": [
            {"name": "末日倒计时", "value": "72", "unit": "小时", "kind": "countdown"},
            {"name": "", "value": "invalid"},  # dropped — empty name
            {"value": "noname"},  # dropped — no name
            "not a dict",  # dropped — wrong type
            {"name": "无value"},  # dropped — no value
        ],
    }
    block = _render_hard_fact_snapshot_section(snapshot)
    assert "末日倒计时: 72 小时" in block
    assert "invalid" not in block
    assert "noname" not in block
    assert "无value" not in block


def test_render_chapter_draft_markdown_strips_scene_metadata_artifacts() -> None:
    chapter = SimpleNamespace(chapter_number=3, title="静默航道", chapter_goal="追查失踪巡逻舰")
    scene_drafts = [
        SimpleNamespace(
            content_md=(
                "## 场景 1：旧搭档回舰\n\n"
                "**scene_summary:** 本场说明双方重新接触。\n\n"
                "顾临没有先开口，只是把旧证件按在桌上。"
            )
        ),
        SimpleNamespace(content_md="## 场景 2：信号残响\n\n舱壁深处传来断续的回波。"),
    ]

    content = render_chapter_draft_markdown(chapter, scene_drafts)

    assert "scene_summary" not in content
    assert "顾临没有先开口" in content
    assert "舱壁深处传来断续的回波" in content


def test_sanitize_strips_rewrite_template_prose() -> None:
    """The sanitizer must strip the legacy render_rewritten_* fallback prose.

    Every one of these sentences originated in the template prose generators
    in ``reviews.render_rewritten_scene_markdown`` /
    ``reviews.render_rewritten_chapter_markdown`` and leaked into chapters
    2, 3, 5, 7-13, 15, 20, 25 of the apocalypse-supply-1775626373 output.
    Catching them at the sanitizer layer protects legacy content and acts as
    a defense-in-depth after the fallbacks themselves were replaced.
    """
    leaked = (
        "这一刻，程彻重新被推回《末日商城》第13章的核心冲突。"
        "叙事仍采用 third-limited 视角，但会更强调 狠、快、压迫感 的压迫感。\n\n"
        "这一版重写围绕\u201c让主角拿到一条新线索，同时付出新的代价\u201d展开，"
        "并把\u201c把悬念和敌意推高到下一层\u201d真正落实到动作、停顿、呼吸和目光变化里。\n\n"
        "金属舱壁传来的冷意、警报灯反复切换的微红、以及每一次视线交锋后更明显的戒备。"
        "人物说出口的话和没有说出口的话同时构成冲突。\n\n"
        "程彻抓起挂在门后的黑色双肩包。"
    )

    cleaned = sanitize_novel_markdown_content(leaked)

    # All six rewrite-template sentences must be gone
    assert "重新被推回《" not in cleaned
    assert "叙事仍采用" not in cleaned
    assert "third-limited" not in cleaned
    assert "这一版重写围绕" not in cleaned
    assert "金属舱壁传来的冷意" not in cleaned
    assert "人物说出口的话和没有说出口的话" not in cleaned
    # Real prose survives
    assert "程彻抓起挂在门后的黑色双肩包" in cleaned


def test_sanitize_strips_chapter_phase_prefix_prose() -> None:
    """Lines that open with ``第N章(开场|中段|结尾)，`` are template leakage."""
    leaked = (
        "第13章中段，程彻重新被推回《用倒计时、资源窗口、重生信息差和末日商城》"
        "第13章的核心冲突。\n\n"
        "程彻拉开抽屉。"
    )
    cleaned = sanitize_novel_markdown_content(leaked)
    assert "第13章中段" not in cleaned
    assert "程彻拉开抽屉" in cleaned


def test_sanitize_strips_html_comment_markers() -> None:
    """Fallback placeholders must never reach published chapters."""
    with_markers = (
        "<!-- scene-draft-fallback project=\"demo\" chapter=1 scene=1 -->\n\n"
        "程彻的心脏在胸腔里擂鼓。\n\n"
        "<!-- rewrite-chapter-fallback project=\"demo\" chapter=5 reason=\"llm-unavailable\" -->"
    )
    cleaned = sanitize_novel_markdown_content(with_markers)
    assert "<!--" not in cleaned
    assert "scene-draft-fallback" not in cleaned
    assert "rewrite-chapter-fallback" not in cleaned
    assert "程彻的心脏在胸腔里擂鼓" in cleaned


def test_sanitize_strips_rewrite_chapter_wrapper_sentences() -> None:
    """The chapter-rewrite fallback's wrapper sentences must never leak."""
    leaked = (
        "上一阶段留下的局势仍压在众人心头：昨夜的追车余波未散。"
        "这一章不再只是承接，而是要把冲突继续推向更高层级。\n\n"
        "程彻在黑市巷口等周远。\n\n"
        "章节收束时，追溯执行组不再只是背景，而变成下一章必须立刻面对的现实。"
    )
    cleaned = sanitize_novel_markdown_content(leaked)
    assert "上一阶段留下的局势仍压在众人心头" not in cleaned
    assert "这一章不再只是承接" not in cleaned
    assert "章节收束时" not in cleaned
    assert "程彻在黑市巷口等周远" in cleaned


def test_build_project_markdown_combines_chapters() -> None:
    project = SimpleNamespace(title="长夜巡航", genre="science-fantasy")
    chapter_payloads = [
        (
            SimpleNamespace(chapter_number=1),
            SimpleNamespace(
                content_md=(
                    "# 第1章 失准星图\n\n"
                    "**core_conflict:** 主角必须先活下来。\n\n"
                    "沈砚在封港通告亮起前就察觉到了不对。"
                )
            ),
        ),
        (SimpleNamespace(chapter_number=2), SimpleNamespace(content_md="# 第2章 静默航道")),
    ]

    content = build_project_markdown(project, chapter_payloads)

    assert "# 长夜巡航" in content
    assert "# 第1章 失准星图" in content
    assert "# 第2章 静默航道" in content
    assert "core_conflict" not in content
    assert "沈砚在封港通告亮起前就察觉到了不对" in content


def test_write_markdown_output_creates_file_and_checksum(tmp_path: Path) -> None:
    output_path = tmp_path / "output" / "chapter-001.md"

    storage_uri, checksum = write_markdown_output(output_path, "# Chapter 1")

    assert output_path.exists() is True
    assert storage_uri.endswith("chapter-001.md")
    assert len(checksum) == 64


def test_markdown_to_html_renders_basic_structure() -> None:
    html = markdown_to_html("# 标题\n\n## 小节\n\n> 引文\n\n正文内容")

    assert "<h1>标题</h1>" in html
    assert "<h2>小节</h2>" in html
    assert "<blockquote>" in html
    assert "<p>引文</p>" in html
    assert "<p>正文内容</p>" in html


def test_markdown_to_plain_text_strips_markers() -> None:
    text = markdown_to_plain_text("# 标题\n\n> 引文\n\n- 条目")

    assert "标题" in text
    assert "引文" in text
    assert "条目" in text


def test_build_markdown_reading_stats_counts_cjk_and_latin_text() -> None:
    stats = build_markdown_reading_stats("# 标题\n\n正文内容 Alpha beta 123")

    assert stats["word_count"] == 9
    assert stats["character_count"] == 18
    assert stats["paragraph_count"] == 2
    assert stats["estimated_read_minutes"] == 1


def test_build_docx_bytes_creates_zip_package() -> None:
    payload = build_docx_bytes("我的书", "# 标题\n\n正文")

    assert payload[:2] == b"PK"
    assert b"word/document.xml" in payload


def test_build_epub_bytes_creates_zip_package() -> None:
    payload = build_epub_bytes("我的书", "# 标题\n\n正文")

    assert payload[:2] == b"PK"
    assert b"application/epub+zip" in payload


def test_write_binary_output_creates_file_and_checksum(tmp_path: Path) -> None:
    output_path = tmp_path / "output" / "project.docx"

    storage_uri, checksum = write_binary_output(output_path, b"binary-content")

    assert output_path.exists() is True
    assert storage_uri.endswith("project.docx")
    assert len(checksum) == 64


def test_build_pdf_bytes_requires_optional_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("reportlab"):
            raise ImportError("reportlab is intentionally unavailable in this test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="reportlab"):
        build_pdf_bytes("我的书", "# 标题\n\n正文")
