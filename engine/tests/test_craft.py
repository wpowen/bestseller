import tempfile
import unittest
from pathlib import Path
from bookengine.craft import surface_hits, reference_features, recall_events, reference_fragments
from bookengine.config import Book


class CraftTests(unittest.TestCase):
    def test_english_and_object_pronoun_are_distinct(self):
        text='阿杏把他推开。三折 like 一道兽角。她抬头。'
        hits=surface_hits(text,[dict(name='阿杏',gender='女')])
        self.assertEqual([h['text'] for h in hits],['三折 like 一道兽角。'])
        self.assertEqual(surface_hits('维修编号HX-0719。追魂纹钻进阿宁伤口，他仍扣住阿杏。',[dict(name='阿宁',gender='男'),dict(name='阿杏',gender='女')]),[])

    def test_references_return_only_located_fragments(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);old='她当年烧掉祖宅的欠条，债主第二天送来一袋米，门口的狗也没有叫。'
            (p/'chapter-001.body.md').write_text(old)
            body='他记得：'+old+'如今狗已经老了。'
            refs=reference_fragments([dict(chapter=1,body_quote=body+'不存在',source_quote=old)],body,p)
            self.assertEqual(len(refs),1)
            self.assertIn(refs[0]['body_quote'],body)
            self.assertEqual(refs[0]['source_quote'],old)

    def test_reference_features_never_include_source_sentence(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'own.txt';p.write_text('她笑着推开门。\n\n“你还敢来？”\n\n他没走，低头骂了一句。')
            result=reference_features(p)
            self.assertGreater(result['dialogue_ratio'],0)
            self.assertNotIn('你还敢来',str(result))
            self.assertNotIn('style_reference',Book('realistic-life',5,2200,'输入').record())

    def test_recall_routes_only_tail_and_quotes_real_old_body(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);text='姑娘当年烧掉了欠条，欠债人改天送来一袋米。'
            (p/'chapter-001.body.md').write_text(text)
            history=[dict(events=[dict(chapter=1,event='姑娘烧掉欠条，获赠一袋米')])]
            self.assertEqual(recall_events(p,history,20,100,25),[])
            recalled=recall_events(p,history,23,100,25)
            self.assertEqual(recalled[0]['original_excerpt'],text)
