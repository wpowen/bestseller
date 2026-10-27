"""Complete-source/tools capacity closure and the real commercial request builder."""
from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_review_transport as transport
from bestseller.services.commercial_source_evidence import assemble_commercial_sources
from bestseller.services.commercial_source_span_transport import decode_commercial_source_spans
from bestseller.services.commercial_review_tool import review_submission_tools
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.waiting_chapter_review_resume import _renderer_fence
from bestseller.settings import load_settings


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def specimen():
    bodies = ['\n\n'.join(
        f'第{i}号航海记录，观测员核实第{n}号月面标记，随后封存原始星图并登记，不增添任何新推断。'
        for i in range(1, 86 if n < 3 else 138)) for n in (1, 2, 3)]
    bodies[2] = '天文观测员0与天文观测员1登记星图。\n\n' + bodies[2]
    prior = [{'chapter_number': n, 'body': body} for n, body in enumerate(bodies[:2], 1)]
    source = {'story_bible': {'participants': [
        {'name': f'天文观测员{n}', 'knowledge_state': {'literal_source_observations': [
            {'body_anchors': list(build_critic_body_anchors(body)),
             'source_evidence': [{'source_anchor_ids': list(build_critic_body_anchors(body)),
                                  'quoted_text': body}]}]}}
        for n, body in enumerate(bodies[:2])]}}
    return bodies[2], prior, source


def assembled():
    body, prior, source = specimen()
    return assemble_commercial_sources(chapter_number=3, content_md=body,
                                       previous_chapters=prior, source=source)


def test_only_overflow_factors_exact_schema_and_roundtrips_entire_logical_packet(monkeypatch):
    sources = assembled()
    tools = review_submission_tools(sources)
    before = deepcopy((sources, tools))
    user = '完整来源：' + sources.text + '\n最后的原始指令必须保留。'
    monkeypatch.setattr(transport, 'MAX_PROMPT_CHARACTERS', 85000)
    with pytest.raises(transport.CommercialReviewTransportError, match='input_budget_exceeded'):
        transport.prepare_commercial_review_transport(
            '核查全部原文，不得增删来源。', user, sources=sources, tools=tools, output_tokens=8192)
    wire, actual_tools, receipt = transport.prepare_commercial_review_submission(
        '核查全部原文，不得增删来源。', user, sources=sources, tools=tools, output_tokens=8192)
    assert actual_tools == review_submission_tools(sources, compact=True) != tools
    assert receipt['capacity']['budget_passed'] and not receipt['capacity']['truncated']
    assert receipt['capacity']['total_token_budget'] == 65536
    assert receipt['capacity']['token_safety_factor'] == 1.5
    assert receipt['capacity']['output_reserve_tokens'] == 8192
    assert receipt['capacity']['combined_characters'] <= 85000
    assert receipt['encoding'] == 'commercial-spans-v4'
    transport.verify_commercial_review_transport(wire, sources=sources, receipt=receipt)
    encoded = json.loads(wire[receipt['packet_start']:receipt['packet_end']])
    decoded = decode_commercial_source_spans(encoded)
    assert canonical(decoded) == sources.text
    assert decoded['citation_catalog'] == json.loads(sources.text)['citation_catalog']
    assert wire.endswith('\n最后的原始指令必须保留。')
    assert (sources, tools) == before
    assert receipt['tool_schema_transport']['original_tools_sha256'] == hashlib.sha256(canonical(tools).encode()).hexdigest()
    assert receipt['tool_schema_transport']['wire_tools_sha256'] == hashlib.sha256(canonical(actual_tools).encode()).hexdigest()


def test_fitting_native_request_and_tools_stay_byte_exact():
    sources = assemble_commercial_sources(chapter_number=1, content_md='星图封存。', source={}, previous_chapters=[])
    tools = review_submission_tools(sources)
    user = '全部原文：' + sources.text
    wire, actual_tools, receipt = transport.prepare_commercial_review_submission(
        '独立核查。', user, sources=sources, tools=tools, output_tokens=8192)
    assert wire == user and actual_tools == tools and receipt['encoding'] == 'identity'
    assert 'tool_schema_transport' not in receipt


