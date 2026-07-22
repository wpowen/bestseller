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
# Single source of truth lives in acceptance_contract so the last scene's
# writer prompt quotes exactly the terms this audit scans for.
from bestseller.services.acceptance_contract import (  # noqa: E402
    ENDING_HOOK_ANCHOR_TERMS as _HOOK_TERMS,
)


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
    findings.extend(_scan_scene_card_prose_copy(text, scenes))
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
        if segment is not None and _phrase_present_fuzzy(text, image):
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


def _scan_scene_card_prose_copy(
    text: str,
    scenes: Sequence[Any],
) -> list[DeterministicAuditFinding]:
    """Block prose copied from scene-card writing aids.

    Scene facts (names, objects, state changes and hook outcomes) are allowed to
    recur.  Dialogue templates, sensory descriptions, rewrite hints and action
    scripts are not: chapter-first generation must realize those facts with new
    prose instead of expanding or lightly paraphrasing planning text.
    """

    if not text or not scenes:
        return []
    prose_segments = [
        (match.group(0), match.start())
        for match in re.finditer(r"[^。！？!?\n]{8,}[。！？!?]?", text)
    ]
    findings: list[DeterministicAuditFinding] = []
    seen_sources: set[str] = set()
    for scene in scenes:
        for source in _scene_card_prose_sources(scene):
            normalized_source = _normalize_copy_text(source)
            # Short fragments are usually facts or names, not evidence of
            # scaffolding theft.  Require a sentence-like source.
            if len(normalized_source) < 18 or normalized_source in seen_sources:
                continue
            seen_sources.add(normalized_source)
            copied_at = _find_near_copy_offset(
                normalized_source,
                prose_segments,
            )
            if copied_at is None:
                continue
            line, col = _line_col(text, copied_at)
            findings.append(
                DeterministicAuditFinding(
                    code="SCENE_CARD_PROSE_COPIED",
                    severity="high",
                    matched_text=source[:80],
                    line_number=line,
                    column=col,
                    suggested_action=(
                        "保留该节点要求的事实和结果，但用新的动作、对白与后果重写；"
                        "不得照抄或换词改写场景卡句子。"
                    ),
                )
            )
            if len(findings) >= 3:
                return findings
    return findings


def _scene_card_prose_sources(scene: Any) -> tuple[str, ...]:
    values: list[Any] = [
        getattr(scene, "key_dialogue_beats", None),
        getattr(scene, "sensory_anchors", None),
        getattr(scene, "rewrite_hint", None),
    ]
    metadata = getattr(scene, "metadata_json", None)
    if isinstance(metadata, Mapping):
        methodology = metadata.get("methodology_contract")
        scene_contract = metadata.get("scene_contract")
        for contract in (methodology, scene_contract):
            if not isinstance(contract, Mapping):
                continue
            for key, value in contract.items():
                normalized_key = str(key).casefold()
                if any(
                    marker in normalized_key
                    for marker in (
                        "dialogue",
                        "sensory",
                        "rewrite",
                        "prose",
                        "action_sequence",
                    )
                ):
                    values.append(value)

    sources: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            clean = value.strip()
            if clean:
                sources.append(clean)
            return
        if isinstance(value, Mapping):
            for nested in value.values():
                collect(nested)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for nested in value:
                collect(nested)

    for value in values:
        collect(value)
    return tuple(sources)


def _normalize_copy_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "")).casefold()


