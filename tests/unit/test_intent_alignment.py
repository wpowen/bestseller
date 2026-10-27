"""意图对表门（2026-08-19《摔下山三次》定罪）。

用户意图 tags 只用于路由、无人对表成品：成品丢「升级流」、简介标签行
模型自产「慢热」对抗爽文意图、设定反着写题材升级预期。三面对账 +
两票判官，首跑 warn-only 留痕。
"""

from __future__ import annotations

import pytest
from test_intent_alignment_submission_boundary import (  # noqa: F401
    AfterIntentBoundary,
    AfterIntentFailure,
    intent_boundary,
    no_external_intent_io,
    reports,
)

from bestseller.services.intent_alignment import (
    audit_and_rebuild_tagline,
    build_intent_alignment_messages,
    intent_tags_from_contract,
    missing_intent_tags,
    parse_intent_alignment_verdict,
    replace_tagline,
)

pytestmark = pytest.mark.unit

_INTENT = ["废柴逆袭", "升级流", "血脉觉醒"]


def test_missing_intent_tags_catches_the_shuaixia_case():
    final = ["东方玄幻", "废柴逆袭", "血脉觉醒", "锻体流", "门派经营"]
    assert missing_intent_tags(_INTENT, final) == ["升级流"]


def test_tagline_alien_rebuild():
    synopsis = (
        "标签：东方玄幻+宗门+废柴逆袭+热血+慢热+成长\n\n"
        "我蹲在山门石桩上十二年。"
    )
    audit = audit_and_rebuild_tagline(
        synopsis,
        intent_tags=_INTENT,
        final_tags=["东方玄幻", "废柴逆袭", "血脉觉醒"],
        genre_labels=["东方玄幻"],
    )
    # 宗门/热血/慢热/成长 都不在 意图∪成品∪题材 里 → 异物
    assert "慢热" in audit.alien_tokens
    assert audit.rebuilt_line is not None
    assert "慢热" not in audit.rebuilt_line
    assert "升级流" in audit.rebuilt_line, "重建行必须带全意图 tags"
    rebuilt = replace_tagline(synopsis, audit.rebuilt_line)
    assert rebuilt.splitlines()[0] == audit.rebuilt_line
    assert "我蹲在山门石桩上十二年。" in rebuilt, "正文不许动"


def test_clean_tagline_untouched():
    synopsis = "标签：东方玄幻+废柴逆袭+升级流\n\n正文。"
    audit = audit_and_rebuild_tagline(
        synopsis, intent_tags=_INTENT, final_tags=[], genre_labels=["东方玄幻"]
    )
    assert audit.alien_tokens == []
    assert audit.rebuilt_line is None


def test_no_tagline_is_not_a_disease():
    audit = audit_and_rebuild_tagline(
        "我蹲在山门石桩上十二年。", intent_tags=_INTENT
    )
    assert audit.tagline is None and audit.rebuilt_line is None


def test_judge_messages_and_parser():
    system, user = build_intent_alignment_messages(
        intent_tags=_INTENT,
        genre_label="东方玄幻",
        premise="p",
        synopsis="s",
        spine={"wants": "w"},
    )
    assert "落点引文" in user and "counter_elements" in user
    assert "只输出JSON" in system

    verdict = parse_intent_alignment_verdict(
        {
            "items": {
                "废柴逆袭": {"pass": True, "quote": "最没用的弟子"},
                "升级流": {"pass": False, "quote": ""},
            },
            "counter_elements": [
                {"quote": "连飞都不会是他的道", "against": "升级流"}
            ],
            "revise_direction": "给锻体一条可感的升级阶梯",
        },
        intent_tags=_INTENT,
    )
    assert verdict is not None
    assert verdict["failed_tags"] == ["升级流"]
    assert verdict["items"]["血脉觉醒"]["pass"] is None, "判官漏项=unknown 不定罪"
    assert verdict["counter_elements"][0]["against"] == "升级流"


def test_contract_tag_extraction_priority():
    contract = {
        "genre_intent": {
            "user_tags": [],
            "tags": ["废柴逆袭", "升级流", "血脉觉醒"],
            "default_tags": ["x"],
        }
    }
    # Split contracts keep defaults as classification metadata, not user intent.
    assert intent_tags_from_contract(contract) == []


@pytest.mark.asyncio
async def test_finalize_wires_intent_alignment(intent_boundary):  # noqa: F811 - imported pytest fixture
    from bestseller.services import intent_review_submission as submission

    f = intent_boundary
    with pytest.raises(AfterIntentBoundary):
        await f.run()
    assert len(f.requests) == 2
    first, second = f.requests
    assert [row["request"].sample_slot for row in f.requests] == [0, 1]
    assert all(row["request"].prompt_template == "conception_intent_alignment"
               and row["request"].logical_role == "critic"
               and row["request"].prompt_version == submission.INTENT_REVIEW_PROMPT_VERSION
               and row["request"].tool_choice == "required" for row in f.requests)
    assert first["source"] == second["source"]
    assert first["effective_input"] == second["effective_input"]
    assert first["effective_hash"] == second["effective_hash"]
    assert first["namespace"] == second["namespace"]
    assert first["root"] != second["root"] and first["run_id"] != second["run_id"]
    assert first["source"]["cost_style"] == "minimal"
    assert (
        first["source"]["user_controls"]["creation_intent_contract"]
        == f.state["ctx"]["creation_intent_contract"]
    )
    after = f.stopped[0]
    assert "升级流" in after["tags"] and "默认分类" not in after["tags"]
    assert "慢热" not in after["synopsis"].splitlines()[0]
    assert after["premise"] == f.state["premise"] and not f.editor_calls


