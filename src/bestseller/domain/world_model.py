"""Schema for the per-book :class:`WorldModel` — the derived world's "constitution".

A world model is a *differential* artefact:

* ``axioms``    — the 1-3 atomic what-ifs the premise injects (the perturbation).
* ``baseline``  — the reality substrate the axioms are diffed against.
* ``world_laws``— per-dimension rules: how the baseline is forced to change, with
  a behavioural, machine-checkable ``enforcement`` assertion. These are the
  rules every chapter must obey.
* ``content_settings`` — concrete things (currency name, transport, food) each
  *traceable* to a law via ``derived_from_law``; never invented free-floating.
* ``fault_lines`` — the friction points where laws collide; the story stands on
  one of them.

Genre-neutral by construction: nothing genre-specific lives in the schema, only
in the values a book derives at run time. Mirrors ``domain/ideology.py``.
"""

# ruff: noqa: RUF001, ANN401

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Coercion helpers (mirror story_design_kernel.py's LLM-alias normalisation)
# ---------------------------------------------------------------------------


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "；".join(_text(v) for v in value if _text(v))
    return str(value).strip()


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _text(data.get(key))
        if text:
            return text
    return ""


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        # LLMs (e.g. MiniMax) sometimes emit a stringified list: "['a', 'b']".
        if text[0] in "[(" and text[-1] in "])":
            import ast

            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, (list, tuple)):
                    return [_text(item) for item in parsed if _text(item)]
            except (ValueError, SyntaxError):
                pass
        return [text]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = _text(item)
            if text:
                out.append(text)
        return out
    text = _text(value)
    return [text] if text else []


def _int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class WorldLaw(BaseModel, frozen=True):
    """One derived rule of the world: baseline → delta, with an enforceable check."""

    dimension: str = Field(min_length=1)
    baseline: str = ""
    delta: str = Field(min_length=1)
    order: int = 1
    derived_from: list[str] = Field(default_factory=list)
    enforcement: str = Field(min_length=1)
    story_use: str = ""
    specificity: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data.setdefault("dimension", _first_text(data, "key", "axis", "name", "id"))
        if not _text(data.get("delta")):
            data["delta"] = _first_text(
                data, "change", "consequence", "rule", "result", "effect", "description"
            )
        if not _text(data.get("baseline")):
            data["baseline"] = _first_text(
                data, "baseline_state", "before", "reality", "status_quo"
            )
        if not _text(data.get("enforcement")):
            data["enforcement"] = (
                _first_text(
                    data,
                    "assertion",
                    "constraint",
                    "check",
                    "rule_check",
                    "must",
                    "invariant",
                )
                or _text(data.get("delta"))
                or "正文须与本条世界规律保持一致。"
            )
        if not _text(data.get("delta")):
            data["delta"] = _text(data.get("enforcement"))
        data["derived_from"] = _text_list(
            data.get("derived_from")
            or data.get("axiom_refs")
            or data.get("from")
            or data.get("source_axioms")
        )
        data["order"] = max(1, _int(data.get("order"), 1))
        for key in ("dimension", "baseline", "story_use"):
            if key in data:
                data[key] = _text(data.get(key))
        try:
            data["specificity"] = float(data.get("specificity", 0.0) or 0.0)
        except (TypeError, ValueError):
            data["specificity"] = 0.0
        return data


class FaultLine(BaseModel, frozen=True):
    """A friction point where derived laws collide — a story seam."""

    name: str = Field(min_length=1)
    tension: str = Field(min_length=1)
    world_law_refs: list[str] = Field(default_factory=list)
    used_by_protagonist: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data.setdefault("name", _first_text(data, "title", "key", "id"))
        if not _text(data.get("tension")):
            data["tension"] = _first_text(
                data, "conflict", "friction", "description", "clash", "summary"
            )
        data["world_law_refs"] = _text_list(
            data.get("world_law_refs") or data.get("law_refs") or data.get("laws")
        )
        used = data.get("used_by_protagonist")
        data["used_by_protagonist"] = bool(used) if used is not None else False
        return data


class ContentSetting(BaseModel, frozen=True):
    """A concrete derived setting, traceable to the law it descends from."""

    name: str = ""
    dimension: str = ""
    value: str = Field(min_length=1)
    derived_from_law: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data.setdefault("name", _first_text(data, "title", "key", "id", "label"))
        if not _text(data.get("value")):
            data["value"] = _first_text(
                data, "description", "detail", "setting", "content", "summary"
            )
        # ``derived_from_law`` is a single str, but the LLM often emits a list
        # (e.g. ["value_and_currency"]) — coerce so it never fails validation.
        data["derived_from_law"] = _text(data.get("derived_from_law")) or _first_text(
            data, "law", "law_ref", "from_law", "derived_from", "source_law"
        )
        for key in ("name", "dimension", "value"):
            if key in data:
                data[key] = _text(data.get(key))
        return data


