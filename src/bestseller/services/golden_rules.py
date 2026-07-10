"""Unified golden-three-chapters rules.

Previously the opening rules for chapters 1-3 were maintained independently
in four places with inconsistent thresholds ("前 100 字" vs "前 200 字" vs
"前 800 字"). This module provides a single renderer so that generation
prompts and the LLM judge share the same standard.
"""

from __future__ import annotations


def render_golden_three_rules(
    chapter_number: int,
    language: str,
    *,
    path_mode: str = "chapter_first",
) -> str:
    """Render unified golden-three-chapters opening rules.

    Parameters
    ----------
    chapter_number
        The chapter being generated/judged.
    language
        ``"zh-CN"``, ``"zh"``, ``"en"``, etc.
    path_mode
        ``"chapter_first"`` (full, for chapter-first draft prompts),
        ``"scene"`` (compact, for scene-level prompts),
        ``"judge"`` (for the LLM quality judge).
    """
    is_en = language.lower().startswith("en")

    if 4 <= chapter_number <= 10:
        return _render_front_ten_rules(chapter_number, is_en)

    if not 1 <= chapter_number <= 3:
        return ""

    if is_en:
        return _render_golden_three_en(path_mode)
    return _render_golden_three_zh(path_mode)


def _render_golden_three_zh(path_mode: str) -> str:
    """Chinese golden-three-chapters rules (chapters 1-3)."""
    if path_mode == "scene":
        # Compact version for scene-level prompts
        return (
            "# OUTPUT FORMAT · 开篇硬指标\n"
            "- 第一句 ≤ 25 个汉字（要狠）。\n"
            "- 第一段 ≤ 50 个汉字（要快）。\n"
            "- 前 200 字必须出现至少 1 个可视化异常物 / 异常动作。\n"
            "- 前 500 字内主角必须因这个异常被迫做出决定。\n"
            "- 必须让读者看见主角想要什么、怕失去什么、为何不能离开。\n"
            "- 为本章爽点铺设可见条件，并让第一步结果获得旁人 / 后果确认。\n"
        )
    # Full version for chapter-first and judge
    return (
        "【黄金三章·开篇硬契约】\n"
        "1. 前 100 字：必须给读者可感知的压力或异常（视觉 / 听觉 / 物件异常）。\n"
        "2. 前 300 字：主角必须表现出一个可代入的人性破绽——不能只是冷静执行规则。\n"
        "3. 前 500 字：主角必须因异常被迫做出决定（不能只是观察 / 对话 / 回忆）。\n"
        "4. 前 800 字：读者必须从动作 / 对白中自然得知——主角身份处境、"
        "地方 / 局势、主宰本章生死的核心规则（规则第一次生效前必须对读者完整可见）、"
        "金手指首次生效时读者能一句话说出它是什么。\n"
        "5. 主角目标与利害必须可见：想要什么、怕失去什么、为何不能离开；"
        "本章至少交付一个具体爽点，并用旁人反应或结果变化确认。\n"
        "6. 章末必须让读者能盘点主角赢了什么、付出了什么，并留下一个能立刻点开下一章的具体悬念，"
        "最后一句必须落在完成画面帧、人物动作、物件变化或选择点。\n"
        "7. 句法节奏：禁止「一拍一段」分镜腔——单句独段连续 ≤ 2 段、"
        "全章占叙述段 < 1/4。\n"
    )


def _render_golden_three_en(path_mode: str) -> str:
    """English golden-three-chapters rules (chapters 1-3)."""
    if path_mode == "scene":
        return (
            "# OPENING METRICS\n"
            "- First sentence: ≤ 15 words (hit hard).\n"
            "- First paragraph: ≤ 30 words (move fast).\n"
            "- First 150 words: at least 1 visible anomaly / abnormal action.\n"
            "- First 350 words: protagonist must be forced to decide by the anomaly.\n"
            "- Make visible what the protagonist wants, fears losing, and why they cannot leave.\n"
            "- Set up visible payoff conditions and confirm the first result by reaction or consequence.\n"
        )
    return (
        "[GOLDEN THREE CHAPTERS — OPENING HARD CONTRACT]\n"
        "1. First 80 words: reader must feel pressure or anomaly (visual / auditory / object).\n"
        "2. First 200 words: protagonist must show a relatable human flaw.\n"
        "3. First 350 words: protagonist must be forced to act by the anomaly.\n"
        "4. First 550 words: reader must learn from action / dialogue — who the protagonist is, "
        "the setting, the core rule governing this chapter's stakes (fully visible before first use), "
        "and the golden finger (describable in one sentence when it first activates).\n"
        "5. Make the protagonist's goal, feared loss, and reason they cannot leave visible; "
        "deliver one concrete payoff confirmed by reaction or changed outcome.\n"
        "6. Chapter end: make the gain and cost countable, then leave one concrete cliffhanger.\n"
        "7. Rhythm: no staccato single-line paragraphs — max 2 consecutive, < 1/4 of total.\n"
    )


def _render_front_ten_rules(chapter_number: int, is_en: bool) -> str:
    """Rules for chapters 4-10 (front-ten retention)."""
    if is_en:
        return (
            "[FRONT-TEN RETENTION RULES]\n"
            "1. First 150 words must pick up the previous chapter's hook and escalate immediately.\n"
            "2. This chapter must deliver: one new evidence, one active choice, "
            "one concrete cost, or one rule subversion.\n"
            "3. Dialogue must distinguish character voices — no everyone-speaks-in-short-cold-lines.\n"
            "4. Chapter end must leave a concrete object, action, sound, image, or choice pressure. "
            "Last sentence must stay inside the current scene.\n"
        )
    return (
        "【前十章留存硬规则】\n"
        "1. 开头 200 字必须承接上一章钩子并立刻升级，不得重新铺垫。\n"
        "2. 本章必须有一个新证据、一次主动选择、一个具体代价或一次规则反用。\n"
        "3. 对话必须区分人物腔调，禁止所有人都用冷短句。\n"
        "4. 章末必须留下具体物件、动作、声音、画面或选择压力，"
        "且最后一句必须仍在现场内。\n"
    )
