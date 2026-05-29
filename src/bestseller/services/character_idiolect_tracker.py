from __future__ import annotations

# ruff: noqa: RUF001
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel


@dataclass(frozen=True)
class CharacterActionProfile:
    character_name: str
    action_verbs_used: Mapping[str, int]
    top_overused_actions: tuple[str, ...]
    diversity_score: float
    chapters_seen: int


_ACTION_VERBS = tuple(
    "盯 握 把 按 抓 看 望 瞥 扫 摸 推 拉 扣 退 站 走 跑 停 举 落 转 贴 敲 咬".split()
)


async def compute_character_idiolect(
    session: AsyncSession,
    project: ProjectModel,
    character_name: str,
    *,
    chapter_number_upto: int | None = None,
) -> CharacterActionProfile:
    query = (
        select(ChapterModel.chapter_number, ChapterDraftVersionModel.content_md)
        .join(ChapterDraftVersionModel, ChapterDraftVersionModel.chapter_id == ChapterModel.id)
        .where(
            ChapterModel.project_id == project.id,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .order_by(ChapterModel.chapter_number.asc())
    )
    if chapter_number_upto is not None:
        query = query.where(ChapterModel.chapter_number < int(chapter_number_upto))
    result = await session.execute(query)
    return compute_character_idiolect_from_texts(
        character_name,
        tuple((int(ch), str(text or "")) for ch, text in result.all()),
    )


def compute_character_idiolect_from_texts(
    character_name: str,
    chapter_texts: Sequence[tuple[int, str]],
) -> CharacterActionProfile:
    counter: Counter[str] = Counter()
    chapters_seen: set[int] = set()
    name = str(character_name or "").strip()
    if not name:
        return CharacterActionProfile("", {}, (), 1.0, 0)
    pattern = re.compile(re.escape(name) + r"[\u4e00-\u9fff]{0,5}")
    for chapter_number, text in chapter_texts:
        found = False
        for match in pattern.finditer(text or ""):
            window = match.group(0)
            for verb in _ACTION_VERBS:
                if verb in window:
                    counter[verb] += 1
                    found = True
        if found:
            chapters_seen.add(int(chapter_number))
    total = sum(counter.values())
    # Diversity = Shannon entropy normalized to [0, 1]. Sole-verb distributions
    # score 0; a perfectly even spread across the verb pool scores 1. The
    # earlier ``len(counter)/total`` formula was a bug — it punished common
    # characters because total grows linearly with usage while unique verbs
    # plateau (林渊 ended up at 0.026 with 24 unique verbs / 900 occurrences).
    diversity = _shannon_diversity(counter) if total > 0 else 1.0
    top = tuple(verb for verb, count in counter.most_common(5) if count >= 2)
    return CharacterActionProfile(
        character_name=name,
        action_verbs_used=dict(counter),
        top_overused_actions=top,
        diversity_score=diversity,
        chapters_seen=len(chapters_seen),
    )


def render_idiolect_avoidance_block(
    profiles: Sequence[CharacterActionProfile],
    *,
    language: str = "zh-CN",
) -> str:
    weak = [
        profile
        for profile in profiles
        if profile.top_overused_actions and profile.diversity_score < 0.4
    ]
    if not weak:
        return ""
    if str(language or "").lower().startswith("en"):
        lines = ["[Character action variety]"]
        for profile in weak:
            lines.append(
                f"- {profile.character_name}: avoid repeated actions "
                f"{', '.join(profile.top_overused_actions)}."
            )
        return "\n".join(lines)
    lines = ["【角色动作多样性约束】"]
    for profile in weak:
        common = "、".join(profile.top_overused_actions)
        lines.append(
            f"{profile.character_name}：动作多样性 {profile.diversity_score:.2f}（偏低）。"
            f"本章避免再使用：{common}；改用观察、判断、移动、取证、决断类动作。"
        )
    return "\n".join(lines)


def _shannon_diversity(counter: Counter[str]) -> float:
    """Shannon entropy of the verb-usage distribution, normalized to [0, 1].

    ``1.0`` = perfectly uniform spread across observed verbs (max variety).
    ``0.0`` = a single verb dominates everything.
    """
    total = sum(counter.values())
    if total <= 0:
        return 1.0
    observed = [count for count in counter.values() if count > 0]
    if len(observed) <= 1:
        return 0.0
    entropy = -sum((c / total) * math.log2(c / total) for c in observed)
    max_entropy = math.log2(len(observed))
    if max_entropy <= 0:
        return 0.0
    return max(0.0, min(1.0, entropy / max_entropy))


__all__ = [
    "CharacterActionProfile",
    "compute_character_idiolect",
    "compute_character_idiolect_from_texts",
    "render_idiolect_avoidance_block",
]
