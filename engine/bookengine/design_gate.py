"""Completeness check for a new book's design before the first chapter is planned.

The check only looks at whether the designed story layers exist and differ from one
another. It says nothing about whether the story is good.
"""

from .q1_schema import DATA, rows

# Written into a newly prepared bible; only such books get the scheduled story design layer.
STORY_LAYER_VERSION = 1
MIN_TRUTH_LAYERS = 3
MIN_FACTIONS = 3
MIN_FORESHADOWS = 3
MIN_DISTINCT_BEAT_SHARE = 0.8


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def thread_issues(threads, optional=()):
    """Every thread kind the story uses needs real, mutually different beats."""
    lines = rows(threads.get("threads")) if isinstance(threads, dict) else rows(threads)
    issues = []
    described = []
    for kind in DATA["thread_kinds"]:
        beats = [
            _text(beat.get("description"))
            for line in lines
            if line.get("kind") == kind
            for beat in rows(line.get("beats"))
        ]
        filled = [text for text in beats if text]
        described.extend(filled)
        if not filled and kind not in optional:
            issues.append(f"threads.{kind}：没有任何带具体事件的节拍")
    if described and len(set(described)) < len(described) * MIN_DISTINCT_BEAT_SHARE:
        issues.append("threads：多个节拍的description相同，每个节拍须是不同的具体事件")
    return issues


def bible_issues(bible):
    world = bible.get("world") if isinstance(bible.get("world"), dict) else {}
    power = bible.get("power") if isinstance(bible.get("power"), dict) else {}
    issues = []
    truths = [row for row in rows(world.get("truth_layers")) if _text(row.get("truth"))]
    if len(truths) < MIN_TRUTH_LAYERS:
        issues.append(f"world.truth_layers：须有{MIN_TRUTH_LAYERS}层各不相同的真相")
    factions = [row for row in rows(world.get("factions")) if _text(row.get("name")) and _text(row.get("interest"))]
    if len(factions) < MIN_FACTIONS:
        issues.append(f"world.factions：至少{MIN_FACTIONS}个势力，各写name与interest")
    foreshadows = [row for row in rows(bible.get("main_foreshadows")) if _text(row.get("description"))]
    if len(foreshadows) < MIN_FORESHADOWS:
        issues.append(f"main_foreshadows：至少{MIN_FORESHADOWS}条带具体物件或事件的主伏笔")
    hero = next((card for card in rows(bible.get("characters")) if card.get("role") == "protagonist"), {})
    premise = _text(bible.get("premise"))
    if premise and _text(hero.get("desire")) == premise:
        issues.append("主角desire：不能照抄简介，写他本人此刻最想要的一件事")
    return issues


def design_issues(bible, threads):
    bible = bible if isinstance(bible, dict) else {}
    # A love interest is the story's choice; without one the romance line may stay empty.
    has_romance = any(card.get("role") == "romance" for card in rows(bible.get("characters")))
    return bible_issues(bible) + thread_issues(threads, optional=() if has_romance else ("romance",))


def gate_record(attempts, final_issues):
    return dict(
        status="passed" if not final_issues else "incomplete_continued",
        attempts=attempts,
        final_issues=final_issues,
        policy="只检查设计层是否齐全且互不相同，不评价故事好坏；不合格先重生成，重试用尽后留痕继续",
    )
