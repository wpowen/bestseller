"""简介/正典自洽校验 — 引文核对式 LLM 判官（校验任务，不是评分任务）。

2026-08-07 真机 custom-xianxia-1786090118：对外简介同时压着四条倒计时
（一个时辰/今夜/月底/一个月），土豆先被徒弟揣走又被主角下锅，premise 说
三十五岁、spine 说三十年厨房功夫（5 岁开始颠勺）。而全链没有任何一处测逻辑：
``comprehensibility`` 只数生造黑话（打了满分 5.0），病理检测全是词表层，
画像判官只答「点不点」。**矛盾对现有每一把尺子都不可见。**

为什么不直接问 LLM「这段通顺吗」：同日校准已证明问感受得到吹捧
（aversion 案例：judge 说「不恶心」而人反胃）。所以这里只给校验任务——
「找出互相矛盾的两处，把两段原文逐字引出来」——然后**程序核对引文确实是
输入文本的子串**，引不出原文的发现按幻觉丢弃。模型没有打分空间，
只有可证伪的产出。

fail-open：LLM 不可用/超时/全部发现核对失败 → 空报告 + ``llm_used=False``，
绝不误毙。调用方拿 verified findings 当 fatal 级信号用。
"""

from __future__ import annotations

from dataclasses import dataclass

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese prompts are intentional.
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# 原四类是「两段引文摆在一起矛盾自明」的事实矛盾，真机零冤案，挣到了
# 候选出局权。2026-08-23 新增的三类逻辑病（机制矛盾/无锚指代/论据不撑
# 论点）判断成分更重，按「新检测器只挣重生和留痕」规矩先做教学轴：进
# 打磨反馈与审计痕迹，不参与候选出局；真机验证零冤案后再提权。
_FATAL_KINDS = frozenset({"timeline", "fact", "reference", "number"})
_ADVISORY_KINDS = frozenset(
    {
        "mechanism",
        "dangling",
        "claim_unsupported",
        "effect_unexplained",
        "invented_entity",
    }
)

# 单引文病：天然没有第二段引文（dangling=指代无着落，effect_unexplained=
# 效果无机制，invented_entity=正典里「不存在的那句」引不出来）。
_SINGLE_QUOTE_KINDS = frozenset(
    {"dangling", "effect_unexplained", "invented_entity"}
)


@dataclass(frozen=True)
class CoherenceFinding:
    """一条**通过引文核对**的矛盾。quote_a/quote_b 保证接地于输入文本。

    ``touches_synopsis`` 标记矛盾是否涉及简介本身：正典内部矛盾
    （premise↔spine，如 35 岁 vs 三十年厨房功夫）不是文案的错——拿它连坐
    冠军候选会把候选全毙掉再回退到同病的 v0，等于白跑。候选淘汰只看
    touches_synopsis=True 的；正典矛盾交给构思重生循环去改正典。
    """

    kind: str        # _FATAL_KINDS | _ADVISORY_KINDS 之一
    quote_a: str
    quote_b: str     # _SINGLE_QUOTE_KINDS 时允许为空（单引文病）
    explanation: str
    touches_synopsis: bool = True

    @property
    def is_fatal(self) -> bool:
        """是否有候选出局权。教学轴（机制/无锚/论据）只留痕不杀。"""

        return self.kind in _FATAL_KINDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "quote_a": self.quote_a,
            "quote_b": self.quote_b,
            "explanation": self.explanation,
            "touches_synopsis": self.touches_synopsis,
            "fatal": self.is_fatal,
        }


@dataclass(frozen=True)
class CoherenceReport:
    findings: tuple[CoherenceFinding, ...]
    llm_used: bool
    dropped_unverified: int = 0  # 模型给了但引文核对失败（幻觉）的条数

    @property
    def passed(self) -> bool:
        """fail-open：判官不可用视为通过；有核实矛盾才算不过。"""

        return not self.findings

    @property
    def synopsis_findings(self) -> tuple[CoherenceFinding, ...]:
        """涉及简介本身的全部发现（含教学轴，供留痕与打磨反馈）。"""

        return tuple(f for f in self.findings if f.touches_synopsis)

    @property
    def fatal_synopsis_findings(self) -> tuple[CoherenceFinding, ...]:
        """涉及简介且有出局权的发现——候选淘汰只看这些。"""

        return tuple(f for f in self.findings if f.touches_synopsis and f.is_fatal)

    @property
    def canon_findings(self) -> tuple[CoherenceFinding, ...]:
        """纯正典内部矛盾（premise↔spine）——要送回构思重生去改，不怪文案。"""

        return tuple(f for f in self.findings if not f.touches_synopsis)

    def feedback_lines(self) -> list[str]:
        """供重生循环回灌的整改行（一条矛盾一行，带原文）。"""

        return [
            f"自相矛盾（{f.kind}）：「{f.quote_a}」与「{f.quote_b}」不能同时成立"
            f"——{f.explanation}。改到只留一个说法。"
            for f in self.findings
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "llm_used": self.llm_used,
            "dropped_unverified": self.dropped_unverified,
            "passed": self.passed,
            "schema_version": "blurb-coherence.v1",
        }


