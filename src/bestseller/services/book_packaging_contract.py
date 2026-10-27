"""Immutable, target-aware title and introduction packaging contract.

Packaging is a release input, not a presentation convenience.  This module is
deliberately persistence-agnostic so API, workers and exporters can all use the
same hash and active-variant rules before their database adapter is connected.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_hash(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


class IntroVariant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    variant_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    promise_refs: tuple[str, ...] = ()

    @property
    def content_hash(self) -> str:
        return canonical_hash(
            {"variant_id": self.variant_id, "text": self.text, "promise_refs": self.promise_refs}
        )


class PackagingSurface(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_platform: str = Field(min_length=1)
    listing_surface: str = Field(min_length=1)
    active_intro_variant_id: str = Field(min_length=1)
    active_intro_variant_hash: str = Field(min_length=1)
    title_register: str = "web_novel"


class BookPackagingContractModel(BaseModel):
    """A frozen revision; changing any field creates a superseding revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "title-intro-contract.v1"
    project_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    title: str = Field(min_length=2, max_length=80)
    # Optional for v1 fixtures/imports; mandatory for newly promoted creation
    # contracts.  The active listing must use the approved hook, never a stale
    # metadata logline.
    logline: str | None = Field(default=None, min_length=1, max_length=240)
    title_promise: str = Field(min_length=1)
    protagonist: str = Field(min_length=1)
    situation: str = Field(min_length=1)
    action: str = Field(min_length=1)
    result: str = Field(min_length=1)
    genre: str = Field(min_length=1)
    tags: tuple[str, ...] = ()
    effect_skills: tuple[str, ...] = ()
    architecture_refs: tuple[str, ...] = ()
    intro_variants: tuple[IntroVariant, ...] = ()
    surfaces: tuple[PackagingSurface, ...] = ()
    cover_hash: str | None = None
    status: str = "candidate"
    supersedes_contract_hash: str | None = None
    invalidates_evidence: bool = True

    @property
    def contract_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json", exclude={"contract_hash"}))

    @property
    def canonical_hash(self) -> str:
        """Compatibility spelling used by artifact and evidence adapters."""
        return self.contract_hash

    def to_artifact(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["contract_hash"] = self.contract_hash
        return payload

    def active_variant(self, target_platform: str, listing_surface: str) -> IntroVariant:
        surface = next(
            (
                item
                for item in self.surfaces
                if item.target_platform == target_platform
                and item.listing_surface == listing_surface
            ),
            None,
        )
        if surface is None:
            raise ValueError("ACTIVE_INTRO_VARIANT_MISSING")
        variant = next(
            (
                item
                for item in self.intro_variants
                if item.variant_id == surface.active_intro_variant_id
            ),
            None,
        )
        if variant is None or variant.content_hash != surface.active_intro_variant_hash:
            raise ValueError("PACKAGING_VARIANT_HASH_MISMATCH")
        return variant

    def active_payload(self, target_platform: str, listing_surface: str) -> dict[str, Any]:
        variant = self.active_variant(target_platform, listing_surface)
        surface = next(
            item
            for item in self.surfaces
            if item.target_platform == target_platform and item.listing_surface == listing_surface
        )
        payload = {
            "contract_hash": self.contract_hash,
            "project_id": self.project_id,
            "revision": self.revision,
            "title": self.title,
            "logline": self.logline,
            "title_promise": self.title_promise,
            "target_platform": target_platform,
            "listing_surface": listing_surface,
            "active_intro_variant_id": variant.variant_id,
            "active_intro_variant_hash": variant.content_hash,
            "active_intro_text": variant.text,
            "title_register": surface.title_register,
            "tags": list(self.tags),
            "effect_skills": list(self.effect_skills),
            "architecture_refs": list(self.architecture_refs),
            "cover_hash": self.cover_hash,
        }
        payload["active_payload_hash"] = canonical_hash(payload)
        return payload


_PLACEHOLDER = re.compile(r"^(?:新书|未命名|测试|test|untitled|project|小说)[\s_\-0-9]*$", re.I)
_TAG_WORDS = ("异世界", "异世", "穿越", "系统", "升级", "经营", "金手指")


def validate_packaging_contract(contract: BookPackagingContractModel) -> tuple[str, ...]:
    errors: list[str] = []
    if _PLACEHOLDER.fullmatch(contract.title.strip()):
        errors.append("TITLE_PLACEHOLDER_OR_INTERNAL_NAME")
    if sum(word in contract.title for word in _TAG_WORDS) >= 3:
        errors.append("TITLE_TAG_STACK")
    if not contract.intro_variants:
        errors.append("ACTIVE_INTRO_VARIANT_MISSING")
    for surface in contract.surfaces:
        try:
            contract.active_variant(surface.target_platform, surface.listing_surface)
        except ValueError as exc:
            errors.append(str(exc))
    if not contract.surfaces and contract.intro_variants:
        errors.append("ACTIVE_INTRO_VARIANT_MISSING")
    if not contract.architecture_refs:
        errors.append("PACKAGING_CONTRACT_MISMATCH")
    return tuple(dict.fromkeys(errors))


def build_book_packaging_contract(
    *,
    project_id: str,
    title: str,
    title_promise: str,
    logline: str | None = None,
    protagonist: str,
    situation: str,
    action: str,
    result: str,
    genre: str,
    tags: Iterable[object] = (),
    effect_skills: Iterable[object] = (),
    architecture_refs: Iterable[object] = (),
    intro_variants: Iterable[object] = (),
    surfaces: Iterable[object] = (),
    cover_hash: str | None = None,
    revision: int = 1,
    supersedes_contract_hash: str | None = None,
) -> BookPackagingContractModel:
    contract = BookPackagingContractModel(
        project_id=project_id,
        revision=revision,
        title=title,
        title_promise=title_promise,
        logline=logline,
        protagonist=protagonist,
        situation=situation,
        action=action,
        result=result,
        genre=genre,
        tags=tuple(str(x) for x in tags if str(x).strip()),
        effect_skills=tuple(str(x) for x in effect_skills if str(x).strip()),
        architecture_refs=tuple(str(x) for x in architecture_refs if str(x).strip()),
        intro_variants=tuple(IntroVariant.model_validate(x) for x in intro_variants),
        surfaces=tuple(PackagingSurface.model_validate(x) for x in surfaces),
        cover_hash=cover_hash,
        supersedes_contract_hash=supersedes_contract_hash,
    )
    errors = validate_packaging_contract(contract)
    if errors:
        raise ValueError(",".join(errors))
    return contract


def get_active_contract(
    contract: BookPackagingContractModel | Mapping[str, Any],
    target_platform: str,
    listing_surface: str,
) -> dict[str, Any]:
    parsed = (
        contract
        if isinstance(contract, BookPackagingContractModel)
        else BookPackagingContractModel.model_validate(contract)
    )
    return parsed.active_payload(target_platform, listing_surface)


def supersede_contract_revision(
    previous: BookPackagingContractModel,
    **changes: Any,  # noqa: ANN401
) -> BookPackagingContractModel:
    payload = previous.model_dump(mode="python")
    payload.update(changes)
    payload["revision"] = previous.revision + 1
    payload["supersedes_contract_hash"] = previous.contract_hash
    # A revision cannot silently carry an old active selector after changing variants.
    if "intro_variants" in changes or "surfaces" in changes:
        payload["invalidates_evidence"] = True
    return BookPackagingContractModel.model_validate(payload)


__all__ = [
    "BookPackagingContractModel",
    "IntroVariant",
    "PackagingSurface",
    "build_book_packaging_contract",
    "canonical_hash",
    "get_active_contract",
    "supersede_contract_revision",
    "validate_packaging_contract",
]
