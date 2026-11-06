"""Independent prose cleaning and deterministic, reference-only book diagnostics."""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import tempfile

import yaml


_CHAPTER_LINE = re.compile(r"^第[0-9０-９零〇一二三四五六七八九十百千万两]+[章场幕](?:\s|[：:]|$).*$")
_SEPARATOR = re.compile(r"(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,}")
_EMOJI_UNIT = (
    r"(?:[\U0001F1E6-\U0001F1FF]{2}|[0-9#*]\ufe0f?\u20e3|[©®™]\ufe0f|"
    r"[\U0001F000-\U0001FAFF\u2600-\u27bf])\ufe0f?[\U0001F3FB-\U0001F3FF]?"
)
_EMOJI = re.compile(_EMOJI_UNIT + r"(?:\u200d" + _EMOJI_UNIT + r")*|[\ufe0f\u200d]")
_PRIVATE = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")


def clean_prose(text):
    """Return (text, counts); remove only standalone structure and unwanted glyphs.

    Counts are removed lines for structure, sequences for emoji, and codepoints
    for private use. Normal paragraphs, punctuation and line endings are retained.
    No filesystem or model calls; safe for both writing and polishing clients.
    """
    counts = dict(markdown_headings=0, chapter_headings=0, separators=0, emoji=0, private_use=0, paragraph_markers=0)

    def strip_markers(match):
        counts["paragraph_markers"] += len(re.findall(r"\[[PS]\d+\]", match[2], re.I))
        return match[1]

    text = re.sub(r"^([ \t]*)((?:\[[PS]\d+\][ \t]*)+)", strip_markers, text, flags=re.M | re.I)
    kept = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        kind = None
        if re.match(r"^#{1,6}(?:\s|$)", stripped):
            kind = "markdown_headings"
        elif _CHAPTER_LINE.fullmatch(stripped) and not re.search(r"[。！？!?]", stripped):
            kind = "chapter_headings"
        elif _SEPARATOR.fullmatch(stripped):
            kind = "separators"
        if kind:
            counts[kind] += 1
        else:
            kept.append(line)
    cleaned, counts["emoji"] = _EMOJI.subn("", "".join(kept))
    cleaned, counts["private_use"] = _PRIVATE.subn("", cleaned)
    counts["total"] = sum(counts.values())
    return cleaned, counts


def _read(path, unknown):
    if not path.exists():
        unknown.append(dict(file=path.name, reason="missing_metadata"))
        return {}
    try:
        value = (
            yaml.safe_load(path.read_text("utf-8")) if path.suffix == ".yaml" else json.loads(path.read_text("utf-8"))
        )
        if isinstance(value, dict):
            return value
    except (OSError, ValueError, yaml.YAMLError):
        pass
    unknown.append(dict(file=path.name, reason="invalid_metadata"))
    return {}


def _rows(value):
    if isinstance(value, dict):
        return [dict(v, name=k) if isinstance(v, dict) else dict(name=k) for k, v in value.items()]
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _chapters(book, source, unknown):
    found = {}
    # Higher priority paths replace lower priority paths; one source per chapter.
    for folder, pattern in ((book, r"chapter-(\d+)(?:\.body)?\.md"), (book / "final", r"chapter-(\d+)\.md")):
        if source == "draft" and folder != book:
            continue
        for path in sorted(folder.glob("chapter-*.md")):
            match = re.fullmatch(pattern, path.name)
            if match and path.is_file():
                n = int(match[1])
                if folder == book and n in found and found[n].name.endswith(".body.md"):
                    continue
                found[n] = path
    result = []
    for n, path in sorted(found.items()):
        text = path.read_text("utf-8")
        meta = _read(book / f"chapters/{n:03d}.json", unknown)
        heading = re.search(r"(?m)^\s*#{1,6}\s+(.+)$", text)
        title = meta.get("title") or (heading[1] if heading else "")
        title = re.sub(r"^第[0-9零〇一二三四五六七八九十百千万两]+章\s*", "", str(title))
        result.append(dict(number=n, text=text, meta=meta, title=title, path=str(path.relative_to(book))))
    return result


