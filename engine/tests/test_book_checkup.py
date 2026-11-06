"""Read-only diagnostics cover legacy and structured books without model calls."""

import json
from pathlib import Path
import subprocess
import sys

import yaml

from tools.book_checkup import check_book, write_checkup


def put(book, relative, value):
    path = book / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


def snapshot(book):
    return {str(p.relative_to(book)): p.read_bytes() for p in book.rglob("*") if p.is_file()}


def sample(book):
    bible = dict(
        volumes=[dict(number=1, start=1, end=2, location="南港"), dict(number=2, start=3, end=3, location="北城")],
        characters=[
            dict(name="顾衡", role="antagonist", identity="北城账房"),
            dict(name="阮宁", identity="码头掌柜", address="宁姐", aliases=["夜鸦"]),
        ],
        antagonists=[dict(name="顾衡", tier=1, stage=1, exit=2)],
    )
    put(book, "story_bible.yaml", yaml.safe_dump(bible, allow_unicode=True))
    bodies = ["# 脏标题\n顾衡推开门。阮宁是码头掌柜。\n她走到南港。", "北城，顾衡站在桥头。", "顾衡推门，夜鸦走了。"]
    for n, body in enumerate(bodies, 1):
        put(book, f"chapter-{n:03d}.body.md", body)
        put(book, f"chapters/{n:03d}.json", dict(number=n, title=f"问门{n}", state={}))
    return bible


def test_structured_book_reports_every_diagnostic_without_writing(tmp_path):
    sample(tmp_path)
    put(tmp_path, "chapters/001.json", dict(title="问门一", state=dict(end_location="南港", end_quote="她走到南港。")))
    put(
        tmp_path,
        "chapters/002.json",
        dict(title="问门二", state=dict(opening_location="北城", opening_quote="北城，顾衡站在桥头。")),
    )
    before = snapshot(tmp_path)
    result = check_book(tmp_path)
    assert snapshot(tmp_path) == before
    assert result["reference_only"] is True
    assert result["stages"]["count"] == 2
    assert result["stages"]["location_count"] == 2
    opponent = result["antagonists"][0]
    assert (opponent["name"], opponent["mention_count"], opponent["span"]) == ("顾衡", 3, 3)
    assert opponent["after_exit_mentions"] == [3]
    assert opponent["active_appearance_status"] == "Unknown"
    assert result["title_keywords"]["repeated"]["门"] == [1, 2, 3]
    assert result["location_breaks"]["count"] == 2
    assert [row["chapters"] for row in result["location_breaks"]["issues"]] == [[1, 2], [2, 3]]
    assert result["location_breaks"]["issues"][1]["basis"] == "shared_stage_transition"
    assert result["location_breaks"]["issues"][0]["status"] == "Inference"
    assert result["format_residue"]["heading_chapters"] == [1]
    assert any(r["name"] == "顾衡" for r in result["introductions"]["missing"])
    assert not any(r["name"] == "阮宁" for r in result["introductions"]["missing"])
    assert result["aliases"]["issues"] == []


def test_planned_locations_are_not_promoted_to_observed_story_locations(tmp_path):
    sample(tmp_path)
    put(tmp_path, "chapter-001.body.md", "阮宁停在河桥。")
    put(tmp_path, "chapters/001.json", dict(end_location="河桥", end_quote="阮宁停在河桥。"))
    result = check_book(tmp_path)
    assert result["stages"]["location_count"] == 2
    observed = result["stages"]["observed_locations"]
    assert observed["locations"] == ["河桥"] and observed["count"] == 1
    assert observed["unknown_chapters"] == [2, 3]
    assert result["stages"]["distribution"][0]["observed_end_locations"] == ["河桥"]


def test_explicit_movement_and_ungrounded_metadata_are_not_counted_as_breaks(tmp_path):
    sample(tmp_path)
    put(tmp_path, "chapter-002.body.md", "她从南港走到北城。顾衡站在桥头。")
    put(tmp_path, "chapters/001.json", dict(state=dict(end_location="南港", end_quote="她走到南港。")))
    put(tmp_path, "chapters/002.json", dict(state=dict(opening_location="北城", opening_quote="她从南港走到北城。")))
    breaks = check_book(tmp_path)["location_breaks"]
    assert breaks["count"] == 1
    assert breaks["issues"][0]["chapters"] == [2, 3]  # saved plan's next stage still lacks a transfer
    assert breaks["issues"][0]["basis"] == "shared_stage_transition"
    put(tmp_path, "chapters/002.json", dict(state=dict(opening_location="京城", opening_quote="伪造引文")))
    result = check_book(tmp_path)
    assert result["location_breaks"]["count"] == 1
    assert result["location_breaks"]["issues"][0]["chapters"] == [2, 3]
    assert result["location_breaks"]["unknown"]