def build_coherence_messages(
    *, synopsis: str, premise: str = "", spine: dict[str, Any] | None = None
) -> tuple[str, str]:
    """(system, user)。输入把简介和正典事实并排给，跨文本矛盾一起查。"""

    spine_block = ""
    if spine:
        spine_block = "\n".join(
            f"  {k}：{v}" for k, v in spine.items() if str(v or "").strip()
        )
    # ⚠️ 这里保持 2026-08-07 验证过的四类窄任务。逻辑病三新轴
    # （mechanism/dangling/claim_unsupported）不进这条大杂烩调用——
    # 2026-08-23 A/B 实测：七类塞一个 prompt 后病稿 3 轮只中 1 次、
    # 对照稿反被冤 2 次，正是 hook-pull 定过罪的「注意力稀释」。
    # 新轴走 build_axis_prosecution_messages 的每轴独立检察官调用。
    system = (
        "你是逻辑校对员。只做一件事：在给出的文本里找**互相矛盾**的说法。"
        "矛盾指两处说法不能同时为真——时间期限互斥、同一物品/人物状态冲突、"
        "数字对不上（年龄与经历年数）、指代没有着落。"
        "不评价文笔，不提建议，不找'可以更好'的地方——只找硬矛盾。"
        "没有矛盾就输出空列表，不要硬凑。\n"
        "每条矛盾必须给出两段**逐字引用的原文**（quote_a、quote_b，各≤40字，"
        "必须能在原文里原样找到，一个字都不能改），引不出原文的不要报。\n"
        '只输出 JSON：{"contradictions": [{"kind": "timeline|fact|reference|number", '
        '"quote_a": "...", "quote_b": "...", "why": "一句话说明为何不能同时成立"}]}'
    )
    user_parts = []
    if premise.strip():
        user_parts.append(f"【前提】\n{premise.strip()}")
    if spine_block:
        user_parts.append(f"【故事脊柱】\n{spine_block}")
    user_parts.append(f"【简介】\n{synopsis.strip()}")
    user_parts.append("找出以上文本内部及互相之间的硬矛盾。输出严格 JSON。")
    return system, "\n\n".join(user_parts)


def _normalise_for_quote_match(s: str) -> str:
    """引文核对前压掉空白——模型常丢换行，但一个字都不许改。"""

    return "".join(str(s or "").split())


# 引文接地判据：全或无的逐字匹配在真机上不稳——同一 prompt 逐次采样，模型
# 偶尔掐头去尾或改一个标点，真发现就被整条当幻觉丢掉（冒烟实测 dropped=1、
# findings=0，检测器直接归零）。改成「长公共连续片段」：引文里必须有一段
# ≥ max(8, 60% 长度) 的连续文字逐字存在于原文。凭空编造的引文不会与原文
# 共享这么长的连续片段，可证伪性不丢。
_QUOTE_MIN_RUN = 8


def _quote_grounded(quote: str, haystack: str) -> bool:
    q = _normalise_for_quote_match(quote)
    if not q:
        return False
    if q in haystack:
        return True
    need = max(_QUOTE_MIN_RUN, int(len(q) * 0.6))
    if len(q) < need:
        return False
    for length in range(len(q), need - 1, -1):
        for start in range(0, len(q) - length + 1):
            if q[start : start + length] in haystack:
                return True
    return False


