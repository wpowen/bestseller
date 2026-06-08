"""A/B: 角色入戏推演(character embodiment) 对正文"人感/画面感"的影响。

用户假设：先让模型【真正带入主角】(身份/能力/处境/此刻要做的决定)，以第一人称
推演"他此刻会怎么想、怎么做、怎么说"，再据此落笔，正文会更有"人感"、更少机械感。

设计(对照纪律见 [[scene-grounding-cinematic-gap]]：绝对盲评 + 模型无关，禁成对swap)：
  - 两臂喂【完全相同】的场景 brief + 同一写手模型(DeepSeek-v4-flash) + 同一目标字数。
  - 唯一变量：B 臂在落笔前多一个【入戏推演 pass】(模型扮演主角输出第一人称内心)，
    再把推演结果作为"主角此刻真实内心"喂给写手；A 臂直接写。
  - 多场景(3) × 多样本(2/臂) 抗方差。
  - 盲评：裁判只看正文，不知臂别；两个独立家族裁判(MiniMax 主 + DeepSeek 次)交叉。

Run:  .venv/bin/python scripts/verify_character_embodiment_ab.py
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

WRITER_MODEL = "deepseek/deepseek-v4-flash"
WRITER_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
WRITER_BASE = "https://api.deepseek.com"

JUDGES = [
    # (label, model, api_base, api_key)
    ("MiniMax", "openai/MiniMax-M2.7-highspeed", "https://api.minimaxi.com/v1", os.environ.get("MINIMAX_API_KEY", "")),
    ("DeepSeek", "deepseek/deepseek-v4-flash", "https://api.deepseek.com", os.environ.get("DEEPSEEK_API_KEY", "")),
]

GENRE = "都市异能"
TARGET_CHARS = 850
SAMPLES = 2

# ── 主角设定(本书 bible，两臂共享) ─────────────────────────────────────────
PROTAGONIST = """\
姓名：陆沉，32岁，县城电工，工龄12年。
性格：沉默寡言，不爱欠人情，认死理，习惯把风险扛在自己身上。
能力(刚觉醒不久)：「气运守恒」——他能"借"别人的运气为己用，掌心有一道黑印记录因果；
  借一还二，每七日翻一番；透支未还时，身体会从内向外发凉、皮肤泛黑(像地下电缆漏电，
  旁人看不出，接触到的人会发麻)。手腕内侧会浮现一个数字，代表当前未偿的"运债"。
处境：他没把能力告诉任何人。借运能立刻解燃眉之急，但债滚得极快，他正学着克制。"""

# ── 3 个中段场景 brief(两臂逐字相同；难度适中，不预先具体化以免混入物料具体度变量) ──
SCENES = [
    {
        "id": "s1_loan",
        "beat": """\
场景：母亲住院，缴费窗口催着补三万押金，账上只剩四千。陆沉站在医院走廊，
人群里有个刚中了彩票正在打电话炫耀的中年男人。陆沉知道：他只要"借"一点这人的运气，
今晚的刮刮乐或许就能凑齐钱——但运债会翻番。他必须当场决定借不借、借多少。
要求：把这场决定写成正文，落在陆沉的感知与选择上。""",
    },
    {
        "id": "s2_confront",
        "beat": """\
场景：催债的混混堵在出租屋楼道，为首的叫刀疤，三个人。陆沉欠的不是钱，是上次"借运"
连累了刀疤的兄弟出车祸——但这事没人能证明，刀疤只是觉得"晦气都跟陆沉有关"。
陆沉手腕的运债数字这几天一直在涨，身体正发凉。他得在不暴露能力的前提下脱身。
要求：把这场对峙写成正文，落在陆沉的感知与选择上。""",
    },
    {
        "id": "s3_choice",
        "beat": """\
