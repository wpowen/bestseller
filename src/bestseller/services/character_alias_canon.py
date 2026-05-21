"""Project-level character name canon.

Backs the ``story-bible/character-aliases.yaml`` file. The canon is the
single source of truth for "which spellings of a name refer to the same
character", and equally important "which spellings must NOT collide".

Without this, the writer drifts between e.g. 周元 and 周元青 (which in
the production novel 道种破虚 ended up being two different characters in
two adjacent chapters, with no reader-side way to tell). With this canon,
``validate_chapter_name_canon`` flags any name variant that isn't on the
allow-list as a violation; the editor is then asked to either pick a
canonical form or register the variant.

The canon is loaded once per chapter validation and is *append-only* across
the lifetime of the project (renames are surfaced to the user, never silent).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CharacterCanonEntry:
    canonical: str
    aliases: tuple[str, ...]                          # includes canonical
    forbidden_collisions: tuple[str, ...] = ()        # names that must point elsewhere
    notes: str = ""

    def all_known_spellings(self) -> tuple[str, ...]:
        seen: list[str] = []
        for name in (self.canonical, *self.aliases):
            if name and name not in seen:
                seen.append(name)
        return tuple(seen)


@dataclass(frozen=True)
class NameCanonViolation:
    """A name appearing in chapter text that violates the canon."""

    spelling: str
    line_no: int
    excerpt: str
    kind: str    # "unknown_name" | "forbidden_collision" | "ambiguous"
    suggestion: str = ""


@dataclass(frozen=True)
class CharacterCanon:
    entries: tuple[CharacterCanonEntry, ...]
    # Reverse index: spelling -> canonical (for fast lookup)
    spelling_to_canonical: dict[str, str] = field(default_factory=dict)
    forbidden_pairs: tuple[tuple[str, str], ...] = ()

    @classmethod
    def empty(cls) -> CharacterCanon:
        return cls(entries=(), spelling_to_canonical={}, forbidden_pairs=())

    def canonical_of(self, spelling: str) -> str | None:
        return self.spelling_to_canonical.get(spelling)

    def is_known(self, spelling: str) -> bool:
        return spelling in self.spelling_to_canonical


# ---------------------------------------------------------------------------
# Loading / building
# ---------------------------------------------------------------------------


_NAME_TOKEN_RE = re.compile(r"[一-鿿]{2,4}")


def _build_index(entries: tuple[CharacterCanonEntry, ...]) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
    spelling_to_canonical: dict[str, str] = {}
    forbidden: list[tuple[str, str]] = []
    for entry in entries:
        for spelling in entry.all_known_spellings():
            existing = spelling_to_canonical.get(spelling)
            if existing and existing != entry.canonical:
                # Duplicate alias claimed by two characters; that itself is a
                # corruption of the canon -- log loudly but prefer the first
                # registration to keep behaviour deterministic.
                logger.warning(
                    "character_alias_canon: spelling %r claimed by both %r and %r; keeping %r",
                    spelling, existing, entry.canonical, existing,
                )
                continue
            spelling_to_canonical[spelling] = entry.canonical
        for blocked in entry.forbidden_collisions:
            forbidden.append((entry.canonical, blocked))
    return spelling_to_canonical, tuple(forbidden)


def load_character_canon(canon_path: Path | str) -> CharacterCanon:
    """Load ``character-aliases.yaml``. Missing file => empty canon (gate is opt-in)."""
    path = Path(canon_path)
    if not path.exists():
        return CharacterCanon.empty()

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.error("character_alias_canon: failed to parse %s: %s", path, exc)
        return CharacterCanon.empty()

    chars = raw.get("characters") or []
    entries: list[CharacterCanonEntry] = []
    for record in chars:
        if not isinstance(record, dict):
            continue
        canonical = str(record.get("canonical") or "").strip()
        if not canonical:
            continue
        aliases_raw = record.get("aliases") or [canonical]
        aliases = tuple(str(a).strip() for a in aliases_raw if str(a).strip())
        forbidden_raw = record.get("forbidden_collisions") or []
        forbidden = tuple(str(f).strip() for f in forbidden_raw if str(f).strip())
        notes = str(record.get("notes") or "").strip()
        entries.append(
            CharacterCanonEntry(
                canonical=canonical,
                aliases=aliases,
                forbidden_collisions=forbidden,
                notes=notes,
            )
        )

    index, forbidden_pairs = _build_index(tuple(entries))
    return CharacterCanon(
        entries=tuple(entries),
        spelling_to_canonical=index,
        forbidden_pairs=forbidden_pairs,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


# Common Chinese honorific / title suffixes that should be stripped before
# canon lookup so 周元青 and 周公子 (where 周公子 is an alias) still match.
_HONORIFIC_SUFFIXES = ("公子", "师兄", "师姐", "长老", "执事", "管事", "前辈", "道友")


# 2-char prose tokens that look name-like to a frequency scanner but are
# common nouns / concepts / particles. Without filtering, ALL of these
# would raise spurious unknown_name warnings.
_NOT_A_NAME = frozenset({
    # Pronouns / references
    "他们", "她们", "你们", "我们", "自己", "别人", "众人", "有人", "无人",
    # Adverbs / connectives
    "突然", "忽然", "刚才", "片刻", "果然", "终于", "原来", "其实", "其中",
    "另一", "其他", "什么", "怎么", "为什么", "已经", "现在", "还是", "果真",
    "也是", "便是", "似乎", "仿佛", "如此", "如是", "应该", "或许",
    "一道", "一片", "一阵", "一种", "像是", "像一", "像被", "像有", "像在",
    "可以", "可能",
    # Body / pose anchors
    "丹田", "掌心", "指尖", "指节", "胸口", "嘴角", "目光", "眉头", "眉宇",
    "肩膀", "脸色", "脖颈", "喉咙", "心脏", "心头", "手中", "怀中", "袖中",
    "胸中", "心中", "眼中", "口中", "嘴中", "腰间", "肩头", "肩上", "膝上",
    "头顶", "身前", "身后", "身上", "身侧", "脚下", "面前", "眼前", "面上",
    "掌中",
    # Time / lighting
    "月光", "晨光", "夜色", "黄昏", "傍晚", "深夜",
    # Generic prose nouns
    "声音", "脚步", "气息", "面容", "影子", "光芒", "光晕", "意识", "神识",
    "记忆", "心思", "念头", "决定", "决心", "性命", "因果", "缘分", "命运",
    # Xianxia jargon (not names)
    "道种", "道典", "灵气", "灵力", "灵根", "筑基", "炼气", "金丹", "元婴",
    "符文", "封印", "封禁", "禁制", "阴阳", "境界", "宗门", "禁地", "按律",
    # Dialogue particles / casual tokens
    "小子", "丫头", "老夫", "老朽", "在下", "晚辈", "前辈", "兄台", "公子",
    "姑娘",
    "瞬间", "一瞬", "顷刻", "霎时", "刹那",
    # Locations that are common nouns
    "门口", "门外", "屋内", "屋外", "丹房", "藏经", "演武", "宿舍", "院子",
    "山门", "废墟", "山道",
})


def _candidate_spellings_in(text: str) -> list[tuple[str, int]]:
    """Surface 2-4 char Han token candidates with line numbers."""
    out: list[tuple[str, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in _NAME_TOKEN_RE.finditer(line):
            token = m.group(0)
            out.append((token, line_no))
    return out


def _strip_honorific(token: str) -> str:
    for suffix in _HONORIFIC_SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[: -len(suffix)]
    return token


def validate_chapter_name_canon(
    chapter_text: str,
    canon: CharacterCanon,
    *,
    min_occurrences_for_alarm: int = 2,
) -> list[NameCanonViolation]:
    """Surface name spellings that violate the canon.

    Args:
        chapter_text: full chapter markdown.
        canon: loaded canon (empty canon => returns []).
        min_occurrences_for_alarm: a token appearing only once may be a
            common-noun coincidence; we only flag tokens appearing >= N times.

    Detection categories:
        * ``forbidden_collision``: text uses a name explicitly listed in some
          entry's ``forbidden_collisions`` -- means it likely got conflated
          with another character.
        * ``unknown_name``: name not in canon at all (writer hallucinated a
          variant). Suggested fix is to register or replace.
    """
    if not canon.entries:
        return []
    if not chapter_text or not chapter_text.strip():
        return []

    # Count candidate name tokens
    counts: dict[str, int] = {}
    first_seen_line: dict[str, int] = {}
    first_seen_line_text: dict[str, str] = {}
    lines = chapter_text.splitlines()
    for token, line_no in _candidate_spellings_in(chapter_text):
        counts[token] = counts.get(token, 0) + 1
        if token not in first_seen_line:
            first_seen_line[token] = line_no
            first_seen_line_text[token] = lines[line_no - 1] if line_no - 1 < len(lines) else ""

    # Build the set of forbidden spellings across all entries
    forbidden_spellings: set[str] = set()
    for entry in canon.entries:
        forbidden_spellings.update(entry.forbidden_collisions)

    # Build "the set of name-like tokens that look real" -- 3-char Han words
    # appearing >= min_occurrences_for_alarm times anywhere in the chapter.
    candidates: list[tuple[str, int]] = [
        (tok, counts[tok])
        for tok in counts
        if counts[tok] >= min_occurrences_for_alarm and len(tok) >= 2
    ]

    violations: list[NameCanonViolation] = []
    for token, _occ in candidates:
        stripped = _strip_honorific(token)
        # Resolve token against canon
        if canon.is_known(token) or canon.is_known(stripped):
            continue

        if token in forbidden_spellings:
            # Find which canonical entry rejected this spelling
            blocking: list[str] = []
            for entry in canon.entries:
                if token in entry.forbidden_collisions:
                    blocking.append(entry.canonical)
            violations.append(
                NameCanonViolation(
                    spelling=token,
                    line_no=first_seen_line.get(token, 0),
                    excerpt=first_seen_line_text.get(token, "")[:120],
                    kind="forbidden_collision",
                    suggestion=(
                        f"'{token}' 与角色 {blocking} 易混。请改用其 canonical 名"
                        f"，或在 character-aliases.yaml 中注册新角色。"
                    ),
                )
            )
            continue

        # Heuristic: is this likely a name? Yes if it looks like surname+given
        # (1 surname char + 1-2 given chars). We use a loose filter: any
        # 2-3 char Han token appearing >= 3 times, not in the common-noun
        # denylist, not stripping to a known canonical via honorific.
        if token in _NOT_A_NAME or _strip_honorific(token) in _NOT_A_NAME:
            continue
        if counts[token] >= 3 and len(token) in (2, 3):
            similar = _find_similar_canonical(token, canon)
            suggestion = (
                f"'{token}' 未在 character-aliases.yaml 中登记。"
                + (f"可能是 '{similar}' 的别名变体？" if similar else "若为新角色请添加；若是错写请改回 canonical。")
            )
            violations.append(
                NameCanonViolation(
                    spelling=token,
                    line_no=first_seen_line.get(token, 0),
                    excerpt=first_seen_line_text.get(token, "")[:120],
                    kind="unknown_name",
                    suggestion=suggestion,
                )
            )

    return violations


def _find_similar_canonical(token: str, canon: CharacterCanon) -> str | None:
    """Heuristic: find a registered name that shares ≥ 2 chars with token."""
    best: tuple[str, int] | None = None
    for spelling in canon.spelling_to_canonical:
        shared = sum(1 for c in token if c in spelling)
        if shared >= 2 and (best is None or shared > best[1]):
            best = (canon.spelling_to_canonical[spelling], shared)
    return best[0] if best else None


def build_name_canon_repair_prompt(violations: list[NameCanonViolation]) -> str:
    """Render an editor-facing instruction listing each name canon violation."""
    if not violations:
        return ""
    bullets: list[str] = []
    for v in violations:
        bullets.append(
            f"- L{v.line_no} 「{v.spelling}」 — {v.kind}\n"
            f"  现场：{v.excerpt}\n"
            f"  建议：{v.suggestion}"
        )
    return (
        "【人名 Canon 违规修复】\n"
        "以下名字未在 story-bible/character-aliases.yaml 中登记，或属于显式禁碰撞集合。\n"
        "请按建议替换为 canonical 形式（首选），或在 yaml 中追加新条目（仅当确为新角色时）。\n\n"
        + "\n".join(bullets)
    )


__all__ = [
    "CharacterCanonEntry",
    "CharacterCanon",
    "NameCanonViolation",
    "load_character_canon",
    "validate_chapter_name_canon",
    "build_name_canon_repair_prompt",
]
