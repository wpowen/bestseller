"""P4 SELECT-only readback: prose stays in memory; output has hashes and counts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID

import asyncpg
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from bestseller.services.quantity_consistency_probe import probe_chapters
from bestseller.settings import load_settings

ROOT = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller")
OUT = ROOT / "reports/parallel-2026-10-28/P4"
PROJECT = UUID("d407ca85-9a9b-4564-a033-6bc529e0dd76")
SQL = """SELECT c.chapter_number, c.id AS chapter_id, c.status, c.production_state,
                d.id AS draft_id, d.version_no, d.promotion_state, d.is_current,
                d.content_md, d.promoted_at
         FROM chapters c
         LEFT JOIN chapter_draft_versions d
           ON d.chapter_id = c.id AND d.project_id = c.project_id
          AND d.promotion_state = 'promoted'
         WHERE c.project_id = $1 AND c.chapter_number BETWEEN 1 AND 3
         ORDER BY c.chapter_number"""


async def main() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = OUT / f"promoted-readback-{stamp}"
    destination.mkdir(exist_ok=False)
    env = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}
    settings = load_settings(
        config_path=ROOT / "config/default.yaml",
        local_config_path=ROOT / "config/local.yaml",
        env=env,
    )
    target = make_url(settings.database.url)
    assert target.database == "bestseller" and target.host in ("localhost", "127.0.0.1")
    connection = None
    try:
        connection = await asyncpg.connect(
            host=target.host,
            port=target.port or 5432,
            user=target.username,
            password=target.password,
            database=target.database,
            timeout=5,
            server_settings={
                "default_transaction_read_only": "on",
                "statement_timeout": "30000",
                "application_name": "par_P4_readonly",
            },
        )
        transaction = connection.transaction(isolation="repeatable_read", readonly=True)
        await transaction.start()
        try:
            safety = await connection.fetchrow(
                "SELECT current_database() AS database, "
                "current_setting('transaction_read_only') AS read_only, "
                "current_setting('statement_timeout') AS statement_timeout"
            )
            assert safety["database"] == "bestseller" and safety["read_only"] == "on"
            project = await connection.fetchrow(
                "SELECT id, title, slug FROM projects WHERE id=$1", PROJECT
            )
            assert project and project["slug"] == "realistic-life-1792907083"
            rows = await connection.fetch(SQL, PROJECT)
            assert len(rows) == 3 and [r["chapter_number"] for r in rows] == [1, 2, 3]
            assert all(r["draft_id"] and r["promotion_state"] == "promoted" for r in rows)
            texts = {r["chapter_number"]: r["content_md"] for r in rows}
            report = probe_chapters(texts)
            sources = []
            for row in rows:
                n = row["chapter_number"]
                raw = row["content_md"]
                export = ROOT / f"output/{project['slug']}/chapter-{n:03}.md"
                sources.append(
                    {
                        k: str(v)
                        if isinstance(v, UUID)
                        else v.isoformat()
                        if isinstance(v, datetime)
                        else v
                        for k, v in dict(row).items()
                        if k != "content_md"
                    }
                    | {
                        "raw_body_sha256": sha256(raw.encode()).hexdigest(),
                        "body_characters": len(raw),
                        "export_sha256": sha256(export.read_bytes()).hexdigest(),
                        "export_matches_promoted_body": export.read_text() == raw,
                    }
                )
            result = {
                "at_utc": datetime.now(UTC).isoformat(),
                "project_id": str(PROJECT),
                "title": project["title"],
                "transaction": dict(safety),
                "transaction_end": "rollback",
                "queries": 3,
                "query_sha256": sha256(SQL.encode()).hexdigest(),
                "database_writes": 0,
                "provider_calls": 0,
                "docker_used": False,
                "source": "live_promoted_drafts",
                "chapters": sources,
            }
        finally:
            await transaction.rollback()
        (destination / "sources.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        (destination / "checker-report.json").write_text(report.to_json() + "\n")
        print(
            json.dumps(
                {
                    "directory": str(destination),
                    "chapters": [
                        {
                            "chapter": s["chapter_number"],
                            "version": s["version_no"],
                            "sha256": s["raw_body_sha256"],
                            "matches_export": s["export_matches_promoted_body"],
                        }
                        for s in sources
                    ],
                    "fact_count": report.metrics["fact_count"],
                    "findings": [i.to_dict() for i in report.issues],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception as error:
        failure = {
            "error_type": type(error).__name__,
            "sqlstate": getattr(error, "sqlstate", None),
            "errno": getattr(error, "errno", None),
            "message": "details redacted",
            "database_writes": 0,
            "provider_calls": 0,
        }
        (destination / "failure.json").write_text(json.dumps(failure, indent=2) + "\n")
        print(json.dumps(failure))
        raise SystemExit(1) from None
    finally:
        if connection is not None:
            await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
