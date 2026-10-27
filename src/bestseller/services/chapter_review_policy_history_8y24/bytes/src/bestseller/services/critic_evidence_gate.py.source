"""Require critic outputs to cite body evidence (anti empty-review)."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Final

CRITIC_MISSING_BODY_EVIDENCE: Final[str] = "CRITIC_MISSING_BODY_EVIDENCE"
CRITIC_EMPTY_REVIEW: Final[str] = "CRITIC_EMPTY_REVIEW"
CRITIC_BODY_EVIDENCE_MISMATCH: Final[str] = "CRITIC_BODY_EVIDENCE_MISMATCH"
CRITIC_BODY_EVIDENCE_UNAVAILABLE: Final[str] = "CRITIC_BODY_EVIDENCE_UNAVAILABLE"
CRITIC_ANCHOR_ADAPTER_POLICY: Final[str] = "critic-catalog-bounded-anchor-v2"
CRITIC_CITATION_TUPLE_POLICY: Final[str] = "critic-citation-tuple.v1"
MAX_CRITIC_ANCHOR_RANGE: Final[int] = 256
_ANCHOR_RANGE_RE = re.compile(
    r"(?P<body>D[0-9a-f]{16}):P(?P<first>[0-9]{4,})"
    r"\s*[-–—]\s*(?:(?P<end_body>D[0-9a-f]{16}):)?P(?P<last>[0-9]{4,})"
)

_EVIDENCE_MARKERS = (
    "EVIDENCE:",
    "证据：",
    "证据:",
    "正文摘录",
    "引用：",
)
_QUOTED_EXCERPT_RE = re.compile(
    r'「(?P<corner>[^」]{4,})」|“(?P<curly>[^”]{4,})”|"(?P<ascii>[^"]{4,})"'
)
_EVIDENCE_REGION_RE = re.compile(
    r"(?:^|\n)\s*(?:EVIDENCE|证据|正文摘录|引用)\s*[:：]?"
    r"(?P<body>.*?)(?=\n\s*(?:VERDICT|METHODOLOGY|REWRITE_DIRECTION|COMMENTARY|"
    r"结论|方法论|重写要求|评论)\s*[:：]|\Z)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class CriticEvidenceFinding:
    severity: str
    code: str
    detail: str


@dataclass(frozen=True)
class CriticEvidenceReport:
    passed: bool
    findings: tuple[CriticEvidenceFinding, ...]
    citations: tuple[dict, ...] = ()
    source_coverage: dict | None = None


def _source_coverage_receipt(
    catalog_count: int, anchor_groups: tuple[tuple[str, ...], ...],
) -> dict:
    """Count immutable source coverage, never issue votes or score weight."""
    distinct = list(dict.fromkeys(key for group in anchor_groups for key in group))
    return {
        "schema": "critic-distinct-source-coverage.v1",
        "citation_row_count": len(anchor_groups),
        "distinct_anchor_ids": distinct,
        "distinct_anchor_count": len(distinct),
        "catalog_anchor_count": catalog_count,
        "counts_as_votes": False,
        "scores_modified": False,
    }


def build_critic_body_anchors(chapter_text: str) -> tuple[dict, ...]:
    """Bind selectable paragraph IDs to exact offsets in one immutable body."""
    digest = hashlib.sha256(chapter_text.encode("utf-8")).hexdigest()
    return tuple(
        {"id": f"D{digest[:16]}:P{index:04d}", "start": match.start(), "end": match.end(),
         "offset_unit": "unicode-codepoint", "body_sha256": digest,
         "text": match.group()}
        for index, match in enumerate(
            # Keep original bytes/offsets. Treat CRLF atomically so its CR
            # cannot become paragraph text or hide the following blank line.
            re.finditer(r"\S[^\r\n]*(?:(?>\r\n|\r|\n)(?![ \t]*(?:\r\n|\r|\n))[^\r\n]*)*", chapter_text), 1
        )
    )


def _unique_citation_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_citation_key")
        result[key] = value
    return result


def _resolve_anchor_ids(key: str, anchors: dict[str, dict]) -> tuple[str, ...]:
    if key in anchors:
        return (key,)
    match = _ANCHOR_RANGE_RE.fullmatch(key)
    if not match or match["end_body"] not in (None, match["body"]):
        raise ValueError("citation_range")
    first, last = int(match["first"]), int(match["last"])
    if not 0 < last - first < MAX_CRITIC_ANCHOR_RANGE:
        raise ValueError("citation_range_bound")
    keys = tuple(f'{match["body"]}:P{index:04d}' for index in range(first, last + 1))
    # Require canonical endpoints and every intervening catalog entry, without guessing.
    if match["first"] != f"{first:04d}" or match["last"] != f"{last:04d}":
        raise ValueError("citation_range_endpoint")
    if not all(value in anchors for value in keys):
        raise ValueError("citation_range_missing")
    return keys


def _validate_anchor_citations(
    text: str, chapter_text: str | None, *,
    citation_policy: str = CRITIC_ANCHOR_ADAPTER_POLICY,
) -> CriticEvidenceReport:
    """Derive source bindings, never a new grade, from retained commentary.

    One range remains one issue (at most 256 paragraphs); independent issues
    may overlap. The future tuple policy permits distinct issues to reuse one
    source selection; the default legacy policy rejects repeated selections. Receipts travel
    inside citations so existing report persistence retains the adapter version.
    """
    try:
        if citation_policy not in {CRITIC_ANCHOR_ADAPTER_POLICY, CRITIC_CITATION_TUPLE_POLICY}:
            raise ValueError("citation_policy_unknown")
        body = chapter_text or ""
        catalog = build_critic_body_anchors(body)
        anchors = {item["id"]: item for item in catalog}
        if len(anchors) != len(catalog) or text.count("EVIDENCE_ANCHORS:") != 1:
            raise ValueError("citation_catalog_ambiguous")
        raw = text.split("EVIDENCE_ANCHORS:", 1)[1].lstrip()
        if raw.startswith("["):
            items, end = json.JSONDecoder(object_pairs_hook=_unique_citation_object).raw_decode(raw)
            if raw[end:].strip():
                raise ValueError("citation_trailing_content")
        else:
            items = []
            for line in raw.splitlines():
                line = line.strip().removeprefix("- ")
                if not line:
                    continue
                parts = line.split("|", 2)
                if len(parts) != 3:
                    raise ValueError("citation_line")
                items.append(dict(zip(("id", "dimension", "reason"), (v.strip() for v in parts), strict=True)))
        if not isinstance(items, list) or not 1 <= len(items) <= len(catalog):
            raise ValueError("citation_count")
        citations = []
        seen = set()
        anchor_groups = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"id", "dimension", "reason"}:
                raise ValueError("citation_schema")
            key = item["id"]
            if not isinstance(key, str):
                raise ValueError("citation_id")
            keys = _resolve_anchor_ids(key, anchors)
            if not all(isinstance(item[k], str) and len(item[k].strip()) >= 2
                       for k in ("dimension", "reason")):
                raise ValueError("citation_rationale")
            identity = (
                (keys, item["dimension"], item["reason"])
                if citation_policy == CRITIC_CITATION_TUPLE_POLICY else keys
            )
            if identity in seen:
                raise ValueError("citation_duplicate")
            seen.add(identity)
            anchor_groups.append(keys)
            segments = [dict(anchors[value]) for value in keys]
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            previous_end = -1
            for segment in segments:
                start, end = segment["start"], segment["end"]
                if (type(start) is not int or type(end) is not int
                        or not previous_end <= start < end <= len(body)
                        or segment["offset_unit"] != "unicode-codepoint"
                        or segment["body_sha256"] != digest
                        or segment["text"] != body[start:end]
                        or not segment["id"].startswith(f"D{digest[:16]}:P")):
                    raise ValueError("citation_source_binding")
                previous_end = end
            resolved = dict(segments[0])
            if len(segments) > 1:
                resolved.update(id=key, end=segments[-1]["end"],
                                text=body[segments[0]["start"]:segments[-1]["end"]],
                                anchors=segments)
            citations.append({
                **resolved, "dimension": item["dimension"], "reason": item["reason"],
                "raw_id": key, "raw_citation": dict(item),
                "normalization_receipt": {
                    "policy_version": citation_policy,
                    "evidence_kind": "derived_anchor_validation",
                    "raw_commentary_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "body_sha256": digest, "anchor_ids": list(keys),
                    "source_text_sha256": hashlib.sha256(resolved["text"].encode("utf-8")).hexdigest(),
                },
            })
    except (ValueError, KeyError, TypeError, IndexError):
        return CriticEvidenceReport(False, (CriticEvidenceFinding(
            "critical", CRITIC_BODY_EVIDENCE_MISMATCH,
            "body anchor citation is malformed, missing or outside this exact draft",
        ),))
    return CriticEvidenceReport(
        True, (), tuple(citations),
        _source_coverage_receipt(len(catalog), tuple(anchor_groups))
        if citation_policy == CRITIC_CITATION_TUPLE_POLICY else None,
    )


def _quoted_excerpts(text: str) -> tuple[str, ...]:
    regions = tuple(match.group("body") for match in _EVIDENCE_REGION_RE.finditer(text))
    return tuple(
        match.group(name)
        for region in regions
        for match in _QUOTED_EXCERPT_RE.finditer(region)
        for name in ("corner", "curly", "ascii")
        if match.group(name)
    )


def _evidence_match_text(value: str) -> str:
    """Normalize only layout whitespace before comparing a quoted excerpt."""

    return re.sub(r"\s+", "", str(value or "")).strip()


def validate_critic_commentary(
    commentary: str,
    *,
    chapter_text: str | None = None,
    min_commentary_chars: int = 40,
    require_evidence_line: bool = True,
    citation_policy: str = CRITIC_ANCHOR_ADAPTER_POLICY,
) -> CriticEvidenceReport:
    """Ensure critic/LLM review text is non-empty and cites the draft."""

    text = (commentary or "").strip()
    if not isinstance(citation_policy, str) or citation_policy not in {
        CRITIC_ANCHOR_ADAPTER_POLICY, CRITIC_CITATION_TUPLE_POLICY,
    }:
        return CriticEvidenceReport(False, (CriticEvidenceFinding(
            "critical", CRITIC_BODY_EVIDENCE_MISMATCH, "unknown citation identity policy",
        ),))
    if (citation_policy == CRITIC_CITATION_TUPLE_POLICY and require_evidence_line
            and "EVIDENCE_ANCHORS:" not in text):
        return CriticEvidenceReport(False, (CriticEvidenceFinding(
            "critical", CRITIC_MISSING_BODY_EVIDENCE, "tuple policy requires exact body anchors",
        ),))
    findings: list[CriticEvidenceFinding] = []
    if len(text) < min_commentary_chars:
        findings.append(
            CriticEvidenceFinding(
                severity="critical",
                code=CRITIC_EMPTY_REVIEW,
                detail=f"review commentary too short ({len(text)} chars)",
            )
        )
        return CriticEvidenceReport(passed=False, findings=tuple(findings))

    if require_evidence_line and "EVIDENCE_ANCHORS:" in text:
        return _validate_anchor_citations(commentary, chapter_text, citation_policy=citation_policy)

    if require_evidence_line:
        has_marker = any(marker in text for marker in _EVIDENCE_MARKERS)
        excerpts = _quoted_excerpts(text)
        has_quote = bool(excerpts)
        if not (has_marker and has_quote):
            findings.append(
                CriticEvidenceFinding(
                    severity="critical",
                    code=CRITIC_MISSING_BODY_EVIDENCE,
                    detail=(
                        "review must include EVIDENCE/证据 line with a quoted "
                        "excerpt from the draft"
                    ),
                )
            )

        if chapter_text is not None:
            body = _evidence_match_text(chapter_text)
            if not body:
                findings.append(
                    CriticEvidenceFinding(
                        severity="critical",
                        code=CRITIC_BODY_EVIDENCE_UNAVAILABLE,
                        detail="reviewed draft body is empty; evidence cannot be verified",
                    )
                )
            elif excerpts and not all(
                _evidence_match_text(excerpt) in body for excerpt in excerpts
            ):
                findings.append(
                    CriticEvidenceFinding(
                        severity="critical",
                        code=CRITIC_BODY_EVIDENCE_MISMATCH,
                        detail="quoted evidence does not occur in the reviewed draft",
                    )
                )

    return CriticEvidenceReport(passed=not findings, findings=tuple(findings))


def build_critic_evidence_prompt_suffix(*, language: str = "zh-CN", anchored: bool = False) -> str:
    """Append to critic system prompts — mandatory evidence format."""

    if anchored:
        return (
            "\n# BODY EVIDENCE PROTOCOL\n"
            "Use the supplied CURRENT_DRAFT BODY_ANCHOR_CATALOG for evidence. Do not compose quotations.\n"
            'EVIDENCE_ANCHORS:\n<actual catalog ID> | <assessed dimension> | <explain how this paragraph supports the verdict>\n'
            "Use one citation per line, from 1 up to the number of entries in this exact BODY_ANCHOR_CATALOG. "
            "Use distinct real IDs or same-body contiguous ranges of at most 256 paragraphs; preserve every issue. "
            "No JSON quoting or Markdown bullets required. "
            "Put this evidence block last. The service resolves exact original text and offsets. "
            "No catalog ID may refer to a plan, old draft or proposed repair. "
            "其他解释中的引号不作为正文摘录；证据只选目录中的真实段落ID并说明关联。\n"
        )
    if str(language or "").lower().startswith("zh"):
        return (
            "\n# OUTPUT FORMAT（追加，必填）\n"
            "EVIDENCE: 用引号摘录正文中 1–2 处具体句子（每处 ≥8 字），并说明对应哪条评分维度。\n"
            "禁止只写「30章均满足」类空审；无摘录视为未读正文。\n"
        )
    return (
        "\n# OUTPUT FORMAT (required)\n"
        "EVIDENCE: quote 1-2 concrete sentences from the draft (≥8 chars each) "
        "and map each to a rubric dimension.\n"
    )


__all__ = [
    "CRITIC_ANCHOR_ADAPTER_POLICY",
    "CRITIC_BODY_EVIDENCE_MISMATCH",
    "CRITIC_BODY_EVIDENCE_UNAVAILABLE",
    "CRITIC_EMPTY_REVIEW",
    "CRITIC_MISSING_BODY_EVIDENCE",
    "CriticEvidenceFinding",
    "CriticEvidenceReport",
    "build_critic_evidence_prompt_suffix",
    "validate_critic_commentary",
]
