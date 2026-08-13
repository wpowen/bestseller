"""一句话钩子的「点开欲」判官（2026-08-11）。

为什么已有的两台仪器都不能用（真机定罪）：
- ``logline_gate``（12 轴）测的是故事智力（决策/代价/因果），不测点开欲——
  「天煞孤星…杀一回」拿了 expand 3.91；
- ``persona_click_judge`` 测 3 秒冲动，但它给同一句打了 3/3 会点 9.0 分，
  划走理由在**夸**「短平快」「套路对了」——它在奖励套路识别，不在测渴望。

本判官的目标函数来自两份真数据的交集：
- 100 本榜单钩子分类（docs/research/board-blurb-hook-research-20260811.md）：
  头部钩子全是**渴望引擎**——读者一秒说得出想看主角赢什么/翻什么身/兑现什么；
- 用户逐条终审（2026-08-11）命名的四种 AI 结构，全部是硬压分项：
  被动主角、对称机制句、「谁A谁就B」规则句、反讽/荒诞处境。

**锚定评分**：判官 prompt 内置固定锚例（强锚=真实榜单钩子记 8-9，弱锚=已定罪
失败模式的改写记 2-3），把刻度钉在真实样本上——绝对分不可信的病根是模型用
自己的先验当刻度，锚例就是外部刻度。锚例与验证集（config/hook_pull_eval.yaml）
**不重叠**，验证脚本会检查这一点。

用法契约：``evaluate_hook_pull`` 多采样取中位（单轮判官噪声大），fail-open
返回 None（判官不可用≠钩子差）。本判官当前**只做测量，不接任何门**——先按
用户的方法论用榜单数据验证它本身，验证通过前它的分数不得驱动任何决策。
"""

from __future__ import annotations

import asyncio
import json
import re as _re
import logging
import statistics
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ruff: noqa: RUF001, RUF002 — Chinese prompts are intentional.

# ── 固定锚例 ────────────────────────────────────────────────────────────────
# 强锚：真实在榜书的钩子（2026-08-10 番茄榜单实抓）。弱锚：用户定罪的四种
# AI 结构的**改写体**（不是验证集原句——锚例进 prompt，验证集必须与它无交集）。
_STRONG_ANCHORS: tuple[tuple[str, str, int], ...] = (
    (
        "领证三天，我被未婚妻杀死",
        "昔日天骄被挚爱诬陷，狱中熬过七年；踏出监狱那天，整个世界才猛然惊觉。",
        9,
    ),
    (
        "高考出分前一晚，兑换北大录取书",
        "高考出分前夜，季白刷到一条帖子：给你十块钱，你怎么花？他随手选了"
        "『北大录取通知书+北京一套房』——系统绑定完成，消费成功。",
        9,
    ),
    (
        "凡骨",
        "世间灵骨共分四品，余者皆为凡骨，无缘修行。一介凡骨许太平，誓要向这"
        "修行界证明：凡骨亦能登仙。",
        8,
    ),
    # 中锚：俗套但欲望明确的普通在榜书——榜单及格线长这样，不是文学奖长这样
    (
        "盖世神医",
        "任你权势滔天，任你富可敌国，在我面前不要嚣张。我是叶秋，能救你的命，"
        "也能要你的命！",
        7,
    ),
)
_WEAK_ANCHORS: tuple[tuple[str, str, int, str], ...] = (
    (
        "（无题）",
        "一个替人修伞的老匠人，被一场大雨逼着亲手拆掉自己修过的每一把伞。",
        2,
        "被动主角+反讽循环：读者说不出想看他赢什么",
    ),
    (
        "（无题）",
        "小镇上有一口井，打水多了井会哭，打水少了全镇渴。",
        2,
        "对称机制句：设计条款不是故事，两头都不是读者在乎的东西",
    ),
    (
        "（无题）",
        "凡是在桥上回头的人，都会看见自己最想忘掉的一天。",
        3,
        "「谁A就B」规则句模板：AI 味本体，没有主角没有渴望",
    ),
)


@dataclass(frozen=True)
class HookPullVerdict:
    """一次多采样评估的聚合结果。"""

    score: float          # 0-10，多采样中位
    samples: tuple[float, ...]
    craving: str          # 判官答出的「读者想看主角赢什么」——答不出即为空
    flags: tuple[str, ...]  # 命中的 AI 结构标签
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "samples": [round(s, 2) for s in self.samples],
            "craving": self.craving,
            "flags": list(self.flags),
            "reason": self.reason,
            "schema_version": "hook-pull-judge.v1",
        }