def parse_and_verify(
    raw: str,
    *,
    source_texts: tuple[str, ...],
    focus_text: str = "",
) -> tuple[tuple[CoherenceFinding, ...], int]:
    """解析模型输出并逐条核对引文；返回 (核实的发现, 丢弃的幻觉数)。

    ``focus_text``（通常是简介）非空时，为每条发现标注 ``touches_synopsis``：
    至少一段引文接地于 focus_text 才为 True。为空时全部视为 True（向后兼容）。
    """

    s = (raw or "").strip()
    payload: Any = None
    try:
        payload = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                payload = None
    items = payload.get("contradictions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return (), 0

    haystack = _normalise_for_quote_match("\n".join(source_texts))
    verified: list[CoherenceFinding] = []
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        qa = str(item.get("quote_a") or "").strip()
        qb = str(item.get("quote_b") or "").strip()
        kind = str(item.get("kind") or "fact")
        if kind not in _FATAL_KINDS | _ADVISORY_KINDS:
            kind = "fact"
        # dangling 是单引文病（指代在全文找不到着落，天然没有第二段引文）；
        # 其余各类仍双引文必填。
        if not qa or (kind not in _SINGLE_QUOTE_KINDS and (not qb or qa == qb)):
            dropped += 1
            continue
        if not _quote_grounded(qa, haystack) or (
            qb and not _quote_grounded(qb, haystack)
        ):
            dropped += 1  # 幻觉引文，丢弃——这是整个设计的核心保险
            continue
        focus = _normalise_for_quote_match(focus_text)
        touches = True
        if focus:
            touches = _quote_grounded(qa, focus) or _quote_grounded(qb, focus)
        verified.append(
            CoherenceFinding(
                kind=kind,
                quote_a=qa[:60],
                quote_b=qb[:60],
                explanation=str(item.get("why") or "")[:120],
                touches_synopsis=touches,
            )
        )
    return tuple(verified), dropped


# ── 逻辑病每轴检察官（2026-08-23）────────────────────────────────────
#
# 真机 v0 简介「判官笔每划一下就吸寡妇一口气 × 划自己掌心救她」的机制自噬、
# 「三个比宋礼年早动手」的无锚比较、「没人想让他活」+ 三条无关贪腐例证——
# 用户读得出病，全部量具零发现。把三类病塞进上面的大杂烩调用 A/B 实测是
# 噪声（病稿 3 轮中 1、对照稿被冤 2），照 hook-pull 定案改成每轴一个窄任务
# 检察官：一次只诉一种病，带正例+反例边界，引文核对照旧。
_AXIS_PROSECUTIONS: dict[str, str] = {
    "mechanism": (
        "你是设定校对员。只查一种病：**机制自相矛盾**——同一动作/物品在文中"
        "两处被赋予不能同时成立的效果，且文中毫无交代。\n"
        "正例：先设定「笔每划一下就吸她一口气」，后文却让主角「划破自己掌心"
        "救她」——按设定，救人这一划同时又在吸被救者的气，文中没有任何解释。\n"
        "反例（不算病）：设定「笔吸执笔人自己的阳气」而主角划自己掌心——"
        "代价一致，机制自洽；或文中明确交代了例外条件。\n"
        "quote_a 引机制设定原文，quote_b 引与之冲突的动作原文。"
    ),
    # 2026-08-25《吃我一筷》定罪：「封灶签子」「宗门议灶」「第二道菜」这种
    # 教科书级无锚，本轴 6 次调用全零发现——旧反例给了「悬念式留白」的
    # 无条件逃生门，模型把一切无锚都自我合理化成留白。留白豁免现在必须
    # 拿引文自证：引不出着落原文就是病。
    "dangling": (
        "你是指代校对员。只查一种病：**无锚指代**——一句话里的比较、代词、"
        "省略宾语，或凭空出现的物件/事件/规矩，在**全文**找不到着落，"
        "读者无法知道它指什么、从哪来。\n"
        "正例：「三个比他早动手」——动手做什么，通篇没有说；「那件事之后他变了」"
        "——那件事全文未提；「把封灶签子改成入股契书」——灶几时被封、谁封的，"
        "全文只字未提，这个物件第一次出现就在被处置；「议灶那天」——这是什么"
        "议程，全文没有交代。\n"
        "反例（不算病）：着落在全文任意位置能**逐字引出**（哪怕在后文）；"
        "题材读者的公共常识（宗门、护体灵气这类通用设定词）不需要着落。\n"
        "想按『悬念式留白』豁免一处指代？只有当你能引出上下文里锁定所指的"
        "那句原文时才许豁免；引不出原文，它就是病，不是留白。\n"
        "quote_a 引无锚的那句原文，quote_b 留空字符串。"
    ),
    # 新轴（2026-08-25）：机制缺位。mechanism 轴只查「矛盾」，查不了「从未
    # 交代」——《吃我一筷》头号病（菜为什么能崩人护体，全篇零交代）在三个
    # 教学轴里无轴可诉。照「新检测器只挣重生和留痕」规矩进教学轴。
    "effect_unexplained": (
        "你是机制校对员。只查一种病：**效果无因**——文中发生了超出常理的"
        "显著效果（一个动作让人跪下/破功/暴富/暴毙这类），而**全文**没有"
        "任何一句交代它凭什么发生。\n"
        "正例：普通人吃了一筷子菜「护体灵气当场炸了」——菜有什么门道，"
        "全文只字未提；杂役做的饭让长老「护体当场崩散」——为什么他的饭有"
        "这个威力，通篇没有一句机制。\n"
        "反例（不算病）：全文任何位置（哪怕只有一句、哪怕在效果之后）存在"
        "交代机制或来历的句子（一件法器、一门功法、一条阵纹、一条规矩、"
        "一笔交易）——**只要你能逐字引出那句交代，就一律不报，无论它交代得"
        "多简略**；简略不是病，缺席才是，你只查「有没有」不查「够不够细」。"
        "效果本身在题材常识内（修士对轰破防不需要解释）也不报；"
        "同一机制多次生效只报第一次。\n"
        "报之前最后核一步：把效果句所在的整句和它前后各一句完整读一遍——"
        "机制常常就写在同一句的前半（「刻下某某纹——一夹菜灵气就被吸走」"
        "这种破折号连写，前半就是机制）或紧邻句里；只引后半个效果分句"
        "而无视同句机制，属于错报。\n"
        "quote_a 引效果发生的那句原文，quote_b 留空字符串。"
    ),
    "claim_unsupported": (
        "你是论证校对员。只查一种病：**论据撑不起论点**——文中先下一个断言，"
        "紧接着给出的例证与断言明显不是一回事。\n"
        "正例：断言「庙里没人想让他活」，例证却是卖寡妇、抹账、卖名册——"
        "全是各自谋利的贪腐，没有一条指向要他的命。\n"
        "反例（不算病）：例证与断言的支撑是间接但在故事逻辑里成立的"
        "（断言「三人都在抢神位」，例证是三人各自攒筹码——成立）；"
        "或断言本身是人物的主观感受。\n"
        "quote_a 引断言原文，quote_b 引例证原文。"
    ),
    # 正典对齐轴（2026-08-25《吃我一筷》定罪第二针）：「封灶签子/议灶/入股
    # 契书」全是正典里不存在的发明实体——词表检测器（ungrounded_claims）管
    # 亲属/数字这类封闭类别，管不了开放词汇。本轴拿正典当对照基准，是唯一
    # 见得到正典的检察官（_CANON_AWARE_AXES）。
    "invented_entity": (
        "你是实体校对员。手里有两份材料：【正典】（已批准构思）和【简介】"
        "（对外文案）。只查一种病：**发明实体**——简介里出现了承担剧情功能的"
        "具体实体（物件、事件、制度、机构、人物），在正典里完全找不到对应物。\n"
        "正例：正典通篇没有封灶、入股之说，简介却写「把封灶签子改成入股契书」"
        "——签子和契书都是凭空发明的剧情道具。\n"
        "反例（不算病）：同一事物的换称、简称或翻译（正典叫「匠修」简介叫"
        "「刻纹师傅」）；从正典事实自然推出的场面细节（正典说他做饭，简介写"
        "「一碗红烧肉」）；通用背景词（宗门、长老、灵气这类题材公共设定）。"
        "只报**承担剧情转折功能**却无正典对应物的实体；拿不准算不算对应，"
        "就不报。\n"
        "quote_a 引简介里含该实体的那句原文，quote_b 留空字符串。"
    ),
}

# 需要正典对照的轴（其余轴只看简介，输入保持逐字节不变）。
_CANON_AWARE_AXES = frozenset({"invented_entity"})

_AXIS_SHARED_TAIL = (
    "\n不评价文笔，不提建议。没有这种病就输出空列表，**宁缺勿滥，不要硬凑**。\n"
    "引文必须**逐字**来自原文（≤40字，一个字都不能改），引不出原文的不要报。\n"
    '只输出 JSON：{"contradictions": [{"kind": "%s", '
    '"quote_a": "...", "quote_b": "...", "why": "一句话说明"}]}'
)


def build_axis_prosecution_messages(
    axis: str, *, synopsis: str, canon_text: str = ""
) -> tuple[str, str]:
    """(system, user) —— 单轴检察官：一次只诉一种逻辑病。

    ``canon_text`` 只有 _CANON_AWARE_AXES 里的轴会用（拼进 user 供比对）；
    其他轴保持纯简介输入，prompt 逐字节不变。
    """

    if axis not in _AXIS_PROSECUTIONS:
        raise ValueError(f"unknown logic axis: {axis}")
    system = _AXIS_PROSECUTIONS[axis] + _AXIS_SHARED_TAIL % axis
    canon_block = (
        f"【正典（已批准构思，实体对照基准）】\n{canon_text.strip()}\n\n"
        if axis in _CANON_AWARE_AXES and str(canon_text or "").strip()
        else ""
    )
    user = (
        f"{canon_block}【简介】\n{synopsis.strip()}\n\n只查上述这一种病。输出严格 JSON。"
    )
    return system, user


async def verify_blurb_coherence(
    session: Any,
    settings: Any,
    *,
    synopsis: str,
    premise: str = "",
    spine: dict[str, Any] | None = None,
    project_id: Any = None,
    advisory_axes: bool = True,
) -> CoherenceReport:
    """对（premise + spine + synopsis）跑引文核对式矛盾扫描。永不 raise。

    ``advisory_axes=True`` 时额外为每类教学轴逻辑病各跑一次窄任务检察官调用
    （教学轴：只留痕+喂打磨，不出局——见 ``_ADVISORY_KINDS``）。
    """

    if not str(synopsis or "").strip():
        return CoherenceReport(findings=(), llm_used=False)
    try:
        from bestseller.services.llm import (
            LLMCompletionRequest,
            complete_text,
        )

        system, user = build_coherence_messages(
            synopsis=synopsis, premise=premise, spine=spine
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                model_tier="standard",
                system_prompt=system,
                user_prompt=user,
                fallback_response='{"contradictions": []}',
                prompt_template="blurb_coherence_judge",
                prompt_version="v1",
                # 2026-08-25：700 帽真机 1/6 截断（发现被静默丢弃），提到 1400。
                max_tokens_override=1400,
                project_id=project_id,
            ),
        )
        sources = tuple(
            t for t in (
                premise,
                "\n".join(f"{k}：{v}" for k, v in (spine or {}).items()),
                synopsis,
            ) if str(t or "").strip()
        )
        findings, dropped = parse_and_verify(
            completion.content or "", source_texts=sources, focus_text=synopsis
        )
        all_findings = list(findings)
        if advisory_axes:
            # invented_entity 轴的对照基准：premise+spine 拼成正典文本。
            # 没有正典可比（自由文本调用）时该轴自动跳过——空基准下人人都是
            # 发明实体，必然全量误报。
            _canon_text = "\n".join(
                t for t in (
                    premise,
                    "\n".join(f"{k}：{v}" for k, v in (spine or {}).items()),
                ) if str(t or "").strip()
            )
            for axis in sorted(_AXIS_PROSECUTIONS):
                if axis in _CANON_AWARE_AXES and len(_canon_text.strip()) < 80:
                    continue
                try:
                    ax_system, ax_user = build_axis_prosecution_messages(
                        axis, synopsis=synopsis, canon_text=_canon_text
                    )
                    ax_completion = await complete_text(
                        session,
                        settings,
                        LLMCompletionRequest(
                            logical_role="critic",
                            model_tier="standard",
                            system_prompt=ax_system,
                            user_prompt=ax_user,
                            fallback_response='{"contradictions": []}',
                            prompt_template="blurb_logic_axis_prosecutor",
                            prompt_version=f"v1-{axis}",
                            max_tokens_override=800,
                            project_id=project_id,
                        ),
                    )
                    ax_findings, ax_dropped = parse_and_verify(
                        ax_completion.content or "",
                        source_texts=(synopsis,),
                        focus_text=synopsis,
                    )
                    dropped += ax_dropped
                    # 检察官只许诉自己的轴——串轴产出按幻觉丢弃。
                    for f in ax_findings:
                        if f.kind == axis:
                            all_findings.append(f)
                        else:
                            dropped += 1
                except Exception:
                    logger.warning(
                        "logic axis prosecutor '%s' failed (fail-open)",
                        axis,
                        exc_info=True,
                    )
        return CoherenceReport(
            findings=tuple(all_findings), llm_used=True, dropped_unverified=dropped
        )
    except Exception:
        logger.warning("blurb coherence verify failed (fail-open)", exc_info=True)
        return CoherenceReport(findings=(), llm_used=False)


__all__ = [
    "CoherenceFinding",
    "CoherenceReport",
    "build_axis_prosecution_messages",
    "build_coherence_messages",
    "parse_and_verify",
    "verify_blurb_coherence",
]
