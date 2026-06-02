"""Title formula pool loaded from YAML.

Replaces the hard-coded ``_title_seeds`` table in ``concept_lab`` with a
YAML-driven pool. Each mechanism has a short, punchy 4-10 char Chinese
``title_core``; the formula pool produces 3-5 title variants per bundle.
All titles are clamped to 6-25 CJK characters per the Chinese web novel
黄金长度 (golden length).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

import yaml

DEFAULT_TITLE_FORMULAS_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "concept_title_formulas.yaml"
)


@dataclass(frozen=True)
class TitleFormula:
    """One title slot-substitution template."""

    id: str
    template: str

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "TitleFormula":
        return cls(
            id=str(payload.get("id") or "").strip(),
            template=str(payload.get("template") or "").strip(),
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Title formula config must be a mapping: {path}")
    return payload


@lru_cache(maxsize=8)
def load_title_formulas(
    path: str | Path = DEFAULT_TITLE_FORMULAS_PATH,
) -> tuple[TitleFormula, ...]:
    effective = Path(path)
    payload = _load_yaml(effective)
    raw = payload.get("formulas")
    if not isinstance(raw, list):
        raise ValueError(f"Title formula config missing 'formulas' list: {effective}")
    formulas = tuple(TitleFormula.from_mapping(item) for item in raw)
    ids = [item.id for item in formulas]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("Title formula ids must be non-empty and unique")
    for formula in formulas:
        if not formula.template:
            raise ValueError(f"Title formula {formula.id!r} has empty template")
    return formulas


@lru_cache(maxsize=8)
def load_title_cores(
    path: str | Path = DEFAULT_TITLE_FORMULAS_PATH,
) -> dict[str, str]:
    effective = Path(path)
    payload = _load_yaml(effective)
    raw = payload.get("mechanism_title_cores") or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"Title formula config missing 'mechanism_title_cores' mapping: {effective}"
        )
    return {str(key): str(value).strip() for key, value in raw.items() if str(value).strip()}


_CJK_PATTERN = re.compile(r"[一-鿿]")
_SLOT_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _cjk_length(text: str) -> int:
    return len(_CJK_PATTERN.findall(text))


def clamp_title_length(text: str, *, low: int = 6, high: int = 25) -> str:
    """Clamp the CJK length of a title to the golden range.

    If too long, truncate on the CJK char boundary. If too short, return as-is
    (the title seed may still be valid because CJK length is what matters).
    """
    cjk_count = _cjk_length(text)
    if low <= cjk_count <= high:
        return text
    if cjk_count > high:
        # Truncate by CJK chars only.
        out_chars: list[str] = []
        kept = 0
        for char in text:
            if _CJK_PATTERN.match(char):
                if kept >= high:
                    break
                out_chars.append(char)
                kept += 1
            else:
                out_chars.append(char)
        return "".join(out_chars).strip()
    return text


def render_title(
    formula: TitleFormula,
    *,
    title_core: str,
    genre_label: str,
    reward: str,
    cost: str,
    direction_title: str,
    hook_type: str,
    n: int = 7,
) -> str:
    """Substitute slots in a title formula template and clamp to golden length."""

    values = {
        "title_core": title_core,
        "genre_label": genre_label,
        "reward": reward,
        "cost": cost,
        "direction_title": direction_title,
        "hook_type": hook_type,
        "n": str(n),
    }
    text = formula.template
    for slot in _SLOT_PATTERN.findall(formula.template):
        text = text.replace("{" + slot + "}", values.get(slot, ""))
    return clamp_title_length(text.strip())


__all__ = [
    "DEFAULT_TITLE_FORMULAS_PATH",
    "TitleFormula",
    "clamp_title_length",
    "load_title_cores",
    "load_title_formulas",
    "render_title",
]
