"""Pure policy REDs; no database, application entry, SDK or provider calls.

Use the existing AST inventory rather than a second source parser. The role
fixture contains only the whitelisted production values captured by main in
RUN2-PRODUCTION-ROLE-CONFIG.json on 2026-10-29; no environment is loaded.
"""

import json
import runpy
from copy import deepcopy
from pathlib import Path

import pytest

from bestseller.services import llm
from bestseller.services import minimax_m31_protocol as m31
from bestseller.services import production_strategy as strategy
from bestseller.settings import LLMRoleSettings


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def scan_repository():
    return runpy.run_path(
        str(ROOT / "scripts/full_flow/simulation/inventory.py")
    )["scan_repository"]


def _role(logical_role="planner"):
    return LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        temperature={"planner": .82, "writer": .85, "critic": .25,
                     "editor": .4, "summarizer": .2}[logical_role],
        max_tokens=1536 if logical_role == "summarizer" else 32768,
        timeout_seconds=180,
        reasoning_effort=None,
        thinking_type=None,
        stream=False,
    )


def _request(template, *, role="planner", cap=None, version="1.0", tools=None):
    return llm.LLMCompletionRequest(
        logical_role=role,
        system_prompt="Synthetic policy boundary; never dispatched.",
        user_prompt="Synthetic policy boundary; never dispatched.",
        fallback_response="{}",
        prompt_template=template,
        prompt_version=version,
        max_tokens_override=cap,
        tools=tools,
    )


def _scan(tmp_path, scan_repository, source):
    folder = tmp_path / "src/bestseller/services"
    folder.mkdir(parents=True)
    (folder / "new_policy_consumer.py").write_text(source, encoding="utf-8")
    inventory = scan_repository(tmp_path)
    assert inventory["parse_failures"] == []
    return inventory


@pytest.mark.parametrize("role", ["planner", "writer", "critic", "editor", "summarizer"])
def test_new_literal_scanned_from_source_cannot_silently_send_complete(
    tmp_path, scan_repository, role,
):
    inventory = _scan(tmp_path, scan_repository,
                      'LLMCompletionRequest(prompt_template="unregistered_policy_census_probe")\n')
    assert [row["template"] for row in inventory["templates"]] == [
        "unregistered_policy_census_probe"
    ]
    request = _request(inventory["templates"][0]["template"], role=role)
    with pytest.raises(ValueError, match="production_template_policy:unregistered_template"):
        strategy.apply_production_stream_settings(request, _role(role), profile="complete")


def test_unregistered_complete_tool_request_is_not_exempt_from_policy():
    request = _request("unregistered_policy_census_tool", tools=[{
        "type": "function", "function": {"name": "synthetic", "parameters": {"type": "object"}},
    }])
    with pytest.raises(ValueError, match="production_template_policy:unregistered_template"):
        strategy.apply_production_stream_settings(request, _role(), profile="complete")


def test_projectless_complete_conception_cannot_silently_accept_new_literal():
    with pytest.raises(ValueError, match="production_template_policy:unregistered_template"):
        strategy.apply_production_stream_settings(
            _request("unregistered_policy_census_conception"), _role(), ambient_complete=True,
        )


@pytest.mark.parametrize("template", [
    "unregistered_policy_census_probe_repair",
    "concept_tournament_candidate_reader_first_v999",
    "planner_unregistered_policy_census_probe",
])
def test_unregistered_dynamic_output_does_not_gain_prefix_or_repair_permission(template):
    with pytest.raises(ValueError, match="production_template_policy:unregistered_template"):
        strategy.apply_production_stream_settings(_request(template), _role(), profile="complete")


@pytest.mark.parametrize("cap,expected", [(None, "max"), (4096, "low")])
def test_existing_critic_effort_is_explicit_without_changing_effective_strength(cap, expected):
    role = _role("critic")
    request = _request("chapter_review", role="critic", cap=cap,
                       version="6.0-structured-body-submission")
    original_cap = llm._effective_request_max_tokens(role, request)
    assert m31.reasoning_effort(role, max_completion_tokens=original_cap) == expected
    effective = strategy.apply_production_stream_settings(request, role, profile="complete")
    assert effective.reasoning_effort == expected
    assert llm._effective_request_max_tokens(effective, request) == original_cap


@pytest.mark.parametrize("profile", [None, "research"])
def test_legacy_and_research_keep_exact_effective_inputs(profile):
    role = _role("critic")
    request = _request("unregistered_policy_census_legacy", role="critic")
    before = llm._effective_input_payload(request, role)
    effective = strategy.apply_production_stream_settings(request, role, profile=profile)
    assert effective.model_dump() == role.model_dump()
    assert llm._effective_input_payload(request, effective) == before


