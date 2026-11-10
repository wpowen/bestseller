"""Address derivation, explicit origins and first-use evidence (legacy target aware)."""

from copy import deepcopy
from collections import Counter
import re

from .nonhuman_speech import _can_speak, is_nonhuman


def address_rows(bible):
    cards = bible.get("characters", [])
    names = {c.get("name") for c in cards if isinstance(c, dict) and c.get("name")}
    lead = next((c.get("name") for c in cards if c.get("role") == "protagonist"), "")
    seen = set()
    for card in cards:
        if is_nonhuman(card) and not _can_speak(card):
            continue
        address = card.get("address")
        if not isinstance(address, str) or not address.strip():
            continue
        for label in re.split(r"[/／、，,；;]", address):
            label = label.strip()
            if not label:
                continue
            target = card.get("address_target")
            origin = card.get("address_origin", "")
            origin = origin.get(label, "") if isinstance(origin, dict) else origin
            derived = [name for name in names if derived_address(label, name)]
            # Legacy cards sometimes store the target's own address on the
            # target card, and sometimes point address_target back to that
            # same card. A unique name-derived target is stronger evidence.
            if len(derived) == 1 and (not target or target == card.get("name") and derived[0] != target):
                target = derived[0]
            if not target:
                target = label if label in names else lead or card.get("name")
            speaker = card.get("name")
            if target == card.get("name") and isinstance(origin, str):
                originators = list(_originators(dict(name=target, origin=origin), cards))
                if len(originators) == 1:
                    speaker = originators[0]
            key = (speaker, target, label, origin)
            if key in seen:
                continue
            seen.add(key)
            yield dict(speaker=speaker, name=target, address=label, origin=origin, known=target in names)


# Plain kinship and teacher terms are ordinary speech, not nicknames that need
# an origin story; an unrelated prefix such as "砚哥" still does.
_PLAIN_ADDRESSES = frozenset(
    "哥 哥哥 大哥 二哥 姐 姐姐 大姐 二姐 弟 弟弟 妹 妹妹 爹 娘 爸 妈 父亲 母亲 爷爷 奶奶 叔 伯 舅舅 姑姑 "
    "师父 师傅 师兄 师姐 师弟 师妹".split()
)


def plain_address(label):
    return label in _PLAIN_ADDRESSES


def derived_address(label, name):
    if not isinstance(name, str) or not name:
        return False
    stem = re.sub(r"^(?:阿|小|老)", "", label)
    stem = re.sub(
        r"(?:师傅|师父|先生|姑娘|公子|小姐|掌柜|老板|会首|理事|宗主|长老|队长|管事|执事|大人|师兄|师姐|师弟|师妹|哥|姐|弟|妹|叔|伯|爷)$",
        "",
        stem,
    )
    return bool(stem and stem in name)


def address_issues(bible):
    rows = list(address_rows(bible))
    issues = [
        dict(row, kind="address_origin", message="称呼须从目标姓名派生，否则填写address_origin并在首次使用章交代")
        for row in rows
        if not row["known"]
        or not derived_address(row["address"], row["name"])
        and not plain_address(row["address"])
        and not _origin(row["origin"])
    ]
    for row in rows:
        if derived_address(row["address"], row["name"]) or plain_address(row["address"]) or not _origin(row["origin"]):
            continue
        for speaker in _originators(row, bible.get("characters", [])):
            owned = [r for r in rows if r["speaker"] == speaker]
            if any(r["name"] == row["name"] and r["address"] == row["address"] for r in owned):
                continue
            detail = dict(
                kind="address_origin",
                reason="originator_address_mismatch",
                originator=speaker,
                expected_address=row["address"],
                expected_target=row["name"],
                message="来历中起名者的人物卡须包含同一目标、同一称呼；可修正来历或起名者的称呼字段",
            )
            issues.append(dict(row, **detail))
            # Include the originator as an editable issue, so the existing
            # field-only repair can fix either side without touching names.
            source = next((c for c in bible["characters"] if c.get("name") == speaker), {})
            issues.append(
                dict(
                    speaker=speaker,
                    name=source.get("address_target", speaker),
                    address=source.get("address", ""),
                    origin=source.get("address_origin", ""),
                    known=True,
                    **detail,
                )
            )
    issues.extend(_legacy_storage_issues(bible, issues))
    return issues


def _legacy_storage_issues(bible, existing):
    """Keep old target-owned cards readable while requiring new speaker-owned data."""
    cards = [card for card in bible.get("characters", []) if isinstance(card, dict)]
    by_name = {card.get("name"): card for card in cards if card.get("name")}
    result = []
    present = {
        (row.get("speaker"), row.get("expected_target"), row.get("expected_address"))
        for row in existing
        if row.get("reason") == "originator_address_mismatch"
    }
    for target_card in cards:
        target = target_card.get("address_target") or target_card.get("name")
        if target != target_card.get("name"):
            continue
        origin = target_card.get("address_origin", "")
        if not isinstance(origin, str):
            continue
        labels = [
            part.strip() for part in re.split(r"[/／、，,；;]", str(target_card.get("address", ""))) if part.strip()
        ]
        for label in labels:
            row = dict(name=target, origin=origin)
            for speaker in _originators(row, cards):
                source = by_name.get(speaker, {})
                source_labels = {
                    part.strip() for part in re.split(r"[/／、，,；;]", str(source.get("address", ""))) if part.strip()
                }
                source_target = source.get("address_target")
                if label in source_labels and source_target == target:
                    continue
                key = (speaker, target, label)
                if key in present:
                    continue
                detail = dict(
                    kind="address_origin",
                    reason="originator_address_mismatch",
                    originator=speaker,
                    expected_address=label,
                    expected_target=target,
                    message="旧卡把称呼放在被称呼者卡上；新结构须放在说话人卡并填写同一目标",
                )
                result.extend(
                    [
                        dict(
                            speaker=target_card.get("name"),
                            name=target,
                            address=label,
                            origin=origin,
                            known=True,
                            **detail,
                        ),
                        dict(
                            speaker=speaker,
                            name=source_target or speaker,
                            address=source.get("address", ""),
                            origin=source.get("address_origin", ""),
                            known=True,
                            **detail,
                        ),
                    ]
                )
                present.add(key)
    return result


