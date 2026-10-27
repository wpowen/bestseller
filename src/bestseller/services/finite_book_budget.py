"""Additional finite run limits on the original ledger, including completion-bound.

No authorization is installed implicitly. Money is integer micro-CNY. A trusted
input-count adapter must provide a model/input-bound, audited upper bound; the
existing heuristic capacity estimate is deliberately not accepted as accounting.
"""

from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from urllib.parse import urlsplit

_CURRENT = ContextVar("finite_book_accounting", default=None)


class FiniteBudgetError(ValueError):
    pass


def need(value, reason):
    if not value:
        raise FiniteBudgetError(reason)


@contextmanager
def bind_run(
    run_id,
    *,
    project_id,
    stage,
    input_counter=None,
    category="primary",
    retry_of=None,
    capacity_limit=65536,
):
    token = _CURRENT.set(
        dict(
            run_id=str(run_id),
            project_id=str(project_id),
            stage=stage,
            input_counter=input_counter,
            category=category,
            retry_of=retry_of,
            capacity_limit=capacity_limit,
        )
    )
    try:
        yield
    finally:
        _CURRENT.reset(token)


def install(
    metadata,
    *,
    run_id,
    project_id,
    model,
    endpoint,
    caps,
    counter_sha256,
    authorization_ref,
    explicit_approval=False,
    capacity_limit=65536,
    max_request_output_tokens=32768,
):
    need(explicit_approval is True and bool(authorization_ref), "finite_authority_required")
    need(
        set(caps) == {"calls", "money_micro_cny", "primary", "transport_retry", "hard_repair"},
        "finite_caps_shape",
    )
    need(
        all(type(v) is int and v >= 0 for v in caps.values())
        and caps["calls"] > 0
        and caps["money_micro_cny"] > 0,
        "finite_caps_invalid",
    )
    need(
        caps["primary"] + caps["transport_retry"] + caps["hard_repair"] <= caps["calls"],
        "finite_caps_categories",
    )
    need(type(counter_sha256) is str and len(counter_sha256) == 64, "finite_counter_not_verified")
    need(urlsplit(endpoint).scheme == "https", "finite_endpoint")
    need(type(capacity_limit) is int and 0 < capacity_limit <= 1_000_000, "finite_capacity_limit")
    need(
        type(max_request_output_tokens) is int and 0 < max_request_output_tokens <= 524288,
        "finite_output_limit",
    )
    out = deepcopy(metadata or {})
    runs = out.setdefault("finite_book_runs", {})
    binding = dict(
        run_id=str(run_id),
        project_id=str(project_id),
        model=model,
        endpoint=endpoint,
        caps=dict(caps),
        counter_sha256=counter_sha256,
        authorization_ref=authorization_ref,
        input_micro_cny_per_token=1,
        output_micro_cny_per_token=4,
        capacity_limit=capacity_limit,
        max_request_output_tokens=max_request_output_tokens,
    )
    if str(run_id) in runs:
        need(runs[str(run_id)]["binding"] == binding, "finite_authority_binding_changed")
    else:
        runs[str(run_id)] = {
            "binding": binding,
            "status": "active",
            "calls": 0,
            "held_micro_cny": 0,
            "spent_micro_cny": 0,
            "counts": dict(primary=0, transport_retry=0, hard_repair=0),
            "attempts": {},
        }
    active = out.get("active_finite_book_run")
    need(active in (None, str(run_id)), "finite_other_run_active")
    out["active_finite_book_run"] = str(run_id)
    return out


async def quote(request, role, payload, digest):
    state = _CURRENT.get()
    if state is None:
        return None
    need(str(request.project_id) == state["project_id"], "finite_project_changed")
    from bestseller.services.book_request_purpose import classify

    try:
        category = classify(state["stage"], request.prompt_template)
    except ValueError as exc:
        raise FiniteBudgetError(str(exc)) from exc
    counter = state["input_counter"]
    if counter is None:
        from bestseller.services.minimax_book_cost_contract import input_bound

        counter = input_bound
    need(callable(counter), "finite_input_bound_unavailable")
    proof = counter(payload)
    if hasattr(proof, "__await__"):
        proof = await proof
    need(
        isinstance(proof, dict)
        and proof.get("schema") == "verified-input-upper-bound.v1"
        and proof.get("model") == role.model
        and proof.get("input_hash") == digest,
        "finite_input_bound_not_bound",
    )
    n = proof.get("input_tokens_upper")
    need(type(n) is int and n >= 0, "finite_input_bound_invalid")
    output = proof.get("output_tokens_upper", payload["max_tokens"])
    need(type(output) is int and output >= payload["max_tokens"] > 0, "finite_output_bound_invalid")
    need(payload["n_candidates"] == 1, "finite_candidate_count")
    from bestseller.services.llm import current_execution_scope

    return {k: v for k, v in state.items() if k != "input_counter"} | dict(
        scope_id=current_execution_scope(),
        journal_sha256=digest,
        input_upper=n,
        output_upper=output,
        template=request.prompt_template,
        category=category,
        counter_sha256=proof.get("counter_sha256"),
        input_hash=digest,
        model=role.model,
        endpoint=role.api_base,
        reserved_micro_cny=n + 4 * output,
    )


