"""翻译腔层（欧化句式）AI-flavor rules — fused from anti-vibe-writing research.

Source: https://github.com/weijt606/anti-vibe-writing (chinese-patterns-to-remove.md
翻译腔层 + 句式层), fiction-calibrated for this pipeline (2026-07-03):

* kept: 对…进行 / 使…得到 nominalisation, evaluative 被字句 (被…很好地 / 被…所…),
  作为一个… opener, lifted copula (堪称/可谓/称得上) density, dash-pair density,
  以前…现在… contrast skeleton density, adjective+colon verdict density;
* dropped (fiction false-positive risk): plural 们, pronoun-density, jargon
  wordlists, markdown/structure rules, texture injection (falsified — taught
  staccato), casual-typing typos.

All rules are advisory (warn, no auto-delete); density constructs are capped
at the score layer so they can never alone force a block → repair churn.
Dialogue spans are exempt everywhere (characters may talk stiltedly on purpose).
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.deslop_revise import _EXTRA_SELF_CHECK


def _cats(text: str) -> list[str]:
    return [s.category for s in detect(text, language="zh").spans]


# ── 独特翻译腔句式（threshold 低，category=translationese，不封顶） ──────


def test_dui_jinxing_nominalisation_fires() -> None:
    assert "translationese" in _cats("战后，他们对护山阵法进行了修复。")


def test_shi_dedao_nominalisation_fires() -> None:
    assert "translationese" in _cats("这一战使他的剑意得到了淬炼。")


def test_jishi_dedao_not_confused_with_shi_dedao() -> None:
    # 「即使…得到」是让步连词，不是「使…得到」空转结构。
    assert "translationese" not in _cats("即使他得到了传承，也未必守得住。")


def test_evaluative_passive_fires() -> None:
    assert "translationese" in _cats("这处隐患已经被长老们很好地解决了。")


def test_bei_suo_passive_density_fires() -> None:
    text = "他被恐惧所支配，脚下发软。片刻后，心神又被那道剑光所摄。"
    assert "translationese" in _cats(text)


def test_single_bei_suo_allowed() -> None:
    assert "translationese" not in _cats("他被恐惧所支配，脚下发软。")


def test_action_passive_is_legit() -> None:
    # 动作被字句是地道中文，不许误伤。
    assert "translationese" not in _cats("他被一掌拍飞，撞碎了石栏。")


def test_zuowei_yige_opener_fires() -> None:
    assert "translationese" in _cats("作为一个修行三百年的散修，他见过太多背叛。")


# ── 密度型痕迹（category 各自独立，进结构封顶族） ────────────────────────


def test_lifted_copula_density_fires() -> None:
    text = "这一手堪称绝妙。此战可谓惨烈。这般手段称得上狠辣。"
    assert "lifted_copula" in _cats(text)


def test_single_lifted_copula_allowed() -> None:
    assert "lifted_copula" not in _cats("这一手堪称绝妙，连长老都眯了眼。")


def test_lifted_copula_in_dialogue_exempt() -> None:
    # 角色说话拿腔拿调是合法刻画。
    text = "「此战可谓惨烈。」他说。「这一手堪称绝妙。」她答。「称得上狠辣。」"
    assert "lifted_copula" not in _cats(text)


def test_dash_pair_density_fires() -> None:
    text = (
        "他停在门口——那扇门三年没开过——手搭上了锁。\n\n"
        "锁是新的——有人来过——而且不止一次。"
    )
    assert "dash_density" in _cats(text)


def test_single_dash_paragraph_allowed() -> None:
    assert "dash_density" not in _cats("他停在门口——那扇门三年没开过。")


def test_then_now_contrast_density_fires() -> None:
    text = (
        "以前他靠双腿翻山，现在一步便是十里。"
        "从前要熬三炉的丹，如今抬手就成。"
        "过去仰望的人，现在站在他身后。"
    )
    assert "then_now_contrast" in _cats(text)


def test_single_then_now_contrast_allowed() -> None:
    assert "then_now_contrast" not in _cats("以前他靠双腿翻山，现在一步便是十里。")


def test_adjective_colon_verdict_density_fires() -> None:
    text = "答案很简单：他早就知道。原因也很直接：没人拦得住。"
    assert "adjective_colon_verdict" in _cats(text)


def test_adjective_colon_before_dialogue_is_legit() -> None:
    # 「声音很冷：『滚。』」是合法的对白引入，不是替读者下判断。
    text = "他的声音很冷：「滚。」她的语气很轻：「不走。」"
    assert "adjective_colon_verdict" not in _cats(text)


# ── 冷读者干净样例：一次合法使用不许误伤 ────────────────────────────────


def test_clean_wuxia_prose_not_flagged() -> None:
    text = (
        "他被人流挤到墙根，肩头撞上砖，才看清告示上那行字。"
        "曾经贴满赏格的墙，如今只剩一张纸。"
        "纸角卷着——风一吹，露出底下半个旧墨印。"
        "他伸手把纸压平，指腹停在那个墨印上。"
    )
    cats = _cats(text)
    for c in (
        "translationese",
        "lifted_copula",
        "dash_density",
        "then_now_contrast",
        "adjective_colon_verdict",
    ):
        assert c not in cats, (c, cats)


def test_density_categories_capped_cannot_block_alone() -> None:
    # 全部密度型新规则同时命中，也只能贡献结构封顶内的分数（<50 修复线）。
    text = (
        "这一手堪称绝妙。此战可谓惨烈。这般手段称得上狠辣。"
        "答案很简单：他早就知道。原因也很直接：没人拦得住。\n\n"
        "他停在门口——那扇门三年没开过——手搭上了锁。\n\n"
        "锁是新的——有人来过——而且不止一次。\n\n"
        "以前他靠双腿翻山，现在一步便是十里。"
        "从前要熬三炉的丹，如今抬手就成。"
        "过去仰望的人，现在站在他身后。"
    )
    report = detect(text, language="zh")
    fired = {s.category for s in report.spans}
    assert {"lifted_copula", "dash_density", "then_now_contrast"} <= fired
    assert report.overall_score < 50.0


# ── deslop 自查表同步 ───────────────────────────────────────────────────


def test_deslop_self_check_covers_translationese() -> None:
    assert "翻译腔" in _EXTRA_SELF_CHECK
    assert "对…进行" in _EXTRA_SELF_CHECK or "对……进行" in _EXTRA_SELF_CHECK


# ── 路由：独特翻译腔进 deslop 触发集，密度型不进（防成本/误伤） ──────────


def _gate_outcome(content: str):
    from bestseller.services.ai_flavor_gate import (
        AiFlavorGateConfig,
        run_ai_flavor_gate,
    )

    cfg = AiFlavorGateConfig(llm_rewrite_enabled=False, write_audit_file=False)
    return run_ai_flavor_gate(
        chapter_number=1, content_md=content, language="zh-CN", config=cfg,
        llm_rewriter=None, project_output_dir=None,
    )


def test_translationese_routes_to_deslop() -> None:
    from bestseller.services.ai_flavor_gate import needs_deslop_revise

    out = _gate_outcome(
        "战后，他们对护山阵法进行了修复。作为一个修行三百年的散修，他见过太多背叛。"
    )
    issue_ids = {i.id for i in (out.report.issues if out.report else [])}
    assert "AI_FLAVOR_TRANSLATIONESE" in issue_ids
    assert needs_deslop_revise(out)
    assert out.decision != "block"  # 修法是重写，不是墙


def test_dash_density_alone_does_not_route_to_deslop() -> None:
    from bestseller.services.ai_flavor_gate import needs_deslop_revise

    out = _gate_outcome(
        "他停在门口——那扇门三年没开过——手搭上了锁。\n\n"
        "锁是新的——有人来过——而且不止一次。"
    )
    issue_ids = {i.id for i in (out.report.issues if out.report else [])}
    assert "AI_FLAVOR_DASH_DENSITY" in issue_ids
    assert not needs_deslop_revise(out)