场景：暴雨夜，陆沉发现邻居家失火，一个小女孩困在二楼。他可以"借"自己的运气给女孩
(逆向操作，第一次尝试)，让她平安——代价是把运债压在自己身上，数字会直接跳到危险线，
身体可能当场垮掉。消防车还有八分钟到。他站在火光前，做这个不划算的决定。
要求：把这场抉择写成正文，落在陆沉的感知与选择上。""",
    },
]

# ── 写手基础指令(两臂共享，确保唯一变量是"入戏推演") ───────────────────────
WRITER_SYSTEM = """\
你是顶尖中文网文写手。写都市异能题材。要求：
- 贴紧主角的连续感官(看到/听到/碰到/疼到)，第三人称限知，只写主角此刻能感知的。
- 情绪靠动作和细节给出，不写"愤怒/震惊/紧张/害怕"这类情绪标签词。
- 设定靠演不靠讲，不要作者跳出来解说因果或点题。
- 只输出正文，不要标题、不要任何说明或前后缀。"""


def writer_user(beat: str, interiority: str | None) -> str:
    base = f"【主角设定】\n{PROTAGONIST}\n\n【本场】\n{beat}\n\n目标约 {TARGET_CHARS} 字。"
    if interiority:
        base += (
            "\n\n【主角此刻的真实内心(已由他本人推演，正文必须长在这上面)】\n"
            + interiority
            + "\n\n把上面的内心活动落成正文：他注意到的具体东西、他的犹豫与决定、"
            "他说出口和咽回去的话，都要从这份内心里自然长出来。"
        )
    return base


# ── B 臂：入戏推演 pass(模型扮演主角本人，输出第一人称内心，不是正文) ────────
EMBODY_SYSTEM = """\
你现在【就是陆沉本人】，不是作者、不是旁观者。用第一人称"我"思考。
不要写小说、不要修辞、不要美化——像真人在那一刻脑子里真实流过的念头。"""


def embody_user(beat: str) -> str:
    return f"""\
这是我(陆沉)的情况：
{PROTAGONIST}

我现在正经历这件事：
{beat}

