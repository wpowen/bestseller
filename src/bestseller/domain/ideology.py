"""Core-ideology (母题) kernel — the book's durable thematic soul.

This is the structured "core idea" the framework planned *without* before: a
cosmic premise + thesis + a primary motif + two secondary motifs (one driving
action, one driving suspense) + a hidden endgame motif + a belief arc + a cost
system. It sits alongside (and feeds) :class:`StoryDesignKernel` so the
worldview, plot tree, beat schedule, volumes and chapters all derive from a
single thematic spine instead of from genre tropes alone.

Theory source:《从天地不仁到逆命而行的小说方法论》— 13 motifs in 4 layers
(cosmic-order / subject-choice / cognitive-crisis / ethical-reversal). The motif
*content* lives in ``config/motif_library.yaml``; this module is the per-book
*binding* of that content.

Design rules (mirroring story_design_kernel.py):
* Frozen pydantic, ``extra="ignore"`` so it can carry forward-compatible fields.
* Aggressive ``mode="before"`` alias normalization — the LLM emits drifting
  field names, and a single bad enrichment entry must never hard-fail the whole
  book's planning (the WorldviewKernel lesson). Unsalvageable list entries are
  dropped, not raised.
* No IO / LLM here. Derivation lives in ``services/ideology_kernel.py``;
  scoring in ``services/ideology_judge.py``; structural checks in
  ``services/ideology_coherence_gate.py``.
"""

# ruff: noqa: RUF001, E501, ANN401, S112  (Chinese rubric strings + LLM-alias validators dominate)

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MotifLayer = Literal[
    "cosmic_order",
    "subject_choice",
    "cognitive_crisis",
    "ethical_reversal",
]
SecondaryRole = Literal["action", "suspense"]

LAYER_KEYS: tuple[str, ...] = (
    "cosmic_order",
    "subject_choice",
    "cognitive_crisis",
    "ethical_reversal",
)

LAYER_DISPLAY: dict[str, str] = {
    "cosmic_order": "宇宙秩序层",
    "subject_choice": "主体抉择层",
    "cognitive_crisis": "认知危机层",
    "ethical_reversal": "伦理反转层",
}

_LAYER_ALIASES: dict[str, str] = {
    "cosmic": "cosmic_order",
    "cosmic_order": "cosmic_order",
    "宇宙": "cosmic_order",
    "宇宙秩序": "cosmic_order",
    "宇宙秩序层": "cosmic_order",
    "world": "cosmic_order",
    "subject": "subject_choice",
    "subject_choice": "subject_choice",
    "agency": "subject_choice",
    "主体": "subject_choice",
    "主体抉择": "subject_choice",
    "主体抉择层": "subject_choice",
    "choice": "subject_choice",
    "cognitive": "cognitive_crisis",
    "cognitive_crisis": "cognitive_crisis",
    "认知": "cognitive_crisis",
    "认知危机": "cognitive_crisis",
    "认知危机层": "cognitive_crisis",
    "epistemic": "cognitive_crisis",
    "ethical": "ethical_reversal",
    "ethical_reversal": "ethical_reversal",
    "伦理": "ethical_reversal",
    "伦理反转": "ethical_reversal",
    "伦理反转层": "ethical_reversal",
    "value": "ethical_reversal",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "；".join(item for item in (_text(v) for v in value) if item)
    if isinstance(value, dict):
        for key in ("value", "description", "summary", "statement", "text", "name"):
            text = _text(value.get(key))
            if text:
                return text
        return "；".join(item for item in (_text(v) for v in value.values()) if item)
    return ""


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _text(data.get(key))
        if text:
            return text
    return ""


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for text in (_text(item) for item in value) if text]
    text = _text(value)
    return [text] if text else []


def _normalize_layer(value: Any) -> str | None:
    raw = _text(value).lower()
    if not raw:
        return None
    if raw in _LAYER_ALIASES:
        return _LAYER_ALIASES[raw]
    for needle, canonical in _LAYER_ALIASES.items():
        if needle in raw:
            return canonical
    return None


