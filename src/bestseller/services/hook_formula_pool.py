"""One-liner formula pool loaded from YAML.

Replaces the previous hard-coded ``EXPRESSION_STYLES`` branches in
``anti_commonsense_hook._render_one_liner`` with a YAML-driven slot-substitution
pool. Selection is deterministic given
``(mechanism.formula_affinity, genre, variant_index)`` — no random sampling.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

import yaml

from bestseller.domain.anti_commonsense_hook import HookMechanism, HookSpec

DEFAULT_FORMULA_POOL_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "hook_one_liner_formulas.yaml"
)


@dataclass(frozen=True)
class HookFormula:
    """One slot-substitution template from the one-liner pool."""

    id: str
    template: str
    requires: tuple[str, ...] = ()
    genre_affinity: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> HookFormula:
        requires_raw = payload.get("requires") or ()
        affinity_raw = payload.get("genre_affinity") or ()
        return cls(
            id=str(payload.get("id") or "").strip(),
            template=str(payload.get("template") or "").strip(),
            requires=tuple(str(item).strip() for item in requires_raw if str(item).strip()),
            genre_affinity=tuple(
                str(item).strip() for item in affinity_raw if str(item).strip()
            ),
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Hook formula config must be a mapping: {path}")
    return payload


@lru_cache(maxsize=8)
def load_formulas(path: str | Path = DEFAULT_FORMULA_POOL_PATH) -> tuple[HookFormula, ...]:
    """Load and validate the one-liner formula pool."""

    effective = Path(path)
    payload = _load_yaml(effective)
    raw = payload.get("formulas")
    if not isinstance(raw, list):
        raise ValueError(f"Hook formula config missing 'formulas' list: {effective}")
    formulas = tuple(HookFormula.from_mapping(item) for item in raw)
    ids = [item.id for item in formulas]
    if not all(ids):
        raise ValueError("Hook formula ids must be non-empty")
    if len(ids) != len(set(ids)):
        raise ValueError("Hook formula ids must be unique")
    for formula in formulas:
        if not formula.template:
            raise ValueError(f"Hook formula {formula.id!r} has empty template")
    return formulas


def list_formulas() -> tuple[HookFormula, ...]:
    return load_formulas()


def get_formula(formula_id: str) -> HookFormula:
    target = str(formula_id or "").strip()
    for formula in load_formulas():
        if formula.id == target:
            return formula
    raise KeyError(f"Unknown hook formula: {formula_id}")


_SLOT_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _required_slots(template: str) -> tuple[str, ...]:
    return tuple(_SLOT_PATTERN.findall(template))


def _genre_overlaps(formula: HookFormula, genre: str) -> bool:
    if not formula.genre_affinity:
        return True
    if "全平台" in formula.genre_affinity:
        return True
    haystack = (genre or "").strip()
    if not haystack:
        return True
    for tag in formula.genre_affinity:
        if tag in haystack or haystack in tag:
            return True
    return False


def select_formula_for_mechanism(
    mechanism: HookMechanism,
    *,
    genre: str = "",
    variant_index: int = 0,
    formula_id: str | None = None,
) -> HookFormula:
    """Pick a formula deterministically. Caller may pin a specific id."""

    pool = load_formulas()
    if formula_id:
        return get_formula(formula_id)
    affinity = tuple(mechanism.formula_affinity or ())
    by_affinity = [item for item in pool if item.id in affinity]
    if by_affinity:
        genre_matched = [item for item in by_affinity if _genre_overlaps(item, genre)]
        candidates = genre_matched or by_affinity
    else:
        candidates = [item for item in pool if _genre_overlaps(item, genre)] or list(pool)
    if not candidates:
        return pool[0]
    return candidates[variant_index % len(candidates)]


def _slot_values(spec: HookSpec, *, mechanism_label: str | None = None) -> dict[str, str]:
    rewards = spec.rewards or ()
    costs = spec.costs or ()
    return {
        "role": spec.protagonist_role or "主角",
        "desire": spec.base_desire or "改变命运",
        "reversal": spec.reversal or "反常识路径",
        "reward": rewards[0] if rewards else "翻盘机会",
        "cost": costs[0] if costs else "可见代价",
        "misunderstanding": spec.misunderstanding or "外界误读",
        "mechanism_label": mechanism_label or spec.mechanism_key.replace("-", " "),
        "hook_type": spec.hook_type or "悬念",
        "opening": spec.opening_frame or "开局",
    }


def render_one_liner_for_spec(
    spec: HookSpec,
    *,
    formula_id: str | None = None,
    variant_index: int = 0,
    mechanism: HookMechanism | None = None,
    mechanism_label: str | None = None,
) -> str:
    """Render the one_liner for a HookSpec using the formula pool.

    This is the single public entry point used by both
    ``anti_commonsense_hook.build_hook_spec_from_mechanism`` and
    ``hook_strength_gate.repair_hook_spec_once``. Pass ``mechanism_label`` to
    display the Chinese label in slots like ``{mechanism_label}`` (e.g. 舔狗翻篇);
    if omitted, the spec's mechanism key is used (legacy behaviour).
    """

    if mechanism is None:
        try:
            from bestseller.services.anti_commonsense_mechanisms import get_mechanism

            mechanism = get_mechanism(spec.mechanism_key)
        except Exception:
            mechanism = SimpleNamespace(formula_affinity=())  # type: ignore[assignment]
    genre = spec.genre or ""
    formula = select_formula_for_mechanism(
        mechanism,
        genre=genre,
        variant_index=variant_index,
        formula_id=formula_id,
    )
    values = _slot_values(spec, mechanism_label=mechanism_label)
    text = formula.template
    for slot in _required_slots(formula.template):
        text = text.replace("{" + slot + "}", values.get(slot, ""))
    return text.strip()


__all__ = [
    "DEFAULT_FORMULA_POOL_PATH",
    "HookFormula",
    "get_formula",
    "list_formulas",
    "load_formulas",
    "render_one_liner_for_spec",
    "select_formula_for_mechanism",
]
