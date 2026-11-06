"""Adoption/resume regressions: real reducers/stage cache, memory files, no network."""

from contextlib import ExitStack, redirect_stdout


from copy import deepcopy


from dataclasses import replace


import io


import json


from pathlib import Path


import re


import unittest


from unittest.mock import patch


import yaml


from bookengine import client as client_module, q1, q1_plan, q1_chapter, q1_extract, q1_prepare, storage


from bookengine.client import Client


from bookengine.config import Book


from bookengine.polish_checks import scan as scan_continuity


from bookengine.q1_quality import merge_state, metrics


BODY = "“" + "我陪你回家" * 12 + "。”\n\n" + "她推开门" * 35 + "。"


CONTINUITY = {
    "blocked": ["天眼", "遁术"],
    "limbs": {"左手": "已废"},
    "balances": {"寿命": {"amount": 65, "unit": "天"}},
}


UNKNOWN_BOUNDARY = dict(opening_location=None, opening_quote=None, end_location=None, end_quote=None, present=None)


class LocalClient(Client):
    """Bypass credential setup; retain Client.stage and replace only HTTP request."""

    def __init__(self, out, replies=()):
        self.out, self.replies = out, dict(replies)
        self.calls, self.prefix, self.glossary = [], "", {}
        self.history_path = out / "calls.json"
        self.context_limit, self.clip_context, self.relaxed = 10000, False, True

    def request(self, stage, prompt, cap, effort, attempt):
        if stage not in self.replies:
            raise AssertionError("Unscripted model request: " + stage)
        self.calls.append(
            dict(
                seq=len(self.calls) + 1,
                stage=stage,
                status="ok",
                prompt=prompt,
                attempt=attempt,
                input_chars=len(prompt),
                input_bytes=len(prompt.encode()),
                output_token_cap=cap,
                usage=dict(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            )
        )
        value = self.replies[stage]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class PolishResumeTests(unittest.TestCase):
    def setUp(self):
        self.out = Path("/__polish_resume_memory__/book")
        self.files = {}
        self.book = Book("suspense-mystery", 1, 200, "本地回归")
        self.bible = dict(
            # Runtime/adoption fixture has no creative design; promise pipeline tests cover that contract.
            title="",
            characters=[dict(name="林宁", role="protagonist")],
            volumes=[dict(number=1, start=1, end=1)],
        )
        self.bible["canon"] = deepcopy(q1_plan.GROWTH["packs"][self.book.prompt_pack])
        self.bible["canon"]["hook_types"] = list(q1.PLAN_DATA["hook_types"])
        self.old = {"characters": {"林宁": {"location": "院", "present": True}}, "continuity": deepcopy(CONTINUITY)}
        self.put(q1.DATA, yaml.safe_dump({"b": "", "review": ""}, allow_unicode=True))
        self.put(self.out / "story_bible.yaml", yaml.safe_dump(self.bible, allow_unicode=True))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        real_read, real_exists, real_glob, real_open = Path.read_text, Path.exists, Path.glob, Path.open

        def virtual(path):
            return path.is_relative_to(self.out.parent)

        def read(path, *args, **kwargs):
            if path in self.files:
                return self.files[path]
            if virtual(path):
                raise FileNotFoundError(str(path))
            return real_read(path, *args, **kwargs)

        def guarded_open(path, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                raise AssertionError("Unexpected disk write: " + str(path))
            return real_open(path, mode, *args, **kwargs)

        def glob(path, pattern):
            if virtual(path):
                return iter(
                    sorted(p for p in self.files if p.is_relative_to(path) and p.relative_to(path).match(pattern))
                )
            return real_glob(path, pattern)

        for target, replacement in (
            ("read_text", read),
            ("open", guarded_open),
            ("glob", glob),
            ("exists", lambda p: p in self.files if virtual(p) else real_exists(p)),
        ):
            self.stack.enter_context(patch.object(Path, target, replacement))
        for module in (q1, q1_chapter, q1_extract, q1_prepare, client_module, storage):
            self.stack.enter_context(patch.object(module, "save", self.put))
        for target in (
            "socket.create_connection",
            "socket.socket.connect",
            "urllib.request.urlopen",
            "bookengine.client.Client.request",
        ):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Network forbidden")))
        # These are tested separately; this suite exercises adoption, not screening.
        self.stack.enter_context(patch.object(q1_extract, "scan", return_value=[]))
        self.stack.enter_context(patch.object(q1_extract, "locate", return_value=[]))
        # Checkup's real filesystem boundary is covered by test_book_checkup_finish.
        self.stack.enter_context(patch.object(q1, "write_checkup", return_value={}))

    def put(self, path, value):
        self.files[Path(path)] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    def get(self, path):
        return json.loads(self.files[Path(path)])

    def extracted(self, **extra):
        return dict(
            dict(
                type="日常",
                hook_type=q1.PLAN_DATA["hook_types"][0],
                payoff={"type": "无兑现", "fulfilled": False, "quote": ""},
                state={},
                edits=[],
            ),
            **extra,
        )

    def assert_unknown_handoff(self, row, *, previous_missing=False):
        missing = ["opening_location", "end_location"] + (["previous_end"] if previous_missing else [])
        self.assertEqual(
            row["handoff"],
            dict(
                status="handoff_unknown",
                issue=None,
                boundary_retried=True,
                missing=missing,
                opening_status="handoff_unknown",
                reference=dict(
                    end_location=None,
                    present=[],
                    end_quote=None,
                    viewpoint=None,
                    basis="previous_end",
                    known_locations=[],
                ),
            ),
        )
        for key in ("opening_location", "opening_quote", "end_location", "end_quote"):
            self.assertIsNone(row[key])
        self.assertEqual(row["present_status"], "unknown")
        self.assertEqual(row["present"], [])
        self.assertTrue(row["state"]["handoff_unknown"])
        self.assertNotIn("end_location", row["state"])

    def amend(self, reply, text="林宁留在院里。"):
        client = LocalClient(self.out)
        client.glossary = {"旧灯": {"definition": "用来照明", "first_chapter": 1}}
        before = deepcopy((self.old, self.bible, reply, self.files, client.glossary))
        candidates = [dict(id=i, categories=["adoption-fixture"]) for i, _ in enumerate(q1._sentences(text), 1)]
        with (
            patch.object(q1_extract, "locate", return_value=candidates),
            patch.object(q1_chapter, "ask", side_effect=AssertionError("amend must be pure")),
        ):
            body, detail = q1.amend(text, reply, self.bible, self.old, {"word_range": [0, 10000]})
        self.assertEqual((self.old, self.bible, reply, self.files, client.glossary), before)
        self.assertEqual(client.calls, [])
        self.assertFalse({"state", "summary", "terms", "advances", "events"} & detail.keys())
        return body, detail, client

    def proposed(self, edits):
        return dict(
            edits=edits,
            state={"characters": {"林宁": {"location": "码头"}}},
            summary="林宁到了码头。",
            advances=[dict(thread="暗线", event="已到码头", done=True)],
            events=[{"event": "已到码头"}],
            actual_payoff="拿到船票",
            terms=[dict(term="院", definition="未采用修订中的定义")],
            opens_new_mainline=True,
            reason="未采用的结尾",
            double_or_reawakening=True,
        )

    def assert_rejected(self, reply):
        body, detail, client = self.amend(reply)
        self.assertEqual(body, "林宁留在院里。")
        self.assertEqual(detail["edits"], [])
        self.assertTrue(detail["adoption_failed"])
        self.assertEqual(self.old["characters"]["林宁"]["location"], "院")
        self.assertEqual(self.old["continuity"], CONTINUITY)
        self.assertEqual(set(client.glossary), {"旧灯"})
        saved_bible = yaml.safe_load(self.files[self.out / "story_bible.yaml"])
        self.assertEqual(saved_bible, self.bible)

    def cached_row(self):
        return dict(
            number=1,
            title="旧章",
            status="complete",
            fact_source="final_body",
            chars=200,
            within_band=True,
            summary="林宁到了桥上。",
            seconds=9.5,
            state=merge_state(self.old, {"characters": {"林宁": {"location": "桥"}}}, self.bible),
            style=metrics(BODY),
            scene_types=["daily"],
            scenes=1,
            meeting_scenes=0,
            scene_classification="model",
            narrative_classification="model",
            payoff_observed=True,
            type="日常",
            hook_type=q1.PLAN_DATA["hook_types"][0],
            payoff="保住住处",
            hard_conflicts=0,
            issues=[],
            r2_events=[],
            polish={},
            dropped_fields=["林宁.desire"],
        )

    def resume(self, completed=0, old_quality=None):
        row = self.cached_row()
        meta = dict(
            mode="Q1",
            book=self.book.record(),
            style="b",
            status="interrupted",
            started=storage.now(),
            completed_chapters=completed,
            execution_seconds=321,
            revision=dict(name=q1.REVISION, started=storage.now(), base_chapters=0, base_seconds=0),
        )
        if old_quality is not None:
            meta["quality"] = deepcopy(old_quality)
        self.put(self.out / "run.json", meta)
        self.put(self.out / "chapters/001.json", row)
        self.put(self.out / "chapter-001.body.md", BODY)
        self.put(self.out / "state.json", row["state"] if completed else self.old)
        historical = [
            dict(
                seq=1,
                stage="legacy-draft",
                status="ok",
                attempt=1,
                input_chars=100,
                usage=dict(prompt_tokens=17, completion_tokens=23, total_tokens=40),
            )
        ]
        self.put(self.out / "calls.json", historical)
        client = LocalClient(self.out)
        client.calls = deepcopy(historical)
        old_body = self.files[self.out / "chapter-001.body.md"]
        with ExitStack() as stack, redirect_stdout(io.StringIO()):
            stack.enter_context(patch.object(q1, "QClient", return_value=client))
            stack.enter_context(patch.object(q1, "prepare", return_value=(self.bible, {"threads": []}, {})))
            stack.enter_context(patch.object(q1, "volume", return_value=dict(number=1, start=1, end=1)))
            stack.enter_context(patch.object(q1, "outlines", return_value=[{"number": 1}]))
            chapter = stack.enter_context(patch.object(q1, "chapter", wraps=q1.chapter))
            for module in (q1_chapter, q1_extract):
                stack.enter_context(
                    patch.object(module, "ask", side_effect=AssertionError("Cached resume must not request"))
                )
            export = stack.enter_context(patch.object(q1, "export"))
            result = q1._run(self.book, self.out, None, self.out.parent / "assets", 180, "b", lambda: False)
        self.assertEqual(client.calls, historical)
        self.assertEqual(self.get(self.out / "calls.json"), historical)
        self.assertEqual(self.files[self.out / "chapter-001.body.md"], old_body)
        self.assertEqual(self.get(self.out / "chapters/001.json")["seconds"], row["seconds"])
        self.assertGreaterEqual(result["execution_seconds"], 321)
        self.assertEqual(export.call_args.args[3], [BODY])
        return result, row, chapter

    def seed_extension(self):
        bible = dict(
            self.bible,
            volumes=[dict(number=1, start=1, end=25)],
            characters=[
                dict(name="林宁", role="protagonist", voice="短句直说", desire="救回姐姐"),
                dict(name="周远舟", role="support", voice="总爱反问", desire="保住药铺"),
                dict(name="赵听禾", role="antagonist", voice="慢声威胁", desire="夺下城门"),
            ],
        )
        self.put(self.out / "story_bible.yaml", yaml.safe_dump(bible, allow_unicode=True))
        self.put(self.out / "threads.yaml", yaml.safe_dump({"threads": []}))
        self.put(self.out / "state.json", self.old)
        original = self.files[self.out / "story_bible.yaml"]
        return original

    def continuation(self, target):
        return {
            "bible": {
                "volumes": [
                    dict(number=n, start=(n - 1) * 25 + 1, end=min(n * 25, target))
                    for n in range(1, (target + 24) // 25 + 1)
                ]
            }
        }