class MotifBinding(BaseModel, frozen=True):
    """A library motif bound to THIS book's concrete material.

    ``motif_key`` references ``config/motif_library.yaml`` (or ``"custom"`` for a
    book-specific motif). ``role`` is set only on secondary motifs; ``reveal_after_volume``
    only on the hidden endgame motif.
    """

    motif_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    layer: MotifLayer
    book_thesis: str = Field(min_length=1)
    book_core_question: str = Field(min_length=1)
    concrete_symbols: list[str] = Field(default_factory=list)
    role: SecondaryRole | None = None
    reveal_after_volume: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data.setdefault("motif_key", _first_text(data, "key", "motif", "id") or "custom")
        data.setdefault(
            "display_name",
            _first_text(data, "name", "title", "motif_name", "label") or data["motif_key"],
        )
        layer = _normalize_layer(data.get("layer") or data.get("layer_key"))
        if layer is not None:
            data["layer"] = layer
        if not _text(data.get("book_thesis")):
            data["book_thesis"] = (
                _first_text(data, "thesis", "thesis_statement", "statement", "theme", "summary")
                or "本书将该母题落到具体人物与代价上。"
            )
        if not _text(data.get("book_core_question")):
            data["book_core_question"] = (
                _first_text(data, "core_question", "question", "dramatic_question")
                or "本书围绕该母题向读者提出的核心追问。"
            )
        data["concrete_symbols"] = _text_list(
            data.get("concrete_symbols")
            or data.get("symbols")
            or data.get("concrete_symbol_hints")
            or data.get("images")
        )
        role = _text(data.get("role")).lower()
        if role in {"action", "行动", "act", "drive"}:
            data["role"] = "action"
        elif role in {"suspense", "悬念", "mystery", "reveal"}:
            data["role"] = "suspense"
        elif role and role not in {"action", "suspense"}:
            data.pop("role", None)
        reveal = data.get("reveal_after_volume")
        if reveal is None:
            reveal = data.get("reveal_volume") or data.get("hidden_until_volume")
        try:
            data["reveal_after_volume"] = int(reveal) if reveal is not None else None
        except (TypeError, ValueError):
            data["reveal_after_volume"] = None
        return data


class BeliefArc(BaseModel, frozen=True):
    """The three binding questions: what the protagonist believes, what shatters
    it, what they rebuild — the spine that turns a motif into a long-novel engine."""

    initial_belief: str = Field(min_length=1)
    midpoint_shatter: str = Field(min_length=1)
    final_reconstruction: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("initial_belief")):
            data["initial_belief"] = _first_text(
                data, "initial", "believes", "starts_believing", "最初相信", "opening_belief"
            )
        if not _text(data.get("midpoint_shatter")):
            data["midpoint_shatter"] = _first_text(
                data, "shatter", "midpoint", "breaks", "中段打碎", "crisis"
            )
        if not _text(data.get("final_reconstruction")):
            data["final_reconstruction"] = _first_text(
                data, "reconstruction", "rebuilds", "final", "结尾重建", "new_order", "ending"
            )
        return data


class CostLaw(BaseModel, frozen=True):
    """One law of the cost system: power/truth/redemption is never free.

    Binds the ideology to the framework's existing cost/price machinery so
    "天地不仁" / "代价" do not stay slogans — every gain carries a visible bill.
    """

    acquires: str = Field(min_length=1)
    costs: str = Field(min_length=1)
    delayed: bool = False
    irreversible: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("acquires")):
            data["acquires"] = (
                _first_text(data, "gain", "acquire", "buys", "power", "obtains", "获得")
                or "力量/真相/救赎"
            )
        if not _text(data.get("costs")):
            data["costs"] = (
                _first_text(data, "cost", "price", "pays", "代价", "tradeoff")
                or "肉身/关系/记忆/寿命/身份之一"
            )
        for flag in ("delayed", "irreversible"):
            if flag in data:
                data[flag] = bool(data[flag])
        return data


class SubTheme(BaseModel, frozen=True):
    """A woven sub-theme (子题) — a concrete thematic proposition threaded through
    the story alongside the primary theme (``thesis_statement``).

    Drawn from the large genre-agnostic theme corpus (or LLM-derived per premise).
    ``motif_key`` / ``layer`` are organising tags (which deep-structure lens it
    expresses); ``woven_through`` is an optional note on where it surfaces.
    """

    proposition: str = Field(min_length=1)
    motif_key: str = ""
    layer: str = ""
    woven_through: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"proposition": value.strip()}
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("proposition")):
            data["proposition"] = _first_text(
                data, "theme", "statement", "text", "sub_theme", "子题", "thesis"
            )
        data.setdefault("motif_key", _first_text(data, "motif", "motif_key", "key"))
        layer = _normalize_layer(data.get("layer") or data.get("layer_key"))
        if layer is not None:
            data["layer"] = layer
        elif "layer" in data:
            data["layer"] = _text(data.get("layer"))
        data.setdefault("woven_through", _first_text(data, "woven_through", "where", "placement"))
        return data


