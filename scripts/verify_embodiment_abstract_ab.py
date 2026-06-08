"""A/B 第二组(决定性)：当物料【抽象】时，角色入戏推演能否把正文从"作文"拉回"画面"？

第一组(具体物料)有天花板效应：基线已很活，入戏增益小。本组改喂【抽象 §default 机制
物料】(框架真实兜底的样子)，看入戏推演是否能补足具体度——即"入戏"是否是物料具体化的
替代/补充杠杆。这直接对应 [[scene-grounding-cinematic-gap]] 的 14× 物料具体度主杠杆。

四臂对照：
  A 抽象物料 + 直接写       (= 框架当前 §default 兜底的真实处境)
  B 抽象物料 + 入戏推演再写  (= 用户假设：入戏能否救场)
  对照锚 C 具体物料 + 直接写  (= 物料具体化杠杆，已知有效，作上界参照)

Run:  .venv/bin/python scripts/verify_embodiment_abstract_ab.py
"""

# ruff: noqa: RUF001, E501

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import statistics

from dotenv import load_dotenv

load_dotenv(".env")

import litellm  # noqa: E402

litellm.suppress_debug_info = True

WMODEL, WBASE = "deepseek/deepseek-v4-flash", "https://api.deepseek.com"
WKEY = os.environ.get("DEEPSEEK_API_KEY", "")
JUDGES = [
    ("MiniMax", "openai/MiniMax-M2.7-highspeed", "https://api.minimaxi.com/v1", os.environ.get("MINIMAX_API_KEY", "")),
    ("DeepSeek", "deepseek/deepseek-v4-flash", "https://api.deepseek.com", os.environ.get("DEEPSEEK_API_KEY", "")),
]
TARGET, SAMPLES = 850, 2

# 抽象物料(框架 §default 兜底真实长相：机制语言，无血肉)
ABSTRACT_BIBLE = """\
主角：男性主角，普通职业者，刚获得一种"商业类型状态引擎"驱动的特殊能力。
能力(抽象机制)：可对"状态变量"进行借取与偿还，遵循"代价记账"规则——状态变化按
  "升级时钟"翻番累积，透支会触发"公开代价"。能力以"状态账"形式记录。
处境：主角面对"核心阻力"，需在"承诺-选择-状态"循环中做出取舍，权衡"阶段回报"与
  "公开代价"。能力尚未对外揭示。"""

# 具体物料(同第一组，作上界参照锚)
CONCRETE_BIBLE = """\
姓名：陆沉，32岁，县城电工。性格沉默，不爱欠人情，认死理。
能力「气运守恒」：能"借"别人的运气，掌心有黑印记录因果；借一还二，每七日翻一番；
  透支未还时身体从内向外发凉、皮肤泛黑(像地下电缆漏电)；手腕内侧浮现数字=未偿运债。
处境：没告诉任何人；借运能救急但债滚得极快，他正学着克制。"""

SCENES = [
    {"id": "loan", "beat": "母亲住院要补三万押金，账上只剩四千。走廊里有个刚中彩票正在炫耀的男人。主角能'借'一点这人的运气凑钱，但代价会翻番。他必须当场决定借不借、借多少。把这场决定写成正文。"},
    {"id": "choice", "beat": "暴雨夜邻居家失火，小女孩困在二楼。主角可以反向'借'自己的运气给女孩让她平安，代价是把债压在自己身上、数字直接跳到危险线、身体可能当场垮掉。消防车还有八分钟到。把这场抉择写成正文。"},
]

WRITER_SYSTEM = """\
你是顶尖中文网文写手，写都市异能题材。要求：贴紧主角连续感官(看到/听到/碰到/疼到)，
第三人称限知，只写主角此刻能感知的；情绪靠动作细节给出，不写"愤怒/震惊/紧张/害怕"等
情绪标签词；设定靠演不靠讲，作者不要跳出来解说因果或点题。只输出正文，无标题无说明。"""


def writer_user(bible: str, beat: str, inter: str | None) -> str:
    s = f"【主角设定】\n{bible}\n\n【本场】\n{beat}\n\n目标约 {TARGET} 字。"
    if inter:
        s += ("\n\n【主角此刻的真实内心(他本人推演，正文必须长在这上面)】\n" + inter +
              "\n\n把上面的内心落成正文：他注意到的具体东西、他的犹豫与决定、说出口和咽回去的话，都从这份内心自然长出来。")
    return s


EMBODY_SYSTEM = """\
你现在【就是这个主角本人】，不是作者、不是旁观者，用第一人称"我"思考。
不要写小说、不要修辞——像真人在那一刻脑子里真实流过的念头。
注意：如果设定里有"状态变量/代价记账/状态账"这类抽象词，把它们想成我身上真实发生的
具体东西(一件物、一个数字、一处身体反应)，用大白话想，不要用这些术语。"""


def embody_user(bible: str, beat: str) -> str:
    return (f"这是我的情况：\n{bible}\n\n我正在经历：\n{beat}\n\n"
            "以'我'的口吻真实想一遍(不超过250字)：1.此刻身体和眼睛先注意到的具体东西？"
            "2.我最在意/最怕什么？3.怎么权衡，最后决定怎么做？4.我会说出口的话/咽回去的话？"
            "只输出内心，不要写成小说。")


