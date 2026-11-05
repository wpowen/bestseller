"""Pure serial repetition/catchphrase candidates; no model calls or external rules."""

import re
from .precision import _sentences
from .text import _paragraph_spans

_QUOTE = r'“[^“”]*”|「[^「」]*」|"[^"]*"'


def _norm(text):
    return re.sub(r"[\W_]+", "", text)


def _units(text):
    return [(a, b, _norm(text[a:b])) for a, b in _sentences(text)] + [
        (m.start(), m.end(), _norm(m[0])) for m in re.finditer(_QUOTE, text)
    ]


def _speeches(text, names):
    quoted = [(m.start(), m.end()) for m in re.finditer(_QUOTE, text)]
    pattern = rf"(?<!\w)({names})(?:说|说道|道|答道|问道|喊道|突然开口)[：:,，]?\s*({_QUOTE})"
    postfix = rf"({_QUOTE})[^\S\r\n]*({names})(?:说道|问道|答道|喊道|说|道|问)(?=[。！？!?，,：:\s]|$)"
    return [
        (m[owner], a + m.start(quote), a + m.end(quote), _norm(m[quote]))
        for a, b in _paragraph_spans(text)
        for rule, owner, quote in ((pattern, 1, 2), (postfix, 2, 1))
        for m in re.finditer(rule, text[a:b])
        if not any(left < a + m.start() < right for left, right in quoted)
    ]


def _matches(phrase, words):
    return phrase == words or len(phrase) >= 10 and phrase in words


def adjacent(text):
    pattern = r"(?<![^{e}])(?P<line>[^{e}]*\S[^{e}]*)(?P<gap>(?:\r\n|[{e}])(?:[^\S{e}]*(?:\r\n|[{e}]))*)(?P=line)(?:\r\n|[{e}]|$)".format(
        e=r"\r\n\v\f\x1c-\x1e\x85\u2028\u2029"
    )
    while re.search(pattern, text):
        text = re.sub(pattern, r"\g<line>\g<gap>", text)
    spans = _sentences(text)
    for (x, y), (a, b) in reversed(list(zip(spans, spans[1:]))):
        if text[x:y] == text[a:b] and not text[a:b].startswith(("“", "「", '"')):
            text = text[:a] + text[b:]
    return adjacent(text) if re.search(pattern, text) else text


def catch_hits(text, prior_texts, cards):
    cards = [c for c in (cards or ()) if isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"]]
    names = "|".join(re.escape(c["name"]) for c in cards) or r"(?!)"
    phrases = [
        (c["name"], _norm(p))
        for c in cards
        for p in (c.get("catchphrase") if isinstance(c.get("catchphrase"), (list, tuple)) else [c.get("catchphrase")])
        if isinstance(p, str) and _norm(p)
    ]
    history = [t for t in list(prior_texts or ())[-9:] if isinstance(t, str)]
    old = {
        words
        for chapter in history
        for _, _, words in _units(chapter)
        if len(re.findall(r"[\u4e00-\u9fff]", words)) >= 10
    }
    prior = [(name, words) for chapter in history for name, _, _, words in _speeches(chapter, names)]
    repeated = [(a, b) for a, b, words in _units(text) if words in old]
    speeches = _speeches(text, names)
    result = []
    for i, (a, b) in enumerate(_sentences(text), 1):
        kinds = ["repeat_quote"] if any(left < b and right > a for left, right in repeated) else []
        for name, left, right, words in speeches:
            if not (left < b and right > a):
                continue
            owners = {owner for owner, phrase in phrases if _matches(phrase, words)}
            if owners and name not in owners:
                kinds.append("catchphrase_owner")
            if name in owners and any(
                owner == name
                and _matches(phrase, words)
                and any(old_name == name and _matches(phrase, old_words) for old_name, old_words in prior)
                for owner, phrase in phrases
            ):
                kinds.append("catchphrase_quota")
        if kinds:
            result.append(dict(id=i, categories=list(dict.fromkeys(kinds))))
    return result
