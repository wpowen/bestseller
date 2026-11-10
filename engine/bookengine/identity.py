"""Fixed cast validation and conservative subject-name candidates."""

from copy import deepcopy
from pathlib import Path
import re
import yaml
from .precision import _sentences
from .character_context import identity_keywords

RULES = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/identity_rules.yaml").read_text())
_NONHUMAN_COMPANION = re.compile(r"契兽|灵宠|幼兽|兽宠|宠兽|战宠|灵兽|妖兽|神兽|兽形|兽身")
_ROMANCE_ROLE = re.compile(r"恋爱对象|感情对象|恋人|爱情线|感情线|官配|CP|相恋|谈恋爱")


def _identity_replacements(bible):
    result = {}
    for card in bible.get("characters", []) if isinstance(bible, dict) else []:
        identity = card.get("identity") if isinstance(card, dict) else None
        if not isinstance(identity, str) or not identity.strip():
            continue
        keywords = identity_keywords(identity, card.get("occupation", ""))
        result[identity] = "、".join(keywords) or "用行动交代来历"
    return result


def _replace_identity_text(value, replacements):
    if isinstance(value, str):
        for original, replacement in replacements.items():
            value = value.replace(original, replacement)
        return value
    if isinstance(value, dict):
        return {key: _replace_identity_text(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_identity_text(item, replacements) for item in value]
    return value


def writer_identity_cards(bible):
    """Return prompt-safe cards with role keywords instead of copyable identity prose."""
    cards = deepcopy(bible.get("characters", []) if isinstance(bible, dict) else [])
    for card in cards:
        identity = card.pop("identity", "") if isinstance(card, dict) else ""
        if identity:
            card["identity_keywords"] = identity_keywords(identity, card.get("occupation", ""))
    return cards


def writer_identity_bible(bible):
    """Build a writer-only bible view without changing saved design facts."""
    current = _replace_identity_text(deepcopy(bible), _identity_replacements(bible))
    if isinstance(current, dict):
        current["characters"] = writer_identity_cards(bible)
    return current


def writer_identity_outline(outline, bible):
    """Remove identity literals from introductions and model-authored scene text."""
    return _replace_identity_text(deepcopy(outline), _identity_replacements(bible))


def _explicit_companion_romance(seed_text, card):
    if not isinstance(seed_text, str):
        return False
    for clause in re.split(r"[。！？!?；;\n]", seed_text):
        if not _ROMANCE_ROLE.search(clause):
            continue
        name = card.get("name")
        if isinstance(name, str) and name and name in clause:
            return True
        if re.search(
            r"(?:契兽|灵宠|幼兽|兽宠|宠兽|战宠|灵兽|妖兽|神兽)"
            r"[^，,；;。！？!?\n]{0,18}(?:恋爱对象|感情对象|恋人|爱情线|感情线|官配|CP|相恋|谈恋爱)|"
            r"(?:恋爱对象|感情对象|恋人|爱情线|感情线|官配|CP)"
            r"[^，,；;。！？!?\n]{0,18}(?:契兽|灵宠|幼兽|兽宠|宠兽|战宠|灵兽|妖兽|神兽)",
            clause,
        ):
            return True
    return False


def repair_companion_roles(bible, seed_text=""):
    """Correct model-inferred nonhuman romance roles unless the seed explicitly requests one."""
    repaired = deepcopy(bible)
    corrections = []
    cards = repaired.get("characters", []) if isinstance(repaired, dict) else []
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict) or card.get("role") != "romance":
            continue
        identity = " ".join(str(card.get(key, "")) for key in ("identity", "species", "type"))
        if not _NONHUMAN_COMPANION.search(identity) or _explicit_companion_romance(seed_text, card):
            continue
        card["role"] = "support"
        corrections.append(
            dict(
                name=card.get("name", ""),
                **{"from": "romance", "to": "support"},
                reason="nonhuman_companion_not_explicit_romance",
            )
        )
    return repaired, corrections


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