async def _c(model, base, key, sys, usr, temp, mx):
    r = await litellm.acompletion(model=model, api_base=base, api_key=key,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        temperature=temp, max_tokens=mx, timeout=180)
    return (r.choices[0].message.content or "").strip()


JUDGE_SYSTEM = """\
你是严格的中文小说编辑，给一段都市异能正文打分，只看文字本身。5维各0-10，AI腔扣分0-10。
human_feel人感 / character角色可信 / visual画面感(能否成像、有无具体可拍镜头) /
immersion代入感 / prose文笔。额外：abstract_leak(0-10，正文里出现"状态变量/代价记账/机制"等
抽象术语或作者解说式议论的程度，越高越差)。只输出JSON：
{"human_feel":0,"character":0,"visual":0,"immersion":0,"prose":0,"ai_tone":0,"abstract_leak":0,"one_line":""}"""

DIMS = ["human_feel", "character", "visual", "immersion", "prose"]


def _pj(raw):
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def judge_one(model, base, key, text):
    try:
        raw = await _c(model, base, key, JUDGE_SYSTEM, f"待评正文：\n\n{text}\n\n输出JSON。", 0.2, 1000)
    except Exception as e:
        print("    judge err", type(e).__name__, e)
        return None
    d = _pj(raw)
    if not d:
        return None
    try:
        pos = sum(float(d[k]) for k in DIMS)
        return {**{k: float(d[k]) for k in DIMS}, "ai_tone": float(d.get("ai_tone", 0)),
                "abstract_leak": float(d.get("abstract_leak", 0)), "final": pos - float(d.get("ai_tone", 0))}
    except Exception:
        return None


async def gen(bible, beat, embodied):
    inter = ""
    if embodied:
        inter = await _c(WMODEL, WBASE, WKEY, EMBODY_SYSTEM, embody_user(bible, beat), 0.9, 900)
    prose = await _c(WMODEL, WBASE, WKEY, WRITER_SYSTEM, writer_user(bible, beat, inter or None), 0.9, 2200)
    return prose, inter


ARMS = [
    ("A_abstract_direct", ABSTRACT_BIBLE, False),
    ("B_abstract_embodied", ABSTRACT_BIBLE, True),
    ("C_concrete_direct", CONCRETE_BIBLE, False),
]


async def main():
    if not WKEY:
        print("no key"); return
    rng = random.Random(20260608)
    drafts = []
    for sc in SCENES:
        for s in range(SAMPLES):
            for arm, bible, emb in ARMS:
                print(f"[gen] {sc['id']} s{s} {arm}")
                try:
                    p, inter = await gen(bible, sc["beat"], emb)
                    if not p:
                        print("   EMPTY, retry once"); p, inter = await gen(bible, sc["beat"], emb)
                    drafts.append({"id": f"{sc['id']}_{arm}_{s}", "scene": sc["id"],
                                   "arm": arm, "text": p, "interiority": inter})
                except Exception as e:
                    print("   fail", type(e).__name__, e)
    json.dump(drafts, open("scripts/_emb_abstract_drafts.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n生成 {len(drafts)} 稿")

    order = list(range(len(drafts))); rng.shuffle(order)
    scored = []
    for i in order:
        d = drafts[i]
        if not d["text"]:
            continue
        row = {"id": d["id"], "arm": d["arm"]}
        for label, m, b, k in JUDGES:
            r = await judge_one(m, b, k, d["text"])
            if r:
                row[label] = r
                print(f"  [{label}] {d['id']}: final={r['final']:.1f} 画面={r['visual']:.1f} 抽象泄漏={r['abstract_leak']:.1f}")
        scored.append(row)
        json.dump(scored, open("scripts/_emb_abstract_scored.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    _report(scored)


def _report(scored):
    print("\n======= 入戏 × 抽象物料 A/B(决定性) =======")
    tag = {"final": "FinalScore", "visual": "画面感", "human_feel": "人感", "character": "角色可信",
           "immersion": "代入感", "prose": "文笔", "ai_tone": "AI腔扣分", "abstract_leak": "抽象泄漏"}
    for label, *_ in JUDGES:
        by = {}
        for s in scored:
            if label in s:
                by.setdefault(s["arm"], []).append(s[label])
        print(f"\n--- 裁判 {label} ---")
        arms = [a for a, *_ in ARMS]
        print(f"{'维度':10}" + "".join(f"{a:>22}" for a in arms))
        for key in ["final", "visual", "human_feel", "character", "immersion", "prose", "ai_tone", "abstract_leak"]:
            cells = ""
            for a in arms:
                v = [r[key] for r in by.get(a, [])]
                cells += f"{statistics.mean(v):>22.2f}" if v else f"{'-':>22}"
            print(f"{tag[key]:10}{cells}")
        for a in arms:
            print(f"   N[{a}]={len(by.get(a, []))}", end="")
        print()
    print("\n判读：若 B(抽象+入戏) 的 FinalScore/画面感 明显高于 A(抽象+直接)、且抽象泄漏更低，"
          "\n      → 入戏推演能补足抽象物料(廉价杠杆)；若 B≈A 而都远低于 C → 物料具体化才是主杠杆，入戏救不了。")


if __name__ == "__main__":
    asyncio.run(main())
