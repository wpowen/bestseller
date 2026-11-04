"""Pure, model-free dialogue diagnostics; no imports from either book engine.

dialogues(text) returns outer, balanced “”, "", 「」 or 『』 quotations in source
order. start/end are Python Unicode offsets for the interior, [start, end);
quote_start/quote_end include delimiters. IDs are D<number>-<start>-<end>.
Quotation marks alone do not establish spoken dialogue or speaker identity:
speaker is always 'Unknown'. Nested quotations are scanned once, never edited.
spoken_dialogue(text, row) separately classifies high-confidence speech for
offline edit filtering; it does not alter the scanner, metrics or edit API.

metrics(text) returns JSON values only. chars counts all Han characters;
dialogue_ratio = quoted Han / all Han. Other diagnostics inspect quotations
only. Pattern counts are lexical heuristics, not literary quality judgments.
Sentence lengths count Han and split on 。！？.!?；; or newline. Variance is
population variance (zero for fewer than two nonempty Han sentences). It is
an overall dialogue sentence-length proxy for similar voices, not a per-speaker
measurement; speaker identity remains Unknown.

apply_dialogue_edits(text, edits) accepts a list of {id, original, text} or exact
{start, end, original, text} interior spans. original must match the current
interior; optional start/end/id assertions must also match. Returns
(new_text, accepted), where accepted contains normalized
edits with original-source offsets, in source order. Invalid edits are skipped;
first valid edit per quotation wins. Blank replacements are forbidden;
one-character replacements are legal. New quote marks, line breaks and
new/increased English word occurrences are forbidden.
Everything outside accepted quotation interiors is preserved byte-for-byte
when encoded identically. The module loads rules from the adjacent data YAML
once on import; these functions neither print nor access files. Reimport the
module (or restart the process) after editing rules for offline recomputation.
"""
from collections import Counter
from pathlib import Path
import re
from statistics import pvariance
import unicodedata

import yaml


_RULES = yaml.safe_load((Path(__file__).resolve().parents[1]
                        / 'data/dialogue_experiment.yaml').read_text(encoding='utf-8'))['rules']

_HAN = re.compile('[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
                  '\U00020000-\U0002fa1f\U00030000-\U000323af]')
_PAIRS = {'“': '”', '"': '"', '「': '」', '『': '』', '‘': '’'}
_QUOTE_MARKS = frozenset('“”"「」『』‘’＂')
_SENTENCES = re.compile(r'[。！？.!?；;\n]+')
_LINE_BREAKS = re.compile(r'\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]')
_ENGLISH = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
_ABSTRACT = re.compile('|'.join(re.escape(word) for word in
                              sorted(_RULES['abstract_nouns'], key=len, reverse=True)))
_CONTRAST = re.compile(_RULES['contrast_pattern'])
_APHORISM = re.compile(_RULES['aphorism_pattern'])
_PARALLEL_PREDICATE = re.compile(_RULES['parallel_predicate_pattern'])
_SPOKEN = {key: re.compile(pattern)
           for key, pattern in _RULES['spoken_dialogue'].items()}


def _han_count(text):
    return len(_HAN.findall(text))


def _escaped(text, offset):
    backslashes = 0
    offset -= 1
    while offset >= 0 and text[offset] == '\\':
        backslashes += 1
        offset -= 1
    return backslashes % 2 == 1


def dialogues(text):
    """Scan balanced outer quotations; see module docstring for source schema."""
    rows = []
    stack = []
    opening = 0
    nested = False
    malformed = False
    for offset, char in enumerate(text):
        if char == '"' and _escaped(text, offset):
            continue
        if not stack:
            if char in _PAIRS and char != '‘':
                opening = offset
                stack.append(_PAIRS[char])
                nested = malformed = False
            continue
        if char == stack[-1]:
            stack.pop()
            if not stack and not malformed:
                rows.append({
                    'id': f'D{len(rows) + 1}-{opening + 1}-{offset}',
                    'start': opening + 1, 'end': offset,
                    'quote_start': opening, 'quote_end': offset + 1,
                    'text': text[opening + 1:offset],
                    'speaker': 'Unknown', 'nested': nested,
                })
        elif char in _PAIRS:
            stack.append(_PAIRS[char])
            nested = True
        elif char in _PAIRS.values():
            malformed = True
    return rows


def spoken_dialogue(text, row):
    """Speech whitelist with general colon cues; labels win, speaker is Unknown."""
    if not isinstance(row, dict) or row.get('nested'):
        return False
    if not any(row == candidate for candidate in dialogues(text)):
        return False
    before, after = text[:row['quote_start']], text[row['quote_end']:]
    if (_SPOKEN['label_prefix_pattern'].search(before)
            or _SPOKEN['label_suffix_pattern'].search(after)):
        return False
    prefix = _SPOKEN['paragraph_break_pattern'].split(before)[-1]
    return (not prefix.strip()
            or bool(_SPOKEN['speech_prefix_pattern'].search(prefix))
            or bool(_SPOKEN['action_prefix_pattern'].search(prefix)))


