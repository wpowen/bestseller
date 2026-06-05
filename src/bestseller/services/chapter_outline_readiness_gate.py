# ruff: noqa: ANN401,RUF001

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from bestseller.services.genre_neutral_signals import object_sensory_shortcut_hits
from bestseller.services.methodology_application_gate import evaluate_methodology_application

_AUTO_REPAIR_RESIDUE_KEYS = frozenset(
    {
        "auto_repair_adjusted_target_word_count",
        "auto_repair_block_codes",
        "auto_repair_length_scale",
        "auto_repair_hint",
        "auto_repair_attempt",
        "auto_repair_min_scene_target_floor",
        "auto_repair_scene_target_cap",
        "auto_repair_source_block_code",
        "auto_repair_original_target_word_count",
        "auto_repair_target_word_count_clamped",
    }
)

_METHODOLOGY_CONTRACT_KEYS = frozenset(
    {
        "stakes",
        "pressure_stack",
        "focus_character",
        "reveal_mode",
        "signature_image",
        "breakpoint",
    }
)

_DIRECT_KINSHIP_PATTERN = r"(父亲|母亲|爷爷|奶奶|祖父|祖母|外公|外婆)"
_PHONE_OPENING_PATTERN = re.compile(r"(电话|手机|微信|短信|语音|来电)")
_IN_PERSON_OPENING_PATTERN = re.compile(r"(楼下|门口|电梯|走廊|屋里|现场|房间|巷口|楼道|进门|推门)")
_LATE_NIGHT_DELIVERY_PATTERN = re.compile(
    r"(快递|配送单|寄件单|运单|揽收|派送)[^。！？\n]{0,40}(23[:：]\d{2}|凌晨|深夜|半夜|子时)"
    r"|(?:23[:：]\d{2}|凌晨|深夜|半夜|子时)[^。！？\n]{0,40}(快递|配送单|寄件单|运单|揽收|派送)"
)
_IMPOSSIBLE_DELIVERY_MARKER = re.compile(r"(不可能|异常|伪造|自助柜|系统延迟|补录|死后|被改过|不是活人)")
_STORY_GUARDRAIL_KEYS = frozenset(
    {
        "alternatives_rejected",
        "bad_examples",
        "blocked_terms",
        "forbidden_actions",
        "forbidden_shortcut",
        "forbidden_signals",
        "negative_examples",
        "rejected_actions",
        "rejected_alternatives",
        "rejected_options",
    }
)


@dataclass(frozen=True)
class ChapterOutlineReadinessIssue:
    code: str
    severity: str
    message: str
    path: str
    repair_hint: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "repair_hint": self.repair_hint,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class ChapterOutlineReadinessReport:
    verdict: str
    issues: tuple[ChapterOutlineReadinessIssue, ...]
    metrics: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    @property
    def blocked(self) -> bool:
        return self.verdict == "blocked"

    @property
    def blocking_issues(self) -> tuple[ChapterOutlineReadinessIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "passed": self.passed,
            "blocked": self.blocked,
            "metrics": dict(self.metrics),
            "issues": [issue.to_dict() for issue in self.issues],
            "blocking_issue_codes": [issue.code for issue in self.blocking_issues],
        }


