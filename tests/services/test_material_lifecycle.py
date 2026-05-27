from __future__ import annotations

# ruff: noqa: RUF001
from pathlib import Path

from bestseller.services.continuity_ledger_writer import (
    ContinuityEntry,
    append_continuity_entry,
    load_recent_continuity_entries,
)
from bestseller.services.material_advancement_gate import (
    MaterialObligation,
    evaluate_material_advancement,
)
from bestseller.services.material_entity_registry import (
    EntityStatus,
    build_entity_registry,
)
from bestseller.services.material_injection_orchestrator import render_material_injection_blocks
from bestseller.services.material_reference_scanner import scan_material_references
from bestseller.services.material_referential_integrity_gate import (
    evaluate_material_referential_integrity,
)
from bestseller.services.signature_audit_gate import evaluate_signature_audit


def test_registry_marks_active_deprecated_and_duplicate_entities(tmp_path: Path) -> None:
    project = _make_material_project(tmp_path)

    registry = build_entity_registry(project)

    assert registry.by_name["林渊"].status == EntityStatus.ACTIVE
    assert registry.by_name["林正淳"].status == EntityStatus.ACTIVE
    assert "父亲" in registry.by_name["林正淳"].aliases
    assert "爸爸" in registry.by_name["林正淳"].aliases
    assert registry.by_name["林逸"].status == EntityStatus.DEPRECATED
    assert registry.by_name["裴镜渊"].status == EntityStatus.DEPRECATED
    duplicate = registry.by_name["林正淳（林渊父亲-失踪者）"]
    assert duplicate.status == EntityStatus.DUPLICATE


def test_reference_scanner_finds_qingnang_deprecated_references(tmp_path: Path) -> None:
    project = _make_material_project(tmp_path)
    registry = build_entity_registry(project)

    problems = scan_material_references(project, registry)

    deprecated = {(p.file, p.referenced_name, p.problem) for p in problems}
    assert ("story-bible/series-brief.md", "林逸", "deprecated") in deprecated
    assert ("obsidian-vault/人物/林渊.md", "裴镜渊", "deprecated") in deprecated
    assert ("obsidian-vault/人物/林渊.md", "周德昌", "deprecated") in deprecated


def test_referential_integrity_gate_maps_problem_codes(tmp_path: Path) -> None:
    project = _make_material_project(tmp_path)

    verdict = evaluate_material_referential_integrity(project)

    assert verdict.verdict == "blocked"
    codes = {finding.code for finding in verdict.findings}
    assert "MATERIAL_REFERENCES_DEPRECATED" in codes
    assert "MATERIAL_DUPLICATE_CANONICAL" in codes


def test_material_injection_orchestrator_renders_reveal_and_rules(tmp_path: Path) -> None:
    project = _make_material_project(tmp_path)

    block = render_material_injection_blocks(
        project,
        chapter_number=2,
        chapter_position="opening",
        prompt_pack_key="suspense-mystery",
        total_token_budget=1200,
    )

    assert "Material obligation packet" in block
    assert "R-001" in block
    assert "denial_account_rule" in block
    assert "截图段要求" in block


def test_material_advancement_gate_blocks_missing_required_tokens() -> None:
    verdict = evaluate_material_advancement(
        "林渊只看见镜子，没有提到关键证据。",
        [
            MaterialObligation(
                kind="reveal",
                identifier="denial_account_rule",
                required_tokens=("否认入账", "第一名否认者"),
            )
        ],
    )

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "MATERIAL_REVEAL_NOT_ADVANCED"


def test_continuity_ledger_writer_appends_and_loads_recent_entries(tmp_path: Path) -> None:
    ledger = tmp_path / "story-bible" / "continuity-ledger.yaml"

    append_continuity_entry(
        ledger,
        ContinuityEntry(
            chapter_no=1,
            new_characters=("林渊",),
            demonstrated_rules=("R-001",),
            end_state={"location": "十七栋"},
            closing_hook="门外没有影子。",
        ),
    )
    append_continuity_entry(ledger, ContinuityEntry(chapter_no=2, closing_hook="铜钱裂开。"))

    recent = load_recent_continuity_entries(ledger, chapter_no=3, window=2)

    assert [entry["chapter_no"] for entry in recent] == [1, 2]


def test_signature_audit_gate_detects_signature_moment() -> None:
    text = (
        "林渊低头看着铜钱。\n\n"
        "铜钱不是在发烫，它是在替死人记账。\n\n"
        "苏婉宁愣住，孙九斤后退，小雨攥紧袖口，楼道里的冷光一点点爬上镜面。"
    )

    verdict = evaluate_signature_audit(text)

    assert verdict.verdict == "pass"
    assert verdict.metrics["signature_hit_count"] >= 1


def _make_material_project(tmp_path: Path) -> Path:
    project = tmp_path / "exorcist-detective-1778051012"
    story_bible = project / "story-bible"
    people = project / "obsidian-vault" / "人物"
    story_bible.mkdir(parents=True)
    people.mkdir(parents=True)
    (story_bible / "series-brief.md").write_text(
        "# Series Brief\n\n## Stakes\n- personal: 林逸会失去自己仍在意的人。\n",
        encoding="utf-8",
    )
    (story_bible / "cast-and-promises.md").write_text(
        "# Cast\n\n"
        "## 林渊\n\n身份：主角。\n\n"
        "## 林正淳\n\n身份：林渊父亲。\n别名：林父\n\n",
        encoding="utf-8",
    )
    (story_bible / "forbidden-leaks-policy.yaml").write_text(
        "schema_version: forbidden-leaks-policy.v1\n"
        "deprecated_should_remove:\n"
        "  - 林逸\n"
        "  - 裴镜渊\n"
        "  - 周德昌\n",
        encoding="utf-8",
    )
    (story_bible / "rule-ledger.md").write_text(
        "| ID | 规则 | 首次出现 | 可见效果 | 破局方法 | 代价/反噬 | 后续用法 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| R-001 | 否认者先入账 | 第 2 章 | 张建军否认死亡 | "
        "逼出真相 | 承认也有代价 | 后续每案升级 |\n",
        encoding="utf-8",
    )
    (story_bible / "clue-ledger.md").write_text("- C-001 缺角铜钱\n", encoding="utf-8")
    (story_bible / "reveal-schedule.yaml").write_text(
        "schema_version: reveal-schedule.v1\n"
        "reveals:\n"
        "  - id: denial_account_rule\n"
        "    earliest_chapter: 2\n"
        "    tokens: [否认入账, 第一名否认者, 张建军]\n",
        encoding="utf-8",
    )
    (people / "林渊.md").write_text(
        "# 林渊\n\n## 关系\n- [[人物/裴镜渊|裴镜渊]]: 追捕对象\n- 周德昌首次提及。\n",
        encoding="utf-8",
    )
    (people / "林正淳（林渊之父）.md").write_text("# 林正淳（林渊之父）\n", encoding="utf-8")
    (people / "林正淳（林渊父亲-失踪者）.md").write_text(
        "# 林正淳（林渊父亲-失踪者）\n",
        encoding="utf-8",
    )
    return project