def build_hook_pull_messages(
    *, title: str, hook: str, genre: str, channel: str = "男频"
) -> tuple[str, str]:
    strong = "\n".join(
        f"  【{score}分】《{t}》——{h}" for t, h, score in _STRONG_ANCHORS
    )
    weak = "\n".join(
        f"  【{score}分】{h}（{why}）" for _, h, score, why in _WEAK_ANCHORS
    )
    system = (
        "你是网文平台信息流的点击率预测器，不是文学评委。你只回答一个问题："
        f"一个刷手机的{channel}读者扫过这条书名+一句话，会不会停下手指点进去。"
        "记住榜单现实：俗套但欲望明确的钩子在真实榜单上就是赢家，"
        "「新颖但读者说不出想看什么」才是输家。"
        "flag 是罕见的定罪事件，不是挑剔清单——真实在榜书大多数 0 个 flag；"
        "每记一个 flag 都必须能从原文引出证据，引不出就不记。只输出JSON。"
    )
    user = (
        f"题材：{genre}\n书名：《{title}》\n一句话：{hook}\n\n"
        "刻度由锚例钉死（7分=普通在榜书的水平；8-9=榜单头部）：\n"
        f"锚例（真实榜单钩子）：\n{strong}\n"
        f"反例（已定罪的失败模式）：\n{weak}\n\n"
        "评分步骤：\n"
        "① 先从书名+一句话里**逐字摘出**主角目标所在的原文片段，填进 "
        "goal_quote（必须是原文连续片段，一个字都不许改写；摘不出就留空）。"
        "『活下去』『变强』『誓要…』『只想…』这类字样都是目标——即使前面"
        "带着『无奈之下』『被迫』的语气，写出来了就是写出来了，不许注销。"
        "然后答出读者点进去想看主角【赢什么/翻什么身/兑现什么优势/清算什么账/"
        "怎么活下来】。**书名和一句话是读者同时看到的一个整体，书名常常就是"
        "钩子本体**（书名里明写的身份反差/目标/能力都算数）。铁律一：craving "
        "必须由 goal_quote 支撑——goal_quote 为空则 craving 必须留空；"
        "「被逼着做X」里的X是别人的要求，不算目标。铁律二：craving 必须落在"
        "上面五类之内——求死、维持现状、保住生计、被迫承受不算。"
        "craving 为空 → 总分不得超过 4。"
        "例外：具体的谜或具体的怕**落在主角自己身上**（他自己是谜团/他自己"
        "被威胁且文本给出当场证据）也算 craving，此时 goal_quote 摘那段"
        "谜/怕的原文。\n"
        "② 定分：欲望明确+题材对味 → 起步就是 6.5-7（对照《盖世神医》）；"
        "反差狠/信息差钩人/事件具体（有数字有动作有当场后果）每样加 0.5-1；"
        "都不沾才落到 5-6。比《凡骨》强的极少（≥8 需给出理由）。"
        "这一步**只评拉力，不查毛病**——缺陷另有专门的检察工序。\n"
        '只输出JSON：{"goal_quote":"主角目标的原文连续片段，或空串",'
        '"craving":"文本里读得出的想看主角赢什么，或空串",'
        '"score":0到10,"reason":"25字内"}'
    )
    return system, user


# ── 缺陷通道：专职检察官，只定罪不打分 ──────────────────────────────────────
# 为什么拆开（v6/v7 真机定罪）：8 个 flag+引文摘取+五类欲望塞进一次调用后，
# 判官变成看什么都像病——11 本在榜书被扣 passive，《封总》(热度260万)被打 4 分。
# 拆开后欲望通道回到 v5 的干净曲线，缺陷通道拿到完整注意力，且每项定罪必须
# 附原文证据引文，解析层逐字核对，编造证据=废票。

_DEFECT_AXES: tuple[str, ...] = (
    "passive", "irony_only", "stilted",
    "logic_break", "hollow_twist", "genre_mismatch",
)


