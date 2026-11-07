"""Instruction 44: external review packaging, verification and reporting."""

import json

import pytest

from tools.review_pack import create_reading_pack, parse_chapter_spec
from tools.review_runner import EDITOR_QUESTIONS, READER_QUESTIONS, review_book, select_reviewer


def _book(root):
    (root / "final").mkdir(parents=True)
    (root / "chapters").mkdir()
    (root / "story_bible.yaml").write_text(
        "title: 《匿名测试书》\nmodel: MiniMax-M3\ninternal_check: should-not-leak\n",
        encoding="utf-8",
    )
    for number, title, body in (
        (1, "门后", "第一章正文。"),
        (2, "雨夜", "第二章正文。"),
        (3, "回声", "第三章正文。"),
    ):
        (root / f"chapters/{number:03d}.json").write_text(
            json.dumps({"title": title, "model": "internal"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (root / f"final/chapter-{number:03d}.md").write_text(
            f"# 第{number}章 错误的内部标题\n\n"
            "<!-- model: MiniMax-M3 -->\n"
            "[内部状态：去 AI 未完成]\n"
            "> 检查标记：unknown\n"
            f"{body}\n",
            encoding="utf-8",
        )
    return root


def test_parse_chapter_spec_supports_ranges_and_deduplicates():
    assert parse_chapter_spec("3,1-2,2,5") == [1, 2, 3, 5]
    with pytest.raises(ValueError, match="章段"):
        parse_chapter_spec("3-1")


def test_anonymous_pack_contains_only_reader_facing_fields(tmp_path):
    book = _book(tmp_path / "book")
    output = tmp_path / "run" / "reading-pack.md"

    result = create_reading_pack(book, output=output, chapter_spec="2-3")

    text = output.read_text(encoding="utf-8")
    assert result["chapters"] == [2, 3]
    assert text.startswith("# 匿名测试书\n")
    assert "## 第2章 雨夜" in text
    assert "## 第3章 回声" in text
    assert "第一章正文" not in text
    assert "第二章正文。" in text
    for forbidden in ("MiniMax", "内部", "检查标记", "unknown", str(book), "错误的内部标题"):
        assert forbidden not in text


def test_anonymous_pack_must_be_written_under_system_temp(tmp_path):
    book = _book(tmp_path / "book")
    with pytest.raises(ValueError, match="系统临时目录"):
        create_reading_pack(book, output="/var/not-system-temp/review.md")


def test_reviewer_selection_prefers_a_configured_different_family(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            (
                "BESTSELLER__LLM__WRITER__MODEL=MiniMax-M3",
                "BESTSELLER__LLM__WRITER__API_BASE=https://minimax.example/v1",
                "BESTSELLER__LLM__WRITER__API_KEY_ENV=MINIMAX_API_KEY",
                "MINIMAX_API_KEY=writer-secret",
                "BESTSELLER__LLM__CRITIC__MODEL=deepseek-chat",
                "BESTSELLER__LLM__CRITIC__API_BASE=https://deepseek.example/v1",
                "BESTSELLER__LLM__CRITIC__API_KEY_ENV=DEEPSEEK_API_KEY",
                "DEEPSEEK_API_KEY=judge-secret",
            )
        ),
        encoding="utf-8",
    )

    reviewer = select_reviewer(env)

    assert reviewer.model == "deepseek-chat"
    assert reviewer.family == "deepseek"
    assert reviewer.same_family is False
    assert "secret" not in repr(reviewer)


class _FakeClient:
    def __init__(self, output, reviewer, prefix):
        self.model = reviewer.model
        self.calls = []
        self.output = output

    def stage(self, name, prompt, schema=None, **kwargs):
        self.calls.append((name, prompt, schema, kwargs))
        chapter = int(name.rsplit("-", 1)[-1])
        return {
            "claims": [
                {
                    "question": "节奏",
                    "conclusion": f"第{chapter}章节奏偏慢",
                    "severity": "medium",
                    "chapter": chapter,
                    "quote": "正文",
                    "subject": "",
                    "action": "",
                },
                {
                    "question": "人物",
                    "conclusion": "人物没记住",
                    "severity": "low",
                    "chapter": chapter,
                    "quote": "",
                    "subject": "",
                    "action": "",
                },
            ],
            "impressions": [],
            "abandon_chapter": chapter,
            "remembered_characters": [{"name": "阿甲", "description": "谨慎"}],
            "signable": False,
        }


def test_two_perspectives_are_fixed_segmented_and_quote_less_claims_become_impressions(tmp_path):
    book = _book(tmp_path / "book")
    for number in range(4, 13):
        (book / f"chapters/{number:03d}.json").write_text(
            json.dumps({"title": f"章{number}"}, ensure_ascii=False), encoding="utf-8"
        )
        (book / f"final/chapter-{number:03d}.md").write_text(f"第{number}章正文。", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(
        "BESTSELLER__LLM__WRITER__MODEL=MiniMax-M3\n"
        "BESTSELLER__LLM__WRITER__API_BASE=https://minimax.example/v1\n"
        "BESTSELLER__LLM__WRITER__API_KEY_ENV=MINIMAX_API_KEY\n"
        "MINIMAX_API_KEY=dummy-key\n",
        encoding="utf-8",
    )

    result = review_book(
        book,
        env,
        output=tmp_path / "review-run",
        chapter_spec="1-12",
        segment_chapters=5,
        client_factory=_FakeClient,
    )

    assert [segment["chapters"] for segment in result["segments"]] == [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12]]
    assert len(result["reviews"]) == 6
    assert {review["perspective"] for review in result["reviews"]} == {"editor", "reader"}
    assert len(result["claims"]) == 6
    assert len(result["impressions"]) == 6
    assert all(item["reason"] == "未附引文" for item in result["impressions"])
    assert all(len(item["quote"]) <= 60 for item in result["claims"])
    assert all(question in result["prompts"]["editor"] for question in EDITOR_QUESTIONS)
    assert all(question in result["prompts"]["reader"] for question in READER_QUESTIONS)
    persisted = json.loads((tmp_path / "review-run/raw-reviews.json").read_text(encoding="utf-8"))
    assert persisted["reviewer"]["same_family"] is True
    assert "dummy-key" not in json.dumps(persisted, ensure_ascii=False)
