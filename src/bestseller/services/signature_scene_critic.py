"""Semantic fallback critic for signature-scene compliance.

The planner provides literal ``must_include_line`` / ``must_include_image``
hints. Those exact strings are cheap and precise to check, but a good draft
can satisfy the mandate through a semantically equivalent image or beat. This
module keeps the fallback deterministic for tests and offline worker runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from bestseller.domain.signature_scene import (
    SignatureSceneArchetype,
    SignatureSceneMandate,
    SignatureSceneStake,
)


@dataclass(frozen=True)
class SignatureSceneCriticReport:
    passed: bool
    confidence: float
    matched_markers: tuple[str, ...]
    detail: str


_ARCHETYPE_MARKERS: dict[SignatureSceneArchetype, tuple[str, ...]] = {
    SignatureSceneArchetype.REVELATION: (
        "真相", "原来", "揭开", "谜底", "答案", "身份", "旧账", "看清",
    ),
    SignatureSceneArchetype.SACRIFICE: (
        "代价", "牺牲", "替你", "我来", "换你", "赴死", "挡下",
    ),
    SignatureSceneArchetype.CONFRONTATION: (
        "对峙", "决战", "正面", "交锋", "压上", "不退", "开战",
    ),
    SignatureSceneArchetype.OATH_BOUND: (
        "立誓", "血誓", "誓约", "以血", "血印", "契约", "我来还",
    ),
    SignatureSceneArchetype.DEFIANCE: (
        "抗命", "不跪", "不退", "反抗", "违令", "偏要", "谁也别想",
    ),
    SignatureSceneArchetype.REUNION: (
        "重逢", "终于见到", "回来", "别来无恙", "久别",
    ),
    SignatureSceneArchetype.BETRAYAL: (
        "背叛", "出卖", "原来是你", "刀从身后", "内鬼",
    ),
    SignatureSceneArchetype.APOTHEOSIS: (
        "觉醒", "破界", "登临", "掌控", "反制", "底牌", "规则",
    ),
    SignatureSceneArchetype.FAREWELL: (
        "永别", "别回头", "送别", "最后一眼", "再也不见",
    ),
    SignatureSceneArchetype.UNVEILING_NAME: (
        "真名", "真身", "名号", "身份", "揭名", "报出名字",
    ),
}

_STAKE_MARKERS: dict[SignatureSceneStake, tuple[str, ...]] = {
    SignatureSceneStake.LIFE_DEATH: ("生死", "活下去", "死", "命", "救"),
    SignatureSceneStake.LOVE_LOSS: ("失去", "放手", "爱", "再见", "亏欠"),
    SignatureSceneStake.LOYALTY_HONOR: ("背叛", "守住", "名声", "荣辱", "忠"),
    SignatureSceneStake.IDENTITY_TRUTH: ("身份", "真相", "名字", "来历", "旧账"),
    SignatureSceneStake.POWER_AUTHORITY: ("权柄", "命令", "规则", "压制", "掌控"),
    SignatureSceneStake.FREEDOM_BONDAGE: ("自由", "束缚", "锁", "困", "挣脱"),
}


def judge_signature_scene_semantics(
    chapter_text: str,
    mandate: SignatureSceneMandate,
) -> SignatureSceneCriticReport:
    """Judge whether a draft semantically pays off a signature-scene mandate."""

    text = chapter_text or ""
    if not text.strip():
        return SignatureSceneCriticReport(
            passed=False,
            confidence=0.0,
            matched_markers=(),
            detail="empty chapter text",
        )

    literal_hits = _literal_hint_hits(text, mandate)
    if literal_hits:
        return SignatureSceneCriticReport(
            passed=True,
            confidence=0.95,
            matched_markers=literal_hits,
            detail="literal signature-scene hint present",
        )

    archetype_hits = _marker_hits(text, _ARCHETYPE_MARKERS.get(mandate.archetype, ()))
    stake_hits = _marker_hits(text, _STAKE_MARKERS.get(mandate.stake, ()))
    summary_hits = _summary_keyword_hits(text, mandate.summary)
    matched = tuple(dict.fromkeys((*archetype_hits, *stake_hits, *summary_hits)))

    passed = bool(archetype_hits and (stake_hits or summary_hits or len(archetype_hits) >= 2))
    confidence = min(
        0.88,
        0.35
        + 0.16 * len(archetype_hits)
        + 0.12 * len(stake_hits)
        + 0.08 * len(summary_hits),
    )
    if not passed:
        confidence = min(confidence, 0.49)
    return SignatureSceneCriticReport(
        passed=passed,
        confidence=confidence,
        matched_markers=matched,
        detail=(
            "semantic signature-scene markers present"
            if passed
            else "no literal hint and insufficient semantic signature-scene markers"
        ),
    )


def _literal_hint_hits(
    text: str,
    mandate: SignatureSceneMandate,
) -> tuple[str, ...]:
    return tuple(
        hint
        for hint in (*mandate.must_include_line, *mandate.must_include_image)
        if hint and hint in text
    )


def _marker_hits(text: str, markers: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(marker for marker in markers if marker in text)


def _summary_keyword_hits(text: str, summary: str) -> tuple[str, ...]:
    keywords = tuple(
        token
        for token in _split_summary_keywords(summary)
        if len(token) >= 2 and token in text
    )
    return keywords[:3]


def _split_summary_keywords(summary: str) -> tuple[str, ...]:
    if not summary:
        return ()
    raw = summary
    for sep in "，,。；;：:、（）()[]【】":
        raw = raw.replace(sep, " ")
    return tuple(dict.fromkeys(part.strip() for part in raw.split() if part.strip()))


__all__ = ["SignatureSceneCriticReport", "judge_signature_scene_semantics"]
