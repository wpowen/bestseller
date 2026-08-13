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


# ── 破折号密度：两档，按人类语料校准 ──────────────────────────────────
#
# 2026-08-06 humanizer 融合。原规则匹配「成对破折号且间距≤120字」、threshold=2，
# 于是把 N 次命中折叠成一个 warn：真机最坏一章 170 个破折号 / 4054 字，只得到
# 一个 span，和 2 个破折号同分——检出了，但量级完全看不见，也从不清理。
# 现在改成全量计数 + 每千字归一化，阈值取自 1187 篇真实出版章的分布
# （中位 0/千字、p99=3.85、最大 10.68）：
#   4-10/千字  → dash_density（越过人类 p99，仅计分，实测人类误报 0.88%）
#   ≥10/千字   → dash_train（超过人类最大值，进 deslop 触发集，人类误报 0.07%）
# 速率只在整章尺度有意义，所以另设 min_chars=1200 与绝对下限 4 处。


# The pool has to be wide enough that a reused clause lands outside the
# repetition detector's 600-char window (~30 sentences at this length).
# Otherwise every chapter-scale fixture also trips a block-severity
# ``narrative_repetition`` and the punctuation assertion gets contaminated.
_CLAUSES = (
    ("他", "把碗放回桌上，看了看窗外的天色"),
    ("她", "数了数墙根那排空酒坛，一共十一个"),
    ("老张", "从灶间端出一碟腌菜，顺手拨亮了油灯"),
    ("账房先生", "在门槛上磕鞋底，泥块掉了一地"),
    ("守夜的", "翻开册子最后一页，墨迹还没干透"),
    ("赶车的", "解下腰间水囊，摇了摇，只剩小半"),
    ("灶上的婆子", "抬手抹去窗棂浮灰，露出底下木纹"),
    ("跑堂的", "拨开炭盆里的灰，找出一块还红着的"),
    ("小二", "把门帘挑起半尺，又放了下去"),
    ("那匹马", "打了个响鼻，鬃毛上还挂着霜"),
    ("院里的狗", "冲着后墙叫了两声，没人应"),
    ("阿七", "蹲在井边刷桶，水溅到了裤脚"),
    ("大先生", "捻着胡子看那张告示，看了很久"),
    ("推车的", "把绳子在腕上绕了三圈，勒出白印"),
    ("卖炭的", "往秤盘上添了半块，眼睛盯着秤星"),
    ("修屋顶的", "从梯子上下来，肩上落了一层瓦灰"),
    ("挑水的", "把扁担换到另一边肩膀，脚下没停"),
    ("那孩子", "攥着一枚铜钱，在门口来回踱"),
    ("补锅的", "敲了敲锅底，声音发闷"),
    ("剃头的", "把刀在皮条上蹭了两下，试了试锋"),
    ("送信的", "抖开油纸包，里头是三封没拆的"),
    ("看仓的", "抽出门闩，闩头磨得发亮"),
    ("码头上的", "把缆绳甩上桩，绕了个死结"),
    ("烧窑的", "开了道缝往里看，热气扑了满脸"),
    ("织布的", "停下梭子，把断了的线头接上"),
    ("赶集回来的", "把空篓子往墙上一挂，篓底还沾着泥"),
    ("拉磨的", "换了个方向走，磨盘吱呀了一声"),
    ("守渡口的", "把船撑离岸边，篙尖点在石阶上"),
    ("采药的", "解开背篓，里头的草叶还带着露水"),
    ("打更的", "把灯笼往上提了提，照亮半条巷子"),
    ("邻家的老妇", "隔着篱笆问了句什么，没人听清"),
    ("那两个脚夫", "把箱子搁下，各自坐到了石阶上"),
)


def _sentences(count: int, *, dashes: int) -> list[str]:
    """``count`` sentences, the first ``dashes`` of which carry one ——.

    Drawn from a pool wide enough that a reused clause falls outside the
    repetition detector's window — a repeated sentence would trip a
    block-severity repetition finding and contaminate assertions that are
    about punctuation.
    """

    out: list[str] = []
    for i in range(count):
        subject, predicate = _CLAUSES[i % len(_CLAUSES)]
        joiner = "——" if i < dashes else "，"
        out.append(f"{subject}{joiner}{predicate}。")
    return out


def _chapter(*, dashes: int, chars: int = 1600) -> str:
    """A chapter-scale passage carrying exactly ``dashes`` narration dashes.

    Density rules only run at ``min_chars``, so a test that means "N dashes
    per 1000 characters" has to supply the 1000 characters too.
    """

    total = max(dashes, 1)
    while True:
        body = "\n\n".join(_sentences(total, dashes=dashes))
        if len(body) >= chars:
            return body
        total += 1


