"""SELECT-only local evaluation. Committed outputs never contain prose excerpts.

Use the repository venv with Q3/src on PYTHONPATH. --discover prints metadata
only. --extra-project IDs chooses two distinct additional adopted projects.
Manuscript samples stay in a private local /tmp directory for human inspection.
"""

# ruff: noqa: RUF001 -- Local review form intentionally uses Chinese punctuation.

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from uuid import UUID

import asyncpg
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from bestseller.services.adopted_chapter_source import choose_usable_promoted_chapter_source
from bestseller.services.prose_procedural_density import AuditConfig, audit_prose
from bestseller.settings import load_settings

ROOT = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller")
OUT = Path(__file__).resolve().parent
PRIMARY = (
    UUID("fa14e681-2bd9-44f5-93d1-7f2ef9900d69"),
    UUID("d407ca85-9a9b-4564-a033-6bc529e0dd76"),
)
DISCOVER = """SELECT p.id,p.title,p.slug,count(*) AS promoted_chapters
FROM projects p JOIN chapter_draft_versions d ON p.id=d.project_id
WHERE d.promotion_state='promoted' AND length(d.content_md)>1000
GROUP BY p.id,p.title,p.slug ORDER BY p.id LIMIT 60"""
INVENTORY = """SELECT p.id,p.title,p.slug,d.promotion_state,count(*) AS drafts,
count(*) FILTER (WHERE d.is_current) AS current_drafts,
count(*) FILTER (WHERE c.status='complete') AS complete_chapters
FROM projects p JOIN chapter_draft_versions d ON p.id=d.project_id
JOIN chapters c ON c.id=d.chapter_id AND c.project_id=p.id
GROUP BY p.id,p.title,p.slug,d.promotion_state ORDER BY p.id,d.promotion_state"""
SCENES = """SELECT p.id,p.title,d.promotion_state,count(*) AS drafts
FROM projects p JOIN scene_draft_versions d ON p.id=d.project_id
WHERE d.promotion_state='promoted' GROUP BY p.id,p.title,d.promotion_state ORDER BY p.id"""
DEBT_ADOPTIONS = """SELECT p.id,p.title,c.chapter_number,
c.metadata->'numeric_promotion_debt'->>'draft_id' AS adopted_draft_id
FROM projects p JOIN chapters c ON c.project_id=p.id
WHERE c.metadata ? 'numeric_promotion_debt' ORDER BY p.id,c.chapter_number"""
BODY_QUERY = """SELECT p.id AS project_id,p.title,p.slug,c.id AS chapter_id,
c.chapter_number,c.status,c.production_state,c.metadata AS chapter_metadata,
d.id AS draft_id,d.version_no,
d.promotion_state,d.is_current,d.promoted_at,d.content_md
FROM projects p JOIN chapters c ON c.project_id=p.id
JOIN chapter_draft_versions d ON d.chapter_id=c.id AND d.project_id=p.id
WHERE p.id=ANY($1::uuid[]) AND d.promotion_state='promoted'
AND c.chapter_number BETWEEN 1 AND 4 ORDER BY p.id,c.chapter_number"""


def _hash(raw: str) -> str:
    return sha256(raw.encode()).hexdigest()


def _scrub(report: dict) -> dict:
    for name in ("state_repetition", "number_repetition"):
        for group in report["metrics"][name]["groups"]:
            group["key_sha256"] = _hash(group.pop("key"))
    dialogue = report["metrics"]["dialogue"]
    mapping = {s["speaker"]: f"speaker_{n}" for n, s in enumerate(dialogue["speakers"], 1)}
    for speaker in dialogue["speakers"]:
        speaker["speaker"] = mapping[speaker["speaker"]]
    for pair in dialogue["pairs"]:
        pair["speakers"] = [mapping[s] for s in pair["speakers"]]
    return report


