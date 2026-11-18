"""Keep motif families the selected card never had out of the generated bible.

Ideation already caps each overused motif family (data/ideation/motifs.yaml) per batch, but
only on the card. The bible is written afterwards and its retries can bring the family back:
《摆摊真的能转运吗》 had no debt words on its first prepare attempt and 22 on its fourth,
which made the antagonist a credit-stall owner and a foreshadow a black debt slip.

The vocabulary is a detector only. It is never sent to the model; a retry quotes the
offending sentences and asks for something grown from the card instead.
"""

import json
from pathlib import Path
import re

import yaml

MOTIFS = Path(__file__).resolve().parents[1] / "data/ideation/motifs.yaml"
CARD_FIELDS = ("title", "hook", "premise", "golden_finger_rule", "opening", "engine", "tags")
QUOTE_LIMIT = 4
_CLAUSE = re.compile(r"[^。！？；\n]+")


def families():
    data = yaml.safe_load(MOTIFS.read_text("utf-8")) or {}
    return {name: tokens for name, tokens in (data.get("families") or {}).items() if tokens}


def _strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _strings(item)]
    return []


def card_families(card):
    text = json.dumps({k: card.get(k) for k in CARD_FIELDS}, ensure_ascii=False)
    return {name for name, tokens in families().items() if any(token in text for token in tokens)}


def foreign_motifs(value, card):
    """{family: [clauses]} for families present in ``value`` but absent from the card."""
    allowed = card_families(card)
    found = {}
    for text in _strings(value):
        for clause in _CLAUSE.findall(text):
            for name, tokens in families().items():
                if name not in allowed and any(token in clause for token in tokens):
                    rows = found.setdefault(name, [])
                    if clause.strip() not in rows:
                        rows.append(clause.strip())
    return found


def motif_count(found):
    return sum(len(rows) for rows in found.values())


def retry_note(found):
    quotes = [quote for rows in found.values() for quote in rows][:QUOTE_LIMIT]
    return (
        "下面这些设定是选题卡里没有的母题，换成从卡片本身长出来的东西（人物身份、物件、伏笔都一样），其余保留："
        + "；".join(f"“{quote}”" for quote in quotes)
    )


def best_attempt(attempts):
    """Fewest foreign-motif clauses, then fewest mechanism drifts; ties keep the latest attempt."""
    return min(attempts, key=lambda row: (motif_count(row["foreign"]), len(row["mechanism"]), -row["redo"]))
