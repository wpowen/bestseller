"""Broader prose lint, occurrence budgets and independent shared USD limits."""
import json
from pathlib import Path
import tempfile
import unittest
from deai.rules import detect,neutral_tags
from deai.client import MiniMax,LLMError
from deai.io import keep_previous

class RecallTests(unittest.TestCase):
    def test_failed_attempt_keeps_only_a_valid_final_from_the_same_source(self):
        previous=dict(status='polished',complete=True)
        self.assertTrue(keep_previous(dict(status='retained',complete=False),previous,'原文','原文'))
        self.assertFalse(keep_previous(dict(complete=False),previous,'旧文','新文'))
        self.assertFalse(keep_previous(dict(complete=True),previous,'原文','原文'))
    def test_named_and_unseen_formula_variants_are_localized(self):
        examples=['沈允禾声音不高，却压住了满廊雨声。','话音落下。','走廊霎时静了。',
                  '他冷声打断：“够了。”','她的脸色一变。','笑意淡了一瞬。','指腹缓缓擦过纸面。',
                  '说话的声音并不大，但连后排也停了交谈。','瞳孔猛地一缩。','他沉声道：“走。”']
        for text in examples:
            with self.subTest(text=text):
                hits=detect(text);self.assertTrue(hits)
                self.assertTrue(all(text[h['start']:h['end']]==h['text'] for h in hits))
    def test_density_only_selects_excess_and_does_not_ban_first_adverb(self):
        self.assertFalse(any(h['type']=='adverb_density' for h in detect('她缓缓走进屋。')))
        text='她缓缓走进屋。\n\n他缓缓放下杯子。\n\n她缓缓起身。'
        hits=[h for h in detect(text) if h['type']=='adverb_density']
        self.assertEqual({h['pid'] for h in hits},{'P2','P3'})
    def test_emotional_tag_is_not_speaker_dialogue_content(self):
        text='“别叫我冷声道，我姓王。”他说。'
        self.assertFalse(any(h['type']=='adverb_dialogue_tag' for h in detect(text)))
        self.assertEqual(neutral_tags('他沉声道：“别叫我冷声道，三张票给我。”'),'他道：“别叫我冷声道，三张票给我。”')
    def test_budget_aggregates_sibling_chapters_without_writer_cost(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);env=root/'.env';env.write_text('MINIMAX_API_KEY=fixture-only-not-a-real-key\n')
            final=root/'final';past=final/'.deai/001/http';past.mkdir(parents=True)
            row=dict(model='MiniMax-M3.1-Flash',input_bytes=0,max_tokens=0,usage={'prompt_tokens':1000000,'completion_tokens':0})
            (past/'ledger.json').write_text(json.dumps([row]))
            (root/'calls.json').write_text(json.dumps([dict(row,usage={'prompt_tokens':9000000,'completion_tokens':0})]))
            client=MiniMax(final/'.deai/002',env,budget=.15,budget_root=final)
            with self.assertRaises(LLMError):client([{'role':'user','content':'test'}],stage='blocked',max_tokens=1)
            self.assertEqual(client.calls,[])

if __name__=='__main__':unittest.main()
