"""Production breadth and downstream headroom, separate from story cost style.

An absent profile is a legacy envelope and preserves its exact paid inputs.
New creation entrypoints explicitly freeze complete/research in user_hints.
This policy limits exploration; it never lowers a quality floor or grants spend.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_HEADROOM = ContextVar("production_downstream_headroom", default=None)
_PROFILE = ContextVar("production_profile", default=None)
PROFILES = frozenset({"complete", "research"})


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
    return role.model_copy(update=updates)


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
