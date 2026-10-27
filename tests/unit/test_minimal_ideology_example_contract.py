"""Neutral producers never fabricate ideology; exact author clauses still survive."""

import json

import pytest

from bestseller.domain.ideology import ideology_kernel_from_dict
from bestseller.services.ideology_kernel import (
    build_ideology_user_prompt,
    fallback_ideology_kernel,
    parse_ideology_kernel,
)
from test_ideology_cost_source_projection import author_metadata

pytestmark = pytest.mark.unit


def assert_neutral(payload):
    kernel = ideology_kernel_from_dict(payload)
    assert kernel.version == 2
    assert kernel.cosmic_premise == kernel.thesis_statement == kernel.core_question == ""
    assert kernel.primary_motif is None and kernel.hidden_endgame_motif is None
    assert kernel.belief_arc is None
    assert kernel.secondary_motifs == kernel.sub_themes == kernel.cost_system == []
    assert kernel.motif_to_world_bindings == kernel.forbidden_resolutions == []
    assert kernel.layer_coverage == {} and kernel.per_volume_thesis_pressure == []


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["standard", "minimal", "external"])
async def test_runtime_fallback_is_neutral_and_never_pays_for_a_model(monkeypatch, style):
    from bestseller.services import ideology_kernel as service

    async def forbidden(*args, **kwargs):
        pytest.fail("A neutral ideology fallback must not invoke the model gateway")

    from bestseller.services import llm

    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(service, "complete_text", forbidden, raising=False)
    kernel = await service.derive_ideology_kernel(
        None, None, premise="远征队寻找失落城市", cost_style=style
    )
    assert_neutral(kernel.model_dump(mode="json"))
    assert kernel.cost_style == ""  # A scalar argument does not prove an author entry.


@pytest.mark.parametrize("response", ["{}", "not json", '{"thesis_statement":"MODEL_PROPOSAL"}'])
def test_parse_cannot_backfill_or_promote_unselected_model_ideology(response):
    kernel = parse_ideology_kernel(response, premise="修钟人收到远洋信件")
    assert_neutral(kernel.model_dump(mode="json"))


@pytest.mark.parametrize("premise", ["少年修符重振家族", "末世小队修复避难所", "侦探追查失踪列车"])
@pytest.mark.parametrize("seed", ["alpha", "beta", "gamma", "delta"])
@pytest.mark.parametrize("language", ["zh", "en"])
def test_neutral_example_and_request_never_invent_a_cost_or_theme(premise, seed, language):
    payload = fallback_ideology_kernel(premise=premise, seed=seed, cost_style="minimal")
    assert_neutral(payload)
    assert payload["cost_style"] == ""
    prompt = build_ideology_user_prompt(
        premise=premise, seed=seed, cost_style="minimal", language=language
    )
    for forbidden in ("短暂脱力", "睡一觉能缓过来", "具身代价", "取消代价系统", "天地不仁", "寿元折损"):
        assert forbidden not in json.dumps(payload, ensure_ascii=False) + prompt


@pytest.mark.parametrize("style", ["standard", "external", "minimal"])
def test_cost_only_entry_never_populates_ideology_fields(style):
    metadata = author_metadata(style)
    payload = fallback_ideology_kernel(premise="远征队寻找失落城市", metadata=metadata)
    assert_neutral(payload)
    assert payload["cost_style"] == style


@pytest.mark.asyncio
@pytest.mark.parametrize("style", [None, "standard", "external", "minimal"])
async def test_exact_author_theme_survives_producer_and_parse_without_invented_structure(monkeypatch, style):
    from bestseller.services import ideology_kernel as service

    fields = {"thesis_statement": "公开失误才能重新获得船员信任。", "core_question": "修钟人是否愿意承担自己的失误？"}
    metadata = author_metadata(style, ideology_fields=fields)

    async def forbidden(*args, **kwargs):
        pytest.fail("Exact author clauses must not require a paid model rewrite")

    from bestseller.services import llm

    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(service, "complete_text", forbidden, raising=False)
    candidates = (
        ideology_kernel_from_dict(fallback_ideology_kernel(metadata=metadata)),
        parse_ideology_kernel('{"thesis_statement":"MODEL_REPLACEMENT","primary_motif":{}}', metadata=metadata),
        await service.derive_ideology_kernel(None, None, premise="船员修钟", metadata=metadata),
    )
    for kernel in candidates:
        assert kernel.thesis_statement == fields["thesis_statement"]
        assert kernel.core_question == fields["core_question"]
        assert kernel.primary_motif is None and kernel.belief_arc is None
        assert kernel.hidden_endgame_motif is None and kernel.cost_system == []
        assert kernel.cost_style == (style or "")
    prompt = build_ideology_user_prompt(premise="船员修钟", metadata=metadata)
    assert all(value in prompt for value in fields.values())
    assert "MODEL_REPLACEMENT" not in prompt