class IdeologyKernel(BaseModel, frozen=True):
    """The book's structured core ideology — its thematic soul.

    Generated after BookSpec, fed INTO StoryDesignKernel so worldview/plot/beats
    derive from this spine. Carried as ``StoryDesignKernel.ideology_kernel`` and
    propagated to every downstream planner prompt.

    ``thesis_statement`` is the ONE primary theme (主主题); ``sub_themes`` are the
    woven sub-themes (子题). Both are drawn from a large, genre-DECOUPLED theme
    pool so same-genre books differ — never bound to genre.
    """

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    cosmic_premise: str = Field(min_length=1)
    thesis_statement: str = Field(min_length=1)
    core_question: str = Field(min_length=1)
    sub_themes: list[SubTheme] = Field(default_factory=list)
    primary_motif: MotifBinding
    secondary_motifs: list[MotifBinding] = Field(default_factory=list)
    hidden_endgame_motif: MotifBinding | None = None
    belief_arc: BeliefArc
    cost_system: list[CostLaw] = Field(min_length=1)
    # 纯正爽文·代价强度档：standard(现状)|external(代价外置,主角不自损)|
    # minimal(极简代价,服务爽感)。render 据此选代价系统块变体。默认 standard。
    cost_style: str = "standard"
    layer_coverage: dict[str, str] = Field(default_factory=dict)
    motif_to_world_bindings: list[str] = Field(default_factory=list)
    per_volume_thesis_pressure: list[str] = Field(default_factory=list)
    forbidden_resolutions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        if not _text(data.get("cosmic_premise")):
            data["cosmic_premise"] = (
                _first_text(data, "premise", "cosmic_premise", "world_premise", "宇宙前提")
                or "这个世界不会因为主角善良就自动奖励他。"
            )
        if not _text(data.get("thesis_statement")):
            data["thesis_statement"] = _first_text(
                data, "thesis", "theme_statement", "statement", "主题宣言", "theme"
            )
        if not _text(data.get("core_question")):
            data["core_question"] = _first_text(
                data, "dramatic_question", "central_question", "question", "核心问题"
            )
        data["motif_to_world_bindings"] = _text_list(
            data.get("motif_to_world_bindings")
            or data.get("world_bindings")
            or data.get("worldview_bindings")
        )
        data["per_volume_thesis_pressure"] = _text_list(
            data.get("per_volume_thesis_pressure")
            or data.get("volume_thesis_pressure")
            or data.get("thesis_pressure")
        )
        data["forbidden_resolutions"] = _text_list(
            data.get("forbidden_resolutions")
            or data.get("forbidden_endings")
            or data.get("cheap_resolutions")
        )
        # Sub-themes: accept list of strings or dicts; drop unsalvageable entries.
        raw_subs = data.get("sub_themes") or data.get("subthemes") or data.get("子题")
        if isinstance(raw_subs, (list, tuple)):
            kept_subs: list[Any] = []
            for entry in raw_subs:
                try:
                    SubTheme.model_validate(entry)
                except Exception:
                    continue
                kept_subs.append(entry)
            data["sub_themes"] = kept_subs
        elif isinstance(raw_subs, str) and raw_subs.strip():
            data["sub_themes"] = [{"proposition": raw_subs.strip()}]
        # Drop unsalvageable secondary/cost entries instead of failing the book.
        raw_secondary = data.get("secondary_motifs")
        if isinstance(raw_secondary, list):
            kept: list[Any] = []
            for entry in raw_secondary:
                try:
                    MotifBinding.model_validate(entry)
                except Exception:
                    continue
                kept.append(entry)
            data["secondary_motifs"] = kept
        raw_costs = data.get("cost_system")
        if isinstance(raw_costs, list):
            kept_costs: list[Any] = []
            for entry in raw_costs:
                try:
                    CostLaw.model_validate(entry)
                except Exception:
                    continue
                kept_costs.append(entry)
            if kept_costs:
                data["cost_system"] = kept_costs
        # A cost system must never be empty — synthesize a minimal law.
        if not data.get("cost_system"):
            data["cost_system"] = [
                {
                    "acquires": "力量或真相",
                    "costs": "关系、记忆、寿命或身份之一",
                    "delayed": True,
                }
            ]
        if not isinstance(data.get("layer_coverage"), dict):
            data["layer_coverage"] = {}
        return data

    @model_validator(mode="after")
    def _backfill_layer_coverage(self) -> IdeologyKernel:
        """Layer coverage is informational; backfill from the bound motifs so the
        gate can audit completeness without trusting the LLM to fill the map."""

        if self.layer_coverage:
            return self
        coverage: dict[str, str] = {}
        for binding in (
            self.primary_motif,
            *self.secondary_motifs,
            *( [self.hidden_endgame_motif] if self.hidden_endgame_motif else [] ),
        ):
            coverage.setdefault(binding.layer, binding.display_name)
        return self.model_copy(update={"layer_coverage": coverage})

    # -- convenience -------------------------------------------------------

    def all_motifs(self) -> tuple[MotifBinding, ...]:
        out = [self.primary_motif, *self.secondary_motifs]
        if self.hidden_endgame_motif is not None:
            out.append(self.hidden_endgame_motif)
        return tuple(out)

    def covered_layers(self) -> frozenset[str]:
        return frozenset(binding.layer for binding in self.all_motifs())

    def secondary_roles(self) -> frozenset[str]:
        return frozenset(b.role for b in self.secondary_motifs if b.role)


