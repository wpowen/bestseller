"""Project integration for the immutable book-design snapshot.

The creation boundary is the only place allowed to choose the protagonist,
tone, and whole-book budget.  Downstream assets may add detail, but they must
carry the snapshot lineage and may not silently replace those decisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from bestseller.domain.book_design_snapshot import (
    BookDesignSnapshot,
    build_book_design_snapshot,
)


@dataclass(frozen=True, slots=True)
class BookDesignIssue:
    code: str
    asset: str
    expected: str
    actual: str


@dataclass(frozen=True, slots=True)
class BookDesignValidationReport:
    snapshot_id: str
    issues: tuple[BookDesignIssue, ...]
    #: Issues that are real drift but must not pause production on their own.
    advisory_codes: frozenset[str] = frozenset()

    @property
    def passed(self) -> bool:
        return not self.issues

    @property
    def blocking_issues(self) -> tuple[BookDesignIssue, ...]:
        """Issues severe enough to stop the book.

        Detection and consequence are deliberately separated. A protagonist
        name the pipeline invented for itself (no user choice anywhere) can
        legitimately differ between artifacts — the champion concept carries no
        name and several independent steps each mint one. That IS drift worth
        reporting and repairing, but pausing a finished conception over it
        killed 《仇人膝上养帝王》 on 2026-07-25 (snapshot 李玄 vs every planning
        artifact 姬衡). An explicitly chosen name still blocks: overwriting a
        user's decision is a different thing entirely.
        """

        return tuple(
            item for item in self.issues if item.code not in self.advisory_codes
        )

    @property
    def blocks_production(self) -> bool:
        return bool(self.blocking_issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "passed": self.passed,
            "blocks_production": self.blocks_production,
            "advisory_codes": sorted(self.advisory_codes),
            "issues": [
                {
                    "code": item.code,
                    "asset": item.asset,
                    "expected": item.expected,
                    "actual": item.actual,
                    "advisory": item.code in self.advisory_codes,
                }
                for item in self.issues
            ],
        }


_CJK_NAME_RE = re.compile(r"^([\u4e00-\u9fff]{2,4})(?=[，,、：:\s]|$)")
_NON_NAME_PREFIXES = (
    "一个",
    "一名",
    "一位",
    "主角",
    "少年",
    "少女",
    "青年",
    "女孩",
    "男孩",
    "男人",
    "女人",
)
# Role/title words that sit immediately before a name in Chinese premises
# (废太子姬衡 / 少年剑修陆沉). Peeled off longest-first so 废太子 wins over 太子.
_NAME_LEADING_TITLES = (
    "废太子", "皇太子", "太子", "废皇子", "皇子", "公主", "郡主", "王爷", "世子",
    "矿场贱民", "贱民",
    "少年剑修", "剑修", "剑客", "书生", "画师", "捕快", "仵作", "医师", "药师",
    "审计员", "审计师", "工程监理", "监理", "会计师", "记者", "律师", "警察",
    "少年", "少女", "青年", "男人", "女人", "落魄", "前世", "重生",
)
# A "name" containing these is a phrase, not a person.
_NAME_DISQUALIFYING_TOKENS = (
    "的", "了", "着", "在", "是", "被", "把", "和", "与", "他", "她", "它",
)
# Longest-first so 废太子 beats 太子 and 少年剑修 beats 少年.
_TITLED_NAME_RE = re.compile(
    "(?:"
    + "|".join(
        re.escape(t)
        for t in sorted(_NAME_LEADING_TITLES, key=len, reverse=True)
    )
    + r")([一-鿿]{2,4})(?=[，,。；;：:\s!?！？]|$|天生|醒来|发现|得到|拥有|[在被把与和了是的])"
)
_LOW_TONE_TOKENS = ("轻松", "幽默", "明快", "温暖", "治愈", "喜剧", "cozy", "light")
_HIGH_TONE_TOKENS = ("高压", "冷硬", "暗黑", "压抑", "沉重", "grim", "dark")
_GENERIC_PROTAGONIST_NAMES = frozenset(
    {
        "主角",
        "主人公",
        "男主",
        "女主",
        "少年",
        "少女",
        "protagonist",
        "hero",
        "unknown protagonist",
    }
)
_GENRE_INTENT_AUTHORITY_FIELDS = (
    "channel_key",
    "genre_key",
    "sub_genre_key",
    "category_key",
    "prompt_pack_key",
    "audience_orientation",
    "narrative_scale",
    "tone_preference",
    "allowed_modernity",
    "user_tags",
    "explicit_enhancers",
)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_generic_protagonist_name(value: object) -> bool:
    return _text(value).casefold() in _GENERIC_PROTAGONIST_NAMES


def _protagonist_name_from_text(value: object) -> str:
    """Pull the protagonist's name out of a premise sentence.

    Two shapes must both work, because the pipeline writes both:

    * ``李玄，二十岁的废太子…``            — name first (the original regex)
    * ``前世…登基的废太子姬衡，醒来…``      — name after a descriptive clause

    Only the first was matched until 2026-07-25, so a real premise yielded ""
    and the design snapshot silently fell back to ``story_spine``. That book
    (《仇人膝上养帝王》) then died at the consistency gate: snapshot 李玄 vs
    every planning artifact 姬衡.

    Fails CLOSED on purpose — a wrong name is worse than no name here, since
    whatever comes out becomes the snapshot the whole book is judged against.
    So the clause-scan only accepts a name that sits immediately before a
    clause boundary and is not a generic role word.
    """

    text = _text(value)
    if not text:
        return ""
    match = _CJK_NAME_RE.match(text)
    if match:
        candidate = match.group(1)
        if not candidate.startswith(_NON_NAME_PREFIXES):
            return candidate

    # Name after a descriptive clause. Anchored on a role/title word rather
    # than on position, because Chinese has no word boundaries and a bare
    # "last 2-4 characters" scan cannot tell 姬衡 from 睁开眼. Bounded to the
    # first clause so a second character's name can never win.
    first_clause = re.split(r"[，,。；;：:!?！？\n]", text, maxsplit=1)[0]
    titled = _TITLED_NAME_RE.search(first_clause)
    if titled:
        candidate = titled.group(1)
        if (
            not candidate.startswith(_NON_NAME_PREFIXES)
            and not _is_generic_protagonist_name(candidate)
            and not any(token in candidate for token in _NAME_DISQUALIFYING_TOKENS)
        ):
            return candidate

    latin = re.match(r"^([A-Za-z][A-Za-z' -]{1,40})(?=[,;:]|\s+(?:is|was)\b)", text)
    return latin.group(1).strip() if latin else ""


def _asset_protagonist(payload: object) -> str:
    asset = _mapping(payload)
    return _protagonist_name_from_text(
        asset.get("who")
        or asset.get("protagonist")
        or asset.get("protagonist_name")
    )


#: Metadata keys that mean "a human (or an explicit upstream contract) named
#: this character on purpose". Anything else is pipeline inference.
_EXPLICIT_PROTAGONIST_KEYS = (
    "creation_protagonist_name",
    "protagonist_name",
    "canonical_protagonist_name",
)


def _has_explicit_protagonist_choice(metadata: Mapping[str, Any]) -> bool:
    """True when the protagonist name was CHOSEN, not inferred from prose."""

    return any(_text(metadata.get(key)) for key in _EXPLICIT_PROTAGONIST_KEYS)


def extract_creation_protagonist_name(metadata: Mapping[str, Any]) -> str:
    """Resolve the protagonist from the approved conception before cast design."""

    for key in (
        "creation_protagonist_name",
        "protagonist_name",
        "canonical_protagonist_name",
    ):
        explicit = _text(metadata.get(key))
        if explicit:
            return explicit

    premise = _text(metadata.get("premise"))
    premise_name = _protagonist_name_from_text(premise)
    if premise_name:
        return premise_name

    for spine in (
        _mapping(metadata.get("story_spine")),
        _mapping(_mapping(metadata.get("concept_contract")).get("story_spine")),
    ):
        name = _asset_protagonist(spine)
        if name:
            return name
    return ""


def _authoritative_creation_protagonist_name(metadata: Mapping[str, Any]) -> str:
    """Return only identity sources allowed to supersede a stale snapshot.

    Generated story-spine and manifest names are deliberately excluded: they
    are downstream interpretations and must never rewrite an already locked
    creation decision.  Explicit creation fields and a concrete name in the
    original premise are creation-boundary evidence.
    """

    for key in _EXPLICIT_PROTAGONIST_KEYS:
        explicit = _text(metadata.get(key))
        if explicit:
            return explicit
    return _protagonist_name_from_text(metadata.get("premise"))


def _manifest_protagonist(metadata: Mapping[str, Any]) -> str:
    manifest = metadata.get("identity_manifest")
    if not isinstance(manifest, Sequence) or isinstance(manifest, (str, bytes)):
        return ""
    rows = [row for row in manifest if isinstance(row, Mapping)]
    row = next(
        (
            item
            for item in rows
            if _text(item.get("role") or item.get("entity_role")).lower()
            in {"protagonist", "main_character", "hero", "主角"}
        ),
        rows[0] if rows else {},
    )
    return _text(row.get("name") or row.get("canonical_name"))


def _writing_tone(metadata: Mapping[str, Any]) -> str:
    profile = _mapping(metadata.get("writing_profile"))
    style = _mapping(profile.get("style"))
    raw = style.get("tone_keywords") or profile.get("tone_keywords") or ()
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Sequence):
        return "、".join(_text(item) for item in raw if _text(item))
    return ""


def _creation_intent_payload(project: Any, metadata: Mapping[str, Any]) -> dict[str, Any]:
    intent = _mapping(metadata.get("genre_intent_contract"))
    tone_preference = _text(intent.get("tone_preference"))
    stored_contract = _mapping(metadata.get("creation_intent_contract"))
    if stored_contract:
        payload = dict(stored_contract)
        if intent:
            payload["genre_intent"] = dict(intent)
        payload["chapter_count"] = int(
            getattr(project, "target_chapters", 0)
            or payload.get("chapter_count")
            or 1
        )
        if tone_preference:
            payload["tone_preference"] = tone_preference
        return payload
    return {
        "genre_intent": dict(intent)
        if intent
        else {
            "genre": _text(getattr(project, "genre", "general-fiction")),
            "channel": "general",
        },
        "chapter_count": int(getattr(project, "target_chapters", 0) or 1),
        "tone_preference": tone_preference or None,
    }


def ensure_project_book_design_snapshot(
    project: Any,
    *,
    protagonist_name: str | None = None,
    force_rebuild: bool = False,
) -> BookDesignSnapshot:
    metadata = dict(getattr(project, "metadata_json", None) or {})
    existing = metadata.get("book_design_snapshot")
    if isinstance(existing, Mapping) and not force_rebuild:
        snapshot = BookDesignSnapshot.model_validate(existing)
        if snapshot.source_hash != snapshot.canonical_hash():
            raise ValueError("book design snapshot hash mismatch")
        if snapshot.snapshot_id != snapshot.canonical_hash()[:16]:
            raise ValueError("book design snapshot id mismatch")
        stored_id = _text(metadata.get("book_design_snapshot_id"))
        stored_hash = _text(metadata.get("book_design_snapshot_hash"))
        if stored_id and stored_id != snapshot.snapshot_id:
            raise ValueError("book design snapshot metadata id mismatch")
        if stored_hash and stored_hash != snapshot.source_hash:
            raise ValueError("book design snapshot metadata hash mismatch")
        authoritative_name = _authoritative_creation_protagonist_name(metadata)
        if (
            authoritative_name
            and not _is_generic_protagonist_name(authoritative_name)
            and authoritative_name != snapshot.protagonist.name
        ):
            superseded = metadata.get("book_design_snapshot_superseded")
            history = (
                [dict(item) for item in superseded if isinstance(item, Mapping)]
                if isinstance(superseded, Sequence)
                and not isinstance(superseded, (str, bytes))
                else []
            )
            history.append(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "protagonist_name": snapshot.protagonist.name,
                    "reason": "authoritative_creation_identity_drift",
                    "replacement_name": authoritative_name,
                }
            )
            metadata["book_design_snapshot_superseded"] = history[-5:]
            metadata["book_design_snapshot_repair_reason"] = (
                "authoritative_creation_identity_drift"
            )
            project.metadata_json = metadata
            return ensure_project_book_design_snapshot(
                project,
                protagonist_name=authoritative_name,
                force_rebuild=True,
            )
        return snapshot

    intent = _mapping(metadata.get("genre_intent_contract"))
    tone_preference = _text(intent.get("tone_preference"))
    creation_intent = _creation_intent_payload(project, metadata)
    target_chapters = int(getattr(project, "target_chapters", 0) or 0)
    target_words = int(getattr(project, "target_word_count", 0) or 0)
    if target_chapters <= 0 or target_words <= 0:
        raise ValueError(
            "book design snapshot requires positive target_chapters and target_word_count"
        )
    canonical_name = (
        _text(protagonist_name)
        or extract_creation_protagonist_name(metadata)
        or _manifest_protagonist(metadata)
    )
    if not canonical_name or _is_generic_protagonist_name(canonical_name):
        raise ValueError(
            "book design snapshot requires an explicit, named creation protagonist"
        )
    entities: list[dict[str, Any]] = []
    legacy_entities = metadata.get("entities")
    registry = metadata.get("entity_registry")
    if legacy_entities is None and isinstance(registry, Mapping):
        legacy_entities = registry.get("entities")
    if isinstance(legacy_entities, Sequence) and not isinstance(
        legacy_entities, (str, bytes)
    ):
        entities.extend(dict(row) for row in legacy_entities if isinstance(row, Mapping))
    manifest = metadata.get("identity_manifest")
    if isinstance(manifest, Sequence) and not isinstance(manifest, (str, bytes)):
        for row in manifest:
            if not isinstance(row, Mapping):
                continue
            name = _text(row.get("name") or row.get("canonical_name"))
            if name:
                entity = (
                    {
                        "entity_type": "character",
                        "canonical_name": name,
                        "aliases": list(row.get("aliases") or ()),
                        "metadata": {"role": row.get("role")},
                    }
                )
                if not any(
                    _text(item.get("canonical_name") or item.get("name")) == name
                    and _text(item.get("entity_type") or item.get("type") or "entity")
                    == "character"
                    for item in entities
                ):
                    entities.append(entity)
    concept = _mapping(metadata.get("concept_contract"))
    spine = _mapping(metadata.get("story_spine"))
    snapshot = build_book_design_snapshot(
        creation_intent=creation_intent,
        protagonist=canonical_name,
        tone=tone_preference or _writing_tone(metadata) or "未指定",
        target_words=target_words,
        chapter_count=target_chapters,
        entities=entities,
        reader_promise=_text(
            concept.get("core_reader_promise")
            or spine.get("core_reader_promise")
            or spine.get("question")
        )
        or None,
        core_story_engine=_text(
            concept.get("unit_engine_ref") or spine.get("unit_engine_ref")
        )
        or None,
    )
    metadata.update(
        {
            "book_design_snapshot": snapshot.model_dump(mode="json"),
            "book_design_snapshot_id": snapshot.snapshot_id,
            "book_design_snapshot_hash": snapshot.source_hash,
            "book_design_snapshot_status": "locked",
        }
    )
    project.metadata_json = metadata
    return snapshot


def validate_project_book_design(project: Any) -> BookDesignValidationReport:
    metadata = _mapping(getattr(project, "metadata_json", None))
    snapshot = ensure_project_book_design_snapshot(project)
    issues: list[BookDesignIssue] = []
    expected_name = snapshot.protagonist.name
    current_intent = _mapping(metadata.get("genre_intent_contract"))
    locked_intent = snapshot.creation_intent.genre_intent.model_dump(mode="json")
    current_chapters = int(getattr(project, "target_chapters", 0) or 0)
    current_words = int(getattr(project, "target_word_count", 0) or 0)
    try:
        normalized_current_intent = build_book_design_snapshot(
            creation_intent=_creation_intent_payload(project, metadata),
            protagonist=snapshot.protagonist,
            tone=snapshot.tone,
            target_words=max(0, current_words),
            chapter_count=max(1, current_chapters),
        ).creation_intent.genre_intent.model_dump(mode="json")
    except (TypeError, ValueError):
        normalized_current_intent = dict(current_intent)
    intent_drift = {
        field: {
            "expected": locked_intent.get(field),
            "actual": normalized_current_intent.get(field),
        }
        for field in _GENRE_INTENT_AUTHORITY_FIELDS
        if normalized_current_intent.get(field) != locked_intent.get(field)
    }
    if intent_drift:
        issues.append(
            BookDesignIssue(
                "creation_intent_mismatch",
                "genre_intent_contract",
                str({key: value["expected"] for key, value in intent_drift.items()}),
                str({key: value["actual"] for key, value in intent_drift.items()}),
            )
        )
    if current_chapters != snapshot.chapter_budget.total_chapters:
        issues.append(
            BookDesignIssue(
                "chapter_budget_mismatch",
                "project.target_chapters",
                str(snapshot.chapter_budget.total_chapters),
                str(current_chapters),
            )
        )
    if current_words != snapshot.word_budget.total_words:
        issues.append(
            BookDesignIssue(
                "word_budget_mismatch",
                "project.target_word_count",
                str(snapshot.word_budget.total_words),
                str(current_words),
            )
        )
    concept = _mapping(metadata.get("concept_contract"))
    for asset, actual in (
        ("story_spine", _asset_protagonist(metadata.get("story_spine"))),
        (
            "concept_contract.story_spine",
            _asset_protagonist(concept.get("story_spine")),
        ),
        ("hook_card", _asset_protagonist(metadata.get("hook_card"))),
        (
            "concept_contract.hook_card",
            _asset_protagonist(concept.get("hook_card")),
        ),
        ("identity_manifest", _manifest_protagonist(metadata)),
    ):
        if actual and actual != expected_name:
            issues.append(
                BookDesignIssue(
                    "protagonist_identity_mismatch",
                    asset,
                    expected_name,
                    actual,
                )
            )
    preference = _text(
        _mapping(metadata.get("genre_intent_contract")).get("tone_preference")
    ).lower()
    writing_tone = _writing_tone(metadata)
    if preference in {"light", "cozy", "comedy", "healing"}:
        has_low = any(token in writing_tone.lower() for token in _LOW_TONE_TOKENS)
        has_high = any(token in writing_tone.lower() for token in _HIGH_TONE_TOKENS)
        if writing_tone and has_high and not has_low:
            issues.append(
                BookDesignIssue(
                    "tone_mismatch",
                    "writing_profile",
                    snapshot.tone,
                    writing_tone,
                )
            )
    # A name-only disagreement between artifacts the pipeline named itself is
    # reported but does not pause the book; an explicitly chosen protagonist
    # still blocks. See BookDesignValidationReport.blocking_issues.
    advisory: frozenset[str] = (
        frozenset()
        if _has_explicit_protagonist_choice(metadata)
        else frozenset({"protagonist_identity_mismatch"})
    )
    return BookDesignValidationReport(
        snapshot.snapshot_id, tuple(issues), advisory_codes=advisory
    )


def planning_snapshot_lineage(project: Any) -> dict[str, str]:
    metadata = _mapping(getattr(project, "metadata_json", None))
    # Imported legacy artifacts predate the creation snapshot contract. Keep
    # them readable without fabricating an identity from downstream material;
    # every new-book path carries ``creation_intent_contract`` and therefore
    # still fails closed if its mandatory snapshot cannot be built or verified.
    if not isinstance(metadata.get("book_design_snapshot"), Mapping) and not isinstance(
        metadata.get("creation_intent_contract"), Mapping
    ):
        return {}
    # The premise is an input *to* the immutable creation snapshot. New-book
    # planning imports that source before the naming LLM resolves a canonical
    # protagonist, so forcing snapshot construction here creates a circular
    # dependency. Only this pre-snapshot boundary may be pending; every later
    # artifact still requires and carries the completed immutable lineage.
    if not isinstance(metadata.get("book_design_snapshot"), Mapping) and not (
        extract_creation_protagonist_name(metadata) or _manifest_protagonist(metadata)
    ):
        return {"source_snapshot_status": "pending_creation_identity"}
    snapshot = ensure_project_book_design_snapshot(project)
    return {
        "source_snapshot_id": snapshot.snapshot_id,
        "source_snapshot_hash": snapshot.source_hash,
        "source_snapshot_version": snapshot.schema_version,
    }


__all__ = [
    "BookDesignIssue",
    "BookDesignValidationReport",
    "ensure_project_book_design_snapshot",
    "extract_creation_protagonist_name",
    "planning_snapshot_lineage",
    "validate_project_book_design",
]
