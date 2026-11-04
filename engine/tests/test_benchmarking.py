import unittest
from bookengine.benchmarking import rank_concepts,SCORE_KEYS
from bookengine.batch import usage_report
from bookengine.craft import surface_hits


class BenchmarkTests(unittest.TestCase):
    def test_rank_keeps_complete_candidates_and_orders_scores(self):
        def card(n,score):return dict(id=n,pitch='尖锐卖点',gift='借力与代价',first_payoff='主动赢一局',titles=['甲书名','乙书名'],scores=dict.fromkeys(SCORE_KEYS,score))
        ranked=rank_concepts(dict(concepts=[card(1,7),card(2,9),{},dict(card(3,10),titles=[])]))
        self.assertEqual([c['id'] for c in ranked],[2,1])

    def test_m3_prices_and_unknown_usage_use_m3_rates(self):
        usage=dict(prompt_tokens=1000,completion_tokens=1000,total_tokens=2000,prompt_tokens_details=dict(cached_tokens=100))
        calls=[dict(model='MiniMax-M3',stage='calibrate',status='ok',usage=usage)]
        report=usage_report(calls)
        self.assertAlmostEqual(report['estimated_usd']['no_cache_discount'],.0015)
        self.assertAlmostEqual(report['estimated_usd']['cache_adjusted'],.001476)
        unknown=usage_report([dict(model='MiniMax-M3',status='http_error',stage='calibrate',input_bytes=100,output_token_cap=1000)])
        self.assertAlmostEqual(unknown['estimated_usd']['unknown_upper_bound'],.00123)

    def test_restraint_first_occurrence_is_not_flagged(self):
        hits=surface_hits('他先复核了一遍。他又说待核。她要按他判断。')
        self.assertEqual(len(hits),2)
        self.assertTrue(all('restraint' in h['categories'] for h in hits))
