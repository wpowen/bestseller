"""Standalone offsets, JSON parsing and conservative immutable-fact checks."""

import ast
import json
import re
from pathlib import Path
import yaml

_HARD = yaml.safe_load((Path(__file__).resolve().parent / "data/deai_rules.yaml").read_text())["hard_guards"]
from collections import Counter
from difflib import SequenceMatcher

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
STOP = re.compile(r"[。！？!?]+[”」』\"’\']*|…{2,}[”」』\"’\']*")
NUM = re.compile(
    r"\d+(?:[./:：．点]\d+)*|[零〇一二三四五六七八九十百千万亿两]{2,}|[零〇一二三四五六七八九十两半](?=个|只|口|件|条|张|人|年|月|日|号|斤|块|角|元|丈|步|米|寸|成|分|秒|天|圈)"
)


def count(text):
    return len(HAN.findall(text))


def paragraphs(text):
    return [
        dict(pid=f"P{i}", start=m.start(), end=m.end(), text=m[0])
        for i, m in enumerate(re.finditer(r"[^\r\n]+", text), 1)
        if m[0].strip()
    ]


def sentences(text):
    result = []
    for p in paragraphs(text):
        start = p["start"]
        for end in [m.end() for m in STOP.finditer(text, p["start"], p["end"])] + [p["end"]]:
            a, b = start, end
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            if a < b:
                result.append(dict(sid=f"S{len(result) + 1}", pid=p["pid"], start=a, end=b, text=text[a:b]))
            start = end
    return result


def parse(raw):
    if isinstance(raw, dict):
        return raw
    raw = re.sub(r"<think>.*?</think>", "", str(raw), flags=re.S).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    for value in (raw, raw[raw.find("{") : raw.rfind("}") + 1]):
        for parser in (json.loads, ast.literal_eval):
            try:
                out = parser(value)
                if isinstance(out, dict):
                    return out
            except (ValueError, SyntaxError, TypeError):
                pass
    raise ValueError("No complete JSON object")


def replace(text, edits):
    for edit in sorted(edits, key=lambda x: x["start"], reverse=True):
        text = text[: edit["start"]] + edit["replacement"] + text[edit["end"] :]
    return text


def invariant(old, new, ctx, entities=(), whole=False):
    reasons = []
    if re.findall(r"\r\n|\r|\n", old) != re.findall(r"\r\n|\r|\n", new):
        reasons.append("paragraph_structure")
    if len(paragraphs(old)) != len(paragraphs(new)):
        reasons.append("paragraph_count")
    if Counter(NUM.findall(old)) != Counter(NUM.findall(new)):
        reasons.append("numbers")
    for name in set(entities):
        if isinstance(name, str) and name and name in old and name not in new:
            reasons.append("entity:" + name)
    mental = _HARD["mental"]
    for word in set(re.findall(mental, old)):
        pattern = _HARD["uncertain"] + word
        if len(re.findall(pattern, old)) > len(re.findall(pattern, new)) and new.count(word) >= old.count(word):
            reasons.append("certainty:" + word)
    if Counter(re.findall(_HARD["body_side"], old)) != Counter(re.findall(_HARD["body_side"], new)):
        reasons.append("body_side")
    avoid = _HARD["avoid"]
    unknown = _HARD["unknown"]
    if (re.search(avoid, old) and not re.search(unknown, old) and re.search(unknown, new)) or (
        re.search(unknown, old) and not re.search(avoid, old) and re.search(avoid, new)
    ):
        reasons.append("knowledge_vs_attitude")
    if whole:
        low, high = ctx.get("word_range", (int(count(old) * 0.9), int(count(old) * 1.1)))
        if not max(low, count(old) * 0.9) <= count(new) <= min(high, count(old) * 1.1):
            reasons.append("word_band")
    elif old.strip() and not new.strip() and len(paragraphs(old)) == 1:
        reasons.append("empty_paragraph")
    return reasons


def distance(a, b):
    return 1 - SequenceMatcher(None, a, b, autojunk=False).ratio()


def rollback_invalid(original, final, edits, ctx, extra=()):
    """Revert only paragraphs whose committed edit violates a hard contract."""
    bad = set(extra) | {e["pid"] for e in edits if invariant(e.get("before", ""), e.get("replacement", ""), ctx)}
    old = {p["pid"]: p for p in paragraphs(original)}
    current = {p["pid"]: p for p in paragraphs(final)}
    changes = [
        dict(start=current[pid]["start"], end=current[pid]["end"], replacement=old[pid]["text"])
        for pid in bad
        if pid in old and pid in current
    ]
    restored = replace(final, changes)
    kept = [e for e in edits if e["pid"] not in bad]
    if invariant(original, restored, ctx, whole=True):
        return original, [], sorted(set(e["pid"] for e in edits))
    return restored, kept, sorted(bad)
