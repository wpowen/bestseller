"""Versioned, deterministic contracts for prose quality evaluation runs.

This module deliberately contains no LLM calls.  It records the evidence a
real run must supply and fails closed when that evidence is incomplete or was
produced through a fallback/degraded path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from bestseller.services.prose_prompt_experiment import build_public_blind_packet


@dataclass(frozen=True)
class QualityEvaluationCase:
    case_id: str
    genre: str
    chapter_number: int
    seed: int


@dataclass(frozen=True)
class QualityEvaluationConfig:
    schema_version: str
    evaluation_version: str
    samples_per_strategy: int
    minimum_draft_samples: int
    required_judgements_per_draft: int
    required_human_ballots_per_draft: int
    compiler_mode: str
    writer_catalog_key: str
    judge_catalog_key: str
    strategies: tuple[str, ...]
    cases: tuple[QualityEvaluationCase, ...]

    @property
    def expected_draft_count(self) -> int:
        return len(self.cases) * len(self.strategies) * self.samples_per_strategy


def canonical_hash(value: object) -> str:
    """Return a stable SHA-256 for JSON-compatible evidence."""

    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def prompt_hashes(
    *,
    system_prompt: str,
    user_prompt: str,
    final_prompt: str,
    context: object,
) -> dict[str, str]:
    """Hash every writer input surface required by the evaluation manifest."""

    return {
        "system": canonical_hash(system_prompt),
        "user": canonical_hash(user_prompt),
        "final": canonical_hash(final_prompt),
        "context": canonical_hash(context),
    }


def load_quality_evaluation_config(path: str | Path) -> QualityEvaluationConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("quality evaluation config must be a mapping")

    cases_raw = _sequence(payload.get("cases"))
    cases = tuple(
        sorted(
            (
                QualityEvaluationCase(
                    case_id=_required_text(item, "case_id"),
                    genre=_required_text(item, "genre"),
                    chapter_number=_positive_int(item, "chapter_number"),
                    seed=_non_negative_int(item, "seed"),
                )
                for item in (_mapping(raw) for raw in cases_raw)
            ),
            key=lambda item: item.case_id,
        )
    )
    strategies = tuple(
        sorted(
            {
                _required_scalar_text(item)
                for item in _sequence(payload.get("strategies"))
            }
        )
    )
    config = QualityEvaluationConfig(
        schema_version=_required_text(payload, "schema_version"),
        evaluation_version=_required_text(payload, "evaluation_version"),
        samples_per_strategy=_positive_int(payload, "samples_per_strategy"),
        minimum_draft_samples=_positive_int(payload, "minimum_draft_samples"),
        required_judgements_per_draft=_positive_int(
            payload, "required_judgements_per_draft"
        ),
        required_human_ballots_per_draft=_positive_int(
            payload, "required_human_ballots_per_draft"
        ),
        compiler_mode=_required_text(payload, "compiler_mode"),
        writer_catalog_key=_required_text(payload, "writer_catalog_key"),
        judge_catalog_key=_required_text(payload, "judge_catalog_key"),
        strategies=strategies,
        cases=cases,
    )
    _validate_config(config)
    return config


def build_evaluation_manifest(
    config: QualityEvaluationConfig,
    *,
    run_id: str,
    git_sha: str,
    docker_image_id: str,
    writer_catalog_key: str,
    writer_actual_model: str,
    judge_catalog_key: str,
    judge_actual_model: str,
) -> dict[str, Any]:
    """Build the immutable identity and empty evidence ledger for a run."""

    identity = {
        "run_id": _required_scalar_text(run_id),
        "evaluation_version": config.evaluation_version,
        "config_hash": canonical_hash(asdict(config)),
        "git_sha": _required_scalar_text(git_sha),
        "docker_image_id": _required_scalar_text(docker_image_id),
        "compiler_mode": config.compiler_mode,
        "writer": {
            "catalog_key": _required_scalar_text(writer_catalog_key),
            "actual_model": _required_scalar_text(writer_actual_model),
        },
        "judge": {
            "catalog_key": _required_scalar_text(judge_catalog_key),
            "actual_model": _required_scalar_text(judge_actual_model),
        },
    }
    expected_drafts = config.expected_draft_count
    expected_draft_ids = [
        f"{case.case_id}__{strategy_id}__s{sample_index}"
        for case in config.cases
        for strategy_id in config.strategies
        for sample_index in range(1, config.samples_per_strategy + 1)
    ]
    expected_judgement_slots = [
        f"{draft_id}::j{slot}"
        for draft_id in expected_draft_ids
        for slot in range(1, config.required_judgements_per_draft + 1)
    ]
    expected_human_ballot_slots = [
        f"{draft_id}::h{slot}"
        for draft_id in expected_draft_ids
        for slot in range(1, config.required_human_ballots_per_draft + 1)
    ]
    return {
        "schema_version": "quality-evaluation-manifest/v1",
        "manifest_id": canonical_hash(identity),
        "identity": identity,
        "expected": {
            "draft_count": expected_drafts,
            "draft_ids": expected_draft_ids,
            "judgement_count": (
                expected_drafts * config.required_judgements_per_draft
            ),
            "judgement_slots": expected_judgement_slots,
            "human_ballot_count": (
                expected_drafts * config.required_human_ballots_per_draft
            ),
            "human_ballot_slots": expected_human_ballot_slots,
        },
        "cases": [asdict(case) for case in config.cases],
        "strategies": list(config.strategies),
        "samples_per_strategy": config.samples_per_strategy,
        "drafts": [],
        "llm_judgements": [],
        "human_ballots": [],
    }


def build_blind_review_packet(
    *, packet_seed: str, candidates: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a public packet plus a separately persisted private label map."""

    return build_public_blind_packet(packet_seed=packet_seed, candidates=candidates)