def reserve(metadata, *, attempt_id, operation_key, model, input_hash, endpoint, binding):
    active = (metadata or {}).get("active_finite_book_run")
    if active is None:
        need(binding is None, "finite_authorization_missing")
        return metadata
    need(
        isinstance(binding, dict) and binding.get("run_id") == active, "finite_run_context_required"
    )
    out = deepcopy(metadata)
    run = out["finite_book_runs"][active]
    b = run["binding"]
    caps = b["caps"]
    need(run["status"] == "active", "finite_run_stopped")
    need(
        (binding["project_id"], model, endpoint) == (b["project_id"], b["model"], b["endpoint"]),
        "finite_route_or_project_changed",
    )
    need(
        type(binding.get("capacity_limit", 65536)) is int
        and 0 < binding.get("capacity_limit", 65536) <= b["capacity_limit"],
        "finite_capacity_binding_changed",
    )
    need(
        binding["counter_sha256"] == b["counter_sha256"] and binding["input_hash"] == input_hash,
        "finite_counter_or_input_changed",
    )
    need(
        type(b.get("max_request_output_tokens")) is int
        and binding["output_upper"] <= b["max_request_output_tokens"],
        "finite_output_limit",
    )
    binding = deepcopy(binding)
    prior_logical = [
        (aid, a)
        for aid, a in run["attempts"].items()
        if a["operation_key"] == operation_key and a["input_hash"] == input_hash
    ]
    if prior_logical:
        need(len(prior_logical) == 1, "finite_retry_limit")
        binding["category"] = "transport_retry"
        binding["retry_of"] = prior_logical[0][0]
    elif binding["category"] == "primary":
        from bestseller.services.book_request_purpose import primary_occurrences

        repeated = sum(
            a["stage"] == binding["stage"] and a.get("template") == binding.get("template")
            for a in run["attempts"].values()
        )
        if repeated >= primary_occurrences(binding["stage"], binding.get("template")):
            binding["category"] = "hard_repair"
    category = binding["category"]
    need(category in run["counts"], "finite_category")
    need(
        run["calls"] < caps["calls"] and run["counts"][category] < caps[category],
        "finite_calls_exhausted",
    )
    amount = binding["reserved_micro_cny"]
    need(
        type(amount) is int and amount == binding["input_upper"] + 4 * binding["output_upper"],
        "finite_quote_invalid",
    )
    need(
        run["held_micro_cny"] + run["spent_micro_cny"] + amount <= caps["money_micro_cny"],
        "finite_money_exhausted",
    )
    if category == "transport_retry":
        prior = run["attempts"].get(binding["retry_of"])
        need(
            prior is not None
            and prior["category"] != "transport_retry"
            and prior["status"] in {"rejected", "not_sent"}
            and prior.get("zero_usage_proven") is True,
            "finite_retry_not_known_unsent",
        )
        need(
            (prior["operation_key"], prior["input_hash"], prior["model"])
            == (operation_key, input_hash, model),
            "finite_retry_changed",
        )
        need(
            not any(a.get("retry_of") == binding["retry_of"] for a in run["attempts"].values()),
            "finite_retry_limit",
        )
    need(attempt_id not in run["attempts"], "finite_duplicate_attempt")
    run["calls"] += 1
    run["counts"][category] += 1
    run["held_micro_cny"] += amount
    run["attempts"][attempt_id] = {
        **deepcopy(binding),
        "operation_key": operation_key,
        "status": "reserved",
    }
    return out


def settle(metadata, *, attempt_id, status, result=None, actual_tokens=None):
    active = (metadata or {}).get("active_finite_book_run")
    if active is None:
        return metadata, None
    out = deepcopy(metadata)
    run = out["finite_book_runs"][active]
    a = run["attempts"].get(attempt_id)
    # Historical attempts are never charged again to a newly approved increment.
    if a is None:
        return metadata, None
    if a["status"] != "reserved":
        return metadata, None
    held = a["reserved_micro_cny"]
    reason = None
    if status == "unknown":
        a["status"] = "unknown"
        run["status"] = "unknown"
        reason = "finite_send_unknown"
    elif status == "not_sent" and actual_tokens == 0:
        a.update(status=status, zero_usage_proven=True)
        run["held_micro_cny"] -= held
    elif (
        status == "completed"
        and isinstance(result, dict)
        and all(
            type(result.get(k)) is int and result[k] >= 0 for k in ("input_tokens", "output_tokens")
        )
    ):
        cost = result["input_tokens"] + 4 * result["output_tokens"]
        a.update(status="completed", actual_micro_cny=cost)
        run["held_micro_cny"] -= held
        run["spent_micro_cny"] += cost
        if (
            result["input_tokens"] > a["input_upper"]
            or result["output_tokens"] > a["output_upper"]
            or run["spent_micro_cny"] + run["held_micro_cny"]
            > run["binding"]["caps"]["money_micro_cny"]
        ):
            run["status"] = "accounting_breach"
            reason = "finite_accounting_bound_breached"
    else:
        a["status"] = "usage_unknown"
        run["status"] = "unknown"
        reason = "finite_usage_unknown"
    return out, reason


