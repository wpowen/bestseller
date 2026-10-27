"""Source-bound ideology checks and outline observations.

Only exact author methodology fields authorize structural or grounding checks.
Unknown legacy themes stay available in history, without becoming requirements.
Cost-language observation is separate from theme authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.domain.ideology import (
    IdeologyKernel,
    ideology_kernel_from_dict,
)
from bestseller.services.story_enhancers import resolve_cost_style


def _coerce_kernel(kernel: IdeologyKernel | dict[str, Any]) -> IdeologyKernel | None:
    if isinstance(kernel, IdeologyKernel):
        return kernel
    if isinstance(kernel, dict):
        try:
            return ideology_kernel_from_dict(kernel)
        except Exception:
            return None
    return None


def evaluate_ideology_kernel_coherence(
    kernel: IdeologyKernel | dict[str, Any],
    *,
    volumes: int = 1,
    total_chapters: int | None = None,
    required: bool = False,
    metadata: dict[str, Any] | None = None,
) -> GateVerdict:
    """Check only exact author fields and explicitly requested layer/role coverage."""
    from bestseller.services.ideology_methodology import resolve_ideology_methodology_source

    source = resolve_ideology_methodology_source(kernel, metadata)
    fields = source["effective_fields"]
    layers = source["required_layers"]
    roles = source["required_secondary_roles"]
    checks_total = len(fields) + len(layers) + len(roles)
    metrics = {
        "volumes": volumes,
        "total_chapters": total_chapters,
        "checks_total": checks_total,
        "checks_passed": 0,
        "methodology_source": source,
    }
    if not checks_total:
        return GateVerdict(
            gate_name="ideology_kernel_coherence", verdict="not_run", coverage=0.0,
            required=required, metrics=metrics,
            summary="No explicit author methodology requirements to evaluate.",
        )

    obj = _coerce_kernel(kernel)
    findings: list[GateFinding] = []
    if obj is None:
        findings.append(GateFinding(
            code="ideology_kernel_invalid", severity="critical",
            message="The supplied kernel cannot represent the authorized methodology.",
            repair_action="Repair the supplied kernel against the exact author contract.",
        ))
    actual = obj.model_dump(mode="json") if obj is not None else {}
    for name, expected in fields.items():
        if name not in actual or actual[name] in (None, "", [], {}):
            matches = name in actual and actual[name] == expected
            code = "ideology_author_field_missing"
        else:
            matches = _author_value_matches(actual[name], expected)
            code = "ideology_author_field_mismatch"
        if matches:
            metrics["checks_passed"] += 1
        else:
            findings.append(GateFinding(
                code=code, severity="critical", path=name,
                message=f"Supplied {name} does not fulfill its explicit author field.",
                repair_action=f"Restore the author-defined {name}; do not add other methodology.",
            ))
    covered = {motif.layer for motif in obj.all_motifs()} if obj is not None else set()
    secondary_roles = obj.secondary_roles() if obj is not None else set()
    for layer in layers:
        if layer in covered:
            metrics["checks_passed"] += 1
        else:
            findings.append(GateFinding(
                code="ideology_missing_layer", severity="critical", path="required_layers",
                message=f"Explicitly required layer is missing: {layer}",
                repair_action=f"Fulfill the author-required layer {layer}.",
            ))
    for role in roles:
        if role in secondary_roles:
            metrics["checks_passed"] += 1
        else:
            findings.append(GateFinding(
                code="ideology_secondary_roles_incomplete", severity="critical",
                path="required_secondary_roles",
                message=f"Explicitly required secondary role is missing: {role}",
                repair_action=f"Fulfill the author-required secondary role {role}.",
            ))
    metrics["covered_layers"] = len(covered)
    return GateVerdict(
        gate_name="ideology_kernel_coherence",
        verdict=("blocked" if required else "warn_only") if findings else "pass",
        coverage=metrics["checks_passed"] / checks_total,
        required=required, findings=tuple(findings), metrics=metrics,
        summary=f"Author methodology checks {metrics['checks_passed']}/{checks_total} fulfilled.",
    )


def _author_value_matches(actual: object, expected: object) -> bool:
    """Compare the author's exact subset without requiring unchosen fields."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _author_value_matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _author_value_matches(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


