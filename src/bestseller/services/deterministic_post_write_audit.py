from __future__ import annotations

# ruff: noqa: RUF001, ANN401
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
import time
from typing import Any

from bestseller.services.canon_guardrails import load_canon_guardrails_file
from bestseller.services.chapter_prose_segmenter import segment_chapter_prose
from bestseller.services.material_entity_registry import EntityStatus, build_entity_registry


@dataclass(frozen=True)
class DeterministicAuditFinding:
    code: str
    severity: str
    matched_text: str
    line_number: int
    column: int
    suggested_action: str


@dataclass(frozen=True)
class DeterministicAuditReport:
    chapter_number: int
    findings: tuple[DeterministicAuditFinding, ...]
    passed: bool
    summary: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "findings": [asdict(item) for item in self.findings],
            "passed": self.passed,
            "summary": dict(self.summary),
        }


_ACTION_VERBS = frozenset(
    "动 握 推 撞 撕 压 按 转 闯 抓 扣 拽 提 砸 跑 退 站 起 落 抬 摁 贴 掀 踢 踩 "
    "拔 甩 扔 接 挡 避 躲 开 关 看 望 瞥 扫 盯 听 闻 摸 探 写 刻 咬".split()
)
_SENSORY_TERMS = frozenset(
    "冷 热 疼 痛 腥 臭 香 亮 暗 黑 白 红 青 紫 声 响 哑 颤 湿 干 黏 烫 冰 光 影 "
    "血 雾 风 雨 霉 锈".split()
)
_HOOK_TERMS = ("？", "?", "什么", "谁", "为什么", "不对", "忽然", "突然", "响起", "裂开", "倒计时")


def audit_chapter_prose(
    *,
    chapter_text: str,
    chapter_number: int,
    project_dir: Path,
    scenes: Sequence[Any] = (),
    chapter_metadata: Mapping[str, Any] | None = None,
) -> DeterministicAuditReport:
    started = time.perf_counter()
    text = chapter_text or ""
    metadata = chapter_metadata or {}
    findings: list[DeterministicAuditFinding] = []
    findings.extend(_scan_forbidden_terms(text, project_dir))
    findings.extend(_scan_deprecated_entities(text, project_dir))
    findings.extend(_scan_signature_images(text, scenes))
    findings.extend(_scan_callback_obligations(text, metadata))
    findings.extend(_scan_opening(text))
    findings.extend(_scan_ending(text))
    findings.extend(_scan_duplicate_paragraphs(text))
    findings.extend(_scan_length(text, metadata))
    summary = Counter(item.code for item in findings)
    summary["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return DeterministicAuditReport(
        chapter_number=int(chapter_number),
        findings=tuple(findings),
        passed=not any(item.severity in {"critical", "high"} for item in findings),
        summary=dict(summary),
    )


def _scan_forbidden_terms(text: str, project_dir: Path) -> list[DeterministicAuditFinding]:
    guardrails = load_canon_guardrails_file(project_dir / "story-bible" / "canon-guardrails.json")
    findings: list[DeterministicAuditFinding] = []
    for item in guardrails.forbidden_terms:
        term = str(item.term or "").strip()
        if not term:
            continue
        for match in re.finditer(re.escape(term), text):
            line, col = _line_col(text, match.start())
            findings.append(
                DeterministicAuditFinding(
                    code="FORBIDDEN_TERM_HIT",
                    severity="critical",
                    matched_text=term,
                    line_number=line,
                    column=col,
                    suggested_action=item.suggestion or "删除或替换该禁用正典词。",
                )
            )
    return findings


def _scan_deprecated_entities(text: str, project_dir: Path) -> list[DeterministicAuditFinding]:
    try:
        registry = build_entity_registry(project_dir)
    except Exception:
        return []
    findings: list[DeterministicAuditFinding] = []
    for record in registry.records:
        if record.status != EntityStatus.DEPRECATED:
            continue
        for term in (record.canonical_name, *record.aliases):
            clean = str(term or "").strip()
            if not clean:
                continue
            pos = text.find(clean)
            if pos < 0:
                continue
            line, col = _line_col(text, pos)
            findings.append(
                DeterministicAuditFinding(
                    code="DEPRECATED_ENTITY_HIT",
                    severity="critical",
                    matched_text=clean,
                    line_number=line,
                    column=col,
                    suggested_action="替换为当前正典实体，或删除整句。",
                )
            )
    return findings


def _scan_signature_images(text: str, scenes: Sequence[Any]) -> list[DeterministicAuditFinding]:
    if not scenes:
        return []
    segments = segment_chapter_prose(text, scenes)
    by_scene = {segment.scene_number: segment for segment in segments}
    findings: list[DeterministicAuditFinding] = []
    for scene in scenes:
        image = _scene_signature_image(scene)
        if not image:
            continue
        scene_number = int(getattr(scene, "scene_number", 0) or 0)
        segment = by_scene.get(scene_number)
        segment_text = segment.text if segment is not None else text
        if _phrase_present_fuzzy(segment_text, image):
            continue
        findings.append(
            DeterministicAuditFinding(
                code="SIGNATURE_IMAGE_MISSING",
                severity="high",
                matched_text=image,
                line_number=1,
                column=1,
                suggested_action=f"补入第 {scene_number} 场招牌画面关键词。",
            )
        )
    return findings


