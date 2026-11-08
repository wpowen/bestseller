"""Critical fixed-cast checks and real local stage/parse/resume; no model calls."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from bookengine.client import Client
from tools.runtime_support import ProjectError
from bookengine.config import Book
from bookengine.identity import name_hits, repair_companion_roles, validate_bible
from bookengine import q1_plan

ROOT = Path(__file__).resolve().parents[2]
CAST = [
    dict(name="陆惊山", role="protagonist", voice="短句直说", desire="救回妹妹"),
    dict(name="周远舟", role="support", voice="爱反问", desire="保住药铺"),
    dict(name="赵听禾", role="antagonist", voice="慢声威胁", desire="夺下城门"),
]


def bible():
    return dict(title="固定人物测试", characters=deepcopy(CAST), volumes=[dict(number=1, start=1, end=20)])


class ScriptedClient(Client):
    """Only replace transport: Client.stage writes and parses real checkpoints."""

    def __init__(self, out, responses):
        self.out, self.responses = Path(out), iter(responses)
        self.calls, self.prefix, self.glossary = [], "", {}
        self.history_path = self.out / "calls.json"
        self.context_limit, self.clip_context, self.relaxed = 10000, False, False

    def request(self, stage, prompt, cap, effort, attempt):
        try:
            raw = next(self.responses)
        except StopIteration:
            raise AssertionError("Unexpected extra model request: " + stage) from None
        if not isinstance(raw, str):
            raise AssertionError("Fixture responses must be raw text for the real parser")
        self.calls.append(dict(stage=stage, status="ok", attempt=attempt))
        return raw


class BibleValidationTests(unittest.TestCase):
    def test_valid_fixed_cast_is_returned_without_mutation(self):
        value = bible()
        before = deepcopy(value)
        self.assertIs(validate_bible(value), value)
        self.assertEqual(value, before)

    def test_missing_or_malformed_cast_is_rejected(self):
        for value in (
            None,
            [],
            {},
            {"characters": None},
            {"characters": {}},
            {"characters": []},
            {"characters": CAST[:1]},
            {"characters": CAST[:2]},
            {"characters": [*CAST[:2], None]},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_bible(value)

    def test_duplicate_name_and_missing_or_multiple_protagonists_are_rejected(self):
        for problem in ("duplicate", "no_protagonist", "two_protagonists"):
            value = bible()
            if problem == "duplicate":
                value["characters"][1]["name"] = value["characters"][0]["name"]
            elif problem == "no_protagonist":
                value["characters"][0]["role"] = "support"
            else:
                value["characters"][1]["role"] = "protagonist"
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                validate_bible(value)

    def test_name_voice_and_desire_must_each_be_nonblank_strings(self):
        for field in ("name", "voice", "desire"):
            for bad in ("", " \n\t ", None, 17):
                for index in range(3):
                    value = bible()
                    value["characters"][index][field] = bad
                    with self.subTest(field=field, bad=bad, index=index), self.assertRaises(ValueError):
                        validate_bible(value)
            value = bible()
            del value["characters"][2][field]
            with self.subTest(field=field, missing=True), self.assertRaises(ValueError):
                validate_bible(value)

    def test_eval49_nonhuman_companion_is_not_assigned_the_romance_role(self):
        value = bible()
        value["characters"][1].update(
            name="蛮玉筠",
            role="romance",
            identity="怕冷贪吃的白爪幼兽",
            gender="雄性",
        )
        value["characters"].append(
            dict(name="沈青禾", role="romance", identity="游历边境的女药师", voice="清晰利落", desire="救人")
        )
        seed = "被逐出驭兽宗的矿镇少年，为救妹妹与怕冷贪吃的幼兽结契。女药师有独立的救人目标。"

        repaired, corrections = repair_companion_roles(value, seed)

        assert repaired is not value
        assert next(card for card in repaired["characters"] if card["name"] == "蛮玉筠")["role"] == "support"
        assert next(card for card in repaired["characters"] if card["name"] == "沈青禾")["role"] == "romance"
        assert corrections == [
            {
                "name": "蛮玉筠",
                "from": "romance",
                "to": "support",
                "reason": "nonhuman_companion_not_explicit_romance",
            }
        ]
        assert value["characters"][1]["role"] == "romance"

    def test_seed_can_explicitly_require_a_nonhuman_romance_role(self):
        value = bible()
        value["characters"][1].update(name="蛮玉筠", role="romance", identity="白爪幼兽", gender="雄性")

        repaired, corrections = repair_companion_roles(value, "白爪幼兽蛮玉筠是主角明确的感情对象与官配。")

        assert repaired["characters"][1]["role"] == "romance"
        assert corrections == []

    def test_prepare_response_is_corrected_before_validation_and_checkpointing(self):
        value = bible()
        value["characters"][1].update(name="蛮玉筠", role="romance", identity="怕冷贪吃的白爪幼兽")

        class Client:
            prefix = ""
            relaxed = False

            def stage(self, _stage, _prompt, _schema, **kwargs):
                response = {"bible": deepcopy(value), "threads": []}
                normalized = kwargs["normalize"](response)
                kwargs["validate"](normalized)
                self.saved = deepcopy(normalized)
                return normalized

        client = Client()
        result = q1_plan._ask(
            client,
            "prepare",
            "prepare",
            [("用户要求", {"user_input": "少年为救妹妹与怕冷贪吃的幼兽结契。"})],
            seed_text="少年为救妹妹与怕冷贪吃的幼兽结契。",
        )

        assert result["bible"]["characters"][1]["role"] == "support"
        assert client.saved == result


class PrepareIdentityTests(unittest.TestCase):
    def prepare(self, client, out):
        with redirect_stdout(io.StringIO()):
            return q1_plan.prepare(client, Book("xuanhuan-power-fantasy", 20, 2200, "固定人物"), out, ROOT / "config")

    def test_six_bad_responses_stop_without_writing_a_bible_or_threads(self):
        for raw in (
            "broken: [",
            json.dumps({"bible": {"title": "缺卡", "characters": []}}),
            json.dumps({"bible": dict(bible(), characters=CAST[:2])}, ensure_ascii=False),
        ):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                out = Path(directory) / "book"
                out.mkdir()
                client = ScriptedClient(out, [raw] * 6)
                with self.assertRaises(ProjectError):
                    self.prepare(client, out)
                self.assertEqual([c["stage"] for c in client.calls], ["plan-q1-prepare"] * 6)
                self.assertFalse((out / "story_bible.yaml").exists())
                self.assertFalse((out / "threads.yaml").exists())
                self.assertFalse(client.relaxed)

    def test_one_bad_then_valid_parse_succeeds_and_resume_has_no_requests(self):
        raw = "```yaml\n" + yaml.safe_dump(dict(bible=bible(), threads=[]), allow_unicode=True) + "```"
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "book"
            out.mkdir()
            client = ScriptedClient(out, ["broken: [", raw] + ["{}"] * 3)
            result = self.prepare(client, out)
            self.assertEqual(len(client.calls), 5)
            self.assertEqual(len(result[0]["characters"]), 3)
            validate_bible(result[0])
            self.assertEqual(result[0]["canon"]["seed"], "固定人物测试")
            self.assertEqual(result[0]["canon"]["scheduling"], q1_plan.GROWTH.get("scheduling", {}))
            paths = [out / name for name in ("story_bible.yaml", "threads.yaml")]
            before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
            cached = ScriptedClient(out, [])
            self.assertEqual(self.prepare(cached, out), result)
            self.assertEqual(cached.calls, [])
            self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in paths], before)
            # Simulate interruption after the bible save: reuse BOTH parsed stage caches.
            paths[1].unlink()
            self.prepare(cached, out)
            self.assertEqual(cached.calls, [])
            self.assertEqual((paths[0].read_bytes(), paths[0].stat().st_mtime_ns), before[0])
            self.assertEqual(yaml.safe_load(paths[1].read_text()), yaml.safe_load(before[1][0]))

    def test_invalid_saved_bible_is_not_silently_replaced_or_sent_to_model(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "book"
            out.mkdir()
            path = out / "story_bible.yaml"
            path.write_text("title: 坏检查点\ncharacters: []\n", encoding="utf-8")
            before = path.read_bytes(), path.stat().st_mtime_ns
            client = ScriptedClient(out, [])
            with self.assertRaises(ValueError):
                self.prepare(client, out)
            self.assertEqual(client.calls, [])
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)


class NameHitTests(unittest.TestCase):
    def test_repeated_unknown_subject_names_raise_candidates_with_source_positions(self):
        for name in ("赵砚", "陆沉", "沈砚"):
            text = f"{name}说：“快走。”\n\n{name}把门推开。\n\n{name}走向河岸。"
            with self.subTest(name=name):
                hits = name_hits(text, bible())
                self.assertTrue(hits, "Unknown repeated subject must not escape by surname")
                self.assertEqual({h["name"] for h in hits}, {name})
                self.assertTrue(all(h["kind"] == "name_drift" for h in hits))
                self.assertTrue(all(name in text[h["start"] : h["end"]] for h in hits))
                self.assertTrue(all(type(h["id"]) is int and h["id"] >= 1 for h in hits))

    def test_known_names_and_card_addresses_are_not_drift(self):
        value = bible()
        value["characters"][1]["address"] = "周叔"
        for name in ("周远舟", "周叔", "赵听禾"):
            text = f"{name}说：“好。”\n\n{name}把灯举起。\n\n{name}走进院子。"
            self.assertEqual(name_hits(text, value), [])

    def test_surrounding_adverbs_sparse_mentions_and_non_subjects_are_not_drift(self):
        for prefix in ("周围", "方才", "方圆", "那一", "那两", "那几", "那三"):
            self.assertEqual(name_hits((prefix + "看不见人。\n\n") * 3, bible()), [])
        self.assertEqual(name_hits("赵砚说：“好。”\n\n赵砚走了。", bible()), [])
        self.assertEqual(name_hits("她把赵砚叫来。\n\n她向赵砚招手。\n\n她把赵砚拉进屋。", bible()), [])
        self.assertEqual(name_hits("", bible()), [])


if __name__ == "__main__":
    unittest.main()
