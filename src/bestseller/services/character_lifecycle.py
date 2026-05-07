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
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

__all__ = [
    "FLASHBACK_MARKERS_ZH",
    "FLASHBACK_MARKERS_EN",
    "FLASHBACK_SCENE_MODES",
    "LIFECYCLE_KINDS",
    "OFFSTAGE_KINDS",
    "is_character_dead_at_chapter",
    "fake_death_revealed_at_chapter",
    "scene_is_flashback_like",
    "prose_window_is_flashback",
    "filter_alive_at_chapter",
    "effective_lifecycle_state",
    "appearance_rule_for",
    "characters_offstage_at_chapter",
]


# ---------------------------------------------------------------------------
# Lifecycle taxonomy
# ---------------------------------------------------------------------------

# Canonical lifecycle kinds the engine recognises. The first 4 mirror the
# legacy ``alive_status`` enum (kept for SQL compatibility); the rest are
# rich states that live in ``metadata_json.lifecycle_status.kind`` because
# adding columns requires a migration and these states often co-exist
# with an alive_status row (e.g. a character marked alive in the DB but
# kept off-stage by a "sealed" lifecycle annotation).
LIFECYCLE_KINDS: tuple[str, ...] = (
    # Default — fully present, may speak/act/be a participant.
    "alive",
    # Alive but visibly hurt; may still act.
    "injured",
    # Actively dying; may speak final words but cannot drive future
    # scenes after the death event commits.
    "dying",
    # Death event has occurred (and not retracted by fake-death reveal).
    "deceased",
    # Whereabouts unknown to the in-story world. Cannot appear as an
    # active participant; MAY return at a later chapter without
    # triggering the resurrection check.
    "missing",
    # Locked away by formation / spell / institution. Cannot appear
    # until released; release chapter is in metadata when planned.
    "sealed",
    # Long sleep / cryostasis / coma — body present but no agency.
    # Cannot drive scenes; may be referenced as a body that someone
    # else acts upon.
    "sleeping",
    # Comatose — same restrictions as sleeping but in non-magical
    # registers (medical / mundane).
    "comatose",
    # Banished from the active locale. May appear in scenes that go
    # to the place they were exiled to, but cannot return to the
    # main stage until recalled.
    "exiled",
)

# States that forbid present-tense scene participation (i.e. "登场"
# in the user's words). The character may still be remembered,
# quoted, mourned, treated as a body / image / letter / relic, or
# appear in clearly-labelled flashback / vision / dream scenes.
OFFSTAGE_KINDS: frozenset[str] = frozenset({
    "deceased",
    "missing",
    "sealed",
    "sleeping",
    "comatose",
    # ``exiled`` is offstage *for the home setting* but allowed to
    # appear in scenes set at the exile location — handled at the
    # appearance-rule level, not flat-blocked here.
})


@dataclass(frozen=True)
class AppearanceRule:
    """Per-state rules for whether a character may appear in a scene."""

    kind: str
    can_act_in_present: bool          # speak new dialogue, take actions
    can_appear_as_body: bool          # corpse / sleeping form / sealed shell
    can_be_remembered: bool           # quoted, mourned, referenced
    can_appear_in_flashback: bool     # in scene_type=flashback / vision / dream
    can_return_without_resurrection_block: bool  # missing → returns naturally
    notes_zh: str
    notes_en: str


_APPEARANCE_RULES: dict[str, AppearanceRule] = {
    "alive": AppearanceRule(
        kind="alive",
        can_act_in_present=True,
        can_appear_as_body=True,
        can_be_remembered=True,
        can_appear_in_flashback=True,
        can_return_without_resurrection_block=True,
        notes_zh="正常出场。",
        notes_en="Normal participation.",
    ),
    "injured": AppearanceRule(
        kind="injured",
        can_act_in_present=True,
        can_appear_as_body=True,
        can_be_remembered=True,
        can_appear_in_flashback=True,
        can_return_without_resurrection_block=True,
        notes_zh="可正常出场，但需体现伤情对动作/语调的拖累。",
        notes_en="May act, but injuries should drag actions / tone.",
    ),
    "dying": AppearanceRule(
        kind="dying",
        can_act_in_present=True,
        can_appear_as_body=True,
        can_be_remembered=True,
        can_appear_in_flashback=True,
        can_return_without_resurrection_block=False,
        notes_zh="生命垂危，对白与动作受限于濒死状态。",
        notes_en="At death's door — speech/action limited.",
    ),
    "deceased": AppearanceRule(
        kind="deceased",
        can_act_in_present=False,
        can_appear_as_body=True,
        can_be_remembered=True,
        can_appear_in_flashback=True,
        can_return_without_resurrection_block=False,
        notes_zh="不可登场。可被怀念/引用/作为遗体或信物提及；闪回/祭奠场景例外。",
        notes_en="No present-tense action. May be remembered, quoted, "
        "appear as corpse / relic, or in flashback / memorial scenes.",
    ),
    "missing": AppearanceRule(
        kind="missing",
        can_act_in_present=False,
        can_appear_as_body=False,
        can_be_remembered=True,
        can_appear_in_flashback=True,
        can_return_without_resurrection_block=True,
        notes_zh="下落不明，本章不可登场。但他可在未来任何章节回归——"
        "回归时不会触发『复活』违规。",
        notes_en="Whereabouts unknown — no present scene participation. "
        "May reappear in any future chapter without triggering the "
        "resurrection check.",
    ),
    "sealed": AppearanceRule(
        kind="sealed",
        can_act_in_present=False,
        can_appear_as_body=True,
        can_be_remembered=True,
        can_appear_in_flashback=True,
        can_return_without_resurrection_block=True,
        notes_zh="被封印，本章不可主动行动或对话。可作为封印体被提及；"
        "解封章节确定后，需按计划解封后再恢复活动。",
        notes_en="Sealed — no present action or dialogue. May be "
        "referenced as a sealed form; release follows the planned "
        "release chapter.",
    ),
    "sleeping": AppearanceRule(
        kind="sleeping",
        can_act_in_present=False,
        can_appear_as_body=True,
        can_be_remembered=True,
        can_appear_in_flashback=True,
        can_return_without_resurrection_block=True,
        notes_zh="处于沉睡/休眠状态，本章不可发出当下动作或对话。"
        "可作为肉身被他人照看、移动、保护。",
        notes_en="Sleeping / dormant — no present action. May be tended, "
        "moved, or guarded as a body by other characters.",
    ),
    "comatose": AppearanceRule(
        kind="comatose",
        can_act_in_present=False,
        can_appear_as_body=True,
        can_be_remembered=True,
        can_appear_in_flashback=True,
        can_return_without_resurrection_block=True,
        notes_zh="昏迷，本章不可发出当下动作或对话。可作为病榻上的身体被照护。",
        notes_en="Comatose — no present action. May be tended as a body.",
    ),
    "exiled": AppearanceRule(
        kind="exiled",
        can_act_in_present=True,  # only at exile location — caller must judge
        can_appear_as_body=True,
        can_be_remembered=True,
        can_appear_in_flashback=True,
        can_return_without_resurrection_block=True,
        notes_zh="被流放，原舞台不可登场。仅可在流放地相关场景中出场。",
        notes_en="Exiled from the main stage. May only appear in scenes "
        "set at the exile location.",
    ),
}


