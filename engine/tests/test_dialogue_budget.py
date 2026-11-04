"""Offline accounting checks for the bounded mixed-model experiment."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from dialogue_client import totals,INFLIGHT

class DialogueBudgetTests(unittest.TestCase):
    def test_mixed_models_use_requested_prices_without_cache_discount(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=[dict(seq=i,requested_model=m,status='ok',usage=dict(prompt_tokens=1000,completion_tokens=1000,total_tokens=2000,cached_tokens=999)) for i,m in enumerate(('MiniMax-M3.1-Flash','MiniMax-M3'),1)]
            (Path(temp)/'calls.json').write_text(json.dumps(rows))
            result=totals(temp)
            self.assertEqual(result['estimated_usd'],.00225)
            self.assertEqual((result['calls'],result['tokens'],result['unknown_usage']),(2,4000,0))

    def test_missing_usage_keeps_conservative_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'calls.json').write_text(json.dumps([dict(seq=1,requested_model='MiniMax-M3.1-Flash',status='interrupted',input_bytes=3000,output_token_cap=1000)]))
            result=totals(temp)
            self.assertEqual(result['estimated_usd'],.00105)
            self.assertEqual(result['unknown_usage'],1)

    def test_reservation_excludes_only_its_own_request_not_earlier_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            rows=[dict(seq=i,requested_model='MiniMax-M3.1-Flash',status='ok',usage=dict(prompt_tokens=1000,completion_tokens=1000,total_tokens=2000)) for i in (1,2)]
            (Path(temp)/'calls.json').write_text(json.dumps(rows));INFLIGHT[temp]=dict(seq=2,upper=.01)
            try:self.assertEqual(totals(temp,True)['estimated_usd'],.00075)
            finally:INFLIGHT.pop(temp,None)

if __name__=='__main__':unittest.main()
