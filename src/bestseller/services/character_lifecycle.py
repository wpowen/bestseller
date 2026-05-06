"""Character lifecycle predicates and prose helpers.

Centralises the "is this character actually dead in chapter N?" decision
and the "is this prose passage a flashback / memorial / reference?"
heuristic, so every consumer (planner, writer prompt, contradiction
checks, post-write scanner) reaches the same conclusion.

Design philosophy
-----------------

The system already has ``alive_status`` and ``death_chapter_number`` on
``CharacterModel``. Three pieces of nuance were missing:

1. **Fake death** — a character can have a planned "death" that is
   later revealed to be a ruse. The reveal chapter is stored as
   ``CharacterModel.metadata_json.fake_death.revealed_chapter``. After
   that chapter the character may act normally again.

2. **Flashback / memorial / quoted use of a deceased character** — once
   a character is dead in the current chapter sense, they may still
   appear in prose as memory, in a will / letter / recording, in
   another character's reminiscence, or as a corpse / funeral image.
   The hard constraint is only that they may NOT take present-tense
   actions or speak new dialogue.

3. **Scene-mode annotations** — scenes may carry a
   ``metadata_json.scene_mode`` of ``flashback`` / ``memorial`` /
   ``vision`` / ``dream`` / ``quoted_reference``, or have
   ``scene_type == 'flashback'``. Either signal exempts the scene from
   the "no deceased participants" rule.

This module is dependency-light (stdlib + typing only) so it can be
imported by everything from the planner to the scanner without
circular-import risk.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

__all__ = [
    "FLASHBACK_MARKERS_ZH",
    "FLASHBACK_MARKERS_EN",
    "FLASHBACK_SCENE_MODES",
    "is_character_dead_at_chapter",
    "fake_death_revealed_at_chapter",
    "scene_is_flashback_like",
    "prose_window_is_flashback",
]


# Scene-mode strings that exempt the scene from "deceased may not appear"
# checks. The set is intentionally loose because different planners /
# importers spell things slightly differently.
FLASHBACK_SCENE_MODES: frozenset[str] = frozenset({
    "flashback",
    "memorial",
    "vision",
    "dream",
    "quoted_reference",
    "reminiscence",
    "回忆",
    "闪回",
    "祭奠",
    "梦境",
    "幻象",
})


# Prose markers that strongly suggest the surrounding sentence is a
# flashback, memorial reference, or quoted-from-the-past framing rather
# than present-tense action. The CJK list covers wuxia / xianxia /
# realistic fiction registers; the EN list covers the most common
# English flashback markers. Both lists are deliberately conservative:
# we accept some false negatives so we never silence a real bug.
FLASHBACK_MARKERS_ZH: tuple[str, ...] = (
    # Memory / reminiscence
    "回想", "回忆", "想起", "记得", "忆起", "记忆里", "记忆中",
    "脑海中", "脑海里", "梦中", "梦里", "幻象中",
    # Past-time anchors
    "曾经", "当年", "那时", "那年", "从前", "往昔", "昔日",
    "数年前", "多年前", "若干年前", "上一世", "前世",
    # Mortuary / posthumous framing
    "葬礼", "灵堂", "坟前", "墓前", "祭奠", "追忆", "追思",
    "遗书", "遗物", "遗言", "家书", "残卷", "残音", "录音",
    "老照片", "影像", "画像", "牌位",
    # Speech-act framing
    "曾说", "曾对", "曾告诉", "曾叮嘱", "留下的话",
)

FLASHBACK_MARKERS_EN: tuple[str, ...] = (
    "remembered", "recalled", "recalling", "thought back",
    "had once", "used to", "years ago", "long ago", "back then",
    "in his memory", "in her memory", "in their memory",
    "the funeral", "her grave", "his grave", "the wake",
    "the will", "the letter", "the recording",
    "as he had said", "as she had said", "had told him",
    "had told her", "had warned", "had whispered",
)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def fake_death_revealed_at_chapter(
    character_metadata: Mapping[str, Any] | None,
    chapter_number: int,
) -> bool:
    """Return True when the character has a fake-death record AND the
    reveal chapter is at or before ``chapter_number``.

    Stored shape::

        metadata_json.fake_death = {"revealed_chapter": int}

    A missing or unparsable record yields ``False`` — i.e. the death is
    treated as real.
    """

    meta = _as_dict(character_metadata)
    fake = meta.get("fake_death")
    if not isinstance(fake, Mapping):
        return False
    revealed = fake.get("revealed_chapter")
    try:
        revealed_int = int(revealed) if revealed is not None else None
    except (TypeError, ValueError):
        return False
    if revealed_int is None:
        return False
    return revealed_int <= int(chapter_number)


def is_character_dead_at_chapter(
    *,
    death_chapter_number: int | None,
    chapter_number: int,
    character_metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Single-source-of-truth predicate: is this character actually dead
    in the present-tense of ``chapter_number``?

    Logic
    -----
    * No ``death_chapter_number`` → not dead.
    * ``death_chapter_number > chapter_number`` → not yet dead (planner
      schedules the death later — caller may want a "must survive"
      constraint instead).
    * ``death_chapter_number <= chapter_number`` AND fake death has been
      revealed at or before ``chapter_number`` → not dead (the death
      was a ruse and the truth is out).
    * Otherwise → dead.
    """

    if death_chapter_number is None:
        return False
    if int(death_chapter_number) > int(chapter_number):
        return False
    if fake_death_revealed_at_chapter(character_metadata, chapter_number):
        return False
    return True


