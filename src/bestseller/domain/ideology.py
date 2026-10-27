"""Optional source-bound ideology fields, with read compatibility for old full kernels.

The four-layer motif corpus is a recommendation resource, not a universal book
contract. New kernels may be empty; hydration never invents missing cosmology,
endings, belief transitions or prices. Final renderers use explicit current
methodology provenance independently of the author's cost choice.
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
    book_core_question: str = ""
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
                or ""
            )
        if not _text(data.get("book_core_question")):
            data["book_core_question"] = (
                _first_text(data, "core_question", "question", "dramatic_question")
                or ""
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

    initial_belief: str = ""
    midpoint_shatter: str = ""
    final_reconstruction: str = ""

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
                or ""
            )
        if not _text(data.get("costs")):
            data["costs"] = (
                _first_text(data, "cost", "price", "pays", "代价", "tradeoff")
                or ""
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

    version: int = 2
    cosmic_premise: str = ""
    thesis_statement: str = ""
    core_question: str = ""
    sub_themes: list[SubTheme] = Field(default_factory=list)
    primary_motif: MotifBinding | None = None
    secondary_motifs: list[MotifBinding] = Field(default_factory=list)
    hidden_endgame_motif: MotifBinding | None = None
    belief_arc: BeliefArc | None = None
    cost_system: list[CostLaw] = Field(default_factory=list)
    # Retained cache label only. Current author cost authority is resolved
    # independently; missing provenance never defaults to standard.
    cost_style: str = ""
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
                or ""
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
        # Empty means unselected; hydration must not invent a story mechanic.
        if not data.get("cost_system"):
            data["cost_system"] = []
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
            if binding is not None:
                coverage.setdefault(binding.layer, binding.display_name)
        return self.model_copy(update={"layer_coverage": coverage})

    # -- convenience -------------------------------------------------------

    def all_motifs(self) -> tuple[MotifBinding, ...]:
        out = [*([self.primary_motif] if self.primary_motif else []), *self.secondary_motifs]
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


def _render_effective_ideology(kernel: Any, metadata: dict[str, Any] | None) -> str:
    import json

    from bestseller.services.ideology_methodology import resolve_ideology_methodology_source
    from bestseller.services.story_enhancers import resolve_cost_style

    receipt = resolve_ideology_methodology_source(kernel, metadata)
    lines: list[str] = []
    if receipt["effective_fields"] or receipt["required_layers"] or receipt["required_secondary_roles"]:
        lines.append("【作者明确选择的理念条款：仅约束明示范围】")
        if receipt["effective_fields"]:
            lines.append(json.dumps(receipt["effective_fields"], ensure_ascii=False, sort_keys=True))
        if receipt["required_layers"]:
            lines.append("作者选择的结构层：" + "、".join(receipt["required_layers"]))
        if receipt["required_secondary_roles"]:
            lines.append("作者选择的副母题职责：" + "、".join(receipt["required_secondary_roles"]))
    style = resolve_cost_style(metadata)
    if style == "standard":
        lines.append("作者代价方向：标准代价，按当前故事因果呈现可见后果；这不授权任何主题或结局禁则。")
    elif style == "external":
        lines.append("作者代价方向：外置代价，由世界、对手或资源承担，主角不自损；不指定主题或故事结构。")
    elif style == "minimal":
        lines.append("作者代价方向：极简代价，能力使用不强制削弱主角；不指定主题或故事结构。")
    return "\n".join(lines)


def render_ideology_compact_block(
    kernel: IdeologyKernel | dict[str, Any] | None, *, max_subs: int = 3,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Render only current explicit clauses, retaining exact text even in compact mode."""
    return _render_effective_ideology(kernel, metadata) if kernel is not None else ""


def render_ideology_kernel_prompt_block(
    kernel: IdeologyKernel | dict[str, Any] | None, *, max_volume_pressure: int = 8,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Old cached proposals cannot become mandatory methodology at the final boundary."""
    return _render_effective_ideology(kernel, metadata) if kernel is not None else ""


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
