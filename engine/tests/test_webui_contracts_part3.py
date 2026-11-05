"""Behavior-preserving split of test_webui_contracts.py."""

from support_webui_contracts import *


class CostTestsPart1(CostTests):
    def test_known_usage_is_not_given_cache_discount(self):
        folder = self.book()
        put(
            folder / "calls.json",
            [
                {
                    "model": "MiniMax-M3.1-Flash",
                    "usage": {
                        "prompt_tokens": 1000000,
                        "completion_tokens": 100000,
                        "prompt_tokens_details": {"cached_tokens": 1000000},
                    },
                }
            ],
        )
        result = library.costs(folder)
        self.assertAlmostEqual(result["known"], 0.21)
        self.assertEqual(result["cost"], result["known"])
        self.assertEqual(result["unknown"], 0)

    def test_unknown_and_inflight_keep_bounds_across_all_ledger_sources(self):
        folder = self.book()
        put(
            folder / "calls.json",
            [{"usage": {}, "input_bytes": 100000, "output_token_cap": 10000, "status": "timeout"}],
        )
        put(
            folder / "http/ledger.json",
            [{"usage": None, "input_bytes": 200000, "max_tokens": 20000, "status": "inflight"}],
        )
        put(
            folder / "final/.deai/001/http/ledger.json",
            [{"model": "MiniMax-M3", "usage": None, "input_bytes": 100000, "max_tokens": 10000, "status": "failed"}],
        )
        before = snapshot(folder)
        result = library.costs(folder)
        self.assertEqual((result["calls"], result["unknown"]), (3, 3))
        self.assertAlmostEqual(result["unknown_bound"], 0.105)
        self.assertEqual(result["cost"], result["unknown_bound"])
        self.assertEqual(library.costs(folder), result)
        self.assertEqual(snapshot(folder), before)

    def test_missing_usage_with_no_caps_is_not_zero_cost(self):
        folder = self.book()
        put(folder / "calls.json", [{"usage": None}])
        result = library.costs(folder)
        self.assertEqual(result["unknown"], 1)
        self.assertGreater(result["cost"], 0)
        self.assertTrue(math.isfinite(result["cost"]))

    def test_partial_usage_does_not_erase_known_tokens_with_smaller_default_bound(self):
        folder = self.book()
        # A legacy receipt can contain known usage while missing input byte caps.
        put(folder / "calls.json", [{"usage": {"prompt_tokens": 100000}, "output_token_cap": 100}])
        result = library.costs(folder)
        self.assertEqual(result["unknown"], 1)
        self.assertGreaterEqual(result["cost"], 0.015)
