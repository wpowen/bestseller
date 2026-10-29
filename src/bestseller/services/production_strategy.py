"""Production breadth and downstream headroom, separate from story cost style.

An absent profile is a legacy envelope and preserves its exact paid inputs.
New creation entrypoints explicitly freeze complete/research in user_hints.
This policy limits exploration; it never lowers a quality floor or grants spend.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
from pathlib import Path

_HEADROOM = ContextVar("production_downstream_headroom", default=None)
_PROFILE = ContextVar("production_profile", default=None)
PROFILES = frozenset({"complete", "research"})
_RUN2_POLICY_PATH = Path(__file__).resolve().parents[3] / "config/run2_new_book_template_policy.json"
_RUN2_NEW_BOOK_GENERATION_ALIASES = frozenset({
    "conception_finalize_reader_first_v1",
    "conception_finalize_reader_first_v1_repair",
    "conception_story_spine_polish_reader_first_v1",
    "conception_story_spine_polish_reader_first_v1_repair",
    "conception_reader_first_repair_v1",
    "fanqie_reader_first_field_roles",
    "planner_reader_first_full_outline_commercial_surgical_repair",
})


def resolve_run2_new_book_template_policy(request, role):
    """Return the finite audit expectation, not a claim about the current wire.

    Generic entries are audit-only. Unknown names/roles stay outside this
    table; this resolver grants no source, dispatch or paid authority.
    """
    policy = json.loads(_RUN2_POLICY_PATH.read_text(encoding="utf-8"))
    if (not isinstance(policy, dict)
            or policy.get("schema") != "run2-new-book-template-policy.v1"
            or not isinstance(policy.get("templates"), dict)):
        raise ValueError("run2_new_book_template_policy:invalid_config")
    entry = policy["templates"].get(request.prompt_template)
    if entry is None:
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("roles"), dict):
        raise ValueError("run2_new_book_template_policy:invalid_config")
    rule = entry["roles"].get(request.logical_role)
    if rule is None:
        return None
    if (not isinstance(rule, dict)
            or rule.get("effort") not in ("medium", "bounded_generation", "bounded_review")
            or rule.get("cap") != "effective_request"
            or not isinstance(rule.get("reason"), str) or not rule["reason"].strip()):
        raise ValueError("run2_new_book_template_policy:invalid_config")
    from bestseller.services.llm import _effective_request_max_tokens

    cap = _effective_request_max_tokens(role, request)
    effort = rule["effort"]
    if effort != "medium":
        narrowed = cap <= 4096 or cap < role.max_tokens
        effort = "low" if narrowed else "max" if effort == "bounded_review" else "medium"
    return {"effort": role.reasoning_effort or effort,
            "max_completion_tokens": cap, "reason": rule["reason"]}


def normalize_profile(value, *, default="complete"):
    profile = str(value or default).strip().lower()
    if profile not in PROFILES:
        raise ValueError("production_profile must be complete or research")
    return profile


def profile_from_hints(hints):
    return normalize_profile((hints or {}).get("production_profile"), default="research")


def downstream_headroom(chapters):
    """Planning envelope, not a claim that quality success is guaranteed.

    Reserve 10 foundation/outline, six draft/review/knowledge operations per
    chapter and four whole-book/finalization operations. Repairs beyond this
    envelope still need available original credit; nothing is replenished.
    """
    if type(chapters) is not int or chapters < 1:
        raise ValueError("chapter count must be positive")
    return {"calls": 14 + 6 * chapters, "tokens": 20000 + 12000 * chapters}


def tournament_config(config, profile):
    out = deepcopy(config)
    if profile == "complete":
        # Keep architecture, intent, source, reality and all judge floors.
        # Two pitches still compete; only one full card is expanded initially.
        out.update(n_candidates=2, raw_idea_pool_multiplier=1,
                   raw_idea_generation_batch_size=2, raw_idea_pool_topup_calls=0,
                   premise_card_count=1)
    return out


def candidate_count(calculated, profile):
    return min(2, calculated) if profile == "complete" else calculated


@contextmanager
def protect_architect_headroom(profile, chapters, *, stop_after_conception=False):
    """Reserve downstream credit without changing the architect's paid input."""
    reserve = _HEADROOM.get()
    if profile and not stop_after_conception:
        normalize_profile(profile)
        required = downstream_headroom(chapters)
        reserve = {key: max(required[key], (reserve or {}).get(key, 0))
                   for key in ("calls", "tokens")}
    token = _HEADROOM.set(reserve)
    try:
        yield
    finally:
        _HEADROOM.reset(token)


