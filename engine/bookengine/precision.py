"""Editorial candidates with original one-based sentence IDs and string offsets.
Hits invite review; they do not establish defective prose or AI authorship.
"""
import re
from pathlib import Path
import yaml

from bookengine.text import _paragraph_spans


_CATEGORIES = ('summary', 'epiphany', 'poetic')
_STOP = re.compile(r'[。！？!?]+[”」』"’\']*|…{2,}[”」』"’\']*')
_BODY = r'[^。！？!?]'


_RULES = {k:tuple(re.compile(p) for p in v) for k,v in yaml.safe_load((Path(__file__).resolve().parents[1]/'data/sentence_patterns.yaml').read_text()).items()}

_CONTRAST = re.compile(r'(?:不是|并非|不再是)' + _BODY + r'{1,100}(?:而是|只是)')
_REFLECTION = re.compile(r'意识到|明白|知道|感觉到|真正|意义|证明|代价|人生|命运|活下去|'
                         r'开始|结束|选择|信任|责任|勇气|希望|决定|野心|意味着')
_ENDING = re.compile(r'这一切才刚刚开始|一切才刚刚开始|这只是开始|故事才刚刚开始|'
                     r'未来的路' + _BODY + r'{0,20}(?:才|将|还)|'
                     r'这(?:就|才)是' + _BODY + r'{0,12}(?:意义|代价|命运)')
_BORROWED = yaml.safe_load((Path(__file__).resolve().parents[1]/'data/borrowed_style.yaml').read_text())


def _sentences(text):
    sentences = []
    for left, right in _paragraph_spans(text):
        start = left
        for end in [m.end() for m in _STOP.finditer(text, left, right)] + [right]:
            a, b = start, end
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            if a < b:
                sentences.append((a, b))
            start = end
    return sentences


def locate(text) -> list[dict]:
    """Locate matching sentences; ending means the last sentence of the last paragraph."""
    spans = _sentences(text)
    paragraphs = _paragraph_spans(text)
    last_paragraph = paragraphs[-1][0] if paragraphs else 0
    hits = []
    for index, (start, end) in enumerate(spans, 1):
        sentence = text[start:end]
        ending = index == len(spans)
        categories = [category for category in _CATEGORIES
                      if any(p.search(sentence) for p in _RULES[category])]
        if not sentence.lstrip().startswith(('“','「','"')):
            categories += [p['kind'] for p in _BORROWED['sentence_patterns'] if re.search(p['regex'],sentence)]
        if ((_CONTRAST.search(sentence) and _REFLECTION.search(sentence))
                or (start >= last_paragraph and _ENDING.search(sentence))):
            if 'summary' not in categories:
                categories.insert(0, 'summary')
        if categories:
            hits.append(dict(id=index, start=start, end=end, text=sentence,
                             categories=categories, ending=ending))
    return hits


def apply(text, edits, targets=None, allow_empty=False, allow_style=False) -> tuple[str, list]:
    """Accept the first valid edit per hit ID, all against original offsets.

    accepted contains {id, text} in input order. Invalid IDs, empty text, and
    replacements still matching a rule (also when split into sentences) are ignored.
    """
    hits = {hit['id']: hit for hit in (targets if targets is not None else locate(text))}
    accepted, replacements, seen = [], [], set();spans=_sentences(text)
    for edit in edits if isinstance(edits, (list, tuple)) else []:
        if not isinstance(edit, dict):
            continue
        number, replacement = edit.get('id'), edit.get('text')
        if isinstance(number,str) and re.fullmatch(r'S?\d+',number,re.I):number=int(number.lstrip('Ss'))
        if (type(number) is not int or number not in hits or number in seen
                or not isinstance(replacement, str) or not allow_empty and not replacement.strip() or '\n' in replacement or '\r' in replacement or not allow_style and locate(replacement)):
            continue
        hit = hits[number]
        for a,b in reversed(spans[number:number+3]):
            neighbor=text[a:b]
            if len(neighbor)>=8 and replacement.endswith(neighbor):replacement=replacement[:-len(neighbor)]
            position=replacement.find(neighbor[:12]) if len(neighbor)>=12 else -1
            if allow_style and position>0:replacement=replacement[:position]
        accepted.append(dict(id=number, text=replacement))
        replacements.append((hit['start'], hit['end'], replacement))
        seen.add(number)
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, accepted
