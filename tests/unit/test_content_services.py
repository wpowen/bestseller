from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

from bestseller.services.drafts import (
    build_scene_draft_prompts,
    count_words,
    render_chapter_draft_markdown,
    render_scene_draft_markdown,
)
from bestseller.services.exports import (
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


def test_render_scene_draft_markdown_contains_scene_structure() -> None:
    project = SimpleNamespace(title="长夜巡航")
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

    assert "## 场景 1：封港命令" in content
    assert "抛出禁令任务" in content
    assert "沈砚、港务官" in content


def test_render_scene_draft_markdown_uses_story_bible_context() -> None:
    project = SimpleNamespace(title="长夜巡航")
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

    assert "长篇约束" in content
    assert "关键世界规则" in content
    assert "故事圣经约束" in user_prompt
    assert "本卷目标：找到第一份铁证" in user_prompt


def test_scene_draft_prompt_includes_recent_context_sections() -> None:
    project = SimpleNamespace(title="长夜巡航")
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

    content = render_scene_draft_markdown(
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

    assert "近期剧情回顾" in content
    assert "已知时间线节点" in content
    assert "参与角色可见事实" in content
    assert "不得泄露未来章节才会揭示的信息" in user_prompt


def test_scene_draft_prompt_includes_narrative_graph_sections() -> None:
    project = SimpleNamespace(title="长夜巡航")
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

    content = render_scene_draft_markdown(
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
    )
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
    )

    assert "当前叙事线与节拍" in content
    assert "伏笔与兑现约束" in content
    assert "合同式写作约束" in content
    assert "叙事树上下文" in content
    assert "chapter/scene contract" in user_prompt
    assert "deterministic path retrieval" in user_prompt
    assert "必须覆盖 scene contract" in user_prompt


def test_render_chapter_draft_markdown_combines_scene_drafts() -> None:
    chapter = SimpleNamespace(chapter_number=3, title="静默航道", chapter_goal="追查失踪巡逻舰")
    scene_drafts = [
        SimpleNamespace(content_md="## 场景 1：旧搭档回舰"),
        SimpleNamespace(content_md="## 场景 2：信号残响"),
    ]

    content = render_chapter_draft_markdown(chapter, scene_drafts)

    assert "# 第3章 静默航道" in content
    assert "## 场景 1：旧搭档回舰" in content
    assert "## 场景 2：信号残响" in content


def test_build_project_markdown_combines_chapters() -> None:
    project = SimpleNamespace(title="长夜巡航", genre="science-fantasy")
    chapter_payloads = [
        (SimpleNamespace(chapter_number=1), SimpleNamespace(content_md="# 第1章 失准星图")),
        (SimpleNamespace(chapter_number=2), SimpleNamespace(content_md="# 第2章 静默航道")),
    ]

    content = build_project_markdown(project, chapter_payloads)

    assert "# 长夜巡航" in content
    assert "# 第1章 失准星图" in content
    assert "# 第2章 静默航道" in content


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
    assert "<blockquote><p>引文</p></blockquote>" in html
    assert "<p>正文内容</p>" in html


def test_markdown_to_plain_text_strips_markers() -> None:
    text = markdown_to_plain_text("# 标题\n\n> 引文\n\n- 条目")

    assert "标题" in text
    assert "引文" in text
    assert "条目" in text


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