def evaluate_chapter_outline_readiness(
    *,
    chapter_number: int,
    chapter_title: str | None,
    chapter_target_word_count: int | None,
    chapter_metadata: Mapping[str, Any] | None,
    scene_cards: Sequence[Mapping[str, Any] | Any],
    pending_rewrite_task_count: int = 0,
) -> ChapterOutlineReadinessReport:
    """Evaluate whether a chapter outline is executable before prose drafting.

    This gate catches deterministic chapter-level failures that scene-local
    richness gates cannot see: collapsed word budgets, stale auto-repair
    residue, unresolved rewrite tasks, and lifespan-impossible timeline anchors.
    """

    issues: list[ChapterOutlineReadinessIssue] = []
    scene_count = len(scene_cards)
    chapter_target = _positive_int(chapter_target_word_count)
    scene_targets = [
        _positive_int(_get(scene, "target_word_count")) or 0 for scene in scene_cards
    ]
    target_sum = sum(scene_targets)
    metrics: dict[str, Any] = {
        "chapter_number": chapter_number,
        "chapter_title": chapter_title,
        "scene_count": scene_count,
        "chapter_target_word_count": chapter_target,
        "scene_target_word_count_sum": target_sum,
        "pending_rewrite_task_count": pending_rewrite_task_count,
    }

    if scene_count == 0:
        issues.append(
            ChapterOutlineReadinessIssue(
                code="OUTLINE_NO_SCENES",
                severity="critical",
                message="Chapter has no scene cards to execute.",
                path="scene_cards",
                repair_hint="Materialize at least one scene card before drafting.",
            )
        )

    if pending_rewrite_task_count > 0:
        issues.append(
            ChapterOutlineReadinessIssue(
                code="OUTLINE_PENDING_REWRITE_TASK",
                severity="critical",
                message="Chapter still has unresolved rewrite tasks.",
                path="rewrite_tasks",
                repair_hint=(
                    "Resolve, cancel, or supersede pending rewrite tasks before "
                    "starting a new chapter run."
                ),
            )
        )

    if scene_count > 0 and chapter_target is not None:
        expected_scene_target = chapter_target / scene_count
        low_scene_threshold = max(350, int(expected_scene_target * 0.65))
        high_scene_threshold = max(low_scene_threshold + 1, int(expected_scene_target * 1.75))
        scene_sum_min_threshold, scene_sum_max_threshold = chapter_scene_budget_sum_thresholds(
            chapter_target
        )
        metrics.update(
            {
                "expected_scene_target_word_count": round(expected_scene_target, 2),
                "low_scene_target_threshold": low_scene_threshold,
                "high_scene_target_threshold": high_scene_threshold,
                "scene_sum_min_threshold": scene_sum_min_threshold,
                "scene_sum_max_threshold": scene_sum_max_threshold,
            }
        )
        if target_sum < scene_sum_min_threshold:
            issues.append(
                ChapterOutlineReadinessIssue(
                    code="OUTLINE_SCENE_BUDGET_TOO_LOW",
                    severity="critical",
                    message="Scene word-count budget is far below the chapter target.",
                    path="scene_cards[*].target_word_count",
                    repair_hint=(
                        "Rebalance scene targets so their sum reaches at least "
                        "82% of the chapter target."
                    ),
                )
            )
        elif target_sum > scene_sum_max_threshold:
            issues.append(
                ChapterOutlineReadinessIssue(
                    code="OUTLINE_SCENE_BUDGET_TOO_HIGH",
                    severity="major",
                    message="Scene word-count budget is far above the chapter target.",
                    path="scene_cards[*].target_word_count",
                    repair_hint="Reduce scene targets or raise the chapter target before drafting.",
                )
            )
        for index, target in enumerate(scene_targets, start=1):
            if target <= 0:
                issues.append(
                    ChapterOutlineReadinessIssue(
                        code="OUTLINE_SCENE_TARGET_MISSING",
                        severity="critical",
                        message=f"Scene {index} is missing a positive word-count target.",
                        path=f"scene_cards[{index - 1}].target_word_count",
                        repair_hint="Set an explicit target_word_count for every scene card.",
                    )
                )
            elif target < low_scene_threshold:
                issues.append(
                    ChapterOutlineReadinessIssue(
                        code="OUTLINE_SCENE_TARGET_TOO_LOW",
                        severity="major",
                        message=f"Scene {index} target is too low for this chapter budget.",
                        path=f"scene_cards[{index - 1}].target_word_count",
                        repair_hint=(
                            "Expand the scene target or split the chapter budget "
                            "across fewer scenes."
                        ),
                    )
                )
            elif target > high_scene_threshold:
                issues.append(
                    ChapterOutlineReadinessIssue(
                        code="OUTLINE_SCENE_TARGET_TOO_HIGH",
                        severity="major",
                        message=f"Scene {index} target is too high for this chapter budget.",
                        path=f"scene_cards[{index - 1}].target_word_count",
                        repair_hint="Split this scene or rebalance the chapter budget.",
                    )
                )

    for index, scene in enumerate(scene_cards, start=1):
        metadata = _mapping_or_empty(_get(scene, "metadata_json") or _get(scene, "metadata"))
        residue = sorted(key for key in _AUTO_REPAIR_RESIDUE_KEYS if key in metadata)
        if residue:
            issues.append(
                ChapterOutlineReadinessIssue(
                    code="OUTLINE_STALE_AUTO_REPAIR_RESIDUE",
                    severity="critical",
                    message=(
                        f"Scene {index} still carries auto-repair residue: "
                        f"{', '.join(residue)}."
                    ),
                    path=f"scene_cards[{index - 1}].metadata",
                    repair_hint=(
                        "Normalize scene targets and remove stale auto-repair "
                        "metadata before rerunning."
                    ),
                )
            )

        methodology = _mapping_or_empty(metadata.get("methodology_contract"))
        missing_methodology = sorted(_METHODOLOGY_CONTRACT_KEYS - set(methodology))
        if missing_methodology:
            front_blocking = chapter_number <= 10
            issues.append(
                ChapterOutlineReadinessIssue(
                    code="OUTLINE_METHODOLOGY_CONTRACT_INCOMPLETE",
                    severity="critical" if front_blocking else "minor",
                    message=f"Scene {index} methodology contract is incomplete.",
                    path=f"scene_cards[{index - 1}].metadata.methodology_contract",
                    repair_hint=(
                        "Fill stakes, pressure_stack, focus_character, reveal_mode, "
                        "signature_image, and breakpoint."
                    ),
                    blocking=front_blocking,
                )
            )

        purpose_text = _textify(_get(scene, "purpose"))
        if chapter_number <= 10:
            entry_text = _textify(_get(scene, "entry_state") or _get(scene, "entry"))
            exit_text = _textify(_get(scene, "exit_state") or _get(scene, "exit"))
            hook_text = _textify(_get(scene, "hook_requirement") or _get(scene, "hook"))
            missing_scene_fields = [
                label
                for label, value in (
                    ("purpose", purpose_text),
                    ("entry_state", entry_text),
                    ("exit_state", exit_text),
                    ("hook_requirement", hook_text),
                )
                if not value.strip()
            ]
            if missing_scene_fields:
                issues.append(
                    ChapterOutlineReadinessIssue(
                        code="OUTLINE_SCENE_EXECUTION_CONTRACT_INCOMPLETE",
                        severity="critical",
                        message=(
                            f"Front-ten scene {index} lacks executable fields: "
                            f"{', '.join(missing_scene_fields)}."
                        ),
                        path=f"scene_cards[{index - 1}]",
                        repair_hint=(
                            "每个前十章场景必须写清读者进场看到什么、离场得到什么、"
                            "本场钩子是什么；否则正文只能自由发挥。"
                        ),
                    )
                )

        connector_count = len(
            re.findall(r"[，,；;、]|同时|然后|再|又|并且|以及", purpose_text)
        )
        if len(purpose_text) > 180 and connector_count >= 8:
            issues.append(
                ChapterOutlineReadinessIssue(
                    code="OUTLINE_SCENE_ACTION_OVERLOAD",
                    severity="minor",
                    message=f"Scene {index} purpose carries too many actions for one scene.",
                    path=f"scene_cards[{index - 1}].purpose",
                    repair_hint=(
                        "Reduce the scene to one pressure turn, one evidence turn, "
                        "and one exit hook."
                    ),
                    blocking=False,
                )
            )

    scene_outline_texts = [_scene_outline_text(scene) for scene in scene_cards]
    timeline_text = "\n".join(
        [
            _chapter_metadata_outline_text(chapter_metadata or {}),
            *scene_outline_texts,
        ]
    )
    forbidden_scan_text = "\n".join(
        [
            _chapter_metadata_story_text(chapter_metadata or {}),
            *scene_outline_texts,
        ]
    )
    if chapter_number <= 10:
        forbidden_terms = _chapter_forbidden_signal_terms(chapter_metadata or {})
        forbidden_hits = [
            term for term in forbidden_terms if term and term in forbidden_scan_text
        ]
        if forbidden_hits:
            issues.append(
                ChapterOutlineReadinessIssue(
                    code="OUTLINE_FORBIDDEN_SIGNAL_CONFLICT",
                    severity="critical",
                    message=(
                        "Outline story fields contain signals that the chapter "
                        "contract explicitly forbids."
                    ),
                    path="chapter_outline.object_signal_contract.forbidden_signals",
                    repair_hint=(
                        "删除或替换这些禁用物件/术语信号："
                        f"{'、'.join(dict.fromkeys(forbidden_hits[:8]))}。"
                    ),
                )
            )
        for index, scene in enumerate(scene_cards, start=1):
            scene_text = scene_outline_texts[index - 1] if index - 1 < len(scene_outline_texts) else ""
            scene_forbidden_hits = [
                term
                for term in _scene_forbidden_action_surface_terms(scene)
                if term and term in scene_text
            ]
            if scene_forbidden_hits:
                issues.append(
                    ChapterOutlineReadinessIssue(
                        code="OUTLINE_SCENE_FORBIDDEN_ACTION_CONFLICT",
                        severity="critical",
                        message=(
                            f"Scene {index} contract contains actions that the "
                            "scene card itself forbids."
                        ),
                        path=f"scene_cards[{index - 1}]",
                        repair_hint=(
                            "先修场景卡，再进入正文生成；删除这些冲突动作："
                            f"{'、'.join(dict.fromkeys(scene_forbidden_hits[:8]))}。"
                        ),
                    )
                )
    if chapter_number <= 3 and scene_cards:
        first_scene = scene_cards[0]
        first_scene_text = _scene_outline_text(first_scene)
        first_scene_surface_text = _scene_surface_text(first_scene)
        opening_text = "\n".join(
            [
                str(chapter_title or ""),
                _chapter_metadata_outline_text(chapter_metadata or {}),
                first_scene_text,
            ]
        )
        chapter_meta = _mapping_or_empty(chapter_metadata)
        if (
            _PHONE_OPENING_PATTERN.search(opening_text)
            and not _IN_PERSON_OPENING_PATTERN.search(first_scene_surface_text)
            and chapter_meta.get("opening_medium_approved") is not True
        ):
            issues.append(
                ChapterOutlineReadinessIssue(
                    code="OUTLINE_WEAK_MEDIATED_OPENING",
                    severity="major",
                    message="Golden-three opening is locked to phone/message mediation without an approved rationale.",
                    path="chapter_outline.opening_situation",
                    repair_hint=(
                        "Move the first scene into an immediate visible situation "
                        "(arrival, door, elevator, body/evidence, or face-to-face pressure), "
                        "or explicitly mark opening_medium_approved with a stronger rationale."
                    ),
                )
            )
    heat_hits = object_sensory_shortcut_hits(timeline_text)
    object_signal_contract = _mapping_or_empty(_mapping_or_empty(chapter_metadata).get("object_signal_contract"))
    if chapter_number <= 10 and heat_hits >= 3 and not object_signal_contract:
        issues.append(
            ChapterOutlineReadinessIssue(
                code="OUTLINE_OBJECT_SIGNAL_UNBOUNDED",
                severity="major",
                message="Outline repeatedly uses object heat as a signal without a stable signal contract.",
                path="chapter_outline.object_signal_contract",
                repair_hint=(
                    "Define what each object signal means, when it triggers, and what it cannot do; "
                    "replace repeated heat cues with distinct visual or tactile evidence."
                ),
            )
        )
    if (
        chapter_number <= 10
        and _LATE_NIGHT_DELIVERY_PATTERN.search(timeline_text)
        and not _IMPOSSIBLE_DELIVERY_MARKER.search(timeline_text)
    ):
        issues.append(
            ChapterOutlineReadinessIssue(
                code="OUTLINE_REAL_WORLD_PLAUSIBILITY_GAP",
                severity="critical",
                message="Outline uses late-night courier/delivery mechanics without marking the impossibility as evidence.",
                path="chapter_outline.real_world_plausibility",
                repair_hint=(
                    "删除不符合现实流程的夜间寄送/揽收，或把它明确设计成异常证据："
                    "伪造、自助柜补录、系统延迟、死后生成或非活人操作。"
                ),
            )
        )
    for index, scene in enumerate(scene_cards, start=1):
        metadata = _mapping_or_empty(_get(scene, "metadata_json") or _get(scene, "metadata"))
        methodology = _mapping_or_empty(metadata.get("methodology_contract"))
        hookish_text = "\n".join(
            [
                _textify(_get(scene, "hook_requirement") or _get(scene, "hook")),
                _textify(methodology.get("breakpoint") or methodology.get("cut_point")),
            ]
        )
        # Knowledge-boundary leaks are book-specific (they depend on the cast's
        # ordinary characters and the book's own specialist glossary) and are now
        # enforced genre-neutrally by the outline_commercial_judge "认知边界"
        # constraint. The deterministic check here hardcoded one detective book's
        # cast (王建业/小雨/…) + jargon (认账/镜债/…), leaking it into every project,
        # so it was removed.

    methodology_application_report = evaluate_methodology_application(
        chapter_number=chapter_number,
        chapter_metadata=chapter_metadata or {},
        scene_cards=scene_cards,
    )
    metrics["methodology_application_score"] = methodology_application_report.score
    metrics["methodology_application_count"] = methodology_application_report.application_count
    for finding in methodology_application_report.findings:
        issues.append(
            ChapterOutlineReadinessIssue(
                code=finding.code,
                severity=finding.severity,
                message=finding.message,
                path=finding.path,
                repair_hint=finding.repair_hint,
                blocking=finding.blocking,
            )
        )
    if _has_direct_kinship_lifespan_conflict(timeline_text):
        issues.append(
            ChapterOutlineReadinessIssue(
                code="OUTLINE_TIMELINE_LIFESPAN_CONFLICT",
                severity="critical",
                message=(
                    "Outline places a direct kinship role too close to a "
                    "three-hundred-year anchor."
                ),
                path="chapter_outline.timeline",
                repair_hint=(
                    "Separate ancient-covenant history from "
                    "father/grandparent-generation actions."
                ),
            )
        )
    for pair in _forbidden_timeline_pairs(chapter_metadata or {}):
        anchor = str(pair.get("anchor", "")).strip()
        subjects = [str(item).strip() for item in pair.get("subjects", []) if str(item).strip()]
        if anchor and subjects and _has_near_pair(timeline_text, anchor, subjects):
            issues.append(
                ChapterOutlineReadinessIssue(
                    code="OUTLINE_TIMELINE_ANCHOR_CONFLICT",
                    severity="critical",
                    message=f"Outline violates forbidden timeline anchor near {anchor}.",
                    path="chapter_outline.timeline",
                    repair_hint=(
                        "Rewrite the outline so the forbidden subject and anchor "
                        "are not causally coupled."
                    ),
                )
            )

    if any(issue.blocking for issue in issues):
        verdict = "blocked"
    elif issues:
        verdict = "warn_only"
    else:
        verdict = "pass"
    return ChapterOutlineReadinessReport(
        verdict=verdict,
        issues=tuple(issues),
        metrics=metrics,
    )