def evaluate_release_gate(
    manifest: Mapping[str, Any], *, require_human: bool = True
) -> dict[str, Any]:
    """Return pass only when every required piece of real evidence is valid."""

    expected = _mapping(manifest.get("expected"))
    expected_drafts = _integer(expected.get("draft_count"))
    expected_draft_ids = {
        str(item) for item in _sequence(expected.get("draft_ids")) if str(item)
    }
    expected_judgements = _integer(expected.get("judgement_count"))
    expected_judgement_slots = {
        str(item)
        for item in _sequence(expected.get("judgement_slots"))
        if str(item)
    }
    if (
        not expected_judgement_slots
        and expected_draft_ids
        and expected_judgements % len(expected_draft_ids) == 0
    ):
        per_draft = expected_judgements // len(expected_draft_ids)
        expected_judgement_slots = {
            f"{draft_id}::j{slot}"
            for draft_id in expected_draft_ids
            for slot in range(1, per_draft + 1)
        }
    expected_human = _integer(expected.get("human_ballot_count")) if require_human else 0
    expected_human_slots = (
        {
            str(item)
            for item in _sequence(expected.get("human_ballot_slots"))
            if str(item)
        }
        if require_human
        else set()
    )
    drafts = [_mapping(item) for item in _sequence(manifest.get("drafts"))]
    judgements = [
        _mapping(item) for item in _sequence(manifest.get("llm_judgements"))
    ]
    ballots = [_mapping(item) for item in _sequence(manifest.get("human_ballots"))]

    reasons: list[str] = []
    identity = _mapping(manifest.get("identity"))
    writer_identity = _mapping(identity.get("writer"))
    judge_identity = _mapping(identity.get("judge"))
    if not _all_text(identity, "git_sha", "docker_image_id") or identity.get(
        "docker_image_id"
    ) == "unverified":
        reasons.append("unverified_runtime_identity")
    if (
        not _all_text(writer_identity, "catalog_key", "actual_model")
        or not _all_text(judge_identity, "catalog_key", "actual_model")
        or writer_identity.get("actual_model") == "unresolved"
        or judge_identity.get("actual_model") == "unresolved"
    ):
        reasons.append("unresolved_model_identity")
    actual_draft_ids = {
        str(item.get("draft_id")) for item in drafts if item.get("draft_id")
    }
    if len(drafts) < expected_drafts or (
        expected_draft_ids and not expected_draft_ids.issubset(actual_draft_ids)
    ):
        reasons.append("missing_drafts")
    if len(judgements) < expected_judgements:
        reasons.append("missing_judgements")
    if require_human and len(ballots) < expected_human:
        reasons.append("missing_human_ballots")

    judgement_slots = {_judgement_slot(item) for item in judgements}
    judgement_slots.discard(None)
    judgement_keys = [_judgement_evidence_key(item) for item in judgements]
    judgement_keys = [key for key in judgement_keys if key is not None]
    if len(judgement_keys) != len(set(judgement_keys)):
        reasons.append("duplicate_judgements")
    if expected_judgement_slots and any(
        slot not in expected_judgement_slots for slot in judgement_slots
    ):
        reasons.append("out_of_scope_judgements")
    if expected_judgement_slots and not expected_judgement_slots.issubset(
        judgement_slots
    ):
        reasons.append("missing_judgement_coverage")

    ballot_slots = {_human_ballot_slot(item) for item in ballots}
    ballot_slots.discard(None)
    ballot_keys = [_human_ballot_evidence_key(item) for item in ballots]
    ballot_keys = [key for key in ballot_keys if key is not None]
    if require_human and len(ballot_keys) != len(set(ballot_keys)):
        reasons.append("duplicate_human_ballots")
    if require_human and expected_human_slots and any(
        slot not in expected_human_slots for slot in ballot_slots
    ):
        reasons.append("out_of_scope_human_ballots")
    if require_human and expected_human_slots and not expected_human_slots.issubset(
        ballot_slots
    ):
        reasons.append("missing_human_ballot_coverage")

    if any(bool(item.get("fallback_used")) for item in (*drafts, *judgements)):
        reasons.append("fallback_samples")
    if any(bool(item.get("degraded")) for item in (*drafts, *judgements)):
        reasons.append("degraded_samples")

    valid_drafts = sum(_valid_draft(item) for item in drafts)
    valid_judgements = sum(_valid_judgement(item) for item in judgements)
    valid_ballots = sum(_valid_ballot(item) for item in ballots) if require_human else 0
    if len(drafts) >= expected_drafts and valid_drafts < expected_drafts:
        reasons.append("invalid_draft_evidence")
    if len(judgements) >= expected_judgements and valid_judgements < expected_judgements:
        reasons.append("invalid_judgement_evidence")
    if require_human and len(ballots) >= expected_human and valid_ballots < expected_human:
        reasons.append("invalid_human_ballots")

    return {
        "schema_version": "quality-release-gate/v1",
        "status": "inconclusive" if reasons else "pass",
        "reasons": list(dict.fromkeys(reasons)),
        "counts": {
            "expected_drafts": expected_drafts,
            "valid_drafts": valid_drafts,
            "expected_judgements": expected_judgements,
            "valid_judgements": valid_judgements,
            "expected_human_ballots": expected_human,
            "valid_human_ballots": valid_ballots,
        },
    }


