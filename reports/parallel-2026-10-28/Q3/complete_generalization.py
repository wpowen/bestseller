"""Q3 fixed-rule corpus completion; PostgreSQL SELECT only, local files only."""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import re
import tempfile
from types import SimpleNamespace

import asyncpg
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from bestseller.services.adopted_chapter_source import choose_usable_promoted_chapter_source
from bestseller.services.prose_procedural_density import (
    DialogueTurn,
    _extract_dialogue,
    audit_prose,
)
from bestseller.settings import load_settings

ROOT = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller")
OUT = Path(__file__).resolve().parent / "completion"
SEED = 20261028
DIALOGUE_THRESHOLD = 0.35
CANDIDATE = "03b02877-6190-4608-8771-9bf7835aa624"
SQL = """WITH ranked AS (
SELECT p.id AS project_id,p.title,c.id AS chapter_id,c.chapter_number,
c.metadata AS chapter_metadata,d.id AS draft_id,d.version_no,
d.promotion_state,d.is_current,d.promoted_at,d.content_md,
row_number() OVER (PARTITION BY c.id ORDER BY d.version_no DESC,d.id) AS rank
FROM projects p JOIN chapters c ON c.project_id=p.id
JOIN chapter_draft_versions d ON d.project_id=p.id AND d.chapter_id=c.id
WHERE (p.id::text IN ('fa14e681-2bd9-44f5-93d1-7f2ef9900d69',
'd407ca85-9a9b-4564-a033-6bc529e0dd76','d9c76f6d-4ae9-4ecd-9090-3b0fd7d01c85')
AND d.promotion_state='promoted') OR
(p.id::text='03b02877-6190-4608-8771-9bf7835aa624' AND d.promotion_state='candidate'))
SELECT * FROM ranked WHERE rank=1 ORDER BY project_id,chapter_number"""


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def legacy_body(data: dict) -> tuple[str, list[DialogueTurn]]:
    parts, turns = [], []
    for node in data["nodes"]:
        for kind in ("text", "dialogue"):
            if kind not in node:
                continue
            value = node[kind]["content"]
            if not isinstance(value, str):
                raise ValueError("Legacy prose content must be a string")
            parts.append(value)
            if kind == "dialogue":
                turns.append(DialogueTurn(str(node[kind]["character_id"]), value))
    if not parts:
        raise ValueError("Empty legacy prose")
    return "\n".join(parts), turns


def select_legacy(files: list[Path]) -> list[Path]:
    files = sorted(files, key=lambda f: int(re.fullmatch(r"ch(\d+)\.json", f.name)[1]))
    if len(files) < 40:
        raise ValueError("Need forty distinct chapters")
    # Reproducible corpus sampling, not a cryptographic operation.
    return files[:20] + sorted(random.Random(SEED).sample(files[20:], 20))  # noqa: S311


def units(audit: dict) -> dict[str, list[dict]]:
    m = audit["metrics"]
    result = defaultdict(list)
    for name in ("clock", "process_terms"):
        result[name] = [{"unit": f"{name}:{s['start']}", "spans": [s]} for s in m[name]["spans"]]
    for name in ("state_repetition", "number_repetition"):
        result[name] = [
            {"unit": f"{name}:{g['spans'][0]['start']}", "spans": g["spans"]}
            for g in m[name]["groups"]
        ]
    result["negation_chains"] = [
        {"unit": f"negation_chains:{chain[0]['start']}", "spans": chain}
        for chain in m["negation_chains"]["spans"]
    ]
    result["dialogue"] = [
        {"unit": "dialogue:" + ":".join(pair["speakers"]), "spans": [], "pair": pair}
        for pair in m["dialogue"]["pairs"]
        if pair["bigram_jaccard"] >= DIALOGUE_THRESHOLD
    ]
    return result


def scrub(audit: dict) -> dict:
    audit = json.loads(json.dumps(audit, ensure_ascii=False))
    for name in ("state_repetition", "number_repetition"):
        for g in audit["metrics"][name]["groups"]:
            g["key_sha256"] = digest(g.pop("key"))
    d = audit["metrics"]["dialogue"]
    mapping = {s["speaker"]: f"speaker_{i}" for i, s in enumerate(d["speakers"], 1)}
    for s in d["speakers"]:
        s["speaker"] = mapping[s["speaker"]]
    for pair in d["pairs"]:
        pair["speakers"] = [mapping[s] for s in pair["speakers"]]
    return audit


