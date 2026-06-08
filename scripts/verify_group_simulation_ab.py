"""A/B 第三组(决定性)：群体仿真演绎 vs 单角色入戏 vs 直接写。

用户想法：把大纲+本章放进群体仿真，让每个角色作为独立 agent 在"上帝/导演"设定的
规则与边界内【自主演绎】这一章，涌现出有摩擦的互动，再由写手转成正文 → 故事有"人感和灵魂"。

三臂对照(关键：群体仿真更贵，必须证明它比便宜的单角色入戏更强才值得做)：
  A baseline       大纲 → 写手直接写                         (= 框架当前处境)
  B embodied       主角单人入戏推演 → 写手                    (= 上一组已证的便宜杠杆)
  C group_sim      导演设场+每角色独立 agent 逐回合自主演绎 → 涌现转录 → 写手  (= 用户的群体仿真)

裁判：MiniMax + DeepSeek 两独立家族绝对盲评。多角色场景(对峙/谈判)，因为群体仿真的
增益点 = 配角自主性(不是提线木偶) → 故判官加一维 agency(配角自主可信度)。

Run:  .venv/bin/python scripts/verify_group_simulation_ab.py
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
TARGET, SAMPLES, SIM_TURNS = 900, 2, 3


async def _c(model, base, key, sys, usr, temp, mx):
    r = await litellm.acompletion(model=model, api_base=base, api_key=key,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        temperature=temp, max_tokens=mx, timeout=180)
    return (r.choices[0].message.content or "").strip()


async def W(sys, usr, temp=0.9, mx=2200):
    return await _c(WMODEL, WBASE, WKEY, sys, usr, temp, mx)


# ── 主角(能力设定，与上组一致：抽象机制，保留头空间) ─────────────────────────
PROTAG = """\
陆沉，32岁县城电工。沉默，认死理，不爱欠人情，习惯把风险扛自己身上。
能力(刚觉醒)：可对"状态变量"借取与偿还，遵循"代价记账"——按"升级时钟"翻番累积，
透支触发"公开代价"，以"状态账"记录。没告诉任何人。"""

# ── 角色库(配角有各自的私有目标，给群体仿真用) ─────────────────────────────
CHARS = {
    "陆沉": {"bg": PROTAG, "goal": "在不暴露能力的前提下脱身/达成目的，少欠人情。"},
    "刀疤": {"bg": "刀疤，38岁，本地讨债的，手下两个兄弟。讲江湖规矩，要面子，其实怕事闹大惊动条子。最近觉得'晦气都跟陆沉沾边'。",
              "goal": "逼陆沉给个说法或拿点钱走，但不想真出人命惹麻烦，台阶递到了就收。"},
    "马姐": {"bg": "马姐，45岁，医院附近开小卖部，消息灵通，见过陆沉做的怪事一角。精明，重人情也算计。",
              "goal": "想从陆沉这弄清他到底有什么本事，能帮自己病重的老伴，愿意拿点筹码交换。"},
}

SCENES = [
    {"id": "confront", "cast": ["陆沉", "刀疤"],
     "outline": "出租屋楼道，刀疤带两个兄弟堵住陆沉，为'晦气'讨说法。约束：不许真动手见血；本场结束时陆沉脱身但留下后患(刀疤撂下狠话)。陆沉手腕的'代价'这几天在涨，身体发凉。",
     "open": "陆沉提着塑料袋上楼，楼道声控灯灭着。三个人影堵在他家门口，为首的脸上有道疤。"},
    {"id": "deal", "cast": ["陆沉", "马姐"],
     "outline": "小卖部，马姐主动找陆沉,旁敲侧击想交换。约束：本场结束时两人达成一个脆弱的、各有保留的口头协议，但彼此都没全交底。",
     "open": "陆沉来买烟。马姐没急着收钱，把烟往柜台上一压，眼睛盯着他的手。"},
]

# ── 写手(三臂共享) ─────────────────────────────────────────────────────────
WSYS = """\
你是顶尖中文网文写手，写都市异能。贴紧主角连续感官，第三人称限知，只写他此刻能感知的；
情绪靠动作细节，不写'愤怒/震惊/紧张/害怕'等标签词；设定靠演不靠讲，作者不解说不点题；
配角要有自己的立场和盘算，不是工具人。只输出正文，无标题无说明。"""


def w_user(scene, extra_label=None, extra=None):
    s = f"【主角】\n{PROTAG}\n\n【本场大纲】\n{scene['outline']}\n\n【开场】\n{scene['open']}\n\n目标约 {TARGET} 字。"
    if extra:
        s += f"\n\n【{extra_label}】\n{extra}\n\n把上面的内容落成连续正文，让每个动作、每句话都从中自然长出来。"
    return s


# ── 群体仿真引擎 ───────────────────────────────────────────────────────────
DIRECTOR_SYS = """\
你是故事的'上帝/导演'。你不写小说。你的职责：根据大纲设定本场的硬约束(必须发生什么、
不能发生什么、本场如何收束)，并裁定每一回合后局势是否推进、是否该收场。客观、简短。"""

ACTOR_SYS_T = """\
你现在【就是{name}】本人，不是作者、不是旁观者，用第一人称'我'。
你的人物：{bg}
你这一场私下想要的：{goal}
规则：① 只根据你能看到/听到的局面行动，不知道别人心里想什么；② 像真人即兴反应，可以有
小情绪、试探、让步或得寸进尺；③ 把你身上任何'状态变量/代价记账'之类抽象设定，想成你身上
真实发生的具体东西(一处身体反应、一个数字、一件事的后果)，用大白话，不要用术语。
每回合只输出你这一下的【动作】+【说的话】+一句【没说出口的真实想法】，简短，不要写成小说。"""


async def run_group_sim(scene):
    """导演设场 + 多角色逐回合自主演绎 → 返回涌现转录。"""
    cast = scene["cast"]
    setup = await W(DIRECTOR_SYS,
        f"大纲：{scene['outline']}\n开场：{scene['open']}\n出场：{', '.join(cast)}\n"
        "请用5行内列出本场硬约束与收场条件(给角色当边界)。", 0.4, 600)
    transcript = [f"[导演设定]\n{setup}", f"[开场]\n{scene['open']}"]
    for turn in range(SIM_TURNS):
        for name in cast:
            c = CHARS[name]
            visible = "\n".join(transcript[-8:])  # 只给近况，模拟有限感知
            beat = await W(ACTOR_SYS_T.format(name=name, bg=c["bg"], goal=c["goal"]),
                f"目前局面(你能感知到的)：\n{visible}\n\n现在轮到你({name})。这一下你怎么做、说什么？", 0.95, 400)
            transcript.append(f"[{name}·第{turn+1}回合]\n{beat}")
    # 导演收束：提炼涌现出的关键拍子
    summary = await W(DIRECTOR_SYS,
        "以下是本场自主演绎记录：\n" + "\n".join(transcript) +
        "\n\n请提炼成给写手用的'本场实际发生的关键拍子'(动作/对白/转折，按时间顺序，含涌现出的细节)。", 0.4, 1200)
    return "\n".join(transcript), summary


EMBODY_SYS = """\
你现在【就是陆沉】本人，第一人称'我'，不写小说不修辞。把'状态变量/代价记账'想成我身上
真实发生的具体东西，用大白话。真实想一遍(≤250字)：此刻先注意到的具体东西/最怕什么/
怎么权衡决定/说出口和咽回去的话。只输出内心。"""


async def gen_arm(arm, scene):
    if arm == "A_baseline":
        return await W(WSYS, w_user(scene)), ""
    if arm == "B_embodied":
        inter = await W(EMBODY_SYS, f"我的情况：{PROTAG}\n本场：{scene['outline']}\n开场：{scene['open']}")
        return await W(WSYS, w_user(scene, "主角此刻的真实内心(他本人推演)", inter)), inter
    if arm == "C_group_sim":
        transcript, summary = await run_group_sim(scene)
        return await W(WSYS, w_user(scene, "本场角色自主演绎出的关键拍子(必须据此写)", summary)), summary
    raise ValueError(arm)


# ── 盲评 ──────────────────────────────────────────────────────────────────
JSYS = """\
你是严格中文小说编辑，给一段都市异能正文打分，只看文字本身。各0-10：
human_feel人感 / character主角可信 / agency配角自主性(配角是否像有自己盘算的活人，而非工具人) /
visual画面感(能否成像、有无具体可拍镜头) / immersion代入感 / prose文笔。
另：ai_tone AI腔扣分0-10。只输出JSON：
{"human_feel":0,"character":0,"agency":0,"visual":0,"immersion":0,"prose":0,"ai_tone":0,"one_line":""}"""

DIMS = ["human_feel", "character", "agency", "visual", "immersion", "prose"]


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
        raw = await _c(model, base, key, JSYS, f"待评正文：\n\n{text}\n\n输出JSON。", 0.2, 900)
    except Exception as e:
        print("    judge err", type(e).__name__, e); return None
    d = _pj(raw)
    if not d:
        return None
    try:
        pos = sum(float(d[k]) for k in DIMS)
        return {**{k: float(d[k]) for k in DIMS}, "ai_tone": float(d.get("ai_tone", 0)),
                "final": pos - float(d.get("ai_tone", 0))}
    except Exception:
        return None


ARMS = ["A_baseline", "B_embodied", "C_group_sim"]


async def main():
    if not WKEY:
        print("no key"); return
    rng = random.Random(20260608)
    drafts = []
    for sc in SCENES:
        for s in range(SAMPLES):
            for arm in ARMS:
                print(f"[gen] {sc['id']} s{s} {arm}")
                try:
                    text, aux = await gen_arm(arm, sc)
                    if not text:
                        print("   EMPTY retry"); text, aux = await gen_arm(arm, sc)
                    drafts.append({"id": f"{sc['id']}_{arm}_{s}", "scene": sc["id"], "arm": arm, "text": text, "aux": aux})
                except Exception as e:
                    print("   fail", type(e).__name__, e)
                json.dump(drafts, open("scripts/_groupsim_drafts.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
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
                print(f"  [{label}] {d['id']}: final={r['final']:.1f} 画面={r['visual']:.1f} 配角自主={r['agency']:.1f}")
        scored.append(row)
        json.dump(scored, open("scripts/_groupsim_scored.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    _report(scored)


def _report(scored):
    print("\n======= 群体仿真 vs 单人入戏 vs 直接写(绝对盲评) =======")
    tag = {"final": "FinalScore", "visual": "画面感", "human_feel": "人感", "character": "主角可信",
           "agency": "配角自主", "immersion": "代入感", "prose": "文笔", "ai_tone": "AI腔扣分"}
    for label, *_ in JUDGES:
        by = {}
        for s in scored:
            if label in s:
                by.setdefault(s["arm"], []).append(s[label])
        print(f"\n--- 裁判 {label} ---")
        print(f"{'维度':10}" + "".join(f"{a:>18}" for a in ARMS))
        for key in ["final", "human_feel", "character", "agency", "visual", "immersion", "prose", "ai_tone"]:
            cells = ""
            for a in ARMS:
                v = [r[key] for r in by.get(a, [])]
                cells += f"{statistics.mean(v):>18.2f}" if v else f"{'-':>18}"
            print(f"{tag[key]:10}{cells}")
        print("   " + "  ".join(f"N[{a}]={len(by.get(a, []))}" for a in ARMS))
    print("\n判读：C>B>A 且配角自主明显高 → 群体仿真值得做；C≈B → 贵但不比单人入戏强，优先单人入戏。")


if __name__ == "__main__":
    asyncio.run(main())