class WorldModel(BaseModel, frozen=True):
    """The book's derived world: axioms diffed against a baseline into laws."""

    version: int = 1
    axioms: list[str] = Field(default_factory=list)
    baseline: str = ""
    baseline_rationale: str = ""
    uniqueness_principle: str = ""
    world_laws: list[WorldLaw] = Field(default_factory=list)
    fault_lines: list[FaultLine] = Field(default_factory=list)
    content_settings: list[ContentSetting] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        data = _mapping(value)
        if not data:
            return value
        data["axioms"] = _text_list(
            data.get("axioms") or data.get("world_axioms") or data.get("premises")
        )
        if not _text(data.get("baseline")):
            data["baseline"] = _first_text(data, "baseline_key", "substrate", "reality_baseline")
        if not _text(data.get("uniqueness_principle")):
            data["uniqueness_principle"] = _first_text(
                data, "uniqueness", "principle", "core_principle"
            )
        for key in ("world_laws", "fault_lines", "content_settings"):
            raw = data.get(key)
            if raw is None:
                data[key] = []
            elif isinstance(raw, dict):
                data[key] = [raw]
        data["version"] = max(1, _int(data.get("version"), 1))
        return data

    def covered_dimensions(self) -> set[str]:
        return {law.dimension for law in self.world_laws if law.dimension}

    def mean_specificity(self) -> float:
        if not self.world_laws:
            return 0.0
        return round(sum(law.specificity for law in self.world_laws) / len(self.world_laws), 4)

    def laws_for_dimensions(self, dimensions: set[str]) -> list[WorldLaw]:
        return [law for law in self.world_laws if law.dimension in dimensions]


# ---------------------------------------------------------------------------
# (De)serialisation + prompt rendering
# ---------------------------------------------------------------------------


def world_model_from_dict(data: dict[str, Any]) -> WorldModel:
    """Validate a dict into a :class:`WorldModel` (raises on truly invalid)."""

    return WorldModel.model_validate(data)


def world_model_to_dict(model: WorldModel) -> dict[str, Any]:
    return model.model_dump(mode="python")


def render_world_model_prompt_block(model: WorldModel, *, max_laws: int = 14) -> str:
    """Render the world model as a compact prompt block for downstream stages."""

    lines = ["【世界模型(本书世界宪法,所有内容须遵守且从中生长)】"]
    if model.axioms:
        lines.append("· 公理:" + "；".join(model.axioms))
    if model.baseline:
        lines.append(f"· 基线底座:{model.baseline}")
    if model.uniqueness_principle:
        lines.append(f"· 独特性原则:{model.uniqueness_principle}")
    if model.world_laws:
        lines.append("· 世界规律(dimension｜delta｜enforcement):")
        for law in model.world_laws[:max_laws]:
            lines.append(f"  - [{law.dimension}] {law.delta}｜约束:{law.enforcement}")
    if model.fault_lines:
        lines.append("· 故事断层线:")
        for fl in model.fault_lines:
            mark = "★主角站位" if fl.used_by_protagonist else ""
            lines.append(f"  - {fl.name}:{fl.tension}{mark}")
    return "\n".join(lines)


def world_model_health_summary(model: WorldModel) -> dict[str, Any]:
    """Cheap observability snapshot (used by validation + gate reporting)."""

    return {
        "axiom_count": len(model.axioms),
        "baseline": model.baseline,
        "law_count": len(model.world_laws),
        "covered_dimensions": sorted(model.covered_dimensions()),
        "dimension_count": len(model.covered_dimensions()),
        "fault_line_count": len(model.fault_lines),
        "protagonist_fault_lines": [
            fl.name for fl in model.fault_lines if fl.used_by_protagonist
        ],
        "content_setting_count": len(model.content_settings),
        "mean_specificity": model.mean_specificity(),
        "laws_without_derivation": sum(1 for law in model.world_laws if not law.derived_from),
    }


__all__ = [
    "ContentSetting",
    "FaultLine",
    "WorldLaw",
    "WorldModel",
    "render_world_model_prompt_block",
    "world_model_from_dict",
    "world_model_health_summary",
    "world_model_to_dict",
]