def _originators(row, cards):
    """Resolve named calling actors only; an incidental actor is not a namer."""
    names = {c["name"] for c in cards if c.get("name")}
    for name in sorted(names):
        if name == row["name"] or name not in row["origin"]:
            continue
        tail = row["origin"].split(name, 1)[1]
        # Do not attribute a later named character's calling action to this one.
        for other in names - {name}:
            tail = tail.split(other, 1)[0]
        if re.search(r"直呼|叫|喊|唤|称|(?:取|起)(?:了|的)?(?:名|绰号|外号)", tail):
            yield name


def _origin(value):
    return (
        isinstance(value, str)
        and len(value.strip()) >= 4
        and value.strip().lower() not in {"unknown", "不明", "来历不明"}
    )


def _explanation(text, label, origin):
    return next(
        (
            sentence
            for sentence in re.findall(r"[^。！？!?\n]+[。！？!?]?", text)
            if label in sentence
            and origin in sentence
            and not re.search(r"不是|并非|不曾|并未|没有|无关|[？?]", sentence)
        ),
        "",
    )


def _first_use_check(row, corpus, complete):
    label = row["address"]
    first_use = min((n for n, text in corpus.items() if label in text), default=None)
    if first_use is None or row["known"] and (derived_address(label, row["name"]) or plain_address(label)):
        return None
    text = corpus[first_use]
    start = text.index(label)
    window = range(max(1, first_use - 1), first_use + 2)
    valid = row["known"] and _origin(row["origin"])
    evidence = [
        dict(chapter=n, quote=quote)
        for n in window
        if valid and (quote := _explanation(corpus.get(n, ""), label, row["origin"]))
    ]
    status = (
        "explained"
        if evidence
        else "missing"
        if not valid
        else "pending"
        if not complete and first_use + 1 > max(corpus)
        else "Unknown"
        if any(n not in corpus for n in window if not complete or n <= max(corpus))
        else "missing"
    )
    return dict(
        row,
        kind="address_origin",
        status=status,
        chapter=first_use,
        first_use_chapter=first_use,
        start=start,
        end=start + len(label),
        quote=label,
        evidence=evidence,
        valid_origin=bool(valid),
        message="首次使用非姓名派生称呼，须在本章或前后一章用同句肯定叙述绑定称呼和圣经来历",
    )


def address_checkup(bible, chapters, *, complete=False):
    """Pure readback interface: chapter rows contain number/text, never saved flags.

    complete=True closes the last chapter's future window for a finished book.
    A missing interior chapter stays Unknown; an unwritten next chapter is pending.
    """
    corpus = {c["number"]: c["text"] for c in chapters}
    checks = [check for row in address_rows(bible) if (check := _first_use_check(row, corpus, complete))]
    return dict(
        authority="agent_editorial_not_human",
        counts=dict(Counter(c["status"] for c in checks)),
        checks=checks,
        findings=[c for c in checks if c["status"] != "explained"],
    )


def address_findings(text, bible, earlier, *, number=None):
    """Runtime repair candidates; the whole-book check also inspects future prose."""
    number = number if number is not None else max(earlier, default=0) + 1
    corpus = {**earlier, number: text}
    findings = []
    for row in address_rows(bible):
        label = row["address"]
        if label not in text:
            continue
        check = _first_use_check(row, corpus, complete=False)
        if not check or check["status"] == "explained":
            continue
        start = text.index(label)
        findings.append(
            dict(
                check,
                status="Inference" if check["valid_origin"] and check["first_use_chapter"] == number else "Unknown",
                chapter=number,
                start=start,
                end=start + len(label),
            )
        )
    return findings


def repair_addresses(bible, ask):
    """Bounded, field-only repair. Never mutate an existing saved bible."""
    current = deepcopy(bible)
    attempts = []
    for retry in range(4):
        issues = address_issues(current)
        attempts.append(dict(regeneration=retry, issues=issues))
        if not issues or retry == 3:
            return current, dict(status="failed_continued" if issues else "passed", issues=issues, attempts=attempts)
        raw = ask(
            "address_design", "address-design", [("称呼错误", issues), ("只读人物卡", current["characters"])], retry + 1
        )
        patches = raw.get("characters", []) if isinstance(raw, dict) else []
        for issue in issues:
            card = next(c for c in current["characters"] if c.get("name") == issue["speaker"])
            patch = next((p for p in patches if isinstance(p, dict) and p.get("name") == card.get("name")), {})
            for field in ("address", "address_origin", "address_target"):
                if isinstance(patch.get(field), str) and patch[field].strip():
                    card[field] = patch[field]