def test_legacy_volumes_and_generic_outdoor_to_tunnel_transition(tmp_path):
    put(
        tmp_path,
        "story_bible.yaml",
        "volumes:\n- {number: 1, start: 1, end: 25, stage: 旧城}\n- {number: 2, start: 26, end: 50, stage: 新城}\n",
    )
    put(tmp_path, "chapter-001.body.md", "门外透进一丝天光。众人鱼贯冲出。")
    put(tmp_path, "chapter-002.body.md", "矿车轰鸣，钢齿嵌入岩壁。")
    result = check_book(tmp_path)
    assert result["stages"]["count"] == 2
    assert result["stages"]["distribution"][0]["planned_chapters"] == 25
    issue = result["location_breaks"]["issues"][0]
    assert issue["chapters"] == [1, 2]
    assert issue["basis"] == "lexical_boundary"
    assert issue["status"] == "Inference"


def test_final_precedence_draft_fallback_and_no_duplicate_chapters(tmp_path):
    sample(tmp_path)
    put(tmp_path, "final/chapter-001.md", "顾衡是北城账房。")
    put(tmp_path, "chapter-001.md", "# 渲染标题\n")
    put(tmp_path, "chapter-001.r2.draft.md", "# 废弃稿\n")
    result = check_book(tmp_path)
    assert result["chapter_count"] == 3
    assert result["sources"]["1"] == "final/chapter-001.md"
    assert result["sources"]["2"] == "chapter-002.body.md"
    assert result["format_residue"]["total"] == 0
    assert check_book(tmp_path, source="draft")["format_residue"]["total"] == 1


def test_keyword_counts_are_per_chapter_and_not_chapter_numbers(tmp_path):
    for n in range(1, 4):
        put(tmp_path, f"chapter-{n:03d}.md", f"# 第{n}章 铜门铜门\n正文。")
    result = check_book(tmp_path)
    assert result["title_keywords"]["repeated"]["铜门"] == [1, 2, 3]
    assert "章" not in result["title_keywords"]["repeated"]


def test_aliases_allow_registered_addresses_but_flag_new_self_names(tmp_path):
    sample(tmp_path)
    put(tmp_path, "chapter-001.body.md", "“跟宁姐走，叫我夜鸦。”阮宁说。\n“跟铁哥走！叫我渡鸦。”顾衡喊。")
    issues = check_book(tmp_path)["aliases"]["issues"]
    assert {r["alias"] for r in issues} == {"铁哥", "渡鸦"}
    assert all(r["status"] == "Inference" for r in issues)


def test_nonnaming_idioms_negation_and_questions_are_not_positive_evidence(tmp_path):
    sample(tmp_path)
    put(
        tmp_path,
        "chapter-001.body.md",
        "顾衡是北城账房吗？阮宁不是码头掌柜。\n“倒叫我刮目相看，别叫我名字，你再叫我一次丙七三。”",
    )
    result = check_book(tmp_path)
    assert result["aliases"]["issues"] == []
    assert {r["name"] for r in result["introductions"]["missing"]} == {"顾衡", "阮宁"}


def test_report_symlink_cannot_overwrite_prose(tmp_path):
    sample(tmp_path)
    body = tmp_path / "chapter-001.body.md"
    before = body.read_bytes()
    (tmp_path / "book_checkup.json").symlink_to(body)
    write_checkup(tmp_path)
    assert body.read_bytes() == before
    assert not (tmp_path / "book_checkup.json").is_symlink()


def test_unknown_missing_or_invalid_metadata_is_explicit_and_repeatable(tmp_path):
    put(tmp_path, "chapter-001.body.md", "有人进门。")
    put(tmp_path, "story_bible.yaml", "characters: [broken")
    put(tmp_path, "chapters/001.json", "not JSON")
    first = check_book(tmp_path)
    assert first == check_book(tmp_path)
    assert first["unknown"]
    assert first["stages"]["count"] is None
    assert first["introductions"]["coverage"] == "Unknown"


def test_nonconsecutive_chapters_never_claim_continuity(tmp_path):
    put(tmp_path, "chapter-001.md", "门外天光，众人鱼贯冲出。")
    put(tmp_path, "chapter-003.md", "矿车轰鸣，钢齿嵌入岩壁。")
    result = check_book(tmp_path)
    assert result["location_breaks"]["count"] == 0
    assert result["location_breaks"]["unknown"]


def test_report_write_and_standalone_cli_only_add_report_and_are_deterministic(tmp_path):
    sample(tmp_path)
    before = snapshot(tmp_path)
    result = write_checkup(tmp_path)
    assert json.loads((tmp_path / "book_checkup.json").read_text()) == result
    assert {k: v for k, v in snapshot(tmp_path).items() if k != "book_checkup.json"} == before
    script = Path(__file__).resolve().parents[1] / "tools/book_checkup.py"
    report_before = (tmp_path / "book_checkup.json").read_bytes()
    completed = subprocess.run(
        [sys.executable, str(script), str(tmp_path)], cwd=tmp_path, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "book_checkup.json").read_bytes() == report_before
    assert {k: v for k, v in snapshot(tmp_path).items() if k != "book_checkup.json"} == before