def ideology_kernel_from_dict(data: dict[str, Any]) -> IdeologyKernel:
    """Validate and hydrate a kernel from persisted or LLM-produced data."""

    return IdeologyKernel.model_validate(data)


def ideology_kernel_to_dict(kernel: IdeologyKernel) -> dict[str, Any]:
    """Serialize a kernel using JSON-compatible values."""

    return kernel.model_dump(mode="json")


def render_ideology_compact_block(
    kernel: IdeologyKernel | dict[str, Any] | None,
    *,
    max_subs: int = 3,
) -> str:
    """A short ideology summary for token-tight prompts (compact 细纲 mode).

    Keeps the load-bearing spine — 主主题 / 核心问题 / 信念弧(碎→立) / 一条代价 /
    一条禁用解法 — so even compact downstream prompts still reference the ideology.
    """

    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        try:
            kernel = ideology_kernel_from_dict(kernel)
        except Exception:
            return ""
    parts = [
        "【核心理念(必须贯彻)】",
        f"主主题：{kernel.thesis_statement}",
        f"核心问题：{kernel.core_question}",
        f"信念弧：碎「{kernel.belief_arc.midpoint_shatter}」→ 立「{kernel.belief_arc.final_reconstruction}」",
    ]
    if kernel.sub_themes:
        parts.append("子题：" + "；".join(t.proposition for t in kernel.sub_themes[:max_subs]))
    if kernel.cost_system:
        law = kernel.cost_system[0]
        _cs = getattr(kernel, "cost_style", "standard") or "standard"
        if _cs == "external":
            parts.append(
                f"代价：获得「{law.acquires}」→ 外部代价（树敌/暴露/消耗，主角不自损）"
            )
        elif _cs == "minimal":
            parts.append(f"代价：获得「{law.acquires}」→ 从简（服务爽感，不阻碍）")
        else:
            parts.append(f"代价：获得「{law.acquires}」必付「{law.costs}」")
    if kernel.forbidden_resolutions:
        parts.append(f"禁用解法：{kernel.forbidden_resolutions[0]}")
    return "\n".join(parts)


def _render_cost_system_block(kernel: IdeologyKernel) -> list[str]:
    """代价系统块——纯正爽文三档变体。standard 与旧渲染逐字节一致。"""

    cost_style = getattr(kernel, "cost_style", "standard") or "standard"

    if cost_style == "external":
        lines = [
            "### 代价系统（外置代价——主角一路爽，代价由世界承担，不自损）",
            "- 硬规则：一切代价外置——树敌、暴露行踪、招来强敌、消耗外部资源、"
            "错失机会、连累局势；由对手/环境/时局付账。",
            "- 禁止：削减主角的记忆/身体/关系/寿命/地位；金手指不得给主角制造"
            "自损后果或永久残缺。",
        ]
        lines.extend(
            f"- 获得「{law.acquires}」→ 外部代价（世界找主角麻烦，主角不掉血）"
            for law in kernel.cost_system[:6]
        )
        return lines

    if cost_style == "minimal":
        lines = [
            "### 代价系统（极简代价——服务爽感与节奏，点到为止，不阻碍主角）",
            "- 以机会成本/时间/树敌为主，轻描淡写；不写削弱主角的代价账，不让代价"
            "打断爽点兑现。",
        ]
        lines.extend(
            f"- 获得「{law.acquires}」→ 顺势推进（代价从简，不喧宾夺主）"
            for law in kernel.cost_system[:6]
        )
        return lines

    # standard —— 与旧渲染逐字节一致
    lines = ["### 代价系统（力量/真相/救赎都必须付费, 不可白给）"]
    for law in kernel.cost_system[:6]:
        flags = []
        if law.delayed:
            flags.append("延迟支付")
        if law.irreversible:
            flags.append("不可逆")
        flag_text = f"（{', '.join(flags)}）" if flags else ""
        lines.append(f"- 获得「{law.acquires}」→ 代价「{law.costs}」{flag_text}")
    return lines


