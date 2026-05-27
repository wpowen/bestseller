from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.outline_specificity_gate import (
    PLACEHOLDER_BLACKLIST,
    _has_named_entity,
)

_REQUIRED_READINESS_FIELDS: tuple[str, ...] = (
    "prewrite_anchor",
    "chapter_objective",
    "scene_beats",
    "required_evidence",
    "required_payoff",
    "pressure_handoff",
    "forbidden_moves",
    "scene_drive",
    "hook_contract",
)

_CHANGE_AXIS_RE = re.compile(
    r"\b(plot|clue|relationship|status|resource|exposure)\b|"
    r"(剧情|线索|证据|关系|状态|资源|身份|暴露|风险|代价|压力)"
)


def load_prewrite_contract(story_bible_dir: str | Path) -> dict[str, object]:
    path = Path(story_bible_dir) / "prewrite-contract.json"
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def evaluate_prewrite_contract_coverage(
    *,
    chapter_no: int,
    contract: Mapping[str, Any] | None = None,
    story_bible_dir: str | Path | None = None,
) -> GateVerdict:
    payload = dict(contract or {})
    if story_bible_dir is not None and not payload:
        payload = load_prewrite_contract(story_bible_dir)
    chapter_payload = _chapter_payload(payload, chapter_no)
    findings: list[GateFinding] = []
    if not chapter_payload:
        findings.append(
            GateFinding(
                code="prewrite_contract_chapter_missing",
                severity="critical",
                message=f"prewrite contract missing chapter {chapter_no}",
                path=f"prewrite-contract.json:chapter:{chapter_no}",
                repair_action="materialize chapter prewrite contract before writing",
            )
        )
    elif not str(chapter_payload.get("prewrite_anchor") or "").strip():
        findings.append(
            GateFinding(
                code="prewrite_anchor_missing",
                severity="critical",
                message=f"chapter {chapter_no} lacks prewrite_anchor",
                path=f"prewrite-contract.json:chapter:{chapter_no}:prewrite_anchor",
                repair_action="add prewrite_anchor linking seam, outline, and payoff",
            )
        )
    return GateVerdict(
        gate_name="prewrite_contract_coverage",
        verdict="blocked" if findings else "pass",
        coverage=0.0 if findings else 1.0,
        findings=tuple(findings),
        metrics={"chapter_no": chapter_no},
    )