def _scan_callback_obligations(
    text: str,
    metadata: Mapping[str, Any],
) -> list[DeterministicAuditFinding]:
    obligations = metadata.get("callback_obligations") or ()
    findings: list[DeterministicAuditFinding] = []
    for item in obligations:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("obligation_kind") or "") not in {"must_payoff", "must_reference"}:
            continue
        surface = str(item.get("clue_surface") or "").strip()
        if surface and surface not in text:
            findings.append(
                DeterministicAuditFinding(
                    code="CALLBACK_OBLIGATION_MISSING",
                    severity="high",
                    matched_text=surface,
                    line_number=1,
                    column=1,
                    suggested_action="正文必须显性 reference/payoff 该伏笔表层形态。",
                )
            )
    return findings


def _scan_opening(text: str) -> list[DeterministicAuditFinding]:
    opening = re.sub(r"\s+", "", text)[:100]
    if not opening:
        return []
    has_action = any(term in opening for term in _ACTION_VERBS)
    has_sensory = any(term in opening for term in _SENSORY_TERMS)
    if has_action and has_sensory:
        return []
    return [
        DeterministicAuditFinding(
            code="OPENING_PRESSURE_THIN",
            severity="high",
            matched_text=opening[:40],
            line_number=1,
            column=1,
            suggested_action="前100字必须同时包含可见动作和感官压力。",
        )
    ]


def _scan_ending(text: str) -> list[DeterministicAuditFinding]:
    ending = re.sub(r"\s+", "", text)[-120:]
    if not ending or any(term in ending for term in _HOOK_TERMS):
        return []
    return [
        DeterministicAuditFinding(
            code="ENDING_HOOK_MISSING",
            severity="high",
            matched_text=ending[-40:],
            line_number=max(1, text.count("\n") + 1),
            column=1,
            suggested_action="最后120字必须落到未解问题、新信息、动作未完成态或具体威胁。",
        )
    ]


def _scan_duplicate_paragraphs(text: str) -> list[DeterministicAuditFinding]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if len(p.strip()) >= 12]
    findings: list[DeterministicAuditFinding] = []
    for i, left in enumerate(paragraphs):
        for right in paragraphs[i + 1 :]:
            if _char_bigram_similarity(left, right) > 0.85:
                line, col = _line_col(text, text.find(right))
                findings.append(
                    DeterministicAuditFinding(
                        code="PARAGRAPH_DUPLICATE_PARAPHRASE",
                        severity="high",
                        matched_text=right[:60],
                        line_number=line,
                        column=col,
                        suggested_action="删除或合并近重复段落，换成推进情节的信息。",
                    )
                )
                return findings
    return findings


def _scan_length(text: str, metadata: Mapping[str, Any]) -> list[DeterministicAuditFinding]:
    hard_min = int(metadata.get("hard_min_word_count") or 0)
    hard_max = int(metadata.get("hard_max_word_count") or 0)
    if hard_min <= 0 and hard_max <= 0:
        return []
    word_count = len(re.findall(r"[\u4e00-\u9fff]", text or "")) + len(
        re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", text or "")
    )
    if hard_max > 0 and word_count > hard_max:
        return [
            DeterministicAuditFinding(
                code="LENGTH_OUT_OF_BAND",
                severity="high",
                matched_text=str(word_count),
                line_number=1,
                column=1,
                suggested_action=f"压缩到 {hard_max} 字以内。",
            )
        ]
    if hard_min > 0 and word_count < hard_min:
        return [
            DeterministicAuditFinding(
                code="LENGTH_OUT_OF_BAND",
                severity="high",
                matched_text=str(word_count),
                line_number=1,
                column=1,
                suggested_action=f"扩写到至少 {hard_min} 字。",
            )
        ]
    return []


def _scene_signature_image(scene: Any) -> str:
    metadata = getattr(scene, "metadata_json", None)
    if not isinstance(metadata, Mapping):
        return ""
    candidates = [
        metadata.get("signature_image"),
        (metadata.get("methodology_contract") or {}).get("signature_image")
        if isinstance(metadata.get("methodology_contract"), Mapping)
        else None,
        (metadata.get("scene_contract") or {}).get("signature_image")
        if isinstance(metadata.get("scene_contract"), Mapping)
        else None,
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _phrase_present_fuzzy(text: str, phrase: str) -> bool:
    clean_text = re.sub(r"\s+", "", text or "")
    clean_phrase = re.sub(r"\s+", "", phrase or "")
    if not clean_phrase:
        return True
    if clean_phrase in clean_text:
        return True
    window = max(len(clean_phrase), 4)
    for index in range(0, max(1, len(clean_text) - window + 1)):
        if SequenceMatcher(None, clean_text[index : index + window], clean_phrase).ratio() >= 0.7:
            return True
    return False


def _char_bigram_similarity(left: str, right: str) -> float:
    def grams(value: str) -> set[str]:
        chars = re.sub(r"\s+", "", value)
        return {chars[i : i + 2] for i in range(max(0, len(chars) - 1))}

    lgrams = grams(left)
    rgrams = grams(right)
    if not lgrams or not rgrams:
        return 0.0
    return (2 * len(lgrams & rgrams)) / (len(lgrams) + len(rgrams))


def _line_col(text: str, offset: int) -> tuple[int, int]:
    prefix = text[: max(0, offset)]
    line = prefix.count("\n") + 1
    col = len(prefix.rsplit("\n", 1)[-1]) + 1
    return line, col


__all__ = [
    "DeterministicAuditFinding",
    "DeterministicAuditReport",
    "audit_chapter_prose",
]