def route_authorized(metadata, binding, model, endpoint):
    active = (metadata or {}).get("active_finite_book_run")
    if not isinstance(binding, dict) or binding.get("run_id") != active:
        return False
    run = (metadata.get("finite_book_runs") or {}).get(active) or {}
    b = run.get("binding") or {}
    return run.get("status") == "active" and (model, endpoint, binding.get("project_id")) == (
        b.get("model"),
        b.get("endpoint"),
        b.get("project_id"),
    )


def compile_attempt_grant(
    metadata,
    *,
    binding,
    attempt_id,
    operation_key,
    input_hash,
    model,
    endpoint,
    workflow_id,
    template,
    version,
):
    """Compile one exact grant only inside the approved finite reserve transaction.

    Existing grants are never replaced. The originating human reference remains
    exact; the derived ID is a run-policy request capability, not another user
    message or a fresh old-slot allowance.
    """
    import hashlib

    active = metadata["active_finite_book_run"]
    run = metadata["finite_book_runs"][active]
    need(attempt_id in run["attempts"], "finite_attempt_not_reserved")
    gid = hashlib.sha256(
        ("finite-run-attempt.v1\n" + active + "\n" + attempt_id).encode()
    ).hexdigest()
    out = deepcopy(metadata)
    out.setdefault("single_dispatch_grants", {})[gid] = {
        "schema": "single-dispatch-authorization.v1",
        "physical_attempt_cap": 1,
        "status": "available",
        "used_attempt_id": None,
        "binding": {
            "scope_id": binding["scope_id"],
            "operation_key": operation_key,
            "input_hash": input_hash,
            "model": model,
            "endpoint": endpoint,
            "workflow_id": str(workflow_id),
            "template": template,
            "version_prefix": version,
            "dispatch_scope": "workflow",
            "user_message_id": run["binding"]["authorization_ref"],
            "prior_workflow_id": active,
            "prior_journal_sha256": binding["journal_sha256"],
            "authority_evidence": {
                "type": "approved-finite-book-run",
                "run_id": active,
                "stage": binding["stage"],
                "bounded_attempt_id": attempt_id,
                "original_grants_unchanged": True,
            },
        },
    }
    return out, gid


async def guard_prepared_request(request, role, payload, digest, *, session=None):
    # Run before claim: unsupported families/cost contracts are known pre-send
    # waits and must not leave a claimed operation behind.
    if _CURRENT.get() is not None:
        from bestseller.infra.db import models as m
        from bestseller.services.llm import current_execution_scope

        need(session is not None, "finite_preclaim_session_required")
        budget = await session.get(
            m.ExecutionBudgetModel, current_execution_scope(), populate_existing=True
        )
        state = _CURRENT.get()
        run = (
            ((budget.metadata_json or {}).get("finite_book_runs") or {}).get(state["run_id"])
            if budget is not None
            else None
        )
        need(run is not None and run.get("status") == "active", "finite_run_stopped")
        need(
            type(state["capacity_limit"]) is int
            and 0 < state["capacity_limit"] <= run["binding"]["capacity_limit"],
            "finite_capacity_binding_changed",
        )
        quoted = await quote(request, role, payload, digest)
        need(
            type(run["binding"].get("max_request_output_tokens")) is int
            and quoted["output_upper"] <= run["binding"]["max_request_output_tokens"],
            "finite_output_limit",
        )


def current_capacity(*, model_limit=None):
    from bestseller.services.model_context_capacity import selected_context
    state = _CURRENT.get()
    ceiling = selected_context() if model_limit is None else model_limit
    if state is not None:
        return min(state["capacity_limit"], ceiling) if ceiling is not None else state["capacity_limit"]
    return ceiling if ceiling is not None else 65536


def bound_book_plan(project_id):
    """A finite manuscript planning stage still requires native prefix admission."""
    state = _CURRENT.get()
    return bool(
        state is not None
        and state["project_id"] == str(project_id)
        and state["stage"].rsplit(":", 1)[-1] == "plan"
    )


def compiler_capacity_for(project_id, *, output_tokens):
    """One book's compiler allowance; SEND still requires the locked ledger.

    The whole-request guard remains authoritative after exact compilation.
    Outside a matching finite context the existing compiler budget is retained.
    """
    state = _CURRENT.get()
    if state is None or state["project_id"] != str(project_id) or current_capacity() <= 65536:
        return None
    need(type(output_tokens) is int and output_tokens > 0, "finite_compiler_output_invalid")
    return {
        "run_id": state["run_id"],
        "capacity_limit": current_capacity(),
        "output_reserve": output_tokens,
        "input_ceiling": current_capacity() - output_tokens - 512,
        "authorization_claimed": False,
    }
