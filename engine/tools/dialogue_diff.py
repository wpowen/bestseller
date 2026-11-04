"""Pure offline diagnostics for edits to the same ordered quotation slots.

One line means one outer quotation classified by dialogue_metrics.spoken_dialogue,
not a physical source line. Labels and other nonspoken quotes remain narration.
All slots (including labels) must retain their count, delimiters and spoken mask;
otherwise alignment fails closed, lines is empty and distances/changes are zero.
The explicit alignment flag must be checked before interpreting those zeros.

Levenshtein is divided by max(source length, replacement length, 1). Each spoken
slot counts as substantive at normalized distance >= .25 AND at least two Han
edits after punctuation, whitespace and the conservative particle list below are
removed. Even a one-character utterance may be expanded/rephrased; a single Han
substitution alone does not pass. No minimum original line length is imposed.
Means include unchanged spoken slots. Literal distances use the untouched quote
interiors, including punctuation. Distances do not establish semantic equivalence.

facts_program_flags are lexical warnings, not factual validation: numbers,
negation, time, condition and relation cue multisets are compared per slot.
Synonymous rewrites can warn, and changed names, assignments or implied facts can
escape detection. An empty warning list never certifies preserved facts.
No file/network access, mutation, engine imports or model calls occur in the API.
"""
from collections import Counter
import re
import unicodedata

if __package__:
    from .dialogue_metrics import dialogues, spoken_dialogue
else:
    from dialogue_metrics import dialogues, spoken_dialogue


# Do not strip negation, condition, time, pronouns, quantities or modal verbs.
# This deliberately short list is lexical; it is not a Chinese segmenter.
_PARTICLES = frozenset('的地得了着吗呢吧啊呀嘛啦呐哇哟哦噢唉哎诶')
_HAN = re.compile('[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
                  '\U00020000-\U0002fa1f\U00030000-\U000323af]')
_FACT_PATTERNS = {
    'numbers_changed': re.compile(
        r'[+-]?[0-9０-９]+(?:[.．][0-9０-９]+)*[%％]?'
        r'|[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]+'),
    'negation_changed': re.compile(r'没有|不能|不许|不准|不得|并非|不是|从未|未曾|不|没|未|无|别|勿|莫'),
    'time_changed': re.compile(
        r'大前天|大后天|前天|昨天|今天|明天|后天|昨晚|今晚|明晚|'
        r'凌晨|早晨|早上|上午|中午|下午|傍晚|晚上|夜里|'
        r'此前|之后|之前|以后|以前|随后|刚才|现在|马上|立刻'),
    'condition_changed': re.compile(r'只有|只要|除非|否则|如果|要是|倘若|假如|若|才'),
    'relation_changed': re.compile(
        r'哥哥|姐姐|弟弟|妹妹|父亲|母亲|父子|母子|夫妻|兄弟|姐妹|'
        r'师父|师兄|师姐|师弟|师妹|丈夫|妻子|儿子|女儿|'
        r'副驾|后排|前排|左侧|右侧|属于|交给|借给|欠'),
}


def _normalize(text):
    return ''.join(char for char in text
                   if not char.isspace() and char not in _PARTICLES
                   and not unicodedata.category(char).startswith('P'))


def _levenshtein(left, right):
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, source in enumerate(left, 1):
        current = [i]
        for j, target in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (source != target)))
        previous = current
    return previous[-1]


def _distance(left, right):
    return _levenshtein(left, right) / max(len(left), len(right), 1)


def _narration(text, rows, spoken):
    # Tuple boundaries preserve each spoken slot, even empty/adjacent interiors.
    pieces = []
    last = 0
    for row, is_spoken in zip(rows, spoken):
        if is_spoken:
            pieces.append(text[last:row['start']])
            last = row['end']
    pieces.append(text[last:])
    return tuple(pieces)


def _signature(text, rows, spoken):
    return [(text[row['quote_start']], text[row['quote_end'] - 1], is_spoken)
            for row, is_spoken in zip(rows, spoken)]


def dialogue_diff(before: str, after: str) -> dict:
    """Compare ordered spoken slots; return JSON-compatible metrics and warnings.

    Required consumer checks: slot_alignment_unchanged, narration_unchanged,
    substantive_ratio and facts_program_flags. Fact warnings need review; this
    function does not accept/reject edits or claim semantic/factual equivalence.
    The per-slot lines expose both literal and normalized distance for audit.
    """
    if not isinstance(before, str) or not isinstance(after, str):
        raise TypeError('before and after must be strings')
    old_rows, new_rows = dialogues(before), dialogues(after)
    old_spoken = [spoken_dialogue(before, row) for row in old_rows]
    new_spoken = [spoken_dialogue(after, row) for row in new_rows]
    aligned = (_signature(before, old_rows, old_spoken)
               == _signature(after, new_rows, new_spoken))
    flags = []
    lines = []
    if not aligned:
        flags.append({'kind': 'quote_slots_changed',
                      'before_slots': len(old_rows), 'after_slots': len(new_rows),
                      'before_spoken': sum(old_spoken), 'after_spoken': sum(new_spoken)})
    else:
        for slot, (old, new, is_spoken) in enumerate(zip(old_rows, new_rows, old_spoken), 1):
            if not is_spoken:
                continue
            left, right = _normalize(old['text']), _normalize(new['text'])
            distance = _distance(left, right)
            han_edits = _levenshtein(''.join(_HAN.findall(left)),
                                       ''.join(_HAN.findall(right)))
            lines.append({'slot': slot, 'before': old['text'], 'after': new['text'],
                          'edit_distance': distance,
                          'literal_edit_distance': _distance(old['text'], new['text']),
                          'content_han_edits': han_edits,
                          'substantive': distance >= 0.25 and han_edits >= 2})
            for kind, pattern in _FACT_PATTERNS.items():
                old_cues = Counter(pattern.findall(old['text']))
                new_cues = Counter(pattern.findall(new['text']))
                if old_cues != new_cues:
                    flags.append({'kind': kind, 'slot': slot,
                                  'before': dict(old_cues), 'after': dict(new_cues)})
    total = sum(old_spoken)
    changed = sum(line['substantive'] for line in lines)
    return {
        'total_lines': total,
        'changed_lines': changed,
        'substantive_ratio': changed / total if total else 0.0,
        'mean_edit_distance': sum(line['edit_distance'] for line in lines) / total if total else 0.0,
        'mean_literal_edit_distance': sum(line['literal_edit_distance'] for line in lines) / total if total else 0.0,
        'narration_unchanged': (_narration(before, old_rows, old_spoken)
                                == _narration(after, new_rows, new_spoken)),
        'facts_program_flags': flags,
        'slot_alignment_unchanged': aligned,
        'lines': lines,
    }