def build_hook_defect_messages(
    *, title: str, hook: str, genre: str
) -> tuple[str, str]:
    system = (
        "你是网文选题的缺陷检察官。你不评好坏、不打分，只回答：下列六种"
        "**定罪级缺陷**是否确凿在场。真实在榜书大多数一条都不沾——"
        "拿不准就是不在场；每定一条罪都必须给出原文证据引文（evidence 必须"
        "是书名或一句话里的**逐字连续片段**），引不出证据就不许定罪。只输出JSON。"
    )
    user = (
        f"题材：{genre}\n书名：《{title}》\n一句话：{hook}\n\n"
        "六种缺陷的判据（逐条核对）：\n"
        "1. passive：主角从头到尾没有任何文本明写的自己的目标。被动开局"
        "（穿越/被抓/被逼入绝境）是网文标准形态，只要文本里写了主角的目标"
        "（活下去/变强/逃出去/复仇/查清自己是谁/只想…）就不算。evidence "
        "摘『最能证明全句没有主角目标』的片段；\n"
        "2. irony_only：整条钩子止于反讽/荒诞处境本身，没有任何读者在乎的"
        "赌注。喜剧/搞笑开局不算；有目标或有赌注就不算；\n"
        "3. stilted：一句话正文（不含书名）本身是病句——连读拗口、成分堆叠"
        "（如连着两个方位词）、生造搭配、翻译腔。俗、套路、口语不算；\n"
        "4. logic_break：逐个动作检查**谁对谁做了什么**——施受必须成立"
        "（『职业玩家被医院劝退』不成立：劝退只能发生在他任职/就读的机构，"
        "医院对病人只能劝告）。施受不匹配、前后互斥、因果接不上都算；\n"
        "5. hollow_twist：结尾落点是自明之事——做了A自然就B（『离婚后不再"
        "替前夫家看藏品』：离都离了当然不看），假装反转的同义反复。真落点"
        "必须写出别人要付的代价或当场后果；\n"
        "6. genre_mismatch：**上错书架**——盖住题材名，光读书名+一句话会把"
        "这本书归到明显不同的架上（悬疑灵异架上放无任何超自然的纯探案、"
        "东方玄幻架上放无超凡元素的现代行当）。题材的世界观组分在场"
        "（修仙/宗门/斩神/末世/诡异空间/游戏异界…书名里出现也算）就不算；"
        "都市系题材只要都市质感在场就不算。此项 evidence 允许留空。\n"
        '只输出JSON：{"hits":[{"axis":"六个词之一","evidence":"原文逐字片段"}]}'
        "；一条不沾就输出 {\"hits\":[]}"
    )
    return system, user


def parse_hook_defect_verdict(
    raw: str, *, source_text: str
) -> list[str] | None:
    """解析缺陷检察官的一票。证据引文逐字核对，对不上=该项弃权（不定罪）。"""

    text = (raw or "").strip()
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        payload = json.loads(text[start:end], strict=False)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
        return None
    normalized_source = _re.sub(r"\s+", "", source_text)
    hits: list[str] = []
    for entry in payload["hits"]:
        if not isinstance(entry, dict):
            continue
        axis = str(entry.get("axis") or "").strip()
        matched = next((a for a in _DEFECT_AXES if axis.startswith(a)), None)
        if matched is None or matched in hits:
            continue
        evidence = _re.sub(r"\s+", "", str(entry.get("evidence") or ""))
        if matched != "genre_mismatch" and (
            not evidence or evidence not in normalized_source
        ):
            continue  # 无证据或编造证据=废票
        hits.append(matched)
    return hits


def parse_hook_pull_verdict(
    raw: str, *, source_text: str = ""
) -> dict[str, Any] | None:
    """解析单次判官采样。

    ``source_text``（书名+一句话）给定时做**引文核对**：goal_quote 声称是
    原文连续片段，编造的引文=作弊的测量，整个样本作废（返回 None 触发重采），
    而不是替它圆场。这是把「craving 禁脑补」从口头铁律变成确定性校验。
    """

    text = (raw or "").strip()
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        payload = json.loads(text[start:end], strict=False)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "score" not in payload:
        return None
    try:
        score = max(0.0, min(10.0, float(payload.get("score"))))
    except (TypeError, ValueError):
        return None
    known_flags = (
        "passive", "symmetric_rule", "whoever_rule", "irony_only", "stilted",
        "logic_break", "hollow_twist", "genre_mismatch",
    )
    flags: list[str] = []
    for raw_flag in payload.get("flags") or []:
        text_flag = str(raw_flag).strip()
        # 模型爱在标签后面接解释文；归一化到规范标签，认不出的丢弃
        for known in known_flags:
            if text_flag.startswith(known) and known not in flags:
                flags.append(known)
                break
    goal_quote = str(payload.get("goal_quote") or "").strip()
    craving = str(payload.get("craving") or "").strip()
    if source_text and goal_quote:
        normalized_source = _re.sub(r"\s+", "", source_text)
        normalized_quote = _re.sub(r"\s+", "", goal_quote)
        if normalized_quote not in normalized_source:
            return None  # 编造引文=作弊的测量，废样本重采
    if not goal_quote:
        craving = ""  # 无引文支撑的 craving 一律视为脑补
    return {
        "score": score,
        "goal_quote": goal_quote,
        "craving": craving,
        "flags": flags,
        "reason": str(payload.get("reason") or "").strip(),
    }


