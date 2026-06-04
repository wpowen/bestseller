"""Simulation Oracle — MiroFish 群体智能推演接入层 (Phase 1 / T2).

把 MiroFish 的"群体智能推演"接成 BestSeller 的「故事推演脊柱」,锚在故事设计层
(:class:`StoryDesignKernel`)。本模块负责:

1. 从已生成小说 / 项目元数据**导出推演请求** (:func:`export_request_from_novel`)。
2. 调用一个 :class:`OracleClient` 做推演:
   - :class:`MiroFishClient` —— HTTP 接真 MiroFish (graph→sim→report),按需启用。
   - :class:`HeuristicOracle` —— 离线确定性兜底,**永不阻断流水线**,亦作单测 oracle。
3. 把 :class:`OracleResult` 映射成 ``beat_schedule`` / ``plot_tree`` 候选,
   注入既有 kernel (:func:`augment_kernel`),并保证:
   - 干净中文 (过 ``story_design_kernel_gate`` 的 ``fallback_source_leak`` 检测);
   - ``beat_schedule`` 覆盖到 ``target_chapters`` (避免 ``beat_schedule_incomplete``)。

设计原则:纯函数为主、不可变 DTO、对外部依赖优雅降级。详见
``docs/mirofish-integration-architecture-2026-06.md``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

# 复用框架"规划层商业门"的具象压力词表 —— 让 oracle 的榜单级具象度标准与下游
# commercial_planning_readiness 闸门**同源不漂移**。这是"确保融合"的关键:oracle
# 产出用的就是 gate 检查的同一把尺。
from bestseller.services.commercial_planning_readiness import (
    _CONCRETE_PRESSURE_TERMS as RANKING_PRESSURE_TERMS,
)
from bestseller.services.prewrite_quality_profile import (
    sanitize_distilled_leak,
)

# 主角能动性动词(规划层要求"主角在场上做决定")与可见损失词(要求"指明失败会失去什么")。
_AGENCY_VERBS: tuple[str, ...] = (
    "选择", "拒绝", "反击", "揭穿", "保护", "谈判", "设局", "赌",
    "夺回", "立规", "出招", "决断", "清算", "对抗",
)
_VISIBLE_LOSS_TERMS: tuple[str, ...] = (
    "失去", "舍弃", "代价", "灭口", "毁", "暴露", "倒戈", "把柄",
    "出局", "牺牲", "断绝", "重伤", "背叛",
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OracleConfig:
    """Simulation Oracle 运行配置 (从环境变量读取,不侵入中央 settings)。"""

    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    rounds: int = 30
    timeout_s: int = 600
    # T1:复用项目已配 LLM(MiMo/DeepSeek/...)做真推理,无需 MiroFish/Zep。
    llm_enabled: bool = False

    @classmethod
    def from_env(cls) -> OracleConfig:
        def _flag(name: str) -> bool:
            return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            enabled=_flag("MIROFISH_ORACLE_ENABLED"),
            base_url=os.getenv("MIROFISH_BASE_URL", "").strip(),
            api_key=os.getenv("MIROFISH_API_KEY", "").strip(),
            model=os.getenv("MIROFISH_MODEL", "").strip(),
            rounds=int(os.getenv("MIROFISH_ROUNDS", "30") or 30),
            timeout_s=int(os.getenv("MIROFISH_TIMEOUT_S", "600") or 600),
            llm_enabled=_flag("MIROFISH_ORACLE_LLM"),
        )


# ---------------------------------------------------------------------------
# 入参 DTO (角色种子 / 推演请求)
# ---------------------------------------------------------------------------

# 角色定位词 → 实体类型。更具体的定位词优先;"主角"仅当作开头主词时命中,
# 避免 "对照主角" 这类描述被误判 (与 Phase0 发现的 genre-misroute 同类 bug)。
_ROLE_HINTS: tuple[tuple[str, str], ...] = (
    ("阵法师", "Ally"),
    ("同伴", "Ally"),
    ("师尊", "Mentor"),
    ("师父", "Mentor"),
    ("天骄", "Rival"),
    ("反派", "Rival"),
    ("既得利益", "Rival"),
    ("对手", "Rival"),
)

_STANCE_BY_TYPE = {
    "Protagonist": "challenger",
    "Rival": "vested",
}


def infer_entity_type(description: str) -> str:
    """从角色描述推断叙事实体类型 (Protagonist/Ally/Mentor/Rival/Character)。"""

    desc = (description or "").strip()
    if desc.startswith("主角"):
        return "Protagonist"
    for hint, etype in _ROLE_HINTS:
        if hint in desc:
            return etype
    return "Character"


@dataclass(frozen=True)
class CharacterSeed:
    """喂给仿真的单个角色种子 (= 一个 Agent)。"""

    name: str
    description: str
    entity_type: str = "Character"
    state: str = ""  # 最新快照行

    @property
    def stance(self) -> str:
        return _STANCE_BY_TYPE.get(self.entity_type, "neutral")

    def persona(self) -> str:
        text = self.description.strip()
        if self.state.strip():
            text = f"{text}。当前状态：{self.state.strip()}"
        return text


@dataclass(frozen=True)
class CanonEdge:
    subject: str
    predicate: str
    value: str
    valid_from_ch: int = 0


@dataclass(frozen=True)
class OracleRequest:
    """一次推演请求。``volume_index`` 非空 = T3 滚动预演 (热启动,带真实快照)。"""

    slug: str
    target_chapters: int
    premise: str = ""
    world_summary: str = ""
    timeline: str = ""
    characters: tuple[CharacterSeed, ...] = ()
    canon_edges: tuple[CanonEdge, ...] = ()
    question: str = ""
    volume_index: int | None = None

    @property
    def protagonist(self) -> CharacterSeed | None:
        for c in self.characters:
            if c.entity_type == "Protagonist":
                return c
        return self.characters[0] if self.characters else None


# ---------------------------------------------------------------------------
# 产出 DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmergentBeat:
    chapter_range: str
    duty: str
    state_change: str
    payoff: str
    hook_or_aftereffect: str

    def to_kernel_dict(self) -> dict[str, str]:
        return {
            "chapter_range": self.chapter_range,
            "duty": self.duty,
            "state_change": self.state_change,
            "payoff": self.payoff,
            "hook_or_aftereffect": self.hook_or_aftereffect,
        }


@dataclass(frozen=True)
class EmergentSubplot:
    key: str
    line_type: str  # subplot/relationship/character/world/...
    label: str
    role: str
    current_state: str
    target_state: str
    dependency_on_mainline: str
    failure_if_removed: str

    def to_plot_node_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "line_type": self.line_type,
            "label": self.label,
            "role": self.role,
            "current_state": self.current_state,
            "target_state": self.target_state,
            "dependency_on_mainline": self.dependency_on_mainline,
            "failure_if_removed": self.failure_if_removed,
        }


@dataclass(frozen=True)
class MotivationFlag:
    character: str
    issue: str
    suggested_fix: str


@dataclass(frozen=True)
class OracleResult:
    beats: tuple[EmergentBeat, ...] = ()
    subplots: tuple[EmergentSubplot, ...] = ()
    motivation_flags: tuple[MotivationFlag, ...] = ()
    natural_direction: str = ""
    source: str = "heuristic"  # mirofish | heuristic | disabled
    # 榜单级质量自检:produces 是否达到可定稿的榜单级具象度。
    # 未达 → needs_enrichment,必须由真 MiroFish/planner LLM 升级后才能进终稿,
    # 绝不让通用草稿冒充榜单级成品。
    ranking_ready: bool = True
    quality_findings: tuple[str, ...] = ()

    @property
    def needs_enrichment(self) -> bool:
        return not self.ranking_ready


# ---------------------------------------------------------------------------
# 榜单级质量门 (绑定框架同源标准)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OracleQualityReport:
    """对 oracle 产出做榜单级具象度自检 (与 commercial_planning_readiness 同尺)。"""

    ranking_ready: bool
    concrete_beats: int
    total_beats: int
    findings: tuple[str, ...]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def evaluate_oracle_quality(result: OracleResult, request: OracleRequest) -> OracleQualityReport:
    """判断 oracle 产出是否达到榜单级具象度。

    标准(对齐框架规划层商业门):
    - 每个 beat 必须含**具体压力词**(逼/否则/夺/灭口/封锁…),拒绝抽象空话;
    - 开局 beat(黄金三章)还须同时有**主角能动性** + **可见损失** + **章末钩子**。
    """

    findings: list[str] = []
    beats = result.beats
    proto_name = request.protagonist.name if request.protagonist else ""
    concrete = 0
    for i, beat in enumerate(beats):
        blob = " ".join(
            (beat.duty, beat.state_change, beat.payoff, beat.hook_or_aftereffect)
        )
        is_concrete = _contains_any(blob, RANKING_PRESSURE_TERMS)
        if is_concrete:
            concrete += 1
        else:
            findings.append(f"beat[{beat.chapter_range}] 缺具体压力(抽象空话,达不到榜单级)")
        if i == 0:  # 黄金开局段更严
            if not (_contains_any(blob, _AGENCY_VERBS) or (proto_name and proto_name in blob)):
                findings.append(f"开局 beat[{beat.chapter_range}] 缺主角能动性(主角未在场上做决定)")
            if not _contains_any(blob, _VISIBLE_LOSS_TERMS):
                findings.append(f"开局 beat[{beat.chapter_range}] 缺可见损失/代价")
            if not beat.hook_or_aftereffect.strip():
                findings.append(f"开局 beat[{beat.chapter_range}] 缺章末钩子")
    ranking_ready = bool(beats) and concrete == len(beats) and not findings
    return OracleQualityReport(
        ranking_ready=ranking_ready,
        concrete_beats=concrete,
        total_beats=len(beats),
        findings=tuple(findings),
    )


def _attach_quality(result: OracleResult, request: OracleRequest) -> OracleResult:
    """跑榜单级自检并把结果写回 OracleResult (ranking_ready / quality_findings)。"""

    report = evaluate_oracle_quality(result, request)
    return replace(
        result,
        ranking_ready=report.ranking_ready,
        quality_findings=report.findings,
    )


# ---------------------------------------------------------------------------
# 客户端 Protocol + 两个实现
# ---------------------------------------------------------------------------


class OracleClient(Protocol):
    def deduce(self, request: OracleRequest) -> OracleResult: ...


# 六阶段冲突演进 (与 planning.md 对齐) → 中文 beat 模板。
# 模板含占位符 {P}=主角 / {A}=对手,且每段都内置**具体压力词 + 主角能动性 + 可见损失
# + 章末钩子**,使产出能达到榜单级具象度 (而非通用空话);全中文,过 leak/off_genre。
_PHASE_TEMPLATES: tuple[tuple[str, str, str, str], ...] = (
    ("建立核心承诺:{A}当场拦下{P},以威胁逼其证明价值,否则出局",
     "{P}被迫做出第一次决断——反击而非退让",
     "{P}以小博大夺回一次主动权,兑现首个爽点",
     "{A}撂下威胁:限期拿不出证据就灭口"),
    ("暗中角力升级:{A}一方布局封锁{P}的资源与人脉",
     "盟友关系出现裂痕,{P}发现情报被人倒卖",
     "{P}设局反将一军,当场揭穿一名内鬼",
     "一封匿名信暗示旧案另有主使"),
    ("信任崩塌:{P}因既往选择首次付出真实代价",
     "一名同伴被{A}逼迫倒戈或被灭口",
     "{P}承受损失但守住底线,兑现'赢之后反噬'的反套路",
     "{A}抛出{P}最不愿面对的把柄"),
    ("派系冲突全面爆发,局势从个人对抗升级为势力对决",
     "{P}被迫公开立场,与{A}阵营正面开战",
     "{P}赢下一场关键战役,却也暴露更大危机",
     "战后发现真正的幕后者另有其人"),
    ("存亡级终局危机:{A}逼{P}在两个挚爱之间抉择",
     "{P}被迫舍弃最珍视之物(major loss)",
     "兑现倒数第二段最深代价,情绪势能蓄满",
     "{P}带着重伤立下最后赌注"),
    ("内在转化与闭环:{P}完成从挑战者到立规者的转身",
     "{P}清算{A},重写规则",
     "兑现主题层面的回答与情感收束,主线闭环",
     "新秩序留下一个克制的续作钩子"),
)


def _ground_text(text: str, protagonist: str, antagonist: str) -> str:
    return text.replace("{P}", protagonist).replace("{A}", antagonist)


@dataclass(frozen=True)
class HeuristicOracle:
    """离线确定性 oracle。

    不联网、不调 LLM,用 win/loss 节奏 + 六阶段冲突从请求"长出"beat/支线/动机漏洞。
    它既是 MiroFish 不可用时的降级实现,也是单测里的稳定 oracle。产出全中文。
    """

    def deduce(self, request: OracleRequest) -> OracleResult:
        beats = _segment_beats(request.target_chapters, request)
        subplots = _subplots_from_characters(request.characters)
        flags = _motivation_flags(request.characters)
        proto = request.protagonist
        direction = (
            f"{proto.name}已在开局取得进展,第{(request.volume_index or 1) + 1}卷的真正压力"
            "应来自'赢之后的治理真空/代价',而非更强的敌人——以此校正卷卷皆胜的失衡。"
            if proto
            else "故事的下一段压力应来自既有胜利的反噬,而非单纯加码对手强度。"
        )
        draft = OracleResult(
            beats=beats,
            subplots=subplots,
            motivation_flags=flags,
            natural_direction=direction,
            source="heuristic",
        )
        return _attach_quality(draft, request)


@dataclass(frozen=True)
class MiroFishClient:
    """接真 MiroFish 的 HTTP 客户端 (graph→sim→report)。

    Phase 1 仅实现调用骨架;启用需 ``OracleConfig.enabled`` 且服务可达。
    任何异常都应由 :class:`SimulationOracle` 捕获并降级,本类不自行兜底。
    """

    config: OracleConfig

    def deduce(self, request: OracleRequest) -> OracleResult:  # pragma: no cover - 需真服务
        import httpx  # 延迟导入,避免无谓依赖

        payload = {
            "seed": _render_seed_document(request),
            "ontology": narrative_ontology(),
            "agent_profiles": [
                {
                    "name": c.name,
                    "persona": c.persona(),
                    "stance": c.stance,
                    "source_entity_type": c.entity_type,
                }
                for c in request.characters
            ],
            "requirement": request.question,
            "rounds": self.config.rounds,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        with httpx.Client(base_url=self.config.base_url, timeout=self.config.timeout_s) as client:
            resp = client.post("/api/report", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return _parse_report(data, request)


# ---------------------------------------------------------------------------
# T1:LLM 推演客户端(复用项目已配模型,如小米 MiMo / DeepSeek,0 新 key)
# ---------------------------------------------------------------------------

# system prompt 把"榜单级要求"硬编码进去:具体压力词、角色落地、win/loss 均衡、全中文、
# 严格 JSON。这样 LLM 的真推理产出从源头就对齐下游商业门,而非事后补救。
_LLM_ORACLE_SYSTEM = (
    "你是一个网文'群体智能编剧室'。给定一本小说的设定、角色和当前状态,你要像让角色真活一遍"
    "那样推演接下来的故事,产出可直接落进创作框架的结构化结果。\n\n"
    "硬性要求(达不到视为失败):\n"
    "1. 每条 beat 必须具体、有压力:出现'逼/否则/夺/抢/灭口/封锁/当场/把柄/代价'这类具体动作词,"
    "禁止'推进剧情/有所成长/更精彩'这类空话。\n"
    "2. 必须落到本书真实角色名上(用我给的主角/对手名),不要用'主角/对手'泛称。\n"
    "3. 走向要符合 win/loss 节奏:开局小胜立钩子,中段多挫折与代价,倒数第二段付出最深代价,"
    "结尾闭环;**避免主角每段都赢**。\n"
    "4. 涌现支线要来自配角的真实动机,且说明它如何与主线因果咬合。\n"
    "5. 指出任一动机站不住、会让读者出戏的角色。\n"
    "6. 全中文。**只输出 JSON,不要任何解释或代码围栏。**\n\n"
    "输出 JSON 结构:\n"
    "{\n"
    '  "natural_direction": "一句话点明故事真正该往哪走(尤其纠正卷卷皆胜)",\n'
    '  "beats": [{"chapter_range":"1-10","duty":"...","state_change":"...",'
    '"payoff":"...","hook_or_aftereffect":"..."}],\n'
    '  "subplots": [{"key":"emergent_1","line_type":"subplot|relationship|character|world",'
    '"label":"...","role":"...","current_state":"...","target_state":"...",'
    '"dependency_on_mainline":"...","failure_if_removed":"..."}],\n'
    '  "motivation_flags": [{"character":"...","issue":"...","suggested_fix":"..."}]\n'
    "}"
)


def _build_llm_oracle_user_prompt(request: OracleRequest) -> str:
    proto = request.protagonist.name if request.protagonist else "主角"
    rival = next((c.name for c in request.characters if c.entity_type == "Rival"), "对手")
    lines = [
        f"小说: {request.slug}  目标章数: {request.target_chapters}",
        f"主角: {proto}   主要对手: {rival}",
    ]
    if request.premise:
        lines += [f"前提: {request.premise.strip()[:600]}"]
    if request.characters:
        lines += ["角色:"]
        for c in request.characters:
            lines.append(f"  - {c.name}（{c.entity_type}）: {c.persona()[:200]}")
    if request.canon_edges:
        lines += ["地面真相:"]
        for e in request.canon_edges[:20]:
            lines.append(f"  - {e.subject} {e.predicate} = {e.value}")
    if request.timeline:
        lines += [f"时间轴: {request.timeline.strip()[:400]}"]
    lines += [
        "",
        f"请推演接下来的故事,beat 必须覆盖到第 {request.target_chapters} 章。",
        request.question or "给出走向、3-5条角色驱动beat、2-3条涌现支线、动机漏洞。",
    ]
    return "\n".join(lines)


def _parse_llm_oracle_json(raw: str, request: OracleRequest) -> OracleResult:
    """容错解析 LLM 返回的 JSON → OracleResult;任何异常/空结果回退 Heuristic。"""

    text = (raw or "").strip()
    if "```" in text:  # 去掉可能的代码围栏
        text = re.sub(r"```[a-zA-Z]*", "", text).replace("```", "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return HeuristicOracle().deduce(request)
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return HeuristicOracle().deduce(request)

    beats = tuple(
        EmergentBeat(
            chapter_range=str(b.get("chapter_range", "")).strip(),
            duty=str(b.get("duty", "")).strip(),
            state_change=str(b.get("state_change", "")).strip(),
            payoff=str(b.get("payoff", "")).strip(),
            hook_or_aftereffect=str(b.get("hook_or_aftereffect", "")).strip(),
        )
        for b in (data.get("beats") or [])
        if isinstance(b, dict) and b.get("chapter_range") and b.get("duty")
    )
    if not beats:  # LLM 没给有效 beat → 回退,保证下游永远有合法产出
        return HeuristicOracle().deduce(request)

    subplots = tuple(
        EmergentSubplot(
            key=str(s.get("key") or f"emergent_{i}").strip(),
            line_type=str(s.get("line_type") or "subplot").strip(),
            label=str(s.get("label", "")).strip() or "涌现支线",
            role=str(s.get("role", "")).strip() or "配角",
            current_state=str(s.get("current_state", "")).strip() or "待定",
            target_state=str(s.get("target_state", "")).strip() or "在本段被引爆并影响主线",
            dependency_on_mainline=str(s.get("dependency_on_mainline", "")).strip()
            or "与主线形成因果咬合",
            failure_if_removed=str(s.get("failure_if_removed", "")).strip()
            or "删去则世界对事件无反应,丰富度塌陷",
        )
        for i, s in enumerate(data.get("subplots") or [], start=1)
        if isinstance(s, dict)
    )
    flags = tuple(
        MotivationFlag(
            character=str(f.get("character", "")).strip(),
            issue=str(f.get("issue", "")).strip(),
            suggested_fix=str(f.get("suggested_fix", "")).strip(),
        )
        for f in (data.get("motivation_flags") or [])
        if isinstance(f, dict) and f.get("character")
    )
    parsed = OracleResult(
        beats=beats,
        subplots=subplots,
        motivation_flags=flags,
        natural_direction=str(data.get("natural_direction", "")).strip(),
        source="llm",
    )
    return _attach_quality(parsed, request)


@dataclass(frozen=True)
class LLMOracleClient:
    """用一个 LLM 做真推演。

    ``complete(system, user) -> raw_text`` 由调用方注入:
    - 生产期(Phase 3 接进 planner):注入框架 ``complete_text`` 的适配器(已有 session);
    - 独立验证/CLI:注入 litellm 直连适配器(复用 .env 里的 key,如 MiMo/DeepSeek)。
    解析失败/模型异常一律由 :class:`SimulationOracle` 兜底降级,本类不自行吞错。
    """

    complete: Callable[[str, str], str]

    def deduce(self, request: OracleRequest) -> OracleResult:
        raw = self.complete(_LLM_ORACLE_SYSTEM, _build_llm_oracle_user_prompt(request))
        return _parse_llm_oracle_json(raw, request)


def build_llm_oracle_prompts(request: OracleRequest) -> tuple[str, str]:
    """公开 API:返回 (system, user) 提示词,供异步调用方(如 planner)驱动外部 LLM。"""

    return _LLM_ORACLE_SYSTEM, _build_llm_oracle_user_prompt(request)


def parse_llm_oracle_result(raw: str, request: OracleRequest) -> OracleResult:
    """公开 API:把 LLM 原始返回解析成 OracleResult(失败回退 Heuristic)。"""

    return _parse_llm_oracle_json(raw, request)


# ---------------------------------------------------------------------------
# 门面
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimulationOracle:
    """对外门面:选择客户端、推演、注入 kernel,并保证降级不阻断。

    ``llm_complete`` 注入后(且 ``config.llm_enabled``),T1 走真 LLM 推演;
    选择优先级:真 MiroFish > LLM 真推演 > Heuristic 离线兜底。
    """

    config: OracleConfig = field(default_factory=OracleConfig)
    llm_complete: Callable[[str, str], str] | None = None

    def _client(self) -> OracleClient:
        if self.config.enabled and self.config.base_url:
            return MiroFishClient(self.config)
        if self.config.llm_enabled and self.llm_complete is not None:
            return LLMOracleClient(self.llm_complete)
        return HeuristicOracle()

    def deduce(self, request: OracleRequest) -> OracleResult:
        try:
            return self._client().deduce(request)
        except Exception:
            return HeuristicOracle().deduce(request)

    def augment_kernel(self, base_kernel: dict[str, Any], request: OracleRequest) -> dict[str, Any]:
        """用推演产出增强既有 kernel 的 beat_schedule / plot_tree。"""

        result = self.deduce(request)
        return augment_kernel(base_kernel, result, request.target_chapters)


# ---------------------------------------------------------------------------
# 纯函数:映射 / 注入 / 覆盖补全
# ---------------------------------------------------------------------------


def augment_kernel(
    base_kernel: dict[str, Any],
    result: OracleResult,
    target_chapters: int,
) -> dict[str, Any]:
    """把 OracleResult 注入 kernel 字典并清洗。

    - ``beat_schedule``:若原 kernel 为空/过短,用推演 beats 替换并补全章节覆盖。
    - ``plot_tree``:追加涌现支线 (按 key 去重),保留既有 main 线。
    - 全量 ``sanitize_distilled_leak`` 防止任何泄漏触发 gate。
    """

    kernel = dict(base_kernel)

    # --- beat_schedule:保证覆盖到 target_chapters ---
    incoming_beats = [b.to_kernel_dict() for b in result.beats]
    existing_beats = list(kernel.get("beat_schedule") or [])
    chosen = (
        incoming_beats
        if _coverage(incoming_beats) >= _coverage(existing_beats)
        else existing_beats
    )
    kernel["beat_schedule"] = _ensure_chapter_coverage(chosen, target_chapters)

    # --- plot_tree:追加支线,保留 main,去重 ---
    existing_nodes = list(kernel.get("plot_tree") or [])
    seen_keys = {str(n.get("key")) for n in existing_nodes if isinstance(n, dict)}
    for sp in result.subplots:
        node = sp.to_plot_node_dict()
        if node["key"] not in seen_keys:
            existing_nodes.append(node)
            seen_keys.add(node["key"])
    kernel["plot_tree"] = existing_nodes

    # --- 榜单级守门:未达榜单级的产出必须标记待升级,绝不冒充终稿 ---
    # extra=ignore 会在 pydantic 校验时丢弃该键,但 dict 流转给 orchestrator 时保留,
    # 用以提示"此 kernel 的 beat 为 ranking-aware 草稿,需经真仿真/LLM + commercial 门定稿"。
    if result.needs_enrichment:
        kernel["oracle_meta"] = {
            "ranking_ready": False,
            "needs_enrichment": True,
            "source": result.source,
            "findings": list(result.quality_findings),
            "note": "beat_schedule 为榜单感知草稿,定稿前必须经真 MiroFish/planner LLM 升级"
                    "并通过 commercial_planning_readiness / commercial_novel_gate。",
        }
    else:
        kernel["oracle_meta"] = {"ranking_ready": True, "source": result.source}

    cleaned = sanitize_distilled_leak(kernel)
    return cleaned if isinstance(cleaned, dict) else kernel


def _coverage(beats: list[dict[str, Any]]) -> int:
    """beat 列表覆盖到的最大章号 (与 gate 的解析规则一致)。"""

    max_ch = 0
    for beat in beats:
        raw = beat.get("chapter_range") if isinstance(beat, dict) else None
        if isinstance(raw, str):
            for m in re.finditer(r"(\d{1,4})(?:\s*[-至到]\s*(\d{1,4}))?", raw):
                max_ch = max(max_ch, int(m.group(2) or m.group(1)))
    return max_ch


def _ensure_chapter_coverage(
    beats: list[dict[str, Any]], target_chapters: int
) -> list[dict[str, Any]]:
    """确保 beat_schedule 覆盖到 target_chapters,否则把末段补到末章。"""

    if not beats:
        beats = [b.to_kernel_dict() for b in _segment_beats(target_chapters)]
    if target_chapters <= 0:
        return beats
    if _coverage(beats) >= target_chapters:
        return beats
    tail = dict(beats[-1])
    start = _coverage(beats) + 1
    tail["chapter_range"] = f"{min(start, target_chapters)}-{target_chapters}"
    return [*beats[:-1], tail]


def _segment_beats(
    target_chapters: int, request: OracleRequest | None = None
) -> tuple[EmergentBeat, ...]:
    """按六阶段把 1..target_chapters 切成覆盖完整的 beat 段。

    win/loss 与具体压力已编码进模板;若给了 request,则把 {P}/{A} 落到本书真实
    主角/对手名上,使 beat 具象到"这一本书"而非通用空话(榜单级前提)。
    """

    protagonist = "主角"
    antagonist = "对手"
    if request is not None:
        if request.protagonist:
            protagonist = request.protagonist.name
        rival = next((c for c in request.characters if c.entity_type == "Rival"), None)
        if rival:
            antagonist = rival.name

    n = max(1, int(target_chapters or 1))
    seg_count = min(len(_PHASE_TEMPLATES), max(1, (n + 9) // 10))
    bounds = _even_ranges(n, seg_count)
    beats: list[EmergentBeat] = []
    for i, (lo, hi) in enumerate(bounds):
        duty, change, payoff, hook = _PHASE_TEMPLATES[i if i < len(_PHASE_TEMPLATES) else -1]
        beats.append(
            EmergentBeat(
                chapter_range=f"{lo}-{hi}" if lo != hi else f"{lo}",
                duty=_ground_text(duty, protagonist, antagonist),
                state_change=_ground_text(change, protagonist, antagonist),
                payoff=_ground_text(payoff, protagonist, antagonist),
                hook_or_aftereffect=_ground_text(hook, protagonist, antagonist),
            )
        )
    return tuple(beats)


def _even_ranges(n: int, parts: int) -> list[tuple[int, int]]:
    parts = max(1, min(parts, n))
    size = n // parts
    rem = n % parts
    ranges: list[tuple[int, int]] = []
    cur = 1
    for i in range(parts):
        span = size + (1 if i < rem else 0)
        ranges.append((cur, cur + span - 1))
        cur += span
    return ranges


_LINE_TYPE_BY_ENTITY = {
    "Ally": "relationship",
    "Mentor": "subplot",
    "Rival": "subplot",
    "Faction": "world",
}


def _subplots_from_characters(characters: tuple[CharacterSeed, ...]) -> tuple[EmergentSubplot, ...]:
    out: list[EmergentSubplot] = []
    idx = 0
    for c in characters:
        if c.entity_type == "Protagonist":
            continue
        idx += 1
        line_type = _LINE_TYPE_BY_ENTITY.get(c.entity_type, "subplot")
        cur = c.state.strip() or c.description.strip()
        out.append(
            EmergentSubplot(
                key=f"emergent_{idx}_{_slug(c.name)}",
                line_type=line_type,
                label=f"{c.name}的涌现暗线",
                role=c.description.strip() or f"{c.name}",
                current_state=cur,
                target_state=f"{c.name}的动机在本段被正面引爆并影响主线",
                dependency_on_mainline=f"{c.name}的选择改变主角面对的局势,与主线形成因果咬合",
                failure_if_removed="删去则主角的胜利缺乏外部映照,世界对事件无反应,丰富度塌陷",
            )
        )
    return tuple(out)


def _motivation_flags(characters: tuple[CharacterSeed, ...]) -> tuple[MotivationFlag, ...]:
    flags: list[MotivationFlag] = []
    for c in characters:
        desc = c.description.strip()
        thin = not re.search(r"代价|失去|输不起|把柄|契约|stake|压力", desc)
        if c.entity_type == "Rival" and thin:
            flags.append(
                MotivationFlag(
                    character=c.name,
                    issue="反派动机过薄:缺少'输不起的具体东西',下一步对抗会显假、令读者出戏",
                    suggested_fix=(
                        f"在 story-bible 给{c.name}补一条 stake"
                        "(他个人会因主角推进而失去什么)"
                    ),
                )
            )
    return tuple(flags)


def _slug(name: str) -> str:
    return re.sub(r"\s+", "_", (name or "x").strip()) or "x"


# ---------------------------------------------------------------------------
# 叙事本体 (替换 MiroFish 社媒本体) + 种子文档渲染
# ---------------------------------------------------------------------------


def narrative_ontology() -> dict[str, Any]:
    """叙事本体:实体=故事主体,关系=叙事驱动力 (供 MiroFish graph 直注)。"""

    return {
        "entity_types": [
            {"name": "Protagonist", "description": "主视角推进主弧的核心角色"},
            {"name": "Ally", "description": "(暂时)与主角同盟的同伴"},
            {"name": "Mentor", "description": "师长/前辈,常背负复杂利害"},
            {"name": "Rival", "description": "同级对手/既得利益代表"},
            {"name": "Faction", "description": "宗门/家族/势力集团"},
            {"name": "Authority", "description": "主角挑战的立规/执序方"},
            {"name": "Location", "description": "关键地点"},
            {"name": "Artifact", "description": "血脉/功法/法宝/关键物件"},
            {"name": "Character", "description": "其他具名角色兜底"},
            {"name": "Group", "description": "其他集体兜底"},
        ],
        "edge_types": [
            {"name": "SEEKS", "description": "追求目标/物件"},
            {"name": "ALLIED_WITH", "description": "当前同盟"},
            {"name": "MENTORS", "description": "教导/指引"},
            {"name": "OPPOSES", "description": "对抗/阻挡"},
            {"name": "OWES_DEBT_TO", "description": "道义/血/人情债"},
            {"name": "BELONGS_TO", "description": "归属"},
            {"name": "GUARDS", "description": "守护/掌控"},
            {"name": "THREATENS", "description": "威胁"},
        ],
        "analysis_summary": "Narrative ontology for swarm story-direction prediction.",
    }


def _render_seed_document(request: OracleRequest) -> str:
    parts = [f"# 叙事种子 — {request.slug}", ""]
    if request.premise:
        parts += ["## 前提", request.premise, ""]
    if request.world_summary:
        parts += ["## 世界观", request.world_summary, ""]
    parts += ["## 角色 (= Agent)"]
    for c in request.characters:
        parts.append(f"- {c.name}（{c.entity_type}/{c.stance}）：{c.persona()}")
    if request.canon_edges:
        parts += ["", "## 地面真相"]
        for e in request.canon_edges:
            parts.append(f"- {{{e.subject}}} {e.predicate} = {e.value} (ch{e.valid_from_ch})")
    if request.timeline:
        parts += ["", "## 时间轴", request.timeline]
    task = request.question or "推演接下来最可能的故事走向、涌现支线与动机漏洞。"
    parts += ["", "## 推演任务", task]
    return "\n".join(parts)


def _parse_report(data: dict[str, Any], request: OracleRequest) -> OracleResult:  # pragma: no cover
    """把 MiroFish 报告 JSON 蒸馏成 OracleResult。

    MiroFish 报告结构随版本变化,此处只取约定字段;缺字段时回退到 HeuristicOracle,
    确保产出永远可注入 kernel。
    """

    beats = tuple(
        EmergentBeat(
            chapter_range=str(b.get("chapter_range", "")),
            duty=str(b.get("duty", "")),
            state_change=str(b.get("state_change", "")),
            payoff=str(b.get("payoff", "")),
            hook_or_aftereffect=str(b.get("hook_or_aftereffect", "")),
        )
        for b in (data.get("beats") or [])
        if b.get("chapter_range")
    )
    if not beats:
        return HeuristicOracle().deduce(request)
    parsed = OracleResult(
        beats=beats, source="mirofish", natural_direction=str(data.get("direction", ""))
    )
    return _attach_quality(parsed, request)


# ---------------------------------------------------------------------------
# 从已生成小说导出推演请求 (Phase0 导出器的产品化)
# ---------------------------------------------------------------------------

_NOVEL_LINE_RE = re.compile(r"\s*[-*]\s*([^：:]+)[：:]\s*(.+)")
_CANON_RE = re.compile(
    r"\{subject:\s*([^}]+)\}\D*`([^`]+)`\s*=\s*\"([^\"]+)\"[^)]*valid_from_ch=(\d+)"
)


def export_request_from_novel(
    novel_dir: str | Path,
    *,
    target_chapters: int,
    question: str = "",
    volume_index: int | None = None,
) -> OracleRequest:
    """读取 ``output/ai-generated/{slug}/`` 结构化资产 → :class:`OracleRequest`。"""

    base = Path(novel_dir)
    bible = base / "story-bible"

    characters = tuple(
        CharacterSeed(name=name, description=desc, entity_type=infer_entity_type(desc),
                      state=_latest_snapshot(base).get(name, ""))
        for name, desc in _parse_kv(bible / "characters.md")
    )
    canon = tuple(
        CanonEdge(subject=s, predicate=p, value=v, valid_from_ch=int(ch))
        for s, p, v, ch in _CANON_RE.findall(_read(base / "knowledge" / "canon-facts.md"))
    )
    return OracleRequest(
        slug=base.name,
        target_chapters=target_chapters,
        premise=_read(bible / "premise.md"),
        world_summary=_read(bible / "world.md")[:4000],
        timeline=_read(base / "knowledge" / "timeline.md"),
        characters=characters,
        canon_edges=canon,
        question=question or "从最新快照出发,推演下一卷走向、涌现支线与动机漏洞。",
        volume_index=volume_index,
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _parse_kv(path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in _read(path).splitlines():
        m = _NOVEL_LINE_RE.match(line)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def _latest_snapshot(base: Path) -> dict[str, str]:
    snap_dir = base / "knowledge" / "character-snapshots"
    if not snap_dir.exists():
        return {}
    snaps = sorted(snap_dir.glob("after-ch-*.md"))
    if not snaps:
        return {}
    return dict(_parse_kv(snaps[-1]))
