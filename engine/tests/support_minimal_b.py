"""Exercise r2 prompt packing, local amendments and saved facts without HTTP."""

from copy import deepcopy


import json


from pathlib import Path


import re


import tempfile


import unittest


import yaml


from bookengine import q1, q1_plan


from tools.runtime_support import QuotaExhausted


from bookengine.client import Client


from bookengine.config import Book


from bookengine.storage import load, save


from bookengine.text import number_paragraphs


class CaptureClient(Client):
    def __init__(self, out, bodies, replies=None):
        # Keep Client.stage's real parser/cache, bypass credentials and HTTP setup.
        self.out, self.bodies = out, iter(bodies)
        self.replies = dict(replies or {})
        self.prefix = "PREFIX_SENTINEL"
        self.calls = []
        self.context_limit = 10000
        self.clip_context = True
        self.relaxed = True
        self.minimal_prose = True
        self.history_path = out / "calls.json"

    def request(self, stage, prompt, cap, effort, attempt):
        entry = dict(seq=len(self.calls) + 1, stage=stage, prompt=prompt, status="ok", effort=effort, attempt=attempt)
        self.calls.append(entry)
        if stage in self.replies:
            value = self.replies[stage]
            if isinstance(value, Exception):
                entry["status"] = "failed"
                raise value
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if re.fullmatch(r"r2-ch\d+-draft", stage):
            return next(self.bodies)
        if re.fullmatch(r"r2-ch\d+-(?:finish|final-state)", stage):
            return json.dumps(
                dict(
                    summary="阿宁在院里等她。",
                    events=[],
                    advances=[],
                    state={"characters": {"阿宁": {"location": "院", "present": True}}},
                    scene_types=["daily"],
                    type="日常",
                    payoff={"type": "无兑现", "fulfilled": False, "quote": ""},
                    hook_type=q1.PLAN_DATA["hook_types"][0],
                    structure="日常喘息里藏冲突",
                ),
                ensure_ascii=False,
            )
        raise AssertionError("Unexpected request: " + stage)


def body(dialogue_chars, total=200):
    dialogue = ("我陪你回家" * total)[:dialogue_chars]
    narration = ("她走到院门前停下" * total)[: total - dialogue_chars]
    return f"“{dialogue}。”\n\n{narration}。"


class MinimalBTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name)
        self.data = yaml.safe_load(q1.DATA.read_text(encoding="utf-8"))
        self.book = Book("suspense-mystery", 25, 200, "USER_INPUT_SENTINEL")
        self.bible = dict(
            premise="BIBLE_SENTINEL",
            summary="BIBLE_SUMMARY_SENTINEL",
            world={"rules": ["PARAGRAPH_RULE_SENTINEL"]},
            characters=[
                dict(
                    name="阿宁",
                    role="protagonist",
                    voice="说话直接，先问再答。",
                    taboos=["TABOO_SENTINEL"],
                    habits=["HABIT_SENTINEL"],
                )
            ],
        )
        self.bible["canon"] = deepcopy(q1_plan.GROWTH["packs"][self.book.prompt_pack])
        self.bible["canon"]["hook_types"] = list(q1.PLAN_DATA["hook_types"])
        self.threads = {
            "threads": [
                dict(
                    id="line",
                    kind="dark",
                    beats=[dict(id="wait", volume=1, description="阿宁在院里等她，要求一起回家")],
                )
            ]
        }
        self.outline = dict(
            number=2,
            title="等候",
            scenes=[{"characters": ["阿宁"]}],
            structure="对峙谈判",
            summary="OUTLINE_SUMMARY_SENTINEL",
        )
        self.previous = "她把门推开，阿宁就在院里。"
        save(self.out / "chapter-001.body.md", self.previous)

    def run_chapter(self, client, state=None, rows=None, outline=None):
        return q1.chapter(
            client,
            self.book,
            self.bible,
            self.threads,
            {},
            {"number": 1, "start": 1, "end": 25},
            self.outline if outline is None else outline,
            {} if state is None else state,
            [{"summary": "HISTORY_SUMMARY_SENTINEL"}],
            [{"number": 1, "summary": "RECENT_SUMMARY_SENTINEL"}] if rows is None else rows,
            self.out,
            "b",
            self.data,
        )

    def extraction(self, **updates):
        result = dict(
            summary="阿宁在院里等她。",
            type="日常",
            hook_type=q1.PLAN_DATA["hook_types"][0],
            scene_types=["daily"],
            state={"characters": {"阿宁": {"location": "院", "present": True}}},
            payoff={"type": "无兑现", "fulfilled": False, "quote": ""},
            gains=[],
            costs=[],
            solutions=[],
            advances=[],
            events=[],
        )
        result.update(updates)
        return result

    def assert_saved(self, row, text, number=2):
        self.assertEqual((self.out / f"chapter-{number:03d}.body.md").read_text(), text)
        self.assertEqual(load(self.out / f"chapters/{number:03d}.json"), row)
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["fact_source"], "final_body")
        self.assertEqual(row["chars"], q1.chinese_count(text))

    def assert_calls(self, client, stages):
        self.assertEqual([c["stage"] for c in client.calls], stages)
        # Count every request except drafting, including any word/length/repair.
        outside = [c for c in client.calls if not c["stage"].endswith("-draft")]
        self.assertLessEqual(len(outside), 2)
        self.assertTrue(all(c["attempt"] == 1 for c in client.calls))
        self.assertTrue(all("PREFIX_SENTINEL" not in c["prompt"] for c in client.calls))
        self.assertEqual(client.prefix, "PREFIX_SENTINEL")
