"""Intra-chapter factual continuity critic (advisory).

Why this module exists
----------------------
The 2026-07-20 generation-unit A/B found chapter-first beats scene-by-scene on
state coherence *except* in the one case where the chapter broke its own facts:
it weighed 低筋面粉 and then kneaded 高筋面粉, and lost that case 4-0. Scene mode
loses to cross-scene drift; chapter mode loses to intra-chapter drift. Only the
first had a gate.

The gap was verified before this gate was written, not assumed: the offending
sample scores zero findings from ``common_sense_gate`` and only an unrelated
``ENDING_HOOK_MISSING`` from ``deterministic_post_write_audit``. Existing checks
cover cross-chapter canon regression and a few hardcoded arithmetic patterns —
none of them notice a named object changing its stated properties mid-chapter.

Advisory by construction
------------------------
Findings are emitted at ``warn`` and never block a write. An LLM contradiction
detector has a real false-positive rate, and this codebase has already shipped
one book-killing false positive from a checker that was allowed to block
(``NAMING_OUT_OF_POOL``). Findings feed the repair-hint path so a flagged
chapter gets a targeted patch instead of a rejection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.settings import AppSettings

logger = logging.getLogger(__name__)

CONTINUITY_FINDING_CODE = "CHAPTER_INTERNAL_CONTINUITY"

_MAX_FINDINGS = 6
_MAX_CHAPTER_CHARS = 12_000
# Categories the critic may return. Anything else is dropped rather than
# forwarded, so a hallucinated category cannot invent a new repair scope.
_ALLOWED_CATEGORIES = frozenset(
    {"object", "quantity", "name", "body_state", "time", "place"}
)


@dataclass(frozen=True)
class ContinuityFinding:
    category: str
    detail: str
    first_evidence: str
    second_evidence: str

    def as_repair_hint(self) -> str:
        return (
            f"[{self.category}] {self.detail}"
            f"（前文：{self.first_evidence} / 后文：{self.second_evidence}）"
        )


@dataclass(frozen=True)
class ContinuityReport:
    findings: tuple[ContinuityFinding, ...] = ()
    skipped_reason: str | None = None

    @property
    def passed(self) -> bool:
        return not self.findings

    def repair_hints(self) -> tuple[str, ...]:
        return tuple(finding.as_repair_hint() for finding in self.findings)

    def as_payload(self) -> dict[str, Any]:
        return {
            "code": CONTINUITY_FINDING_CODE,
            "passed": self.passed,
            "skipped_reason": self.skipped_reason,
            "findings": [
                {
                    "category": item.category,
                    "detail": item.detail,
                    "first_evidence": item.first_evidence,
                    "second_evidence": item.second_evidence,
                }
                for item in self.findings
            ],
        }


_SYSTEM_PROMPT = """你是中文长篇小说的连贯性核对员。你只做一件事：找出**同一章正文内部前后自相矛盾**的事实。

只报这几类矛盾：
- object：同一件东西的属性/材质/型号前后不一致（例：先称「低筋面粉」，和面时变成「高筋面粉」）
- quantity：同一批东西的数目/金额/重量前后对不上
- name：同一个人的姓名/称谓/身份前后不一致，或同一人被当作两个人
- body_state：已经写死的身体状态后文无交代地消失或反转（受伤的手又能用了）
- time：同一章内的时刻/时长/先后顺序互相打架
- place：人物在没有交代位移的情况下换了地点

严格要求：
- 只报**同一章正文里能同时找到两处原文**的矛盾。找不到两处原句就不要报。
- 不报文笔、节奏、AI腔、情节好坏——那些有别的评审负责，不归你管。
- 不报"作者可能没交代清楚"的猜测。前后确实冲突才算。
- 人物**故意说谎、记错、认知错误**不是矛盾；叙述层面确实写反了才是。
- **叙事聚焦不是数量矛盾**：一堆东西里后文只跟着写其中一件（"那只牛皮纸信封""那把刀"），
  是正常的镜头收窄，不是说别的消失了。只有正文明确说出"只剩一个""全都没了""一共两件"
  这类与前文数目直接打架的话，才算 quantity 矛盾。
- **同一处矛盾只报一条**：换个角度描述同一个冲突，不要拆成多条。
- 宁可少报也不要误报。没有把握就不报。没有矛盾就返回空列表。