# ── 从 warn-only 升级为定向修复（2026-08-19 用户第二次定罪）──────────────
# 真机《逢魔夜市签收人》：用户勾「金手指」，成品 tags 无它、判官抓到两条
# 方向相反的设定，门却只留痕，书照建——「抓到了不修」等于没抓。


def test_missing_user_tags_are_backfilled_deterministically():
    import inspect

    from bestseller.services import conception

    src = (inspect.getsource(conception.run_conception_pipeline)
           + inspect.getsource(conception._review_conception_candidate))
    assert "tags = [*(tags or []), *_ia_missing]" in src, "用户勾的标签缺失必须确定性补回"
    # default_tags 不许硬塞（跨书同质化，2026-08-01 裁决）
    assert "_ia_missing" in src


@pytest.mark.asyncio
async def test_intent_repair_is_wired_with_recheck(intent_boundary):  # noqa: F811 - imported pytest fixture
    f = intent_boundary
    f.initial, f.recheck = ["fail", "fail"], "fail"
    # Two distinct recheck citations keep the actual burden >= initial; the
    # separate one-counter regression below preserves the diagnostic bug.
    f.recheck_counter_count = 2
    with pytest.raises(AfterIntentFailure, match="CONCEPTION_INTENT_REPAIR_UNRESOLVED") as caught:
        await f.run()
    assert len(f.editor_calls) == 1 and len(f.requests) == 3
    assert f.editor_calls[0]["template"] == "conception_intent_repair"
    assert "保持故事身份、主角、核心机制与世界观不变" in f.editor_calls[0]["user_prompt"]
    initial, _, recheck = f.requests
    assert [row["source"]["phase"] for row in f.requests] == [
        "initial", "initial", "recheck",
    ]
    assert recheck["request"].sample_slot == 0
    assert recheck["source"]["candidate"] == f.fixed
    assert recheck["source"]["user_controls"] == initial["source"]["user_controls"]
    assert recheck["source"]["cost_style"] == initial["source"]["cost_style"] == "minimal"
    assert recheck["request"].prompt_template == initial["request"].prompt_template
    assert recheck["effective_hash"] != initial["effective_hash"]
    assert recheck["namespace"] != initial["namespace"] and recheck["root"] != initial["root"]
    assert all(row["status"] == "verified" for row in reports(caught.value))
    failure = next(
        row for row in caught.value.conception_log if row.get("agent") == "intent_repair"
    )
    assert failure["adopted"] is False and failure["reason"] == "recheck_no_improvement"
    assert not f.joint_calls and not f.stopped
    assert len(f.structure_calls) == 1 and f.structure_calls[0]["scope"] == "story"
    assert f.structure_calls[0]["report"]["candidate"]["premise"] == f.state["premise"]


def test_pollution_gate_block_persists_conception_log():
    """污染门毙书也要留档（2026-08-19：撞了哪本书只能从任务事件人肉挖）。"""
    import inspect

    from bestseller.services import conception
    from bestseller.web import server

    src = (inspect.getsource(conception.run_conception_pipeline)
           + inspect.getsource(conception._review_conception_candidate))
    assert "_cc_exc.conception_log" in src, "拦截异常必须携带构思日志带出 async 帧"

    web_src = inspect.getsource(server.WebTaskManager._run_autowrite_worker)
    assert web_src.count("_persist_conception_log") >= 2, (
        "AppealBar 与 ConceptContract 两条拦截路径都必须落档"
    )


# ── 代价档纳入意图对表（2026-08-19 真机《替嫁夜…》）─────────────────────
# 用户勾 minimal（能力不带自损），成品把「反噬压在自己胸口/灰印多爬一寸」
# 当核心笔墨，判官只判 tag 落点因而放行——代价档也是用户设定。


def test_cost_rule_only_for_no_selfharm_tiers():
    _, user_min = build_intent_alignment_messages(
        intent_tags=["金手指"], genre_label="东方玄幻",
        premise="p", synopsis="s", spine=None, cost_style="minimal",
    )
    _, user_ext = build_intent_alignment_messages(
        intent_tags=["金手指"], genre_label="东方玄幻",
        premise="p", synopsis="s", spine=None, cost_style="external",
    )
    _, user_std = build_intent_alignment_messages(
        intent_tags=["金手指"], genre_label="东方玄幻",
        premise="p", synopsis="s", spine=None, cost_style="standard",
    )
    for u in (user_min, user_ext):
        assert "代价档判定" in u and "cost_violations" in u
        # 限制≠代价：边界条件不得被当成违规上报
        assert "不算自损，属于合法限制" in u
        # 召回收紧（2026-08-19 真机漏报「每逆转一次少一炷香寿」）：
        # 给判定公式而不是类别列举，文学化损耗照报
        assert "使用即扣减" in u
        assert "独立检查项" in u
    assert "代价档判定" not in user_std, "standard 档允许自损，不判"


def test_cost_violations_join_repair_channel():
    verdict = parse_intent_alignment_verdict(
        {
            "items": {"金手指": {"pass": True, "quote": "灰鳞骨"}},
            "counter_elements": [],
            "cost_violations": [{"quote": "反噬也压在自己胸口"}],
        },
        intent_tags=["金手指"],
    )
    assert verdict is not None
    assert verdict["failed_tags"] == []
    # 并入 counter_elements → 复用同一条定向修复通道
    assert len(verdict["counter_elements"]) == 1
    assert "代价档" in verdict["counter_elements"][0]["against"]


def test_conception_passes_cost_style_to_judge():
    import inspect

    from bestseller.services import conception

    src = (inspect.getsource(conception.run_conception_pipeline)
           + inspect.getsource(conception._review_conception_candidate))
    assert src.count("cost_style=_ia_cost_style") == 2, "主判与复核都必须带代价档"
    assert 'explicit_enhancers' in src
