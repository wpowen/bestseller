"""Finite, literal provider scripts for complete-plan -> whole-book export.

Only wire responses are synthetic. No service, reader, review parser, adoption,
approval, knowledge closure or exporter is replaced. Source-bound submissions
must be authored explicitly in the private input script; never infer a passing
grade, source ID, hash, quotation or repair from a request/tool schema.

The example loads the EXISTING synthetic fixture. It is a transport example,
not a source-conditioned manuscript or proof that production gates will pass.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID

from e2e_sim.tapes import SCORE_FIELDS, endpoints_from_environment, private_fixture_body
from e2e_sim.transport import TAPE_SCHEMA, Tape

SCRIPT_SCHEMA = "whole-book-script.v1"
PROJECT_ID = "fa14e681-2bd9-44f5-93d1-7f2ef9900d69"
PRIVATE_ROOT = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-par-private/P3")

# Explicit production protocol inventory, not a registry of auto-pass factories.
# Each optional/repair call still needs its own literal private response. Registry
# membership does not authorize a dispatch or make an unscripted call succeed.
# phase, native source module, format, tool name (when applicable).
COVERAGE = {}


def _protocol(phase, source, kind, names, tool=None):
    for name in names.split():
        COVERAGE[name] = {"phase": phase, "source": source, "format": kind, "tool": tool}


_protocol(
    "planning",
    "planning_draft_admission.py",
    "tool",
    "planning_draft_admission",
    "submit_planning_admission",
)
_protocol("planning", "prewrite_review.py", "text", "prewrite_material_repair")
_protocol(
    "planning",
    "chapter_outline_readiness.py",
    "json",
    "continuation_outline_rebase continuation_source_hard_repair "
    "chapter_outline_prefix_surgical_repair",
)
_protocol("chapter", "drafts.py", "text", "chapter_first_writer scene_writer scene_writer_regen")
_protocol("chapter", "drafts.py", "json", "prewrite_plan_manifest content_validation")
_protocol("chapter", "reviews.py", "tool", "chapter_review", "submit_chapter_critique")
_protocol(
    "chapter",
    "reviews.py",
    "text",
    "scene_review chapter_rewrite chapter_rewrite_repair "
    "chapter_rewrite_quality_retrofit_repair chapter_localized_opening_repair "
    "chapter_localized_ending_repair scene_rewrite",
)
_protocol(
    "chapter",
    "chapter_llm_quality_judge.py",
    "tool",
    "chapter_commercial_quality_judge",
    "submit_chapter_review",
)
_protocol(
    "chapter",
    "draft_continuity.py",
    "tool",
    "draft_continuity_hard_safety",
    "submit_draft_continuity_safety",
)
_protocol("chapter", "chapter_continuity_critic.py", "json", "chapter_continuity_critic")
_protocol("chapter", "chapter_window_quality_judge.py", "json", "chapter_window_quality_judge")
_protocol("chapter", "volume_quality_judge.py", "json", "volume_quality_checkpoint_judge")
_protocol("chapter", "reader_judge.py", "json", "reader_judge")
_protocol("chapter", "ideology_judge.py", "json", "ideology_judge")
_protocol("chapter", "world_law_consistency_gate.py", "json", "world_law_consistency")
_protocol(
    "chapter",
    "independent_quality_judge.py",
    "json",
    "independent_quality_judge independent_quality_judge_json_repair",
)
_protocol("chapter", "voice_drift.py", "json", "voice_drift_check voice_drift_check_repair")
_protocol("chapter", "story_engine_review.py", "json", "story_engine_transition_receipt")
_protocol(
    "knowledge",
    "feedback.py",
    "json",
    "chapter_feedback_extraction chapter_feedback_extraction_repair",
)
_protocol(
    "knowledge", "continuity.py", "json", "chapter_state_snapshot chapter_state_snapshot_repair"
)
_protocol(
    "knowledge",
    "story_bible.py",
    "json",
    "bible_update_v1 bible_update_v1_repair scene_bible_delta_v1",
)
_protocol("knowledge", "knowledge.py", "text", "scene_knowledge_summary")
_protocol("knowledge", "summarization.py", "text", "rolling_summary")
_protocol(
    "knowledge",
    "knowledge_evidence_reconciliation.py",
    "json",
    "feedback_evidence_reconciliation feedback_evidence_coverage_repair",
)
_protocol("book_audit", "consistency.py", "text", "project_consistency_review")
_protocol("book_audit", "litstyle_prose_judge.py", "json", "litstyle_prose_judge")
# Whole-story deslop uses the existing scene_writer name, so it is also allowed
# in book_audit. Match ordinals are shared with chapter scene_writer sends.
_protocol("book_audit", "fanqie_short_semantic_review.py", "json", "fanqie_short_semantic_review")
_protocol("book_audit", "fanqie_long_semantic_review.py", "json", "fanqie_long_semantic_review")
_protocol(
    "book_audit",
    "fanqie_short_semantic_evidence_selection.py",
    "tool",
    "fanqie_short_semantic_evidence_selection",
    "select_whole_semantic_evidence",
)
_protocol(
    "book_audit",
    "fanqie_short_title_semantic_judge.py",
    "tool",
    "fanqie_short_title_semantic_judge",
    "judge_current_publication_title",
)
_protocol("book_audit", "reader_panel_judge.py", "json", "reader_panel_judge")
_protocol("book_audit", "causal_attribution.py", "json", "causal_attribution")
_protocol("book_audit", "artifact_health_audit.py", "json", "artifact_health_audit")
_protocol("book_audit", "book_quality_closure.py", "text", "closure_preflight")
_protocol("book_audit", "book_ending_recovery.py", "tool", "book_ending", "submit_book_ending")

REQUIRED_CHAPTER = frozenset(
    {
        "chapter_first_writer",
        "chapter_commercial_quality_judge",
        "chapter_review",
        "chapter_feedback_extraction",
        "chapter_state_snapshot",
        "bible_update_v1",
    }
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class ScriptError(ValueError):
    """Safe fixed code; never include authored prose/prompt/provider arguments."""


class BindingRequiredError(ScriptError):
    """Unresolved literal source-bound outputs; safe metadata for main's handoff."""

    def __init__(self, missing):
        self.missing = tuple(missing)
        super().__init__("script_source_bound_responses_required")