def _stage_stats(bible, chapters):
    stages = _rows(bible.get("volumes", bible.get("stages")))
    observed, unknown = [], []
    for chapter in chapters:
        location, quote = _boundary(chapter, "end")
        if location:
            observed.append(dict(chapter=chapter["number"], location=location, quote=quote))
        else:
            unknown.append(chapter["number"])
    distribution = []
    for stage in stages:
        start, end = stage.get("start"), stage.get("end", stage.get("exit_chapter"))
        valid = type(start) is int and type(end) is int and 0 < start <= end
        distribution.append(
            dict(
                number=stage.get("number"),
                location=stage.get("location", stage.get("stage")),
                start=start,
                end=end,
                planned_chapters=end - start + 1 if valid else None,
                available_chapters=[c["number"] for c in chapters if valid and start <= c["number"] <= end],
                observed_end_locations=sorted(
                    {r["location"] for r in observed if valid and start <= r["chapter"] <= end}
                ),
            )
        )
    locations = sorted({r["location"] for r in distribution if isinstance(r["location"], str) and r["location"]})
    return dict(
        count=len(stages) if stages else None,
        location_count=len(locations) if stages else None,
        locations=locations,
        distribution=distribution,
        observed_locations=dict(
            count=len({r["location"] for r in observed}),
            locations=sorted({r["location"] for r in observed}),
            chapters=observed,
            unknown_chapters=unknown,
            basis="grounded_end_state",
            status="Evidence" if observed else "Unknown",
        ),
        basis="saved_plan",
        status="Evidence" if stages else "Unknown",
    )


def _opponents(bible, chapters, stages):
    from bookengine.retired_opponents import active_opponent_hits

    opponents = {
        c["name"]: c
        for c in _rows(bible.get("characters"))
        if c.get("name") and c.get("role") in ("antagonist", "opponent", "villain", "反派", "对手")
    }
    opponents.update(
        {c["name"]: dict(opponents.get(c["name"], {}), **c) for c in _rows(bible.get("antagonists")) if c.get("name")}
    )
    result = []
    for name, card in sorted(opponents.items()):
        mentions = [c["number"] for c in chapters if name in c["text"]]
        exit_at = card.get("exit", card.get("exit_chapter"))
        if not isinstance(exit_at, int) and card.get("exit_volume"):
            exit_at = next((s["end"] for s in stages if s["number"] == card["exit_volume"]), None)
        result.append(
            dict(
                name=name,
                chapters=mentions,
                mention_count=len(mentions),
                first=mentions[0] if mentions else None,
                last=mentions[-1] if mentions else None,
                span=mentions[-1] - mentions[0] + 1 if mentions else 0,
                tier=card.get("tier"),
                exit=exit_at,
                after_exit_mentions=[n for n in mentions if isinstance(exit_at, int) and n > exit_at],
                status="Evidence",
                active_appearance_status="Unknown",
                active_after_exit_chapters=[
                    c["number"]
                    for c in chapters
                    if any(h["name"] == name for h in active_opponent_hits(c["text"], bible, c["number"]))
                ],
                active_appearance_basis="surface_agent_or_action; Inference",
            )
        )
    return result


def _keywords(chapters):
    hits = defaultdict(list)
    for chapter in chapters:
        words = set()
        for run in re.findall(r"[\u3400-\u9fff]+|[A-Za-z]+", chapter["title"]):
            if run.isascii():
                words.add(run.lower())
                continue
            for size in range(1, min(4, len(run)) + 1):
                words.update(
                    run[i : i + size]
                    for i in range(len(run) - size + 1)
                    if size > 1 or run[i] not in "的一是在了和与之为有不又第章"
                )
        for word in sorted(words):
            hits[word].append(chapter["number"])
    return dict(
        repeated={k: v for k, v in sorted(hits.items()) if len(v) >= 3},
        threshold=3,
        basis="Chinese 1-4 character substrings and English words; per-title counts, not semantic segmentation",
    )


def _boundary(chapter, side):
    text = chapter["text"]
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    passage = "\n\n".join(paragraphs[:2]) if side == "opening" else text[-1200:]
    meta = chapter["meta"]
    for data in (meta, meta.get("state", {}), meta.get("boundary", {})):
        if not isinstance(data, dict):
            continue
        location, quote = data.get(side + "_location"), data.get(side + "_quote")
        if (
            isinstance(location, str)
            and location
            and isinstance(quote, str)
            and quote
            and location in quote
            and quote in passage
        ):
            return location, quote
    return None, None


