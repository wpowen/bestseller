"""方法论补丁提案：失败归因批次聚合 → 人审提案（榜单对标闭环 P4.1）。

attribution loop（``quality_attribution_loop``）每轮把读者面板反馈归因到
artifact 层并修复**那一章** —— 修章不修法，同一类失败在下一本书重演。
本模块把一个或多个跑书产出的归因报告（``audits/quality-attribution-loop/
attribution_report.jsonl``）按 (root_layer, missing-pattern) 聚合，当某类
根因跨章/跨书反复出现时生成「方法论补丁提案」。

提案是**给人审的**：写入 markdown + JSON，不自动改 config/阈值 —— 自动调参
没有外部地面真值时会放大判官偏置（见判官校准 P0.3 的教训）。
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ATTRIBUTION_REPORT_RELPATH = Path("audits/quality-attribution-loop/attribution_report.jsonl")

# 提案触发阈值：同一 (layer, pattern) 至少出现这么多次才值得立项
DEFAULT_MIN_OCCURRENCES = 3


@dataclass(frozen=True)
class MethodologyPatchProposal:
    """One human-reviewable proposal aggregated from recurring attributions."""

    proposal_id: str
    root_layer: str
    pattern: str
    occurrences: int
    books: tuple[str, ...]
    sample_issues: tuple[str, ...]
    sample_directives: tuple[str, ...]
    suggested_target: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "root_layer": self.root_layer,
            "pattern": self.pattern,
            "occurrences": self.occurrences,
            "books": list(self.books),
            "sample_issues": list(self.sample_issues),
            "sample_directives": list(self.sample_directives),
            "suggested_target": self.suggested_target,
            "status": "pending_review",
        }


@dataclass(frozen=True)
class ProposalBatch:
    proposals: tuple[MethodologyPatchProposal, ...]
    books_scanned: int
    records_scanned: int
    skipped_books: tuple[str, ...] = field(default_factory=tuple)


# root_layer → 建议落点（人审时的修改入口提示）
_LAYER_TARGETS = {
    "outline": "config/writing_methodology.yaml（OUTLINE_* 段）或 planner 章纲契约",
    "materialization": "material_library 派生链 / 场景卡生成 prompt",
    "scene_card": "scene contract 生成与校验（narrative_contracts）",
    "prose": "config/writing_methodology.yaml（PROSE_SCENE 段）/ quality_levers",
    "story_bible": "story_bible 生成与 ensure_* 自举",
}


def _normalize_pattern(missing: str) -> str:
    """Collapse a free-text ``missing`` description into a recurrence key.

    Strips book-specific tokens (names in quotes, chapter numbers, digits) so
    the same failure mode from different books lands in one bucket.
    """
    text = str(missing or "").strip()
    if not text:
        return "(unspecified)"
    text = re.sub(r"[「『\"《][^」』\"》]{1,30}[」』\"》]", "〈X〉", text)
    text = re.sub(r"第?\s*\d+\s*[章节卷]", "第N章", text)
    text = re.sub(r"\d+", "N", text)
    return text[:120]


def _iter_attribution_records(book_root: Path) -> list[dict[str, Any]]:
    report_path = book_root / ATTRIBUTION_REPORT_RELPATH
    if not report_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in report_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                records.append(row)
    except OSError as exc:
        logger.warning("attribution report unreadable (%s): %s", report_path, exc)
    return records


def aggregate_methodology_proposals(
    book_roots: list[Path],
    *,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
) -> ProposalBatch:
    """Aggregate attribution reports across books into patch proposals."""
    buckets: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    books_scanned = 0
    records_scanned = 0
    skipped: list[str] = []
    for book_root in book_roots:
        records = _iter_attribution_records(Path(book_root))
        if not records:
            skipped.append(Path(book_root).name)
            continue
        books_scanned += 1
        for record in records:
            records_scanned += 1
            layer = str(record.get("root_layer") or "unknown")
            pattern = _normalize_pattern(str(record.get("missing") or ""))
            buckets[(layer, pattern)].append((Path(book_root).name, record))

    proposals: list[MethodologyPatchProposal] = []
    for index, ((layer, pattern), rows) in enumerate(
        sorted(buckets.items(), key=lambda item: -len(item[1]))
    ):
        if len(rows) < min_occurrences:
            continue
        books = tuple(dict.fromkeys(book for book, _ in rows))
        proposals.append(
            MethodologyPatchProposal(
                proposal_id=f"mpp-{index + 1:03d}",
                root_layer=layer,
                pattern=pattern,
                occurrences=len(rows),
                books=books,
                sample_issues=tuple(
                    str(record.get("issue_id") or "") for _, record in rows[:5]
                ),
                sample_directives=tuple(
                    dict.fromkeys(
                        str(record.get("repair_directive") or "")[:160]
                        for _, record in rows[:5]
                        if record.get("repair_directive")
                    )
                ),
                suggested_target=_LAYER_TARGETS.get(layer, "（人工定位）"),
            )
        )
    return ProposalBatch(
        proposals=tuple(proposals),
        books_scanned=books_scanned,
        records_scanned=records_scanned,
        skipped_books=tuple(skipped),
    )


def render_proposals_markdown(batch: ProposalBatch) -> str:
    lines = ["# 方法论补丁提案（人审）", ""]
    lines.append(
        f"扫描 {batch.books_scanned} 本书 / {batch.records_scanned} 条归因记录；"
        f"产出 {len(batch.proposals)} 份提案。"
    )
    if batch.skipped_books:
        lines.append(f"无归因报告跳过：{'、'.join(batch.skipped_books)}")
    lines.append("")
    if not batch.proposals:
        lines.append("（没有达到复现阈值的系统性根因 — 无提案。）")
    for proposal in batch.proposals:
        lines += [
            f"## {proposal.proposal_id} — [{proposal.root_layer}] ×{proposal.occurrences}",
            "",
            f"- **复现模式**：{proposal.pattern}",
            f"- **涉及书目**：{'、'.join(proposal.books)}",
            f"- **建议修改入口**：{proposal.suggested_target}",
            f"- **修复指令样本**：",
        ]
        for directive in proposal.sample_directives:
            lines.append(f"  - {directive}")
        lines += [
            "- **处置**：[ ] 采纳并落 config/方法论  [ ] 误报  [ ] 已由其他改动覆盖",
            "",
        ]
    return "\n".join(lines)


def write_proposal_batch(batch: ProposalBatch, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "methodology_patch_proposals.json"
    md_path = out_dir / "methodology_patch_proposals.md"
    json_path.write_text(
        json.dumps(
            {
                "books_scanned": batch.books_scanned,
                "records_scanned": batch.records_scanned,
                "proposals": [proposal.to_dict() for proposal in batch.proposals],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    md_path.write_text(render_proposals_markdown(batch), encoding="utf-8")
    return md_path, json_path
