"""Address derivation, explicit origins and first-use evidence (legacy target aware)."""

from copy import deepcopy
import re


def address_rows(bible):
    cards = bible.get("characters", [])
    names = {c.get("name") for c in cards if isinstance(c, dict)}
    lead = next((c.get("name") for c in cards if c.get("role") == "protagonist"), "")
    for card in cards:
        address = card.get("address")
        if not isinstance(address, str) or not address.strip():
            continue
        for label in re.split(r"[/／、，,；;]", address):
            label = label.strip()
            if not label:
                continue
            # Historical address describes how the speaker addresses the lead;
            # it must never register the speaker as the owner of that alias.
            target = card.get("address_target") or (label if label in names else lead or card.get("name"))
            origin = card.get("address_origin", "")
            origin = origin.get(label, "") if isinstance(origin, dict) else origin
            yield dict(speaker=card.get("name"), name=target, address=label, origin=origin, known=target in names)


def derived_address(label, name):
    if not isinstance(name, str) or not name:
        return False
    stem = re.sub(r"^(?:阿|小|老)", "", label)
    stem = re.sub(r"(?:师傅|师父|先生|姑娘|公子|师兄|师姐|师弟|师妹|哥|姐|弟|妹|叔|伯|爷)$", "", stem)
    return bool(stem and stem in name)


def address_issues(bible):
    return [
        dict(row, kind="address_origin", message="称呼须从目标姓名派生，否则填写address_origin并在首次使用章交代")
        for row in address_rows(bible)
        if not row["known"] or not derived_address(row["address"], row["name"]) and not _origin(row["origin"])
    ]


def _origin(value):
    return (
        isinstance(value, str)
        and len(value.strip()) >= 4
        and value.strip().lower() not in {"unknown", "不明", "来历不明"}
    )


def _explained(text, label, origin):
    return any(
        label in sentence and origin in sentence and not re.search(r"不是|并非|不曾|并未|没有|无关|[？?]", sentence)
        for sentence in re.findall(r"[^。！？!?\n]+[。！？!?]?", text)
    )


def address_findings(text, bible, earlier):
    """Only a locally bound, non-negated origin can discharge first-use duty."""
    findings, seen = [], set()
    for row in address_rows(bible):
        label = row["address"]
        if label in seen or label not in text:
            continue
        seen.add(label)
        if row["known"] and derived_address(label, row["name"]):
            continue
        # Missing bible origin is still an issue even if a previous chapter used it.
        valid = row["known"] and _origin(row["origin"])
        if valid and any(_explained(previous, label, row["origin"]) for previous in earlier.values()):
            continue
        first_use = min((chapter for chapter, previous in earlier.items() if label in previous), default=None)
        start = text.index(label)
        end = start + len(label)
        sentence = next(
            (m.group() for m in re.finditer(r"[^。！？!?\n]+[。！？!?]?", text) if m.start() <= start < m.end()), ""
        )
        if valid and row["origin"] in sentence and not re.search(r"不是|并非|不曾|并未|没有|无关|[？?]", sentence):
            continue
        findings.append(
            dict(
                row,
                kind="address_origin",
                status="Inference" if valid and first_use is None else "Unknown",
                first_use_chapter=first_use,
                start=start,
                end=end,
                quote=text[start:end],
                message="首次使用非姓名派生称呼，需在同句交代圣经登记的来历；不能把称呼当说话人的别名",
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
