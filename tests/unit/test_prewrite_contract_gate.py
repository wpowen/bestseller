# ruff: noqa: RUF001

from __future__ import annotations

import json

from bestseller.services.prewrite_contract_gate import (
    evaluate_prewrite_contract_coverage,
    evaluate_prewrite_contract_readiness,
)


def test_prewrite_contract_gate_reads_chapter_anchor_from_story_bible(tmp_path) -> None:
    story_bible = tmp_path / "story-bible"
    story_bible.mkdir()
    (story_bible / "prewrite-contract.json").write_text(
        json.dumps(
            {
                "chapters": {
                    "72": {
                        "prewrite_anchor": "承接 ch71 回执镜片, 推进张家开门人。",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    verdict = evaluate_prewrite_contract_coverage(
        chapter_no=72,
        story_bible_dir=story_bible,
    )

    assert verdict.passed is True


def test_prewrite_contract_gate_blocks_missing_anchor() -> None:
    verdict = evaluate_prewrite_contract_coverage(
        chapter_no=72,
        contract={"chapters": {"72": {"prewrite_anchor": ""}}},
    )

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "prewrite_anchor_missing"


def test_prewrite_contract_gate_blocks_missing_chapter_contract() -> None:
    verdict = evaluate_prewrite_contract_coverage(
        chapter_no=72,
        contract={"chapters": {"71": {"prewrite_anchor": "上一章"}}},
    )

    assert verdict.verdict == "blocked"
    assert verdict.findings[0].code == "prewrite_contract_chapter_missing"


def test_prewrite_readiness_blocks_placeholder_contract() -> None:
    verdict = evaluate_prewrite_contract_readiness(
        chapter_no=83,
        contract={
            "chapters": {
                "83": {
                    "prewrite_anchor": "接住上一章具体尾钩，推动本章主线。",
                    "chapter_objective": "推动本章剧情发展。",
                    "scene_beats": ["让林渊主动判断并付出代价。"],
                }
            }
        },
    )

    codes = {finding.code for finding in verdict.findings}
    assert verdict.verdict == "blocked"
    assert "PREWRITE_PLACEHOLDER_TEXT" in codes
    assert "PREWRITE_REQUIRED_FIELD_MISSING" in codes
    assert "PREWRITE_SCENE_BEATS_TOO_THIN" in codes


def test_prewrite_readiness_accepts_qingnang_style_specific_contract() -> None:
    verdict = evaluate_prewrite_contract_readiness(
        chapter_no=85,
        contract={
            "chapters": {
                "85": {
                    "prewrite_anchor": (
                        "承接 ch84 义庄铜镜封存编号，林渊用青囊、铜钱和"
                        "登记记录三重验签。"
                    ),
                    "chapter_objective": (
                        "确认取镜人不是林正淳本人，并把父亲名字被冒用转成"
                        "现实证据链。"
                    ),
                    "scene_beats": [
                        "苏婉宁调出清水桥义庄登记册，落地封存编号和签名笔压差异。",
                        "林渊用青囊验字，铜钱冷痕反咬登记册指纹。",
                        "镜影用父亲名字制造反证，迫使林渊公开核验签名链。",
                    ],
                    "required_evidence": "青囊验字、铜钱冷痕、登记册指纹",
                    "required_payoff": "父亲名字被冒用的第一层现实证据",
                    "pressure_handoff": "下一章转入井口填井手续反压。",
                    "forbidden_moves": ["不得让青囊直接替主角解题"],
                    "scene_drive": "每场至少推动 plot、clue、status 中两项。",
                    "hook_contract": "章末只抛井口填井手续反压，不新增副本。",
                }
            }
        },
    )

    assert verdict.verdict == "pass"
    assert verdict.findings == ()
