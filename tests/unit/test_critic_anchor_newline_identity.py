"""Paragraph IDs bind original Unicode offsets for every source newline form."""
import hashlib
import re

import pytest

from bestseller.services.critic_evidence_gate import build_critic_body_anchors

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
@pytest.mark.parametrize('blank', ['', '  ', '\t'])
def test_exact_original_offsets_and_hash_without_newline_normalization(newline, blank):
    parts = ['岑遥记录了潮位🙂。', 'Ada saved Cafe\u0301.', 'A third distinct record.']
    body = (newline + blank + newline).join(parts)
    anchors = build_critic_body_anchors(body)
    assert [a['text'] for a in anchors] == parts
    digest = hashlib.sha256(body.encode()).hexdigest()
    for index, anchor in enumerate(anchors, 1):
        assert anchor['id'] == f'D{digest[:16]}:P{index:04d}'
        assert anchor['body_sha256'] == digest
        assert body[anchor['start']:anchor['end']] == parts[index - 1]


@pytest.mark.parametrize('body', [
    'First.\n\nSecond.', 'Line1.\nLine2.\n\nLine3.', 'A.\n', ' A.\n\n B.',
    'A.\n \nB.', 'Cafe\u0301.\n\n🙂。', '', '\n\n', 'A.\n\nB.\n',
])
def test_existing_lf_catalog_text_offsets_and_ids_are_unchanged(body):
    old = list(re.finditer(r'\S[^\n]*(?:\n(?![ \t]*\n)[^\n]*)*', body))
    anchors = build_critic_body_anchors(body)
    assert [(a['start'], a['end'], a['text']) for a in anchors] == [
        (m.start(), m.end(), m.group()) for m in old]
