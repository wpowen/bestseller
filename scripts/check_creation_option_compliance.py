#!/usr/bin/env python
"""Does the generated book actually obey the create-form options?

Written after a book created with 爽文无代价 (``cost_style: minimal``) built a
cost ledger as its core mechanic and wrote "本作主角必须有代价" into its own
writing profile. The switch was captured correctly at every plumbing hop, so
plumbing checks proved nothing — the violation was in the *content*.

This checks the content instead: it reads the artefacts the writer prompt is
actually assembled from (tags, trope keywords, writing profile, ideology
kernel) and reports whether they contradict the options the user chose.

Read-only. No LLM calls.

    python scripts/check_creation_option_compliance.py --slug <slug>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from bestseller.infra.db.models import ProjectModel  # noqa: E402
from bestseller.infra.db.session import (  # noqa: E402
    create_engine,
    create_session_factory,
)
from bestseller.settings import load_settings  # noqa: E402

# Phrases that mean "the protagonist is billed for their own power". Deliberately
# narrow: a world that is merely dangerous is allowed under 无代价, so generic
# words like 危险/风险/敌人 are NOT violations.
_SELF_BILLED_COST_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"代价型(金手指|能力|系统)", "把金手指定义为代价型"),
    (r"(双向|等价|对等)代价", "双向/等价代价机制"),
    (r"越(用|偷|强)越(亏|弱|脆|虚)", "越用越亏的账本"),
    (r"必须有代价", "把「必须有代价」写成规则"),
    (r"(反噬|自损|折寿|损寿|减寿|燃血|耗命)", "自损类代价"),
    (r"(代价|收费|抽成|收税)机制", "代价被做成机制"),
    (r"每(章|次).{0,8}(算账|付出|代价)", "逐章结算的代价账"),
)

#: Cost words that are fine on their own — the world may charge the world.
_ALLOWED_CONTEXT = ("对手", "敌人", "世界", "局势", "树敌", "暴露")

#: Negation immediately before a cost word inverts its meaning. Without this the
#: checker flagged 「借他人一息灵机**而不自损**」 — a phrase that states exactly
#: the compliance it was accused of violating. A checker that cries wolf on
#: correct text gets ignored, which is worse than not having it.
_NEGATORS = ("不", "无", "非", "免", "毋须", "无需", "不必", "没有")


@dataclass
class Violation:
    where: str
    pattern_label: str
    excerpt: str

    def render(self) -> str:
        return f"  [{self.where}] {self.pattern_label} → 「{self.excerpt}」"


@dataclass
class ComplianceReport:
    slug: str
    cost_style: str = "standard"
    effect_skills: tuple[str, ...] = ()
    violations: list[Violation] = field(default_factory=list)
    checked_fields: list[str] = field(default_factory=list)
    project_exists: bool = True

    @property
    def passed(self) -> bool:
        return not self.violations

    def render(self) -> str:
        if not self.project_exists:
            return f"{self.slug}: 项目行尚未入库（构思阶段），无可检查内容。"
        lines = [
            f"书籍: {self.slug}",
            f"cost_style: {self.cost_style}",
            f"effect_skills: {', '.join(self.effect_skills) or '(无)'}",
            f"已检查字段: {', '.join(self.checked_fields) or '(无)'}",
            "",
        ]
        if self.cost_style not in ("minimal", "external"):
            lines.append("cost_style 非无代价档，跳过代价合规判定。")
            return "\n".join(lines)
        if self.passed:
            lines.append("✅ 未发现与「无代价」设定冲突的内容。")
        else:
            lines.append(f"❌ 发现 {len(self.violations)} 处冲突：")
            lines.extend(v.render() for v in self.violations)
        return "\n".join(lines)


def _walk_strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        if value.strip():
            yield path, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk_strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{path}[{index}]")


#: Paths that are pure word lists — a cost word there is the book promising not
#: to use it, the opposite of a violation. Deliberately narrow: prose fields
#: like ``forbidden`` carry sentences, and 「系统流无成本金手指（本作主角必须有
#: 代价）」 sitting in one is a genuine breach dressed as a prohibition.
_PROHIBITION_PATHS = ("taboo_word", "taboo_topic", "禁词", "禁区")


def _scan(where: str, payload: Any) -> list[Violation]:
    found: list[Violation] = []
    for path, text in _walk_strings(payload):
        if any(marker in path for marker in _PROHIBITION_PATHS):
            continue
        for pattern, label in _SELF_BILLED_COST_PATTERNS:
            match = re.search(pattern, text)
            if not match:
                continue
            start = max(0, match.start() - 16)
            excerpt = text[start : match.end() + 24].replace("\n", " ")
            # Negated forms assert compliance, not violation. The negator is not
            # always adjacent — 「不会反噬」 puts a modal in between — so scan a
            # short window rather than only the character immediately before.
            prefix = text[max(0, match.start() - 6) : match.start()]
            if any(neg in prefix for neg in _NEGATORS):
                continue
            # A cost the WORLD pays is allowed even under minimal.
            window = text[max(0, match.start() - 30) : match.end() + 30]
            if any(token in window for token in _ALLOWED_CONTEXT):
                continue
            found.append(
                Violation(
                    where=f"{where}{('.' + path) if path else ''}",
                    pattern_label=label,
                    excerpt=excerpt,
                )
            )
    return found


async def _load_prose(session: Any, project_id: Any) -> list[tuple[int, str]]:
    """Current chapter prose, oldest first. Empty when nothing is written yet."""

    from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel

    rows = (
        await session.execute(
            select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
            .join(
                ChapterDraftVersionModel,
                ChapterDraftVersionModel.chapter_id == ChapterModel.id,
            )
            .where(
                ChapterModel.project_id == project_id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
            .order_by(ChapterModel.chapter_number)
        )
    ).all()
    return [(int(n), str(t or "")) for n, t in rows if str(t or "").strip()]


async def _check(slug: str, *, include_prose: bool = True) -> ComplianceReport:
    engine = create_engine(load_settings())
    factory = create_session_factory(engine=engine)
    try:
        async with factory() as session:
            project = (
                await session.execute(
                    select(ProjectModel).where(ProjectModel.slug == slug)
                )
            ).scalar_one_or_none()
            if project is None:
                return ComplianceReport(slug=slug, project_exists=False)

            metadata = dict(project.metadata_json or {})
            enhancers = metadata.get("story_enhancers")
            enhancers = enhancers if isinstance(enhancers, Mapping) else {}
            report = ComplianceReport(
                slug=slug,
                cost_style=str(enhancers.get("cost_style") or "standard"),
                effect_skills=tuple(enhancers.get("effect_skills") or ()),
            )

            # Only the artefacts that actually reach a generation prompt.
            for key in (
                "tags",
                "trope_keywords",
                "writing_profile",
                "ideology_kernel",
                "commercial_brief",
                "book_design_snapshot",
            ):
                if key in metadata:
                    report.checked_fields.append(key)
                    report.violations.extend(_scan(key, metadata[key]))

            # A compliant plan can still produce non-compliant prose, and prose
            # is what the reader gets. Scan it too when it exists.
            if include_prose:
                chapters = await _load_prose(session, project.id)
                # Record the scan itself, not only its hits: a clean chapter that
                # never appears in ``checked_fields`` is indistinguishable from a
                # chapter that was never read, and "nothing found" then silently
                # means "nothing looked at".
                if chapters:
                    report.checked_fields.append(f"正文×{len(chapters)}章")
                for number, text in chapters:
                    report.violations.extend(_scan(f"chapter[{number}]", {"prose": text}))
            return report
    finally:
        await engine.dispose()


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--json", dest="json_path")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="只查规划物料，跳过正文（正文尚未产出时更快）",
    )
    args = parser.parse_args()

    report = await _check(args.slug, include_prose=not args.plan_only)
    print(report.render())
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(
                {
                    "slug": report.slug,
                    "cost_style": report.cost_style,
                    "passed": report.passed,
                    "violations": [
                        {"where": v.where, "label": v.pattern_label, "excerpt": v.excerpt}
                        for v in report.violations
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
