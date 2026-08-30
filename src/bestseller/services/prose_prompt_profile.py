"""Single source of truth for how much instruction the prose writer receives.

Why this module exists
----------------------
A 2026-07-21 dose-response experiment (5 instruction levels x 3 generations,
blind-ranked in 3 independent rounds) measured what the chapter writer prompt
should actually contain:

===========================  ==========  ==========
instruction size             blind mean  rank
===========================  ==========  ==========
0 chars (material only)            5.75  worst, 3 rounds running
175-633 chars (core rules)      6.8-7.8  best band
20,988 chars (31 blocks)           3.50  worst overall
===========================  ==========  ==========

Two findings are solid. Rules are necessary — the no-rules arm ranked last in
every round, because the *story material itself* is procedural and nothing
pulls the prose back toward people. And 31 constraint blocks are catastrophic:
the writer stops writing a story and starts filling in a compliance form.

Within 175-633 chars the differences (0.92) were smaller than the variance
between repeat generations of the same config (1.13), so this module does not
pretend to know the exact optimum inside that band.

What ``lean`` drops, and why it is safe
---------------------------------------
Everything dropped here is either (a) an *acceptance* concern that already runs
as a post-generation gate, or (b) a *planning* concern that belongs upstream of
prose. Verified: ``chapter_predraft_quality_gate`` and
``methodology_application_gate`` read the generation bundle directly and never
compare against prompt text, so removing a block from the prompt does not
weaken any gate.

What ``lean`` deliberately KEEPS
--------------------------------
Canon-critical context stays. Timeline canon, character role limits, dialogue
voice, canon guardrails and the character safety block prevent factual
contradictions — they are story facts the writer cannot invent, not stylistic
instructions. Dropping them would trade AI-flavour for continuity errors.
"""

from __future__ import annotations

from typing import Literal

Profile = Literal["full", "lean"]

#: Gate-feedback fields inside the 【硬约束与门禁】 wrapper that ``lean`` drops.
#: The wrapper holds 14 fields; only these 7 are gate/market feedback. The rest
#: (timeline canon, character role, dialogue voice, canon guardrails, reader
#: contract, hype constraints, signature scene) are canon context and stay.
LEAN_DROPPED_CONSTRAINT_FIELDS: frozenset[str] = frozenset(
    {
        "chapter_length_block",
        "scene_coherence_block",
        "hook_echo_block",
        "exposition_density_block",
        "voice_dna_block",
        "chapter_market_constraints_block",
        "prior_persona_feedback_block",
    }
)

#: Named prompt sections ``lean`` omits. Kept as a declared set (rather than
#: scattered ``if`` statements at each append site) so the profile is auditable
#: in one place and testable without rendering a whole prompt.
LEAN_DROPPED_SECTIONS: frozenset[str] = frozenset(
    {
        "acceptance_contract",  # 写前验收契约 — 2578 chars; runs as a gate already
        "contract_must_hit",  # 必须显性兑现的章节契约 — same
        "word_count_rules",  # 字数与结构 289-char block — lean swaps in a
        #                      one-sentence band (same numbers the gate reads);
        #                      an invisible floor made the writer underproduce
        #                      ~21% and fail a contract it never saw
        "front_forbidden_terms",  # 前十章禁写与物件信号 — deterministic audit owns it
        "slop_blacklist",  # AI套话黑名单 — blacklist priming is falsified
        "opening_retention",  # 前十章留存硬规则 — compressed into core rules
        "closing_hook",  # 章末收尾钩子 — compressed into core rules
        "methodology_evidence",  # 方法论证据 — meta, not writing guidance
        "opening_scene_contract",  # 开场场景指导等 5 项 — compressed into core rules
        "quality_uplift",  # 全书重复词禁用清单 — observed to push the writer
        #                    into inventing fresh jargon to dodge banned words
    }
)


def prose_profile_drops_section(
    section: str,
    *,
    project_metadata: object | None = None,
    explicit: str | None = None,
) -> bool:
    """Whether the resolved profile omits ``section`` from the writer prompt.

    Callers outside the chapter-first builder need this because the decision
    "does this block reach the writer" must have one answer. The rewrite path
    used to append 【全书重复词禁用清单】 unconditionally while ``lean`` had
    deliberately dropped it from the first-draft prompt — and most shipped prose
    comes from the rewrite path, so the block the profile had excluded reached
    the writer anyway, through the back door (2026-08-04).
    """

    if section not in LEAN_DROPPED_SECTIONS:
        return False
    # Same resolution chain the chapter-first builder uses, global default
    # included — reading it is the whole point, since ``prose_prompt_profile``
    # is normally set there rather than per book.
    settings_default: str | None = None
    try:
        from bestseller.settings import load_settings

        settings_default = getattr(
            getattr(load_settings(), "pipeline", None), "prose_prompt_profile", None
        )
    except Exception:  # noqa: BLE001 - an unreadable setting must not change the prompt
        settings_default = None
    profile = resolve_prose_prompt_profile(
        explicit=explicit,
        project_metadata=project_metadata,
        settings_default=settings_default,
    )
    return not section_enabled(section, profile)


def resolve_prose_prompt_profile(
    *,
    explicit: str | None = None,
    project_metadata: object | None = None,
    settings_default: str | None = None,
) -> Profile:
    """Resolve the profile: explicit > per-book metadata > global setting.

    Mirrors ``_chapter_first_requested``'s precedence so a book's prose profile
    and its generation unit are configured the same way.
    """

    for candidate in (
        explicit,
        _metadata_profile(project_metadata),
        settings_default,
    ):
        normalized = str(candidate or "").strip().lower()
        if normalized in {"lean", "minimal"}:
            return "lean"
        if normalized in {"full", "legacy"}:
            return "full"
    return "full"


def _metadata_profile(project_metadata: object | None) -> str | None:
    if not isinstance(project_metadata, dict):
        return None
    value = project_metadata.get("prose_prompt_profile")
    return value if isinstance(value, str) else None


def section_enabled(section: str, profile: Profile) -> bool:
    """Whether a named prompt section is injected under ``profile``."""

    if profile != "lean":
        return True
    return section not in LEAN_DROPPED_SECTIONS


def constraint_field_enabled(field: str, profile: Profile) -> bool:
    """Whether a 【硬约束与门禁】 sub-field is injected under ``profile``."""

    if profile != "lean":
        return True
    return field not in LEAN_DROPPED_CONSTRAINT_FIELDS


__all__ = [
    "LEAN_DROPPED_CONSTRAINT_FIELDS",
    "LEAN_DROPPED_SECTIONS",
    "Profile",
    "constraint_field_enabled",
    "resolve_prose_prompt_profile",
    "section_enabled",
]
