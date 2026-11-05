"""Small prose selectors and local style features; no source prose in prompts."""

from collections import Counter
import re
from pathlib import Path

from .q1_quality import metrics


def reference_features(path):
    if not path:
        return {}
    text = Path(path).read_text(encoding="utf-8")
    m = metrics(text)
    sentences = [s for s in re.split(r"[。！？!?\n]+", text) if s.strip()]
    verbs = "说 问 笑 看 走 跑 抓 推 拉 递 躲 踢 骂 摸 停 抬 蹲 咬 听 想".split()
    return dict(
        sentence_chars=round(m["chars"] / max(1, len(sentences)), 1),
        dialogue_ratio=m["dialogue_ratio"],
        paragraph_chars=m["average_paragraph_chars"],
        single_sentence_ratio=m["single_sentence_ratio"],
        frequent_verbs=Counter({v: text.count(v) for v in verbs}).most_common(8),
    )


def surface_hits(text, cards=()):
    from .precision import _sentences

    spans = _sentences(text)
    result = []
    speaker = None
    restraint = 0
    for i, (a, b) in enumerate(spans, 1):
        sentence = text[a:b]
        people = sorted(
            [(sentence.rfind(c["name"]), c) for c in cards if c.get("name") and c["name"] in sentence],
            key=lambda p: p[0],
        )
        english = re.findall(
            r"(?<![A-Za-z])[A-Za-z]{2,}(?![A-Za-z])",
            re.sub(r"(?<![A-Za-z0-9])[A-Z]{1,6}(?:-[A-Z0-9]+)+(?![A-Za-z0-9])", "", sentence),
        )
        allowed = {"AI", "DNA", "NPC", "VR", "ID", "VIP"} | {c.get("name", "") for c in cards}
        kinds = ["english"] if any(w not in allowed for w in english) else []
        count = len(re.findall(r"复核|待核|按[他她]判断", sentence))
        restraint += count
        if count and restraint > 1:
            kinds.append("restraint")
        for pronoun in re.finditer(
            r"(?:^|[，,：:])([他她])(?:说|问|道|抬|低|看|转|仍|却|又|没|的|站|走|拿|把|指|笑)", sentence
        ):
            preceding = [c for pos, c in people if pos < pronoun.start(1)]
            person = preceding[-1] if preceding else speaker
            if (
                person
                and person.get("gender") in ("男", "女")
                and pronoun[1] != ("他" if person["gender"] == "男" else "她")
            ):
                kinds.append("pronoun_candidate")
        if people:
            speaker = people[-1][1]
        if kinds:
            result.append(
                dict(
                    id=i,
                    start=a,
                    end=b,
                    text=sentence,
                    categories=kinds,
                    ending=i == len(spans),
                    context=text[max(0, a - 160) : min(len(text), b + 100)],
                )
            )
    return result


def recall_events(out, history, chapter, total, volume_end):
    from .q1_plan import callbacks

    if chapter < total * 0.9 and chapter < volume_end - 2:
        return []
    result = []
    for event in callbacks(history):
        path = Path(out) / f"chapter-{event['chapter']:03d}.body.md"
        if not path.exists():
            continue
        words = set(re.findall(r"[\u4e00-\u9fff]{2}", event["event"]))
        parts = path.read_text().split("\n\n")
        chosen = sorted(enumerate(parts), key=lambda p: sum(w in p[1] for w in words), reverse=True)[:3]
        excerpt = chosen[0][1][:300] if chosen else ""
        result.append(dict(chapter=event["chapter"], event=event["event"][:120], original_excerpt=excerpt))
    return result[:3]


def reference_fragments(references, body, source):
    from difflib import SequenceMatcher

    result = []
    seen = set()
    for ref in references if isinstance(references, list) else []:
        try:
            n = int(ref["chapter"])
            old = (Path(source) / f"chapter-{n:03d}.body.md").read_text()
        except (TypeError, KeyError, ValueError, OSError):
            continue
        if n in seen:
            continue
        pair = []
        for key, text in (("body_quote", body), ("source_quote", old)):
            quote = str(ref.get(key, ""))
            match = SequenceMatcher(None, quote, text, autojunk=False).find_longest_match(0, len(quote), 0, len(text))
            pair.append(text[match.b : match.b + match.size][:180])
        if min(map(len, pair)) >= 20:
            result.append(dict(chapter=n, body_quote=pair[0], source_quote=pair[1]))
            seen.add(n)
    return result
