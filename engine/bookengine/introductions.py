"""Pure, conservative first-appearance and address checks for the chapter repair loop.

``annotate_introductions(outlines, cards, previously_seen=())`` returns a deep copy
with per-chapter ``introductions`` records. Explicit scene cast supplements card
name matching; arbitrary prose is never treated as a complete name registry.

``check_introductions(text, cards, previously_seen=(), names=())`` returns findings
with kind/name/status/message and exact quote/start/end offsets. ``names`` can be
the outline's introductions records, or explicit names, including missing cards.
Evidence means a card phrase was locally tied to the first mention, not human
acceptance. Inference means a repair candidate; Unknown means insufficient data.
Unmatched paraphrases and incidental mentions need editorial judgment. Nickname
checks cover explicit naming and address contexts, not arbitrary Chinese words.
``repair_issues(findings)`` supplies messages to the existing bounded repair loop;
this module does not call models, mutate state, or mark anyone durably introduced.
"""

from copy import deepcopy
import re


_CJK = r"\u3400-\u9fff"
_ROLES = {"protagonist", "support", "supporting", "antagonist", "villain", "hero", "主角", "配角", "反派"}
_EMPTY = {"未知", "不明", "待定", "无", "unknown", "none"}
_ORIGIN_FIELDS = ("identity", "background", "origin", "身份", "背景", "来历", "role")
_NAMING = re.compile(
    rf"(?:叫我|我叫|称我为|自称|人称|外号(?:叫|是)?|绰号(?:叫|是)?)\s*[“‘\"']?"
    rf"([{_CJK}]{{1,8}}?)(?=[”’\"'。！，,；;：:\s]|$)"
)
_ADDRESS = re.compile(
    rf"(?:跟着|跟|听|喊|找|请|问|谢)([{_CJK}]{{1,3}}?(?:哥|姐|爷|叔|嫂|婆|老大))"
    r"(?=[走来去说喊带救帮，,。！!？?、\s”’\"']|$)"
)
_VOCATIVE = re.compile(rf"(?:^|[“‘\"'\n])([{_CJK}]{{1,3}}?(?:哥|姐|爷|叔|嫂|婆|老大))(?=[，,！!？?])")


def _strings(value):
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def _names(value):
    if isinstance(value, str):
        return [value]
    return [n for item in value for n in _strings(item.get("name", "") if isinstance(item, dict) else item)]


def _registry(cards):
    by_name, labels = {}, {}
    for card in cards:
        if not isinstance(card, dict) or not isinstance(card.get("name"), str) or not card["name"].strip():
            continue
        name = card["name"].strip()
        by_name[name] = card
        # Legacy address fields can describe how this person addresses another
        # character. Only explicit aliases establish identity ownership.
        for label in [name, *_strings(card.get("aliases"))]:
            labels.setdefault(label, set()).add(name)
    for name in by_name:
        labels[name] = {name}
    return by_name, labels


def _canonical(name, labels):
    matches = labels.get(name, set())
    return next(iter(matches)) if len(matches) == 1 else name


def _origins(card):
    return list(
        dict.fromkeys(
            s
            for field in _ORIGIN_FIELDS
            for s in _strings(card.get(field))
            if s.lower() not in _EMPTY and s.lower() not in _ROLES
        )
    )


def _outline_cast(value):
    if not isinstance(value, dict):
        return []
    result = _names(value.get("characters", [])) + _names(value.get("present", []))
    for scene in value.get("scenes", []):
        if isinstance(scene, dict):
            result.extend(_outline_cast(scene))
    return result


def _outline_text(value):
    if isinstance(value, dict):
        return "\n".join(
            _outline_text(v) for k, v in value.items() if k not in {"where", "location", "title", "introductions"}
        )
    if isinstance(value, list):
        return "\n".join(_outline_text(v) for v in value)
    return value if isinstance(value, str) else ""


def annotate_introductions(outlines, cards, previously_seen=()):
    """Annotate a chronological batch; caller supplies cast seen before the batch."""
    by_name, labels = _registry(cards)
    seen = {_canonical(n, labels) for n in _names(previously_seen)}
    result = deepcopy(outlines)
    for chapter in result:
        text = _outline_text(chapter)
        candidates = _outline_cast(chapter) + [label for label in labels if label in text]
        records = []
        for label in dict.fromkeys(candidates):
            name = _canonical(label, labels)
            if name in seen:
                continue
            seen.add(name)
            card = by_name.get(name)
            origin = "；".join(_origins(card)) if card else ""
            source = "ambiguous_alias" if len(labels.get(label, ())) > 1 else "card" if card else "missing_card"
            records.append(dict(name=name, origin=origin, status="Evidence" if origin else "Unknown", source=source))
        chapter["introductions"] = records
    return result


def _finding(kind, name, status, message, text="", start=0, end=0):
    return dict(kind=kind, name=name, status=status, message=message, quote=text[start:end], start=start, end=end)