def add_sample(
    samples: list,
    doc: dict,
    signal: str,
    unit: dict | None,
    window: tuple[int, int] | None = None,
    original: dict | None = None,
) -> None:
    text = doc["text"]
    spans = unit["spans"] if unit else []
    if window:
        start, end = window
    elif spans:
        start, end = max(0, spans[0]["start"] - 65), min(len(text), spans[-1]["end"] + 85)
    else:
        start, end = 0, min(len(text), 500)
    chunks = []
    if signal == "dialogue":
        wanted = set(unit["pair"]["speakers"])
        chunks = [
            f"{t.speaker}: {t.text}" for t in _extract_dialogue(text)[0] if t.speaker in wanted
        ]
    elif spans:
        chunks = [text[max(0, s["start"] - 50) : min(len(text), s["end"] + 70)] for s in spans[:5]]
    sample = {
        "sample_id": f"S{len(samples) + 1:03}",
        "source": doc["source"],
        "chapter": doc["chapter"],
        "body_sha256": digest(text),
        "signal": signal,
        "unit_id": f"{doc['id']}:{unit['unit']}" if unit else None,
        "start": start,
        "end": end,
        "excerpt_sha256": digest(text[start:end]),
        "original_73": original is not None,
        "review_type": "agent_editorial_not_human",
        "extraction_correct": None,
        "editorial_defect": None,
        "reason": None,
        "excerpt": text[start:end],
        "related_chunks": chunks,
    }
    if original:
        sample["original_sample"] = original
    samples.append(sample)


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    old_path = OUT.parent / "generalization.json"
    old = json.loads(old_path.read_text())
    env = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}
    settings = load_settings(
        config_path=ROOT / "config/default.yaml",
        local_config_path=ROOT / "config/local.yaml",
        env=env,
    )
    url = make_url(settings.database.url)
    assert url.host in ("127.0.0.1", "localhost") and url.database == "bestseller"
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        timeout=5,
        server_settings={
            "default_transaction_read_only": "on",
            "statement_timeout": "30000",
            "application_name": "par_Q3_completion_readonly",
        },
    )
    tx = connection.transaction(readonly=True, isolation="repeatable_read")
    docs = []
    try:
        await tx.start()
        safety = dict(
            await connection.fetchrow(
                "SELECT current_database() AS database,"
                "current_setting('transaction_read_only') AS read_only"
            )
        )
        assert safety == {"database": "bestseller", "read_only": "on"}
        for row in await connection.fetch(SQL):
            pid = str(row["project_id"])
            n = row["chapter_number"]
            if (pid.startswith("fa14") and n > 4) or (pid.startswith("d407") and n > 3):
                continue
            label = "xuanhuan_candidate" if pid == CANDIDATE else pid[:4]
            if pid != CANDIDATE:
                metadata = row["chapter_metadata"]
                metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
                ch = SimpleNamespace(
                    id=row["chapter_id"], project_id=row["project_id"], metadata_json=metadata
                )
                dr = SimpleNamespace(
                    id=row["draft_id"],
                    chapter_id=row["chapter_id"],
                    project_id=row["project_id"],
                    promotion_state=row["promotion_state"],
                    is_current=row["is_current"],
                    promoted_at=row["promoted_at"],
                    content_md=row["content_md"],
                )
                assert choose_usable_promoted_chapter_source(chapter=ch, drafts=[dr]) is not None
            text = row["content_md"]
            docs.append(
                {
                    "id": f"{label}:{n}",
                    "source": label,
                    "chapter": n,
                    "text": text,
                    "metadata": {
                        k: str(row[k]) if k in ("project_id", "chapter_id", "draft_id") else row[k]
                        for k in (
                            "project_id",
                            "chapter_id",
                            "draft_id",
                            "version_no",
                            "promotion_state",
                        )
                    },
                    "adoption": "候选，未采用" if pid == CANDIDATE else "已采用",
                    "audit": audit_prose(text),
                }
            )
    finally:
        await tx.rollback()
        await connection.close()
    selected = select_legacy(list((ROOT / "output/天机录/if/chapters").glob("ch*.json")))
    for f in selected:
        raw = f.read_bytes()
        data = json.loads(raw)
        text, turns = legacy_body(data)
        docs.append(
            {
                "id": f"tianjilu:{data['number']}",
                "source": "tianjilu_legacy",
                "chapter": data["number"],
                "text": text,
                "metadata": {
                    "path": str(f.relative_to(ROOT)),
                    "file_sha256": sha256(raw).hexdigest(),
                    "native_dialogue_turns": len(turns),
                },
                "adoption": "旧链路；本地IF节点正文，未证明现行采用",
                "audit": audit_prose(text),
                "native_dialogue_audit": scrub(audit_prose(text, dialogue_turns=turns))["metrics"][
                    "dialogue"
                ],
            }
        )
    by_id = {(d["source"], d["chapter"]): d for d in docs}
    samples = []
    for p in old["projects"]:
        for c in p["chapters"]:
            d = by_id[(p["project_id"][:4], c["source"]["chapter_number"])]
            assert digest(d["text"]) == c["audit"]["body_sha256"], "Original adopted input drift"
            u = units(d["audit"])
            cursor = defaultdict(int)
            for original in c["samples"]:
                signal = original["signal"]
                unit = None
                if signal != "unflagged_control":
                    flattened = [unit for unit in u[signal] for _ in unit["spans"]]
                    unit = flattened[cursor[signal]]
                    cursor[signal] += 1
                assert (
                    digest(d["text"][original["start"] : original["end"]])
                    == original["excerpt_sha256"]
                )
                add_sample(samples, d, signal, unit, (original["start"], original["end"]), original)
    assert len(samples) == 73
    for label in sorted({d["source"] for d in docs}):
        for signal in (
            "clock",
            "state_repetition",
            "number_repetition",
            "process_terms",
            "negation_chains",
            "dialogue",
        ):
            candidates = [
                (d, u) for d in docs if d["source"] == label for u in units(d["audit"])[signal]
            ]
            existing = {s["unit_id"] for s in samples}
            candidates = [(d, u) for d, u in candidates if f"{d['id']}:{u['unit']}" not in existing]
            # Eight additional evenly spaced units, independent of wording or editor labels.
            for i in (
                sorted(
                    {
                        round(i * (len(candidates) - 1) / min(7, len(candidates) - 1))
                        for i in range(min(8, len(candidates)))
                    }
                )
                if len(candidates) > 1
                else range(len(candidates))
            ):
                d, u = candidates[i]
                add_sample(samples, d, signal, u)
        selected_docs = [d for d in docs if d["source"] == label]
        k = 0
        while sum(s["source"] == label for s in samples) < 10 or (
            label in ("xuanhuan_candidate", "tianjilu_legacy") and k < 4
        ):
            d = selected_docs[k % len(selected_docs)]
            start = min(max(0, len(d["text"]) - 220), int(len(d["text"]) * (k + 1) / 12))
            add_sample(
                samples, d, "unflagged_control", None, (start, min(len(d["text"]), start + 220))
            )
            k += 1
    private = Path(tempfile.mkdtemp(prefix="bestseller-Q3-completion-"))
    os.chmod(private, 0o700)
    material = private / "editorial-material.json"
    material.write_text(
        json.dumps(
            {"documents": docs, "samples": samples}, ensure_ascii=False, default=str, indent=2
        )
    )
    os.chmod(material, 0o600)
    public = {
        "schema": "Q3-completion.v1",
        "at_utc": datetime.now(UTC).isoformat(),
        "transaction": safety,
        "transaction_end": "rollback",
        "database_writes": 0,
        "model_api_calls": 0,
        "docker_used": False,
        "module_sha256": digest(
            (
                Path(__file__).parents[3] / "src/bestseller/services/prose_procedural_density.py"
            ).read_text()
        ),
        "config_sha256": docs[0]["audit"]["config_sha256"],
        "random_seed": SEED,
        "legacy_chapters": [json.loads(f.read_text())["number"] for f in selected],
        "selection": "first20 plus seed20261028 random20 without replacement from remaining1180",
        "dialogue_hypothesis_threshold": DIALOGUE_THRESHOLD,
        "original_73_rebound_exactly": True,
        "private_material_path": str(material),
        "documents": [
            {k: (scrub(v) if k == "audit" else v) for k, v in d.items() if k != "text"}
            for d in docs
        ],
        "samples": [
            {k: v for k, v in s.items() if k not in ("excerpt", "related_chunks")} for s in samples
        ],
    }
    (OUT / "corpus-and-samples.json").write_text(
        json.dumps(public, ensure_ascii=False, default=str, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "private_material": str(material),
                "documents": len(docs),
                "samples": len(samples),
                "by_source": {
                    label: sum(s["source"] == label for s in samples)
                    for label in sorted({d["source"] for d in docs})
                },
                "legacy_chapters": public["legacy_chapters"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__, "details": "redacted"}))
        raise SystemExit(1) from None