def _breaks(chapters):
    issues, unknown = [], []
    for old, new in zip(chapters, chapters[1:]):
        pair = [old["number"], new["number"]]
        if pair[1] != pair[0] + 1:
            unknown.append(dict(chapters=pair, reason="missing_chapters"))
            continue
        previous, end_quote = _boundary(old, "end")
        opening, opening_quote = _boundary(new, "opening")
        if previous and opening:
            head = "\n\n".join(re.split(r"\n\s*\n", new["text"])[:2])
            movement = (
                previous in head
                and opening in head
                and re.search(r"离开|走出|走进|抵达|赶到|来到|穿过|沿.{0,12}(?:走|行)|从.{0,25}(?:到|往|去)", head)
            )
            if previous != opening and not movement:
                issues.append(
                    dict(
                        chapters=pair,
                        previous=previous,
                        opening=opening,
                        end_quote=end_quote,
                        opening_quote=opening_quote,
                        status="Inference",
                        basis="grounded_locations",
                    )
                )
            continue
        tail, head = old["text"][-1600:], new["text"][:350]
        outside = re.search(r"天光|阳光|天空|露天|天幕|户外", tail)
        escape = re.search(r"冲出|走出|逃出|跑出|鱼贯|跨过门槛", tail)
        inside = re.search(r"岩壁|矿道|矿轨|运矿|矿车|洞内|地下|隧道|洞顶", head)
        movement = re.search(r"返回|折返|退回|回到|重新进入", head)
        if outside and escape and inside and not movement:
            issues.append(
                dict(chapters=pair, end_quote=tail, opening_quote=head, status="Inference", basis="lexical_boundary")
            )
        else:
            unknown.append(dict(chapters=pair, reason="missing_grounded_boundaries"))
    return dict(count=len(issues), issues=issues, unknown=unknown, status="reference_only")