def _mentions(text, labels):
    # Longer labels own overlapping spans, so a short alias cannot steal a name.
    found, occupied = [], []
    for label in sorted(labels, key=lambda s: (-len(s), s)):
        for match in re.finditer(re.escape(label), text):
            a, b = match.span()
            if not any(a < y and b > x for x, y in occupied):
                found.append((a, b, label))
                occupied.append((a, b))
    return sorted(found)


def _bound_identity(sentence, label, origins):
    if re.search(r"[？?]", sentence):
        return False
    for term in (s.strip() for origin in origins for s in re.split(r"[，,。；;、\n]", origin)):
        if len(term) < 2:
            continue
        if re.search(re.escape(label) + r"\s*[，,]\s*" + re.escape(term) + r"(?=[，,。；;]|$)", sentence):
            return True
        for clause in re.split(r"[，,；;]", sentence):
            if re.search(r"不是|并非|不曾|不再|未曾|[？?]", clause):
                continue
            escaped, subject = re.escape(term), re.escape(label)
            if re.search(escaped + r"(?:的)?\s*" + subject, clause):
                return True
            if re.search(subject + r"\s*(?:(?:原本|曾经|原|本|曾)?是|乃|身为|作为)?\s*" + escaped, clause):
                return True
    return False


def _evidence_span(text, first, labels, origins):
    a, b, label = first
    sentences = list(re.finditer(r"[^。！？!?\n]+[。！？!?]?", text))
    index = next(i for i, sentence in enumerate(sentences) if sentence.start() <= a < sentence.end())
    current = sentences[index]
    local = current.group()
    same_cast = {n for _, _, token in _mentions(local, labels) for n in labels[token]}
    if _bound_identity(local, label, origins):
        return current.span()
    # Pronoun binding is only attempted when there is one named subject in this
    # sentence. A following sentence must stay in the same paragraph and begin
    # with this subject or a pronoun. Broader coreference is deliberately unknown.
    if len(same_cast) == 1:
        if _bound_identity(re.sub(r"(?<=[，,])\s*[他她]", label, local), label, origins):
            return current.span()
        if index + 1 < len(sentences):
            following = sentences[index + 1]
            gap = text[current.end() : following.start()]
            next_text = following.group().lstrip()
            if "\n" not in gap and re.match(r"(?:他|她|" + re.escape(label) + ")", next_text):
                next_names = {n for _, _, token in _mentions(next_text, labels) for n in labels[token]}
                if next_names <= same_cast and _bound_identity(re.sub(r"^[他她]", label, next_text), label, origins):
                    return current.start(), following.end()
    return None


def check_introductions(text, cards, previously_seen=(), names=()):
    """Find introductions and explicit alias candidates; never imply full coverage."""
    by_name, labels = _registry(cards)
    seen = {_canonical(n, labels) for n in _names(previously_seen)}
    expected = list(dict.fromkeys(_canonical(n, labels) for n in _names(names)))
    hits = _mentions(text, labels)
    firsts, findings, ambiguous = {}, [], set()
    for hit in hits:
        if len(labels[hit[2]]) > 1:
            if hit[2] not in ambiguous:
                ambiguous.add(hit[2])
                findings.append(
                    _finding("ambiguous_alias", hit[2], "Unknown", "称呼对应多张人物卡，需核对归属", text, *hit[:2])
                )
            continue
        name = next(iter(labels[hit[2]]))
        firsts.setdefault(name, hit)
    if not by_name and not expected:
        findings.append(_finding("missing_cards", "", "Unknown", "缺少有效人物卡，无法验证登场和合法称呼"))
    for name in dict.fromkeys([*firsts, *expected]):
        if name in seen:
            continue
        card = by_name.get(name)
        first = firsts.get(name)
        if not card:
            findings.append(_finding("missing_card", name, "Unknown", f"{name}缺少人物卡；勿臆造身份，先核对卡片"))
        elif not first:
            findings.append(_finding("not_mentioned", name, "Unknown", f"本章未定位到{name}，无法核对首次登场"))
        elif not (origins := _origins(card)):
            findings.append(
                _finding("missing_origin", name, "Unknown", f"{name}人物卡缺少可核对身份/来历", text, *first[:2])
            )
        elif span := _evidence_span(text, first, labels, origins):
            findings.append(
                _finding("introduction_present", name, "Evidence", "首次出现附近有与人物卡一致的身份/来历", text, *span)
            )
        else:
            findings.append(
                _finding(
                    "missing_introduction",
                    name,
                    "Inference",
                    f"{name}首次出现附近未定位到人物卡身份/来历（{'；'.join(origins)}）；核对后仅补本段交代",
                    text,
                    *first[:2],
                )
            )
    aliases = set()
    addresses = {s for card in by_name.values() for s in _strings(card.get("address"))}
    for pattern in (_NAMING, _ADDRESS, _VOCATIVE):
        for match in pattern.finditer(text):
            token = match[1]
            if (
                token in labels
                or token in addresses
                or token in aliases
                or token in {"我", "你", "他", "她", "哥哥", "姐姐", "叔叔", "爷爷"}
            ):
                continue
            if pattern in (_ADDRESS, _VOCATIVE) and token.startswith(("我", "你", "他", "她", "亲", "表", "堂")):
                continue
            aliases.add(token)
            findings.append(
                _finding(
                    "unregistered_alias",
                    token,
                    "Inference",
                    f"称呼/自称“{token}”未登记人物卡；核对归属后改用合法称呼",
                    text,
                    *match.span(1),
                )
            )
    return findings