async def evaluate_hook_pull(
    session: Any,
    settings: Any,
    *,
    title: str,
    hook: str,
    genre: str,
    channel: str = "男频",
    samples: int = 3,
) -> HookPullVerdict | None:
    """多采样取中位。fail-open：判官不可用返回 None，绝不伪造分数。"""

    from bestseller.services.llm import LLMCompletionRequest, complete_text

    system, user = build_hook_pull_messages(
        title=title, hook=hook, genre=genre, channel=channel
    )
    results: list[dict[str, Any]] = []
    for _ in range(max(1, samples)):
        try:
            completion = await complete_text(
                session, settings,
                LLMCompletionRequest(
                    logical_role="critic", model_tier="strong",
                    system_prompt=system, user_prompt=user,
                    fallback_response="{}",
                    prompt_template="hook_pull_judge", prompt_version="v1",
                    max_tokens_override=300,
                ),
            )
            parsed = parse_hook_pull_verdict(
                completion.content or "", source_text=f"{title} {hook}"
            )
            if parsed is not None:
                results.append(parsed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("hook pull judge sample failed", exc_info=True)
    if not results:
        return None
    scores = tuple(r["score"] for r in results)
    best = min(results, key=lambda r: abs(r["score"] - statistics.median(scores)))
    desire_score = float(statistics.median(scores))

    # 缺陷通道：独立检察官采样，多数票定罪（≥半数），证据引文已在解析层核对。
    defect_system, defect_user = build_hook_defect_messages(
        title=title, hook=hook, genre=genre
    )
    defect_votes: list[list[str]] = []
    for _ in range(max(1, samples)):
        try:
            completion = await complete_text(
                session, settings,
                LLMCompletionRequest(
                    logical_role="critic", model_tier="strong",
                    system_prompt=defect_system, user_prompt=defect_user,
                    fallback_response='{"hits":[]}',
                    prompt_template="hook_defect_judge", prompt_version="v1",
                    max_tokens_override=300,
                ),
            )
            vote = parse_hook_defect_verdict(
                completion.content or "", source_text=f"{title} {hook}"
            )
            if vote is not None:
                defect_votes.append(vote)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("hook defect judge sample failed", exc_info=True)
    convicted: list[str] = []
    if defect_votes:
        # 两张有证据的票即定罪（不是过半）：证据引文已被逐字核对，冤案率
        # 实测为 0（v8：23 本在榜书 × 3 票，0 项被定罪），漏放才是主要风险。
        quorum = min(2, len(defect_votes))
        for axis in _DEFECT_AXES:
            if sum(axis in vote for vote in defect_votes) >= quorum:
                convicted.append(axis)
    # 句法级定罪结构永远确定性检出，与 LLM 票无关
    convicted.extend(
        h for h in detect_condemned_hook_structures(f"{title} {hook}")
        if h not in convicted
    )

    cap = 10.0
    if convicted:
        cap = 4.0 if convicted == ["stilted"] else 3.0
    return HookPullVerdict(
        score=min(desire_score, cap),
        samples=scores,
        craving=best["craving"],
        flags=tuple(convicted),
        reason=best["reason"],
    )


# 用户 2026-08-11 定罪的两种规则句是**句法**结构，交给确定性检测器而不是
# LLM flag（真机实测：LLM 判官 3 采样只有 1 次抓到「开口就是要走，闭口就是
# 要留；给谁照谁就欠一盏灯」）。词表教训（裸字「门」事件）仍然适用：这里
# 匹配的是句式骨架，不含任何题材名词。
_WHOEVER_RULE = _re.compile(
    r"谁[^，。；！？\n]{0,14}[，,]?\s*谁(?:就|便|都)"  # 谁A，谁就B
    r"|凡是?[^，。；！？\n]{2,16}的人?[，,][^，。；！？\n]{0,4}都(?:会|要|得)"  # 凡…的（人），都会…
)
# 维持式处境模板（2026-08-11 三批定罪）：「天生阴命的X，命里注定要Y，
# 只好一边A一边B」——身份+宿命+日常维持，没有任何已经发生的事件。真机罪证：
# 悬疑池前 3 名全是此模板换职业的克隆；人类基线 100 本榜单头部片段 0 命中
# （命里/命中注定、只好/只能一边…一边 两组标记都是 0/100）。
_ROUTINE_SETUP = _re.compile(
    r"命[里中]注定"
    r"|只[好能][^，。；！？\n]{0,6}一边[^，。；！？\n]{1,20}一边"
)
# 「每X就Y」机制条款（2026-08-12 四批）：『他每补一行剧情，门外就消失一个
# 人』——和谁A谁就B同族的规则说明腔。人类基线 100 本榜单头部片段 0 命中。
# 每+时间间隔（每过/每隔/每逢/每到/每天/每晚…）是人类惯用的复现叙述，放过
# （全量语料唯一误报=《诡舍》『每过一段时间就要被拉入』）；只抓 每+动作→就+后果。
_PER_ACTION_RULE = _re.compile(
    r"每(?!过|隔|逢|到|当|次|回|年|月|日|天|晚|夜)"
    r"(?:[^，。；！？\n]{1,14}[，,][^，。；！？\n]{0,8}就[^，。；！？\n]{2,20}"
    # 真机漏网（2026-08-12）：「每掉一块围观玩家就升一级」靠『升』绕过了
    # 后果动词表——补量变/状态动词，仍保持白名单式（防误伤叙事句）。
    r"|[^，。；！？\n]{1,10}就(?:会|有|要|得|多|少|升|涨|长|加|掉|变|消失|死))"
)
# 极性字母表：对立的一对才构成「机制条款」的两头。这是语法级的封闭小集合，
# 不是题材词表（今天/明天这类非对立差一字不许命中——真机校准里《时停起手》
# 的「今天不用，明天还能累积」就是必须放过的边界样本）。只收**数量/状态**极性；
# 方位/时序极性（上下/前后/来去/早晚）是人类对偶与范围列举的常客——100 本
# 榜单全量简介逐行扫出的 3 个误报全是它们（上到领导下到军嫂、人前人后），已剔除。
_POLARITY_PAIRS = frozenset(
    frozenset(p)
    for p in (
        ("多", "少"), ("开", "闭"), ("高", "低"), ("快", "慢"), ("生", "死"),
        ("有", "无"), ("满", "空"), ("张", "合"),
    )
)


def _polarity_flip(head_l: str, head_r: str) -> bool:
    if len(head_l) != len(head_r) or head_l == head_r:
        return False
    diffs = [(a, b) for a, b in zip(head_l, head_r) if a != b]
    return len(diffs) == 1 and frozenset(diffs[0]) in _POLARITY_PAIRS


def _symmetric_rule_hit(text: str) -> bool:
    """对称机制条款：相邻两个短句在同一位置发生一次极性翻转——
    句头翻转（捞多了…，捞少了…）或推论记号前翻转（开口就是走，闭口就是留）。"""

    clauses = [c.strip() for c in _re.split(r"[，,。；：;！？\n]|——", text) if c.strip()]
    for left, right in zip(clauses, clauses[1:]):
        # 句头对齐：捞多了鱼死 / 捞少了客人走
        for k in (2, 3, 4):
            if _polarity_flip(left[:k], right[:k]):
                return True
        # 推论记号前的尾对齐：…亡者开口就是要走 / 闭口就是要留
        for marker in ("就是", "就", "则", "便"):
            if marker in left and marker in right:
                head_l = left.split(marker, 1)[0]
                head_r = right.split(marker, 1)[0]
                width = min(len(head_l), len(head_r), 4)
                if width >= 2 and _polarity_flip(head_l[-width:], head_r[-width:]):
                    return True
                break
    return False


def detect_condemned_hook_structures(text: str) -> list[str]:
    """确定性检出用户定罪的规则句结构。命中即该钩子不合格，与判官分数无关。"""

    hits: list[str] = []
    if _WHOEVER_RULE.search(text or ""):
        hits.append("whoever_rule")
    if _symmetric_rule_hit(text or ""):
        hits.append("symmetric_rule")
    if _ROUTINE_SETUP.search(text or ""):
        hits.append("routine_setup")
    if _PER_ACTION_RULE.search(text or ""):
        hits.append("per_action_rule")
    return hits


def anchor_texts() -> tuple[str, ...]:
    """锚例文本——验证脚本用它检查锚例与验证集不重叠（防泄漏）。"""

    return tuple(h for _, h, *_ in (*_STRONG_ANCHORS, *_WEAK_ANCHORS))


__all__ = [
    "HookPullVerdict",
    "anchor_texts",
    "build_hook_pull_messages",
    "detect_condemned_hook_structures",
    "evaluate_hook_pull",
    "parse_hook_pull_verdict",
]