def _find_near_copy_offset(
    normalized_source: str,
    prose_segments: Sequence[tuple[str, int]],
) -> int | None:
    source_len = len(normalized_source)
    for segment, offset in prose_segments:
        normalized_segment = _normalize_copy_text(segment)
        if normalized_source in normalized_segment:
            return offset
        if len(normalized_segment) < max(12, int(source_len * 0.75)):
            continue
        if len(normalized_segment) <= int(source_len * 1.25):
            if SequenceMatcher(None, normalized_source, normalized_segment).ratio() >= 0.9:
                return offset
            continue
        window = max(1, int(source_len * 1.15))
        step = max(1, source_len // 5)
        for start in range(0, max(1, len(normalized_segment) - window + 1), step):
            candidate = normalized_segment[start : start + window]
            if SequenceMatcher(None, normalized_source, candidate).ratio() >= 0.9:
                return offset
    return None


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
    # Window widened 120→220 (2026-07-22). Chapter-first prose reliably lands
    # the hook in the last 2-3 sentences and then adds one closing image; a
    # 120-char window sees only that trailing image and misfires. Verified on a
    # live chapter that ended on THREE strong hooks (a rune surfacing in the
    # protagonist's veins, a red sub-signature about to appear on a roster, a
    # bronze token routing to the mine's true owner) followed by one quiet
    # image sentence — flagged as hookless, repaired twice, still flagged.
    ending = re.sub(r"\s+", "", text)[-220:]
    # A concrete approaching threat is a valid hook even when it is not
    # phrased as a question or one of the small legacy anchor words.  The
    # xianxia canary ended with “甬道那头脚步声更近了。不是一个人。”;
    # rejecting that forced a full-chapter repair despite a clear next-step
    # threat.  Keep this narrow: require an approach/arrival pattern rather
    # than accepting any generic mention of a door or sound.
    _approaching_threat = re.search(
        r"(?:脚步|人影|声音|门外|甬道|追兵|身影).{0,10}"
        r"(?:更近|逼近|靠近|越来越近|到了|来了|不止一个|不是一个|忽然响)",
        ending,
    )
    # Forward-looking suspense: an unresolved future state the reader is left
    # waiting on, even without a question mark or an arriving threat. Kept
    # narrow — each pattern names an open thread ("will appear", "handed toward
    # someone", "along some rule chain he doesn't know"), not generic prose.
    _forward_suspense = re.search(
        r"(?:会多出|将要|即将|就要)"
        r"|(?:递|送|传|流)(?:向|往|给)(?:那|这|某|一)"
        r"|(?:不知道|说不清|看不见|未知)的(?:某|那|一)?(?:条|种|个|道|场)"
        r"|(?:某条|某种|某个|某道)(?:规则|线|链|力量|命令)"
        r"|(?:待甄别|待查|悬而未决)"
        r"|(?:若隐若现|浮现|显出|冒出)",
        ending,
    )
    if (
        not ending
        or any(term in ending for term in _HOOK_TERMS)
        or _approaching_threat
        or _forward_suspense
    ):
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
    clean_text = _normalize_phrase_for_presence(text)
    clean_phrase = _normalize_phrase_for_presence(phrase)
    if not clean_phrase:
        return True
    if clean_phrase in clean_text:
        return True
    # Signature images are short semantic anchors, not quotes.  A writer may
    # naturally turn “炭灰里跪稳的跛足” into “膝盖落进炭灰，跛足的节奏稳住”
    # while preserving the same image.  For Chinese anchors, accept a
    # majority of their meaningful bigrams when the phrase contains at least
    # two such units.  This is deliberately stricter than a single-token hit
    # and still rejects unrelated prose.
    if re.search(r"[\u4e00-\u9fff]", clean_phrase):
        phrase_bigrams = {
            clean_phrase[index : index + 2]
            for index in range(len(clean_phrase) - 1)
            if re.search(r"[\u4e00-\u9fff]{2}", clean_phrase[index : index + 2])
        }
        if len(phrase_bigrams) >= 2:
            matched = sum(1 for bigram in phrase_bigrams if bigram in clean_text)
            if matched >= 2 and matched / len(phrase_bigrams) >= 0.25:
                return True
    window = max(len(clean_phrase), 4)
    for index in range(0, max(1, len(clean_text) - window + 1)):
        if SequenceMatcher(None, clean_text[index : index + window], clean_phrase).ratio() >= 0.7:
            return True
    return False


def _normalize_phrase_for_presence(value: str) -> str:
    table = str.maketrans(
        {
            "“": '"',
            "”": '"',
            "「": '"',
            "」": '"',
            "『": '"',
            "』": '"',
            "，": ",",
            "。": ".",
            "；": ";",
            "：": ":",
            "！": "!",
            "？": "?",
        }
    )
    return re.sub(r"\s+", "", (value or "").translate(table))


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
