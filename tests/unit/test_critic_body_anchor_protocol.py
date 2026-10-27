import json
import hashlib

import pytest

from bestseller.services import critic_evidence_gate as gate
from bestseller.services.critic_evidence_gate import build_critic_body_anchors, validate_critic_commentary

BODY = '甲将封好的信交给乙。\n\n乙把信收进口袋，走向门口。\n窗外仍然下着雨。'


def commentary(items):
    return ('VERDICT: pass\nCOMMENTARY: 这份正文的动作与因果能从被选段落获得支持。\n'
            'EVIDENCE_ANCHORS: ' + json.dumps(items, ensure_ascii=False))


def test_ids_resolve_to_exact_body_offsets_without_treating_explanatory_plan_quotes_as_prose():
    anchors = build_critic_body_anchors(BODY)
    assert len(anchors) == 2
    for a in anchors:
        assert BODY[a['start']:a['end']] == a['text']
    result = validate_critic_commentary(commentary([{'id': anchors[1]['id'], 'dimension': 'causality',
        'reason': '收信和离场已经发生；规划的“下一章揭信”不是本章事件。'}]), chapter_text=BODY)
    assert result.passed
    assert result.citations[0]['text'] == anchors[1]['text']
    assert result.citations[0]['start'] == anchors[1]['start']


def test_unknown_empty_duplicate_or_forged_quote_rejected():
    valid = {'id': build_critic_body_anchors(BODY)[0]['id'], 'dimension': 'causality', 'reason': '交接行为明确。'}
    for items in ([], [valid, valid], [{**valid, 'id': 'P0099'}],
                  [{**valid, 'text': '我伪造的正文'}], [{**valid, 'reason': ''}]):
        assert not validate_critic_commentary(commentary(items), chapter_text=BODY).passed
    assert not validate_critic_commentary(commentary([valid]), chapter_text='').passed


def test_legacy_fabricated_quote_stays_invalid():
    result = validate_critic_commentary('COMMENTARY: 有足够长的评论，但实际引用了一句从未发生过的内容。\n'
        'EVIDENCE: “这一句并不在当前正文之中。”', chapter_text=BODY)
    assert not result.passed


def test_anchor_from_another_body_cannot_be_reused():
    old = build_critic_body_anchors(BODY)[0]
    result = validate_critic_commentary(commentary([{'id': old['id'], 'dimension': 'continuity',
        'reason': '引用了不同版本的正文。'}]), chapter_text=BODY + '正文已变化。')
    assert not result.passed


def test_line_protocol_keeps_quotes_in_reasons_separate_from_body():
    anchor = build_critic_body_anchors(BODY)[0]
    response = ('VERDICT: pass\nCOMMENTARY: 本段明确给出交接行为，有助理解下一个行动。\n'
        'EVIDENCE_ANCHORS:\n' + anchor['id'] + ' | causality | 计划的"揭示信件"不等于已发生事件。')
    report = validate_critic_commentary(response, chapter_text=BODY)
    assert report.passed and report.citations[0]['text'] == anchor['text']


@pytest.mark.parametrize('count,paragraph', [(26, '档案管理员核对信件'), (7, 'The diver checks the oxygen gauge'),
                                           (5, '行星探测器记录磁场')])
