"""Offline contracts only; no DB, model, business mocks or native receipts."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import httpx

from e2e_sim.transport import NativeObservation, ScriptedTransportError, Tape
from e2e_sim.tapes import private_fixture_body
from e2e_sim.whole_book_tapes import (
    COVERAGE,
    BindingRequiredError,
    ScriptError,
    _private_paths,
    assert_consumed,
    document,
    example_script,
    full_book_script,
    manifest,
    text_response,
    tool_response,
)


class WholeBookTapeTests(unittest.TestCase):
    def setUp(self):
        self.script = example_script()

    def test_example_compiles_every_chapter_through_book_audit(self):
        value = document(self.script, env={})
        tape = Tape(value)
        self.assertEqual(value["schema"], "v3-scripted-provider-tape.v1")
        self.assertTrue(value["simulation_only"])
        counts = manifest(self.script)["template_calls"]
        self.assertEqual(counts["chapter_first_writer"], self.script["chapter_count"])
        self.assertEqual(counts["bible_update_v1"], self.script["chapter_count"])
        self.assertEqual(counts["project_consistency_review"], 1)
        self.assertEqual(len(tape.document["entries"]), sum(counts.values()))

    def test_unknown_template_is_script_error_without_payload_disclosure(self):
        self.script["calls"].append(
            {
                "phase": "book_audit",
                "template": "SECRET_UNKNOWN",
                "response": text_response("SECRET_BODY"),
            }
        )
        with self.assertRaisesRegex(ScriptError, "script_unknown_template") as caught:
            document(self.script, env={})
        self.assertNotIn("SECRET", str(caught.exception))

    def test_no_missing_chapter_or_book_audit_is_accepted(self):
        for template in (
            "chapter_first_writer",
            "chapter_review",
            "bible_update_v1",
            "project_consistency_review",
        ):
            script = deepcopy(self.script)
            script["calls"] = [c for c in script["calls"] if c["template"] != template]
            with self.subTest(template=template), self.assertRaises(ScriptError):
                document(script, env={})

    def test_chapter_numbers_and_phase_cannot_hide_missing_coverage(self):
        for update in (
            {"chapter_number": True},
            {"chapter_number": 0},
            {"chapter_number": 99},
            {"phase": "book_audit"},
        ):
            script = deepcopy(self.script)
            script["calls"][0].update(update)
            with self.subTest(update=update), self.assertRaises(ScriptError):
                document(script, env={})

    def test_exact_template_ordinals_count_physical_sends_and_do_not_repeat(self):
        # An explicitly scripted extra critic sample gets the next ordinal.
        sample = deepcopy(
            next(
                c
                for c in self.script["calls"]
                if c["template"] == "chapter_commercial_quality_judge"
            )
        )
        self.script["calls"].insert(2, sample)
        value = document(self.script, env={})
        matches = [
            e["match"]
            for e in value["entries"]
            if e["match"]["prompt_template"] == sample["template"]
        ]
        self.assertEqual([m["call_index"] for m in matches], list(range(1, len(matches) + 1)))
        self.assertTrue(all(m["project_id"] == self.script["project_id"] for m in matches))
        self.assertTrue(all(len(e["responses"]) == 1 for e in value["entries"]))

    def test_actual_tape_boundary_rejects_unknown_overrun_and_wrong_project(self):
        value = document(self.script, env={})
        request = httpx.Request(
            "POST",
            "https://par-p3.invalid/v1/chat/completions",
            json={"model": "offline", "messages": []},
        )
        match = value["entries"][0]["match"]
        for observation in (
            NativeObservation("uncovered_template", 1, self.script["project_id"]),
            NativeObservation(match["prompt_template"], 999, self.script["project_id"]),
            NativeObservation(match["prompt_template"], 1, "00000000-0000-0000-0000-000000000001"),
        ):
            tape = Tape(
                value, template_observer=lambda _r, _c, observation=observation: observation
            )
            with self.subTest(observation=observation), self.assertRaises(ScriptedTransportError):
                tape.handle(request)
            self.assertEqual(tape.events[-1]["event"], "harness_UNMAPPED")

    def test_consumption_requires_all_exact_sends_and_no_harness_errors(self):
        value = document(self.script, env={})
        tape = Tape(value)
        with self.assertRaisesRegex(ScriptError, "script_unconsumed_calls"):
            assert_consumed(tape)
        # Drive the actual transport with observed metadata; no business functions patched.
        request = httpx.Request(
            "POST",
            "https://par-p3.invalid/v1/chat/completions",
            json={"model": "offline", "messages": []},
        )
        for entry in value["entries"]:
            m = entry["match"]
            tape.template_observer = lambda _r, _c, m=m: NativeObservation(
                m["prompt_template"], m["call_index"], m["project_id"]
            )
            tape.handle(request)
        assert_consumed(tape)
        with self.assertRaises(ScriptedTransportError):
            tape.handle(request)
        with self.assertRaisesRegex(ScriptError, "script_transport_errors"):
            assert_consumed(tape)

    def test_tool_template_never_accepts_free_text_or_wrong_tool(self):
        call = next(c for c in self.script["calls"] if c["template"] == "chapter_review")
        for response in (
            text_response("pass"),
            tool_response("wrong_function", {"verdict": "pass"}),
        ):
            call["response"] = response
            with (
                self.subTest(response=response),
                self.assertRaisesRegex(ScriptError, "script_tool_submission_required"),
            ):
                document(self.script, env={})

    def test_source_bound_optional_template_requires_authored_response(self):
        self.assertIn("planning_draft_admission", COVERAGE)
        self.assertIn("draft_continuity_hard_safety", COVERAGE)
        self.script["calls"].insert(
            0, {"phase": "planning", "template": "planning_draft_admission"}
        )
        with self.assertRaises(ScriptError):
            document(self.script, env={})

    def test_capture_hash_and_counts_required_and_no_nan_or_duplicate_json(self):
        for update in (
            {"prefix_plan_sha256": "bad"},
            {"chapter_count": True},
            {"project_id": "bad"},
            {"unrecognized": "SECRET_BODY"},
        ):
            script = {**deepcopy(self.script), **update}
            with self.subTest(update=update), self.assertRaises(ScriptError):
                document(script, env={})
        call = next(c for c in self.script["calls"] if c["template"] == "chapter_state_snapshot")
        for raw in ('{"facts": [], "facts": []}', '{"facts": [], "x": NaN}'):
            call["response"]["content"] = raw
            with self.subTest(raw=raw), self.assertRaises(ScriptError):
                document(self.script, env={})

    def test_manifest_is_metadata_only_and_detached(self):
        report = manifest(self.script)
        encoded = json.dumps(report)
        self.assertNotIn('"content":', encoded)
        self.assertNotIn('"tool_calls":', encoded)
        self.assertEqual(report["official_export"], "native_only_no_model_response")
        self.assertFalse(report["integration_passed"])
        value = document(self.script, env={})
        value["entries"][0]["responses"][0]["content"] = "CHANGED"
        self.assertNotEqual(self.script["calls"][0]["response"]["content"], "CHANGED")

    def test_manifest_hash_binds_the_compiled_runtime_routes(self):
        import hashlib

        env = {"BESTSELLER__LLM__WRITER__API_BASE": "https://offline.invalid/custom/v1"}
        value = document(self.script, env=env)
        report = manifest(self.script, env=env)
        canonical = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        self.assertEqual(report["tape_sha256"], hashlib.sha256(canonical).hexdigest())

    def test_original_routes_are_exact_and_no_fake_export_model_call(self):
        value = document(
            self.script,
            env={"BESTSELLER__LLM__WRITER__API_BASE": "https://offline.invalid/custom/v1"},
        )
        self.assertEqual(value["allowed_endpoints"][0]["path"], "/custom/v1/chat/completions")
        self.assertNotIn("official_export", COVERAGE)

    def test_full_profile_reports_missing_bound_submissions_without_auto_pass(self):
        body = private_fixture_body()
        with self.assertRaises(BindingRequiredError) as caught:
            full_book_script(
                project_id=self.script["project_id"],
                prefix_plan_sha256=self.script["prefix_plan_sha256"],
                chapter_bodies=[body],
                bound_responses={},
            )
        self.assertIn("fanqie_short_semantic_review:1", caught.exception.missing)
        self.assertIn("fanqie_short_title_semantic_judge:1", caught.exception.missing)
        self.assertIn("chapter_commercial_quality_judge:1", caught.exception.missing)
        self.assertIn("litstyle_prose_judge:2", caught.exception.missing)

    def test_private_paths_reject_symlink_ancestors_permissions_and_existing_output(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            script, output = root / "script.json", root / "tape.json"
            script.write_text("{}")  # Path guard input only, never a usable response.
            os.chmod(script, 0o600)
            _private_paths(script, output, root)
            for mode in (0o644, 0o640):
                os.chmod(script, mode)
                with self.assertRaisesRegex(ScriptError, "private_input_permissions"):
                    _private_paths(script, output, root)
            os.chmod(script, 0o600)
            output.touch()
            with self.assertRaisesRegex(ScriptError, "output_exists"):
                _private_paths(script, output, root)
            output.unlink()
            link = root / "linked"
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ScriptError, "private_symlink"):
                _private_paths(link / "script.json", output, root)
            with self.assertRaisesRegex(ScriptError, "outside_private_root"):
                _private_paths(script, root.parent / "tape.json", root)
            repo = Path(__file__).resolve().parents[2]
            with self.assertRaisesRegex(ScriptError, "external_private_root"):
                _private_paths(repo / "script.json", repo / "tape.json", repo)

    def test_real_native_primary_and_commercial_submission_validators(self):
        # Pure native functions: no DB/session/service/gateway/adoption patches.
        from bestseller.domain.feedback import ChapterFeedbackPayload
        from bestseller.services.commercial_review_tool import submitted_review_payload
        from bestseller.services.commercial_review_wire import parse_commercial_wire
        from bestseller.services.commercial_source_evidence import assemble_commercial_sources
        from bestseller.services.primary_review_submission import parse_primary_review_submission

        body = private_fixture_body()
        specs = {c["template"]: c["response"] for c in self.script["calls"]}
        primary = deepcopy(specs["chapter_review"])
        primary["tool_calls"][0]["id"] = "synthetic-physical-call"
        completion = SimpleNamespace(**primary, raw_message=None)
        _text, receipt, errors = parse_primary_review_submission(completion, body)
        self.assertEqual(errors, ())
        self.assertEqual(receipt["status"], "verified")
        commercial = SimpleNamespace(**specs["chapter_commercial_quality_judge"])
        raw, _receipt, errors = submitted_review_payload(commercial)
        self.assertEqual(errors, ())
        sources = assemble_commercial_sources(
            chapter_number=1, content_md=body, source={}, previous_chapters=[]
        )
        _value, receipt, errors = parse_commercial_wire(raw, sources)
        self.assertEqual(errors, ())
        self.assertEqual(receipt["status"], "verified")
        # Exact authored constraints change native required checks; the sample
        # must be rejected instead of getting a fabricated bound PASS.
        sources = assemble_commercial_sources(
            chapter_number=1,
            content_md=body,
            source={
                "approved_creation_contracts": [
                    {"hard": True, "source": "user", "constraint": "synthetic constraint"}
                ]
            },
            previous_chapters=[],
        )
        self.assertTrue(any(row["kind"] == "constraint" for row in sources.catalog.values()))
        _value, _receipt, errors = parse_commercial_wire(raw, sources)
        self.assertTrue(errors)
        payload = ChapterFeedbackPayload.model_validate_json(
            specs["chapter_feedback_extraction"]["content"]
        )
        self.assertEqual(payload.canon_facts, [])


if __name__ == "__main__":
    unittest.main()
