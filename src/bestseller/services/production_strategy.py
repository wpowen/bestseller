"""Production breadth and downstream headroom, separate from story cost style.

An absent profile is a legacy envelope and preserves its exact paid inputs.
New creation entrypoints explicitly freeze complete/research in user_hints.
This policy limits exploration; it never lowers a quality floor or grants spend.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

_HEADROOM = ContextVar("production_downstream_headroom", default=None)
_PROFILE = ContextVar("production_profile", default=None)
PROFILES = frozenset({"complete", "research"})
_TEMPLATE_POLICY_PATH = Path(__file__).resolve().parents[3] / "config/production_template_policy.json"
_POLICY_SCHEMA = "production-template-policy.v1"
_POLICY_EFFORTS = frozenset({"medium", "bounded_generation", "bounded_review"})
_CONTEXT_EFFORT = {"project_complete": "medium", "ambient_complete": "bounded_generation"}
_DYNAMIC_IDENTITY = ("file", "function", "call", "keyword", "expression_sha256")
_UNNAMED_RESOLUTIONS = {
    "readonly_probe": "missing_keyword",
    "legacy_readonly_reconstruction": "missing_keyword",
    "kwargs_named": "kwargs",
}
_MISSING = object()


def _policy_operand(value):
    if value is _MISSING:
        return {"type": "missing", "length": 0, "sha256": None}
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {"type": type(value).__name__, "length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def _policy_error(code, field, expected, actual):
    error = ValueError("production_template_policy:" + code)
    error.differences = [{"field": field, "expected": _policy_operand(expected),
                          "actual": _policy_operand(actual)}]
    return error


def _dynamic_identity(site):
    return tuple(site.get(key) for key in _DYNAMIC_IDENTITY)


def _validate_policy_roles(roles):
    if not isinstance(roles, dict):
        raise _policy_error("invalid_registry", "roles", "role object", roles)
    for logical_role, rule in roles.items():
        if logical_role not in {"planner", "writer", "critic", "editor", "summarizer"}:
            raise _policy_error("invalid_registry", "logical_role", "known logical role", logical_role)
        effort = rule.get("effort") if isinstance(rule, dict) else None
        valid_effort = (isinstance(effort, str) and effort in _POLICY_EFFORTS) or effort == _CONTEXT_EFFORT
        if (not isinstance(rule, dict) or not valid_effort
                or rule.get("cap") != "effective_request"
                or not isinstance(rule.get("reason"), str) or not rule["reason"].strip()):
            raise _policy_error("invalid_registry", "role_policy", "explicit effort, effective_request cap and reason", rule)


def _validate_runtime_pattern(pattern):
    """Only literal names, 20 lowercase hex, positive integers and finite groups.

    No wildcard, arbitrary character class, lookaround, backreference or group
    repetition is part of the native naming grammar. This classifies a name;
    it authenticates neither the embedded digest nor a source/paid receipt.
    """
    def invalid():
        return _policy_error("invalid_registry", "runtime_rule.pattern", "anchored typed native naming grammar", pattern)

    if not isinstance(pattern, str) or not pattern.startswith("^") or not pattern.endswith("$"):
        raise invalid()
    body = pattern[1:-1]
    token_pattern = re.compile(r"\[0-9a-f\]\{20\}|\[1-9\]\[0-9\]\*|\(\?:|\)\??|\||[A-Za-z0-9_]+")
    branches, cursor, typed = [False], 0, False
    if not re.match(r"[A-Za-z][A-Za-z0-9_]*", body):
        raise invalid()
    while cursor < len(body):
        match = token_pattern.match(body, cursor)
        if match is None:
            raise invalid()
        token, cursor = match.group(), match.end()
        if token == "(?:":
            branches.append(False)
        elif token == "|":
            if len(branches) == 1 or not branches[-1]:
                raise invalid()
            branches[-1] = False
        elif token in {")", ")?"}:
            if len(branches) == 1 or not branches.pop():
                raise invalid()
            branches[-1] = True
        else:
            branches[-1] = True
            typed = typed or token.startswith("[")
    if len(branches) != 1 or not branches[0] or not typed:
        raise invalid()
    try:
        re.compile(pattern)
    except re.error:
        raise invalid() from None


def _unnamed_request_registry(policy):
    """Review exact constructor/function ASTs without permitting unnamed sends."""
    entries = policy.get("unnamed_request_sites", [])
    if not isinstance(entries, list):
        raise _policy_error("invalid_registry", "unnamed_request_sites", "reviewed site list", entries)
    registered = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("kind") == "explicit_none":
            raise _policy_error("unregistered_unnamed_request", "unnamed_request.kind", "no explicit None exception", "explicit_none")
        if (not isinstance(entry, dict)
                or not all(isinstance(entry.get(key), str) for key in _DYNAMIC_IDENTITY)
                or entry.get("keyword") != "prompt_template"
                or not re.fullmatch(r"[0-9a-f]{64}", entry["expression_sha256"])):
            raise _policy_error("invalid_registry", "unnamed_request_sites", "five-key constructor/function AST identity", entry)
        resolution, names = entry.get("resolution"), entry.get("templates")
        kind = _UNNAMED_RESOLUTIONS.get(resolution) if isinstance(resolution, str) else None
        if (kind is None or entry.get("kind", kind) != kind
                or not isinstance(entry.get("reason"), str) or not entry["reason"].strip()
                or not isinstance(names, list)
                or any(not isinstance(name, str) or name not in policy["templates"] for name in names)
                or (kind == "kwargs") != bool(names)):
            raise _policy_error("invalid_registry", "unnamed_request_sites", "reviewed kind, resolution, finite names and reason", entry)
        identity = _dynamic_identity(entry)
        if identity in registered:
            raise _policy_error("invalid_registry", "unnamed_request_sites", "unique site identity", list(identity))
        registered[identity] = entry
    return registered


@lru_cache(maxsize=1)
def _parse_template_policy(raw):
    """Cache by exact file bytes, so a changed registry cannot reuse old data."""
    try:
        policy = json.loads(raw)
    except (ValueError, UnicodeError):
        raise _policy_error("invalid_registry", "registry", "valid JSON", raw) from None
    if not isinstance(policy, dict) or policy.get("schema") != _POLICY_SCHEMA:
        raise _policy_error("invalid_registry", "schema", _POLICY_SCHEMA, policy)
    templates, sites = policy.get("templates"), policy.get("dynamic_sites")
    if not isinstance(templates, dict) or not isinstance(sites, list):
        raise _policy_error("invalid_registry", "registry", "templates object and dynamic_sites list", policy)
    for name, entry in templates.items():
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", name) or not isinstance(entry, dict):
            raise _policy_error("invalid_registry", "templates", "exact template entry", name)
        _validate_policy_roles(entry.get("roles"))
    identities = set()
    for site in sites:
        if (not isinstance(site, dict)
                or not all(isinstance(site.get(key), str) for key in _DYNAMIC_IDENTITY)
                or not re.fullmatch(r"[0-9a-f]{64}", site["expression_sha256"])
                or not isinstance(site.get("templates"), list)
                or any(not isinstance(name, str) or name not in templates for name in site["templates"])):
            raise _policy_error("invalid_registry", "dynamic_sites", "sealed site with registered finite outputs", site)
        identity = _dynamic_identity(site)
        if identity in identities:
            raise _policy_error("invalid_registry", "dynamic_sites", "unique site identity", list(identity))
        identities.add(identity)
    rules = policy.get("runtime_rules", [])
    if not isinstance(rules, list):
        raise _policy_error("invalid_registry", "runtime_rules", "rule list", rules)
    rule_ids, patterns = set(), set()
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("id"), str)
                or not rule["id"] or rule["id"] in rule_ids):
            raise _policy_error("invalid_registry", "runtime_rule", "unique named rule", rule)
        pattern = rule.get("pattern")
        _validate_runtime_pattern(pattern)
        if pattern in patterns:
            raise _policy_error("invalid_registry", "runtime_rule.pattern", "unique pattern", pattern)
        _validate_policy_roles(rule.get("roles"))
        bindings = rule.get("source_bindings")
        if not rule["roles"] or not isinstance(bindings, list) or not bindings:
            raise _policy_error("invalid_registry", "runtime_rule", "explicit roles and AST source bindings", rule)
        seen_bindings = set()
        for binding in bindings:
            if (not isinstance(binding, dict) or set(binding) != set(_DYNAMIC_IDENTITY)
                    or not all(isinstance(binding.get(key), str) for key in _DYNAMIC_IDENTITY)
                    or _dynamic_identity(binding) not in identities
                    or _dynamic_identity(binding) in seen_bindings):
                raise _policy_error("invalid_registry", "runtime_rule.source_bindings", "unique registered five-key AST identity", binding)
            seen_bindings.add(_dynamic_identity(binding))
        rule_ids.add(rule["id"])
        patterns.add(pattern)
    _unnamed_request_registry(policy)
    return policy


def _template_policy():
    try:
        raw = _TEMPLATE_POLICY_PATH.read_bytes()
    except OSError as error:
        raise _policy_error("invalid_registry", "registry", "readable registry", type(error).__name__) from None
    return _parse_template_policy(raw)


def assert_template_policy_coverage(inventory):
    """Check exact literals and sealed dynamic expressions from the AST census."""
    policy = _template_policy()
    if (not isinstance(inventory, dict) or inventory.get("parse_failures") != []
            or not isinstance(inventory.get("templates"), list)
            or not isinstance(inventory.get("dynamic_sites"), list)):
        raise _policy_error("invalid_inventory", "inventory", "complete AST inventory without parse failures", inventory)
    # Older synthetic inventories predate this census. The actual scanner
    # always emits it; an explicit None cannot use any reviewed exception.
    unnamed_sites = inventory.get("unnamed_send_sites", [])
    if not isinstance(unnamed_sites, list):
        raise _policy_error("invalid_inventory", "unnamed_send_sites", "site list", unnamed_sites)
    registered = _unnamed_request_registry(policy)
    for site in unnamed_sites:
        if (not isinstance(site, dict)
                or not all(isinstance(site.get(key), str) for key in _DYNAMIC_IDENTITY)
                or site.get("kind") not in {"missing_keyword", "kwargs"}):
            raise _policy_error("unregistered_unnamed_request", "unnamed_request", "reviewed non-None constructor", site)
        entry = registered.get(_dynamic_identity(site))
        if entry is None or site["kind"] != _UNNAMED_RESOLUTIONS[entry["resolution"]]:
            raise _policy_error("unregistered_unnamed_request", "unnamed_request", "registered exact AST identity and kind", site)
    for row in inventory["templates"]:
        name = row.get("template", _MISSING) if isinstance(row, dict) else _MISSING
        if not isinstance(name, str) or name not in policy["templates"]:
            raise _policy_error("unregistered_template", "prompt_template", sorted(policy["templates"]), name)
    identities = {_dynamic_identity(site) for site in policy["dynamic_sites"]}
    actual_identities = set()
    for site in inventory["dynamic_sites"]:
        if (not isinstance(site, dict)
                or not all(isinstance(site.get(key), str) for key in _DYNAMIC_IDENTITY)
                or _dynamic_identity(site) not in identities):
            raise _policy_error("unregistered_dynamic_site", "dynamic_site", "registered exact AST identity", site)
        actual_identities.add(_dynamic_identity(site))
    for rule in policy.get("runtime_rules", []):
        for binding in rule["source_bindings"]:
            if _dynamic_identity(binding) not in actual_identities:
                raise _policy_error("unregistered_dynamic_site", "runtime_rule.source_binding", binding, _MISSING)


def _template_policy_roles(policy, name):
    # An exact entry is authoritative even when it deliberately grants no role.
    if name in policy["templates"]:
        return policy["templates"][name]["roles"]
    matches = [rule for rule in policy.get("runtime_rules", [])
               if isinstance(name, str) and re.fullmatch(rule["pattern"], name)]
    if not matches:
        raise _policy_error("unregistered_template", "prompt_template", "registered literal or typed native name", name)
    if len(matches) != 1:
        raise _policy_error("ambiguous_template", "runtime_rules", "one matching rule", [r["id"] for r in matches])
    return matches[0]["roles"]


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
    from bestseller.services import minimax_m31_protocol as m31

    if not (ambient_complete or profile == "complete") or not m31.applies(role.model):
        return role
    roles = _template_policy_roles(_template_policy(), request.prompt_template)
    if request.logical_role not in roles:
        raise _policy_error("unregistered_role", "logical_role", sorted(roles), request.logical_role)
    # Tool requests still use the existing non-streaming wire path, but no
    # longer bypass the exact template/role policy. Preserve their role flag.
    updates = {} if request.tools else {"stream": True}
    if not role.reasoning_effort:
        effort = roles[request.logical_role]["effort"]
        if isinstance(effort, dict):
            effort = effort["project_complete" if profile == "complete" else "ambient_complete"]
        if effort != "medium":
            from bestseller.services.llm import _effective_request_max_tokens

            cap = _effective_request_max_tokens(role, request)
            narrowed = cap <= 4096 or cap < role.max_tokens
            effort = "low" if narrowed else "max" if effort == "bounded_review" else "medium"
        updates["reasoning_effort"] = effort
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