def write_evaluation_run_skeleton(
    root: str | Path, manifest: Mapping[str, Any]
) -> dict[str, str]:
    """Create the fixed quality-eval artifact layout without fabricating evidence."""

    output_root = Path(root)
    output_root.mkdir(parents=True, exist_ok=True)
    drafts = output_root / "drafts"
    judgements = output_root / "llm-judgements"
    packets = output_root / "human-packets"
    for directory in (drafts, judgements, packets):
        directory.mkdir(parents=True, exist_ok=True)

    cases_path = output_root / "cases.jsonl"
    cases_path.write_text(
        "".join(
            f"{json.dumps(case, ensure_ascii=False, sort_keys=True)}\n"
            for case in _sequence(manifest.get("cases"))
        ),
        encoding="utf-8",
    )
    manifest_path = output_root / "manifest.json"
    _write_json(manifest_path, manifest)
    gate_path = output_root / "release-gate.json"
    gate = evaluate_release_gate(manifest, require_human=True)
    _write_json(gate_path, gate)
    report_path = output_root / "report.html"
    report_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Quality evaluation</title>"
        f"<h1>{gate['status']}</h1>",
        encoding="utf-8",
    )
    return {
        "cases": str(cases_path),
        "drafts": str(drafts),
        "llm_judgements": str(judgements),
        "human_packets": str(packets),
        "manifest": str(manifest_path),
        "report": str(report_path),
        "release_gate": str(gate_path),
    }


