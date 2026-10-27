"""Final packaging promotion gate for a ranking-candidate book.

This is deliberately separate from the appeal scorers.  Appeal scores are
useful calibration signals; this gate answers the narrower release question:
is there enough independently auditable evidence to call the package a
ranking candidate?  A missing external reader run therefore cannot silently
turn into a pass.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from typing import Any

# ruff: noqa: ANN401, RUF001 — persisted JSON and Chinese findings are intentional.


_DEFAULTS: dict[str, Any] = {
    "title_min": 80.0,
    "blurb_min": 72.0,
    "title_floors": {"hook_power": 3.5, "graspable_subject": 3.0},
    "blurb_floors": {
        "hook_strength": 3.5,
        "emotion_charge": 3.0,
        "concreteness": 3.0,
        "comprehensibility": 2.5,
        "open_loop_end": 3.5,
        "genre_signal": 3.0,
    },
    "persona_min_clicks": 2,
    "persona_min_samples": 3,
    "persona_min_avg_score": 6.0,
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = -1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _dimensions(verdict: Any) -> dict[str, float]:
    payload = verdict.to_dict() if hasattr(verdict, "to_dict") else verdict
    dimensions = _mapping(payload).get("dimensions", ())
    result: dict[str, float] = {}
    if isinstance(dimensions, Sequence) and not isinstance(dimensions, (str, bytes)):
        for item in dimensions:
            row = _mapping(item)
            key = str(row.get("key") or "").strip()
            if key:
                result[key] = _number(row.get("score"))
    return result


def _payload(value: Any) -> Mapping[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return _mapping(value)


def _selected_effect_names(selected_effects: Any) -> tuple[str, ...]:
    if isinstance(selected_effects, Mapping):
        values: Any = selected_effects.keys()
    elif isinstance(selected_effects, Sequence) and not isinstance(selected_effects, (str, bytes)):
        values = selected_effects
    elif selected_effects:
        values = (selected_effects,)
    else:
        values = ()
    names: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("key") or value.get("name") or value.get("effect")
        name = str(value or "").strip().lower().replace("-", "_")
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _compact_evidence_text(value: Any) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()【】\[\]]+", "", str(value or ""))


def _effect_evidence(
    effect_realization: Any,
    effect: str,
    *,
    title_text: str,
    blurb_text: str,
) -> tuple[bool, str, bool]:
    """Return pass, evidence summary and whether it is bound to the package."""

    payload = _payload(effect_realization)
    candidate: Any = payload.get(effect)
    if candidate is None:
        # Accept a list of {effect/key, passed, evidence} rows as well.
        rows = effect_realization if isinstance(effect_realization, Sequence) else ()
        for row in rows:
            item = _mapping(row)
            key = str(item.get("effect") or item.get("key") or "").lower().replace("-", "_")
            if key == effect:
                candidate = item
                break
    if isinstance(candidate, bool):
        return candidate, "" if not candidate else "boolean-only", False
    row = _payload(candidate)
    passed = row.get("passed") is True or row.get("realized") is True
    evidence = row.get("evidence") or row.get("quote") or row.get("evidence_quote")
    if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)):
        evidence = ";".join(str(v).strip() for v in evidence if str(v).strip())
    evidence_text = str(evidence or "").strip()
    package_text = _compact_evidence_text(f"{title_text}\n{blurb_text}")
    evidence_quote = _compact_evidence_text(row.get("evidence_quote") or row.get("quote"))
    quote_bound = len(evidence_quote) >= 4 and evidence_quote in package_text
    # Structural explanations are interpretation, not proof that the reader
    # can observe the effect in this exact package.
    return passed, evidence_text, quote_bound


def evaluate_ranking_candidate_packaging(
    *,
    title_verdict: Any,
    blurb_verdict: Any,
    persona_report: Any = None,
    selected_effects: Any = (),
    effect_realization: Any = None,
    title_text: str = "",
    blurb_text: str = "",
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate final public packaging with independent hard floors.

    ``title_verdict`` and ``blurb_verdict`` may be domain objects or their
    persisted ``to_dict`` forms.  ``INCONCLUSIVE`` is intentionally never a
    pass: the package must be re-run with real persona evidence before it can
    be promoted.
    """

    section = _mapping(config)
    if "ranking_candidate_packaging" in section:
        section = _mapping(section.get("ranking_candidate_packaging"))
    cfg = dict(_DEFAULTS)
    cfg.update({k: v for k, v in section.items() if k not in {"title_floors", "blurb_floors"}})
    title_floors = dict(_DEFAULTS["title_floors"])
    title_floors.update(_mapping(section.get("title_floors")))
    blurb_floors = dict(_DEFAULTS["blurb_floors"])
    blurb_floors.update(_mapping(section.get("blurb_floors")))

    title = _payload(title_verdict)
    blurb = _payload(blurb_verdict)
    title_dims = _dimensions(title_verdict)
    blurb_dims = _dimensions(blurb_verdict)
    findings: list[dict[str, Any]] = []
    blocking: list[str] = []

    def block(
        code: str,
        message: str,
        metric: str,
        actual: Any = None,
        required: Any = None,
    ) -> None:
        blocking.append(code)
        row: dict[str, Any] = {"code": code, "message": message, "metric": metric}
        if actual is not None:
            row["actual"] = actual
        if required is not None:
            row["required"] = required
        findings.append(row)

    title_total = _number(title.get("total"))
    if title_total < float(cfg["title_min"]):
        block(
            "TITLE_TOTAL_BELOW_RANKING_FLOOR",
            "标题总分未达到榜单候选门槛",
            "title.total",
            title_total,
            cfg["title_min"],
        )
    for key, floor in title_floors.items():
        actual = title_dims.get(key, -1.0)
        if actual < float(floor):
            block(
                f"TITLE_{key.upper()}_BELOW_FLOOR",
                f"标题关键维度 {key} 未过硬门",
                f"title.{key}",
                actual,
                floor,
            )

    blurb_total = _number(blurb.get("total"))
    if blurb_total < float(cfg["blurb_min"]):
        block(
            "BLURB_TOTAL_BELOW_RANKING_FLOOR",
            "简介总分未达到榜单候选门槛",
            "blurb.total",
            blurb_total,
            cfg["blurb_min"],
        )
    for key, floor in blurb_floors.items():
        actual = blurb_dims.get(key, -1.0)
        if actual < float(floor):
            block(
                f"BLURB_{key.upper()}_BELOW_FLOOR",
                f"简介关键维度 {key} 未过硬门",
                f"blurb.{key}",
                actual,
                floor,
            )

    persona = _payload(persona_report)
    persona_available = persona.get("llm_used") is True and _number(persona.get("samples"), 0) > 0
    if not persona_available:
        block(
            "PERSONA_EVIDENCE_UNAVAILABLE",
            "真实读者画像判定不可用，不能宣称榜单候选",
            "persona.llm_used",
            False,
            True,
        )
    else:
        samples = _number(persona.get("samples"), 0)
        clicks = _number(persona.get("clicks"), 0)
        avg_score = _number(persona.get("avg_score"))
        min_samples = int(cfg["persona_min_samples"])
        min_clicks = int(cfg["persona_min_clicks"])
        if samples < min_samples or clicks < min_clicks:
            block(
                "PERSONA_CLICK_RATE_BELOW_RANKING_FLOOR",
                "画像读者点击未达到至少 2/3",
                "persona.clicks",
                clicks,
                min_clicks,
            )
        if avg_score < float(cfg["persona_min_avg_score"]):
            block(
                "PERSONA_AVG_SCORE_BELOW_RANKING_FLOOR",
                "画像读者平均吸引力低于硬门",
                "persona.avg_score",
                avg_score,
                cfg["persona_min_avg_score"],
            )

    selected = _selected_effect_names(selected_effects)
    if "comedy_engine" in selected:
        passed, evidence, content_bound = _effect_evidence(
            effect_realization,
            "comedy_engine",
            title_text=title_text,
            blurb_text=blurb_text,
        )
        if not passed or not evidence or evidence == "boolean-only" or not content_bound:
            block(
                "COMEDY_PACKAGING_REALIZATION_MISSING",
                "显式选择 comedy_engine，但公开包装没有与实际文案绑定的兑现证据",
                "effect_realization.comedy_engine",
                evidence or False,
                "passed + evidence_quote grounded in the current title or blurb",
            )

    status = (
        "PASS"
        if not blocking
        else ("INCONCLUSIVE" if "PERSONA_EVIDENCE_UNAVAILABLE" in blocking else "BLOCKED")
    )
    return {
        "passed": not blocking,
        "status": status,
        "blocking_codes": blocking,
        "findings": findings,
        "metrics": {
            "title_total": title_total,
            "title_dimensions": title_dims,
            "blurb_total": blurb_total,
            "blurb_dimensions": blurb_dims,
            "persona_available": persona_available,
            "persona_samples": _number(persona.get("samples"), 0),
            "persona_clicks": _number(persona.get("clicks"), 0),
            "persona_avg_score": _number(persona.get("avg_score")),
            "selected_effects": list(selected),
        },
        "schema_version": "ranking-candidate-packaging.v1",
    }


__all__ = ["evaluate_ranking_candidate_packaging"]