# ---------------------------------------------------------------------------
# Deterministic outline-grounding audit (soft prior for the LLM judge)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdeologyGroundingResult:
    """Deterministic read of how well an outline grounds the kernel.

    Densities/flags only — a *soft prior* for the LLM judge, never a gate. ``note``
    states the limitation honestly (semantic expression is left to the judge)."""

    symbol_hits: int
    symbol_total: int
    symbol_coverage: float
    thesis_keyword_hits: int
    shatter_staged: bool
    forbidden_violation_hits: tuple[str, ...]
    cost_language_present: bool
    flagged: tuple[str, ...]
    note: str = (
        "确定性仅覆盖: 母题可视化符号是否落在大纲、主题关键词回响、信念崩点是否排布、"
        "禁用解法字面命中、代价语言是否在场; 母题是否被真正戏剧化交 LLM 判官。"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_hits": self.symbol_hits,
            "symbol_total": self.symbol_total,
            "symbol_coverage": round(self.symbol_coverage, 3),
            "thesis_keyword_hits": self.thesis_keyword_hits,
            "shatter_staged": self.shatter_staged,
            "forbidden_violation_hits": list(self.forbidden_violation_hits),
            "cost_language_present": self.cost_language_present,
            "flagged": list(self.flagged),
            "note": self.note,
        }


_COST_MARKERS: tuple[str, ...] = (
    "代价", "付出", "折寿", "牺牲", "失去", "偿还", "账", "反噬", "换取", "成本", "price", "cost",
)


def _keywords(text: str, *, minlen: int = 2, maxlen: int = 6, limit: int = 8) -> list[str]:
    """Cheap content-word extraction from a thesis sentence (CJK n-gram-ish)."""

    cleaned = [c for c in text if "一" <= c <= "鿿"]
    s = "".join(cleaned)
    out: list[str] = []
    # sliding bigrams/trigrams as crude keywords
    for n in (3, 2):
        for i in range(0, max(0, len(s) - n + 1), n):
            frag = s[i : i + n]
            if minlen <= len(frag) <= maxlen and frag not in out:
                out.append(frag)
            if len(out) >= limit:
                return out
    return out


def audit_ideology_outline_grounding(
    kernel: IdeologyKernel | dict[str, Any],
    outline_text: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> IdeologyGroundingResult:
    """Deterministically read how well ``outline_text`` grounds the kernel."""

    from bestseller.services.ideology_methodology import resolve_ideology_methodology_source

    source = resolve_ideology_methodology_source(kernel, metadata)
    fields = source["effective_fields"]
    text = outline_text or ""
    symbols: list[str] = []
    motifs = [fields.get("primary_motif"), *(fields.get("secondary_motifs") or [])]
    hidden = fields.get("hidden_endgame_motif")
    if isinstance(hidden, dict):
        motifs.append(hidden)
    for motif in motifs:
        if isinstance(motif, dict):
            symbols.extend(motif.get("concrete_symbols") or [])
    symbols = [symbol for symbol in dict.fromkeys(symbols) if symbol]
    symbol_hits = sum(1 for symbol in symbols if symbol in text)
    symbol_total = len(symbols)
    symbol_coverage = symbol_hits / symbol_total if symbol_total else 0.0
    thesis_kw = _keywords(fields.get("thesis_statement") or "")
    thesis_hits = sum(1 for keyword in thesis_kw if keyword in text)
    belief = fields.get("belief_arc") or {}
    shatter_kw = _keywords(belief.get("midpoint_shatter") or "", limit=4)
    shatter_staged = any(keyword in text for keyword in shatter_kw)
    forbidden_hits = tuple(
        resolution[:24] for resolution in fields.get("forbidden_resolutions", [])
        for keyword in _keywords(resolution, limit=3) if keyword in text
    )

    cost_present = any(m in text for m in _COST_MARKERS)

    flagged: list[str] = []
    if symbol_total and symbol_coverage < 0.34:
        flagged.append("low_symbol_grounding")
    if thesis_kw and thesis_hits == 0:
        flagged.append("thesis_absent_from_outline")
    if shatter_kw and not shatter_staged:
        flagged.append("belief_shatter_unstaged")
    if not cost_present and resolve_cost_style(metadata) == "standard":
        flagged.append("no_cost_language")
    if forbidden_hits:
        flagged.append("possible_forbidden_resolution")

    return IdeologyGroundingResult(
        symbol_hits=symbol_hits,
        symbol_total=symbol_total,
        symbol_coverage=symbol_coverage,
        thesis_keyword_hits=thesis_hits,
        shatter_staged=shatter_staged,
        forbidden_violation_hits=tuple(dict.fromkeys(forbidden_hits)),
        cost_language_present=cost_present,
        flagged=tuple(flagged),
    )


__all__ = [
    "IdeologyGroundingResult",
    "audit_ideology_outline_grounding",
    "evaluate_ideology_kernel_coherence",
]