def test_static_registry_rejects_new_literal_from_existing_scanner(tmp_path, scan_repository):
    inventory = _scan(tmp_path, scan_repository,
                      'LLMCompletionRequest(prompt_template="unregistered_policy_census_probe")\n')
    with pytest.raises(ValueError, match="production_template_policy:unregistered_template"):
        strategy.assert_template_policy_coverage(inventory)


def test_static_registry_rejects_unregistered_dynamic_expression(tmp_path, scan_repository):
    inventory = _scan(tmp_path, scan_repository,
                      'def build(logical_name):\n'
                      '    return LLMCompletionRequest(prompt_template=f"planner_{logical_name}")\n')
    assert len(inventory["dynamic_sites"]) == 1
    with pytest.raises(ValueError, match="production_template_policy:unregistered_dynamic_site"):
        strategy.assert_template_policy_coverage(inventory)


def test_json_repair_derived_name_is_in_static_coverage(tmp_path, scan_repository):
    inventory = _scan(tmp_path, scan_repository,
                      '_llm_call_json(template="unregistered_policy_census_json")\n')
    assert {row["template"] for row in inventory["templates"]} == {
        "unregistered_policy_census_json", "unregistered_policy_census_json_repair",
    }
    with pytest.raises(ValueError, match="production_template_policy:unregistered_template"):
        strategy.assert_template_policy_coverage(inventory)


def test_existing_scanner_must_distinguish_dynamic_expression_drift(tmp_path, scan_repository):
    before = _scan(tmp_path / "before", scan_repository,
                   'def build(template, enabled):\n'
                   '    return LLMCompletionRequest(prompt_template=('
                   'template + "_reader_first_v1" if enabled else template))\n')
    after = _scan(tmp_path / "after", scan_repository,
                  'def build(template, enabled):\n'
                  '    return LLMCompletionRequest(prompt_template=('
                  'template + "_unregistered_v2" if enabled else template))\n')
    old, new = before["dynamic_sites"][0], after["dynamic_sites"][0]
    # Both currently render as <IfExp>. That display label cannot seal a domain.
    assert old["expression"] == new["expression"]
    for row in (old, new):
        digest = row.get("expression_sha256")
        assert isinstance(digest, str) and len(digest) == 64, (
            "dynamic template AST fingerprint is missing; display labels are not authority"
        )
    assert old["expression_sha256"] != new["expression_sha256"]


@pytest.fixture(scope="module")
def actual_inventory(scan_repository):
    return scan_repository(ROOT)


def test_actual_whole_repository_inventory_has_explicit_coverage(actual_inventory):
    assert actual_inventory["parse_failures"] == []
    strategy.assert_template_policy_coverage(actual_inventory)


def test_actual_inventory_new_literal_mutation_is_rejected(
    actual_inventory, tmp_path, scan_repository,
):
    changed = deepcopy(actual_inventory)
    added = _scan(tmp_path, scan_repository,
                  'LLMCompletionRequest(prompt_template="unregistered_whole_inventory_probe")\n')
    changed["templates"].extend(added["templates"])
    with pytest.raises(ValueError, match="production_template_policy:unregistered_template"):
        strategy.assert_template_policy_coverage(changed)


