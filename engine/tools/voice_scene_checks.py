"""Offline, read-only editorial checks for INBOX15/16.

Rules load once on import; public calls do no I/O and never modify inputs.
scene_hits returns whole original paragraphs for possible targeted rewriting.
Paragraphs are one-based (including headings); Unicode offsets are [start,end).
chapter_checks returns {chapter, confirmed, candidates}. Every finding includes
kind/status and original text/offsets/paragraph. 'confirmed' means only a literal
contract violation (heading, normalized duplicate, forbidden alias), never proven
identity, AI authorship or complete NLP continuity. Other findings require human
review; no inferred person is bound to an unknown nickname or pronoun.

cast is a sequence of {name, gender, aliases: [...], forbidden_aliases: [...]}.
Only explicit aliases are allowed; gender may be male/female or 男/女. Sentence
similarity strips punctuation/space, within three original sentence positions;
each compared sentence must contain at least eight Han characters.
outline_checks accepts {scenes:[{opponent,contest_id,result,...}]} or a scene list;
only exact final result labels count. Missing contest IDs, prose outcomes,
recaps, hypotheses and temporary/misjudged results establish no final result.
These lexical checks are intentionally incomplete; an empty report is no proof.
"""
from difflib import SequenceMatcher
from pathlib import Path
import re
import unicodedata

import yaml

from bookengine.identity import RULES as IDENTITY_RULES, name_hits
from bookengine.text import _paragraph_spans


_RULES = yaml.safe_load((Path(__file__).resolve().parents[1]
                         / 'data/voice_scene.yaml').read_text(encoding='utf-8'))
_SCENE, _CHAPTER = _RULES['scene'], _RULES['chapter']
_HAN = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]')
_STOP = re.compile(r'[。！？!?]+[”」』"’\']*|…{2,}[”」』"’\']*|\r?\n')
_QUOTES = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"')


def _heading(text):
    return re.fullmatch(_CHAPTER['heading'], unicodedata.normalize('NFKC', text))


def _sentences(text):
    result = []
    for paragraph, (left, right) in enumerate(_paragraph_spans(text), 1):
        start = left
        for end in [m.end() for m in _STOP.finditer(text, left, right)] + [right]:
            a, b = start, end
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            if a < b and not _heading(text[a:b]):
                result.append((paragraph, a, b))
            start = end
    return result


def _narrative(text):
    return _QUOTES.sub(lambda m: ' ' * len(m[0]), text)


def _forms(cast):
    return {value for card in cast for value in [card['name'], *card.get('aliases', [])]
            if isinstance(value, str) and value}


def _mentions(text, names):
    if not names:
        return []
    pattern = '|'.join(re.escape(n) for n in sorted(names, key=lambda n: (-len(n), n)))
    return list(re.finditer(pattern, text))


def _covered(start, end, mentions):
    return any(m.start() <= start and end <= m.end() for m in mentions)


def scene_hits(text):
    """Return {paragraph,start,end,kinds,text} candidates; never a quality verdict."""
    paragraphs = _paragraph_spans(text)
    kinds = {i: set() for i in range(1, len(paragraphs) + 1)}
    narrative = _narrative(text)
    for index, (paragraph, a, b) in enumerate(_sentences(text)):
        sentence = narrative[a:b]
        if (index < _SCENE['opening_sentences']
                and re.search(_SCENE['opening_environment'], sentence)
                and re.search(_SCENE['opening_frame'], sentence)
                and not re.search(_SCENE['embodied'], sentence)):
            kinds[paragraph].add('opening_empty_shot')
        # A list is a contiguous run of noun-like items joined by 、 or comma.
        item = _SCENE['object_item']
        if re.search(item + r'(?:\s*[、，,]\s*' + item + ')'
                     + '{' + str(_SCENE['object_minimum'] - 1) + ',}', sentence):
            kinds[paragraph].add('object_list')
        if re.search(_SCENE['personified_light'], sentence):
            kinds[paragraph].add('personified_light')
        if any(re.search(p, sentence) for p in _SCENE['precise_micro_action']):
            kinds[paragraph].add('precise_micro_action')
        if any(word in sentence for word in _SCENE['generic_imagery']):
            kinds[paragraph].add('generic_imagery')
    return [dict(paragraph=i, start=a, end=b, kinds=sorted(kinds[i]), text=text[a:b])
            for i, (a, b) in enumerate(paragraphs, 1) if kinds[i]]


def _chapter_number(value):
    if value.isdigit():
        return int(value)
    digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    total, current = 0, 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in '十百千万':
            unit = {'十': 10, '百': 100, '千': 1000, '万': 10000}[char]
            total += (current or 1) * unit
            current = 0
    return total + current


