"""Offline INBOX19 rule regressions: real prose, synthetic variants, no clients."""

from pathlib import Path
import re
import subprocess
import unittest

from bookengine.precision import _sentences
from bookengine.text import _paragraph_spans
from deai import rules as production_rules


ENGINE = Path(__file__).resolve().parents[1]
GEMINI = ENGINE.parent / "books/compare/gemini.md"
if not GEMINI.exists():
    # Linked worktrees share the authorized original; never copy or rewrite prose.
    common = subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ENGINE, text=True
    ).strip()
    GEMINI = Path(common).parent / "books/compare/gemini.md"


def sentence_rows(text):
    paragraphs = _paragraph_spans(text)
    return [
        dict(
            sid=f"S{i}",
            pid=f"P{next(j for j, (a, b) in enumerate(paragraphs, 1) if a <= start < b)}",
            start=start,
            end=end,
            text=text[start:end],
        )
        for i, (start, end) in enumerate(_sentences(text), 1)
    ]


class DeaiRulesTests(unittest.TestCase):
    rules = production_rules

    def test_rules_are_not_discarded_by_empty_model_votes(self):
        rule = dict(sid="S1", type="cliche_simile", severity=2, source="rule")
        self.assertEqual(self.rules.merge_hits([rule], []), [rule])
        fact = dict(rule, type="date_arithmetic", source="model")
        self.assertEqual(self.rules.merge_hits([rule], [fact]), [rule])

    def test_real_gemini_named_phrases_are_counted_and_located(self):
        # Read the authorized original, never a copied fixture or a model response.
        text = GEMINI.read_text(encoding="utf-8")
        rows = sentence_rows(text)
        hits = self.rules.detect(text, rows=rows)
        expected = {
            "死鱼眼": "cliche_simile",
            "像狗啃的一样": "cliche_simile",
            "像三口棺材": "cliche_simile",
            "阴森": "ominous_rendering",
            "阴毒": "ominous_rendering",
        }
        for phrase, kind in expected.items():
            with self.subTest(phrase=phrase):
                matching_rows = [row for row in rows if phrase in row["text"]]
                self.assertTrue(matching_rows, "Authorized source must contain the named phrase")
                for row in matching_rows:
                    self.assertTrue(
                        any(
                            hit["sid"] == row["sid"] and hit["type"] == kind and hit["source"] == "rule" for hit in hits
                        )
                    )
        measured = self.rules.features(text, rows=rows)
        self.assertGreaterEqual(measured["simile_count"], 3)
        self.assertGreaterEqual(measured["cliche_count"], 3)
        self.assertGreaterEqual(measured["ominous_count"], 2)
        for name in ("simile", "cliche", "ominous"):
            self.assertGreater(measured[name + "_per_1000"], 0)

    def test_rules_generalize_to_new_sentences(self):
        examples = [
            ("新来的掌柜瞪着一双死鱼眼。", "cliche_simile"),
            ("修过的篱笆仍像狗啃的一样。", "cliche_simile"),
            ("渡口的货柜排着，像三口棺材。", "cliche_simile"),
            ("地下车站显得阴森。", "ominous_rendering"),
            ("陌生人露出阴毒的笑容。", "ominous_rendering"),
        ]
        for text, kind in examples:
            with self.subTest(text=text):
                self.assertTrue(any(hit["type"] == kind and hit["source"] == "rule" for hit in self.rules.detect(text)))
                key = "simile_count" if kind == "cliche_simile" else "ominous_count"
                self.assertGreaterEqual(self.rules.features(text)[key], 1)

    def test_cliche_terms_alias_counts_longest_nonoverlapping_matches(self):
        text = "一张磨盘般的脸。篱笆像狗啃的一样。货柜像三口棺材。"
        rules = {
            "lexicons": {
                "simile": {
                    "type": "cliche_simile",
                    "cliche_terms": ["磨盘", "磨盘般的脸", "像狗啃的一样", "像三口棺材"],
                    "regex": r"像[^。]{1,28}一样",
                }
            },
            "patterns": [],
        }
        measured = self.rules.features(text, rules=rules)
        self.assertEqual(measured["simile_count"], 3)
        self.assertEqual({hit["sid"] for hit in self.rules.detect(text, rules=rules)}, {"S1", "S2", "S3"})

    def test_regex_without_terms_counts_and_densities_use_han_denominator(self):
        text = "ABC 123 她缓缓起身，微微一笑。墙像纸一样薄。屋里阴森，目光阴毒。"
        rules = {
            "lexicons": {
                "simile": {"type": "cliche_simile", "regex": r"像[^。]{1,28}一样"},
                "cliche": {"type": "cliche_density", "regex": "缓缓|微微"},
                "ominous": {"type": "ominous_rendering", "regex": "阴森|阴毒"},
            },
            "patterns": [],
        }
        measured = self.rules.features(text, rules=rules)
        han = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
        for name, count in [("simile", 1), ("cliche", 2), ("ominous", 2)]:
            with self.subTest(metric=name):
                self.assertEqual(measured[name + "_count"], count)
                self.assertAlmostEqual(measured[name + "_per_1000"], 1000 * count / han, delta=0.001)

    def test_rule_hits_enter_merge_without_model_votes_and_win_duplicates(self):
        hits = self.rules.detect("地下室阴森。")
        self.assertTrue(hits)
        self.assertEqual(self.rules.merge_hits(hits, []), hits)
        model_duplicate = dict(hits[0], source="model", votes=1)
        merged = self.rules.merge_hits(hits, [model_duplicate])
        self.assertEqual(len(merged), len(hits))
        self.assertTrue(all(hit["source"] == "rule" for hit in merged))

    def test_deduplication_preserves_same_rule_in_different_sentences(self):
        text = "屋里阴森，走廊也阴森。\n\n地窖阴森。"
        self.assertEqual(self.rules.features(text)["ominous_count"], 3)
        hits = self.rules.detect(text)
        ominous = [hit for hit in hits if hit["type"] == "ominous_rendering"]
        self.assertEqual({hit["sid"] for hit in ominous}, {"S1", "S2"})
        merged = self.rules.merge_hits(hits + hits, [dict(hit, source="model") for hit in hits])
        self.assertEqual(len(merged), len({(hit["sid"], hit["type"]) for hit in hits}))
        self.assertEqual({hit["sid"] for hit in merged}, {"S1", "S2"})

    def test_offsets_and_ids_preserve_original_unicode_crlf_and_rows(self):
        text = "🧰他开门。\r\n\r\n\t柜底阴森。  门外阴毒。\r\n"
        rows = sentence_rows(text)
        supplied = self.rules.detect(text, rows=rows)
        automatic = self.rules.detect(text)
        self.assertEqual(supplied, automatic)
        self.assertEqual({hit["sid"] for hit in supplied}, {"S2", "S3"})
        by_id = {row["sid"]: row for row in rows}
        for hit in supplied:
            row = by_id[hit["sid"]]
            self.assertEqual(hit["pid"], "P2")
            self.assertEqual(text[hit["start"] : hit["end"]], hit["text"])
            self.assertLessEqual(row["start"], hit["start"])
            self.assertLess(hit["start"], hit["end"])
            self.assertLessEqual(hit["end"], row["end"])
            self.assertTrue(hit["rule"])
            self.assertIn(hit["severity"], (1, 2, 3))
            self.assertEqual(hit["source"], "rule")

    def test_fact_types_are_excluded_from_rule_detection_and_both_merge_inputs(self):
        facts = ("continuity_conflict", "date_arithmetic", "era_conflict")
        text = "屋里阴森。"
        rules = {
            "lexicons": {},
            "patterns": [{"id": kind, "type": kind, "regex": "阴森", "certain": True} for kind in facts],
        }
        self.assertEqual(self.rules.detect(text, rules=rules), [])
        style = self.rules.detect(text)
        self.assertTrue(style)
        fact_hits = [dict(style[0], type=kind) for kind in facts]
        merged = self.rules.merge_hits(style + fact_hits, [dict(hit, source="model") for hit in fact_hits])
        self.assertEqual(merged, style)

    def test_concrete_negation_is_not_a_corrective_negation_issue(self):
        for text in (
            "这股味道不是樟脑，而是旧棉花受潮的味道。",
            "杯里不是白酒，而是凉开水。",
            "电话里说话的不是姐姐，而是她的同事。",
        ):
            with self.subTest(text=text):
                hits = self.rules.detect(text)
                self.assertFalse(
                    any(hit["type"] == "negation_turn" or hit["rule"] == "corrective_negation" for hit in hits)
                )


if __name__ == "__main__":
    unittest.main()
