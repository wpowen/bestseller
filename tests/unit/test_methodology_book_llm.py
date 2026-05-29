from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bestseller.services.llm import LLMCompletionRequest, LLMCompletionResult
from bestseller.services.methodology_book_distillation import (
    MethodologyCandidateDeck,
    load_methodology_candidates,
)
import bestseller.services.methodology_book_llm as methodology_llm
from bestseller.settings import LLM_RUNTIME_PROFILES, load_settings


def test_xiaomi_mimo_runtime_profile_uses_token_plan_openai_endpoint() -> None:
    profile = LLM_RUNTIME_PROFILES["xiaomi-mimo"]
    roles = profile["roles"]

    assert profile["label"] == "Xiaomi MiMo"
    assert roles["summarizer"]["model"] == "openai/mimo-v2.5-pro"
    assert roles["summarizer"]["api_base"] == "https://token-plan-cn.xiaomimimo.com/v1"
    assert roles["summarizer"]["api_key_env"] == "XIAOMI_MIMO_API_KEY"
    assert roles["summarizer"]["api_key_header"] == "api-key"


@pytest.mark.asyncio
async def test_extract_methodology_candidates_coerces_identity_and_validates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    private_root = tmp_path / "private"
    payload_path = private_root / "source-9001" / "llm_payloads" / "sec-0001.prompt.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text(
        json.dumps(
            {
                "task_type": "writing_methodology_section_extraction",
                "source_id": "source-9001",
                "section_id": "sec-0001",
                "abs_section_no": 1,
                "boundary_type": "chapter_heading",
                "section_title_redacted": "sec-0001",
                "system": "Extract transferable writing methodology.",
                "section_text": "先定义场景目标, 再设置阻碍与结果, 最后检查变化是否可验证。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    job = {
        "job_id": "source-9001-sec-0001",
        "source_id": "source-9001",
        "section_id": "sec-0001",
        "private_payload_ref": "source-9001/llm_payloads/sec-0001.prompt.json",
    }

    async def fake_complete_text(
        _session: Any,
        _settings: Any,
        request: LLMCompletionRequest,
    ) -> LLMCompletionResult:
        assert request.logical_role == "summarizer"
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "candidates": [
                        {
                            "candidate_id": "场景目标检查",
                            "source_id": "source-0000",
                            "section_id": "sec-9999",
                            "title": "场景目标阻碍结果检查",
                            "category": "scene_design",
                            "scope": ["scene"],
                            "stage": ["planning", "review"],
                            "core_claim": "每个场景应能说明目标、阻碍、行动与可验证结果。",
                            "operating_steps": ["写出目标", "添加阻碍", "记录结果变化"],
                            "anti_patterns": ["只有事件没有变化"],
                            "required_contract_fields": ["goal", "obstacle", "outcome"],
                            "framework_bindings": ["scene_card", "chapter_outline_readiness_gate"],
                            "gate_bindings": [{"gate": "scene_contract", "default_mode": "warn"}],
                            "alignment_terms": ["goal-obstacle-result"],
                            "conflicts_with": [],
                            "confidence": 1.3,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            provider="unit-test",
            model_name="stub",
            finish_reason="stop",
        )

    monkeypatch.setattr(methodology_llm, "complete_text", fake_complete_text)
    deck = await methodology_llm.extract_methodology_candidates_for_job(
        object(),  # type: ignore[arg-type]
        load_settings(),
        repo_root=repo_root,
        private_root=private_root,
        job=job,
        schema=methodology_llm.load_methodology_candidate_schema(repo_root),
        max_section_chars=None,
    )

    assert len(deck.candidates) == 1
    candidate = deck.candidates[0]
    assert candidate.source_id == "source-9001"
    assert candidate.section_id == "sec-0001"
    assert candidate.candidate_id == "source-9001.sec-0001.01"
    assert candidate.confidence == 1.0


def test_empty_section_result_marks_job_done(tmp_path: Path) -> None:
    out_path = tmp_path / "methodology_candidates.review.jsonl"
    methodology_llm.append_methodology_section_result_jsonl(
        out_path,
        source_id="source-9001",
        section_id="sec-0002",
        deck=MethodologyCandidateDeck(candidates=()),
    )

    assert methodology_llm.existing_methodology_section_keys(out_path) == {
        ("source-9001", "sec-0002")
    }


@pytest.mark.asyncio
async def test_parallel_runner_writes_review_jsonl_and_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_dir = tmp_path / "repo" / "data" / "methodology_books" / "source-9001"
    jobs_path = package_dir / "llm_jobs" / "section_jobs.index.jsonl"
    jobs_path.parent.mkdir(parents=True)
    jobs = [
        {
            "job_id": "source-9001-sec-0001",
            "source_id": "source-9001",
            "section_id": "sec-0001",
            "private_payload_ref": "source-9001/llm_payloads/sec-0001.prompt.json",
        },
        {
            "job_id": "source-9001-sec-0002",
            "source_id": "source-9001",
            "section_id": "sec-0002",
            "private_payload_ref": "source-9001/llm_payloads/sec-0002.prompt.json",
        },
    ]
    jobs_path.write_text(
        "".join(json.dumps(job, ensure_ascii=False) + "\n" for job in jobs),
        encoding="utf-8",
    )

    class DummySessionScope:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def fake_extract(
        _session: object,
        _settings: object,
        *,
        job: dict[str, Any],
        **_kwargs: object,
    ) -> MethodologyCandidateDeck:
        section_id = str(job["section_id"])
        return MethodologyCandidateDeck.model_validate(
            {
                "candidates": [
                    {
                        "candidate_id": f"method.{section_id}",
                        "source_id": "source-9001",
                        "section_id": section_id,
                        "title": f"方法 {section_id}",
                        "category": "scene_design",
                        "scope": ["scene"],
                        "stage": ["planning"],
                        "core_claim": "场景方法必须能转成可检查合约。",
                        "framework_bindings": ["scene_card"],
                        "confidence": 0.8,
                    }
                ]
            }
        )

    monkeypatch.setattr(methodology_llm, "session_scope", lambda _settings: DummySessionScope())
    monkeypatch.setattr(methodology_llm, "extract_methodology_candidates_for_job", fake_extract)

    processed, failures = await methodology_llm.run_pending_methodology_section_jobs_parallel(
        package_dir=package_dir,
        repo_root=tmp_path / "repo",
        private_root=tmp_path / "private",
        settings=load_settings(),
        schema={},
        max_concurrency=2,
    )
    (
        processed_again,
        failures_again,
    ) = await methodology_llm.run_pending_methodology_section_jobs_parallel(
        package_dir=package_dir,
        repo_root=tmp_path / "repo",
        private_root=tmp_path / "private",
        settings=load_settings(),
        schema={},
        max_concurrency=2,
    )

    assert (processed, failures) == (2, 0)
    assert (processed_again, failures_again) == (0, 0)
    review_path = package_dir / "methodology_candidates.review.jsonl"
    assert methodology_llm.existing_methodology_section_keys(review_path) == {
        ("source-9001", "sec-0001"),
        ("source-9001", "sec-0002"),
    }
    assert len(load_methodology_candidates(review_path).candidates) == 2