@pytest.mark.parametrize('separator', ['–', '-', '—'])
@pytest.mark.parametrize('line_protocol', [False, True])
def test_contiguous_range_preserves_all_issues_and_exact_source(count, paragraph, separator, line_protocol):
    body = '\n\n'.join(f'{paragraph} {i}。\nNext line {i}.' for i in range(count))
    anchors = build_critic_body_anchors(body)
    key = anchors[0]['id'] + separator + anchors[-1]['id'].split(':')[1]
    # A whole-body issue may overlap independent issues citing particular paragraphs.
    items = [{'id': anchors[index]['id'], 'dimension': 'continuity', 'reason': f'问题 {index} remains unresolved'}
             for index in sorted({0, count // 2, count - 1})]
    items.append({'id': key, 'dimension': '目标兑现', 'reason': '全文检查依然发现缺项；do not delete this issue.'})
    raw = commentary(items) if not line_protocol else (
        'COMMENTARY: Each independent issue must remain bound to this exact current draft.\nEVIDENCE_ANCHORS:\n'
        + '\n'.join(f'{x["id"]} | {x["dimension"]} | {x["reason"]}' for x in items))
    raw = '\n ' + raw + '\n'
    report = validate_critic_commentary(raw, chapter_text=body)
    assert report.passed
    assert len(report.citations) == len(items) == 4
    assert [c['raw_citation'] for c in report.citations] == items
    whole = report.citations[-1]
    assert whole['id'] == whole['raw_id'] == key
    assert whole['anchors'] == list(anchors)
    assert body[whole['start']:whole['end']] == whole['text'] == body
    receipt = whole['normalization_receipt']
    assert receipt['policy_version'] == gate.CRITIC_ANCHOR_ADAPTER_POLICY
    assert receipt['evidence_kind'] == 'derived_anchor_validation'
    assert receipt['anchor_ids'] == [a['id'] for a in anchors]
    assert receipt['raw_commentary_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert receipt['source_text_sha256'] == receipt['body_sha256'] == hashlib.sha256(body.encode()).hexdigest()
    assert validate_critic_commentary(raw, chapter_text=body) == report
    # Receipts survive the existing reviews citation serialization without wiring changes.
    assert json.loads(json.dumps(list(report.citations)))[-1]['normalization_receipt'] == receipt


def test_fully_qualified_same_body_endpoints_resolve_exact_subrange():
    body = '\n\n'.join(f'Unrelated seafloor survey station {i}.' for i in range(9))
    anchors = build_critic_body_anchors(body)
    raw_id = anchors[2]['id'] + '–' + anchors[5]['id']
    item = {'id': raw_id, 'dimension': 'evidence', 'reason': 'The same-body subrange supports this issue.'}
    report = validate_critic_commentary(commentary([item]), chapter_text=body)
    assert report.passed
    citation = report.citations[0]
    assert citation['anchors'] == list(anchors[2:6])
    assert citation['text'] == body[anchors[2]['start']:anchors[5]['end']]


@pytest.mark.parametrize('bad', ['foreign', 'outside', 'reverse', 'zero', 'equal', 'noncanonical', 'unbounded'])
def test_invalid_ranges_fail_closed_without_partial_issues(bad):
    body = '\n\n'.join(f'Independent weather observation {i}.' for i in range(257))
    anchors = build_critic_body_anchors(body)
    prefix = anchors[0]['id'].split(':')[0]
    suffixes = {'foreign': '–D0000000000000000:P0002', 'outside': '–P0258',
                'reverse': '–P0001', 'zero': '–P0000', 'equal': '–P0002',
                'noncanonical': '–P00003', 'unbounded': '–P0257'}
    key = f'{prefix}:P0002' + suffixes[bad]
    if bad == 'unbounded':
        key = anchors[0]['id'] + suffixes[bad]
    item = {'id': anchors[0]['id'], 'dimension': 'evidence', 'reason': 'Retain the finding.'}
    report = validate_critic_commentary(commentary([item, {**item, 'id': key}]), chapter_text=body)
    assert not report.passed and report.citations == ()
    assert report.findings[0].code == gate.CRITIC_BODY_EVIDENCE_MISMATCH


def test_range_bound_inclusive_and_duplicate_aliases_rejected():
    body = '\n\n'.join(f'Satellite telemetry {i}.' for i in range(256))
    anchors = build_critic_body_anchors(body)
    item = {'id': anchors[0]['id'] + '–P0256', 'dimension': 'evidence', 'reason': 'Telemetry is missing a signal.'}
    assert validate_critic_commentary(commentary([item]), chapter_text=body).passed
    alias = {**item, 'id': anchors[0]['id'] + '-' + anchors[-1]['id']}
    assert not validate_critic_commentary(commentary([item, alias]), chapter_text=body).passed


@pytest.mark.parametrize('corruption', ['missing_middle', 'duplicate_id', 'offset', 'hash', 'text', 'order'])
def test_range_requires_unique_complete_and_exact_catalog(monkeypatch, corruption):
    body = 'Archive entry one.\n\nArchive entry two.\n\nArchive entry three.'
    catalog = [dict(a) for a in build_critic_body_anchors(body)]
    key = catalog[0]['id'] + '–P0003'
    if corruption == 'missing_middle':
        catalog.pop(1)
    elif corruption == 'duplicate_id':
        catalog.append(dict(catalog[1]))
    elif corruption == 'order':
        catalog[1]['start'], catalog[1]['end'] = catalog[0]['start'], catalog[0]['end']
        catalog[1]['text'] = catalog[0]['text']
    else:
        field = {'offset': 'end', 'hash': 'body_sha256', 'text': 'text'}[corruption]
        catalog[1][field] = -1 if corruption == 'offset' else 'changed'
    monkeypatch.setattr(gate, 'build_critic_body_anchors', lambda _: tuple(catalog))
    item = {'id': key, 'dimension': 'archive', 'reason': 'All intermediate records are necessary.'}
    assert not validate_critic_commentary(commentary([item]), chapter_text=body).passed


@pytest.mark.parametrize('ambiguity', ['duplicate_json_key', 'json_tail', 'multiple_blocks'])
def test_ambiguous_protocol_never_drops_an_issue(ambiguity):
    key = build_critic_body_anchors(BODY)[0]['id']
    item = {'id': key, 'dimension': 'source', 'reason': 'Every issue must be retained exactly.'}
    raw = commentary([item])
    if ambiguity == 'duplicate_json_key':
        raw = raw.replace('"id":', '"id": "forged", "id":', 1)
    elif ambiguity == 'json_tail':
        raw += '\n' + key + ' | source | A second issue cannot be silently discarded.'
    else:
        raw += '\nEVIDENCE_ANCHORS:\n' + key + ' | source | Duplicate block is ambiguous.'
    assert not validate_critic_commentary(raw, chapter_text=BODY).passed


def test_range_from_previous_body_stays_invalid_without_mutating_raw():
    anchors = build_critic_body_anchors(BODY)
    item = {'id': anchors[0]['id'] + '–P0002', 'dimension': 'source', 'reason': 'The old body must stay bound.'}
    raw = commentary([item])
    snapshot = raw.encode()
    assert validate_critic_commentary(raw, chapter_text=BODY).passed
    assert not validate_critic_commentary(raw, chapter_text=BODY + '\nNew event.').passed
    assert raw.encode() == snapshot
