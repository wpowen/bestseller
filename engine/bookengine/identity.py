"""Fixed cast validation and conservative subject-name candidates."""

from pathlib import Path
import re
import yaml
from .precision import _sentences

RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/identity_rules.yaml").read_text())


def validate_bible(bible):
    cards = bible.get("characters", []) if isinstance(bible, dict) else []
    if (
        not isinstance(cards, list)
        or len(cards) < 3
        or not all(
            isinstance(c, dict) and all(isinstance(c.get(k), str) and c[k].strip() for k in ("name", "voice", "desire"))
            for c in cards
        )
        or len({c["name"] for c in cards}) != len(cards)
        or sum(c.get("role") == "protagonist" for c in cards) != 1
    ):
        raise ValueError(
            "Critical story bible: require unique fixed cast, protagonist, two supporting cards, voice and desire"
        )
    return bible


def validate_cast_contract(bible):
    """Validate new speaker-owned address fields without rejecting legacy omissions."""
    from .address_contracts import _originators, derived_address

    cards = bible.get("characters", []) if isinstance(bible, dict) else []
    names = {card.get("name") for card in cards if isinstance(card, dict) and card.get("name")}
    for card in cards:
        gender = card.get("gender")
        if gender is not None and (not isinstance(gender, str) or not gender.strip()):
            raise ValueError("Critical story bible: gender must be a nonblank string when present")
        address, target = card.get("address"), card.get("address_target")
        if not address and not target:
            continue
        if not isinstance(address, str) or not address.strip() or target not in names:
            raise ValueError("Critical story bible: address and known address_target must be paired")
        labels = [part.strip() for part in re.split(r"[/／、，,；;]", address) if part.strip()]
        for label in labels:
            derived = [name for name in names if derived_address(label, name)]
            if len(derived) == 1 and target == card.get("name") and derived[0] != target:
                raise ValueError(
                    "Critical story bible: address belongs on the speaker card and must target its name owner"
                )
        origin = card.get("address_origin", "")
        if target == card.get("name") and isinstance(origin, str):
            if any(name != card.get("name") for name in _originators(dict(name=target, origin=origin), cards)):
                raise ValueError("Critical story bible: target-owned legacy address is not valid for a new bible")
    return bible


def name_hits(text, bible):
    known = {
        n for c in bible.get("characters", []) for n in (c.get("name"), c.get("address")) if isinstance(n, str) and n
    }
    pattern = RULES["subject_pattern"].replace("SURNAME", "".join(RULES["surname"]))
    return [
        dict(id=i, start=a, end=b, name=m[1], kind="name_drift")
        for i, (a, b) in enumerate(_sentences(text), 1)
        for m in re.finditer(pattern, text[a:b])
        if m[1] not in known and m[1] not in RULES["exclude"] and text.count(m[1]) >= RULES["minimum_mentions"]
    ]
