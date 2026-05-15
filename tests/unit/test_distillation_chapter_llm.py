from __future__ import annotations

import json
from pathlib import Path

import pytest

from bestseller.services.distillation_chapter_llm import (
    chapter_card_keys_missing_craft,
    existing_chapter_card_keys,
    iter_pending_jobs,
    load_chapter_card_schema,
    merge_chapter_card_segments,
    split_chapter_text_for_llm,
    upsert_chapter_card_jsonl,
    validate_chapter_card,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_split_chapter_text_respects_hard_cap() -> None:
    body = "x" * 25000
    parts = split_chapter_text_for_llm(body, soft=8000, hard=12000)
    assert len(parts) > 1
    assert all(len(p) <= 12000 for p in parts)


def test_merge_chapter_card_segments_concatenates() -> None:
    schema = load_chapter_card_schema(_repo_root())
    base = {
        "source_id": "source-test",
        "abs_chapter_no": 1,
        "volume_no": 1,
        "chapter_function": "part-a",
        "state_changes": [{"axis": "a", "before": "b", "after": "c", "story_value": "d"}],
        "reader_rewards": ["r1"],
        "open_hooks": ["h1"],
        "reusable_mechanisms": ["m1"],
        "craft_observations": {
            "sentence_rhythm": "short action beat",
            "do_not_copy": ["distinctive phrase"],
        },
        "non_reusable_specifics": ["n1"],
        "risk_flags": [],
        "confidence": 0.9,
    }
    second = dict(base)
    second["chapter_function"] = "part-b"
    second["reader_rewards"] = ["r1", "r2"]
    second["confidence"] = 0.75
    merged = merge_chapter_card_segments([base, second], schema=schema)
    assert "分段1/2" in merged["chapter_function"] and "分段2/2" in merged["chapter_function"]
    assert merged["confidence"] == 0.75
    assert "r2" in merged["reader_rewards"]
    assert "short action beat" in merged["craft_observations"]["sentence_rhythm"]
    assert "distinctive phrase" in merged["craft_observations"]["do_not_copy"]


def test_validate_chapter_card_accepts_sample_shape() -> None:
    schema = load_chapter_card_schema(_repo_root())
    row = {
        "source_id": "source-test",
        "abs_chapter_no": 1,
        "volume_no": 1,
        "chapter_function": "test",
        "state_changes": [
            {
                "axis": "a",
                "before": "b",
                "after": "c",
                "story_value": "d",
            }
        ],
        "reader_rewards": ["r"],
        "open_hooks": ["h"],
        "reusable_mechanisms": ["m"],
        "non_reusable_specifics": ["n"],
        "risk_flags": [],
        "confidence": 0.5,
    }
    validate_chapter_card(row, schema=schema)


def test_validate_chapter_card_rejects_missing_field() -> None:
    schema = load_chapter_card_schema(_repo_root())
    row = {"source_id": "x", "abs_chapter_no": 1}
    with pytest.raises(ValueError, match="missing"):
        validate_chapter_card(row, schema=schema)


def test_existing_chapter_card_keys(tmp_path: Path) -> None:
    p = tmp_path / "chapter_cards.jsonl"
    p.write_text(
        json.dumps({"source_id": "source-0001", "abs_chapter_no": 2}) + "\n",
        encoding="utf-8",
    )
    keys = existing_chapter_card_keys(p)
    assert ("source-0001", 2) in keys


def test_iter_pending_jobs_respects_done_and_limit(tmp_path: Path) -> None:
    pkg = tmp_path / "source-0009"
    (pkg / "llm_jobs").mkdir(parents=True)
    jobs = [
        {"job_id": "a-1", "source_id": "source-0009", "abs_chapter_no": 1, "private_payload_ref": "x"},
        {"job_id": "a-2", "source_id": "source-0009", "abs_chapter_no": 2, "private_payload_ref": "y"},
        {"job_id": "a-3", "source_id": "source-0009", "abs_chapter_no": 3, "private_payload_ref": "z"},
    ]
    (pkg / "llm_jobs" / "chapter_jobs.index.jsonl").write_text(
        "\n".join(json.dumps(j) for j in jobs) + "\n",
        encoding="utf-8",
    )
    done = {("source-0009", 1)}
    pending = list(iter_pending_jobs(pkg, existing_keys=done, limit=1))
    assert len(pending) == 1
    assert pending[0]["abs_chapter_no"] == 2


def test_iter_pending_jobs_can_refresh_missing_craft(tmp_path: Path) -> None:
    pkg = tmp_path / "source-0009"
    (pkg / "llm_jobs").mkdir(parents=True)
    job = {"job_id": "a-1", "source_id": "source-0009", "abs_chapter_no": 1}
    (pkg / "llm_jobs" / "chapter_jobs.index.jsonl").write_text(
        json.dumps(job) + "\n",
        encoding="utf-8",
    )

    done = {("source-0009", 1)}
    pending = list(
        iter_pending_jobs(
            pkg,
            existing_keys=done,
            refresh_keys={("source-0009", 1)},
            limit=None,
        )
    )

    assert len(pending) == 1
    assert pending[0]["abs_chapter_no"] == 1


def test_upsert_chapter_card_replaces_existing_row(tmp_path: Path) -> None:
    p = tmp_path / "chapter_cards.jsonl"
    old = {"source_id": "source-0001", "abs_chapter_no": 1, "chapter_function": "old"}
    new = {
        "source_id": "source-0001",
        "abs_chapter_no": 1,
        "chapter_function": "new",
        "craft_observations": {"sentence_rhythm": "short"},
    }
    p.write_text(json.dumps(old) + "\n", encoding="utf-8")

    assert chapter_card_keys_missing_craft(p) == {("source-0001", 1)}
    upsert_chapter_card_jsonl(p, new)

    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["chapter_function"] == "new"
    assert chapter_card_keys_missing_craft(p) == set()