def _aliases(card):
    values = [card.get("name", "")]
    aliases = card.get("aliases", [])
    values.extend([aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else [])
    return [s for s in values if isinstance(s, str) and s]


def _cast_stats(bible, chapters):
    cards = _rows(bible.get("characters", bible.get("character_cards")))
    missing, introduced, alias_issues = [], [], []
    registered = [s for card in cards for s in [*_aliases(card), card.get("address", "")] if isinstance(s, str) and s]
    for card in cards:
        names = _aliases(card)
        occurrence = next(
            (
                (c, min((c["text"].find(n), n) for n in names if n in c["text"]))
                for c in chapters
                if any(n in c["text"] for n in names)
            ),
            None,
        )
        if not occurrence:
            continue
        chapter, (offset, name) = occurrence
        text = chapter["text"]
        start = max(text.rfind("。", 0, offset), text.rfind("\n", 0, offset), offset - 60) + 1
        sentence_end = re.search(r"[。！？\n]", text[offset:])
        end = offset + sentence_end.end() if sentence_end else min(len(text), offset + 160)
        quote = text[start:end]
        origin = any(
            re.search(re.escape(n) + r"(?:[，, ]?(?:原来|本来|原本|他|她))?(?:是|原是|本是|曾是|来自|出身)", quote)
            or re.search(r"(?:掌柜|账房|矿奴|监工|头目|医师|大夫|弟子|师父|商人|侍卫|村民|捕快)" + re.escape(n), quote)
            for n in names
        )
        identity = next(
            (card.get(k) for k in ("identity", "origin", "background") if isinstance(card.get(k), str) and card[k]),
            None,
        )
        origin = origin or bool(identity and any(identity + n in quote for n in names))
        if re.search(r"不是|并非|[？?]", quote):
            origin = False
        row = dict(
            name=card.get("name"), chapter=chapter["number"], quote=quote, status="Evidence" if origin else "Inference"
        )
        (introduced if origin else missing).append(row)
    for chapter in chapters:
        text = chapter["text"]
        candidates = [
            (m[1], m.start(), m.end())
            for m in re.finditer(
                r"(?:^|[“‘\"'，。！？\n])(?:叫我|喊我|我叫|称我为|自称|人称)([\u4e00-\u9fff]{1,8})(?=[，。！？、”’\s]|$)",
                text,
            )
        ]
        candidates += [
            (m[1], m.start(), m.end())
            for m in re.finditer(
                r"(?:跟|听|找|叫|给)([\u4e00-\u9fff]{1,3}(?:哥|姐|爷|叔|姨))(?:走|来|去|说|[，。！？”\s])", text
            )
        ]
        for alias, start, end in candidates:
            if alias not in ("哥哥", "姐姐", "自己") and not any(alias in s for s in registered):
                alias_issues.append(
                    dict(
                        chapter=chapter["number"],
                        alias=alias,
                        quote=text[max(0, start - 20) : end + 20],
                        status="Inference",
                    )
                )
    return (
        dict(missing=missing, introduced=introduced, coverage="registered_cast_only" if cards else "Unknown"),
        dict(issues=alias_issues, coverage="registered_aliases_and_explicit_self_names" if cards else "Unknown"),
    )


def check_book(book, *, source="final"):
    """Analyze without writing. Prefer final per chapter, falling back to draft.

    source='draft' checks saved writer bodies instead. Statistics are references:
    name mentions are not active appearances, ngrams are not semantic keywords,
    and heuristic continuity/introduction/alias findings require human review.
    """
    if source not in ("final", "draft"):
        raise ValueError("source must be final or draft")
    book = Path(book).resolve()
    if not book.is_dir():
        raise ValueError("Book directory does not exist")
    unknown = []
    bible = _read(book / "story_bible.yaml", unknown)
    chapters = _chapters(book, source, unknown)
    stages = _stage_stats(bible, chapters)
    from bookengine.retired_opponents import location_regression
    from bookengine.stage_transition import stage_transition
    from bookengine.prose_repetition import clean_repetitions

    regressions = [issue for c in chapters if (issue := location_regression(bible, c["number"], *_boundary(c, "end")))]
    by_number = {c["number"]: c for c in chapters}
    transitions = [
        dict(row, chapters=[c["number"] - 1, c["number"]])
        for c in chapters
        if (
            row := stage_transition(
                bible,
                c["number"],
                clean_prose(c["text"])[0],
                _boundary(by_number[c["number"] - 1], "end")[0] if c["number"] - 1 in by_number else None,
            )
        )
    ]
    residue = [dict(chapter=c["number"], **clean_prose(c["text"])[1]) for c in chapters]
    repetitions = [
        dict(
            chapter=c["number"],
            **{
                k: v
                for k, v in clean_repetitions(clean_prose(c["text"])[0], remove_safe=False).items()
                if k in ("counts", "records")
            },
        )
        for c in chapters
    ]
    intros, aliases = _cast_stats(bible, chapters)
    if not chapters:
        unknown.append(dict(reason="no_chapters"))
    return dict(
        schema_version=1,
        reference_only=True,
        source_preference=source,
        chapter_count=len(chapters),
        sources={str(c["number"]): c["path"] for c in chapters},
        stages=stages,
        antagonists=_opponents(bible, chapters, stages["distribution"]),
        title_keywords=_keywords(chapters),
        location_breaks=_breaks(chapters),
        location_regressions=dict(count=len(regressions), issues=regressions),
        stage_transitions=dict(
            checks=transitions,
            missing=[r["chapters"] for r in transitions if r["status"] == "missing"],
            unknown=[r["chapters"] for r in transitions if r["status"] == "unknown"],
        ),
        repetition=dict(
            sentence_repetitions=sum(r["counts"]["sentence_repetitions"] for r in repetitions),
            narrative_repetitions=sum(r["counts"]["narrative_repetitions"] for r in repetitions),
            dialogue_repetitions=sum(r["counts"]["dialogue_repetitions"] for r in repetitions),
            adjacent_paragraph_pairs=sum(r["counts"]["adjacent_paragraph_pairs"] for r in repetitions),
            chapters=[r for r in repetitions if r["records"]],
        ),
        format_residue=dict(
            total=sum(c["total"] for c in residue),
            chapters=[c for c in residue if c["total"]],
            heading_chapters=[c["chapter"] for c in residue if c["markdown_headings"] or c["chapter_headings"]],
        ),
        introductions=intros,
        aliases=aliases,
        unknown=unknown,
        limitations=[
            "Counts are references, not reader acceptance.",
            "Active appearances use surface agent/action cues; unrecognized semantics remain Unknown.",
            "Continuity and introduction/alias issues are Inference, not semantic proof.",
            "Unregistered named characters and the meaning of dialogue remain Unknown.",
        ],
    )


def write_checkup(book, *, source="final"):
    """Write only book_checkup.json; never alter prose, plans or checkpoints."""
    report = check_book(book, source=source)
    destination = Path(book) / "book_checkup.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return report


def main(argv=None):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description="整书体检（参考）：只读书稿，仅写 book_checkup.json")
    parser.add_argument("book", type=Path)
    parser.add_argument("--source", choices=("final", "draft"), default="final")
    args = parser.parse_args(argv)
    try:
        result = write_checkup(args.book, source=args.source)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"book_checkup: {exc}\n")
    print(
        json.dumps(
            dict(
                chapters=result["chapter_count"],
                stages=result["stages"]["count"],
                location_breaks=result["location_breaks"]["count"],
                format_residue=result["format_residue"]["total"],
                report=str(args.book / "book_checkup.json"),
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