def repair_issues(findings):
    """Messages for existing repair prompts, including Unknown without inventing facts."""
    return [f"[{row['status']}] {row['message']}" for row in findings if row["status"] != "Evidence"]


def world_term_registry(bible):
    """Build a conservative glossary from explicit world-design fields."""
    result = {}

    def add(term, kind, definition):
        if isinstance(term, str) and term.strip() and len(term.strip()) <= 24:
            result.setdefault(term.strip(), dict(kind=kind, definition=str(definition or "").strip()))

    glossary = bible.get("glossary", {}) if isinstance(bible, dict) else {}
    if isinstance(glossary, dict):
        for term, definition in glossary.items():
            add(term, "glossary", definition)
    elif isinstance(glossary, list):
        for row in glossary:
            if isinstance(row, dict):
                add(row.get("term", row.get("name")), row.get("kind", "glossary"), row.get("definition"))
    world = bible.get("world", {}) if isinstance(bible.get("world"), dict) else {}
    levels = world.get("levels", [])
    levels = levels if isinstance(levels, list) else []
    for index, level in enumerate(levels, 1):
        term = level.get("name") if isinstance(level, dict) else level
        supplied = level.get("definition", level.get("effect")) if isinstance(level, dict) else None
        previous = levels[index - 2] if index > 1 else None
        previous = previous.get("name") if isinstance(previous, dict) else previous
        definition = supplied or f"境界序列第{index}阶" + (f"，承接{previous}" if previous else "")
        add(term, "level", definition)
    factions = world.get("factions", []) if isinstance(world.get("factions"), list) else []
    for faction in factions:
        if isinstance(faction, dict):
            add(faction.get("name"), "faction", f"组织势力，目标是{faction.get('interest', '')}".rstrip("是"))
    stages = world.get("stages", []) if isinstance(world.get("stages"), list) else []
    for stage in stages:
        if isinstance(stage, dict):
            term = stage.get("stage", stage.get("location", stage.get("name")))
            add(term, "location", stage.get("description") or "当前故事所在的地区或舞台")
        elif isinstance(stage, str):
            add(stage, "location", "故事中的地区或舞台")
    power = bible.get("power", {}) if isinstance(bible.get("power"), dict) else {}
    add(power.get("name"), "system", power.get("rule") or power.get("limit") or "主角使用的能力体系")
    return result


def _term_sentence(text, offset):
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", text):
        if match.start() <= offset < match.end():
            return match
    return None


def _term_is_bound(term, card, sentence):
    if re.search(r"不是|并非|[？?]", sentence):
        return False
    if re.search(re.escape(term) + r"(?:是|即|指|属于|叫作|名为)", sentence):
        return True
    definition = card.get("definition", "")
    if definition and definition in sentence:
        return True
    cues = {
        "level": r"境界|第[一二三四五六七八九十\d]+(?:阶|层|级)",
        "faction": r"宗门|组织|势力|帮会|学派",
        "location": r"位于|坐落|地区|城|镇|山|谷|驿|州|国",
        "system": r"能力|术法|体系|规则|借用|催动",
        "glossary": r"意思是|也就是|用来|能够|不能",
    }
    return bool(re.search(cues.get(card.get("kind"), r"意思是|也就是"), sentence))


_UNRESOLVED_ENTITY = re.compile(r"(?:另一|第二|其余)(?:头|只|名|个)(?:契兽|灵兽|妖兽|雪兽|同伴|弟子|守卫)")


def check_world_first_appearances(text, registry, previous_text="", character_names=()):
    """Check a term exactly where it first appears; later explanation does not rewrite history."""
    findings = []
    for term, card in registry.items():
        if term in previous_text or term not in text:
            continue
        offset = text.find(term)
        sentence = _term_sentence(text, offset)
        if sentence is None:
            continue
        present = _term_is_bound(term, card, sentence.group())
        findings.append(
            dict(
                kind="term_introduction_present" if present else "term_first_use_missing",
                term=term,
                category=card.get("kind"),
                definition=card.get("definition", ""),
                status="Evidence" if present else "Inference",
                quote=sentence.group(),
                start=sentence.start(),
                end=sentence.end(),
            )
        )
    names = [name for name in character_names if isinstance(name, str) and name]
    for match in _UNRESOLVED_ENTITY.finditer(text):
        sentence = _term_sentence(text, match.start())
        local = sentence.group() if sentence else match.group()
        tail = text[match.end() : sentence.end()] if sentence else ""
        if any(name in tail for name in names):
            continue
        findings.append(
            dict(
                kind="unresolved_entity_reference",
                term=match.group(),
                category="entity_reference",
                definition="用已登场实体的名字或唯一身份说明所指对象",
                status="Inference",
                quote=local,
                start=sentence.start() if sentence else match.start(),
                end=sentence.end() if sentence else match.end(),
            )
        )
    return findings
