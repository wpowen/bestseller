import json
import unittest
from unittest.mock import Mock, patch

from bookengine.batch import usage_report
from bookengine.benchmarking import LEGACY_SCALE, SCORE_KEYS, concept_design, rank_concepts
from bookengine.config import Book
from bookengine.craft import surface_hits


class BenchmarkTests(unittest.TestCase):
    def test_rank_keeps_complete_candidates_and_orders_scores(self):
        def card(n, score):
            return dict(
                id=n,
                pitch="尖锐卖点",
                gift="借力与代价",
                first_payoff="主动赢一局",
                titles=["甲书名", "乙书名"],
                scores=dict.fromkeys(SCORE_KEYS, score),
            )

        ranked = rank_concepts(dict(concepts=[card(1, 7), card(2, 9), {}, dict(card(3, 10), titles=[])]))
        self.assertEqual([c["id"] for c in ranked], [2, 1])

    def test_m3_prices_and_unknown_usage_use_m3_rates(self):
        usage = dict(
            prompt_tokens=1000, completion_tokens=1000, total_tokens=2000, prompt_tokens_details=dict(cached_tokens=100)
        )
        calls = [dict(model="MiniMax-M3", stage="calibrate", status="ok", usage=usage)]
        report = usage_report(calls)
        self.assertAlmostEqual(report["estimated_usd"]["no_cache_discount"], 0.0015)
        self.assertAlmostEqual(report["estimated_usd"]["cache_adjusted"], 0.001476)
        unknown = usage_report(
            [dict(model="MiniMax-M3", status="http_error", stage="calibrate", input_bytes=100, output_token_cap=1000)]
        )
        self.assertAlmostEqual(unknown["estimated_usd"]["unknown_upper_bound"], 0.00123)

    def test_restraint_first_occurrence_is_not_flagged(self):
        hits = surface_hits("他先复核了一遍。他又说待核。她要按他判断。")
        self.assertEqual(len(hits), 2)
        self.assertTrue(all("restraint" in h["categories"] for h in hits))


def test_concept_score_matches_requested_length_and_resumes_old_checkpoints(tmp_path):
    client = Mock(out=tmp_path)
    for chapters, historical in ((1, False), (10, False), (600, False), (10, True)):
        client.out = tmp_path / f"{chapters}-{historical}"
        keys = (*SCORE_KEYS[:-1], LEGACY_SCALE) if historical else SCORE_KEYS
        cards = [
            dict(
                id=i,
                pitch="卖点",
                gift="借力",
                first_payoff="发现",
                titles=["题一", "题二"],
                scores=dict.fromkeys(keys, 5),
            )
            for i in range(1, 13)
        ]
        cards[-1]["scores"][keys[-1]] = 10
        with patch("bookengine.benchmarking.ask", return_value={"concepts": cards}) as ask:
            book = Book("xuanhuan-power-fantasy", chapters, 2000, "依照实际章数收束")
            assert concept_design(client, book)["id"] == 12
            _, _, prompt, _, schema = ask.call_args.args
            assert f"这部{chapters}章" in prompt and f"前{min(3, chapters)}章" in prompt
            assert LEGACY_SCALE not in prompt + schema
            assert set(json.loads(schema)["concepts"][0]["scores"]) == set(SCORE_KEYS)
            assert concept_design(client, book) == cards[-1]
            ask.assert_called_once()
