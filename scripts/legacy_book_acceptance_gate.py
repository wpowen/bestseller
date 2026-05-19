"""Evaluate a historical book against the current whole-book acceptance bar."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
for item in (_SRC,):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from bestseller.infra.db.session import session_scope  # noqa: E402
from bestseller.services.legacy_book_acceptance_gate import (  # noqa: E402
    evaluate_legacy_book_acceptance,
)
from bestseller.services.book_quality_closure import (  # noqa: E402
    count_current_chapter_drafts,
    count_planned_chapters_without_current_draft,
)
from bestseller.services.premium_book_gate import (  # noqa: E402
    evaluate_premium_project_readiness,
    premium_book_gate_report_to_dict,
)
from bestseller.services.projects import get_project_by_slug  # noqa: E402
from bestseller.services.scorecard import compute_scorecard  # noqa: E402
from bestseller.settings import AppSettings, get_runtime_env_value, load_settings  # noqa: E402


def _model_execution_ready(settings: AppSettings) -> bool:
    keys = {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "LITELLM_API_KEY",
        "DEEPSEEK_API_KEY",
    }
    for role in (
        settings.llm.planner,
        settings.llm.writer,
        settings.llm.critic,
        settings.llm.summarizer,
        settings.llm.editor,
    ):
        if role.api_key_env:
            keys.add(role.api_key_env)
        if role.rate_limit_fallback_api_key_env:
            keys.add(role.rate_limit_fallback_api_key_env)
    return any(bool(get_runtime_env_value(key)) for key in keys)


def _load_repair_plan(package_dir: Path) -> dict[str, object]:
    path = package_dir / "audits" / "quality-retrofit" / "autonomous-repair-plan.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="DB-backed project/output slug.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    parser.add_argument(
        "--fail",
        action="store_true",
        help="Exit non-zero when the historical book is not accepted.",
    )
    return parser.parse_args()


async def _run(slug: str) -> dict[str, object]:
    settings = load_settings()
    package_dir = Path(settings.output.base_dir) / slug
    async with session_scope(settings) as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            raise ValueError(f"Project '{slug}' was not found.")
        scorecard = await compute_scorecard(
            session,
            project.id,
            expected_chapter_count=project.target_chapters,
        )
        premium_report = evaluate_premium_project_readiness(project)
        repair_plan = _load_repair_plan(package_dir)
        current_chapters = await count_current_chapter_drafts(session, project)
        draftless_chapters = await count_planned_chapters_without_current_draft(
            session,
            project,
        )
        scorecard_payload = {
            **scorecard.to_dict(),
            "current_chapters": current_chapters,
            "draftless_chapters": draftless_chapters,
        }
        acceptance = evaluate_legacy_book_acceptance(
            scorecard=scorecard_payload,
            premium_gate_report=premium_book_gate_report_to_dict(premium_report),
            repair_plan=repair_plan,
            model_execution_ready=_model_execution_ready(settings),
        )
        out_dir = package_dir / "audits" / "legacy-acceptance"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "slug": slug,
            "acceptance": acceptance.to_dict(),
            "scorecard": scorecard_payload,
            "premium_gate": premium_book_gate_report_to_dict(premium_report),
            "repair_plan": {
                "task_count": repair_plan.get("task_count", 0),
                "priority_counts": repair_plan.get("priority_counts", {}),
                "cause_counts": repair_plan.get("cause_counts", {}),
            },
            "current_chapters": current_chapters,
            "target_chapters": int(project.target_chapters or 0),
        }
        report_path = out_dir / "report.json"
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payload["report_path"] = str(report_path)
        return payload


def main() -> int:
    args = _parse_args()
    payload = asyncio.run(_run(args.slug))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        acceptance = payload["acceptance"]
        assert isinstance(acceptance, dict)
        status = "PASS" if acceptance.get("passed") else "FAIL"
        print(
            "{status} {slug} readiness={readiness} quality_score={score}".format(
                status=status,
                slug=args.slug,
                readiness=acceptance.get("readiness_level"),
                score=acceptance.get("metrics", {}).get("quality_score"),
            )
        )
        for finding in acceptance.get("findings", []):
            print(
                "- [{severity}] {code} {path}: {actual} (expected {expected})".format(
                    severity=finding.get("severity"),
                    code=finding.get("code"),
                    path=finding.get("path"),
                    actual=finding.get("actual"),
                    expected=finding.get("expected"),
                )
            )
    acceptance = payload["acceptance"]
    assert isinstance(acceptance, dict)
    return 1 if args.fail and not acceptance.get("passed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
