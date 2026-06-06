"""Deterministic structural gate + outline-grounding audit for the ideology kernel.

Two pure, model-independent passes:

* :func:`evaluate_ideology_kernel_coherence` — audits the IdeologyKernel's own
  structure (4 layers covered, both secondary roles present, hidden motif has a
  reveal slot, cost system non-trivial, anti-slogan guard present, no motif
  reuse). Returns a :class:`GateVerdict`.

  **Advisory by default** (``required=False``): like every theme-quality lever in
  this framework (and per the 2026-05/06 regression lesson), it REPORTS and feeds
  repair but never hard-aborts a book. Callers that want a hard prewrite block can
  pass ``required=True``.

* :func:`audit_ideology_outline_grounding` — a deterministic check of whether an
  *outline* actually grounds the kernel: are the primary motif's concrete symbols
  present, is the thesis echoed, is the belief-arc's shatter staged, is a
  forbidden resolution being used? This is a *soft prior* fed to the LLM judge
  (mirroring ``litstyle_prose.detect_ai_tone``) — it never gates anything.
"""

# ruff: noqa: RUF001, E501

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.domain.ideology import (
    LAYER_DISPLAY,
    LAYER_KEYS,
    IdeologyKernel,
    ideology_kernel_from_dict,
)


def _coerce_kernel(kernel: IdeologyKernel | dict[str, Any]) -> IdeologyKernel | None:
    if isinstance(kernel, IdeologyKernel):
        return kernel
    if isinstance(kernel, dict):
        try:
            return ideology_kernel_from_dict(kernel)
        except Exception:
            return None
    return None


# Generic placeholder theses the deterministic fallback emits — a real book must
# specialise them, so emitting one verbatim is a (soft) finding.
_GENERIC_THESIS_MARKERS: tuple[str, ...] = (
    "本书将该母题落到具体人物与代价上。",
    "本书围绕该母题向读者提出的核心追问。",
)