def evaluate_prewrite_contract_readiness(
    *,
    chapter_no: int,
    contract: Mapping[str, Any] | None = None,
    story_bible_dir: str | Path | None = None,
) -> GateVerdict:
    payload = dict(contract or {})
    if story_bible_dir is not None and not payload:
        payload = load_prewrite_contract(story_bible_dir)
    coverage = evaluate_prewrite_contract_coverage(
        chapter_no=chapter_no,
        contract=payload,
    )
    chapter_payload = _chapter_payload(payload, chapter_no)
    findings: list[GateFinding] = list(coverage.findings)
    if not chapter_payload:
        return GateVerdict(
            gate_name="prewrite_contract_readiness",
            verdict="blocked",
            coverage=0.0,
            findings=tuple(findings),
            metrics={"chapter_no": chapter_no, "specificity_score": 0.0},
        )

    missing = [
        key
        for key in _REQUIRED_READINESS_FIELDS
        if not _field_has_content(chapter_payload.get(key))
    ]
    for key in missing:
        findings.append(
            GateFinding(
                code="PREWRITE_REQUIRED_FIELD_MISSING",
                severity="critical",
                message=f"chapter {chapter_no} prewrite contract missing {key}",
                path=f"prewrite-contract.json:chapter:{chapter_no}:{key}",
                repair_action=(
                    "fill a concrete prewrite field before drafting this chapter"
                ),
            )
        )

    for key in _REQUIRED_READINESS_FIELDS:
        value = chapter_payload.get(key)
        hits = _placeholder_hits(value)
        if hits:
            findings.append(
                GateFinding(
                    code="PREWRITE_PLACEHOLDER_TEXT",
                    severity="critical",
                    message=(
                        f"chapter {chapter_no} {key} contains placeholder wording: "
                        f"{', '.join(sorted(set(hits)))}"
                    ),
                    path=f"prewrite-contract.json:chapter:{chapter_no}:{key}",
                    repair_action=(
                        "replace placeholder wording with concrete people, evidence, "
                        "place, time, payoff, and pressure handoff"
                    ),
                )
            )

    scene_beats = _as_text_sequence(chapter_payload.get("scene_beats"))
    thin_beat_count = sum(1 for beat in scene_beats if _beat_is_too_thin(beat))
    if len(scene_beats) < 3 or thin_beat_count:
        findings.append(
            GateFinding(
                code="PREWRITE_SCENE_BEATS_TOO_THIN",
                severity="critical",
                message=(
                    f"chapter {chapter_no} scene_beats must contain at least three "
                    "concrete beats and each beat should move two narrative axes"
                ),
                path=f"prewrite-contract.json:chapter:{chapter_no}:scene_beats",
                repair_action=(
                    "add three or more concrete scene beats that advance at least two "
                    "of plot/clue/relationship/status/resource/exposure"
                ),
            )
        )

    if not _field_has_content(chapter_payload.get("forbidden_moves")):
        findings.append(
            GateFinding(
                code="PREWRITE_FORBIDDEN_MOVES_MISSING",
                severity="critical",
                message=f"chapter {chapter_no} lacks forbidden move constraints",
                path=f"prewrite-contract.json:chapter:{chapter_no}:forbidden_moves",
                repair_action=(
                    "add forbidden moves that prevent drift, premature reveals, and "
                    "state rollback"
                ),
            )
        )

    combined_text = _stringify(chapter_payload)
    if combined_text.strip() and not _has_named_entity(combined_text):
        findings.append(
            GateFinding(
                code="PREWRITE_LACKS_NAMED_ENTITY",
                severity="critical",
                message=(
                    f"chapter {chapter_no} prewrite contract lacks a named person, "
                    "object, place, or time anchor"
                ),
                path=f"prewrite-contract.json:chapter:{chapter_no}",
                repair_action=(
                    "bind the contract to named characters, evidence, places, clocks, "
                    "or concrete objects"
                ),
            )
        )

    critical = any(finding.severity == "critical" for finding in findings)
    coverage_value = 1.0 if not findings else max(0.0, 1.0 - min(1.0, len(findings) / 6))
    return GateVerdict(
        gate_name="prewrite_contract_readiness",
        verdict="blocked" if critical else ("warn_only" if findings else "pass"),
        coverage=coverage_value,
        findings=tuple(findings),
        metrics={
            "chapter_no": chapter_no,
            "missing_field_count": len(missing),
            "scene_beat_count": len(scene_beats),
            "thin_scene_beat_count": thin_beat_count,
            "specificity_score": coverage_value,
        },
    )


def _chapter_payload(payload: Mapping[str, Any], chapter_no: int) -> Mapping[str, Any]:
    chapters = payload.get("chapters")
    if isinstance(chapters, Mapping):
        raw = chapters.get(str(chapter_no)) or chapters.get(chapter_no)
        if isinstance(raw, Mapping):
            return raw
    contracts = payload.get("chapter_contracts")
    if isinstance(contracts, list):
        for item in contracts:
            if isinstance(item, Mapping) and int(item.get("chapter_no") or 0) == chapter_no:
                return item
    if int(payload.get("chapter_no") or 0) == chapter_no:
        return payload
    return {}


def _field_has_content(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_field_has_content(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_field_has_content(item) for item in value)
    return bool(value)


def _placeholder_hits(value: object) -> tuple[str, ...]:
    text = _stringify(value)
    if not text.strip():
        return ()
    return tuple(pattern for pattern in PLACEHOLDER_BLACKLIST if pattern and pattern in text)


def _as_text_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(text for item in value if (text := _stringify(item).strip()))
    return ()


def _change_axis_count(text: str) -> int:
    return len({match.group(0).lower() for match in _CHANGE_AXIS_RE.finditer(text)})


def _beat_is_too_thin(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 14:
        return True
    if _placeholder_hits(stripped):
        return True
    axis_count = _change_axis_count(stripped)
    # Chinese prewrite beats often encode the axis through concrete evidence
    # and action rather than English labels. Treat a named anchor plus a
    # concrete state/evidence verb as executable even when axis labels are absent.
    if axis_count >= 2:
        return False
    concrete_action = re.search(
        r"落地|确认|迫使|暴露|反咬|公开|转入|推进",
        stripped,
    )
    return not (_has_named_entity(stripped) and concrete_action)


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(f"{key}: {_stringify(item)}" for key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "\n".join(_stringify(item) for item in value)
    return str(value)