def test_dash_train_fires_on_chapter_scale_flood() -> None:
    # 20 dashes in ~1.6k chars ≈ 12/千字 — above the human corpus maximum.
    cats = _cats(_chapter(dashes=20))
    assert "dash_train" in cats
    assert "dash_density" not in cats  # escalated, not double-reported


def test_dash_density_fires_between_human_p99_and_max() -> None:
    # 8 dashes in ~1.6k chars ≈ 5/千字 — past human p99, below the train band.
    cats = _cats(_chapter(dashes=8))
    assert "dash_density" in cats
    assert "dash_train" not in cats


def test_span_records_hit_count_so_magnitude_is_visible() -> None:
    spans = [
        s
        for s in detect(_chapter(dashes=20), language="zh").spans
        if s.category == "dash_train"
    ]
    assert len(spans) == 1
    assert spans[0].hit_count == 20  # not 1 — the whole point of the fix


def test_human_normal_dash_use_allowed() -> None:
    # 3 dashes in ~1.6k chars ≈ 2/千字 — inside the published-fiction band.
    cats = _cats(_chapter(dashes=3))
    assert "dash_density" not in cats
    assert "dash_train" not in cats


def test_short_fragment_never_rate_flagged() -> None:
    # A scene card / fragment: 1 dash in 16 chars is 62/千字 as a raw rate.
    # min_chars must stop the rule from reading that as a flood.
    text = "他停在门口——那扇门三年没开过。"
    assert "dash_density" not in _cats(text)
    assert "dash_train" not in _cats(text)


def test_dialogue_dashes_are_exempt() -> None:
    # 打断号是地道用法，不该被算进叙述密度。
    quoted = "\n\n".join(f"“{s[:-1]}——”" for s in _sentences(20, dashes=0))
    body = quoted + "\n\n" + "\n\n".join(_sentences(60, dashes=0)[20:])
    cats = _cats(body)
    assert "dash_density" not in cats
    assert "dash_train" not in cats


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
    # 破折号用温和档（越过人类 p99 但未到 dash_train）——极端档是刻意不封顶的。
    core = (
        "这一手堪称绝妙。此战可谓惨烈。这般手段称得上狠辣。"
        "答案很简单：他早就知道。原因也很直接：没人拦得住。\n\n"
        "以前他靠双腿翻山，现在一步便是十里。"
        "从前要熬三炉的丹，如今抬手就成。"
        "过去仰望的人，现在站在他身后。"
    )
    report = detect(core + "\n\n" + _chapter(dashes=8), language="zh")
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


def test_mild_dash_band_stays_out_of_deslop_trigger_set() -> None:
    # 温和档占我们成稿的 60%（人类 0.88%）——一律触发整章重写不划算，
    # 保持原有的「密度型不进触发集」判断：只计分，由自查表和风格锚点约束。
    from bestseller.services.ai_flavor_gate import DESLOP_DISCOURSE_CATEGORIES

    out = _gate_outcome(_chapter(dashes=8))
    issue_ids = {i.id for i in (out.report.issues if out.report else [])}
    assert "AI_FLAVOR_DASH_DENSITY" in issue_ids
    assert "dash_density" not in DESLOP_DISCOURSE_CATEGORIES


def test_dash_train_is_a_standalone_deslop_trigger() -> None:
    # 极端档必须自己就能触发整段重写，不依赖同章恰好还有别的发现——
    # 一章可以只有破折号这一个毛病，分数远在 warn 带以下就 ship 出去。
    from bestseller.services.ai_flavor_gate import DESLOP_DISCOURSE_CATEGORIES

    assert "dash_train" in DESLOP_DISCOURSE_CATEGORIES


def test_dash_train_does_route_to_deslop() -> None:
    # patcher 改不了标点职能（每处要判断换句号/逗号/括号/冒号还是删），
    # 只有整段重写能拆开破折号连挂。
    from bestseller.services.ai_flavor_gate import needs_deslop_revise

    out = _gate_outcome(_chapter(dashes=20))
    issue_ids = {i.id for i in (out.report.issues if out.report else [])}
    assert "AI_FLAVOR_DASH_TRAIN" in issue_ids
    assert needs_deslop_revise(out)
    assert out.decision != "block"  # 修法是重写，不是墙


def test_deslop_self_check_covers_dash_flood() -> None:
    assert "破折号泛滥" in _EXTRA_SELF_CHECK
    assert "破折号连挂" in _EXTRA_SELF_CHECK
