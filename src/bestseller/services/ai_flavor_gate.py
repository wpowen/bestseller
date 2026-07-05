"""Chapter-level AI-flavor gate.

This gate runs *after* the chapter draft is finalized but *before* the
signing/export step. Its job:

1. Detect AI-flavor spans in the chapter (bilingual).
2. Apply *only* localized fixes at the marked positions (no rewriting
   of unrelated prose).
3. Re-detect once; if blocking spans remain, return a hard
   ``CheckerReport`` so the pipeline can route the chapter to the
   machine-repair state.

The gate is intentionally surgical — it never re-rolls the whole
chapter and never edits content outside the spans the detector pinned.
That keeps the existing rewrite-cascade off the critical path.

Public API
----------

* ``run_ai_flavor_gate`` — async wrapper used by ``pipelines.py``.
* ``AiFlavorGateOutcome`` — frozen value type the pipeline reacts to.

The pure detection/patching logic lives under
``bestseller.services.ai_flavor.*``; this module wires it to the
``CheckerReport`` schema, the audit-file writer, and the optional LLM
rewriter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bestseller.services.ai_flavor import (
    AiFlavorReport,
    AiFlavorSpan,
    PatchResult,
    apply_patches,
    detect,
)
from bestseller.services.ai_flavor.patcher import MicroRewriter, PatchEdit
from bestseller.services.checker_schema import CheckerIssue, CheckerReport


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config & outcomes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AiFlavorGateConfig:
    """Operator-facing knobs (sourced from ``quality_gates.yaml``).

    Field defaults match the recommended v1 posture: gate enabled,
    block at score 50/55, warn at 25/30, cluster threshold 3, LLM
    rewrite enabled with a per-chapter budget cap.
    """

    enabled: bool = True
    block_score_cn: int = 50
    block_score_en: int = 55
    warn_score_cn: int = 25
    warn_score_en: int = 30
    cluster_threshold: int = 3
    llm_rewrite_enabled: bool = True
    llm_budget_per_chapter: int = 8
    write_audit_file: bool = True
    audit_dir_relative: str = "audits"
    data_dir: str = "data/ai_flavor"
    # When the gate cannot bring the score below the block threshold,
    # the pipeline marks the chapter for machine repair (recommended)
    # rather than letting it slip through with a warning.
    block_on_residual: bool = True
    # On a block, run a whole-passage 去AI味 rewrite (deslop_revise) and
    # re-check before routing to machine repair. The span patcher can only
    # delete/swap words; this clears discourse-level flavor (info-narration,
    # 结论先行, 解释规则) the patcher can't touch.
    deslop_revise_enabled: bool = True


@dataclass(frozen=True)
class AiFlavorGateOutcome:
    """What the pipeline gets back. Frozen so the call site can stash it
    on the workflow run metadata without worrying about mutation."""

    enabled: bool
    language: str
    chapter_number: int
    before_score: float
    after_score: float
    patched_text: str | None
    edits: tuple[PatchEdit, ...] = ()
    report: CheckerReport | None = None
    # ``decision`` is the routing signal:
    #   "pass"            — no spans worth patching; original text stands.
    #   "patched"         — fixes applied, residual score below threshold.
    #   "block"           — fixes applied but score still above threshold;
    #                       pipeline must mark machine repair required.
    decision: str = "pass"
    metrics: dict[str, Any] = field(default_factory=dict)


# Discourse-level tells the span patcher CANNOT fix (it only deletes/swaps
# words): narrator rule-exposition, parallel gloss, conclusion-first. These
# are the user's top AI-flavor complaint ("平白叙述/解释规则") and must trigger
# the whole-passage deslop rewrite even when the total score stays under the
# block threshold — otherwise they ship silently (each is advisory/capped).
# These are the category strings the detector actually emits
# (data/ai_flavor/patterns_zh.json): info_narration = 旁白解释规则/术语/来历/路数 +
# 本该点破; negated_definition = 不是X而是Y / 与其…不如 (结论先行下定义);
# epiphany_announcement = 顿悟宣告; face_emotion_label = 脸色X贴标签;
# empty_reaction_shot = 双手分开无效镜头. All advisory (no static suggestion) → the
# span patcher leaves them untouched, so only the whole-passage deslop can clear them.
DESLOP_DISCOURSE_CATEGORIES = frozenset(
    {
        "info_narration",
        "negated_definition",
        "epiphany_announcement",
        "face_emotion_label",
        "empty_reaction_shot",
        # 篇章级车轱辘：同一意思反复换皮写 / 感觉词堆叠 — only a whole-passage
        # deslop can collapse it (sentence rules can't see chapter-wide repetition).
        "narrative_repetition",
        # 他没X 否定式克制扎堆 + 单句独段装腔：尤其紧张场景里这两样最招人厌，
        # 是 patcher 删不掉的句式/节奏腔，必须整段重写。
        "negative_action_filler",
        "staccato_saturation",
        # 翻译腔独特句式（对…进行/使…得到/评价式被字句/作为一个）：patcher 改不了
        # 句法，只有整段重写能还原成中文语序。真机 40 章 0 命中（2026-07-03 A/B），
        # 罕见即触发便宜；密度型（dash_density 等）故意不进触发集，防成本与误伤。
        "translationese",
        # 跨模态通感病句（响/声「湿得像」）：patcher 无静态替换、改不了语病，分数低
        # (advisory) 却是冷读者第一眼就卡住的硬伤，罕见即触发便宜。真机 ch1 首句。
        # 明喻密度型（simile_overrun）故意不进触发集，与 dash_density 同理防成本误伤。
        "synaesthesia_mismatch",
    }
)


def needs_deslop_revise(outcome: "AiFlavorGateOutcome") -> bool:
    """Whether the chapter should run the deslop rewrite.

    True when the score blocked OR the (post-patch) report still carries a
    discourse tell the patcher cannot touch. The latter is the fix for the
    POV blind spot: rule-exposition scores low (advisory) yet is exactly what
    deslop exists to rewrite.
    """

    if outcome.decision == "block":
        return True
    report = outcome.report
    if report is None:
        return False
    # gate adapts each span into a CheckerIssue with id == AI_FLAVOR_<CATEGORY>.
    deslop_ids = {f"AI_FLAVOR_{c.upper()}" for c in DESLOP_DISCOURSE_CATEGORIES}
    return any(getattr(i, "id", "") in deslop_ids for i in report.issues)


# ---------------------------------------------------------------------------
# CheckerReport adaptation.
# ---------------------------------------------------------------------------


def _block_threshold(cfg: AiFlavorGateConfig, language: str) -> int:
    return cfg.block_score_en if language == "en" else cfg.block_score_cn


def _warn_threshold(cfg: AiFlavorGateConfig, language: str) -> int:
    return cfg.warn_score_en if language == "en" else cfg.warn_score_cn


def _span_to_issue(span: AiFlavorSpan) -> CheckerIssue:
    """Adapt one span to the unified ``CheckerIssue`` shape."""

    severity_map = {"block": "high", "warn": "medium", "info": "low"}
    return CheckerIssue(
        id=f"AI_FLAVOR_{span.category.upper()}",
        type="ai_flavor",
        severity=severity_map.get(span.severity, "medium"),  # type: ignore[arg-type]
        location=f"chars {span.start}-{span.end}: '{span.matched_text}'",
        description=span.why or f"AI-flavor pattern: {span.matched_text}",
        suggestion=(
            f"建议替换为：{span.suggestions[0]!r}"
            if span.suggestions and span.suggestions[0]
            else "删除该句或重写句子"
        ),
        can_override=(span.severity != "block"),
    )


def _build_report(
    *,
    chapter_number: int,
    pre_report: AiFlavorReport,
    post_report: AiFlavorReport,
    patch: PatchResult | None,
    decision: str,
    block_score: int,
    warn_score: int,
) -> CheckerReport:
    """Combine pre/post detection into a single CheckerReport.

    Hard violations come from the *post-patch* report — we only block
    on residual issues the patcher could not resolve. Soft suggestions
    come from the pre-patch report (sans those already fixed) so the
    audit trail still surfaces what was changed.
    """

    issues = tuple(_span_to_issue(s) for s in post_report.spans)
    passed = decision in ("pass", "patched") and not post_report.block_spans
    overall = max(0, 100 - int(post_report.overall_score))
    summary_bits: list[str] = []
    summary_bits.append(
        f"AI flavor score {pre_report.overall_score:.1f} → {post_report.overall_score:.1f}"
    )
    if patch is not None:
        summary_bits.append(
            f"patched {patch.edits_count} span(s)"
            + (f" (skipped {len(patch.skipped)})" if patch.skipped else "")
        )
    if decision == "block":
        summary_bits.append(
            f"residual ≥ block threshold ({block_score}); requires machine repair"
        )

    return CheckerReport(
        agent="ai-flavor-gate",
        chapter=chapter_number,
        overall_score=overall,
        passed=passed,
        issues=issues,
        metrics={
            "before_score": pre_report.overall_score,
            "after_score": post_report.overall_score,
            "block_threshold": block_score,
            "warn_threshold": warn_score,
            "block_span_count": len(post_report.block_spans),
            "warn_span_count": len(post_report.warn_spans),
            "info_span_count": len(post_report.info_spans),
            "patched_count": patch.edits_count if patch else 0,
            "skipped_count": len(patch.skipped) if patch else 0,
            "decision": decision,
            "language": post_report.language,
        },
        summary="; ".join(summary_bits),
    )


# ---------------------------------------------------------------------------
# Audit file writer.
# ---------------------------------------------------------------------------


def _write_audit(
    *,
    project_output_dir: Path,
    audit_subdir: str,
    chapter_number: int,
    pre: AiFlavorReport,
    post: AiFlavorReport,
    patch: PatchResult,
    decision: str,
) -> Path | None:
    """Write a human-readable audit file. Returns the written path or
    ``None`` on failure (we never want audit I/O to crash the gate)."""

    try:
        audit_dir = project_output_dir / audit_subdir
        audit_dir.mkdir(parents=True, exist_ok=True)
        path = audit_dir / f"ai-flavor-ch{chapter_number}.md"
        lines: list[str] = []
        lines.append(f"# AI-flavor gate — chapter {chapter_number}")
        lines.append("")
        lines.append(f"- language: `{post.language}`")
        lines.append(
            f"- score: **{pre.overall_score:.1f} → {post.overall_score:.1f}**"
        )
        lines.append(f"- decision: **{decision}**")
        lines.append(
            f"- edits: {patch.edits_count} | skipped: {len(patch.skipped)}"
        )
        lines.append("")
        if patch.edits:
            lines.append("## Edits applied")
            lines.append("")
            for idx, edit in enumerate(patch.edits, 1):
                lines.append(
                    f"### {idx}. `{edit.strategy}` — `{edit.span.rule_id}`"
                )
                lines.append(f"- why: {edit.span.why}")
                lines.append(f"- span: chars {edit.span.start}-{edit.span.end}")
                lines.append("")
                lines.append("**before**")
                lines.append("```")
                lines.append(edit.before.strip())
                lines.append("```")
                lines.append("")
                lines.append("**after**")
                lines.append("```")
                lines.append(edit.after.strip() or "(deleted)")
                lines.append("```")
                lines.append("")
        if post.spans:
            lines.append("## Residual findings")
            lines.append("")
            for span in post.spans:
                lines.append(
                    f"- [{span.severity}] `{span.rule_id}` @ {span.start}-{span.end}: "
                    f"`{span.matched_text}` — {span.why}"
                )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    except OSError:
        logger.debug(
            "ai_flavor_gate: audit write failed for ch%d", chapter_number, exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def run_ai_flavor_gate(
    *,
    chapter_number: int,
    content_md: str,
    language: str | None,
    config: AiFlavorGateConfig,
    llm_rewriter: MicroRewriter | None = None,
    project_output_dir: Path | None = None,
) -> AiFlavorGateOutcome:
    """Run the gate on a finalized chapter draft.

    Synchronous on purpose — detection and patching are pure CPU/string
    work, and keeping the API non-async lets the gate be invoked from
    both async pipelines and CLI tools without a wrapper.
    """

    if not config.enabled:
        return AiFlavorGateOutcome(
            enabled=False,
            language=(language or "zh-CN"),
            chapter_number=chapter_number,
            before_score=0.0,
            after_score=0.0,
            patched_text=None,
            decision="pass",
        )

    data_dir = Path(config.data_dir)
    pre = detect(
        content_md,
        language=language,
        chapter_number=chapter_number,
        data_dir=data_dir,
    )
    block_threshold = _block_threshold(config, pre.language)
    warn_threshold = _warn_threshold(config, pre.language)

    # Nothing to do — fast path.
    if not pre.spans:
        return AiFlavorGateOutcome(
            enabled=True,
            language=pre.language,
            chapter_number=chapter_number,
            before_score=0.0,
            after_score=0.0,
            patched_text=None,
            decision="pass",
            report=_build_report(
                chapter_number=chapter_number,
                pre_report=pre,
                post_report=pre,
                patch=None,
                decision="pass",
                block_score=block_threshold,
                warn_score=warn_threshold,
            ),
            metrics={
                "before_score": 0.0,
                "after_score": 0.0,
                "block_threshold": block_threshold,
                "warn_threshold": warn_threshold,
            },
        )

    patch = apply_patches(
        content_md,
        pre.spans,
        language=pre.language,
        llm_rewriter=llm_rewriter if config.llm_rewrite_enabled else None,
        llm_budget=config.llm_budget_per_chapter,
    )

    post = detect(
        patch.patched_text,
        language=language,
        chapter_number=chapter_number,
        data_dir=data_dir,
    )

    # Decision tree: did we get below block threshold?
    residual_block = bool(post.block_spans) or post.overall_score >= block_threshold
    if residual_block and config.block_on_residual:
        decision = "block"
    elif patch.edits_count > 0:
        decision = "patched"
    else:
        # No edits applied (only skipped warn/info spans). Stays as "pass"
        # so the pipeline doesn't churn — the soft findings are still
        # recorded on the CheckerReport.
        decision = "pass"

    audit_path: Path | None = None
    if config.write_audit_file and project_output_dir is not None:
        audit_path = _write_audit(
            project_output_dir=project_output_dir,
            audit_subdir=config.audit_dir_relative,
            chapter_number=chapter_number,
            pre=pre,
            post=post,
            patch=patch,
            decision=decision,
        )

    report = _build_report(
        chapter_number=chapter_number,
        pre_report=pre,
        post_report=post,
        patch=patch,
        decision=decision,
        block_score=block_threshold,
        warn_score=warn_threshold,
    )

    return AiFlavorGateOutcome(
        enabled=True,
        language=pre.language,
        chapter_number=chapter_number,
        before_score=pre.overall_score,
        after_score=post.overall_score,
        patched_text=(patch.patched_text if patch.edits_count > 0 else None),
        edits=patch.edits,
        report=report,
        decision=decision,
        metrics={
            "before_score": pre.overall_score,
            "after_score": post.overall_score,
            "block_threshold": block_threshold,
            "warn_threshold": warn_threshold,
            "edits": patch.edits_count,
            "skipped": len(patch.skipped),
            "audit_path": str(audit_path) if audit_path else None,
        },
    )
