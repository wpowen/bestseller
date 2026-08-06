"""全 prompt 面注入普查回归（2026-07-31）。

背景：尸体/账 类母题反复出现在所有书里，根因不是题材配置，而是散落在
横切面里的注入点——修复提示里的具体意象建议、跨题材共用的原型台词、
概念种子库里的母题词、单书物料包的过宽路由、以及框架黑话（账本/台账）
在每本书修复 prompt 里的高频出现。本文件把这次清剿的每一处锁成断言：
谁再把这些词加回 prompt 面，测试直接红。

原则：检测器词表（anti_default_motif 的正则、appeal 词库）是防线，
不在本文件管辖范围——它们不进 prompt。
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.unit


def test_planning_readiness_repair_hints_carry_no_genre_specific_imagery() -> None:
    """开局修复提示不得建议 尸证 这类题材绑定的具体意象。

    （电梯 仍出现在 _IN_PERSON_OPENING_PATTERN 检测正则里——检测器是防线，
    不进 prompt，不在断言范围。）
    """
    from bestseller.services import planning_readiness_gate

    source = inspect.getsource(planning_readiness_gate)
    assert "尸证" not in source
    assert "或面对面冲突" in source


def test_concept_leap_seed_pool_carries_no_ledger_motif() -> None:
    """概念种子库不得内置 账本/善恶簿 类母题种子（它会被渲染进构思 prompt）。"""
    from bestseller.services import concept_leap

    source = inspect.getsource(concept_leap)
    assert "账" not in source
    assert "ledger_of_souls" not in source


def test_folk_master_dialogue_archetype_is_deledgered() -> None:
    """P1_folk_master 会被推断给任何题材的老年角色，其台词库必须无账意象。"""
    from bestseller.services import dialogue_archetypes

    source = inspect.getsource(dialogue_archetypes)
    assert "账" not in source


def test_single_book_reference_packs_are_fully_deleted() -> None:
    """2026-07-31 裁决升级：历史书单书参考包（道种破虚/青囊/代价之鸢等）
    整体删除——连完整书名信号也不得复活它们，新书只允许题材级物料。"""
    from bestseller.services import material_density
    from bestseller.services.material_density import _select_material_pack

    for name in (
        "_build_qingnang_pack",
        "_xianxia_upgrade_pack_spec",
        "_female_no_cp_pack_spec",
        "_english_romantasy_pack_spec",
    ):
        assert not hasattr(material_density, name), name

    pack_id_full, _ = _select_material_pack(
        "proj-y",
        "道种破虚 参考包信号",
        title="道种破虚",
        genre="仙侠升级流",
        sub_genre="宗门逆袭",
        language="zh-CN",
    )
    assert pack_id_full != "xianxia_upgrade"


def test_ledger_jargon_is_renamed_in_every_zh_prompt_surface() -> None:
    """框架黑话「账本/台账」不得出现在中文 prompt 面（改用「清单」）。"""
    from bestseller.services import (
        autonomous_book_repair,
        continuity,
        hook_ledger_runtime,
        payoff_ledger_runtime,
    )

    for module in (payoff_ledger_runtime, hook_ledger_runtime, continuity):
        source = inspect.getsource(module)
        assert "账本" not in source, module.__name__
        assert "台账" not in source, module.__name__

    assert "线索账本" not in inspect.getsource(autonomous_book_repair)


def test_methodology_relationship_debt_hints_carry_no_ledger_props() -> None:
    """relationship_debts 的解释性提示不得枚举 记账/欠条/账本/债契 等道具词。"""
    from bestseller.services import methodology_application_gate

    source = inspect.getsource(methodology_application_gate)
    for token in ("记账", "欠条", "账本", "债契"):
        assert token not in source, token


def test_generation_prompts_no_longer_inject_guardrails_personas_or_menus() -> None:
    """2026-08-01 根治裁决：防线全部退出 prompt，框架不再向生成 prompt 供给
    画像表/情绪事件菜单/反母题护栏块。守卫只活在出口侧检测器里。"""
    from bestseller.services import blurb_copywriter, conception, planner

    planner_src = inspect.getsource(planner)
    assert "planner_anti_default_block(" not in planner_src
    assert "resolve_persona(" not in planner_src
    assert "_planner_reader_persona_block(" not in planner_src

    conception_src = inspect.getsource(conception)
    assert "resolve_persona(" not in conception_src
    assert "genre_emotion_exemplars(" not in conception_src

    blurb_src = inspect.getsource(blurb_copywriter)
    assert "题材高唤起情绪范例" not in blurb_src
    assert "他的雷点" not in blurb_src


def test_concept_generation_prompt_names_no_motif_vocabulary() -> None:
    """禁令+点名列举 是同一个缺陷类：说"禁止写 X"就是把 X 塞进模型上下文。

    真机对照（2026-08-03）：出口门禁已全部拆除后建的第一本书仍叫《雾街债主》，
    通篇债印/卖身契/还债——回查发现概念生成 prompt 里还留着两条禁令，分别点名
    「失忆、扣命、掉寿命、资源债」和「寿命、身份、记忆、家底、亲情」。删掉之后
    同一条 prompt 里这些词全部归零。这条测试守住那个零。
    """
    from bestseller.services import concept_tournament as ct

    system, user = ct._build_engine_kernel_messages(
        genre="仙侠升级",
        sub_genre="宗门逆袭",
        lane="纯题材直觉",
        chapter_count=30,
        tone_preference="hot",
        cost_style="minimal",
    )
    blob = system + user
    for token in ("债", "账", "欠", "寿", "失忆", "记忆", "亲情", "尸", "遗体", "殡仪"):
        assert token not in blob, f"概念生成 prompt 又出现母题词：{token}"


def test_the_motif_police_stay_retired() -> None:
    """2026-08-02 根治裁决的执法者：母题警察不得以任何形式复活。

    死亡与债务是普通故事材料。框架一边命令写代价（每章 cost_or_tradeoff、
    代价账 hard gate、题材物料的 no-free-win），一边因为写了代价而处决产物，
    两本真书死在地基与卷纲阶段。谁想再加回来，先在这里红。
    """
    from bestseller.services import anti_default_motif, planner

    # 检测器全部退役
    for text in ("债主拿着欠条来讨债", "矿洞深处埋着一具枯骨", "力量反噬后短期失声"):
        assert not anti_default_motif.contains_debt_motif(text)
        assert not anti_default_motif.contains_default_death_motif(text)
        assert not anti_default_motif.contains_minimal_cost_violation(text)
        assert not anti_default_motif.is_debt_dominated(text)

    # 护栏块不渲染任何文本
    assert anti_default_motif.anti_debt_block(is_en=False) == ""
    assert anti_default_motif.planner_anti_default_block({}, is_en=False) == ""

    # planner 的三连处决门不得回来
    gate_source = inspect.getsource(
        planner._validate_planner_creation_intent_payload
    )
    assert "raise" not in gate_source

    # 纯爽不得再翻译成"只许用这些代价"的白名单
    from bestseller.services.ideology_kernel import cost_style_directive

    minimal = cost_style_directive("minimal", is_en=False)
    assert "白名单" not in minimal
    assert "完整可用集合" not in minimal


def test_planner_relationship_debt_notes_carry_no_ledger_props() -> None:
    """planner 的 methodology_contract 说明不得枚举 欠账/记账 等账务道具词。"""
    from bestseller.services import planner

    source = inspect.getsource(planner)
    assert "禁止因此出现欠账" not in source
    assert "欠账/记账设定" not in source


def test_outline_hard_constraints_are_bounded() -> None:
    """修复指令必须有上界，否则它自己会把书撑死。

    这些 "- 硬约束" 行被 `+=` 到 prompt 末尾，编译器因此把它们归入
    `primary_task`——required 且无 max_tokens、无 trim_policy，是整条 prompt 里
    唯一不能被压缩的块。判官每修一轮就追加一批，两轮之后 2026-08-03 的
    《雾街债主》编译失败（必需 15336 > 可用 14400）并因此死亡。
    """
    from bestseller.services.planner import (
        _OUTLINE_CONSTRAINT_BLOCK_CHAR_CAP,
        _bounded_outline_constraints,
    )

    runaway = [f"第{i}条修复指令：" + "补写具体冲突与代价。" * 12 for i in range(40)]
    kept = _bounded_outline_constraints(runaway)

    rendered = "\n".join(f"- {c}" for c in kept)
    assert len(rendered) <= _OUTLINE_CONSTRAINT_BLOCK_CHAR_CAP
    assert kept, "上界不能把指令清空"
    # 保留最新的（它们回应的是最近一次判决）
    assert kept[-1] == runaway[-1]

    # 未超限时原样透传
    small = ["补第 1 章的可见冲突", "第 2 章补一个具体损失"]
    assert _bounded_outline_constraints(small) == small


def test_progressive_outline_merge_covers_every_batch() -> None:
    """滚动大纲的每一批都必须并入可物化批次，否则窗口永远不完整。

    规划器按批各存一版 VOLUME_CHAPTER_OUTLINE（v1=1-3、v2=4-6、v3=7-8），而合并
    只在整卷末尾跑一次。旧实现只读最新一版，于是只有最后一批被物化：2026-08-03
    《雾街债主》与其 A/B 对照书都只落了第 7、8 章，滚动窗口永不完整，每次写作都
    抛 "Approved rolling outline window is not fully materialized"，自愈据此无限
    重发同一个卷纲重规划。
    """
    from bestseller.services.pipelines import _merge_progressive_outline_batch

    batches = [
        [{"chapter_number": n} for n in (1, 2, 3)],
        [{"chapter_number": n} for n in (4, 5, 6)],
        [{"chapter_number": n} for n in (7, 8)],
    ]

    merged: list = []
    for batch in batches:
        merged = _merge_progressive_outline_batch(merged, batch)
    assert [c["chapter_number"] for c in merged] == [1, 2, 3, 4, 5, 6, 7, 8]

    # 重发的同一批次可以安全重放（自愈会反复重生成最后一批）
    for _ in range(3):
        merged = _merge_progressive_outline_batch(merged, batches[-1])
    assert [c["chapter_number"] for c in merged] == [1, 2, 3, 4, 5, 6, 7, 8]


def test_constraint_block_shrinks_with_the_remaining_budget() -> None:
    """固定上界不够——基础 prompt 已占约 92% 预算，上界必须随剩余空间收缩。

    2026-08-03 两次真机：《雾街债主》15336 与《废意回收》15685，都超过 14400 可用。
    删掉 8257 字技能清单后仍然超，因为基础 prompt 自身就是 13217 tokens。
    """
    from bestseller.services.planner import _bounded_outline_constraints

    directives = [f"指令{i}：" + "补写具体冲突。" * 8 for i in range(20)]

    # 完全没有余量 → 全部丢弃（宁可少指令，也不能编译失败把书弄死）
    assert _bounded_outline_constraints(directives, headroom_chars=0) == []

    # 余量很小 → 只保留最新的、且不超过余量
    tight = _bounded_outline_constraints(directives, headroom_chars=200)
    rendered = "\n".join(f"- {c}" for c in tight)
    assert len(rendered) <= 200
    if tight:
        assert tight[-1] == directives[-1]

    # 余量充足 → 原样透传
    small = ["补第 1 章可见冲突", "第 2 章补一个具体损失"]
    assert _bounded_outline_constraints(small, headroom_chars=4000) == small


def test_settled_chapters_count_as_written_for_volume_advance() -> None:
    """「已定稿」只许有一个定义，否则书在窗口边界原地打转。

    `book_closure` 把 quality_debt / repair_exhausted / needs_human_review 都算
    已定稿并据此提升定稿、导出成书；而卷推进计数曾要求 production_state=="ok"。
    2026-08-03《废意回收》8 章全部 quality_debt（质量系统自己的终局判定：停止
    修复、发布最好的稿），推进计数却报 0/8 written，于是日志写下
    "not advancing to later volumes"，第 2 个窗口永不规划，30 章的书永远停在
    第 8 章。这条测试锁住"两处必须一致的事实只许住在一个地方"。
    """
    import inspect

    from bestseller.services import pipelines
    from bestseller.services.book_closure import SETTLED_PRODUCTION_STATES

    source = inspect.getsource(pipelines._count_written_chapters_in_volume)
    assert 'ChapterModel.production_state == "ok"' not in source
    assert "ChapterModel.production_state.in_" in source
    assert "SETTLED_PRODUCTION_STATES" in source
    # 这个集合就是判据本身：debt 状态必须在里面，否则回归无声复发
    assert {"ok", "quality_debt", "repair_exhausted"} <= SETTLED_PRODUCTION_STATES


def test_a_promoted_draft_is_not_re_judged_at_export() -> None:
    """定稿 = 发布决定已经做完，导出只许记账不许翻案。

    2026-08-04：50 章的书 completed、50 稿全部提升，却没有整书导出——第 28 章
    production_state=ok，在导出时被重跑的质量快照判 ANTI_META_ENDING_OUT_OF_SCENE
    而否决全书。让步集合是 `EXPORT_SHIPPABLE - {"ok"}`：判得越干净越会被一票否决。
    而且两次跑的不是同一个检查（生成时上下文窗口窄，导出时把全部前文都传进去）。
    """
    import inspect

    from bestseller.services import exports

    source = inspect.getsource(exports.collect_publication_blockers)
    assert "_is_promoted" in source
    assert 'promotion_state' in source
    # 让步判据必须含"已提升"，不能只看 debt 状态
    assert "_EXPORT_DEBT_PRODUCTION_STATES or _is_promoted" in source
    # 结构性检查仍在让步之前，不受影响
    assert source.index("no scene provenance") < source.index("_is_promoted")


def test_the_export_gate_cannot_overturn_a_settled_chapter() -> None:
    """终局导出门不得推翻生产管线已经做完的裁决。

    2026-08-04：50 章的书走到 completed、50 稿全部提升，却**没有整书导出**——
    第 28 章（自己的管线判 ok，不在 debt 名单里）在导出前被终检判
    ANTI_META_ENDING_OUT_OF_SCENE，于是整本书不许发布，读者拿到 50 个散文件。
    上一次修复只豁免了 quality_debt 章，把干净章这条路留着，同一个坑换个入口。

    闭环的前提是每一章都已终局，所以这里没有"还该保留牙齿"的章。
    """
    conceded: list[str] = []
    from bestseller.services.book_closure import _closure_quality_gate

    gate = _closure_quality_gate((1, 2), conceded)

    class _Failed:
        passed = False
        patched_text = "正文"
        errors = ("第28章：统一质量快照未通过（ANTI_META_ENDING_OUT_OF_SCENE）",)
        issues: tuple[str, ...] = ()

    import bestseller.services.pipelines as pipelines_module

    original = pipelines_module.run_final_quality_gates
    pipelines_module.run_final_quality_gates = lambda **kw: _Failed()
    try:
        # 干净章（不在 debt 名单）也必须被放行并记账
        clean = gate(chapter_number=28)
        assert clean.passed is True
        # debt 章照旧放行
        debt = gate(chapter_number=1)
        assert debt.passed is True
    finally:
        pipelines_module.run_final_quality_gates = original

    assert len(conceded) == 2
    assert any("第28章" in item and "复审不过" in item for item in conceded)
    assert any("第1章" in item and "预算耗尽" in item for item in conceded)


def test_the_repeated_phrase_ban_list_respects_the_prose_profile() -> None:
    """lean 剖面赶出去的块，不许从重写路径的后门回来。

    lean 明确排除【全书重复词禁用清单】，代码注释写着「会把写手逼去发明新黑话
    绕开禁用词」。但 `_render_quality_uplift_rewrite_block` 无条件拼接它，而大部分
    成稿由重写路径产出——被排除的块照样到了写手手上（2026-08-04）。
    """
    import inspect

    from bestseller.services import reviews
    from bestseller.services.prose_prompt_profile import LEAN_DROPPED_SECTIONS

    assert "quality_uplift" in LEAN_DROPPED_SECTIONS
    source = inspect.getsource(reviews._render_quality_uplift_rewrite_block)
    assert "prose_profile_drops_section" in source
    # 本章自己的修复指令不属于该 section，必须仍然下发
    assert "rewrite_escalation" in source


def test_the_ban_list_bans_style_not_chinese_grammar() -> None:
    """禁用清单只许禁文体癖，不许禁中文语法，也不许把一个短语算成五条。

    2026-08-04《全家嫌我废物…》的清单前三条是「了一下」「出来的」「的时候」，
    并把「左手虎口那道旧疤」切成 5 个重叠滑窗各列一条。
    """
    from bestseller.services.cross_chapter_ngram_tracker import (
        _is_grammatical_collocation,
        _windows_of_one_phrase,
    )

    for grammar in ("了一下", "出来的", "的时候", "的样子"):
        assert _is_grammatical_collocation(grammar), grammar
    for imagery in ("把玉牌", "那口漏", "三房祖坟", "灶坑底下"):
        assert not _is_grammatical_collocation(imagery), imagery

    # 同一短语的滑窗合并；不同短语不合并
    assert _windows_of_one_phrase("左手虎口那", "手虎口那道")
    assert _windows_of_one_phrase("手虎口那道", "虎口那道旧")
    assert not _windows_of_one_phrase("三房祖坟", "灶坑底下")


def test_an_interrupted_run_cannot_strand_a_chapter_on_its_worst_draft() -> None:
    """被打断的运行不得让某章永远挂着残稿。

    章循环先把 is_current 翻给刚生成的稿，最后才排名选优。运行若死在这中间
    （worker 重启 / ARQ 取消 / 锁超时），该章就停在最后写下的那份上——可能是残稿。
    而后再没人回头看它：章已 blocked、修复预算已耗尽，没有管线会再打开它。

    2026-08-03《废意回收》第 7 章：写手返回 9 字残稿，本该给它排名的那次 repair
    在 78 秒后被取消，于是这份残稿把整本书的收尾卡了八小时，而 2474 字的好稿就在
    隔壁一行。修复由 repair 开工前统一扫一遍，排名复用章循环那个同一个 helper。
    """
    import inspect

    from bestseller.services import repair

    assert hasattr(repair, "_restore_best_chapter_drafts")
    sweep = inspect.getsource(repair._restore_best_chapter_drafts)
    # 排名必须复用同一个 helper，不许另起一套「最好」的定义
    assert "_promote_best_scoring_chapter_draft_on_stall" in sweep
    # 换稿要留声
    assert "logger.warning" in sweep

    entry = inspect.getsource(repair.run_project_repair)
    assert "_restore_best_chapter_drafts(session, project)" in entry
    # 必须在修复正式开工之前跑，否则闸门读到的还是残稿
    assert entry.index("_restore_best_chapter_drafts") < entry.index(
        'current_step_name = "collect_pending_rewrite_tasks"'
    )


def test_bookkeeping_writes_cannot_abort_a_generation_transaction() -> None:
    """记账类写入不得有能力拖垮整本书。

    `diversity_budgets` 每个项目一行，而每条章管线都写它——同一本书并发两条管线
    就抢同一行的行锁。`lock_timeout=2s` 取消掉输的那条语句，而被取消的语句会中止
    它所在的**整个事务**：2026-08-03 一条记账 INSERT 就此杀掉了《废意回收》一次
    28 分钟的 autowrite，管线的下一条语句报
    `InFailedSQLTransactionError: current transaction is aborted`。

    这是提示词当参考读的多样性账，不是正文。SAVEPOINT 把锁超时关在这条语句里，
    漏掉的一次由下一章的写入补上。
    """
    import inspect

    from bestseller.services import diversity_budget

    source = inspect.getsource(diversity_budget.save_diversity_budget)
    assert "session.begin_nested()" in source, "记账写入缺少 SAVEPOINT 隔离"
    # 必须包住真正的执行语句
    nested_idx = source.index("session.begin_nested()")
    exec_idx = source.index("await session.execute(stmt)")
    assert nested_idx < exec_idx
    # 失败要留声，不许静默
    assert "logger.warning" in source


def test_outline_findings_behind_the_written_frontier_are_advisory() -> None:
    """已交付的章不能再被审判来阻断没写的章。

    2026-08-03《废意回收》已写 16 章、已排 23 章，整书大纲语义门却因第 13 章一条
    OUTLINE_STATE_REGRESSION 和第 1 章七条 OUTLINE_REUSED_PAYLOAD_ANCHOR 判定
    needs_replan——全部位于写作前沿之后方。这些发现无法执行：你不会去给一本已经
    发出正文的第 1 章重规划大纲。而项目就此没有任何所有者：正文通道拒绝
    needs_replan 的书，重规划通道拒绝已有正文的书。

    前沿用的是 current_chapter_number——滚动窗口推进用的同一个指针。
    """
    import inspect

    from bestseller.services import pipelines

    source = inspect.getsource(pipelines._record_outline_semantic_gate)
    assert "_written_frontier" in source
    assert 'getattr(project, "current_chapter_number", 0)' in source
    # 必须在 promotion_allowed 之前过滤，否则等于没过滤
    assert source.index("_settled_findings = tuple(") < source.index(
        '"promotion_allowed": not hard_findings'
    )
    # 被过滤掉的仍需留在报告里，不许静默吞掉
    assert '"settled_chapter_findings"' in source


def test_a_degenerate_regeneration_cannot_evict_a_healthy_chapter() -> None:
    """退化生成不得顶掉一份健康的正文。

    2026-08-03：整章写手对《废意回收》第 7 章返回了「# 第7章：灶桩\\n\\n阿苓先醒的。」
    ——7 个 output token，finish_reason=stop，不是截断而是退化。整章生成路径把这份
    9 字残稿设为当前稿，挤掉了 2474 字的好稿；该章从此恒为 blocked（残稿过不了任何
    门），又反过来污染了窗口跳过计数与顺序守卫两个下游判断。

    同一个判断在 reviews 的章重写与场景重写守卫里早就存在，唯独「重新生成整章」
    这个入口没有。判据必须与那两处同形——「低于章节硬下限」**且**「比被替换的稿
    明显短」，两个条件缺一不可：纯比例会误伤合法瘦身（同一本书第 11 章从 4541 字
    正确改到 2226 字，目标约 2600）。
    """
    import inspect

    from bestseller.services import drafts

    # 与 reviews 两处守卫同一个比例
    assert drafts._DEGENERATE_REGENERATION_RATIO == 0.85

    source = inspect.getsource(drafts)
    anchor = "_keeps_prior_draft = bool("
    assert anchor in source, "整章生成路径的退化守卫不见了"
    block = source.split(anchor, 1)[1][:600]
    # 下限与比例必须同时出现——只有比例会拦掉合法瘦身
    assert "word_count < _floor" in block
    assert "_DEGENERATE_REGENERATION_RATIO" in block

    tail = source.split(anchor, 1)[1][:2500]
    # 守卫命中时：不得清掉旧的 is_current，新稿也不得成为当前稿
    assert "is_current=not _keeps_prior_draft" in tail
    assert "if not _keeps_prior_draft:" in tail


def test_a_settled_chapter_is_not_a_sequence_gap() -> None:
    """已定稿的章不是「还没写完」，不得拦住下一章。

    顺序守卫用 `production_state != "ok"` 筛，然后把 quality_debt 章送进
    `chapter_block_is_structural`——那个分类器的 docstring 明写「用于 blocked
    的章」，而已定稿章不带任何可识别的门禁键，于是落到「无法识别→保守判为
    结构性」的兜底。2026-08-03《废意回收》第 1、2、5、8 章（全部已定稿、全部
    有当前稿）因此把第 9 章永久拦在门外。
    """
    import inspect

    from bestseller.services import pipelines
    from bestseller.services.book_closure import SETTLED_PRODUCTION_STATES

    source = inspect.getsource(pipelines._load_prior_incomplete_chapter_numbers)
    assert (
        'if production_state != "ok" and chapter_block_is_structural' not in source
    )
    assert "SETTLED_PRODUCTION_STATES" in source
    # 已定稿仍必须在真正被拦之前短路掉分类器
    settled_idx = source.index("SETTLED_PRODUCTION_STATES")
    classifier_idx = source.index("chapter_block_is_structural")
    assert settled_idx < classifier_idx
    assert "quality_debt" in SETTLED_PRODUCTION_STATES


def test_book_in_progress_is_not_judged_by_whole_book_consistency() -> None:
    """没写完的书不能用整书一致性去审——审完还硬阻断就等于永不完稿。

    自愈的 project_pipeline 通道不传 current_volume_number / chapter_numbers，
    于是一本 8/30 章的书走了整书判定，verdict=attention（它本来就还没有结局），
    requires_human_review=True，卷循环因此停下，窗口 2 再也没被规划。
    """
    from bestseller.services.pipelines import _project_consistency_warn_only_scope

    # 明确的分片信号照旧生效
    assert (
        _project_consistency_warn_only_scope(
            current_volume_number=1, chapter_numbers=None
        )
        == "partial_volume"
    )
    # 没有分片信号，但书还没写完 → 仍然只是警告
    assert (
        _project_consistency_warn_only_scope(
            current_volume_number=None,
            chapter_numbers=None,
            written_chapters=8,
            target_chapters=30,
        )
        == "book_in_progress"
    )
    # 全书写完 → 整书一致性恢复完整效力
    assert (
        _project_consistency_warn_only_scope(
            current_volume_number=None,
            chapter_numbers=None,
            written_chapters=30,
            target_chapters=30,
        )
        is None
    )
    # 目标未知时不得凭空豁免
    assert (
        _project_consistency_warn_only_scope(
            current_volume_number=None,
            chapter_numbers=None,
            written_chapters=0,
            target_chapters=0,
        )
        is None
    )


def test_forward_window_advance_does_not_reject_the_architecture() -> None:
    """窗口推进到尚未规划的章是进度，不是架构失效。

    滚动窗口在上一段写完后立刻前移，于是每本多窗口的书都必然经历"新窗口已存在、
    其章节尚未物化"的一刻。旧实现在这一刻写 needs_replan：自愈的规划通道只在零
    正文时运行，正文通道又拒绝一切架构被判无效的书——刚写完 8 章好正文的书就此
    永久静止，只剩每分钟一条 skip 日志（2026-08-03《废意回收》）。
    """
    import inspect

    from bestseller.services import pipelines

    source = inspect.getsource(pipelines._select_rolling_outline_window)
    forward_branch = source.split("rolling_window_pending_materialization")[0]
    # 前向推进分支必须出现在 needs_replan 之前，且自身不写 needs_replan
    assert "needs_replan" not in forward_branch.split("if missing == expected_numbers")[-1]
    assert "materialized_frontier" in source
    assert "min(missing) > materialized_frontier" in source


def test_window_skip_uses_the_same_pointer_the_window_selector_advances_on() -> None:
    """判断「这个窗口已经过去了」必须用推进窗口的那同一个指针。

    第一版用「窗口内已定稿章数」判断，结果 project_repair 把其中一章从
    quality_debt 翻成 blocked，计数变 7/8，循环于是重进已经写完的窗口 1，
    再次撞上「窗口已推进到 9-16」而中止——同一本书同一天连栽两次。
    修复那一章是修复通道的活，不是写作循环的活。
    """
    import inspect

    from bestseller.services import pipelines

    loop = inspect.getsource(pipelines.run_progressive_autowrite_pipeline)
    selector = inspect.getsource(pipelines._select_rolling_outline_window)

    # 选择器用 current_chapter >= window_end 推进窗口
    assert "current_chapter >= window_end" in selector
    # 循环必须用同一个指针判断窗口是否已过去
    skip = loop.split("is behind the written frontier", 1)
    assert len(skip) == 2, "窗口跳过分支不见了"
    assert "written_frontier >= window_end" in loop
    assert "getattr(project, \"current_chapter_number\", 0)" in loop
    # 不得回退成按章节状态计数（那是第三种说法）
    assert "_count_settled_chapters" not in loop


def test_nothing_to_repair_is_not_nothing_to_do() -> None:
    """「没东西可修」不等于「没事可做」——续写通道必须还能轮到。

    8/30 章的书，前 8 章全部终局 quality_debt，自愈据此 `continue` 掉整个项目，
    于是下面的 under_target_chapters 续写通道永远没机会派活：每 5 分钟打印一次
    「no actionable repair」，第 9 章永远不写（2026-08-03《废意回收》）。
    这一分支已经通过了 can_continue 检查——写作是明确被允许的。
    """
    import inspect

    from bestseller.worker import self_heal

    source = inspect.getsource(self_heal.find_stuck_projects)
    anchor = "has only terminal quality-debt chapters"
    assert anchor in source
    branch = source.split(anchor, 1)[1][:900]
    # 分支体内不得再出现 continue（那会跳过整个项目，饿死续写）
    body = branch.split("local_repair_pending = False", 1)[0]
    assert "continue" not in body


def test_a_rolling_window_numbers_chapters_from_the_window_not_the_volume() -> None:
    """「这段规划从第几章起」不许由 volume_number 回答。

    滚动执行把一个叙事卷切成多个细化窗口，四个窗口的 volume_number 全是 1。
    `_next_chapter_number_for_volume(volume=1)` 返回卷 1 的 min(chapter_number)=1，
    于是窗口 2 被当成「重规划卷 1」，第三次生成第 7、8 章而不是 9-16。
    结果：循环已正确推进到下一个窗口，书却依然长不过第 8 章
    （2026-08-03《废意回收》）。窗口条目来自持久化且经过连续性校验的
    rolling_outline_windows，不是漂移的 VOLUME_PLAN 目标。
    """
    import inspect

    from bestseller.services import planner

    source = inspect.getsource(planner.generate_volume_plan)
    anchor = "chapter_number_offset = await _next_chapter_number_for_volume"
    assert anchor in source
    after = source.split(anchor, 1)[1][:2000]
    assert "rolling_window_index" in after
    assert "chapter_number_offset = _window_start" in after


def test_a_repair_baseline_only_applies_to_the_chapters_it_covers() -> None:
    """修复基线只对它自己覆盖的章有效。

    两处基线恢复都按 volume_number 取，而滚动执行下一卷含多个窗口、共用同一个
    volume_number：规划窗口 2 取到了窗口 1 的大纲（第 7、8 章），走「外科修复」
    把它改一改再存一遍，连存三版且 input_hash 完全相同，第 9-16 章从未被规划
    （2026-08-03《废意回收》）。
    """
    from bestseller.services.planner import _outline_baseline_covers_window

    window = frozenset(range(9, 17))
    window_one_outline = {"chapters": [{"chapter_number": 7}, {"chapter_number": 8}]}
    window_two_outline = {
        "chapters": [{"chapter_number": n} for n in range(9, 17)]
    }

    assert _outline_baseline_covers_window(window_one_outline, window) is False
    assert _outline_baseline_covers_window(window_two_outline, window) is True
    # 部分覆盖也算不匹配（跨窗口的半截基线会把章号拖回去）
    assert (
        _outline_baseline_covers_window(
            {"chapters": [{"chapter_number": 8}, {"chapter_number": 9}]}, window
        )
        is False
    )
    # 没有章号的载荷不构成"不匹配"的证据，保持旧行为
    assert _outline_baseline_covers_window({"chapters": []}, window) is True
    assert _outline_baseline_covers_window(None, window) is True


def test_one_judgement_has_one_severity_across_both_enforcement_points() -> None:
    """同一条判定不许在两个执行点给出两个答案。

    NAMING_POOL_UNDERSIZED 在 write_gate 已降 audit_only，但 bible_gate 这个
    第二执行点仍然 raise ValueError 拒绝落库。2026-08-03《废意回收》在窗口 2
    边界正是被它拦住：书本身完好，只因备用名列表不够长就拒绝物化，滚动窗口
    因此永不完整，自愈无限重发同一个计划。
    """
    from bestseller.services.bible_gate import (
        ADVISORY_CODES,
        BibleCompletenessReport,
        BibleDeficiency,
    )
    from bestseller.services.write_gate import DEFAULT_GATE_CONFIG

    severity = DEFAULT_GATE_CONFIG.mode_by_violation
    for code in ADVISORY_CODES:
        assert severity.get(code, "audit_only") == "audit_only", code

    naming = BibleDeficiency(
        code="NAMING_POOL_UNDERSIZED",
        location="naming_pool",
        detail="池容量 6 < 要求 12",
        prompt_feedback="补充候选名",
    )
    structural = BibleDeficiency(
        code="CORE_WOUND_MISSING",
        location="cast",
        detail="主角缺核心创伤",
        prompt_feedback="补写",
    )

    # 只有备用名不足 → 放行，但仍然被记录下来（不是悄悄吞掉）
    advisory_only = BibleCompletenessReport(deficiencies=(naming,))
    assert advisory_only.passes is True
    assert advisory_only.advisory_deficiencies == (naming,)
    assert advisory_only.blocking_deficiencies == ()

    # 真结构缺陷照旧阻断
    mixed = BibleCompletenessReport(deficiencies=(naming, structural))
    assert mixed.passes is False
    assert mixed.blocking_deficiencies == (structural,)


def test_an_unownable_project_is_announced_not_whispered() -> None:
    """没有任何通道能认领的书必须发一次 WARNING，而不是每分钟一条 INFO。

    「书没了」的真因一向是失败原因看不见。自愈两条通道互斥地跳过同一本书时，
    旧实现只留 INFO，等于书在无声地死着。
    """
    import inspect

    from bestseller.worker import self_heal

    source = inspect.getsource(self_heal.find_stuck_projects)
    anchor = "auto-runnable after prose was committed"
    assert anchor in source
    block = source.split(anchor)[0][-1200:]
    assert "logger.warning" in block
    assert "_INERT_PROJECTS_ANNOUNCED" in block
    # 不得回写 projects.metadata：跑书的管线会整块覆盖 JSONB 抹掉外部写入
    assert "project.metadata_json = {" not in block


def test_no_private_story_content_in_shared_config() -> None:
    """共享配置不得携带某一本书的具体故事内容。

    2026-08-04：通用角色原型库 config/character_engine.yaml 里整套写着同一个
    民国探案故事——「十五年前沈家灭门」「副捕头」「替沈家人讨债」——而随后
    生成的一本玄幻书，主角恰好姓沈、家族叫沈家、故事引擎是欠条。这些样例
    当时并未真正渲染进 prompt（id 不匹配），但共享位放着一本书的私货就是
    上了膛的枪：任何一次 id 对上或开关打开，它就会漏进每一本书。
    原则：具体内容删，机制骨架留。
    """
    import pathlib

    banned = ("沈家", "讨债")
    for name in (
        "character_engine.yaml",
        "chapter_signature_audit.yaml",
        "information_choreography.yaml",
        "scene_grounding.yaml",
    ):
        path = pathlib.Path("config") / name
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if line.lstrip().startswith("#"):
                continue  # 注释不进 YAML 解析，到不了模型
            for token in banned:
                assert token not in line, f"{name}:{lineno} 仍带私货「{token}」：{line.strip()[:60]}"


def test_promise_extraction_does_not_name_debt() -> None:
    """人际承诺机制保留，但不得把「债务」立成一等公民类别。

    2026-08-04：feedback 每章问模型抽「新立的承诺/誓言/债务」，且 debt 是枚举
    首项；抽出的结果存进人际张力台账，台账再经 render_promises_block 回灌写手
    prompt（其文案同样写着「亏欠」）——设计上是个自我强化循环。
    （本次样本书该表 0 行，循环未闭合，所以这是拆隐患不是拆现行。）
    methodology_profiles/platform_character_debt_v1.yaml 早已把该台账标注为
    「债务同质化结构性根因」并降级闸门，但抽取端的词一直没动。
    机制（追踪未了结的承诺）保留，词摘掉。
    """
    from bestseller.services import feedback, interpersonal_promises

    for text in (feedback._SYSTEM_PROMPT_ZH, feedback._SYSTEM_PROMPT_EN):
        assert "债务" not in text
        assert "debt" not in text.lower()
    # 机制本身必须还在
    assert "promises_made" in feedback._SYSTEM_PROMPT_ZH
    assert "fealty" in feedback._SYSTEM_PROMPT_ZH

    block_src = inspect.getsource(interpersonal_promises.render_promises_block)
    assert "亏欠" not in block_src
    assert "未了的人际承诺" in block_src


def test_logline_voice_rules_name_no_banned_vocabulary() -> None:
    """禁令不得点名要禁的词——点名就是注入。

    这是 test_concept_generation_prompt_names_no_motif_vocabulary 那次清剿
    （2026-08-03《雾街债主》）的漏网：`_LOGLINE_VOICE_RULES` 仍点名
    「把柄/筹码/代价/博弈/记账」，而 2026-08-04 的样本书 logline 里就写着
    「每一处漏口都让他在祠堂里多换一枚筹码」——被点名的词原样出现在成品里。
    规则改成只说要什么，不说不要什么。
    """
    from bestseller.services.conception import _LOGLINE_VOICE_RULES

    for token in ("把柄", "筹码", "博弈", "记账", "债", "账"):
        assert token not in _LOGLINE_VOICE_RULES, f"logline 规则又点名「{token}」"
    # 规则本身必须还在起作用
    assert "25-45字" in _LOGLINE_VOICE_RULES
    assert "逗号串三段摘要" in _LOGLINE_VOICE_RULES
