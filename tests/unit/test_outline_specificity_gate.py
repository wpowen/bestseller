# ruff: noqa: RUF001

from __future__ import annotations

from bestseller.services.outline_specificity_gate import evaluate_outline_specificity


def test_blocks_placeholder_chapter_objective() -> None:
    verdict = evaluate_outline_specificity(
        {
            "chapter_no": 76,
            "chapter_objective": "写前补齐：本章只推进一条主账路，必须有一个可读兑现点。",
            "scene_beats": ["接住上一章具体尾钩。"],
            "required_evidence": "写前指定本章唯一核心物证/账印/方位锚。",
            "required_payoff": "写前指定本章阶段性兑现，不能只增加谜团。",
        }
    )

    assert verdict.verdict == "blocked"
    assert "OUTLINE_PLACEHOLDER" in {finding.code for finding in verdict.findings}


def test_blocks_duplicate_scene_beats_across_chapters() -> None:
    previous = {
        "chapter_no": 10,
        "chapter_objective": "林渊在303门口逼王建业交出铜钱回执。",
        "scene_beats": ["林渊查303门牌。", "王建业交出铜钱。", "章尾出现张建军。"],
        "required_evidence": "303门牌和铜钱回执。",
        "required_payoff": "林渊确认王建业撒谎。",
    }
    current = {**previous, "chapter_no": 11}

    verdict = evaluate_outline_specificity(current, prev_outline=previous)

    assert "OUTLINE_BEATS_DUPLICATE_PREV" in {finding.code for finding in verdict.findings}
    assert verdict.verdict == "warn_only"


def test_passes_with_named_entities() -> None:
    verdict = evaluate_outline_specificity(
        {
            "chapter_no": 4,
            "chapter_objective": "23:53，林渊在303门口用罗盘核验陈默藏起的碎玉账。",
            "scene_beats": [
                "林渊让陈默交出碎玉。",
                "小雨指认303门牌后的镜片裂痕。",
            ],
            "required_evidence": "陈默手机、碎玉、303门牌三项必须形成因果链。",
            "required_payoff": "林渊救下小雨，但陈默被镜眼拖入下一章。",
        }
    )

    assert verdict.passed is True
    assert verdict.findings == ()


def test_severity_for_each_pattern_class() -> None:
    verdict = evaluate_outline_specificity(
        {
            "chapter_no": 9,
            "chapter_objective": "推进主线。",
            "scene_beats": ["林渊在303门口查账印。"],
            "required_evidence": "账印。",
            "required_payoff": "阶段性兑现。",
        }
    )

    severities = {finding.code: finding.severity for finding in verdict.findings}
    assert severities["OUTLINE_PLACEHOLDER"] == "critical"