只输出 JSON，不要其他文字：
{"findings":[{"category":"object","detail":"一句话说清矛盾在哪","first_evidence":"前一处正文原句","second_evidence":"后一处正文原句"}]}"""


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(raw[start : end + 1])
        if isinstance(payload, dict):
            return payload
    raise ValueError("continuity critic response did not contain a JSON object")


def parse_continuity_findings(raw_text: str, *, chapter_text: str) -> ContinuityReport:
    """Parse and *verify* the critic response against the chapter.

    Every finding must quote two snippets that actually occur in the chapter.
    A model that paraphrases its evidence is reporting something it cannot
    point at, which is the shape a false positive takes here.
    """

    try:
        payload = _parse_json_object(raw_text)
    except (json.JSONDecodeError, ValueError):
        return ContinuityReport(skipped_reason="unparseable_response")

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        return ContinuityReport(skipped_reason="missing_findings_field")

    verified: list[ContinuityFinding] = []
    # Two findings anchored on the same first quote are the same contradiction
    # described twice. Observed live (2026-07-21 ch6): the envelope-count claim
    # came back as two findings sharing one first_evidence, which would have
    # sent the same repair hint through twice. The prompt asks for one finding
    # per contradiction; this makes it structural rather than a request.
    seen_anchors: set[str] = set()
    for item in raw_findings:
        if not isinstance(item, Mapping):
            continue
        category = str(item.get("category") or "").strip().lower()
        if category not in _ALLOWED_CATEGORIES:
            continue
        detail = str(item.get("detail") or "").strip()
        first = str(item.get("first_evidence") or "").strip()
        second = str(item.get("second_evidence") or "").strip()
        if not detail or not first or not second:
            continue
        if first == second:
            continue
        if not _quote_is_grounded(first, chapter_text):
            continue
        if not _quote_is_grounded(second, chapter_text):
            continue
        anchor = _normalise_for_match(first)
        if anchor in seen_anchors:
            continue
        seen_anchors.add(anchor)
        verified.append(
            ContinuityFinding(
                category=category,
                detail=detail,
                first_evidence=first,
                second_evidence=second,
            )
        )
        if len(verified) >= _MAX_FINDINGS:
            break
    return ContinuityReport(findings=tuple(verified))


def _quote_is_grounded(quote: str, chapter_text: str) -> bool:
    """Accept a quote only if it is really in the chapter.

    Models routinely normalise punctuation or drop an ellipsis when quoting, so
    an exact match alone rejects true positives. Comparing with punctuation and
    whitespace stripped keeps that tolerance without accepting a paraphrase:
    the remaining characters still have to appear verbatim and in order.
    """

    needle = _normalise_for_match(quote)
    if len(needle) < 4:
        return False
    return needle in _normalise_for_match(chapter_text)


_PUNCT_RE = re.compile(r"[\s，。！？；：、“”‘’（）《》〈〉—…·,.!?;:\"'()\[\]{}-]+")


def _normalise_for_match(text: str) -> str:
    return _PUNCT_RE.sub("", str(text or ""))


async def audit_chapter_continuity(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapter_text: str,
    chapter_number: int,
    project_id: Any | None = None,
    participants: Sequence[str] = (),
) -> ContinuityReport:
    """Ask the critic model for intra-chapter contradictions.

    Fails open: any transport, quota, or parsing failure yields an empty report
    with a ``skipped_reason``. A continuity critic that can hard-fail a chapter
    would hand a book's fate to one advisory LLM call.
    """

    text = str(chapter_text or "").strip()
    if not text:
        return ContinuityReport(skipped_reason="empty_chapter")
    if len(text) > _MAX_CHAPTER_CHARS:
        text = text[:_MAX_CHAPTER_CHARS]

    roster = "、".join(str(name) for name in participants if str(name).strip())
    user_prompt = (
        f"章节号：第{chapter_number}章\n"
        + (f"本章登场人物（规范名）：{roster}\n" if roster else "")
        + "\n以下是本章完整正文，请只核对它自身内部的事实矛盾：\n\n"
        + text
    )

    try:
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                model_tier="strong",
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                # An empty finding list is the correct degraded answer: a critic
                # that cannot run must not invent contradictions, and must not
                # stall the chapter either.
                fallback_response='{"findings": []}',
                prompt_template="chapter_continuity_critic",
                prompt_version="v1",
                project_id=project_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - advisory gate must never break a run
        logger.warning(
            "chapter %d: continuity critic unavailable (%s); skipping",
            chapter_number,
            exc.__class__.__name__,
        )
        return ContinuityReport(skipped_reason=f"llm_error:{exc.__class__.__name__}")

    # Read the field directly rather than via getattr-with-default: the first
    # cut of this module used ``getattr(completion, "text", "")``, and because
    # the result field is ``content``, every call silently produced an empty
    # string. The gate looked healthy and found nothing, forever.
    return parse_continuity_findings(completion.content or "", chapter_text=text)


__all__ = [
    "CONTINUITY_FINDING_CODE",
    "ContinuityFinding",
    "ContinuityReport",
    "audit_chapter_continuity",
    "parse_continuity_findings",
]