def render_ideology_kernel_prompt_block(
    kernel: IdeologyKernel | dict[str, Any] | None,
    *,
    max_volume_pressure: int = 8,
) -> str:
    """Render the ideology kernel as a compact prompt block for downstream planners.

    Pure string assembly (no IO) so StoryDesignKernel / volume / chapter prompts
    can embed the thematic spine cheaply.
    """

    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        try:
            kernel = ideology_kernel_from_dict(kernel)
        except Exception:
            return ""

    def _motif_line(binding: MotifBinding, *, prefix: str) -> str:
        extra = ""
        if binding.role:
            extra += f"（{'管行动' if binding.role == 'action' else '管悬念'}）"
        if binding.reveal_after_volume:
            extra += f"（第{binding.reveal_after_volume}卷后揭示）"
        symbols = (
            f"；可视化符号：{', '.join(binding.concrete_symbols[:4])}"
            if binding.concrete_symbols
            else ""
        )
        return (
            f"- {prefix}：{binding.display_name}{extra} [{LAYER_DISPLAY.get(binding.layer, binding.layer)}]\n"
            f"  · 本书主题陈述：{binding.book_thesis}\n"
            f"  · 本书核心问题：{binding.book_core_question}{symbols}"
        )

    lines: list[str] = [
        "## 核心理念内核 (Ideology Kernel) — 全书的思想脊柱, 世界观/卷纲/章纲都必须服务它",
        f"- 宇宙前提：{kernel.cosmic_premise}",
        f"- 主主题（贯穿全书）：{kernel.thesis_statement}",
        f"- 核心问题（贯穿全书的戏剧问题）：{kernel.core_question}",
    ]
    if kernel.sub_themes:
        lines.append("### 穿插子题（与主主题交织, 在不同卷/线上各自承载）")
        for st in kernel.sub_themes[:8]:
            where = f"（{st.woven_through}）" if st.woven_through else ""
            lines.append(f"- 「{st.proposition}」{where}")
    lines.append("### 结构母题脊柱（深层结构 — 用来把主题戏剧化, 不是题材标签）")
    lines.append(_motif_line(kernel.primary_motif, prefix="宇宙母题"))
    for binding in kernel.secondary_motifs:
        lines.append(_motif_line(binding, prefix="副母题"))
    if kernel.hidden_endgame_motif is not None:
        lines.append(_motif_line(kernel.hidden_endgame_motif, prefix="隐藏终局母题"))

    lines.extend(
        [
            "### 信念弧（主角最初相信什么 → 中段打碎什么 → 结尾重建什么）",
            f"- 最初相信：{kernel.belief_arc.initial_belief}",
            f"- 中段打碎：{kernel.belief_arc.midpoint_shatter}",
            f"- 结尾重建：{kernel.belief_arc.final_reconstruction}",
        ]
    )
    lines.extend(_render_cost_system_block(kernel))

    if kernel.motif_to_world_bindings:
        lines.append("### 母题→世界观约束（用母题长出 invariant, 不要先画地图）")
        lines.extend(f"- {item}" for item in kernel.motif_to_world_bindings[:6])
    if kernel.per_volume_thesis_pressure:
        lines.append("### 每卷主题加压（主题不平均用力, 逐卷推进 belief_arc）")
        lines.extend(
            f"- {item}" for item in kernel.per_volume_thesis_pressure[:max_volume_pressure]
        )
    if kernel.forbidden_resolutions:
        lines.append("### 禁用的廉价解法（违反主题宣言即砸全书后劲）")
        lines.extend(f"- {item}" for item in kernel.forbidden_resolutions[:6])

    return "\n".join(lines)


__all__ = [
    "LAYER_DISPLAY",
    "LAYER_KEYS",
    "BeliefArc",
    "CostLaw",
    "IdeologyKernel",
    "MotifBinding",
    "MotifLayer",
    "SecondaryRole",
    "SubTheme",
    "ideology_kernel_from_dict",
    "ideology_kernel_to_dict",
    "render_ideology_compact_block",
    "render_ideology_kernel_prompt_block",
]