def chapter_scene_budget_sum_thresholds(chapter_target: int) -> tuple[int, int]:
    """Return the acceptable scene-budget sum band for a chapter.

    Chinese commercial chapters are intentionally elastic: a 2200-word target
    can still produce a usable 3000-3500 word chapter. This readiness gate
    catches impossible planning budgets without rejecting normal drafting
    variance.
    """

    if 2000 <= chapter_target <= 3500:
        return max(2000, int(chapter_target * 0.82)), 3500
    return int(chapter_target * 0.82), int(chapter_target * 1.20)


def _get(value: Mapping[str, Any] | Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _textify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(_textify(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "\n".join(_textify(item) for item in value)
    return str(value)


def _textify_story_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        chunks: list[str] = []
        for key, item in value.items():
            key_text = str(key)
            if key_text in _STORY_GUARDRAIL_KEYS or key_text.startswith("forbidden_"):
                continue
            chunks.append(_textify_story_value(item))
        return "\n".join(chunks)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "\n".join(_textify_story_value(item) for item in value)
    return str(value)


def _chapter_forbidden_signal_terms(metadata: Mapping[str, Any]) -> list[str]:
    object_signal_contract = _mapping_or_empty(metadata.get("object_signal_contract"))
    raw_terms = object_signal_contract.get("forbidden_signals")
    if not isinstance(raw_terms, Sequence) or isinstance(
        raw_terms, (str, bytes, bytearray)
    ):
        return []
    terms: list[str] = []
    for raw in raw_terms:
        text = str(raw or "").strip()
        if text:
            terms.append(text)
    return list(dict.fromkeys(terms))


def _scene_forbidden_action_surface_terms(scene: Mapping[str, Any] | Any) -> list[str]:
    raw_actions = _get(scene, "forbidden_actions")
    if not isinstance(raw_actions, Sequence) or isinstance(
        raw_actions, (str, bytes, bytearray)
    ):
        return []
    terms: list[str] = []
    for raw_action in raw_actions:
        action = str(raw_action or "").strip()
        if not action:
            continue
        terms.extend(_surface_terms_from_forbidden_action(action))
    return list(dict.fromkeys(term for term in terms if term))


_CONTACT_VERBS: tuple[str, ...] = (
    "按", "贴", "压", "放", "搁", "扣", "摁", "塞", "缠", "绑",
    "挂", "顶", "抵", "蒙", "盖", "捂", "划", "点", "碰", "触",
)
_CONTACT_VERB_CLASS = "[" + "".join(_CONTACT_VERBS) + "]"


def _surface_terms_from_forbidden_action(action: str) -> list[str]:
    # Genre-neutral: derive surface variants from the action text itself via verb-swap
    # generation + the generic token / 不得-clause extraction below. (Previously this
    # hardcoded one detective book's forbidden actions — 湿纸条/铜钱 + 小雨手腕 — which
    # were dead weight for every other book and a cross-book leak.)
    terms: list[str] = []
    # Verb-swap variants: an OBJECT placed-on a TARGET via a contact verb is the same
    # forbidden action regardless of which contact verb the prose chooses, so emit the
    # object × {contact verbs} × {在/到/""} × target cross-product for re-use detection.
    head = re.split(r"[；;。\n]", action.strip(), maxsplit=1)[0]
    head = re.sub(r"^(不得|禁止|不能|不要|严禁)\s*", "", head)
    head = re.sub(r"^把\s*", "", head).rstrip("，,。 ")
    obj_match = re.match(rf"([一-鿿]{{2,4}}?){_CONTACT_VERB_CLASS}", head)
    target_match = re.search(
        rf"{_CONTACT_VERB_CLASS}(?:在|到|住)?([一-鿿]{{2,5}}?)(?:影子|身上|上|里|中)?$",
        head,
    )
    if obj_match and target_match:
        obj = obj_match.group(1)
        target = target_match.group(1)
        if obj and target and obj != target:
            for verb in _CONTACT_VERBS:
                terms.append(f"{obj}{verb}{target}")
                terms.append(f"{obj}{verb}在{target}")
                terms.append(f"{obj}{verb}到{target}")
    if any(token in action for token in ("离场", "下楼", "坐电梯", "回店")):
        terms.extend(["离场", "下楼", "坐电梯", "回店"])
    if any(token in action for token in ("门吞", "拖进门", "合拢")):
        terms.extend(["门吞掉", "拖进门", "门合拢", "合拢"])

    for clause in re.findall(r"(?:不得|禁止|不能|不要)([^。；\n]+)", action):
        cleaned = re.sub(r"^(把|让|写|再次使用|重复)", "", clause.strip(" ，,、；。"))
        if 2 <= len(cleaned) <= 18:
            terms.append(cleaned)
    return terms


def _chapter_metadata_outline_text(metadata: Mapping[str, Any]) -> str:
    """Render only current planning fields, excluding historical audit snapshots."""
    selected = {
        key: metadata.get(key)
        for key in (
            "opening_situation",
            "main_conflict",
            "hook_description",
            "causal_contract",
            "event_cycle_contract",
            "methodology_contract",
            "world_rule_landing",
            "world_state_deltas",
            "object_signal_contract",
        )
        if metadata.get(key) not in (None, "", [], {})
    }
    return _textify(selected)


def _chapter_metadata_story_text(metadata: Mapping[str, Any]) -> str:
    """Render chapter story fields without self-hitting guardrail contracts."""

    selected = {
        key: metadata.get(key)
        for key in (
            "opening_situation",
            "main_conflict",
            "hook_description",
            "causal_contract",
            "event_cycle_contract",
            "methodology_contract",
            "world_rule_landing",
            "world_state_deltas",
        )
        if metadata.get(key) not in (None, "", [], {})
    }
    return _textify_story_value(selected)


def _scene_outline_text(scene: Mapping[str, Any] | Any) -> str:
    metadata = _mapping_or_empty(_get(scene, "metadata_json") or _get(scene, "metadata"))
    methodology = _mapping_or_empty(metadata.get("methodology_contract"))
    scene_contract = _mapping_or_empty(metadata.get("scene_contract"))
    selected = {
        "title": _get(scene, "title"),
        "scene_type": _get(scene, "scene_type"),
        "time_label": _get(scene, "time_label"),
        "participants": _get(scene, "participants"),
        "purpose": _get(scene, "purpose"),
        "entry_state": _get(scene, "entry_state") or _get(scene, "entry"),
        "exit_state": _get(scene, "exit_state") or _get(scene, "exit"),
        "hook_requirement": _get(scene, "hook_requirement") or _get(scene, "hook"),
        "sensory_anchors": _get(scene, "sensory_anchors") or _get(scene, "sensory"),
        "key_dialogue_beats": _get(scene, "key_dialogue_beats"),
        "methodology_contract": methodology,
        "scene_contract": scene_contract,
    }
    return _textify(
        {key: value for key, value in selected.items() if value not in (None, "", [], {})}
    )


def _scene_surface_text(scene: Mapping[str, Any] | Any) -> str:
    selected = {
        "title": _get(scene, "title"),
        "scene_type": _get(scene, "scene_type"),
        "time_label": _get(scene, "time_label"),
        "participants": _get(scene, "participants"),
        "purpose": _get(scene, "purpose"),
        "entry_state": _get(scene, "entry_state") or _get(scene, "entry"),
        "exit_state": _get(scene, "exit_state") or _get(scene, "exit"),
        "hook_requirement": _get(scene, "hook_requirement") or _get(scene, "hook"),
        "sensory_anchors": _get(scene, "sensory_anchors") or _get(scene, "sensory"),
        "key_dialogue_beats": _get(scene, "key_dialogue_beats"),
    }
    return _textify(
        {key: value for key, value in selected.items() if value not in (None, "", [], {})}
    )


def _has_direct_kinship_lifespan_conflict(text: str) -> bool:
    return bool(
        re.search(fr"{_DIRECT_KINSHIP_PATTERN}[^。！？；\n]{{0,24}}三百年前", text)
        or re.search(fr"三百年前[^。！？；\n]{{0,24}}{_DIRECT_KINSHIP_PATTERN}", text)
    )


def _has_near_pair(text: str, anchor: str, subjects: Sequence[str]) -> bool:
    anchor_pattern = re.escape(anchor)
    subject_pattern = "|".join(re.escape(subject) for subject in subjects)
    if not subject_pattern:
        return False
    return bool(
        re.search(fr"({subject_pattern})[^。！？；\n]{{0,32}}{anchor_pattern}", text)
        or re.search(fr"{anchor_pattern}[^。！？；\n]{{0,32}}({subject_pattern})", text)
    )


def _forbidden_timeline_pairs(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_pairs = metadata.get("forbidden_timeline_anchor_pairs")
    if not isinstance(raw_pairs, Sequence) or isinstance(raw_pairs, (str, bytes, bytearray)):
        return []
    pairs: list[dict[str, Any]] = []
    for raw_pair in raw_pairs:
        if isinstance(raw_pair, Mapping):
            subjects = raw_pair.get("subjects")
            if isinstance(subjects, str):
                subjects = [subjects]
            if isinstance(subjects, Sequence):
                pairs.append(
                    {
                        "anchor": raw_pair.get("anchor"),
                        "subjects": list(subjects),
                    }
                )
    return pairs
