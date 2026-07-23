from __future__ import annotations

# Chinese prose fixtures intentionally use native punctuation.
# ruff: noqa: RUF001
import json
from pathlib import Path

import pytest

from bestseller.services.offline_quality_eval import (
    evaluate_manifest,
    render_markdown,
    write_report,
)

pytestmark = pytest.mark.unit


def _chapter(extra: str = "") -> str:
    paragraphs = [
        "林照川推门时，门外的雨刚停，柜台上的旧账本还摊在原处。",
        "他没有先问母亲要不要卖店，而是把欠条一张张压进文件夹。",
        "周琴看着他的手，问他能不能在天黑前交出第一炉点心。",
        "林照川答应下来，转身检查冰柜和灶台，发现电表已经亮起红灯。",
        "他把桂花酥的配方交给阿禾，让她盯住火候，自己去找供电所。",
        "雨水顺着屋檐滴在门槛上，旧铺子的招牌摇了一下，像有人催他做决定。",
        "林照川把湿掉的袖口卷到手肘，重新核对账本上的数字，等着第一炉点心出窑。",
    ]
    return "\n\n".join(paragraphs) + extra


def _manifest(root: Path) -> dict:
    for arm in ("lean", "production"):
        arm_dir = root / arm
        arm_dir.mkdir()
        for number in (1, 2, 3):
            (arm_dir / f"{number}.md").write_text(_chapter(), encoding="utf-8")
    return {
        "schema_version": "offline-quality-eval/v1",
        "evaluation_version": "test",
        "book": {"id": "fixture", "genre": "现实经营"},
        "quality": {"min_chars": 200, "max_chars": 800},
        "facts": {
            "protagonist": {"name": "林照川", "age": 28},
            "required_terms": {"shop": ["旧铺子"]},
            "characters": {"周琴": ["母亲", "卖店"], "阿禾": ["配方", "火候"]},
        },
        "golden_three": {"hook_keywords": ["欠条", "第一炉"], "payoff_keywords": ["供电所"]},
        "reader_promise": {"keywords": ["旧铺子", "点心"], "stages": {"opening": [1, 2, 3]}},
        "arms": {
            "lean": [{"chapter": n, "path": f"lean/{n}.md"} for n in (1, 2, 3)],
            "production": [{"chapter": n, "path": f"production/{n}.md"} for n in (1, 2, 3)],
        },
    }


def test_offline_evaluation_is_reproducible_and_explicit_about_commercial_gap(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    manifest["arms"]["lean"] = {
        "prompt_variant": "lean",
        "prompt_hash": "sha256:test",
        "chapters": manifest["arms"]["lean"],
    }
    first = evaluate_manifest(manifest, base_dir=tmp_path)
    second = evaluate_manifest(manifest, base_dir=tmp_path)

    assert first == second
    assert first["schema_version"] == "offline-quality-eval/v1"
    assert first["static_status"] in {"pass", "warn"}
    assert first["commercial_validation"]["status"] == "not_tested"
    assert first["comparison"]["recommendation"] == "inconclusive"
    assert first["arms"]["lean"]["metadata"]["prompt_hash"] == "sha256:test"
    assert first["arms"]["lean"]["dimensions"]["fact_consistency"]["status"] == "pass"


def test_missing_chapter_is_inconclusive_and_never_a_pass(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["arms"]["production"].append({"chapter": 4, "path": "production/missing.md"})

    report = evaluate_manifest(manifest, base_dir=tmp_path)

    assert report["arms"]["production"]["chapters"][-1]["error"].startswith("read_error:")
    assert report["arms"]["production"]["status"] == "inconclusive"
    assert report["static_status"] == "inconclusive"
    # A missing file is preserved as evidence; raw manuscript text is not copied.
    assert "text" not in report["arms"]["production"]["chapters"][-1]


def test_fact_violation_and_ai_flavor_are_visible(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    bad = _chapter("\n\n林照川二十岁那年已经欠下新债，这一切都是为了证明他终于明白了。")
    (tmp_path / "lean" / "1.md").write_text(bad, encoding="utf-8")
    manifest["facts"]["forbidden_terms"] = ["终于明白"]

    report = evaluate_manifest(manifest, base_dir=tmp_path)
    facts = report["arms"]["lean"]["dimensions"]["fact_consistency"]
    ai = report["arms"]["lean"]["dimensions"]["ai_flavor"]

    assert facts["status"] == "fail"
    assert any(item.startswith("forbidden:") for item in facts["violations"])
    assert ai["ai_score"] >= 0
    assert "pattern_counts" in ai


def test_report_writer_emits_json_and_markdown(tmp_path: Path) -> None:
    report = evaluate_manifest(_manifest(tmp_path), base_dir=tmp_path)
    paths = write_report(report, tmp_path / "report")

    assert paths["json"].exists() and paths["markdown"].exists()
    persisted = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert persisted == report
    assert "商业/榜单验证" in render_markdown(report)