def _need(condition, code):
    if not condition:
        raise ScriptError("script_" + code)


def _unique(pairs):
    value = {}
    for key, child in pairs:
        _need(key not in value, "duplicate_json_key")
        value[key] = child
    return value


def _nonfinite(_):
    raise ScriptError("script_nonfinite_json")


def _loads(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
    except ScriptError:
        raise
    except (ValueError, TypeError, UnicodeError):
        raise ScriptError("script_invalid_json") from None


def _canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (ValueError, TypeError, OverflowError):
        raise ScriptError("script_non_json_value") from None


def text_response(content):
    _need(type(content) is str and bool(content.strip()), "empty_response")
    return {
        "mode": "auto",
        "content": content,
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
    }


def json_response(payload):
    _need(isinstance(payload, (dict, list)) and bool(payload), "empty_json_response")
    return text_response(_canonical(payload))


def tool_response(name, arguments):
    _need(type(arguments) is dict and bool(arguments), "empty_tool_arguments")
    return {
        **text_response("synthetic structured submission"),
        "content": "",
        "finish_reason": "tool_calls",
        "tool_calls": [
            {"type": "function", "function": {"name": name, "arguments": _canonical(arguments)}}
        ],
    }


def _check_response(template, spec):
    _need(type(spec) is dict, "response_required")
    try:
        Tape._validate_spec(spec)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ScriptError("script_response_protocol") from None
    # Fault injections are explicit physical outcomes, never repaired to stop.
    if spec.get("mode") in {"timeout", "disconnect"} or spec.get("finish_reason") in {
        "length",
        "content_filter",
    }:
        return
    protocol = COVERAGE[template]
    if protocol["format"] == "tool":
        calls = spec.get("tool_calls")
        _need(
            spec.get("finish_reason") == "tool_calls"
            and type(calls) is list
            and len(calls) == 1
            and calls[0]["function"]["name"] == protocol["tool"],
            "tool_submission_required",
        )
        payload = _loads(calls[0]["function"]["arguments"])
        _need(type(payload) is dict and bool(payload), "empty_tool_arguments")
    else:
        _need(
            not spec.get("tool_calls") and spec.get("finish_reason", "stop") == "stop",
            "plain_response_required",
        )
        content = spec.get("content")
        _need(type(content) is str and bool(content.strip()), "empty_response")
        if protocol["format"] == "json":
            payload = _loads(content)
            _need(isinstance(payload, (dict, list)) and bool(payload), "empty_json_response")


def document(script, *, env=None):
    """Compile literal authored calls to the existing strict Tape format.

    List order assigns per-template physical-send ordinals; it is not a global
    scheduling assertion. Parallel calls of the same template must have an
    explicitly determined order, or the driver must serialize that cohort.
    """
    _need(
        type(script) is dict
        and set(script)
        == {
            "schema",
            "simulation_only",
            "project_id",
            "prefix_plan_sha256",
            "chapter_count",
            "calls",
        },
        "manifest_fields",
    )
    _need(
        script["schema"] == SCRIPT_SCHEMA and script["simulation_only"] is True, "manifest_schema"
    )
    project_id = script["project_id"]
    try:
        _need(type(project_id) is str and str(UUID(project_id)) == project_id, "project_uuid")
    except (ValueError, TypeError, AttributeError):
        raise ScriptError("script_project_uuid") from None
    _need(
        type(script["prefix_plan_sha256"]) is str
        and _HEX.fullmatch(script["prefix_plan_sha256"]) is not None,
        "prefix_plan_sha256",
    )
    count = script["chapter_count"]
    _need(type(count) is int and count > 0, "chapter_count")
    calls = script["calls"]
    _need(type(calls) is list and bool(calls), "calls_required")
    _canonical(script)  # Also reject NaN in nested tool outputs/usage.
    counts, chapter_templates, entries = Counter(), {}, []
    audit_templates = set()
    for call in calls:
        _need(
            type(call) is dict
            and {"phase", "template", "response"} <= set(call)
            and not set(call) - {"phase", "template", "response", "chapter_number"},
            "call_fields",
        )
        template = call["template"]
        _need(type(template) is str and template in COVERAGE, "unknown_template")
        phase = call["phase"]
        _need(
            phase == COVERAGE[template]["phase"]
            or (template == "scene_writer" and phase == "book_audit")
            or (template == "litstyle_prose_judge" and phase == "chapter"),
            "template_phase",
        )
        if phase in {"chapter", "knowledge"}:
            number = call.get("chapter_number")
            _need(type(number) is int and 1 <= number <= count, "chapter_number")
            chapter_templates.setdefault(number, set()).add(template)
        else:
            _need("chapter_number" not in call, "nonchapter_number")
        if phase == "book_audit":
            audit_templates.add(template)
        _check_response(template, call["response"])
        counts[template] += 1
        index = counts[template]
        entries.append(
            {
                "id": f"whole-book:{template}:{index}",
                "match": {
                    "project_id": project_id,
                    "prompt_template": template,
                    "call_index": index,
                },
                "responses": [deepcopy(call["response"])],
            }
        )
    _need(set(chapter_templates) == set(range(1, count + 1)), "missing_chapter")
    _need(
        all(REQUIRED_CHAPTER <= chapter_templates[n] for n in chapter_templates),
        "missing_chapter_template",
    )
    _need("project_consistency_review" in audit_templates, "missing_book_audit")
    value = {
        "schema": TAPE_SCHEMA,
        "simulation_only": True,
        "allowed_endpoints": endpoints_from_environment(os.environ if env is None else env),
        "entries": entries,
    }
    try:
        Tape(value)
    except (ValueError, TypeError, KeyError):
        raise ScriptError("script_tape_protocol") from None
    return value


def manifest(script, *, env=None):
    """Safe metadata; source bodies and tool arguments never enter reports."""
    value = document(script, env=env)
    counts = Counter(c["template"] for c in script["calls"])
    return {
        "schema": SCRIPT_SCHEMA,
        "simulation_only": True,
        "project_id": script["project_id"],
        "prefix_plan_sha256": script["prefix_plan_sha256"],
        "chapter_count": script["chapter_count"],
        "template_calls": dict(sorted(counts.items())),
        "unscripted_templates": sorted(set(COVERAGE) - set(counts)),
        "tape_sha256": hashlib.sha256(_canonical(value).encode()).hexdigest(),
        "official_export": "native_only_no_model_response",
        "integration_passed": False,
    }


def assert_consumed(tape):
    """Single-sender/partitioned tape only, after all streams settle.

    This checks script consumption only. The parent's native evidence reader
    must separately prove book completeness, adopted sources and official export.
    Never apply to each process holding a copy of a global API/worker tape.
    For that topology main must globally aggregate actual send entry identities,
    reject duplicates and verify each expected physical send exactly once. No
    per-process observer ordinal may be treated as a global ordinal.
    """
    _need(not any(e.get("event") == "harness_UNMAPPED" for e in tape.events), "transport_errors")
    _need(
        all(tape.used[e["id"]] == len(e["responses"]) for e in tape.document["entries"]),
        "unconsumed_calls",
    )


def full_book_script(
    *,
    project_id,
    prefix_plan_sha256,
    chapter_bodies,
    bound_responses,
    additional_calls=(),
    commercial_samples=1,
    chapter_litstyle_samples=1,
    whole_litstyle_samples=1,
):
    """Concrete finite chapter-first short-book plan, or explicit binding blocker.

    `chapter_bodies` is private authored input, never committed prose. Each key
    in bound_responses is 'template:physical_send_index', each value an exact
    response spec. No schema-generated pass or default empty extraction exists.

    This strict profile includes chapter reviews/knowledge, chapter and whole
    litstyle, whole semantic review, evidence selection and title assessment.
    Selection/title are explicit separate driver operations, NOT automatic
    exporter calls. Main can use `document` for its different enabled schedule.
    Conditional repairs/scene mode/etc are literal additional_calls (phase,
    template, chapter_number when applicable), never automatic retry reserves.
    """
    _need(type(chapter_bodies) in (list, tuple) and bool(chapter_bodies), "chapter_bodies")
    _need(
        all(type(body) is str and bool(body.strip()) for body in chapter_bodies), "chapter_bodies"
    )
    _need(type(bound_responses) is dict, "bound_responses")
    for count in (commercial_samples, chapter_litstyle_samples, whole_litstyle_samples):
        _need(type(count) is int and count > 0, "sample_count")
    plan = []
    for number, body in enumerate(chapter_bodies, 1):
        for template, samples in (
            ("chapter_first_writer", 1),
            ("chapter_commercial_quality_judge", commercial_samples),
            ("chapter_review", 1),
            ("litstyle_prose_judge", chapter_litstyle_samples),
            ("chapter_feedback_extraction", 1),
            ("chapter_state_snapshot", 1),
            ("bible_update_v1", 1),
        ):
            for _ in range(samples):
                call = {
                    "phase": "chapter"
                    if template == "litstyle_prose_judge"
                    else COVERAGE[template]["phase"],
                    "template": template,
                    "chapter_number": number,
                }
                if template == "chapter_first_writer":
                    call["response"] = text_response(body)
                plan.append(call)
    for template, samples in (
        ("project_consistency_review", 1),
        ("litstyle_prose_judge", whole_litstyle_samples),
        ("fanqie_short_semantic_review", 1),
        ("fanqie_short_semantic_evidence_selection", 1),
        ("fanqie_short_title_semantic_judge", 1),
    ):
        for _ in range(samples):
            plan.append({"phase": "book_audit", "template": template})
    # Preserve explicitly authored order for extra conditional calls; if a
    # repair interleaves with normal samples, author the entire literal script
    # through document() instead of guessing its relative ordinal here.
    for call in additional_calls:
        _need(type(call) is dict and "response" not in call, "additional_call_fields")
        plan.append(deepcopy(call))
    seen, missing, consumed = Counter(), [], set()
    for call in plan:
        template = call.get("template")
        _need(type(template) is str and template in COVERAGE, "unknown_template")
        seen[template] += 1
        key = f"{template}:{seen[template]}"
        if key in bound_responses:
            _need("response" not in call, "writer_response_override")
            call["response"] = deepcopy(bound_responses[key])
            consumed.add(key)
        elif "response" not in call:
            missing.append(key)
    _need(not set(bound_responses) - consumed, "unused_bound_response")
    if missing:
        raise BindingRequiredError(missing)
    script = {
        "schema": SCRIPT_SCHEMA,
        "simulation_only": True,
        "project_id": project_id,
        "prefix_plan_sha256": prefix_plan_sha256,
        "chapter_count": len(chapter_bodies),
        "calls": plan,
    }
    document(script, env={})
    return script


def example_script(chapter_count=4):
    """Standalone finite transport sample using only the inherited fixture.

    Repeated prose intentionally does not assert literary/continuity acceptance.
    No production source-bound IDs, semantic binding or approval is fabricated.
    Main must author its actual chapter responses in its private script.
    """
    _need(type(chapter_count) is int and chapter_count > 0, "chapter_count")
    body = private_fixture_body()
    from e2e_sim.tapes import document as chapter_document

    base = chapter_document()["entries"]
    primary = deepcopy(
        next(e["responses"][0] for e in base if e["match"]["prompt_template"] == "chapter_review")
    )
    # Keep native score vocabulary and authored citations from the inherited
    # synthetic tape; no schema-derived pass fabrication or business receipts.
    _need(
        set(_loads(primary["tool_calls"][0]["function"]["arguments"])["scores"])
        == set(SCORE_FIELDS),
        "example_score_schema",
    )
    dimensions = (
        "opening_pull readability commercial_pull character_agency decision_intelligence "
        "character_voice_distinction scene_execution continuity methodology_compliance "
        "hook_strength knowledge_boundary real_world_plausibility object_signal_logic "
        "call_plausibility capability_demonstrated material_advancement_score"
    ).split()
    commercial = tool_response(
        "submit_chapter_review",
        {
            "wire_version": "commercial-review-wire.v2",
            "pass": True,
            "overall_score": 0.88,
            "dimension_scores": dict.fromkeys(dimensions, 0.88),
            "issues": [],
            "rewrite_plan": {"scope": "chapter", "preserve": [], "change": [], "instructions": ""},
        },
    )
    feedback = {
        name: []
        for name in (
            "character_states relationship_events arc_beat_updates clue_observations world_details "
            "canon_facts promises_made timeline_events validation_diagnostics"
        ).split()
    }
    calls = []
    for number in range(1, chapter_count + 1):
        for template, spec in (
            ("chapter_first_writer", text_response(body)),
            ("chapter_commercial_quality_judge", commercial),
            ("chapter_review", primary),
            ("chapter_feedback_extraction", json_response(feedback)),
            ("chapter_state_snapshot", json_response({"facts": []})),
            (
                "bible_update_v1",
                json_response(
                    {
                        "character_updates": [],
                        "relationship_updates": [],
                        "world_updates": [],
                    }
                ),
            ),
        ):
            calls.append(
                {
                    "phase": COVERAGE[template]["phase"],
                    "chapter_number": number,
                    "template": template,
                    "response": deepcopy(spec),
                }
            )
    calls.append(
        {
            "phase": "book_audit",
            "template": "project_consistency_review",
            "response": text_response(
                "Synthetic commentary; native findings remain authoritative."
            ),
        }
    )
    return {
        "schema": SCRIPT_SCHEMA,
        "simulation_only": True,
        "project_id": PROJECT_ID,
        # Hash of synthetic fixture bytes, explicitly NOT a captured reader receipt.
        "prefix_plan_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "chapter_count": chapter_count,
        "calls": calls,
    }


def _private_paths(script, output, root):
    """No body into repo/reports, symlink ancestors or broadly readable input."""
    repo = Path(__file__).resolve().parents[2]
    _need(
        root.is_absolute() and script.is_absolute() and output.is_absolute(),
        "private_absolute_paths",
    )
    for path in (root, script, output):
        _need(".." not in path.parts, "private_parent_traversal")
        _need(not any(part.is_symlink() for part in (path, *path.parents)), "private_symlink")
    _need(
        root.is_dir()
        and not root.resolve().is_relative_to(repo)
        and not repo.is_relative_to(root.resolve()),
        "external_private_root",
    )
    # A custom root must also be outside the enclosing production checkout.
    production_root = (
        repo.parents[2]
        if repo.parent.name == "par-worktrees" and repo.parent.parent.name == "scratch"
        else repo
    )
    _need(not root.resolve().is_relative_to(production_root), "external_private_root")
    for path in (script, output):
        _need(path != root and path.is_relative_to(root), "outside_private_root")
        parent = path.parent
        while True:
            info = parent.stat()
            _need(
                stat.S_ISDIR(info.st_mode)
                and stat.S_IMODE(info.st_mode) == 0o700
                and info.st_uid == os.getuid(),
                "private_directory_permissions",
            )
            if parent == root:
                break
            parent = parent.parent
    info = script.stat()
    _need(
        stat.S_ISREG(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_uid == os.getuid(),
        "private_input_permissions",
    )
    _need(not output.exists(), "output_exists")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--script",
        type=Path,
        required=True,
        help="Private literal whole-book script JSON; no inferred responses",
    )
    parser.add_argument("--output", type=Path, required=True, help="New private tape JSON (0600)")
    parser.add_argument(
        "--private-root",
        type=Path,
        default=PRIVATE_ROOT,
        help="Explicit external private root (0700); defaults to P3's private root",
    )
    args = parser.parse_args(argv)
    try:
        _private_paths(args.script, args.output, args.private_root)
        script = _loads(args.script.read_bytes())
        value = document(script)
        # Main owns private paths/directories. Never overwrite an existing tape.
        _need(args.output.is_absolute() and args.output.parent.is_dir(), "absolute_output_required")
        _need(not args.output.is_symlink(), "output_symlink")
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(_canonical(value) + "\n")
        print(_canonical(manifest(script)))
        return 0
    except ScriptError as error:
        print(str(error))
    except (OSError, ValueError, TypeError):
        print("script_private_file_error")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
