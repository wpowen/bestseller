# ruff: noqa: RUF001
"""Public writing-contest capability pack.

The contest mode targets realistic first-person life writing inspired by public
writing campaigns: concrete lived experience, emotional restraint, and a
publication-grade revision bar. It is deterministic by design so prompts,
reviews, and CLI checks agree on the same acceptance contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from typing import Any, Literal

ContestTrack = Literal["original_graphic", "original_video", "ai_literary_video"]

PASS_SCORE = 86
EXCELLENT_SCORE = 93
MIN_GRAPHIC_CJK_CHARS = 800
MIN_VIDEO_CJK_CHARS = 450

_TRACK_LABELS: dict[ContestTrack, str] = {
    "original_graphic": "原创赛道-图文记录",
    "original_video": "原创赛道-视频呈现",
    "ai_literary_video": "AI文学视频赛道",
}

_THEMES: tuple[str, ...] = (
    "后知后觉的爱",
    "那个闪闪发光的人呀",
    "藏在____里的时光",
    "人生中最____的一天",
)

_FIRST_PERSON_TERMS = ("我", "我们", "我的", "俺", "咱", "自己")
_CONCRETE_OBJECT_TERMS = (
    "门",
    "窗",
    "碗",
    "饭",
    "鞋",
    "衣",
    "车",
    "票",
    "灯",
    "钥匙",
    "手机",
    "账本",
    "照片",
    "手",
    "烟",
    "水",
    "雨",
    "土",
    "路",
    "床",
    "桌",
)
_SENSORY_TERMS = (
    "看见",
    "听见",
    "闻到",
    "摸",
    "冷",
    "热",
    "疼",
    "亮",
    "暗",
    "响",
    "哑",
    "湿",
    "苦",
    "甜",
    "烫",
)
_TIME_PLACE_TERMS = (
    "那天",
    "那年",
    "早上",
    "晚上",
    "凌晨",
    "午后",
    "车站",
    "医院",
    "厨房",
    "学校",
    "工地",
    "村口",
    "路上",
    "门口",
    "屋里",
)
_RELATION_TERMS = (
    "母亲",
    "父亲",
    "妈妈",
    "爸爸",
    "奶奶",
    "爷爷",
    "外婆",
    "老师",
    "同学",
    "朋友",
    "师傅",
    "孩子",
    "妻子",
    "丈夫",
    "邻居",
)
_TURNING_TERMS = (
    "后来",
    "直到",
    "那一刻",
    "我才",
    "突然",
    "原来",
    "多年后",
    "回头",
    "明白",
    "才知道",
)
_CLOSURE_TERMS = (
    "如今",
    "现在",
    "后来",
    "再也",
    "我知道",
    "我明白",
    "留在",
    "记得",
    "想起",
)
_AI_DISCLOSURE_TERMS = ("AI", "人工智能", "生成", "辅助", "声明")
_SLOGAN_TERMS = (
    "每一种人生都值得被看见",
    "每个人都是自己故事的主角",
    "照亮人生",
    "时代洪流",
    "烟火人间",
    "温暖治愈",
    "破防了",
    "泪目",
    "狠狠共情",
)
_ABSTRACT_TERMS = (
    "成长",
    "命运",
    "人生",
    "时代",
    "梦想",
    "温暖",
    "治愈",
    "坚强",
    "善良",
    "意义",
)


@dataclass(frozen=True, slots=True)
class WritingContestTheme:
    key: str
    prompt: str
    craft_focus: tuple[str, ...]
    avoid: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "prompt": self.prompt,
            "craft_focus": list(self.craft_focus),
            "avoid": list(self.avoid),
        }


@dataclass(frozen=True, slots=True)
class WritingContestBrief:
    theme: WritingContestTheme
    track: ContestTrack
    title_strategy: tuple[str, ...]
    writing_contract: tuple[str, ...]
    revision_ladder: tuple[str, ...]
    scoring_rubric: dict[str, int]
    prompt: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme.to_dict(),
            "track": self.track,
            "track_label": _TRACK_LABELS[self.track],
            "title_strategy": list(self.title_strategy),
            "writing_contract": list(self.writing_contract),
            "revision_ladder": list(self.revision_ladder),
            "scoring_rubric": dict(self.scoring_rubric),
            "prompt": self.prompt,
        }


@dataclass(frozen=True, slots=True)
class WritingContestFinding:
    code: str
    severity: Literal["critical", "high", "medium", "low"]
    message: str
    repair_action: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "repair_action": self.repair_action,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class WritingContestReview:
    passed: bool
    tier: Literal["reject", "revise", "pass", "excellent"]
    score: int
    track: ContestTrack
    theme: str
    cjk_chars: int
    findings: tuple[WritingContestFinding, ...]
    strengths: tuple[str, ...]
    next_revision_actions: tuple[str, ...]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "tier": self.tier,
            "score": self.score,
            "track": self.track,
            "track_label": _TRACK_LABELS[self.track],
            "theme": self.theme,
            "cjk_chars": self.cjk_chars,
            "findings": [finding.to_dict() for finding in self.findings],
            "strengths": list(self.strengths),
            "next_revision_actions": list(self.next_revision_actions),
            "metrics": dict(self.metrics),
        }


_THEME_BANK: dict[str, WritingContestTheme] = {
    "belated-love": WritingContestTheme(
        key="belated-love",
        prompt="后知后觉的爱",
        craft_focus=(
            "用一个被忽略多年的具体动作证明爱，而不是解释爱。",
            "让叙述者在当下重新理解过去的误会、沉默或笨拙照顾。",
            "结尾必须让前文物件或动作回响一次。",
        ),
        avoid=("直接歌颂父母", "先讲大道理", "用流行热词替代现场"),
    ),
    "shining-person": WritingContestTheme(
        key="shining-person",
        prompt="那个闪闪发光的人呀",
        craft_focus=(
            "写出此人发光之前的平凡和困窘。",
            "用一次选择、一次让步或一次承担让人物亮起来。",
            "叙述者要被这个人改变一个具体判断。",
        ),
        avoid=("把人物写成完美好人", "只罗列品质", "没有代价的感动"),
    ),
    "time-hidden-in": WritingContestTheme(
        key="time-hidden-in",
        prompt="藏在____里的时光",
        craft_focus=(
            "把空格填成一个具体容器：饭盒、车票、旧衣、账本、钥匙等。",
            "至少让这个容器承载两段时间的变化。",
            "用磨损、气味、重量或位置变化写出时间。",
        ),
        avoid=("空泛怀旧", "只写回忆不写物", "用抒情替代细节"),
    ),
    "most-day": WritingContestTheme(
        key="most-day",
        prompt="人生中最____的一天",
        craft_focus=(
            "把空格填成情绪或判断：漫长、安静、丢脸、勇敢、舍不得等。",
            "一天内必须发生一个不可逆的小改变。",
            "用时钟、路程或等待压住叙事节奏。",
        ),
        avoid=("写成流水账", "只写大事件不写人的反应", "结尾强行升华"),
    ),
}

_RUBRIC: dict[str, int] = {
    "theme_grounding": 14,
    "first_person_truth": 12,
    "scene_specificity": 16,
    "human_relationship": 12,
    "emotional_restraint": 14,
    "narrative_turn": 12,
    "ending_echo": 10,
    "platform_compliance": 10,
}


def list_writing_contest_themes() -> tuple[WritingContestTheme, ...]:
    return tuple(_THEME_BANK.values())


def get_writing_contest_theme(theme: str | None = None) -> WritingContestTheme:
    normalized = (theme or "belated-love").strip()
    for item in _THEME_BANK.values():
        if normalized in {item.key, item.prompt}:
            return item
    available = ", ".join(f"{item.key}({item.prompt})" for item in _THEME_BANK.values())
    raise ValueError(f"Unknown writing contest theme '{theme}'. Available: {available}")


def build_writing_contest_brief(
    *,
    theme: str | None = None,
    track: ContestTrack = "original_graphic",
    protagonist: str | None = None,
    material_seed: str | None = None,
) -> WritingContestBrief:
    selected = get_writing_contest_theme(theme)
    _validate_track(track)
    protagonist_text = protagonist.strip() if protagonist else "第一人称叙述者"
    seed_text = material_seed.strip() if material_seed else "一个真实、可核验的生活片段"
    min_chars = _min_chars_for_track(track)
    title_strategy = (
        "标题用一个人、一件物或一天命名，避免口号。",
        "标题可以留下轻微缺口，但正文必须兑现。",
        "不要使用震惊体、榜单体或过度营销语。",
    )
    writing_contract = (
        f"以{protagonist_text}为叙述中心，围绕“{selected.prompt}”写作。",
        f"正文不少于{min_chars}个中文字符，开头80字内出现人、地点、动作或物件。",
        "必须有一个可触摸的核心物件、一个关系对象、一次误解或重新理解。",
        "情绪靠动作和细节传递，不直接喊感动、泪目、治愈。",
        "若使用AI文学视频赛道，必须保留原创故事来源和AI辅助声明。",
    )
    revision_ladder = (
        "第一稿只取真实材料，不追求漂亮句子。",
        "第二稿删除口号、热词和解释性抒情。",
        "第三稿补足时间、地点、物件、动作和人物口气。",
        "第四稿检查结尾是否回响开头，是否留下一个安静但准确的余味。",
        "第五稿按评分门禁修到86分以上，冲奖稿修到93分以上。",
    )
    prompt = _render_generation_prompt(
        theme=selected,
        track=track,
        protagonist=protagonist_text,
        material_seed=seed_text,
        min_chars=min_chars,
    )
    return WritingContestBrief(
        theme=selected,
        track=track,
        title_strategy=title_strategy,
        writing_contract=writing_contract,
        revision_ladder=revision_ladder,
        scoring_rubric=_RUBRIC,
        prompt=prompt,
    )


def review_writing_contest_entry(
    text: str,
    *,
    theme: str | None = None,
    track: ContestTrack = "original_graphic",
    ai_disclosed: bool = False,
) -> WritingContestReview:
    selected = get_writing_contest_theme(theme)
    _validate_track(track)
    clean_text = (text or "").strip()
    cjk_chars = _count_cjk(clean_text)
    min_chars = _min_chars_for_track(track)
    findings: list[WritingContestFinding] = []
    metrics = {
        "min_chars": min_chars,
        "first_person_hits": _count_hits(clean_text, _FIRST_PERSON_TERMS),
        "object_hits": _count_hits(clean_text, _CONCRETE_OBJECT_TERMS),
        "sensory_hits": _count_hits(clean_text, _SENSORY_TERMS),
        "time_place_hits": _count_hits(clean_text, _TIME_PLACE_TERMS),
        "relation_hits": _count_hits(clean_text, _RELATION_TERMS),
        "turning_hits": _count_hits(clean_text, _TURNING_TERMS),
        "closure_hits": _count_hits(_tail(clean_text, 180), _CLOSURE_TERMS),
        "slogan_hits": _count_hits(clean_text, _SLOGAN_TERMS),
        "abstract_density": round(
            _count_hits(clean_text, _ABSTRACT_TERMS) / max(cjk_chars / 100, 1),
            2,
        ),
    }

    score = 100
    score -= _length_penalty(cjk_chars, min_chars, findings)
    score -= _first_person_penalty(clean_text, metrics, findings)
    score -= _theme_penalty(selected, clean_text, findings)
    score -= _specificity_penalty(metrics, findings)
    score -= _relationship_penalty(metrics, findings)
    score -= _turning_penalty(metrics, findings)
    score -= _ending_penalty(metrics, clean_text, findings)
    score -= _slop_penalty(metrics, clean_text, findings)
    score -= _track_penalty(track, clean_text, ai_disclosed, findings)
    score = max(0, min(100, score))

    critical_or_high = any(finding.severity in {"critical", "high"} for finding in findings)
    passed = score >= PASS_SCORE and not critical_or_high
    if score >= EXCELLENT_SCORE and passed:
        tier: Literal["reject", "revise", "pass", "excellent"] = "excellent"
    elif passed:
        tier = "pass"
    elif score >= 72:
        tier = "revise"
    else:
        tier = "reject"

    strengths = _derive_strengths(metrics, selected, clean_text)
    next_actions = tuple(
        finding.repair_action
        for finding in findings
        if finding.severity in {"critical", "high", "medium"}
    )[:6]
    return WritingContestReview(
        passed=passed,
        tier=tier,
        score=score,
        track=track,
        theme=selected.prompt,
        cjk_chars=cjk_chars,
        findings=tuple(findings),
        strengths=strengths,
        next_revision_actions=next_actions,
        metrics=metrics,
    )


def _render_generation_prompt(
    *,
    theme: WritingContestTheme,
    track: ContestTrack,
    protagonist: str,
    material_seed: str,
    min_chars: int,
) -> str:
    focus = "\n".join(f"- {item}" for item in theme.craft_focus)
    avoid = "\n".join(f"- {item}" for item in theme.avoid)
    track_note = (
        "这是AI文学视频赛道：先写原创文字故事，再给出镜头化段落；必须声明AI只做影像辅助。"
        if track == "ai_literary_video"
        else "这是原创表达赛道：正文必须保持第一人称真实叙事，不写成宣传稿。"
    )
    return (
        "你是严苛的公共写作大赛主编。请生成一篇可投稿的中文真实生活叙事。\n\n"
        f"主题：{theme.prompt}\n"
        f"赛道：{_TRACK_LABELS[track]}\n"
        f"叙述中心：{protagonist}\n"
        f"材料种子：{material_seed}\n"
        f"最低长度：{min_chars}个中文字符\n"
        f"赛道说明：{track_note}\n\n"
        "硬性写作合同：\n"
        "- 开头80字内必须出现具体地点、人物动作和一个可触摸物件。\n"
        "- 全文以第一人称推进，不站出来总结时代、人生、文学或平台意义。\n"
        "- 至少包含两处时间/地点锚点、三处物件或身体动作、一处关系误解或迟到的理解。\n"
        "- 情绪必须克制，靠动作、停顿、物件变化和人物口气传递。\n"
        "- 结尾回响开头的物件或动作，不用口号收束。\n\n"
        "本主题重点：\n"
        f"{focus}\n\n"
        "必须避免：\n"
        f"{avoid}\n\n"
        "输出结构：\n"
        "1. 标题\n"
        "2. 正文\n"
        "3. 自检：列出核心物件、关系对象、转折点、结尾回响、需要删除的套话\n"
    )


def _validate_track(track: str) -> None:
    if track not in _TRACK_LABELS:
        raise ValueError(
            "Unknown writing contest track "
            f"'{track}'. Available: {', '.join(_TRACK_LABELS)}"
        )


def _min_chars_for_track(track: ContestTrack) -> int:
    if track == "original_video":
        return MIN_VIDEO_CJK_CHARS
    return MIN_GRAPHIC_CJK_CHARS


def _count_cjk(text: str) -> int:
    return sum(1 for char in text if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff")


def _count_hits(text: str, terms: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term.lower()) for term in terms)


def _head(text: str, cjk_limit: int) -> str:
    return _cjk_window(text, cjk_limit, reverse=False)


def _tail(text: str, cjk_limit: int) -> str:
    return _cjk_window(text, cjk_limit, reverse=True)


def _cjk_window(text: str, cjk_limit: int, *, reverse: bool) -> str:
    chars: list[str] = []
    count = 0
    iterable = reversed(text) if reverse else iter(text)
    for char in iterable:
        chars.append(char)
        if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff":
            count += 1
        if count >= cjk_limit:
            break
    if reverse:
        chars.reverse()
    return "".join(chars)


def _length_penalty(
    cjk_chars: int,
    min_chars: int,
    findings: list[WritingContestFinding],
) -> int:
    if cjk_chars >= min_chars:
        return 0
    severity: Literal["critical", "high"] = "critical" if cjk_chars < min_chars * 0.55 else "high"
    findings.append(
        WritingContestFinding(
            code="length_below_contest_floor",
            severity=severity,
            message=f"正文只有{cjk_chars}个中文字符，低于赛道最低{min_chars}字要求。",
            repair_action="补足完整生活现场：起因、动作、关系对象、转折、结尾回响。",
            evidence=str(cjk_chars),
        )
    )
    return 24 if severity == "critical" else 14


def _first_person_penalty(
    text: str,
    metrics: dict[str, Any],
    findings: list[WritingContestFinding],
) -> int:
    head = _head(text, 160)
    if metrics["first_person_hits"] <= 0 or not any(term in head for term in _FIRST_PERSON_TERMS):
        findings.append(
            WritingContestFinding(
                code="first_person_anchor_missing",
                severity="high",
                message="开头缺少稳定第一人称锚点，容易写成第三方宣传或概述。",
                repair_action="让“我”在开头进入一个具体动作，而不是先解释背景。",
                evidence=head[:80],
            )
        )
        return 12
    return 0


def _theme_penalty(
    theme: WritingContestTheme,
    text: str,
    findings: list[WritingContestFinding],
) -> int:
    expected_terms = tuple(part for part in re.split(r"[_，。 ]+", theme.prompt) if part)
    if expected_terms and not any(term in text for term in expected_terms):
        findings.append(
            WritingContestFinding(
                code="theme_not_visible",
                severity="medium",
                message="正文没有清晰兑现命题关键词，投稿主题感偏弱。",
                repair_action=f"至少让“{theme.prompt}”中的核心词进入叙事转折或结尾回响。",
                evidence=theme.prompt,
            )
        )
        return 7
    return 0


def _specificity_penalty(
    metrics: dict[str, Any],
    findings: list[WritingContestFinding],
) -> int:
    penalty = 0
    if metrics["object_hits"] < 3:
        findings.append(
            WritingContestFinding(
                code="concrete_object_thin",
                severity="medium",
                message="物件细节不足，文章难以形成真实生活质感。",
                repair_action="加入会反复出现的核心物件，并写出它的重量、磨损、位置或用途变化。",
                evidence=f"object_hits={metrics['object_hits']}",
            )
        )
        penalty += 8
    if metrics["sensory_hits"] < 2 or metrics["time_place_hits"] < 2:
        findings.append(
            WritingContestFinding(
                code="scene_anchor_thin",
                severity="medium",
                message="时间、地点或感官锚点不足，场景像概述而不是亲历。",
                repair_action="补两处明确时间/地点，再用声音、温度、疼痛、气味或光线压实现场。",
                evidence=(
                    f"sensory={metrics['sensory_hits']} "
                    f"time_place={metrics['time_place_hits']}"
                ),
            )
        )
        penalty += 8
    return penalty


def _relationship_penalty(
    metrics: dict[str, Any],
    findings: list[WritingContestFinding],
) -> int:
    if metrics["relation_hits"] >= 1:
        return 0
    findings.append(
        WritingContestFinding(
            code="relationship_subject_missing",
            severity="high",
            message="缺少具体关系对象，作品容易只剩自我抒情。",
            repair_action="加入一个可被看见的人：亲人、朋友、老师、同事或邻居，并写出一次互动。",
            evidence="relation_hits=0",
        )
    )
    return 12


def _turning_penalty(
    metrics: dict[str, Any],
    findings: list[WritingContestFinding],
) -> int:
    if metrics["turning_hits"] >= 1:
        return 0
    findings.append(
        WritingContestFinding(
            code="belated_understanding_missing",
            severity="medium",
            message="缺少重新理解或迟到的认知转折，情绪没有被故事推动。",
            repair_action="加入“当时我以为……后来我才知道……”式的事实反转，但用动作呈现。",
            evidence="turning_hits=0",
        )
    )
    return 9


def _ending_penalty(
    metrics: dict[str, Any],
    text: str,
    findings: list[WritingContestFinding],
) -> int:
    tail = _tail(text, 220)
    if metrics["closure_hits"] >= 1 and metrics["object_hits"] >= 3:
        return 0
    findings.append(
        WritingContestFinding(
            code="ending_echo_weak",
            severity="medium",
            message="结尾缺少对开头物件、动作或关系的回响。",
            repair_action="把开头的一个物件或动作带回结尾，让读者自己完成情绪判断。",
            evidence=tail[:120],
        )
    )
    return 8


def _slop_penalty(
    metrics: dict[str, Any],
    text: str,
    findings: list[WritingContestFinding],
) -> int:
    penalty = 0
    if metrics["slogan_hits"] > 0:
        findings.append(
            WritingContestFinding(
                code="slogan_language_detected",
                severity="high",
                message="出现平台口号、流行感动词或宣传式表达，削弱真实感。",
                repair_action="删除口号，把情绪换成一个动作、一个停顿或一句人物原话。",
                evidence=f"slogan_hits={metrics['slogan_hits']}",
            )
        )
        penalty += 12
    if metrics["abstract_density"] > 2.2:
        findings.append(
            WritingContestFinding(
                code="abstract_density_high",
                severity="medium",
                message="抽象词密度偏高，文章可能显得像总结而不是故事。",
                repair_action="每删除一个抽象词，就补一个人、物、动作、声音或具体后果。",
                evidence=f"abstract_density={metrics['abstract_density']}",
            )
        )
        penalty += 7
    if "作为一个" in text or "总而言之" in text or "综上" in text:
        findings.append(
            WritingContestFinding(
                code="essay_voice_detected",
                severity="medium",
                message="出现作文/总结腔，和生活叙事赛道不匹配。",
                repair_action="删除总结句，改成现场中的一句话或一个动作。",
            )
        )
        penalty += 5
    return penalty


def _track_penalty(
    track: ContestTrack,
    text: str,
    ai_disclosed: bool,
    findings: list[WritingContestFinding],
) -> int:
    if track != "ai_literary_video":
        return 0
    disclosed = ai_disclosed or _count_hits(text, _AI_DISCLOSURE_TERMS) >= 2
    if disclosed:
        return 0
    findings.append(
        WritingContestFinding(
            code="ai_track_disclosure_missing",
            severity="critical",
            message="AI文学视频赛道缺少AI辅助声明或原创来源说明。",
            repair_action="补充原创文字来源、AI用于影像化辅助的边界，以及人工复核声明。",
            evidence="ai_disclosed=false",
        )
    )
    return 18


def _derive_strengths(
    metrics: dict[str, Any],
    theme: WritingContestTheme,
    text: str,
) -> tuple[str, ...]:
    strengths: list[str] = []
    if metrics["object_hits"] >= 5:
        strengths.append("物件和动作密度足，具备生活现场感。")
    if metrics["relation_hits"] >= 2:
        strengths.append("关系对象清楚，情绪有承载者。")
    if metrics["turning_hits"] >= 2:
        strengths.append("迟到理解或认知转折明确，符合全民写作的情感结构。")
    if metrics["slogan_hits"] == 0 and metrics["abstract_density"] <= 1.4:
        strengths.append("表达克制，少用口号和抽象升华。")
    if any(term in text for term in re.split(r"[_，。 ]+", theme.prompt) if term):
        strengths.append("命题可见，投稿归属清楚。")
    return tuple(strengths[:5])
