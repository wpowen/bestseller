"""Citation capacity follows the exact body catalog without dropping late issues."""
from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services import critic_evidence_gate as gate

pytestmark = pytest.mark.unit


def _body(count, language):
    phrase = 'Unrelated ocean survey vessel records a temperature' if language == 'en' else '独立天文台正在记录一颗新恒星'
    return '\n\n'.join(f'{phrase} {i}.' for i in range(count))


def _items(body):
    return [{'id': a['id'], 'dimension': 'continuity', 'reason': f'Preserve independent observation {i}.'}
        for i, a in enumerate(gate.build_critic_body_anchors(body))]


def _raw(items, lines=False):
    return ('COMMENTARY: Independent issues must remain individually bound to exact current source text.\n'
        'EVIDENCE_ANCHORS:\n' + ('\n'.join(f'{x["id"]} | {x["dimension"]} | {x["reason"]}' for x in items)
        if lines else json.dumps(items, ensure_ascii=False)))


@pytest.mark.parametrize('count', [1, 8, 9, 26, 41])
@pytest.mark.parametrize('language', ['zh', 'en'])
@pytest.mark.parametrize('lines', [False, True])
def test_all_catalog_citations_survive_with_exact_text_offsets_and_hashes(count, language, lines):
    body = _body(count, language)
    items = _items(body)
    original = deepcopy(items)
    raw = _raw(items, lines)
    before = raw.encode()
    report = gate.validate_critic_commentary(raw, chapter_text=body)
    assert report.passed and len(report.citations) == count
    assert [c['raw_citation'] for c in report.citations] == original
    for citation in report.citations:
        assert citation['text'] == body[citation['start']:citation['end']]
        assert citation['offset_unit'] == 'unicode-codepoint'
        receipt = citation['normalization_receipt']
        assert receipt['policy_version'] == 'critic-catalog-bounded-anchor-v2'
        assert receipt['body_sha256'] == hashlib.sha256(body.encode()).hexdigest()
        assert receipt['source_text_sha256'] == hashlib.sha256(citation['text'].encode()).hexdigest()
        assert receipt['raw_commentary_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert gate.validate_critic_commentary(raw, chapter_text=body) == report
    assert items == original and raw.encode() == before


@pytest.mark.parametrize('bad', ['foreign_body', 'outside', 'duplicate', 'quote_field', 'empty_reason', 'malformed_id', 'extra_item'])
def test_invalid_late_item_rejects_whole_batch_instead_of_trimming_ninth(bad):
    body = _body(9, 'zh')
    items = _items(body)
    if bad == 'foreign_body':
        items[-1]['id'] = gate.build_critic_body_anchors(body + 'changed')[0]['id']
    elif bad == 'outside':
        items[-1]['id'] = items[-1]['id'].replace('P0009', 'P0010')
    elif bad == 'duplicate':
        items[-1]['id'] = items[0]['id']
    elif bad == 'quote_field':
        items[-1]['text'] = 'A fabricated quotation must not become body evidence.'
    elif bad == 'empty_reason':
        items[-1]['reason'] = ''
    elif bad == 'malformed_id':
        items[-1]['id'] = 'P0009'
    else:
        items.append({**items[-1], 'id': items[0]['id'] + '–P0002'})
    raw = _raw(items)
    report = gate.validate_critic_commentary(raw, chapter_text=body)
    assert not report.passed and report.citations == ()
    assert report.findings[0].code == gate.CRITIC_BODY_EVIDENCE_MISMATCH


@pytest.mark.parametrize('count,passes', [(256, True), (257, False)])
def test_catalog_capacity_does_not_relax_per_range_bound(count, passes):
    body = _body(count, 'en')
    items = _items(body)
    items[-1]['id'] = items[0]['id'] + f'–P{count:04d}'
    report = gate.validate_critic_commentary(_raw(items), chapter_text=body)
    assert report.passed is passes
    if passes:
        assert len(report.citations) == count
        assert len(report.citations[-1]['anchors']) == 256
    else:
        assert report.citations == ()


def test_new_suffix_declares_catalog_capacity_and_keeps_legacy_suffix_unchanged():
    suffix = gate.build_critic_evidence_prompt_suffix(language='en', anchored=True)
    assert 'number of entries in this exact BODY_ANCHOR_CATALOG' in suffix
    assert 'at most 256 paragraphs' in suffix and '1-8' not in suffix
    assert 'EVIDENCE:' in gate.build_critic_evidence_prompt_suffix(language='en', anchored=False)
