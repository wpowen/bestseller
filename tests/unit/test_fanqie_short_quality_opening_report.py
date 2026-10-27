"""Pure local evidence tests; no DB, provider, settings or native writes."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from bestseller.services.fanqie_short_opening_gate import (
    FanqieShortOpeningReport,
    evaluate_fanqie_short_opening_gate,
)
from bestseller.services.fanqie_short_quality import (
    FanqieWholePieceReview,
    review_whole_fanqie_short_story,
)
from bestseller.services.qimao_opening_gate import QimaoOpeningFinding


class WholeOpeningReportTests(unittest.TestCase):
    def test_actual_opening_report_retains_ratio_wordcounts_and_all_findings(self):
        body = "  我被房东逼着交钥匙，反手按住了桌上的证据。\n\n" + "旧收音机和未说完的旧事仍留在工作台上。" * 40 + "🧪\n  "
        for ratio in (0.15, 0.30, 0.55):
            with self.subTest(ratio=ratio):
                expected = evaluate_fanqie_short_opening_gate(
                    body, unlock_line_ratio=ratio, protagonist_name="我"
                )
                review = review_whole_fanqie_short_story(
                    body, title="修好旧收音机", unlock_line_ratio=ratio, protagonist_name="我"
                )
                self.assertEqual(review.opening_report, expected)
                self.assertEqual(review.to_dict()["opening_report"], expected.to_dict())
                self.assertEqual(review.opening_passed, expected.passed)
                self.assertEqual(review.opening_report.total_words, len(body.strip()))
                self.assertEqual(review.opening_report.unlock_zone_words, max(50, int(len(body.strip()) * ratio)))
                self.assertEqual(review.opening_report.unlock_ratio, ratio)

    def test_all_severities_and_duplicate_codes_remain_distinct_in_wire_report(self):
        # Synthetic detector output for serialization only; no PASS authority.
        opening = FanqieShortOpeningReport(
            passed=False, unlock_zone_words=300, total_words=1000, unlock_ratio=0.30,
            findings=(
                QimaoOpeningFinding("weak_hook", "critical", "首窗口问题", "原始首窗口证据", 1),
                QimaoOpeningFinding("weak_hook", "critical", "窗口末问题", "原始窗口末证据", 1),
                QimaoOpeningFinding("weak_immersion", "warning", "具名密度", "甲、乙、丙", 1),
            ),
        )
        review = FanqieWholePieceReview(False, False, False, False, [], [], opening_report=opening)
        before = deepcopy(opening)
        wire = review.to_dict()["opening_report"]
        self.assertEqual(wire, opening.to_dict())
        self.assertEqual([f["evidence"] for f in wire["findings"]],
                         ["原始首窗口证据", "原始窗口末证据", "甲、乙、丙"])
        self.assertEqual([f["severity"] for f in wire["findings"]], ["critical", "critical", "warning"])
        wire["findings"][0]["evidence"] = "外部修改"
        self.assertEqual(review.opening_report, before)

    def test_missing_legacy_opening_report_stays_unknown_not_synthesized_from_bool(self):
        for passed in (False, True):
            review = FanqieWholePieceReview(passed, passed, passed, passed, [], [])
            self.assertIsNone(review.opening_report)
            self.assertIsNone(review.to_dict()["opening_report"])
            self.assertIs(review.to_dict()["opening_passed"], passed)

    def test_opening_report_does_not_weaken_taboo_or_minimum_length(self):
        body = "我被逼交钥匙，反手拿出证据，对方当场认账。求过审。"
        review = review_whole_fanqie_short_story(body, protagonist_name="我")
        self.assertIsNotNone(review.opening_report)
        self.assertFalse(review.passed)
        self.assertIn("contains_review_begging", review.taboo_signals)
        self.assertIn("全文过短", review.notes)

    def test_saved_real_v4_report_legacy_fields_unchanged(self):
        path = (Path(__file__).resolve().parents[2] /
                "reports/project-reliability-review-2026-10-24/implementation/original-four-whole-v4-execution.json")
        if not path.is_file():
            self.skipTest("Optional saved production readback is absent; no live read is substituted")
        payload = json.loads(path.read_text(encoding="utf-8"))["payload"]
        expected = deepcopy(payload["original_report"])
        review = review_whole_fanqie_short_story(
            payload["full_text"], title=payload["title"],
            unlock_line_ratio=payload["unlock_line_ratio"], protagonist_name=payload["protagonist"],
        )
        actual = review.to_dict()
        opening = actual.pop("opening_report")
        self.assertEqual(actual, expected)
        self.assertFalse(opening["passed"])
        self.assertEqual(opening["unlock_ratio"], 0.30)
        self.assertEqual(opening["total_words"], 13487)
        self.assertEqual(opening["unlock_zone_words"], 4046)
        self.assertIn("weak_hook", [f["code"] for f in opening["findings"]])
        self.assertIn("first_10k_loop_missing", [f["code"] for f in opening["findings"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
