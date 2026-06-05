"""Genre-neutral signal patterns for planning / outline gates.

These replace per-book hardcoded object/jargon/character lists that leaked one
detective book's cast (青囊不语问阴阳: 铜钱/青囊/罗盘/王建业…) into every project's
planning gates. The patterns here detect the *shape* of a quality smell (a physical
object repeatedly reduced to a "发烫" sensory shortcut instead of an inferable rule)
for ANY genre, without naming a specific book's props or characters.
"""

from __future__ import annotations

import re

# Body / sensation nouns that legitimately run hot, cold, or numb as a *human*
# reaction — these are NOT object-signal shortcuts and must not be flagged.
_BODY_OR_SENSATION_NOUN = (
    "脸|脸颊|面颊|额|额头|耳|耳根|耳尖|脖|脖子|后颈|颈|手|手心|手指|掌心|"
    "心|心口|心头|胸|胸口|后背|脊背|眼|眼眶|眼睛|喉|喉咙|嘴|嘴唇|唇|"
    "身子|身体|浑身|全身|皮肤|血液|血|脑袋|头|太阳穴|鼻|鼻尖|舌|舌尖"
)

# Sensory-shortcut verbs: making an object 发烫/发凉/刺痛 repeatedly, in lieu of giving
# it stable, inferable rules, is a planning/prose smell in every genre.
_SENSORY_SHORTCUT_VERB = (
    "发烫|烫得|滚烫|发热|炽热|灼热|发凉|冰凉|冰冷|刺痛|刺麻|发麻|震颤|嗡鸣|嗡嗡"
)

# An OBJECT noun (1-4 trailing CJK chars) immediately made to 发烫/发凉/… The noun is
# captured so we can reject body/sensation nouns by suffix (e.g. 她脸发烫 → noun 她脸,
# ends with 脸 → a human reaction, not an object signal).
OBJECT_SENSORY_SHORTCUT_PATTERN = re.compile(
    rf"(?P<noun>[一-鿿]{{1,4}}?)(?P<verb>{_SENSORY_SHORTCUT_VERB})"
)
_BODY_NOUN_CHARS = set("".join(_BODY_OR_SENSATION_NOUN.split("|")))


def object_sensory_shortcut_hits(text: str) -> int:
    """Count object-sensory-shortcut occurrences in ``text`` (body-part reactions
    excluded). Used by planning gates to flag over-reliance on the 发烫 shortcut."""

    if not text:
        return 0
    hits = 0
    for match in OBJECT_SENSORY_SHORTCUT_PATTERN.finditer(text):
        noun = match.group("noun")
        if not noun:
            continue
        # Reject if the noun ends in a body/sensation char (它's a human reaction)
        # or is itself entirely body/sensation chars.
        if noun[-1] in _BODY_NOUN_CHARS:
            continue
        hits += 1
    return hits


__all__ = [
    "OBJECT_SENSORY_SHORTCUT_PATTERN",
    "object_sensory_shortcut_hits",
]
