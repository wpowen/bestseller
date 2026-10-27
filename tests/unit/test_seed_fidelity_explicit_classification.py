"""Real async fidelity-normalization boundary, offline gateway tripwire only.

The production regression reason is copied from the human-provided native
run diagnosis. These payloads are fixtures, not new provider verdicts.
"""

# ruff: noqa: RUF001 -- Deliberate Chinese evidence/negation fixtures.
from copy import deepcopy
import socket
from uuid import uuid4

import pytest

from bestseller.services import conception, llm

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
NATIVE_RUN = "ce1f524a-79e1-4694-a9b1-2896cb989b48"
NATIVE_REASON = (
    "四段结构得到保留，但明确篇幅数额在全部候选资产中均未被保留，"
    "属于删除用户明确的数量约束，而不是对故事内容的合理扩写。"
)
POSITIVE_LEGACY = "终稿保留并细化，符合扩写，不构成替换。"


class ExternalCallTripwire(BaseException):
    pass


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise ExternalCallTripwire("classification boundary forbids provider/network/DB")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(llm, "complete_text", denied)
    monkeypatch.setattr(conception, "complete_text", denied)


async def judge(monkeypatch, item, *, stage="final_material", passed=False):
    calls = []
    payload = {
        "passed": passed,
        "hard_conflicts": [deepcopy(item)],
        "compatible_extensions": [],
        "preserved_facts": ["四段结构"],
        "repair_directives": ["恢复用户明确篇幅数额。"],
    }
    before = deepcopy(payload)
    run_id = uuid4()  # Explicit synthetic fixture UUID, never the real paid run.

    async def stub(session, settings, **kwargs):
        calls.append(kwargs)
        return payload, [run_id]

    monkeypatch.setattr(conception, "_llm_call_json", stub)
    report, ids = await conception._judge_explicit_concept_seed_fidelity(
        object(),
        object(),
        concept_seed="修理工沈砚的故事分四段，每段两千字，共八千字。",
        final_result={
            "premise": "修理工沈砚经历四个连续阶段。",
            "synopsis": "沈砚在四个阶段完成修理工作。",
        },
        ctx={"language": "zh-CN", "genre": "都市", "chapter_count": 4},
        fidelity_stage=stage,
    )
    assert payload == before, "Normalization must not mutate the raw paid/fixture payload"
    assert ids == [run_id] and len(calls) == 1 and calls[0]["role"] == "critic"
    return report, calls[0]


@pytest.mark.parametrize(
    "stage,template",
    [
        ("final_material", "conception_final_material_seed_fidelity_judge"),
        ("tournament_winner", "conception_explicit_seed_fidelity_judge"),
        ("reconciled_winner", "conception_reconciled_seed_fidelity_judge"),
        ("preproject_materialized", "conception_preproject_materialized_seed_fidelity_judge"),
    ],
)
async def test_native_hard_quantity_conflict_survives_reason_keyword(monkeypatch, stage, template):
    item = {
        "classification": "hard_conflict",
        "fact": "每段两千字，共八千字",
        "generated": "全部资产仅保留四段，未保留篇幅数额",
        "reason": NATIVE_REASON,
    }
    report, call = await judge(monkeypatch, item, stage=stage)
    assert report["passed"] is False
    assert report["hard_conflicts"] == [item] and report["compatible_extensions"] == []
    assert call["template"] == template


@pytest.mark.parametrize(
    "reason",
    [
        "不构成替换。",
        "并未替换。",
        "合理扩写。",
        POSITIVE_LEGACY,
        "并非合理扩写，而是删除用户事实。",
        "不是合理扩写。",
    ],
)
@pytest.mark.parametrize("classification", ["hard_conflict", " HARD_CONFLICT "])
async def test_explicit_hard_label_wins_even_over_positive_reason_or_pass_flag(
    monkeypatch,
    reason,
    classification,
):
    report, _ = await judge(
        monkeypatch, {"classification": classification, "reason": reason}, passed=True
    )
    assert report["passed"] is False and report["compatible_extensions"] == []
    assert report["hard_conflicts"][0]["classification"] == "hard_conflict"
    assert report["hard_conflicts"][0]["reason"] == reason


@pytest.mark.parametrize(
    "reason",
    [
        NATIVE_REASON,
        "并非合理扩写。",
        "不是合理扩写。",
        "并非不构成替换。",
        "并不是合理扩写，不构成替换。",
        "不能证明不构成替换，属于合理扩写。",
        "虽然符合扩写，但仍删除用户数额。",
        "‘合理扩写’不能解释删除数额。",
        "未保留数量，不构成替换只是模型自称。",
        "合理扩写。",
        "不构成替换。",
    ],
)
async def test_legacy_negated_quoted_or_ambiguous_conclusion_stays_hard(monkeypatch, reason):
    report, _ = await judge(monkeypatch, {"reason": reason})
    assert report["passed"] is False and report["compatible_extensions"] == []
    assert report["hard_conflicts"][0]["reason"] == reason


@pytest.mark.parametrize(
    "reason",
    [
        POSITIVE_LEGACY,
        "符合扩写，不构成替换。",
        "属于合理扩写，并未替换。",
        "不构成替换，属于合理扩写。",
    ],
)
async def test_unlabeled_unambiguous_legacy_positive_conclusion_remains_compatible(
    monkeypatch, reason
):
    report, _ = await judge(monkeypatch, {"reason": reason})
    assert report["passed"] is True and report["hard_conflicts"] == []
    assert report["compatible_extensions"] == [
        {"classification": "compatible_extension", "reason": reason}
    ]


@pytest.mark.parametrize("classification", ["unknown", "potential_conflict", False, 0])
async def test_unknown_or_invalid_explicit_label_cannot_take_legacy_compatibility(
    monkeypatch, classification
):
    report, _ = await judge(
        monkeypatch, {"classification": classification, "reason": POSITIVE_LEGACY}
    )
    assert report["passed"] is False and report["compatible_extensions"] == []


@pytest.mark.parametrize("classification", ["preserved", "compatible_extension", "extension"])
async def test_explicit_compatible_classification_stays_owned_by_judge(monkeypatch, classification):
    item = {"classification": classification, "reason": "原生判官的明确兼容分类。"}
    report, _ = await judge(monkeypatch, item)
    assert report["passed"] is True and report["hard_conflicts"] == []
    assert report["compatible_extensions"] == [item]