def test_actual_inventory_dynamic_expression_mutation_is_rejected(actual_inventory):
    changed = deepcopy(actual_inventory)
    row = next(r for r in changed["dynamic_sites"]
               if r["function"] == "_judge_final_packaging_seed_fidelity")
    assert row["expression_sha256"] != "0" * 64
    row["expression_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="production_template_policy:unregistered_dynamic_site"):
        strategy.assert_template_policy_coverage(changed)


def test_local_naming_helper_body_drift_changes_its_expression_fingerprint(tmp_path, scan_repository):
    def inventory_at(folder, suffix):
        return _scan(folder, scan_repository,
                     'def naming(base):\n'
                     f'    return base + "{suffix}"\n'
                     'def build(base):\n'
                     '    return LLMCompletionRequest(prompt_template=naming(base))\n')
    before = inventory_at(tmp_path / "before", "_candidate_v2")
    after = inventory_at(tmp_path / "after", "_unregistered_v3")
    assert before["dynamic_sites"][0]["expression"] == after["dynamic_sites"][0]["expression"]
    assert (before["dynamic_sites"][0]["expression_sha256"]
            != after["dynamic_sites"][0]["expression_sha256"])


_PACKAGING_BASES = (
    "conception_final_packaging_seed_fidelity_judge",
    "conception_final_packaging_seed_fidelity_arbitration",
    "conception_final_packaging_canonical_quote_recovery",
)


@pytest.mark.parametrize("base", _PACKAGING_BASES)
@pytest.mark.parametrize("suffix", ["", "_repair"])
def test_native_packaging_subject_name_resolves_without_claiming_source_proof(base, suffix):
    # A synthetic shape is a policy classification only; it is not a body hash proof.
    request = _request(base + "_candidate_v2_" + "a1" * 10 + suffix, role="critic")
    role = _role("critic")
    result = strategy.apply_production_stream_settings(request, role, ambient_complete=True)
    assert result.reasoning_effort == "max"
    assert llm._effective_request_max_tokens(result, request) == 32768


@pytest.mark.parametrize("template", [
    "planner_volume_1_chapter_outline",
    "planner_volume_12_chapter_outline_batch_1_4",
    "planner_volume_2_cast_expansion",
    "planner_volume_2_world_disclosure",
    "planner_volume_12_chapter_outline_batch_1_4_commercial_surgical_repair",
    "planner_volume_12_chapter_outline_story_enhancer_route_repair",
])
def test_native_typed_volume_names_resolve_without_relaxing_source_guards(template):
    request, role = _request(template), _role()
    result = strategy.apply_production_stream_settings(request, role, profile="complete")
    assert result.reasoning_effort == "medium"
    assert llm._effective_request_max_tokens(result, request) == 32768


@pytest.mark.parametrize("template", [
    "conception_unreviewed_base_candidate_v2_" + "a" * 20,
    _PACKAGING_BASES[0] + "_candidate_v2_" + "a" * 19,
    _PACKAGING_BASES[0] + "_candidate_v2_" + "a" * 21,
    _PACKAGING_BASES[0] + "_candidate_v2_" + "g" * 20,
    _PACKAGING_BASES[0] + "_candidate_v2_" + "A" * 20,
    _PACKAGING_BASES[0] + "_candidate_v2_" + "a" * 20 + "_extra",
    _PACKAGING_BASES[0] + "_candidate_v2_" + "a" * 20 + "_repair_repair",
    "planner_volume_0_chapter_outline",
    "planner_volume_01_chapter_outline",
    "planner_volume_x_chapter_outline",
    "planner_volume_1_chapter_outline_batch_0_4",
    "planner_volume_1_chapter_outline_extra",
])
def test_runtime_name_grammar_rejects_invalid_variants(template):
    role = "critic" if template.startswith("conception_") else "planner"
    with pytest.raises(ValueError, match="production_template_policy:unregistered_template"):
        strategy.apply_production_stream_settings(
            _request(template, role=role), _role(role), profile="complete",
        )


@pytest.mark.parametrize("template,role", [
    (_PACKAGING_BASES[0] + "_candidate_v2_" + "a" * 20, "planner"),
    ("planner_volume_2_chapter_outline", "critic"),
])
def test_known_runtime_name_with_unproven_role_is_rejected(template, role):
    with pytest.raises(ValueError, match="production_template_policy:unregistered_role"):
        strategy.apply_production_stream_settings(
            _request(template, role=role), _role(role), profile="complete",
        )


def test_prior_actual_native_conception_names_and_failed_finalizer_are_registered():
    # Read only main's sanitized identity census, never request bytes or a ledger.
    census = json.loads((ROOT / "reports/full-flow-2026-10-28/"
                         "RUN2-PRIOR-CONCEPTION-TEMPLATE-CENSUS.json").read_text())
    identities = {(r["origin"]["prompt_template"], r["origin"]["logical_role"])
                  for r in census["completed_templates"]}
    assert len(census["completed_templates"]) == 26
    assert len(identities) == 18
    identities.add(("conception_finalize_reader_first_v1", "editor"))
    registry = json.loads((ROOT / "config/production_template_policy.json").read_text())
    for template, role in sorted(identities):
        assert role in registry["templates"][template]["roles"], (template, role)
        effective = strategy.apply_production_stream_settings(
            _request(template, role=role), _role(role), ambient_complete=True,
        )
        assert effective.reasoning_effort in {"low", "medium", "high", "xhigh", "max"}


_UNNAMED_IDENTITY = ("file", "function", "call", "keyword", "expression_sha256")


def _with_unnamed_sites(actual_inventory, sites):
    inventory = deepcopy(actual_inventory)
    inventory["unnamed_send_sites"].extend(deepcopy(sites))
    return inventory


@pytest.mark.parametrize("source,kind", [
    ('LLMCompletionRequest(prompt_template=None)\n', "explicit_none"),
    ('LLMCompletionRequest()\n', "missing_keyword"),
    ('LLMCompletionRequest(**fields)\n', "kwargs"),
], ids=["new_none", "new_missing", "new_kwargs"])
def test_unnamed_new_constructor_requires_exact_review(
    actual_inventory, tmp_path, scan_repository, source, kind,
):
    added = _scan(tmp_path, scan_repository, source)
    assert len(added["unnamed_send_sites"]) == 1
    site = added["unnamed_send_sites"][0]
    assert site["kind"] == kind
    assert site["keyword"] == "prompt_template"
    assert len(site["expression_sha256"]) == 64
    with pytest.raises(ValueError, match="production_template_policy:unregistered_unnamed_request"):
        strategy.assert_template_policy_coverage(
            _with_unnamed_sites(actual_inventory, [site]),
        )


@pytest.mark.parametrize("source,changed_source,kind,resolution,templates", [
    (
        'def probe():\n'
        '    request = LLMCompletionRequest()\n'
        '    return _effective_input_hash(request)\n',
        'def probe():\n'
        '    request = LLMCompletionRequest()\n'
        '    return complete_text(request)\n',
        "missing_keyword", "readonly_probe", [],
    ),
    (
        'def forward():\n'
        '    fields = {"prompt_template": "chapter_review"}\n'
        '    request = LLMCompletionRequest(**fields)\n'
        '    return complete_text(request)\n',
        'def forward():\n'
        '    fields = {"prompt_template": "chapter_review"}\n'
        '    fields.clear()\n'
        '    request = LLMCompletionRequest(**fields)\n'
        '    return complete_text(request)\n',
        "kwargs", "kwargs_named", ["chapter_review"],
    ),
], ids=["probe_drift", "kwargs_drift"])
def test_unnamed_registered_function_drift_requires_review(
    actual_inventory, tmp_path, scan_repository, monkeypatch,
    source, changed_source, kind, resolution, templates,
):
    before = _scan(tmp_path / "before", scan_repository, source)["unnamed_send_sites"]
    after = _scan(tmp_path / "after", scan_repository, changed_source)["unnamed_send_sites"]
    assert len(before) == len(after) == 1
    old, new = before[0], after[0]
    assert old["kind"] == new["kind"] == kind
    for key in _UNNAMED_IDENTITY[:-1]:
        assert old[key] == new[key], key
    assert old["expression_sha256"] != new["expression_sha256"]

    # Synthetic source is only parsed. Seal its original exact constructor and
    # function in a private registry; this never dispatches or grants a role.
    policy = json.loads((ROOT / "config/production_template_policy.json").read_text())
    policy["unnamed_request_sites"].append({
        **{key: old[key] for key in _UNNAMED_IDENTITY},
        "kind": kind, "resolution": resolution, "templates": templates,
        "reason": "Synthetic AST-only boundary fixture; exact pre-mutation source reviewed.",
    })
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(strategy, "_TEMPLATE_POLICY_PATH", path)
    strategy.assert_template_policy_coverage(_with_unnamed_sites(actual_inventory, before))
    with pytest.raises(ValueError, match="production_template_policy:unregistered_unnamed_request"):
        strategy.assert_template_policy_coverage(_with_unnamed_sites(actual_inventory, after))


def test_unnamed_whole_repository_has_exact_reviewed_explanations(actual_inventory):
    policy = json.loads((ROOT / "config/production_template_policy.json").read_text())
    registered = {
        tuple(site[key] for key in _UNNAMED_IDENTITY): site
        for site in policy["unnamed_request_sites"]
    }
    assert registered
    assert len(registered) == len(policy["unnamed_request_sites"])
    assert actual_inventory["unnamed_send_sites"]
    for site in actual_inventory["unnamed_send_sites"]:
        identity = tuple(site[key] for key in _UNNAMED_IDENTITY)
        assert identity in registered, identity
        entry = registered[identity]
        assert site["kind"] == entry["kind"], identity
        assert entry["reason"].strip(), identity
        if site["kind"] == "kwargs":
            assert entry["resolution"] == "kwargs_named", identity
            assert entry["templates"], identity
            assert all(name in policy["templates"] for name in entry["templates"]), identity
        else:
            assert site["kind"] == "missing_keyword", identity
            assert entry["resolution"] in {
                "readonly_probe", "legacy_readonly_reconstruction",
            }, identity
            assert entry["templates"] == [], identity
    strategy.assert_template_policy_coverage(actual_inventory)