def appearance_rule_for(kind: str | None) -> AppearanceRule:
    """Look up the canonical appearance rule for a lifecycle kind.

    Returns the ``alive`` rule when the kind is unknown / missing — a
    permissive default avoids false positives on legacy data.
    """

    if isinstance(kind, str) and kind.strip().lower() in _APPEARANCE_RULES:
        return _APPEARANCE_RULES[kind.strip().lower()]
    return _APPEARANCE_RULES["alive"]


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


def effective_lifecycle_state(
    *,
    alive_status: str | None,
    death_chapter_number: int | None,
    chapter_number: int,
    character_metadata: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Resolve a character's effective lifecycle kind in chapter N.

    Returns ``(kind, payload)`` where ``payload`` is the rich record
    pulled from ``metadata_json.lifecycle_status`` (or built from
    legacy fields when no rich record exists).

    Resolution priority — the first match wins:

    1. ``metadata_json.lifecycle_status`` if it has a recognised
       ``kind`` AND its ``since_chapter`` is on/before this chapter
       AND its ``scheduled_exit_chapter`` is in the future / unset.
    2. Death timeline: ``death_chapter_number <= N`` AND no fake-death
       reveal yet → ``deceased``.
    3. Legacy ``alive_status`` column (``alive`` / ``injured`` /
       ``dying``).

    Callers use this to drive ``appearance_rule_for`` when deciding
    whether a participant slot is allowed.
    """

    meta = _as_dict(character_metadata)

    # 1. Rich lifecycle record — preferred when present and active.
    rich = meta.get("lifecycle_status")
    if isinstance(rich, Mapping):
        kind = rich.get("kind")
        if isinstance(kind, str) and kind.strip().lower() in LIFECYCLE_KINDS:
            since = rich.get("since_chapter")
            try:
                since_int = int(since) if since is not None else None
            except (TypeError, ValueError):
                since_int = None
            exit_ch = rich.get("scheduled_exit_chapter")
            try:
                exit_int = int(exit_ch) if exit_ch is not None else None
            except (TypeError, ValueError):
                exit_int = None
            if (since_int is None or since_int <= int(chapter_number)) and (
                exit_int is None or exit_int > int(chapter_number)
            ):
                return kind.strip().lower(), dict(rich)

    # 2. Death timeline overrides legacy alive_status.
    if is_character_dead_at_chapter(
        death_chapter_number=death_chapter_number,
        chapter_number=chapter_number,
        character_metadata=character_metadata,
    ):
        return "deceased", {
            "kind": "deceased",
            "since_chapter": death_chapter_number,
            "source": "death_chapter_number",
        }

    # 3. Legacy alive_status fallback.
    legacy = (alive_status or "alive").strip().lower()
    if legacy not in LIFECYCLE_KINDS:
        legacy = "alive"
    return legacy, {"kind": legacy, "source": "alive_status"}


def characters_offstage_at_chapter(
    rows: Iterable[Any],
    chapter_number: int,
) -> list[tuple[Any, str, dict[str, Any]]]:
    """Return ``(row, kind, payload)`` triples for characters whose
    effective lifecycle kind forbids present-tense scene participation.

    "Offstage" here means the rule says ``can_act_in_present == False``
    OR the kind is in :data:`OFFSTAGE_KINDS`. The exiled state is left
    out — exile is location-conditional and the caller decides scene
    by scene.
    """

    out: list[tuple[Any, str, dict[str, Any]]] = []
    for row in rows:
        kind, payload = effective_lifecycle_state(
            alive_status=getattr(row, "alive_status", None),
            death_chapter_number=getattr(row, "death_chapter_number", None),
            chapter_number=chapter_number,
            character_metadata=getattr(row, "metadata_json", None),
        )
        if kind in OFFSTAGE_KINDS:
            out.append((row, kind, payload))
    return out


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