def _samples(text: str, report: dict) -> list[dict]:
    metrics = report["metrics"]
    sampled = []
    for name in (
        "clock",
        "state_repetition",
        "number_repetition",
        "process_terms",
        "negation_chains",
    ):
        value = metrics[name]
        if "groups" in value:
            spans = [s for group in value["groups"] for s in group["spans"]]
        elif name == "negation_chains":
            spans = [s for chain in value["spans"] for s in chain]
        else:
            spans = value["spans"]
        for span in spans[:2]:
            start, end = max(0, span["start"] - 70), min(len(text), span["end"] + 100)
            sampled.append(
                {
                    "signal": name,
                    "start": start,
                    "end": end,
                    "excerpt_sha256": _hash(text[start:end]),
                    "judgment": "pending_human",
                }
            )
    for start in (0, max(0, len(text) // 2 - 100), max(0, len(text) - 220)):
        end = min(len(text), start + 220)
        sampled.append(
            {
                "signal": "unflagged_control",
                "start": start,
                "end": end,
                "excerpt_sha256": _hash(text[start:end]),
                "judgment": "pending_human",
            }
        )
    return sampled


async def main(args: argparse.Namespace) -> None:
    env = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}
    settings = load_settings(
        config_path=ROOT / "config/default.yaml",
        local_config_path=ROOT / "config/local.yaml",
        env=env,
    )
    url = make_url(settings.database.url)
    if url.database != "bestseller" or url.host not in {"localhost", "127.0.0.1"}:
        raise ValueError("Unexpected local source database")
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        database=url.database,
        user=url.username,
        password=url.password,
        timeout=5,
        server_settings={
            "default_transaction_read_only": "on",
            "statement_timeout": "30000",
            "application_name": "par_Q3_readonly",
        },
    )
    transaction = connection.transaction(readonly=True, isolation="repeatable_read")
    try:
        await transaction.start()
        safety = dict(
            await connection.fetchrow(
                "SELECT current_database() AS database,"
                "current_setting('transaction_read_only') AS read_only"
            )
        )
        assert safety == {"database": "bestseller", "read_only": "on"}
        if args.discover:
            inventory = {
                "at_utc": datetime.now(UTC).isoformat(),
                "transaction": safety,
                "database_writes": 0,
                "model_calls": 0,
                "promoted_chapters": [dict(r) for r in await connection.fetch(DISCOVER)],
                "chapter_inventory": [dict(r) for r in await connection.fetch(INVENTORY)],
                "promoted_scenes": [dict(r) for r in await connection.fetch(SCENES)],
                "possible_debt_adoptions": [
                    dict(r) for r in await connection.fetch(DEBT_ADOPTIONS)
                ],
            }
            (OUT / "source-inventory.json").write_text(
                json.dumps(inventory, ensure_ascii=False, default=str, indent=2) + "\n"
            )
            print(json.dumps(inventory, ensure_ascii=False, default=str))
            return
        extra = tuple(UUID(value) for value in args.extra_project)
        projects = PRIMARY + extra
        if not extra or len(set(projects)) != len(projects):
            raise ValueError("Need distinct additional projects")
        rows = await connection.fetch(BODY_QUERY, list(projects))
        by_project = {p: [r for r in rows if r["project_id"] == p] for p in projects}
        for project, selected in by_project.items():
            expected = 4 if project == PRIMARY[0] else 3 if project == PRIMARY[1] else None
            selected[:] = [
                r for r in selected if expected is None or r["chapter_number"] <= expected
            ]
            if not selected or (
                expected is not None
                and [r["chapter_number"] for r in selected] != list(range(1, expected + 1))
            ):
                raise ValueError("Missing or ambiguous adopted chapter coverage")
        private = Path(tempfile.mkdtemp(prefix="bestseller-Q3-local-review-"))
        os.chmod(private, 0o700)
        private_lines = [
            "# Q3 本机人工抽查单",
            "正文仅在本机。逐项标记必要/冗余/误报/漏报；另读首中尾评估情绪与节奏。",
            "当前所有项目均未获真人回执。",
        ]
        results = []
        for project, selected in by_project.items():
            chapters = []
            for row in selected:
                text = row["content_md"]
                metadata_json = row["chapter_metadata"]
                if isinstance(metadata_json, str):
                    metadata_json = json.loads(metadata_json)
                chapter = SimpleNamespace(
                    id=row["chapter_id"], project_id=project, metadata_json=metadata_json
                )
                draft = SimpleNamespace(
                    id=row["draft_id"],
                    chapter_id=row["chapter_id"],
                    project_id=project,
                    promotion_state=row["promotion_state"],
                    is_current=row["is_current"],
                    promoted_at=row["promoted_at"],
                    content_md=text,
                )
                if choose_usable_promoted_chapter_source(chapter=chapter, drafts=[draft]) is None:
                    raise ValueError("Adopted source blocked by its exact continuity receipt")
                report = audit_prose(text, config=AuditConfig())
                samples = _samples(text, report)
                private_lines.append(
                    f"\n## {row['title']} / chapter {row['chapter_number']}"
                    f" / draft {row['draft_id']} / sha {_hash(text)}"
                )
                for sample in samples:
                    private_lines.append(
                        f"\n### {sample['signal']} [{sample['start']}:{sample['end']}]\n\n"
                        f"{text[sample['start'] : sample['end']]}\n\n判断：____（人工待填）"
                    )
                metadata = {
                    k: v
                    for k, v in dict(row).items()
                    if k not in {"content_md", "title", "slug", "chapter_metadata"}
                }
                metadata["source_selector"] = "choose_usable_promoted_chapter_source"
                chapters.append({"source": metadata, "audit": _scrub(report), "samples": samples})
            results.append(
                {
                    "project_id": str(project),
                    "title": selected[0]["title"],
                    "slug": selected[0]["slug"],
                    "chapters": chapters,
                }
            )
        sample_path = private / "samples.md"
        sample_path.write_text("\n".join(private_lines) + "\n")
        os.chmod(sample_path, 0o600)
        receipt = {
            "schema": "Q3-generalization.v1",
            "at_utc": datetime.now(UTC).isoformat(),
            "source": "live_promoted_chapter_drafts",
            "transaction": safety,
            "transaction_end": "rollback",
            "database_writes": 0,
            "model_calls": 0,
            "docker_used": False,
            "public_rules_frozen_before_this_readback": True,
            "changes_since_first_readback": (
                "Generic pronoun, idiom and state-paraphrase fixes came from failing synthetic "
                "tests; no manuscript excerpts were inspected or used as project rules. "
                "The full available corpus was rerun with the final module."
            ),
            "module_sha256": _hash(
                (
                    Path(__file__).parents[3]
                    / "src/bestseller/services/prose_procedural_density.py"
                ).read_text()
            ),
            "query_sha256": _hash(BODY_QUERY),
            "query_count": 2,
            "private_local_samples_path": str(sample_path),
            "human_acceptance": "UNKNOWN: no human inspection receipt",
            "projects": results,
            "additional_project_requirement_met": len(extra) >= 2,
        }
        (OUT / "generalization.json").write_text(
            json.dumps(receipt, ensure_ascii=False, default=str, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "output": str(OUT / "generalization.json"),
                    "local_samples": str(sample_path),
                    "projects": [
                        {"id": p["project_id"], "title": p["title"], "chapters": len(p["chapters"])}
                        for p in results
                    ],
                },
                ensure_ascii=False,
            )
        )
    finally:
        await transaction.rollback()
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--extra-project", action="append", default=[])
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as error:
        safe_reasons = {
            "Missing or ambiguous adopted chapter coverage",
            "Adopted source blocked by its exact continuity receipt",
            "Unexpected local source database",
        }
        print(
            json.dumps(
                {
                    "error_type": type(error).__name__,
                    "details": str(error) if str(error) in safe_reasons else "redacted",
                    "database_writes": 0,
                    "model_calls": 0,
                }
            )
        )
        raise SystemExit(1) from None