def _parallel(sentence):
    """Repeated heads, or balanced clause heads/tails/internal predicates."""
    clauses = [''.join(_HAN.findall(c)) for c in re.split('[，,、：:]', sentence)]
    clauses = [c for c in clauses if c]
    if len(clauses) < 2:
        return False
    # A shared two-character head is a conservative repetition signal.
    for start in range(len(clauses) - 2):
        group = clauses[start:start + 3]
        if (min(map(len, group)) >= 3
                and len({c[:2] for c in group}) == 1
                and max(map(len, group)) - min(map(len, group)) <= 2):
            return True
    if len(clauses) == 2:
        left, right = clauses
        if len(left) == len(right) and len(left) >= 4:
            for match in _PARALLEL_PREDICATE.finditer(left):
                if (0 < match.start() and match.end() < len(left)
                        and right[match.start():match.end()] == match.group()):
                    return True
            heads = re.sub('^(?:我们|你们|他们|她们|你|我|他|她)', '', left)
            other = re.sub('^(?:我们|你们|他们|她们|你|我|他|她)', '', right)
            return left[:2] == right[:2] or left[-2:] == right[-2:] or (
                heads != left and other != right and heads[:1] == other[:1]
            )
    return False


def metrics(text):
    """Return finite JSON diagnostics; sentences never span quotations."""
    rows = dialogues(text)
    sentences = [s for row in rows for s in _SENTENCES.split(row['text'])
                 if _han_count(s)]
    lengths = [_han_count(s) for s in sentences]
    chars = _han_count(text)
    quoted_chars = sum(_han_count(row['text']) for row in rows)
    nouns = [m.group() for row in rows for m in _ABSTRACT.finditer(row['text'])]
    noun_chars = sum(map(len, nouns))
    return {
        'chars': chars,
        'dialogues': len(rows),
        'dialogue_chars': quoted_chars,
        'dialogue_ratio': quoted_chars / chars if chars else 0.0,
        'pattern_counts': {
            'contrast': sum(len(_CONTRAST.findall(s)) for s in sentences),
            'parallelism': sum(_parallel(s) for s in sentences),
            'aphorism': sum(len(_APHORISM.findall(s)) for s in sentences),
            'long_sentence': sum(n > 35 for n in lengths),
        },
        'abstract_noun_count': len(nouns),
        'abstract_noun_chars': noun_chars,
        'abstract_noun_density': noun_chars / quoted_chars if quoted_chars else 0.0,
        'sentence_count': len(lengths),
        'sentence_length_mean': sum(lengths) / len(lengths) if lengths else 0.0,
        'sentence_length_variance': float(pvariance(lengths)) if lengths else 0.0,
    }


def _english_words(text):
    return Counter(_ENGLISH.findall(unicodedata.normalize('NFKC', text)))


def apply_dialogue_edits(text, edits):
    """Replace exact quote interiors only; return source-ordered accepted edits."""
    rows = dialogues(text)
    by_id = {row['id']: row for row in rows}
    by_span = {(row['start'], row['end']): row for row in rows}
    accepted = []
    used = set()
    if not isinstance(edits, (list, tuple)):
        return text, accepted
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get('text'), str):
            continue
        if 'id' in edit:
            if not isinstance(edit['id'], str):
                continue
            row = by_id.get(edit['id'])
        elif type(edit.get('start')) is int and type(edit.get('end')) is int:
            row = by_span.get((edit['start'], edit['end']))
        else:
            continue
        if row is None or row['nested'] or row['id'] in used:
            continue
        if any(key in edit and (type(edit[key]) is not int or edit[key] != row[key])
               for key in ('start', 'end')):
            continue
        if edit.get('original') != row['text']:
            continue
        replacement = edit['text']
        if not replacement.strip():
            continue
        if Counter(_LINE_BREAKS.findall(replacement)) - Counter(
                _LINE_BREAKS.findall(row['text'])):
            continue
        if any(char in _QUOTE_MARKS for char in replacement):
            continue
        if text[row['end']] == '"' and _escaped(replacement, len(replacement)):
            continue
        if _english_words(replacement) - _english_words(row['text']):
            continue
        used.add(row['id'])
        accepted.append({'id': row['id'], 'start': row['start'], 'end': row['end'],
                         'original': row['text'], 'text': replacement})
    accepted.sort(key=lambda edit: edit['start'])
    pieces = []
    cursor = 0
    for edit in accepted:
        pieces.extend((text[cursor:edit['start']], edit['text']))
        cursor = edit['end']
    pieces.append(text[cursor:])
    return ''.join(pieces), accepted
