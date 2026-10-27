"""Emotion Choreography loader + detector (``config/emotion_choreography.yaml``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_str,
    as_str_tuple,
    load_yaml,
)


_CONFIG_FILENAME = "emotion_choreography.yaml"


@dataclass(frozen=True)
class ExpressionLayer:
    """One of the 5 expression layers (physiological / behavioral / object / silence / dialogue)."""

    layer_id: str
    description: str


@dataclass(frozen=True)
class EmotionChoreographyConfig:
    version: str
    basic_emotions: tuple[str, ...]
    expression_layers: dict[str, ExpressionLayer]
    banned_emotion_labels: tuple[str, ...]


def _parse_expression_layers(raw: object) -> dict[str, ExpressionLayer]:
    data = as_dict(raw)
    layers: dict[str, ExpressionLayer] = {}
    for layer_id, body in data.items():
        body_dict = as_dict(body)
        canonical = as_str(layer_id)
        if not canonical:
            continue
        layers[canonical] = ExpressionLayer(
            layer_id=canonical,
            description=as_str(body_dict.get("description")),
        )
    return layers


def _parse_banned(raw: object) -> tuple[str, ...]:
    data = as_dict(raw)
    items = data.get("banned")
    if not isinstance(items, list):
        return ()
    out: list[str] = []
    for entry in items:
        text = as_str(entry)
        if not text:
            continue
        # YAML uses "愤怒 / 怒火中烧 / 怒不可遏" — split on " / "
        for word in text.split("/"):
            cleaned = word.strip()
            if cleaned and cleaned not in out:
                out.append(cleaned)
    return tuple(out)


@lru_cache(maxsize=1)
def load_emotion_choreography() -> EmotionChoreographyConfig:
    """Return the typed view."""

    raw = load_yaml(_CONFIG_FILENAME)
    taxonomy = as_dict(raw.get("emotion_taxonomy"))
    return EmotionChoreographyConfig(
        version=as_str(raw.get("version")),
        basic_emotions=as_str_tuple(taxonomy.get("basic_emotions")),
        expression_layers=_parse_expression_layers(taxonomy.get("expression_layers")),
        banned_emotion_labels=_parse_banned(raw.get("banned_emotion_labels")),
    )


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmotionLabelHit:
    word: str
    count: int


@dataclass(frozen=True)
class EmotionLabelAuditResult:
    total_hits: int
    threshold: int
    passed: bool
    hits: tuple[EmotionLabelHit, ...]


def audit_emotion_labels(
    text: str,
    *,
    threshold: int = 0,
) -> EmotionLabelAuditResult:
    """Scan ``text`` (narration only) for banned emotion-label adjectives."""

    if not text:
        return EmotionLabelAuditResult(0, threshold, True, ())
    config = load_emotion_choreography()
    # Strip dialogue (between full-width or ASCII double quotes).
    narration = re.sub(
        r'[“"][^“”"]*[”"]',
        "",
        text,
    )
    hits: list[EmotionLabelHit] = []
    total = 0
    for word in config.banned_emotion_labels:
        if not word:
            continue
        count = narration.count(word)
        if count > 0:
            hits.append(EmotionLabelHit(word=word, count=count))
            total += count
    return EmotionLabelAuditResult(
        total_hits=total,
        threshold=threshold,
        passed=total <= threshold,
        hits=tuple(hits),
    )


def render_emotion_choreography_block() -> str:
    config = load_emotion_choreography()
    if not config.banned_emotion_labels:
        return ""
    lines: list[str] = ["【emotion_choreography · 情绪契约】"]
    lines.append(
        "- 关键情绪节点从这些表达层选择 ≥ 2 种组成动作—后果链，不为凑数并列堆叠: "
        + ", ".join(config.expression_layers.keys())
    )
    lines.append(
        "- 情绪标签不按词面一律禁用：只改写替读者下结论且不增加信息的用法；"
        "对白、人物自我命名可以保留，过场和必要内心导航允许简洁概括。"
    )
    lines.append("- 不得用通用身体反应或感官清单补位；细节必须改变信息、因果、人物或节奏。")
    lines.append("- 每章必有 compress → release → aftermath 完整弧")
    return "\n".join(lines)
