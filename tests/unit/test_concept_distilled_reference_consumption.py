from __future__ import annotations

import pytest

from bestseller.services import concept_tournament as ct


def test_active_raw_pool_prompt_consumes_verified_reference() -> None:
    marker = "VERIFIED-CRAFT-MARKER"
    system, _user = ct._build_raw_idea_pool_messages(
        genre="悬疑推理",
        sub_genre="民俗诡案",
        count=6,
        prompt_arm="author_pitch",
        distilled_reference_block=marker,
    )
    assert marker in system
    assert "只作建议" in system


def test_active_engine_prompt_consumes_verified_reference_without_authority() -> None:
    marker = "VERIFIED-CRAFT-MARKER"
    system, user = ct._build_engine_kernel_messages(
        genre="悬疑推理",
        sub_genre="民俗诡案",
        lane="纯题材直觉#机制变化",
        chapter_count=30,
        distilled_reference_block=marker,
    )
    assert marker in user
    assert "不是本书事实" in user
    assert marker not in system


def test_unverified_or_other_genre_reference_is_absent() -> None:
    system, user = ct._build_raw_idea_pool_messages(
        genre="东方玄幻",
        sub_genre="宗门经营",
        count=6,
        prompt_arm="author_pitch",
        distilled_reference_block="",
    )
    assert "成熟小说设计参考" not in system + user


@pytest.mark.asyncio
async def test_runner_passes_verified_reference_to_real_pool_entrypoint(monkeypatch) -> None:
    marker = "VERIFIED-RUN-CRAFT-MARKER"
    monkeypatch.setattr(
        ct,
        "_distilled_creative_reference_payload",
        lambda **_: (
            marker,
            {
                "aggregate_key": "suspense-mystery",
                "source_count": 1,
                "source_ids": ["source-0001"],
                "verification": "local_source_manifest_v1",
            },
        ),
    )
    prompts: list[str] = []

    async def generator(system: str, user: str):
        prompts.append(system + user)
        if '"ideas"' in user:
            return (
                '{"ideas":[{"lane":"纯题材直觉","seed":"一个人遇到异常处境",'
                '"graft":"机制在新场景中成立","opening":"当场做出选择",'
                '"why_it_keeps_moving":"选择带来后果","future_situations":["现场"]}]}',
                None,
            )
        return '{"concept":"一个可读的开局"}', None

    result = await ct.run_concept_tournament(
        None,
        None,
        genre="悬疑推理",
        sub_genre="民俗诡案",
        chapter_count=20,
        config={
            "enabled": True,
            "n_candidates": 1,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "minimal",
            "raw_idea_pool_multiplier": 1,
            "raw_idea_generation_batch_size": 1,
            "raw_idea_pool_topup_calls": 0,
        },
        generator=generator,
    )
    assert any(marker in prompt for prompt in prompts)
    assert result.distilled_reference_receipt["status"] == "consumed_by_concept_generation"
    assert result.distilled_reference_receipt["source_ids"] == ["source-0001"]
