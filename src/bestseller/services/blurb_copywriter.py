"""简介独立文案工序（T6, 2026-07-09）。

审计根因：简介此前由 conception finalize 顺手直出——一次 LLM 调用同时产
premise/writing_profile/简介，输入是全部设计 JSON，机制黑话天然漏进读者文案
（真机案例：'保饭碗还是丢工作？'同义反复选择句、'共情被削薄'等设计术语直接
出现在简介里）。

设计原则：简介是产品不是元数据。本模块把简介从"顺手产物"改成独立文案工序：

  1. 输入收窄——只给 spine 六字段 + premise + 金手指一句大白话 + 画像锚 +
     题材情绪范例 + 平台字数带 + 书名。禁止传入三提案 JSON / kernel / 方法论块 /
     world_model：设计视角进不来，黑话就漏不出去。
  2. N 路候选——不同策略角度独立生成（场景钩/身份反差/金手指爽点/规则悬念，
     按题材路由），而不是让模型一次定稿。
  3. 确定性病理筛——``blurb_pathology.detect_blurb_pathology`` 杀病句/黑话/
     模板残留，``blurb_appeal_gate.evaluate_blurb_appeal`` 打点击力分。
  4. 画像判官淘汰赛——``persona_click_judge`` 模拟目标读者 3 秒点不点，冠军
     取平均分最高者；判官不可用时降级为 gate 分排序（不阻塞整个工序）。
  5. 定向打磨——冠军仍不达标时按反馈聚焦重写一次（有界，不无限循环）。
  6. 永不劣于现状——冠军为空、或全部候选都命中致命病理时直接回退 v0；干净的
     冠军若是靠画像判官选出来的，不再用确定性 gate 分去否决它（v0 从未跑过
     persona 评估，两者不是同一把尺）；只有判官不可用、排序降级为 gate 分时，
     才需要 gate 分真的赢过 v0 才放行，否则回退 v0，``fell_back_to_v0=True``。

零依赖 conception.py（避免循环导入——conception.py 反过来调用本模块）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
import json
import logging
from typing import Any, Protocol

from bestseller.services.blurb_pathology import (
    PathologyFinding,
    detect_blurb_pathology,
    detect_ungrounded_blurb_claims,
)

logger = logging.getLogger(__name__)

# ruff: noqa: RUF001, RUF002 — Chinese fixtures/prompts are intentional.

_DEFAULT_STRATEGIES: dict[str, tuple[str, ...]] = {
    "default": ("scene_hook", "identity_contrast", "golden_finger_flex"),
    "suspense": ("scene_hook", "identity_contrast", "rule_suspense"),
}

_STRATEGY_DIRECTIVES: dict[str, str] = {
    "scene_hook": (
        "开局策略=场景钩：首句必须是一个具体时刻——谁、在哪、正在做什么，"
        "读者要像看见一个画面，不许用抽象陈述句开头。"
    ),
    "identity_contrast": (
        "开局策略=身份反差：首句先亮出主角的身份，紧接一个反差（身份与处境、"
        "或身份与真相之间的错位），让人立刻想知道为什么。"
    ),
    "golden_finger_flex": (
        "开局策略=金手指高能：首句或次句必须让读者秒懂主角有什么不一样的"
        "本事或优势，直给爽点，不绕弯子。"
    ),
    "rule_suspense": (
        "开局策略=规则悬念：首句立一条具体、反常的规则或异象，让人立刻想"
        "知道这条规则背后藏着什么。"
    ),
}

_SUSPENSE_TOKENS = ("悬疑", "推理", "怪谈", "恐怖", "惊悚", "灵异", "诡异", "犯罪")


class GeneratorFn(Protocol):
    """(system_prompt, user_prompt) -> (raw_text, llm_run_id)."""

    def __call__(self, system_prompt: str, user_prompt: str) -> Awaitable[tuple[str, Any]]: ...


@dataclass(frozen=True)
class BlurbCandidate:
    """One generated simple candidate + its scoring evidence."""

    strategy: str
    synopsis: str
    gate_score: float | None = None
    pathology: tuple[PathologyFinding, ...] = ()
    persona_click_rate: float | None = None
    persona_avg_score: float | None = None
    llm_run_id: Any = None
    # 引文核对过的自相矛盾（blurb_coherence_judge）。2026-08-07：四条倒计时
    # 互相打架的简介拿了 comprehensibility 满分——词表尺子对逻辑全盲，
    # 淘汰赛必须自己把矛盾候选踢出可选集。
    coherence_contradictions: tuple[dict[str, Any], ...] = ()

    @property
    def has_fatal_pathology(self) -> bool:
        return any(f.severity == "fatal" for f in self.pathology)

    @property
    def has_verified_contradiction(self) -> bool:
        # 出局权只归 fatal 轴（原四类事实矛盾，真机零冤案）。教学轴
        # （mechanism/dangling/claim_unsupported）只留痕+喂打磨，不杀候选。
        # 旧痕迹没有 fatal 键 → 按 fatal 处理（当年只存 fatal 类）。
        return any(f.get("fatal", True) for f in self.coherence_contradictions)

    @property
    def advisory_contradictions(self) -> tuple[dict[str, Any], ...]:
        """教学轴发现（不出局，供打磨反馈）。"""

        return tuple(
            f for f in self.coherence_contradictions if not f.get("fatal", True)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "synopsis": self.synopsis[:220],
            "gate_score": self.gate_score,
            "pathology": [f.to_dict() for f in self.pathology],
            "persona_click_rate": self.persona_click_rate,
            "persona_avg_score": self.persona_avg_score,
            "coherence_contradictions": list(self.coherence_contradictions),
        }


@dataclass
class BlurbCopywritingResult:
    """The full tournament record + the champion synopsis text."""

    champion: str
    champion_strategy: str
    candidates: list[BlurbCandidate] = field(default_factory=list)
    polish_rounds: int = 0
    fell_back_to_v0: bool = False
    persona_used: bool = False
    llm_run_ids: list[Any] = field(default_factory=list)
    # 冠军换掉了正典主角 → 调用方拒绝该冠军、保留 v0。记录下来而不是静默丢弃：
    # 这是文案工序的产出缺陷，必须能在 story_appeal 报告里被看到和追责。
    canon_name_rejected: bool = False
    canon_name_rogue: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "champion_strategy": self.champion_strategy,
            "candidates": [c.to_dict() for c in self.candidates],
            "polish_rounds": self.polish_rounds,
            "fell_back_to_v0": self.fell_back_to_v0,
            "persona_used": self.persona_used,
            "canon_name_rejected": self.canon_name_rejected,
            "canon_name_rogue": list(self.canon_name_rogue),
            "schema_version": "blurb-copywriting.v1",
        }


def load_copywriting_config(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = config.get("copywriting", {}) if isinstance(config, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}
    strategies = cfg.get("strategies") if isinstance(cfg.get("strategies"), dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "n_candidates": int(cfg.get("n_candidates", 3)),
        "persona_samples": int(cfg.get("persona_samples", 2)),
        "max_polish_rounds": int(cfg.get("max_polish_rounds", 1)),
        # The production acceptance bar is deliberately lower to avoid blocking a
        # whole book. A reader-facing candidate below this target still gets its
        # one bounded editorial pass before it can be surfaced as the champion.
        "target_gate_score": float(cfg.get("target_gate_score", 80)),
        "strategies": {
            "default": tuple(strategies.get("default") or _DEFAULT_STRATEGIES["default"]),
            "suspense": tuple(strategies.get("suspense") or _DEFAULT_STRATEGIES["suspense"]),
        },
    }


def _resolve_strategy_bucket(genre: str, sub_genre: str) -> str:
    from bestseller.services.genre_taxonomy import canonicalize

    canonical = str(canonicalize(genre, sub_genre) or "").lower()
    blob = f"{canonical} {genre} {sub_genre}".lower()
    if any(token in blob for token in _SUSPENSE_TOKENS):
        return "suspense"
    return "default"


def _truncate(text: str, limit: int) -> str:
    from bestseller.services.blurb_pathology import truncate_at_sentence

    return truncate_at_sentence(text or "", limit)


def _build_candidate_messages(
    strategy: str,
    *,
    spine: dict[str, Any],
    premise: str,
    golden_finger_line: str,
    title: str,
    tags: list[str],
    genre: str,
    sub_genre: str,
    platform: str | None,
    persona: Any,
    emotion_exemplars: tuple[str, ...],
    book_jargon_terms: tuple[str, ...],
    band: tuple[int, int],
    reader_contract: tuple[str, ...] = (),
) -> tuple[str, str]:
    directive = _STRATEGY_DIRECTIVES.get(strategy, _STRATEGY_DIRECTIVES["scene_hook"])
    lo, hi = band
    jargon_ban = "、".join(book_jargon_terms[:12]) if book_jargon_terms else "（无）"
    del emotion_exemplars  # (2026-08-01) framework event menus no longer enter prompts
    spine_block = "\n".join(
        f"  {k}：{v}" for k, v in spine.items() if str(v or "").strip()
    )
    system = (
        "你是顶尖中文网文详情页文案师，只写给完全不懂本书设定的陌生读者看。"
        "你的任务不是复述设定，是让人3秒内产生'这个我没见过但我秒懂'的冲动点击。"
    )
    user = (
        f"【故事脊柱】\n{spine_block}\n\n"
        f"【故事核】{_truncate(premise, 300)}\n\n"
        f"【金手指/核心规则一句话】{golden_finger_line or '（无）'}\n\n"
        f"【书名】{title}\n"
        f"【频道】{getattr(persona, 'channel', '通用')}\n"
        # 标签行的素材。读者契约来自建书勾选（轻松/爽文/不虐主角…），
        # 本书标签来自选题——两者都是**这本书自己的事实**，不是通用词表。
        f"【已勾选的读者承诺】{'、'.join(reader_contract) or '（无）'}\n"
        f"【本书选题标签】{'、'.join(tags[:10]) or '（无）'}\n"
        "【情绪事件】从本书自己的前提与冲突里选最强的高唤起事件前置，不套其他题材的情绪词。\n\n"
        f"{directive}\n\n"
        f"硬性要求：\n"
        # 形态规则重校于 2026-08-11 百本榜单实抓（docs/research/board-blurb-hook-
        # research-20260811.md）：中位 209 字 / 9 句 / 10 行 / 句均 23 字 / 一句
        # 一行；52% 头部直接贴正文级样本；62% 有标签行。旧的长句形态规则出自
        # 42 条精选语料，与在榜活数据冲突，废弃。
        "①【形态】第一行只有一串【】包住的词：3-6 个词用+号连接，"
        "整行长这样【无系统+单女主+轻松爽文】——**【】里只放词本身，"
        "不要把上面任何字段名（如「标签行」「读者承诺」）写进去**；"
        "只许写题材元素、设定关键词和避雷契约，且每个词都必须能从"
        "上面给的事实推出——禁止编造出版/短剧/评分/完本字数这类信用背书。"
        "标签行**至少 1 个词必须是读者决策信息**——虐不虐、轻不轻松、爽不爽、"
        "主角蠢不蠢，这类点进去之前就想知道的事；其余可以是题材元素。"
        "全是设定关键词的标签行等于没写。"
        "【已勾选的读者承诺】给的是本书勾选项对应的说法，可以直接用，"
        "**也可以换成更贴本书的写法**——要的是那类信息，不是那几个词。"
        f"标签行之后是正文：{lo}-{hi} 字（不含标签行），短句分行，一句一意，"
        "6-12 行，每行都能独立成立；不写大段落；\n"
        "②【体验样本，缺失即废稿】至少一段"
        "正文级样本原文——对白名场面、系统弹窗原文、或旁观者/对手的原话引语，"
        "用引号直接呈现，禁止转述成'他说了什么'。读者要的是预先尝到读这本书的"
        "感觉，不是听你介绍它；\n"
        "③【预期违背】至少一拍'以为X→实则Y'，能做三拍递进最好"
        "（第一天嗤之以鼻→第二天瞳孔地震→第三天三步一叩）；\n"
        "④【爽点见证】主角的强/爽必须由第三方反应呈现——对手颤抖、围观惊呼、"
        "亲友'？？？'——一句自夸都不许有；\n"
        "⑤正文首句≤30字；禁止设计/机制黑话——尤其是这些词："
        f"{jargon_ban}；出现即视为不合格；\n"
        "⑥不得剧透结局；收尾用陈述句或名场面截断——真榜单只有 6% 用问句收尾，"
        "问句的唯一合法位置是开头排比；禁止'殊不知/却不知道/她自己都不知道/"
        "到底还瞒着她什么'这类全知旁白式吊胃口；\n"
        "⑦零AI腔（本以为/却没想到/命运的齿轮/何去何从/敬请期待）；\n"
        "⑧设定里的学术词/机构名/生造术语（拓扑、语义、某某署这类）一律翻译成"
        "读者秒懂的大白话或具体画面——你在给完全不懂设定的人卖书，不是给设定"
        "集写目录；机制再聪明，说不成人话就是废稿。\n"
        "⑨【自洽铁律，违反即废稿】全文只许存在一条倒计时/期限——脊柱里若有多个"
        "时间压力，选最狠的一个，其余不写；只许使用上面【故事脊柱】【故事核】里"
        "已有的人物、物品、数字，不得发明新实体；每一句要能从上一句顺着因果读"
        "下来，不是各写各的卖点然后拼起来；交稿前逐句自查：任何两句放在一起"
        "不能互相矛盾（时间、物品在谁手里、人物年龄经历），发现矛盾删掉弱的那句。\n"
        "⑨b【机制保真】金手指/核心规则的因果链必须与【故事核】逐点一致——什么"
        "动作触发、产出什么（形态和数量）、发生在哪里，一个都不许改：正文按故事核"
        "写，简介许了不一样的诺，读者点进来就是上当。可以少写，不许改写。\n"
        "⑨c【脊柱有病自己裁决】若【故事脊柱】各字段互相打架（多个互斥期限、"
        "年龄与经历年数对不上），以【故事核】为准取其一，其余当作不存在——"
        "把矛盾照抄进简介同样算废稿。\n"
        '只输出 JSON：{"synopsis": "..."}，不要解释。'
    )
    return system, user


def _parse_synopsis_json(raw: str) -> str:
    text = (raw or "").strip()
    try:
        payload = json.loads(text, strict=False)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        payload = None
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(text[start : end + 1], strict=False)
            except json.JSONDecodeError:
                payload = None
    if isinstance(payload, dict):
        return str(payload.get("synopsis") or "").strip()
    # 兜底：未闭合 JSON。真机自闭环实测（2026-08-07）：模型偶发丢收尾的 "}"，
    # 内容完好却因 rfind("}") 落空整条报废——18 个候选里 5 个这样白扔（28%）。
    # 直接截取 synopsis 字符串体：取键后第一个引号到文本里最后一个引号。
    key = text.find('"synopsis"')
    if key != -1:
        colon = text.find(":", key)
        q1 = text.find('"', colon + 1) if colon != -1 else -1
        if q1 != -1:
            rest = text[q1 + 1 :]
            q2 = rest.rfind('"')
            body = rest[:q2] if q2 > 0 else rest
            body = body.replace("\\n", "\n").replace('\\"', '"')
            return body.strip()
    return ""


async def _default_generator(session: Any, settings: Any) -> GeneratorFn:
    async def _call(system_prompt: str, user_prompt: str) -> tuple[str, Any]:
        from bestseller.services.llm import LLMCompletionRequest, complete_text

        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="editor",
                model_tier="strong",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback_response="{}",
                prompt_template="blurb_copywriter_candidate",
                prompt_version="v1",
                max_tokens_override=700,
            ),
        )
        return completion.content or "", completion.llm_run_id

    return _call


def render_blurb_form_reminder(*, lo: int | None = None, hi: int | None = None) -> str:
    """榜单形态铁律的修稿版单一来源——所有「按诊断意见改简介」的路径共用。

    2026-08-18《矿脉认主》定罪：copywriter 冠军按百本榜单实抓形态产出
    （标签行+短句分行+陈述收尾），随后吸引力闸门的修复打磨
    （conception_blurb_polish）用一套相反的旧「点击型」规则重写——
    「首句强钩（疑问/反差）」「结尾留悬念」——把冠军改回单段+问句收尾，
    最后见光的简介两头不沾。同一事实（简介形态契约）住两地，后写的赢。
    任何新的简介改写路径必须拼入本块，不得自带形态规则。
    """

    band = f"正文 {lo}-{hi} 字（不含标签行）；" if lo and hi else ""
    return (
        "【榜单形态铁律（2026-08-11 百本实抓，修稿不得破坏）】"
        "保留开头的【标签行】（3-6 个词用+号连接，只许题材元素/设定关键词/避雷契约，"
        "禁编信用背书）；"
        f"{band}短句分行、一句一意、6-12 行，不写大段落；"
        "至少一段正文级样本原文（对白名场面/系统弹窗/旁观者原话，引号直贴不转述）；"
        "爽点由第三方反应呈现，一句自夸都不许有；"
        "收尾用陈述句或名场面截断——真榜单只有 6% 问句收尾，禁止问句收尾、"
        "禁止'殊不知/却不知道'式全知吊胃口；"
        "禁AI腔（本以为/却没想到/命运的齿轮/何去何从/敬请期待）。"
    )


async def _polish_champion(
    session: Any,
    settings: Any,
    *,
    synopsis: str,
    feedback: str,
    genre: str,
    sub_genre: str,
    language: str,
    premise: str = "",
    spine: dict[str, Any] | None = None,
) -> tuple[str, Any]:
    """One bounded focused-rewrite pass on the tournament champion.

    2026-08-07 修：此前编辑只拿到「当前简介 + 诊断意见」，手里没有任何事实
    基准——改写时自由变造事实（真机产出土豆自相矛盾、期限互斥的简介后，
    打磨环节无从发现也无从纠正）。现在把 premise/spine 作为事实准绳喂进去，
    并明令只调表达，不得增删改事实。
    """

    from bestseller.services.llm import LLMCompletionRequest, complete_text

    spine_block = "\n".join(
        f"  {k}：{v}" for k, v in (spine or {}).items() if str(v or "").strip()
    )
    anchor = ""
    if premise.strip() or spine_block:
        anchor = (
            f"【事实准绳（只许用这里的事实，一个字不许编）】\n"
            f"{premise.strip()}\n{spine_block}\n\n"
        )
    system = "你是顶尖中文网文详情页文案编辑，专精把不达标的简介按诊断意见改到位。"
    user = (
        f"题材：{genre}（{sub_genre}）\n{anchor}当前简介：\n{synopsis}\n\n"
        f"诊断意见：\n{feedback}\n\n"
        "请按诊断意见逐条改写这段简介：先给具体冲突，再讲规则代价，最后留下一个"
        "必须继续看的选择。删掉口语凑句、设定清单、泛泛反问和任何解释给策划看的话。"
        "读者只该看到人物正在被什么逼到墙角。\n"
        f"{render_blurb_form_reminder()}\n"
        "改写只许调整表达、顺序与详略：不得新增人物/物品/数字/期限，不得改变"
        "事实准绳里的任何事实；全文只许保留一条倒计时；交稿前逐句自查任何两句"
        "不得互相矛盾。"
        '只输出 JSON：{"synopsis": "..."}，不要解释。'
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="editor",
            model_tier="strong",
            system_prompt=system,
            user_prompt=user,
            fallback_response=json.dumps({"synopsis": synopsis}, ensure_ascii=False),
            prompt_template="blurb_copywriter_polish",
            prompt_version="v1",
            max_tokens_override=700,
            metadata={"language": language},
        ),
    )
    rewritten = _parse_synopsis_json(completion.content or "")
    return (rewritten or synopsis), completion.llm_run_id


def demote_default_family_candidates(
    candidates: Sequence["BlurbCandidate"],
    *,
    user_named_family: bool,
) -> tuple[list["BlurbCandidate"], list["BlurbCandidate"]]:
    """默认族（债务/账本/丧葬）的候选把冠军位让给不在族里的。降权，不是杀权。

    用户 2026-08-24 报「债务这块的问题反反复复一直出现」。取证：书9 读者实际
    看到的三行（书名+简介+一句话）合起来 `is_debt_dominated=True`、3 个子族。
    构思终稿那道门量的是 premise+synopsis+writing_profile 的**整体 blob**，
    **从不单独量读者会看到的那几行**——而那几行是用户唯一会看到的东西。

    淘汰赛本来就有 N 个候选，所以这里做**选择**而不是事后重写。形状与同日
    卡片层降权一致：

      · 比较式：同池里有不在族里的候选才让位
      · 全池同族 → 原样放行，**绝不清空池**（2026-08-06 定案）
      · 用户自己点名该族 → 完全跳过
      · 不向任何 prompt 写一个该族的词（否定式指令点名母题词＝种词）

    返回 (保留, 被降权的)——被降权的要留痕，用户有权知道成稿有没有落在
    被过度复用的族里。
    """

    from bestseller.services.anti_default_motif import is_debt_dominated

    items = list(candidates)
    if not items or user_named_family:
        return items, []
    clean = [c for c in items if not is_debt_dominated(str(c.synopsis or ""))]
    if not clean or len(clean) == len(items):
        return items, []
    dominated = [c for c in items if c not in clean]
    return clean, dominated


async def run_blurb_copywriting(
    session: Any,
    settings: Any,
    *,
    spine: dict[str, Any],
    premise: str,
    golden_finger_line: str,
    title: str,
    tags: list[str],
    genre: str,
    sub_genre: str,
    platform: str | None,
    language: str,
    v0_synopsis: str,
    hook_card: dict[str, Any] | None = None,
    emotion_exemplars: tuple[str, ...] = (),
    book_jargon_terms: tuple[str, ...] = (),
    reader_contract: tuple[str, ...] = (),
    user_named_default_family: bool = False,
    config: dict[str, Any] | None = None,
    generator: GeneratorFn | None = None,
    persona_judge: Any = None,
) -> BlurbCopywritingResult:
    """Run the independent blurb copywriting tournament. Never raises.

    ``generator``/``persona_judge`` are injectable for tests; production calls
    omit them and get the real LLM-backed defaults.
    """

    cfg = load_copywriting_config(config)
    if not cfg["enabled"]:
        return BlurbCopywritingResult(
            champion=v0_synopsis, champion_strategy="v0_disabled", fell_back_to_v0=True,
        )

    # Fact-grounding canon for ``detect_ungrounded_blurb_claims``: EVERY approved
    # surface, not just the premise. Calibration on the live negative control
    # 《灵根废我用烂账翻盘》 proved the difference — its blurb's 「赶在天亮前把泥封
    # 糊回去」 is absent from the premise but present in hook_card.decision_proof,
    # so a premise-only canon would have failed a perfectly grounded blurb.
    _canon_text = "\n".join(
        part
        for part in (
            str(premise or ""),
            "\n".join(f"{k}：{v}" for k, v in (spine or {}).items() if str(v or "").strip()),
            json.dumps(hook_card, ensure_ascii=False) if hook_card else "",
            str(golden_finger_line or ""),
        )
        if part.strip()
    )

    from bestseller.services.blurb_appeal_gate import (
        evaluate_blurb_appeal,
        platform_blurb_band,
    )
    from bestseller.services.genre_persona import resolve_persona

    llm_run_ids: list[Any] = []
    try:
        # 设置阶段本身也要 fail-open：画像/字数带/策略桶解析任何一步炸了都不该
        # 让"Never raises"的docstring落空——调用方(conception.py)虽然有外层
        # try/except兜底，但那样整个文案工序连"回退v0"的报告都拿不到，直接
        # 静默跳过；这里失败仍应产出一份可持久化的 v0 回退结果。
        persona = resolve_persona(genre, sub_genre, tuple(tags or ()))
        band = platform_blurb_band(platform, config)
        bucket = _resolve_strategy_bucket(genre, sub_genre)
        strategies = cfg["strategies"].get(bucket) or cfg["strategies"]["default"]
        strategies = tuple(strategies)[: max(1, cfg["n_candidates"])]
        gen_fn = generator
        if gen_fn is None:
            gen_fn = await _default_generator(session, settings)
    except Exception:
        logger.warning("blurb copywriting setup failed (non-fatal)", exc_info=True)
        return BlurbCopywritingResult(
            champion=v0_synopsis, champion_strategy="v0_setup_failed", fell_back_to_v0=True,
        )

    candidates: list[BlurbCandidate] = []
    for strategy in strategies:
        try:
            system, user = _build_candidate_messages(
                strategy,
                spine=spine, premise=premise, golden_finger_line=golden_finger_line,
                title=title, tags=tags, genre=genre, sub_genre=sub_genre,
                platform=platform, persona=persona, emotion_exemplars=emotion_exemplars,
                book_jargon_terms=book_jargon_terms, band=band,
                reader_contract=reader_contract,
            )
            raw, run_id = await gen_fn(system, user)
            if run_id is not None:
                llm_run_ids.append(run_id)
            synopsis = _parse_synopsis_json(raw)
            if not synopsis:
                continue
            pathology = tuple(
                detect_blurb_pathology(synopsis, book_jargon_terms=book_jargon_terms)
                + detect_ungrounded_blurb_claims(synopsis, canon_text=_canon_text)
            )
            verdict = evaluate_blurb_appeal(
                title=title, synopsis=synopsis, premise=premise, tags=tags,
                genre=genre, sub_genre=sub_genre, language=language, platform=platform,
                book_jargon_terms=book_jargon_terms,
            )
            candidates.append(
                BlurbCandidate(
                    strategy=strategy, synopsis=synopsis,
                    gate_score=verdict.total, pathology=pathology,
                )
            )
        except Exception:
            logger.warning("blurb copywriting candidate '%s' failed", strategy, exc_info=True)

    persona_used = False
    if candidates:
        try:
            from bestseller.services.persona_click_judge import run_persona_click_judge

            persona_used = True
            persona_candidates = list(candidates)
            for idx, cand in enumerate(persona_candidates):
                report = await run_persona_click_judge(
                    session, settings,
                    title=title, synopsis=cand.synopsis, genre=genre, sub_genre=sub_genre,
                    tags=tuple(tags or ()), samples=cfg["persona_samples"], judge=persona_judge,
                )
                persona_candidates[idx] = BlurbCandidate(
                    strategy=cand.strategy, synopsis=cand.synopsis,
                    gate_score=cand.gate_score, pathology=cand.pathology,
                    persona_click_rate=report.click_rate if report.llm_used else None,
                    persona_avg_score=report.avg_score if report.llm_used else None,
                )
            candidates = persona_candidates
            if not any(c.persona_avg_score is not None for c in candidates):
                persona_used = False
        except Exception:
            logger.warning("persona tournament failed; ranking by gate score", exc_info=True)
            persona_used = False

    # 自洽校验（引文核对式，fail-open）：词表尺子和画像判官对逻辑矛盾全盲
    # （2026-08-07 四条倒计时的简介拿 comprehensibility 满分），必须在选冠军前
    # 把核实有矛盾的候选踢出可选集。只对没有 fatal 病理的候选花这笔钱。
    try:
        from bestseller.services.blurb_coherence_judge import verify_blurb_coherence

        checked: list[BlurbCandidate] = []
        for cand in candidates:
            if cand.has_fatal_pathology:
                checked.append(cand)
                continue
            report = await verify_blurb_coherence(
                session, settings,
                synopsis=cand.synopsis, premise=premise, spine=spine,
            )
            # 只拿【涉及简介本身】的矛盾连坐候选；纯正典矛盾（premise↔spine）
            # 是构思的错，毙掉全部候选再回退到同病的 v0 等于白跑。
            checked.append(
                BlurbCandidate(
                    strategy=cand.strategy, synopsis=cand.synopsis,
                    gate_score=cand.gate_score, pathology=cand.pathology,
                    persona_click_rate=cand.persona_click_rate,
                    persona_avg_score=cand.persona_avg_score,
                    coherence_contradictions=tuple(
                        f.to_dict() for f in report.synopsis_findings
                    ),
                )
            )
            if report.fatal_synopsis_findings:
                logger.warning(
                    "blurb candidate '%s' rejected: %d verified contradiction(s): %s",
                    cand.strategy, len(report.fatal_synopsis_findings),
                    "; ".join(
                        f"{f.quote_a}↔{f.quote_b}"
                        for f in report.fatal_synopsis_findings
                    ),
                )
            _advisory = [f for f in report.synopsis_findings if not f.is_fatal]
            if _advisory:
                logger.warning(
                    "blurb candidate '%s' carries %d advisory logic finding(s) "
                    "(teaching only, not fatal): %s",
                    cand.strategy, len(_advisory),
                    "; ".join(f"[{f.kind}] {f.quote_a}" for f in _advisory),
                )
            if report.canon_findings:
                logger.warning(
                    "canon itself is contradictory (premise↔spine), not the blurb: %s",
                    "; ".join(
                        f"{f.quote_a}↔{f.quote_b}" for f in report.canon_findings
                    ),
                )
        candidates = checked
    except Exception:
        logger.warning("blurb coherence screen failed (fail-open)", exc_info=True)

    # Score every generated candidate for a complete audit trail, but keep
    # fatal-pathology / verified-contradiction candidates ineligible for
    # selection. `or list(candidates)` 是刻意保留的救援路径：全员不合格时仍选
    # 一个去打磨——打磨稿要重新过病理+自洽检查，救不回来的由下方结构性废单
    # 拦住回退 v0（那里同时检查 fatal 与矛盾），带病文案没有出场通道。
    survivors = [
        c for c in candidates
        if not c.has_fatal_pathology and not c.has_verified_contradiction
    ] or list(candidates)
    # 默认族降权（2026-08-24）：读者看到的那几行才是用户唯一会看到的东西，
    # 而构思终稿那道门量的是整体 blob，从不单独量它们。绝不清空池。
    survivors, _family_demoted = demote_default_family_candidates(
        survivors, user_named_family=user_named_default_family
    )

    def _rank_key(c: BlurbCandidate) -> tuple[float, float]:
        return (
            c.persona_avg_score if c.persona_avg_score is not None else -1.0,
            c.gate_score or 0.0,
        )

    champion = max(survivors, key=_rank_key) if survivors else None

    polish_rounds = 0
    if champion is not None:
        try:
            from bestseller.services.story_appeal import (
                build_improvement_feedback,
                load_story_appeal_config,
            )
        except ImportError:
            build_improvement_feedback = None  # type: ignore[assignment]
            load_story_appeal_config = None  # type: ignore[assignment]

        blurb_min = float(
            ((config or {}).get("meets_bar", {}) or {}).get("blurb_min", 68)
        )
        max_polish = cfg["max_polish_rounds"]
        needs_polish = (
            (champion.gate_score or 0.0)
            < max(blurb_min, float(cfg["target_gate_score"]))
            or any(f.severity == "warn" for f in champion.pathology)
            # 教学轴逻辑病（机制矛盾/无锚指代/论据不撑论点）不杀候选，
            # 但必须触发打磨——这正是它们挣到的「重生」权。
            or bool(champion.advisory_contradictions)
        )
        if needs_polish and max_polish > 0 and build_improvement_feedback:
            try:
                _appeal_cfg = load_story_appeal_config() if load_story_appeal_config else (config or {})
                verdict = evaluate_blurb_appeal(
                    title=title, synopsis=champion.synopsis, premise=premise, tags=tags,
                    genre=genre, sub_genre=sub_genre, language=language, platform=platform,
                    book_jargon_terms=book_jargon_terms,
                )
                from bestseller.domain.appeal import PremiseAppealVerdict, StoryAppealReport

                fake_report = StoryAppealReport(
                    genre=genre, sub_genre=sub_genre,
                    premise=PremiseAppealVerdict(total=0, grade="pass", gated_grade="pass"),
                    blurb=verdict, meets_bar=verdict.total >= blurb_min,
                    overall_grade=verdict.grade,
                )
                feedback = build_improvement_feedback(fake_report, _appeal_cfg)
                # 教学轴发现逐条带引文喂进打磨——量具已把病灶指到句子级，
                # 不给打磨手就等于白测。
                for _f in champion.advisory_contradictions:
                    _qa = str(_f.get("quote_a") or "")
                    _qb = str(_f.get("quote_b") or "")
                    _why = str(_f.get("explanation") or "")
                    if _qb:
                        feedback.append(
                            f"逻辑病（{_f.get('kind')}）：「{_qa}」与「{_qb}」"
                            f"放在一起立不住——{_why}。改到自洽。"
                        )
                    else:
                        feedback.append(
                            f"逻辑病（{_f.get('kind')}）：「{_qa}」在全文找不到"
                            f"着落——{_why}。补上着落或删掉。"
                        )
                polished, run_id = await _polish_champion(
                    session, settings, synopsis=champion.synopsis, feedback=feedback,
                    genre=genre, sub_genre=sub_genre, language=language,
                    premise=premise, spine=spine,
                )
                if run_id is not None:
                    llm_run_ids.append(run_id)
                polished_pathology = tuple(
                    detect_blurb_pathology(polished, book_jargon_terms=book_jargon_terms)
                    + detect_ungrounded_blurb_claims(polished, canon_text=_canon_text)
                )
                polished_verdict = evaluate_blurb_appeal(
                    title=title, synopsis=polished, premise=premise, tags=tags,
                    genre=genre, sub_genre=sub_genre, language=language, platform=platform,
                    book_jargon_terms=book_jargon_terms,
                )
                # 打磨稿是新文本，必须重新过自洽校验——改写完全可能把原本
                # 干净的冠军改出矛盾（fail-open：判官不可用时视为通过）。
                polished_contradictions: tuple[dict[str, Any], ...] = ()
                try:
                    from bestseller.services.blurb_coherence_judge import (
                        verify_blurb_coherence as _verify_polished,
                    )

                    _pol_report = await _verify_polished(
                        session, settings,
                        synopsis=polished, premise=premise, spine=spine,
                    )
                    polished_contradictions = tuple(
                        f.to_dict() for f in _pol_report.synopsis_findings
                    )
                except Exception:
                    logger.warning("polished coherence verify failed (fail-open)", exc_info=True)
                polish_rounds = 1
                if (
                    not any(f.severity == "fatal" for f in polished_pathology)
                    # 打磨稿验收同样只拿 fatal 轴否决；教学轴发现留痕即可，
                    # 否则教学轴反而会卡死自己触发的打磨。
                    and not any(
                        c.get("fatal", True) for c in polished_contradictions
                    )
                    and polished_verdict.total >= (champion.gate_score or 0.0)
                ):
                    champion = BlurbCandidate(
                        strategy=champion.strategy, synopsis=polished,
                        gate_score=polished_verdict.total, pathology=polished_pathology,
                        persona_click_rate=champion.persona_click_rate,
                        persona_avg_score=champion.persona_avg_score,
                    )
            except Exception:
                logger.warning("blurb champion polish failed (non-fatal)", exc_info=True)

    v0_verdict_total = 0.0
    try:
        v0_verdict = evaluate_blurb_appeal(
            title=title, synopsis=v0_synopsis, premise=premise, tags=tags,
            genre=genre, sub_genre=sub_genre, language=language, platform=platform,
            book_jargon_terms=book_jargon_terms,
        )
        v0_verdict_total = v0_verdict.total
    except Exception:
        logger.warning("v0 synopsis scoring failed (non-fatal)", exc_info=True)

    # 结构性废单：champion 为空（含全员致命病理/全员核实矛盾——这两类现在
    # 直接不进 survivors），或残留 fatal/矛盾。必须回退 v0，不看任何分数。
    if (
        champion is None
        or champion.has_fatal_pathology
        or champion.has_verified_contradiction
    ):
        return BlurbCopywritingResult(
            champion=v0_synopsis, champion_strategy="v0_fallback",
            candidates=candidates, polish_rounds=polish_rounds,
            fell_back_to_v0=True, persona_used=persona_used, llm_run_ids=llm_run_ids,
        )

    # 干净的冠军若是画像判官淘汰赛选出来的（persona_used），不再用确定性 gate
    # 分去否决它——gate 分和 persona 判断的读者视角不是同一把尺，v0 从未跑过
    # persona 评估，拿 gate 分单方面比会出现真实发生过的错序（真机验证：同一
    # 题材下，具体写实的候选 gate=66.0 分反而低于泛泛套话稿 gate=67.2 分）。
    # persona 淘汰赛已经是比 gate 分更贴近"读者会不会点"的信号，不该被它推翻。
    if persona_used and champion.persona_avg_score is not None:
        return BlurbCopywritingResult(
            champion=champion.synopsis, champion_strategy=champion.strategy,
            candidates=candidates, polish_rounds=polish_rounds,
            fell_back_to_v0=False, persona_used=persona_used, llm_run_ids=llm_run_ids,
        )

    # persona 不可用（判官全废/未启用），排序降级为确定性 gate 分——这时才用
    # 和 v0 同一把尺比较，比不过就回退。
    if (champion.gate_score or 0.0) < v0_verdict_total:
        return BlurbCopywritingResult(
            champion=v0_synopsis, champion_strategy="v0_fallback",
            candidates=candidates, polish_rounds=polish_rounds,
            fell_back_to_v0=True, persona_used=persona_used, llm_run_ids=llm_run_ids,
        )

    return BlurbCopywritingResult(
        champion=champion.synopsis, champion_strategy=champion.strategy,
        candidates=candidates, polish_rounds=polish_rounds,
        fell_back_to_v0=False, persona_used=persona_used, llm_run_ids=llm_run_ids,
    )


__all__ = [
    "BlurbCandidate",
    "BlurbCopywritingResult",
    "load_copywriting_config",
    "run_blurb_copywriting",
]