def scene_is_flashback_like(scene: Any) -> bool:
    """Return True when a scene is annotated as flashback, memorial,
    vision, dream, or quoted reference.

    Recognises three sources of truth:
    1. ``scene.scene_type`` exact match against ``FLASHBACK_SCENE_MODES``.
    2. ``scene.metadata_json.scene_mode`` exact match.
    3. ``scene.metadata_json.is_flashback`` truthy.

    Accepts dicts, ORM rows, dataclasses, or pydantic models — anything
    with attribute or item access.
    """

    if scene is None:
        return False

    def _get(key: str) -> Any:
        if isinstance(scene, Mapping):
            return scene.get(key)
        return getattr(scene, key, None)

    scene_type = _get("scene_type")
    if isinstance(scene_type, str) and scene_type.strip().lower() in FLASHBACK_SCENE_MODES:
        return True

    metadata = _get("metadata_json") or _get("metadata")
    meta = _as_dict(metadata)
    mode = meta.get("scene_mode")
    if isinstance(mode, str) and mode.strip().lower() in FLASHBACK_SCENE_MODES:
        return True
    if bool(meta.get("is_flashback")):
        return True

    return False


# Pre-compiled patterns so the proximity scan is cheap on long chapters.
_FLASHBACK_RE_ZH = re.compile("|".join(re.escape(m) for m in FLASHBACK_MARKERS_ZH))
_FLASHBACK_RE_EN = re.compile(
    "|".join(re.escape(m) for m in FLASHBACK_MARKERS_EN),
    flags=re.IGNORECASE,
)


def prose_window_is_flashback(
    text: str,
    *,
    is_english: bool = False,
) -> bool:
    """Return True when ``text`` (a small window around an event of
    interest — typically <= ~80 chars) contains a flashback marker.

    Used by the post-write death scanner to downgrade or skip findings
    when the death keyword is sitting inside an obvious memory passage.
    Conservative by design: false negatives (missing a real flashback)
    are preferable to false positives (silencing a real death leak).
    """

    if not text:
        return False
    pattern = _FLASHBACK_RE_EN if is_english else _FLASHBACK_RE_ZH
    return bool(pattern.search(text))


def filter_alive_at_chapter(
    rows: Iterable[Any],
    chapter_number: int,
) -> list[Any]:
    """Return only those rows whose character is *alive* in the
    present-tense of ``chapter_number``. Each row must expose
    ``death_chapter_number`` and ``metadata_json``.

    Convenience helper for the deceased-roster query — pulls all
    candidates from SQL, then post-filters with the fake-death rule.
    """

    out: list[Any] = []
    for row in rows:
        death_ch = getattr(row, "death_chapter_number", None)
        meta = getattr(row, "metadata_json", None)
        if not is_character_dead_at_chapter(
            death_chapter_number=death_ch,
            chapter_number=chapter_number,
            character_metadata=meta,
        ):
            out.append(row)
    return out
