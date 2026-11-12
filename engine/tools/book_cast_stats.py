"""Reference-only cast introduction and explicit alias statistics for book_checkup."""

import re


def _rows(value):
    if isinstance(value, dict):
        return [dict(item, name=key) if isinstance(item, dict) else dict(name=key) for key, item in value.items()]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _aliases(card):
    values = [card.get("name", "")]
    aliases = card.get("aliases", [])
    values.extend([aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else [])
    return [value for value in values if isinstance(value, str) and value]


def cast_stats(bible, chapters):
    cards = _rows(bible.get("characters", bible.get("character_cards")))
    missing, introduced, alias_issues = [], [], []
    registered = [
        value
        for card in cards
        for value in [*_aliases(card), card.get("address", "")]
        if isinstance(value, str) and value
    ]
    for card in cards:
        names = _aliases(card)
        occurrence = next(
            (
                (chapter, min((chapter["text"].find(name), name) for name in names if name in chapter["text"]))
                for chapter in chapters
                if any(name in chapter["text"] for name in names)
            ),
            None,
        )
        if not occurrence:
            continue
        chapter, (offset, _) = occurrence
        text = chapter["text"]
        start = max(text.rfind("。", 0, offset), text.rfind("\n", 0, offset), offset - 60) + 1
        sentence_end = re.search(r"[。！？\n]", text[offset:])
        end = offset + sentence_end.end() if sentence_end else min(len(text), offset + 160)
        quote = text[start:end]
        origin = any(
            re.search(re.escape(name) + r"(?:[，, ]?(?:原来|本来|原本|他|她))?(?:是|原是|本是|曾是|来自|出身)", quote)
            or re.search(
                r"(?:掌柜|账房|矿奴|监工|头目|医师|大夫|弟子|师父|商人|侍卫|村民|捕快)" + re.escape(name), quote
            )
            for name in names
        )
        identity = next(
            (
                card.get(key)
                for key in ("identity", "origin", "background")
                if isinstance(card.get(key), str) and card.get(key)
            ),
            None,
        )
        origin = (origin or bool(identity and any(identity + name in quote for name in names))) and not re.search(
            r"不是|并非|[？?]", quote
        )
        row = {
            "name": card.get("name"),
            "chapter": chapter["number"],
            "quote": quote,
            "status": "Evidence" if origin else "Inference",
        }
        (introduced if origin else missing).append(row)
    for chapter in chapters:
        text = chapter["text"]
        candidates = [
            (match[1], match.start(), match.end())
            for match in re.finditer(
                r"(?:^|[“‘\"'，。！？\n])(?:叫我|喊我|我叫|称我为|自称|人称)([\u4e00-\u9fff]{1,8})(?=[，。！？、”’\s]|$)",
                text,
            )
        ]
        candidates += [
            (match[1], match.start(), match.end())
            for match in re.finditer(
                r"(?:跟|听|找|叫|给)([\u4e00-\u9fff]{1,3}(?:哥|姐|爷|叔|姨))(?:走|来|去|说|[，。！？”\s])",
                text,
            )
        ]
        for alias, start, end in candidates:
            if alias not in ("哥哥", "姐姐", "自己") and not any(alias in value for value in registered):
                alias_issues.append(
                    {
                        "chapter": chapter["number"],
                        "alias": alias,
                        "quote": text[max(0, start - 20) : end + 20],
                        "status": "Inference",
                    }
                )
    return (
        {"missing": missing, "introduced": introduced, "coverage": "registered_cast_only" if cards else "Unknown"},
        {
            "issues": alias_issues,
            "coverage": "registered_aliases_and_explicit_self_names" if cards else "Unknown",
        },
    )