def test_overflow_cannot_replace_foreign_tools_or_remove_uncompressible_final_feedback(monkeypatch):
    sources = assembled()
    tools = review_submission_tools(sources)
    monkeypatch.setattr(transport, 'MAX_PROMPT_CHARACTERS', 85000)
    foreign = deepcopy(tools)
    foreign[0]['function']['description'] += ' foreign protocol'
    with pytest.raises(transport.CommercialReviewTransportError, match='input_budget_exceeded'):
        transport.prepare_commercial_review_submission('S', sources.text, sources=sources,
                                                      tools=foreign, output_tokens=8192)
    with pytest.raises(transport.CommercialReviewTransportError, match='input_budget_exceeded'):
        transport.prepare_commercial_review_submission('S', sources.text + '最后内容' * 30000,
            sources=sources, tools=tools, output_tokens=8192)


@pytest.mark.asyncio
async def test_actual_judge_contract_and_physical_request_use_same_final_compact_tools(monkeypatch):
    class Captured(BaseException):
        pass
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError('No database/claim authorized: ' + name)
    captured = {}
    actual = transport.prepare_commercial_review_submission
    def prepare(system, logical_user, **kwargs):
        result = actual(system, logical_user, **kwargs)
        captured.update(system=system, user=logical_user, kwargs=kwargs, prepared=result)
        return result
    async def gateway(session, settings, request):
        captured['request'] = request
        raise Captured
    # Includes the real native framing; still excludes the unfactored tools.
    monkeypatch.setattr(transport, 'MAX_PROMPT_CHARACTERS', 90000)
    monkeypatch.setattr(transport, 'prepare_commercial_review_submission', prepare)
    monkeypatch.setattr(judge, 'complete_text', gateway)
    body, prior, source = specimen()
    settings = load_settings(env={})
    try:
        result = await judge.judge_chapter_commercial_quality(NoDatabase(), settings,
            chapter_number=3, content_md=body, generation_input=source,
            previous_chapters=prior, sample_slot='chapter-quality.independent-v2:0')
    except Captured:
        pass
    else:
        raise AssertionError(result.model_dump(mode="json"))
    request = captured['request']
    wire, tools, receipt = captured['prepared']
    assert request.user_prompt == wire and request.tools == tools
    assert tools == review_submission_tools(captured['kwargs']['sources'], compact=True)
    contract = request.metadata['commercial_evaluation_contract']
    assert contract['tools_sha256'] == hashlib.sha256(canonical(tools).encode()).hexdigest()
    assert contract['instruction_prefix_sha256'] == hashlib.sha256(canonical(
        captured['user'].split(captured['kwargs']['sources'].text, 1)[0]).encode()).hexdigest()
    assert request.metadata['commercial_source_transport'] == receipt
    assert request.metadata['commercial_source_input_receipt']['budget_passed']
    assert request.tool_choice == 'required' and request.max_tokens_override == 8192
    assert _renderer_fence()  # Actual source-derived prerequisite prefix remains exact.


@pytest.mark.parametrize('fault', ['wire_hash', 'original_hash', 'closed_ids', 'capacity_tools'])
def test_schema_transport_receipt_cannot_be_forged(fault, monkeypatch):
    sources = assembled()
    monkeypatch.setattr(transport, 'MAX_PROMPT_CHARACTERS', 85000)
    wire, tools, receipt = transport.prepare_commercial_review_submission(
        'S', sources.text, sources=sources, tools=review_submission_tools(sources), output_tokens=8192)
    if fault == 'wire_hash':
        receipt['tool_schema_transport']['wire_tools_sha256'] = '0' * 64
    elif fault == 'original_hash':
        receipt['tool_schema_transport']['original_tools_sha256'] = '0' * 64
    elif fault == 'closed_ids':
        receipt['tool_schema_transport']['closed_id_set_preserved'] = False
    else:
        receipt['capacity']['tool_schema_sha256'] = '0' * 64
    with pytest.raises(transport.CommercialReviewTransportError, match='tool_schema_binding'):
        transport.verify_commercial_review_transport(wire, sources=sources, receipt=receipt)