def evaluate_ideology_kernel_coherence(
    kernel: IdeologyKernel | dict[str, Any],
    *,
    volumes: int = 1,
    total_chapters: int | None = None,
    required: bool = False,
) -> GateVerdict:
    """Audit the structural coherence of an IdeologyKernel.

    ``required=False`` makes this advisory (``warn_only`` instead of ``blocked``)
    so it never hard-aborts planning; structural failures still surface as
    findings + a repair_action for the regen loop.
    """

    obj = _coerce_kernel(kernel)
    findings: list[GateFinding] = []
    checks_total = 9
    checks_passed = 0

    if obj is None:
        return GateVerdict(
            gate_name="ideology_kernel_coherence",
            verdict="blocked" if required else "warn_only",
            coverage=0.0,
            required=required,
            findings=(
                GateFinding(
                    code="ideology_kernel_invalid",
                    severity="critical",
                    message="ideology kernel payload failed validation",
                    repair_action="regenerate the ideology kernel (derive_ideology_kernel)",
                ),
            ),
            metrics={"volumes": volumes},
        )

    # 1) All four structural layers covered. The cosmic-order layer is ALSO
    # credited by a substantive ``cosmic_premise`` (the world-premise text IS the
    # cosmic-order content) — so a valid non-cosmic-primary spine (e.g. a 真相- or
    # 神佛皆伪-led book) is not falsely flagged when it states its world premise.
    covered = set(obj.covered_layers())
    premise = obj.cosmic_premise.strip()
    _generic_premise = "这个世界不会因为主角善良就自动奖励他。"
    if "cosmic_order" not in covered and len(premise) >= 8 and premise != _generic_premise:
        covered.add("cosmic_order")
    missing_layers = [k for k in LAYER_KEYS if k not in covered]
    if missing_layers:
        findings.append(
            GateFinding(
                code="ideology_missing_layer",
                severity="critical",
                message="母题未覆盖全部四层：缺 "
                + "、".join(LAYER_DISPLAY.get(k, k) for k in missing_layers),
                repair_action="为缺失的层补一个母题(主/副/隐藏其一), 让全书有完整脊柱",
            )
        )
    else:
        checks_passed += 1

    # 2) Secondary motifs carry both action + suspense roles.
    roles = obj.secondary_roles()
    if roles != {"action", "suspense"}:
        findings.append(
            GateFinding(
                code="ideology_secondary_roles_incomplete",
                severity="high",
                message=f"副母题应一个管行动一个管悬念, 当前角色={sorted(roles) or '空'}",
                repair_action="设两个副母题: role=action(主体抉择层)+role=suspense(认知危机层)",
            )
        )
    else:
        checks_passed += 1

    # 3) Hidden endgame motif present (+ reveal slot for multi-volume books).
    hidden = obj.hidden_endgame_motif
    if hidden is None:
        findings.append(
            GateFinding(
                code="ideology_no_hidden_endgame",
                severity="high",
                message="缺隐藏终局母题, 全书缺少后劲与价值反转",
                repair_action="加一个伦理反转层母题做隐藏终局, 50-60%进度后揭示",
            )
        )
    elif volumes > 1 and not hidden.reveal_after_volume:
        findings.append(
            GateFinding(
                code="ideology_hidden_no_reveal_slot",
                severity="medium",
                message="隐藏终局母题未设 reveal_after_volume, 可能过早泄底",
                repair_action="为隐藏母题设 reveal_after_volume(约 60% 卷数)",
            )
        )
        checks_passed += 1  # hidden present; only the slot is soft
    else:
        checks_passed += 1

    # 4) Cost system non-trivial.
    if len(obj.cost_system) >= 2:
        checks_passed += 1
    else:
        findings.append(
            GateFinding(
                code="ideology_thin_cost_system",
                severity="medium",
                message=f"代价系统仅 {len(obj.cost_system)} 条, 力量/真相易变成白给",
                repair_action="补足代价法则(力量、真相、救赎各绑一条可见代价)",
            )
        )

    # 5) Anti-slogan guard present.
    if obj.forbidden_resolutions:
        checks_passed += 1
    else:
        findings.append(
            GateFinding(
                code="ideology_no_forbidden_resolutions",
                severity="medium",
                message="缺 forbidden_resolutions, 主题易被廉价反转(如天道最终奖励好人)砸掉",
                repair_action="列出会背叛主题宣言的禁用解法",
            )
        )

    # 6) World bindings present (motif → invariant bridge).
    if obj.motif_to_world_bindings:
        checks_passed += 1
    else:
        findings.append(
            GateFinding(
                code="ideology_no_world_bindings",
                severity="medium",
                message="缺 motif_to_world_bindings, 母题与世界观脱节(易沦为口号)",
                repair_action="写出母题如何长出 worldview invariant / 代价机制",
            )
        )

    # 7) Per-volume thesis pressure scales.
    want_pressure = min(3, max(1, volumes))
    if len(obj.per_volume_thesis_pressure) >= want_pressure:
        checks_passed += 1
    else:
        findings.append(
            GateFinding(
                code="ideology_thin_volume_pressure",
                severity="low",
                message=f"每卷主题加压仅 {len(obj.per_volume_thesis_pressure)} 条 (建议 ≥ {want_pressure})",
                repair_action="为每卷写一条主题如何逐步推进 belief_arc",
            )
        )

    # 8) No motif reuse across the four slots.
    keys = [b.motif_key for b in obj.all_motifs()]
    if len(set(keys)) == len(keys):
        checks_passed += 1
    else:
        findings.append(
            GateFinding(
                code="ideology_motif_reuse",
                severity="low",
                message=f"主/副/隐藏母题有重复: {keys}",
                repair_action="四个槽位用不同母题, 覆盖不同层与功能",
            )
        )

    # 9) Thesis is book-specific (not the generic placeholder).
    thesis = obj.thesis_statement.strip()
    primary_thesis = obj.primary_motif.book_thesis.strip()
    if thesis and not any(m in thesis for m in _GENERIC_THESIS_MARKERS) and not any(
        m in primary_thesis for m in _GENERIC_THESIS_MARKERS
    ):
        checks_passed += 1
    else:
        findings.append(
            GateFinding(
                code="ideology_generic_thesis",
                severity="medium",
                message="主题宣言仍是占位/模板文本, 未具体到本书",
                repair_action="把主题宣言改写成本书专属的一句话(指向具体人物与代价)",
            )
        )

    coverage = checks_passed / checks_total
    has_critical = any(f.severity == "critical" for f in findings)
    if required and has_critical:
        verdict = "blocked"
    elif findings:
        verdict = "warn_only"
    else:
        verdict = "pass"

    return GateVerdict(
        gate_name="ideology_kernel_coherence",
        verdict=verdict,
        coverage=coverage,
        required=required,
        findings=tuple(findings),
        metrics={
            "volumes": volumes,
            "total_chapters": total_chapters,
            "covered_layers": len(covered),
            "checks_passed": checks_passed,
            "checks_total": checks_total,
        },
        summary=(
            f"理念脊柱结构 {checks_passed}/{checks_total} 项达标"
            + (f"; 缺层: {[LAYER_DISPLAY.get(k, k) for k in missing_layers]}" if missing_layers else "")
        ),
    )


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
) -> IdeologyGroundingResult:
    """Deterministically read how well ``outline_text`` grounds the kernel."""

    obj = _coerce_kernel(kernel)
    text = outline_text or ""
    if obj is None:
        return IdeologyGroundingResult(
            symbol_hits=0, symbol_total=0, symbol_coverage=0.0,
            thesis_keyword_hits=0, shatter_staged=False,
            forbidden_violation_hits=(), cost_language_present=False,
            flagged=("ideology_kernel_invalid",),
        )

    symbols = list(obj.primary_motif.concrete_symbols)
    for b in obj.secondary_motifs:
        symbols.extend(b.concrete_symbols)
    symbols = [s for s in dict.fromkeys(symbols) if s]
    symbol_hits = sum(1 for s in symbols if s and s in text)
    symbol_total = len(symbols)
    symbol_coverage = (symbol_hits / symbol_total) if symbol_total else 0.0

    thesis_kw = _keywords(obj.thesis_statement)
    thesis_hits = sum(1 for kw in thesis_kw if kw in text)

    shatter_kw = _keywords(obj.belief_arc.midpoint_shatter, limit=4)
    shatter_staged = any(kw in text for kw in shatter_kw) if shatter_kw else False

    forbidden_hits = tuple(
        fr[:24] for fr in obj.forbidden_resolutions
        for kw in _keywords(fr, limit=3)
        if kw and kw in text
    )

    cost_present = any(m in text for m in _COST_MARKERS)

    flagged: list[str] = []
    if symbol_total and symbol_coverage < 0.34:
        flagged.append("low_symbol_grounding")
    if thesis_kw and thesis_hits == 0:
        flagged.append("thesis_absent_from_outline")
    if not shatter_staged:
        flagged.append("belief_shatter_unstaged")
    if not cost_present:
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
