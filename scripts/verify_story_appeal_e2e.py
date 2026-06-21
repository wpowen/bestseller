"""End-to-end verification for the story / blurb appeal evaluation system.

Drives the REAL runtime paths:
  * ``evaluate_story_appeal``  (orchestrator → real deterministic blurb gate +
    premise judge with genre-lexicon resolution)
  * ``run_conception_pipeline`` (the real multi-agent conception, with its LLM
    stages stubbed) → proves the appeal report is attached to ``ConceptionResult``,
    that weak ideas trigger bounded keep-best regeneration, and that disabling
    the config is byte-identical (no-op contract).

Zero token / zero side-effect:
  * ``bestseller.services.llm.complete_text`` is stubbed (the appeal judge call).
  * ``conception._llm_call_json`` is stubbed (every conception stage → fallback,
    or a controllable finalize result for the A/B + regeneration scenarios).
  * No DB commit happens (conception never commits; stubs make zero LLM-run rows).
    When a live stack is up we snapshot ``llm_runs`` + ``projects`` counts before
    and after and assert they are unchanged.

Run:  .venv/bin/python scripts/verify_story_appeal_e2e.py
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN003, ANN202, RUF001, E501, S110 — verification script.
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import bestseller.services.conception as conception_mod
from bestseller.services.conception import run_conception_pipeline
import bestseller.services.llm as llm_mod
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.story_appeal import evaluate_story_appeal, load_story_appeal_config
from bestseller.settings import load_settings

# ─────────────────────────────────────────────────────────────────────────────
# Genre-appropriate STRONG blurbs (what a bestseller listing actually looks like)
# ─────────────────────────────────────────────────────────────────────────────
STRONG_BY_GENRE: dict[str, dict] = {
    "玄幻": {
        "title": "我的命格能吞噬",
        "premise": "被宗门判为废体逐出的少年，觉醒能吞噬他人天赋的逆天命格，每被羞辱一次就更强一分。",
        "synopsis": (
            "宗门大比上，他被当众判为废体，逐出师门。\n"
            "临走那天，仇人嗤笑：三年后的祭天大典，就是你的死期。\n"
            "没人知道，他刚刚觉醒了能吞噬一切天赋的命格——你们越是羞辱，我便越强。\n"
            "这一次，曾经踩着他往上爬的天才们，要一个个把命格还回来。"
        ),
        "tags": ["废体逆袭", "吞噬", "打脸", "玄幻", "热血"],
    },
    "都市": {
        "title": "我老婆是首富",
        "premise": "被退婚的废物赘婿，其实是隐藏的商业帝国之主，三天对赌局里步步打脸翻盘。",
        "synopsis": (
            "三年赘婿，受尽白眼。\n"
            "退婚宴上，岳父当众羞辱：三天内拿不出一个亿，就滚出林家。\n"
            "没人知道，他随手转出的，是足以买下整座城的隐藏身份。\n"
            "这一次，他要让所有看不起他的人，跪着求他签字。"
        ),
        "tags": ["赘婿", "打脸", "马甲", "都市", "逆袭"],
    },
    "仙侠": {
        "title": "我在仙界当杂役",
        "premise": "被灭门的散修少年带着一缕仙界传承重生，靠最低贱的杂役身份步步求道，要让仇家高高在上的仙门跪下。",
        "synopsis": (
            "全村被灭那夜，他抱着师父的尸体逃进深山。\n"
            "再睁眼，一缕来自仙界的传承烙进了他的识海：想活，就先苟住。\n"
            "于是他进了仇家的仙门，做最低贱的扫地杂役——白天挨骂，夜里偷修。\n"
            "他们不知道，这个不起眼的杂役，迟早要让整座仙门为当年的血债跪下。"
        ),
        "tags": ["散修崛起", "复仇", "求道", "仙侠", "扮猪吃虎"],
    },
    "科幻": {
        "title": "规则怪谈：我能看见数值",
        "premise": "全球降临诡异规则副本，只有他能看见万物头顶的隐藏数值，靠一张数据之眼在必死规则里逆推活路。",
        "synopsis": (
            "凌晨三点，整座城市的人同时收到一条短信：游戏开始，违规者死。\n"
            "电梯不能在十三楼停、镜子里的人不会眨眼——所有规则都在杀人。\n"
            "而他，是唯一能看见万物头顶隐藏数值的人。\n"
            "当所有人盯着规则发抖时，他已经在用数据，反推出这场死亡游戏的唯一活路。"
        ),
        "tags": ["规则怪谈", "数据流", "悬疑", "科幻", "无限流"],
    },
    "末世": {
        "title": "末世第一天我囤了座山",
        "premise": "重生回末世爆发前一天，他握着前世记忆疯狂囤货,在丧尸潮里把唯一的避难所变成所有人求生的筹码。",
        "synopsis": (
            "重生了，回到丧尸爆发的前一天。\n"
            "前世他饿死在第七天，这一世，他把整座仓库的物资搬空了。\n"
            "三天后天降红雨，邻居们在门外哀求，而他守着堆成山的物资冷笑。\n"
            "末世里最硬的通货不是黄金，是命——而别人的命，现在都攥在他手里。"
        ),
        "tags": ["末世重生", "囤货", "异能觉醒", "末世", "爽文"],
    },
    "纯爱": {
        "title": "他偏要宠我",
        "premise": "重生回被豪门未婚夫背叛前夜，她决意断情绝爱搞事业,偏偏那个传闻心狠手辣的男人,这一世非她不可。",
        "synopsis": (
            "重活一世，她睁眼正是被未婚夫和闺蜜联手算计的前夜。\n"
            "上一世为爱卑微到死，这一世她只想搞钱、复仇、绝不再爱。\n"
            "可那个传闻冷血到没有心的男人，却把她护在身后：你欠我的，用一辈子还。\n"
            "她想逃，他偏要宠——这一次，换他为她低到尘埃里。"
        ),
        "tags": ["重生", "先婚后爱", "复仇", "甜宠", "豪门"],
    },
    "悬疑": {
        "title": "每死一次我就回到案发前",
        "premise": "刑警在追查连环凶案时发现自己每次殉职都会回到案发前十二小时,用一次次死亡逼近那个藏在身边的真凶。",
        "synopsis": (
            "第一次中刀倒地时，他以为自己死了。\n"
            "再睁眼，时间回到了案发前十二小时——同样的雨夜，同样的死局。\n"
            "他发现自己每死一次，就能回到原点，可凶手也越来越警觉。\n"
            "当死亡成了唯一的线索，他必须赶在下一次倒下前，揪出那个一直站在他身边的人。"
        ),
        "tags": ["时间循环", "刑侦", "反转", "悬疑", "烧脑"],
    },
    "女频": {
        "title": "真千金回来后全家跪了",
        "premise": "被赶出家门的真千金带着满身硬核本事归来,假千金还在炫耀身世,她已经一个电话掀了对方所有马甲。",
        "synopsis": (
            "认亲宴上，所有人都等着看那个乡下来的真千金出丑。\n"
            "假千金端着红酒挑衅：没文化的野丫头，也配姓沈？\n"
            "下一秒，国际医学峰会的请柬、商业帝国的股权、神秘大佬的电话同时砸来。\n"
            "她笑着拨通电话：哥，他们欺负我——然后，整个豪门跪成一片。"
        ),
        "tags": ["真假千金", "马甲", "打脸", "团宠", "大女主"],
    },
}

WEAK = {
    "title": "风云传说",
    "premise": "一个少年踏上修炼之路，历经磨难最终成为强者的故事。",
    "synopsis": (
        "这是一个关于成长的故事。主角本以为生活很平凡，却没想到命运的齿轮开始转动，"
        "他将何去何从？一段不平凡的旅程就此展开，让我们拭目以待，敬请期待。"
    ),
    "tags": ["玄幻", "成长"],
}


def _looks_weak(prompt: str) -> bool:
    return any(m in prompt for m in ("敬请期待", "命运的齿轮", "何去何从", "拭目以待"))


def _judge_json(strong: bool) -> str:
    keys = (
        "concept_strength", "novelty", "conflict_stakes", "emotional_value",
        "hook_suspense", "immersion", "sustainability", "audience_fit", "structure_pace",
    )
    val = 4.5 if strong else 1.8
    return json.dumps(
        {
            "dimension_scores": dict.fromkeys(keys, val),
            "rationale": {"concept_strength": "强卖点" if strong else "卖点模糊"},
            "suggestions": [] if strong else ["补齐身份+冲突+代价", "首句换强钩"],
            "overall_comment": "editor-sim",
        },
        ensure_ascii=False,
    )


async def _fake_complete_text(session, settings, request):
    """Content-aware judge stub — discriminates strong vs weak from the prompt.

    Non-judge calls degrade to their declared ``fallback_response`` (the 范本
    pattern), so any other LLM stage stays deterministic + offline.
    """

    template = getattr(request, "prompt_template", "") or ""
    if template == "premise_appeal_judge":
        prompt = f"{getattr(request, 'user_prompt', '')}"
        return LLMCompletionResult(
            content=_judge_json(strong=not _looks_weak(prompt)),
            provider="fake", model_name="fake", llm_run_id=uuid4(),
        )
    return LLMCompletionResult(
        content=getattr(request, "fallback_response", None) or "（mock）",
        provider="fake", model_name="fake",
    )


def _install_complete_text_stub() -> None:
    # Pre-import every module that binds complete_text at module level AND is
    # only lazily imported by conception at runtime — otherwise it isn't in
    # sys.modules yet when we patch and keeps the REAL complete_text (→ a stray
    # llm_run row). These are conception's runtime sub-agents.
    for name in (
        "bestseller.services.concept_methodology_agent",
        "bestseller.services.anti_commonsense_hook",
        "bestseller.services.platform_title_workflow",
        "bestseller.services.brainhole_engine",
        "bestseller.services.concept_lab",
        "bestseller.services.ideology_kernel",
    ):
        try:
            __import__(name)
        except Exception:
            pass
    llm_mod.complete_text = _fake_complete_text
    for module in list(__import__("sys").modules.values()):
        if module is None:
            continue
        if getattr(module, "complete_text", None) is not None and module is not llm_mod:
            try:
                module.complete_text = _fake_complete_text
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Scenario A — every genre can reach the bestseller-grade bar
# ─────────────────────────────────────────────────────────────────────────────
async def scenario_multi_genre(session, settings) -> bool:
    print("\n=== Scenario A · 每题材达标 (genre-appropriate strong blurbs) ===")
    print(f"{'题材':<6}{'premise':>9}{'blurb':>8}  {'grade':<10}{'meets_bar'}")
    all_pass = True
    for genre, b in STRONG_BY_GENRE.items():
        report = await evaluate_story_appeal(
            session, settings,
            premise=b["premise"], synopsis=b["synopsis"], title=b["title"],
            tags=b["tags"], genre=genre, sub_genre=None, chapter_count=600,
        )
        ok = report.meets_bar
        all_pass = all_pass and ok
        flag = "✅" if ok else "❌"
        print(
            f"{genre:<6}{report.premise.total:>9.0f}{report.blurb.total:>8.0f}  "
            f"{report.overall_grade:<10}{flag} (canon={report.canonical_genre})"
        )
    print(f"→ 全题材达标: {'PASS ✅' if all_pass else 'FAIL ❌'}")
    return all_pass


# ─────────────────────────────────────────────────────────────────────────────
# Scenario B — strong vs weak discrimination on the same genre
# ─────────────────────────────────────────────────────────────────────────────
async def scenario_ab(session, settings) -> bool:
    print("\n=== Scenario B · 强/弱 A/B 判别 (玄幻) ===")
    strong = STRONG_BY_GENRE["玄幻"]
    s_rep = await evaluate_story_appeal(
        session, settings, premise=strong["premise"], synopsis=strong["synopsis"],
        title=strong["title"], tags=strong["tags"], genre="玄幻", sub_genre=None,
        chapter_count=600,
    )
    w_rep = await evaluate_story_appeal(
        session, settings, premise=WEAK["premise"], synopsis=WEAK["synopsis"],
        title=WEAK["title"], tags=WEAK["tags"], genre="玄幻", sub_genre=None,
        chapter_count=600,
    )
    print(f"STRONG: premise={s_rep.premise.total:.0f} blurb={s_rep.blurb.total:.0f} "
          f"grade={s_rep.overall_grade} meets_bar={s_rep.meets_bar}")
    print(f"WEAK  : premise={w_rep.premise.total:.0f} blurb={w_rep.blurb.total:.0f} "
          f"grade={w_rep.overall_grade} meets_bar={w_rep.meets_bar} gating={w_rep.premise.gating_caps}")
    print("  WEAK blurb findings:", list(w_rep.blurb.findings)[:3])
    ok = (
        s_rep.meets_bar and not w_rep.meets_bar
        and s_rep.premise.total > w_rep.premise.total + 20
        and len(w_rep.blurb.findings) > 0
    )
    print(f"→ 判别力: {'PASS ✅' if ok else 'FAIL ❌'}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Scenario C — full conception integration: report attached, regen, no-op
# ─────────────────────────────────────────────────────────────────────────────
def _install_conception_stub(weak_then_improve: bool) -> dict:
    state = {"finalize_calls": 0}

    async def fake_llm_call_json(session, settings, *, role, system_prompt, user_prompt,
                                 fallback, template, stage, language="zh-CN", **kw):
        if template == "conception_finalize":
            state["finalize_calls"] += 1
            if weak_then_improve and stage == "conception.final":
                # first finalize → weak idea
                return ({
                    "writing_profile": {}, "premise": WEAK["premise"],
                    "synopsis": WEAK["synopsis"], "title": WEAK["title"],
                    "tags": WEAK["tags"],
                }, [])
            # retry (appeal_retry) → improved, bestseller-grade idea
            strong = STRONG_BY_GENRE["玄幻"]
            return ({
                "writing_profile": {}, "premise": strong["premise"],
                "synopsis": strong["synopsis"], "title": strong["title"],
                "tags": strong["tags"],
            }, [])
        # every other stage → its declared fallback (parsed, deterministic).
        # ``fallback`` is the JSON string conception would use on LLM failure.
        try:
            parsed = json.loads(fallback) if isinstance(fallback, str) else fallback
        except Exception:
            parsed = {}
        return (parsed if isinstance(parsed, dict) else {}, [])

    conception_mod._llm_call_json = fake_llm_call_json
    return state


async def scenario_conception(session, settings) -> bool:
    print("\n=== Scenario C · 全 conception 集成 (重生 + no-op) ===")
    ok = True

    # C1: weak finalize → regeneration should fire and keep the better variant.
    state = _install_conception_stub(weak_then_improve=True)
    try:
        result = await run_conception_pipeline(
            session, settings, genre_key="xuanhuan", chapter_count=600,
            genre="玄幻", sub_genre="升级",
        )
    finally:
        pass
    appeal = result.story_appeal
    attached = bool(appeal) and "premise" in appeal and "blurb" in appeal
    regenerated = state["finalize_calls"] > 1
    improved = appeal.get("premise", {}).get("total", 0) >= 60
    print(f"C1 report_attached={attached} finalize_calls={state['finalize_calls']} "
          f"premise={appeal.get('premise', {}).get('total', 0):.0f} "
          f"blurb={appeal.get('blurb', {}).get('total', 0):.0f} meets_bar={appeal.get('meets_bar')}")
    c1 = attached and regenerated and improved
    print(f"   C1 (报告入库 + 弱稿触发重生 + 保优改善): {'PASS ✅' if c1 else 'FAIL ❌'}")
    ok = ok and c1

    # C2: no-op contract — disabled config → story_appeal stays {}.
    import bestseller.services.story_appeal as sa_mod

    load_story_appeal_config.cache_clear()
    real_loader = sa_mod.load_story_appeal_config

    def disabled_loader():
        return {"enabled": False}

    sa_mod.load_story_appeal_config = disabled_loader
    _install_conception_stub(weak_then_improve=False)
    try:
        result2 = await run_conception_pipeline(
            session, settings, genre_key="xuanhuan", chapter_count=600,
            genre="玄幻", sub_genre="升级",
        )
        c2 = result2.story_appeal == {}
        print(f"C2 disabled → story_appeal is empty dict: {c2}  "
              f"{'PASS ✅' if c2 else 'FAIL ❌'}")
        ok = ok and c2
    finally:
        sa_mod.load_story_appeal_config = real_loader
        load_story_appeal_config.cache_clear()
    return ok


async def main() -> None:
    settings = load_settings()
    _install_complete_text_stub()

    # Best-effort live-stack side-effect snapshot.
    before = await _db_counts()
    session = SimpleNamespace()  # appeal/judge path never touches the DB (slug=None)

    a = await scenario_multi_genre(session, settings)
    b = await scenario_ab(session, settings)
    c = await scenario_conception(session, settings)

    after = await _db_counts()
    if before is not None and after is not None:
        clean = before == after
        print(f"\n=== DB 零副作用 ===\nbefore={before} after={after} "
              f"{'PASS ✅ (未污染)' if clean else 'FAIL ❌ (有写入!)'}")
    else:
        print("\n=== DB 零副作用 ===\n(live 栈未连接 → 跳过；本功能 conception 期不写 DB)")

    print("\n" + "=" * 60)
    verdict = "ALL PASS ✅" if (a and b and c) else "SOME FAILED ❌"
    print(f"L3 端到端结论: A(多题材达标)={a}  B(判别)={b}  C(集成/重生/no-op)={c}  → {verdict}")
    print("=" * 60)


async def _db_counts():
    # Concurrency-robust: a live worker may be writing scene_* rows the whole
    # time, so total llm_runs is noisy. We track only signals THIS feature would
    # ever produce: persisted projects, and llm_run rows for the templates our
    # code emits. All deltas must be 0 (stubs never write; conception never
    # persists a project — the web layer does).
    try:
        from sqlalchemy import func, select

        from bestseller.infra.db.models import LlmRunModel, ProjectModel
        from bestseller.infra.db.session import session_scope

        async with session_scope() as s:
            projects = await s.scalar(select(func.count()).select_from(ProjectModel))
            feature_runs = await s.scalar(
                select(func.count())
                .select_from(LlmRunModel)
                .where(LlmRunModel.prompt_template.in_(
                    ("premise_appeal_judge", "conception_finalize")
                ))
            )
            fake_runs = await s.scalar(
                select(func.count())
                .select_from(LlmRunModel)
                .where(LlmRunModel.provider == "fake")
            )
            return {
                "projects": int(projects or 0),
                "feature_llm_runs": int(feature_runs or 0),
                "fake_llm_runs": int(fake_runs or 0),
            }
    except Exception:
        return None


if __name__ == "__main__":
    asyncio.run(main())