@contextmanager
def protect_downstream(profile, chapters, *, reserve_research=False):
    token = _HEADROOM.set(downstream_headroom(chapters)
        if profile == "complete" or reserve_research else None)
    profile_token = _PROFILE.set(profile)
    try:
        yield
    finally:
        _HEADROOM.reset(token)
        _PROFILE.reset(profile_token)


def assert_conception_headroom(*, calls_used, max_calls, tokens_charged, max_tokens,
                               requested_tokens):
    reserve = _HEADROOM.get()
    if reserve is None:
        return
    if (max_calls - calls_used - 1 < reserve["calls"]
            or max_tokens - tokens_charged - requested_tokens < reserve["tokens"]):
        from bestseller.services.budget import ExecutionBudgetError
        raise ExecutionBudgetError("execution_downstream_budget_reserved")


def stream_conception_enabled():
    return _PROFILE.get() == "complete"


def production_stream_enabled(request, role, *, profile=None, ambient_complete=False):
    from bestseller.services import minimax_m31_protocol as m31
    return bool((ambient_complete or profile == 'complete') and m31.applies(role.model) and not request.tools)


def apply_production_stream_settings(request, role, *, profile=None, ambient_complete=False):
    """One deterministic effective configuration for dispatch and retained reads."""
    complete_project = profile == 'complete'
    if not production_stream_enabled(request,role,profile=profile,ambient_complete=ambient_complete):
        return role
    updates = {'stream': True}
    if not role.reasoning_effort and (
        request.prompt_template in {'conception_finalize_continuation_v2','conception_finalize_adjudication_repair'}
        or (request.logical_role == 'editor' and request.prompt_template in {
            'conception_finalize', 'conception_finalize_repair',
            'conception_finalize_reader_first_v1',
            'conception_finalize_reader_first_v1_repair',
            'conception_concept_seed_final_premise_reconciliation',
            'conception_concept_seed_final_premise_reconciliation_repair',
            'conception_explicit_seed_fidelity_repair',
        })
        or (request.logical_role == 'planner' and request.prompt_template in {
            'conception_concept_seed_final_premise_reconciliation',
            'conception_concept_seed_final_premise_reconciliation_repair',
        })
        or (complete_project and request.logical_role in {'planner','writer','editor'})):
        updates['reasoning_effort'] = 'medium'
    effective = role.model_copy(update=updates)
    if request.prompt_template in _RUN2_NEW_BOOK_GENERATION_ALIASES:
        from bestseller.services import minimax_m31_protocol as m31

        expected = resolve_run2_new_book_template_policy(request, role)
        if expected is not None and m31.reasoning_effort(
            effective, max_completion_tokens=expected["max_completion_tokens"],
        ) != expected["effort"]:
            effective = effective.model_copy(update={"reasoning_effort": expected["effort"]})
    return effective


def freeze_resume_profile(original, incoming):
    """Keep a new UI default out of a legacy paid resume's effective input."""
    original = original or {}
    out = deepcopy(incoming)
    old_hints = original.get("user_hints")
    hints = out.get("user_hints")
    old_hints = old_hints if isinstance(old_hints, dict) else {}
    hints = hints if isinstance(hints, dict) else {}
    frozen = old_hints.get("production_profile", original.get("production_profile"))
    for value in (out.get("production_profile"), hints.get("production_profile")):
        if value is not None and value != frozen:
            raise ValueError("production_profile_changed_requires_new_plan")
    if frozen is not None:
        out["production_profile"] = frozen
        out["user_hints"] = {**hints, "production_profile": frozen}
    return out
