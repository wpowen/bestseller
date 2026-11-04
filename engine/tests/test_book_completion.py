"""Pure patch addressing and fixed-story constraints; whole-book model proof is separate."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from complete_book import apply,violations,scene_value

class CompletionTests(unittest.TestCase):
    def test_plain_prose_and_json_do_not_publish_thinking(self):
        self.assertEqual(scene_value('她开门。'),'她开门。')
        self.assertEqual(scene_value('<think>不交付思考</think>{"text":"她开门。"}'),'她开门。')
        with self.assertRaises(ValueError):scene_value('<think>尚未闭合')
    def test_numbered_patch_changes_only_selected_duplicate_paragraph(self):
        text='她停下。\n\n她停下。\n\n门开了。'
        result=apply({1:text},[dict(chapter=1,start='P2',end='P2',after='她转身。')],False)
        self.assertEqual(result[1],'她停下。\n\n她转身。\n\n门开了。')
    def test_overlap_and_missing_anchor_are_rejected(self):
        for patches in ([dict(chapter=1,before='没有。',after='替换。')],[dict(chapter=1,start='P1',end='P1',after='她走了。')]*2):
            with self.assertRaises(ValueError):apply({1:'她停下。'},patches,False)
    def test_length_and_story_constraints_are_separate(self):
        with self.assertRaises(ValueError):apply({1:'字'*1870},[dict(chapter=1,start='P1',end='P1',after='字'*1800)])
        rules=dict(required=[dict(chapter=1,text='他放下刀。')],forbidden=['另一个凶手'])
        self.assertEqual(violations({1:'他放下刀。'},rules),[])
        self.assertEqual(len(violations({1:'另一个凶手。'},rules)),2)
