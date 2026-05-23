"""Anti-meta gate for chapter-boundary and design-language leaks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bestseller.services.checker_schema import CheckerIssue, CheckerReport


HARD_META_TERMS = (
    "这一章",
    "这一卷",
    "本章",
    "本卷",
    "章末",
    "卷末",
    "故事到此",
    "至此为止",
    "接下来",
    "下一章",
    "钩子",
    "长线",
    "主线",
    "副线",
    "卖点",
    "承诺",
    "读者期待",
    "读者会",
    "我们的主角",
    "余波",
    "涟漪暂歇",
    "暂告一段落",
)

SOFT_META_TERMS = (
    "所有人都在",
    "各方势力",
    "多方势力",
    "江湖众人",
    "原本的",
    "原本隐蔽的",
    "至此已经",
    "重新估算",
    "重新评估",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")
_ACTION_ENDING_RE = re.compile(
    r"(抬|低|回|转|伸|收|攥|握|松|推|拉|扣|按|踩|退|停|看|望|笑|跪|坐|站|走|落|响|亮|灭|开|合|碎|裂|渗|照|贴|碰|撞)"
)
_REVEAL_ENDING_RE = re.compile(r"(竟是|竟然是|原来是|不是.+而是|正是|就是|只有|那是)")
_VISIBLE_REVEAL_RE = re.compile(
    r"(出现|多了|写着|显示|映出|变成|裂开|亮了|发光|发烫|伸出|传来|响起|"
    r"名单|名字|落款|日期|纸片|纸条|照片|印记|裂缝|红绳|铜钱|戒指|镜|门|井|账|字|血|手|脸|声音|脚步|疤)"
)
_SUMMARY_ENDING_RE = re.compile(
    r"(?:知道|明白|意识到|觉得|感觉|想起|想到).{0,24}(?:答案|意思|意味|真相|不对)"
)
_DIRECT_SPEECH_RE = re.compile(r"[“\"].{1,90}[”\"]?$")


@dataclass(frozen=True)
class AntiMetaFinding:
    code: str
    severity: str
    term: str
    excerpt: str
    location: str


@dataclass(frozen=True)
class AntiMetaReport:
    chapter_position: int
    findings: tuple[AntiMetaFinding, ...] = ()
    ending_passed: bool = True
    ending_excerpt: str = ""
    metrics: dict[str, int | bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.ending_passed and not any(f.severity == "block" for f in self.findings)

    def to_checker_report(self) -> CheckerReport:
        issues = [
            CheckerIssue(
                id=f.code,
                type="anti_meta",
                severity="high" if f.severity == "block" else "medium",
                location=f.location,
                description=f"正文泄露章节边界或设计语言：{f.term}",
                suggestion="删掉该句的企划/总结视角，改成当前场景里可见的动作、画面或一句短对白。",
                can_override=f.severity != "block",
            )
            for f in self.findings
        ]
        if not self.ending_passed:
            issues.append(
                CheckerIssue(
                    id="ANTI_META_ENDING_OUT_OF_SCENE",
                    type="anti_meta",
                    severity="high",
                    location="last 3 sentences",
                    description="章场收尾没有落在动作、画面或揭示的一帧。",
                    suggestion="只重写最后 3-5 句，收束为具体动作、物理画面或新事实揭示。",
                    can_override=False,
                )
            )
        score = 100 if not issues else max(0, 100 - len(issues) * 20)
        return CheckerReport(
            agent="anti-meta-gate",
            chapter=self.chapter_position,
            overall_score=score,
            passed=self.passed,
            issues=tuple(issues),
            metrics={
                **self.metrics,
                "ending_passed": self.ending_passed,
                "finding_count": len(self.findings),
            },
            summary=(
                "anti-meta passed"
                if self.passed
                else f"anti-meta found {len(issues)} issue(s)"
            ),
        )


def check_anti_meta_gate(
    text: str,
    *,
    chapter_position: int,
) -> AntiMetaReport:
    if not text:
        return AntiMetaReport(
            chapter_position=chapter_position,
            ending_passed=True,
            metrics={"hard_count": 0, "soft_count": 0},
        )
    findings: list[AntiMetaFinding] = []
    for term in HARD_META_TERMS:
        for match in re.finditer(re.escape(term), text):
            findings.append(
                AntiMetaFinding(
                    code="ANTI_META_HARD_TERM",
                    severity="block",
                    term=term,
                    excerpt=_excerpt(text, match.start(), match.end()),
                    location=f"chars {match.start()}-{match.end()}",
                )
            )
    for term in SOFT_META_TERMS:
        for match in re.finditer(re.escape(term), text):
            findings.append(
                AntiMetaFinding(
                    code="ANTI_META_SOFT_TERM",
                    severity="warn",
                    term=term,
                    excerpt=_excerpt(text, match.start(), match.end()),
                    location=f"chars {match.start()}-{match.end()}",
                )
            )
    ending_sentences = _last_sentences(text, count=3)
    ending_excerpt = "".join(ending_sentences)
    ending_passed = _ending_is_in_scene(ending_sentences)
    return AntiMetaReport(
        chapter_position=chapter_position,
        findings=tuple(findings),
        ending_passed=ending_passed,
        ending_excerpt=ending_excerpt,
        metrics={
            "hard_count": sum(1 for f in findings if f.severity == "block"),
            "soft_count": sum(1 for f in findings if f.severity == "warn"),
        },
    )


def _excerpt(text: str, start: int, end: int, radius: int = 36) -> str:
    return text[max(0, start - radius): min(len(text), end + radius)].replace("\n", " ")


def _last_sentences(text: str, *, count: int) -> list[str]:
    body = re.sub(r"^#+\s*.*$", "", text.strip(), flags=re.MULTILINE).strip()
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
    return sentences[-count:] if sentences else []


def _ending_is_in_scene(sentences: list[str]) -> bool:
    if not sentences:
        return True
    ending = "".join(sentences)
    tail = sentences[-1]
    if any(term in ending for term in HARD_META_TERMS + SOFT_META_TERMS):
        return False
    if _SUMMARY_ENDING_RE.search(tail) and not _VISIBLE_REVEAL_RE.search(tail):
        return False
    if _REVEAL_ENDING_RE.search(tail):
        return True
    if _ACTION_ENDING_RE.search(tail):
        return True
    if _VISIBLE_REVEAL_RE.search(tail):
        return True
    # A live line of dialogue can be an in-scene ending even when the last
    # sentence is grammatically a question or warning rather than an action.
    if _DIRECT_SPEECH_RE.search(tail):
        return True
    # Some valid reveals are two-sentence constructions:
    # "不是小雨的名字。是陈默。"
    if len(sentences) >= 2:
        last_two = "".join(sentences[-2:])
        if _VISIBLE_REVEAL_RE.search(last_two) or _DIRECT_SPEECH_RE.search(last_two):
            return True
    # Image endings often end on a concrete noun rather than a verb.
    return bool(re.search(r"[一-鿿]{1,8}(?:光|影|门|井|水|火|血|字|脸|眼|手|刀|剑|石|雨|雪|风|灯|墙|纸|镜|戒)[。！？!?]?$", tail))


__all__ = [
    "AntiMetaFinding",
    "AntiMetaReport",
    "HARD_META_TERMS",
    "SOFT_META_TERMS",
    "check_anti_meta_gate",
]
