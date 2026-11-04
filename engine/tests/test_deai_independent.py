"""Pure independent post-processing contracts; real models are tested separately."""
from collections import Counter
import unittest
from deai.text import paragraphs,sentences,replace,invariant,parse,count
from deai.rules import detect,merge_hits

class IndependentTests(unittest.TestCase):
    def test_parse_fences_literals_and_incomplete_errors(self):
        self.assertEqual(parse('```json\n{"a":1}\n```'),{'a':1})
        self.assertEqual(parse("{'a': True}"),{'a':True})
        with self.assertRaises(ValueError):parse('{"a":')
    def test_offsets_preserve_crlf_and_surrounding_bytes(self):
        text='  她开门。\r\n\r\n\t屋里阴森。她拿票。\r\n'
        hit=detect(text)[0];new=replace(text,[dict(start=hit['start'],end=hit['end'],replacement='屋里没有灯。')])
        self.assertEqual(new,text[:hit['start']]+'屋里没有灯。'+text[hit['end']:])
        self.assertEqual(invariant(text,new,{}),[])
    def test_paragraph_insertion_deletion_and_empty_replacement_rejected(self):
        old='她开门。\n\n她坐下。'
        self.assertIn('paragraph_count',invariant(old,'她开门。',{}))
        self.assertIn('paragraph_structure',invariant(old,'她开门。\n她坐下。',{}))
    def test_numbers_and_entities_are_not_silently_changed(self):
        self.assertIn('numbers',invariant('许秋拿三张票。','许秋拿两张票。',{},['许秋']))
        self.assertIn('entity:许秋',invariant('许秋拿三张票。','周秋拿三张票。',{},['许秋']))
    def test_length_is_relative_to_original_and_explicit_range(self):
        self.assertIn('word_band',invariant('她'*100,'她'*89,{},whole=True))
        self.assertIn('word_band',invariant('她'*100,'她'*111,{},whole=True))
        self.assertEqual(invariant('她'*100,'她'*105,{},whole=True),[])
    def test_fact_issues_never_add_to_style(self):
        hit=detect('屋里阴森。')[0]
        self.assertEqual(merge_hits([], [dict(hit,type='date_arithmetic')]),[])
    def test_uncertain_emotion_cannot_be_promoted_to_fact(self):
        self.assertIn('certainty:吓',invariant('她像被吓着一样松手。','她吓得松手。',{}))
        self.assertEqual(invariant('她像被吓着一样松手。','她松开了手。',{}),[])
        self.assertIn('body_side',invariant('他用左手开门。','他用右手开门。',{}))
    def test_migration_rolls_back_only_violating_paragraph(self):
        from deai.text import rollback_invalid
        original='她像被吓着一样松手。\n\n屋里阴森。';final='她吓得松手。\n\n屋里没有灯。'
        edits=[dict(pid='P1',before='她像被吓着一样松手。',replacement='她吓得松手。'),dict(pid='P2',before='屋里阴森。',replacement='屋里没有灯。')]
        text,kept,pids=rollback_invalid(original,final,edits,{})
        self.assertEqual(text,'她像被吓着一样松手。\n\n屋里没有灯。');self.assertEqual(pids,['P1']);self.assertEqual(len(kept),1)
    def test_multiple_edits_retain_unselected_sentence_and_spacing(self):
        t='她开门。 他起身。\n\n她坐下。';r=sentences(t)
        e=[dict(start=r[0]['start'],end=r[0]['end'],replacement='她推门。'),dict(start=r[2]['start'],end=r[2]['end'],replacement='她坐好。')]
        self.assertEqual(replace(t,e),'她推门。 他起身。\n\n她坐好。')