def _validate_config(config: QualityEvaluationConfig) -> None:
    if config.schema_version != "quality-evaluation/v1":
        raise ValueError(f"unsupported quality evaluation schema: {config.schema_version}")
    if len({case.case_id for case in config.cases}) != len(config.cases):
        raise ValueError("quality evaluation case_id values must be unique")
    if config.samples_per_strategy < 2:
        raise ValueError("samples_per_strategy must be at least 2")
    chapters = {case.chapter_number for case in config.cases}
    if not {1, 3, 10}.issubset(chapters) or not any(chapter > 10 for chapter in chapters):
        raise ValueError("cases must cover chapters 1, 3, 10 and a mid-book chapter")
    if config.expected_draft_count < config.minimum_draft_samples:
        raise ValueError("configured case matrix does not meet minimum_draft_samples")


def _valid_draft(item: Mapping[str, Any]) -> bool:
    writer = _mapping(item.get("writer"))
    hashes = _mapping(item.get("prompt_hashes"))
    generation = _mapping(item.get("generation"))
    return bool(
        _all_text(item, "draft_id", "case_id", "strategy_id", "compiler_mode")
        and _integer(item.get("sample_index")) > 0
        and _all_text(writer, "catalog_key", "actual_model")
        and _all_text(hashes, "system", "user", "final", "context")
        and all(
            generation.get(key) is not None
            for key in (
                "temperature",
                "max_tokens",
                "input_tokens",
                "output_tokens",
                "cost",
            )
        )
        and not item.get("fallback_used")
        and not item.get("degraded")
    )


def _valid_judgement(item: Mapping[str, Any]) -> bool:
    judge = _mapping(item.get("judge"))
    return bool(
        _all_text(item, "draft_id")
        and _integer(item.get("evaluation_slot")) > 0
        and _all_text(judge, "catalog_key", "actual_model")
        and _mapping(item.get("scores"))
        and not item.get("fallback_used")
        and not item.get("degraded")
    )


def _valid_ballot(item: Mapping[str, Any]) -> bool:
    return bool(
        _all_text(item, "packet_id", "target_id", "rater_id", "choice")
        and _integer(item.get("ballot_slot")) > 0
    )


def _judgement_slot(item: Mapping[str, Any]) -> str | None:
    draft_id = str(item.get("draft_id") or "").strip()
    slot = _integer(item.get("evaluation_slot"))
    return f"{draft_id}::j{slot}" if draft_id and slot > 0 else None


def _judgement_evidence_key(item: Mapping[str, Any]) -> tuple[str, str, str] | None:
    slot = _judgement_slot(item)
    judge = _mapping(item.get("judge"))
    if slot is None or not _all_text(judge, "catalog_key", "actual_model"):
        return None
    return slot, str(judge["catalog_key"]), str(judge["actual_model"])


def _human_ballot_slot(item: Mapping[str, Any]) -> str | None:
    target_id = str(item.get("target_id") or "").strip()
    slot = _integer(item.get("ballot_slot"))
    return f"{target_id}::h{slot}" if target_id and slot > 0 else None


def _human_ballot_evidence_key(item: Mapping[str, Any]) -> tuple[str, str] | None:
    slot = _human_ballot_slot(item)
    rater_id = str(item.get("rater_id") or "").strip()
    return (slot, rater_id) if slot and rater_id else None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _integer(value: object) -> int:
    return int(value) if isinstance(value, int | float | str) and str(value).strip() else 0


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    return _required_scalar_text(payload.get(key))


def _required_scalar_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("required text value is empty")
    return text


def _positive_int(payload: Mapping[str, Any], key: str) -> int:
    value = _integer(payload.get(key))
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _non_negative_int(payload: Mapping[str, Any], key: str) -> int:
    value = _integer(payload.get(key))
    if value < 0:
        raise ValueError(f"{key} must be non-negative")
    return value


def _all_text(payload: Mapping[str, Any], *keys: str) -> bool:
    return all(
        isinstance(payload.get(key), str) and bool(str(payload[key]).strip())
        for key in keys
    )