以"我"的口吻，真实地想一遍(不超过250字，分点也行)：
1. 此刻我身体和眼睛先注意到的具体东西是什么？(越具体越好，不要抽象)
2. 我心里最在意/最怕的是什么？
3. 我会怎么权衡，最后决定怎么做？
4. 我会说出口的话 / 我咽回去没说的话各是什么？
只输出我的内心，不要写成小说。"""


async def _complete(model: str, base: str, key: str, system: str, user: str,
                    temperature: float, max_tokens: int) -> str:
    r = await litellm.acompletion(
        model=model, api_base=base, api_key=key,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature, max_tokens=max_tokens, timeout=180,
    )
    return (r.choices[0].message.content or "").strip()


async def gen_baseline(beat: str) -> str:
    return await _complete(WRITER_MODEL, WRITER_BASE, WRITER_KEY,
                           WRITER_SYSTEM, writer_user(beat, None), 0.9, 2200)


async def gen_embodied(beat: str) -> tuple[str, str]:
    interiority = await _complete(WRITER_MODEL, WRITER_BASE, WRITER_KEY,
                                  EMBODY_SYSTEM, embody_user(beat), 0.9, 900)
    prose = await _complete(WRITER_MODEL, WRITER_BASE, WRITER_KEY,
                            WRITER_SYSTEM, writer_user(beat, interiority), 0.9, 2200)
    return prose, interiority


# ── 盲评裁判 ────────────────────────────────────────────────────────────────
JUDGE_SYSTEM = """\
你是严格的中文小说编辑，给一段【都市异能】正文打分。只看这段文字本身，不知道它怎么来的。
按 5 个维度，每个 0-10 分(可小数)，再给 AI腔扣分 0-10(越像AI/八股越高)。
- 人感(human_feel)：读起来像真人写的、有血肉，还是机械拼装？
- 角色可信(character)：人物的反应、犹豫、选择是否可信、像一个具体的人？
- 画面感(visual)：能不能在脑中成像？有没有具体可拍的镜头(而非抽象概括)？
- 代入感(immersion)：读者是否被拉进主角的处境、想往下读？
- 文笔(prose)：语言质感、节奏。
只输出严格 JSON，不要任何多余文字：
{"human_feel":0,"character":0,"visual":0,"immersion":0,"prose":0,"ai_tone":0,"one_line":"一句点评"}"""


def judge_user(text: str) -> str:
    return f"待评正文：\n\n{text}\n\n按要求输出 JSON。"


def _parse_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


DIMS = ["human_feel", "character", "visual", "immersion", "prose"]


async def judge_one(label: str, model: str, base: str, key: str, text: str) -> dict | None:
    try:
        raw = await _complete(model, base, key, JUDGE_SYSTEM, judge_user(text), 0.2, 1200)
    except Exception as exc:
        print(f"    judge {label} error: {type(exc).__name__}: {exc}")
        return None
    d = _parse_json(raw)
    if not d:
        return None
    try:
        pos = sum(float(d[k]) for k in DIMS)
        ai = float(d.get("ai_tone", 0))
        return {**{k: float(d[k]) for k in DIMS}, "ai_tone": ai,
                "final": pos - ai, "one_line": d.get("one_line", "")}
    except Exception:
        return None


async def main() -> None:
    if not WRITER_KEY:
        print("DEEPSEEK_API_KEY 未设置，退出。")
        return
    rng = random.Random(20260608)

    # 1) 生成两臂草稿
    drafts: list[dict] = []
    for scene in SCENES:
        for s in range(SAMPLES):
            print(f"[gen] {scene['id']} sample{s} baseline ...")
            try:
                a = await gen_baseline(scene["beat"])
                drafts.append({"id": f"{scene['id']}_b{s}", "scene": scene["id"],
                               "arm": "baseline", "text": a, "interiority": ""})
            except Exception as exc:
                print(f"  baseline gen failed: {type(exc).__name__}: {exc}")
            print(f"[gen] {scene['id']} sample{s} embodied ...")
            try:
                p, inter = await gen_embodied(scene["beat"])
                drafts.append({"id": f"{scene['id']}_e{s}", "scene": scene["id"],
                               "arm": "embodied", "text": p, "interiority": inter})
            except Exception as exc:
                print(f"  embodied gen failed: {type(exc).__name__}: {exc}")

    with open("scripts/_embodiment_drafts.json", "w", encoding="utf-8") as f:
        json.dump(drafts, f, ensure_ascii=False, indent=2)
    print(f"\n生成 {len(drafts)} 稿，已存 scripts/_embodiment_drafts.json")

    # 2) 盲评：打乱顺序，裁判只看 text
    order = list(range(len(drafts)))
    rng.shuffle(order)
    scored: list[dict] = []
    for idx in order:
        d = drafts[idx]
        row = {"id": d["id"], "scene": d["scene"], "arm": d["arm"]}
        for label, model, base, key in JUDGES:
            r = await judge_one(label, model, base, key, d["text"])
            if r:
                row[label] = r
                print(f"  judge[{label}] {d['id']}: final={r['final']:.1f} "
                      f"画面={r['visual']:.1f} 人感={r['human_feel']:.1f} AI腔={r['ai_tone']:.1f}")
        scored.append(row)
        with open("scripts/_embodiment_scored.json", "w", encoding="utf-8") as f:
            json.dump(scored, f, ensure_ascii=False, indent=2)

    _report(scored)


def _report(scored: list[dict]) -> None:
    print("\n=========== 角色入戏推演 A/B 结果(绝对盲评) ===========")
    for label, *_ in JUDGES:
        rows = [s for s in scored if label in s]
        by_arm: dict[str, list[dict]] = {}
        for s in rows:
            by_arm.setdefault(s["arm"], []).append(s[label])
        print(f"\n----- 裁判：{label}  (N base={len(by_arm.get('baseline', []))} "
              f"embodied={len(by_arm.get('embodied', []))}) -----")
        print(f"{'维度':12} {'baseline':>9} {'embodied':>9} {'Δ':>7}")
        for key in ["final", *DIMS, "ai_tone"]:
            b = [r[key] for r in by_arm.get("baseline", [])]
            e = [r[key] for r in by_arm.get("embodied", [])]
            if not b or not e:
                continue
            bm, em = statistics.mean(b), statistics.mean(e)
            tag = {"final": "FinalScore", "ai_tone": "AI腔扣分", "human_feel": "人感",
                   "character": "角色可信", "visual": "画面感", "immersion": "代入感",
                   "prose": "文笔"}.get(key, key)
            print(f"{tag:12} {bm:9.2f} {em:9.2f} {em - bm:+7.2f}")
    # 跨裁判合并(每稿两裁判 final 均值，再按臂)
    print("\n----- 两裁判合并 FinalScore -----")
    merged: dict[str, list[float]] = {"baseline": [], "embodied": []}
    for s in scored:
        finals = [s[label]["final"] for label, *_ in JUDGES if label in s]
        if finals:
            merged[s["arm"]].append(statistics.mean(finals))
    bm = statistics.mean(merged["baseline"]) if merged["baseline"] else float("nan")
    em = statistics.mean(merged["embodied"]) if merged["embodied"] else float("nan")
    print(f"baseline={bm:.2f}  embodied={em:.2f}  Δ={em - bm:+.2f}")
    print("判读：两裁判同向且 Δ 明显为正 → 入戏推演有真实增益；分歧/落噪声内 → 单章无稳健优势。")


if __name__ == "__main__":
    asyncio.run(main())