def chapter_checks(text, cast, chapter=1):
    """Locate literal violations and separate human-review candidates; see schema above."""
    report = dict(chapter=chapter, confirmed=[], candidates=[])
    paragraphs = _paragraph_spans(text)
    registered = _forms(cast)
    mentions = _mentions(text, registered)

    def add(kind, start, end, status='candidate', **details):
        paragraph = next(i for i, (a, b) in enumerate(paragraphs, 1) if a <= start < b)
        row = dict(kind=kind, status=status, paragraph=paragraph,
                   start=start, end=end, text=text[start:end], **details)
        report['confirmed' if status == 'confirmed' else 'candidates'].append(row)

    offset = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip('\r\n')
        match = _heading(body)
        if match and (text[:offset].strip() or _chapter_number(match[1]) != chapter):
            add('internal_chapter_heading', offset, offset + len(body), 'confirmed',
                expected_chapter=chapter, found_chapter=_chapter_number(match[1]))
        offset += len(line)

    sentences = _sentences(text)
    normalized = [''.join(c for c in unicodedata.normalize('NFKC', text[a:b])
                          if c.isalnum()) for _, a, b in sentences]
    for index, (paragraph, a, b) in enumerate(sentences):
        current = normalized[index]
        if len(_HAN.findall(text[a:b])) < _CHAPTER['minimum_han']:
            continue
        for old in range(index - 1, max(-1, index - _CHAPTER['neighbor_distance'] - 1), -1):
            previous = normalized[old]
            _, old_a, old_b = sentences[old]
            if len(_HAN.findall(text[old_a:old_b])) < _CHAPTER['minimum_han']:
                continue
            ratio = SequenceMatcher(None, previous, current, autojunk=False).ratio()
            if ratio >= _CHAPTER['similarity']:
                _, left, right = sentences[old]
                exact = previous == current
                add('repeated_sentence' if exact else 'similar_sentence', a, b,
                    'confirmed' if exact else 'candidate', similarity=round(ratio, 4),
                    previous=dict(paragraph=sentences[old][0], start=left, end=right,
                                  text=text[left:right]))
                break

    # Adapt only explicit aliases to identity's existing subject-name scanner.
    cards = [{'name': value} for value in sorted(registered)]
    for hit in name_hits(text, {'characters': cards}):
        for match in re.finditer(re.escape(hit['name']), text[hit['start']:hit['end']]):
            a, b = hit['start'] + match.start(), hit['start'] + match.end()
            if not _covered(a, b, mentions):
                add('unknown_name', a, b, name=hit['name'])

    surnames = IDENTITY_RULES['surname'] + _CHAPTER['compound_surnames']
    surname = '(?:' + '|'.join(map(re.escape, sorted(surnames, key=len, reverse=True))) + ')'
    for card in cast:
        name = card['name']
        if len(name) >= 3:
            for match in re.finditer(surname + re.escape(name[-2:]), text):
                if match[0] not in registered and not _covered(*match.span(), mentions):
                    add('surname_drift', *match.span(), name=match[0], expected=name)
        for alias in card.get('forbidden_aliases', []):
            if not isinstance(alias, str) or not alias or alias in registered:
                continue
            for match in re.finditer(re.escape(alias), text):
                if not _covered(*match.span(), mentions):
                    add('forbidden_alias', *match.span(), 'confirmed', name=alias,
                        expected=name, evidence='literal_forbidden_alias')

    narrative_text = _narrative(text)
    nickname = _CHAPTER['nickname'].replace('SURNAME', ''.join(IDENTITY_RULES['surname']))
    forbidden = {alias for card in cast for alias in card.get('forbidden_aliases', [])}
    for paragraph, a, b in sentences:
        sentence = text[a:b]
        # Scan address-shaped tokens, with generic kinship terms excluded.
        for match in re.finditer(nickname, sentence):
            name = match[0]
            start, end = a + match.start(), a + match.end()
            if (name not in registered and name not in forbidden
                    and name not in _CHAPTER['nickname_exclusions']
                    and not _covered(start, end, mentions)):
                add('unregistered_nickname', start, end, name=name)

        narrative = narrative_text[a:b]
        present = _mentions(narrative, registered)
        owners = [card for card in cast if any(m[0] in {card['name'], *card.get('aliases', [])}
                                               for m in present)]
        # Multiple named participants make pronoun attribution too ambiguous.
        if len(owners) != 1:
            continue
        card = owners[0]
        gender = next((key for key, values in _CHAPTER['genders'].items()
                       if str(card.get('gender', '')).lower() in values), None)
        if gender:
            wrong = _CHAPTER['pronoun'][gender]
            subject = re.search(r'(?:^|[，,；;：:])\s*' + wrong + r'(?!们|的)', narrative)
            forms = [card['name'], *card.get('aliases', [])]
            explicit = any(re.search(re.escape(name) + r'\s*'
                                    + _CHAPTER['opposite_description'][gender], narrative)
                           for name in forms)
            if subject or explicit:
                add('gender_conflict', a, b, name=card['name'], expected_gender=gender,
                    evidence='same_sentence_lexical_candidate')
        if (all(seat in narrative for seat in _CHAPTER['seats'])
                and len(present) >= 2 and not re.search(_CHAPTER['movement'], narrative)):
            add('seat_conflict', a, b, name=card['name'], evidence='manual_review_only')

    for key in ('confirmed', 'candidates'):
        unique = {(h['kind'], h['start'], h['end'], h.get('expected')): h for h in report[key]}
        report[key] = sorted(unique.values(), key=lambda h: (h['start'], h['end'], h['kind']))
    return report


def outline_checks(outline):
    """Flag repeat final outcomes only for the same explicit (opponent, contest_id)."""
    scenes = outline.get('scenes', []) if isinstance(outline, dict) else outline
    if not isinstance(scenes, (list, tuple)):
        return []
    seen, hits = {}, []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        opponent, contest, result = (scene.get(k) for k in ('opponent', 'contest_id', 'result'))
        if (not isinstance(opponent, str) or not opponent.strip()
                or not isinstance(contest, str) or not contest.strip()
                or result not in _RULES['outline']['final_results']
                or any(scene.get(flag) for flag in _RULES['outline']['nonfinal_flags'])
                or scene.get('final') is False):
            continue
        key = opponent.strip(), contest.strip()
        if key in seen:
            hits.append(dict(kind='repeated_final_result', status='confirmed', scene=index,
                             previous_scene=seen[key], opponent=key[0], contest_id=key[1],
                             result=result, evidence='explicit_structured_final_results'))
        else:
            seen[key] = index
    return hits
