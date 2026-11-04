import copy
import unittest

from bookengine.checks import advance_state, check_events, high_confidence, length_band, text_issues


class S3Tests(unittest.TestCase):
    def setUp(self):
        self.state = dict(characters={'甲': dict(location='院', present=True)},
                          objects={'伞': dict(location='院', owner='甲')},
                          clock=dict(day=2, minute=30), relationships=[{'pair': ['甲', '乙']}],
                          foreshadows={'信': 'open'})
        self.text = '甲离开院子。\n\n甲走进屋里。\n\n甲撑着伞坐在屋里。'

    def event(self, paragraph=1, **changes):
        event = dict(kind='character', name='甲', location='院', action='stay',
                     paragraph=paragraph, quote=self.text.split('\n\n')[paragraph - 1])
        event.update(changes)
        return event

    def kinds(self, events, state=None):
        return [i['kind'] for i in check_events(self.state if state is None else state, events, self.text)]

    def test_band_and_exact_banned_phrases(self):
        self.assertEqual(length_band(100), (85, 115))
        self.assertEqual(length_band(101), (86, 116))
        issues = text_issues('禁用，禁用。\n\n禁 用。', ['禁用', '禁用', ''])
        self.assertEqual([(i['kind'], i['paragraph']) for i in issues], [('banned_phrase', 1)])
        self.assertFalse(issues[0]['hard'])

    def test_repetition_counts_current_and_history_retains_evidence(self):
        sentence = '院子深处那棵老树终于开花了'
        self.assertFalse(text_issues(sentence + '。' + sentence + '。'))
        current = f'“{sentence}！”\n\n{sentence}。{sentence}。'
        issues = text_issues(current, previous_texts=[f'「{sentence}。」', ' '.join(sentence) + '。'])
        self.assertEqual([i['paragraph'] for i in issues], [1, 2])
        self.assertEqual(issues[1]['evidence'], [sentence + '。'] * 2)
        self.assertFalse(text_issues('天黑了。' * 4))
        self.assertFalse(text_issues('陆沉。不。够了。七个字也不计算。' * 4))
        self.assertTrue(text_issues('八个完整文字才计算。' * 3))
        self.assertFalse(text_issues('“你在哪？”她问。“我到家了。”她问。“拿到东西了吗？”她问。'))
        self.assertFalse(text_issues(sentence + '。', previous_texts=[sentence[:4] + '，' + sentence[4:]] * 2))

    def test_only_explicit_simultaneous_presence_is_hard(self):
        for text, expected in [
            ('同一时刻，甲站在东门，甲却坐在西门。', True),
            ('甲站在东门。随后甲坐在西门。', False),
            ('甲离开东门，甲坐在西门。', False),
            ('甲站在东门，甲坐在西门。', False),
            ('同时，甲计划站在东门，甲坐在西门。', False),
            ('同时，甲站在东门，甲通过视频坐在西门。', False),
            ('回忆中，同时甲站在东门，甲坐在西门。', False),
            ('同时，甲站在东门，甲坐在东门内。', False),
            ('同时，甲站在东门，甲坐在东门口。', False),
            ('同时，甲站在东门，乙坐在西门。', False),
            ('同时，甲站在东门，后来甲坐在西门。', False),
            ('同时，甲站在东门，甲想象甲坐在西门。', False),
            ('“同时，甲站在东门，甲坐在西门。”', False),
        ]:
            with self.subTest(text=text):
                places = ('东门', '东门内' if '东门内' in text else '东门口' if '东门口' in text else '西门')
                events = [{k: v for k, v in self.event(location=p).items() if k != 'quote'} for p in places]
                issues = high_confidence({}, events, text)
                self.assertEqual(bool(issues), expected)
                if issues:
                    self.assertEqual(issues[0]['kind'], 'simultaneous_locations')

    def test_old_location_absence_and_objects_never_block(self):
        events = [self.event(action='leave'), self.event(2, location='屋'),
                  self.event(3, kind='object', name='伞', owner='甲', location='屋')]
        self.assertFalse(check_events(self.state, events, self.text))
        state = advance_state(self.state, events, self.text)
        self.assertEqual(state['characters']['甲']['location'], '屋')
        self.assertEqual(state['objects']['伞']['location'], '屋')
        call = self.event(2, location='远方', remote=True)
        self.assertFalse(advance_state(self.state, [events[0], call], self.text)['characters']['甲']['present'])

    def test_death_persists_and_only_current_physical_presence_blocks(self):
        text = '甲死亡。\n\n甲站在屋里。'
        death = dict(kind='death', name='甲', paragraph=1, quote='甲死亡。')
        live = self.event(2, location='屋里', quote='甲站在屋里。')
        before = copy.deepcopy((self.state, death, live))
        issues = high_confidence(self.state, [live, death], text)
        self.assertEqual([i['kind'] for i in issues], ['dead_character'])
        state = advance_state(self.state, [death], text)
        self.assertTrue(state['characters']['甲']['dead'])
        self.assertEqual([i['kind'] for i in high_confidence(state, [live], text)], ['dead_character'])
        for flag in ('flashback', 'projected', 'remote'):
            self.assertFalse(high_confidence(state, [dict(live, **{flag: True})], text))
            self.assertNotIn('dead', advance_state(self.state, [dict(death, **{flag: True})], text)['characters']['甲'])
        for quote in ('甲的遗体在屋里。', '甲计划站在屋里。', '甲说他站在屋里。'):
            self.assertFalse(high_confidence(state, [dict(live, paragraph=1, quote=quote)], quote))
        unknown = advance_state({}, [death], text)
        self.assertTrue(unknown['characters']['甲']['dead'])
        self.assertTrue(advance_state(state, [dict(live, quote='甲的遗体在屋里。')], '甲死亡。\n\n甲的遗体在屋里。')['characters']['甲']['dead'])
        self.assertEqual(high_confidence({}, [dict(death, quote='非精准引文'), live], text)[0]['kind'], 'dead_character')
        self.assertEqual(high_confidence({'_dead': ['甲']}, [dict(live, quote=None)], text)[0]['kind'], 'dead_character')
        self.assertEqual(high_confidence({'_dead': ['甲']}, [{k: v for k, v in live.items() if k != 'quote'}], text)[0]['kind'], 'dead_character')
        self.assertEqual((self.state, death, live), before)

    def test_clock_and_flashback_do_not_mutate_current_state(self):
        self.text = '第1天23点59分。\n\n第3天0点。\n\n甲撑着伞坐在屋里。'
        time = dict(kind='time', day=1, minute=1439, paragraph=1, quote='第1天23点59分。')
        self.assertEqual(self.kinds([time]), ['time_reversal'])
        flashbacks = [dict(time, flashback=True), self.event(2, location='旧宅', flashback=True),
                      self.event(3, kind='object', name='伞', owner='乙', location='旧宅', flashback=True)]
        self.assertFalse(self.kinds(flashbacks))
        self.assertEqual(advance_state(self.state, flashbacks, self.text), self.state)
        forward = dict(time, day=3, minute=0)
        self.assertFalse(self.kinds([forward, dict(forward, paragraph=2, quote='第3天0点。')]))
        self.assertEqual(self.kinds([dict(time, day=2, minute=29)]), ['time_reversal'])
        self.assertFalse(high_confidence(self.state, [dict(time, projected=True)], self.text))
        self.assertEqual(advance_state(self.state, [forward], self.text)['clock'], dict(day=3, minute=0))

    def test_time_requires_explicit_current_time_and_paragraph_not_exact_quote(self):
        event = dict(kind='time', day=1, minute=0, paragraph=1)
        for text in ('甲坐下。', '最早上午8点。', '预约上午8点。', '预计上午8点。',
                     '明日上午8点。', '明天上午8点。', '次日凌晨1点。', '第二天凌晨1点。', '翌日凌晨1点。'):
            self.assertFalse(high_confidence(self.state, [event], text))
        self.assertFalse(high_confidence(self.state, [dict(kind='time', day=1, minute=0)], '现在凌晨1点。'))
        self.assertFalse(high_confidence(self.state, [dict(event, source='预计')], '上午8点。'))
        self.assertEqual(high_confidence(self.state, [dict(event, quote='大约凌晨')], '现在凌晨1点。')[0]['kind'], 'time_reversal')

    def test_overnight_rollover_and_real_same_day_reversal(self):
        state = dict(self.state,clock=dict(day=3,minute=22*60))
        event = dict(kind='time',day=3,minute=8*60,paragraph=1)
        for text in ('清晨八点。','早上八点。','次日八点。','第二天八点。'):
            self.assertFalse(high_confidence(state,[event],text))
            self.assertEqual(advance_state(state,[dict(event,quote=text)],text)['clock'],dict(day=4,minute=480))
        text='他睡了一夜。\n\n钟指向八点。'
        self.assertFalse(high_confidence(state,[dict(event,paragraph=2)],text))
        self.assertEqual(advance_state(state,[dict(event,paragraph=2,quote='钟指向八点。')],text)['clock']['day'],4)
        self.assertEqual(high_confidence(state,[event],'仍是同一天，现在八点。')[0]['kind'],'time_reversal')
        self.assertEqual(state['clock'],dict(day=3,minute=1320))

    def test_reject_ungrounded_and_malformed_events_without_advancing(self):
        bad = [None, {}, self.event(quote='别处的句子'), self.event(paragraph=True),
               self.event(quote=' '), self.event(action='teleport'), self.event(present=1),
               self.event(kind='object', owner=None), self.event(flashback='yes'),
               dict(kind='time', day=True, minute=0, paragraph=1, quote='甲离开院子。')]
        issues = check_events(self.state, bad, self.text)
        self.assertEqual(len(issues), len(bad))
        self.assertTrue(all(i['kind'] == 'ungrounded_event' and not i['hard'] for i in issues))
        self.assertEqual(advance_state(self.state, bad, self.text), self.state)
        self.assertEqual(self.kinds([self.event(quote='甲走进屋里。')]), ['ungrounded_event'])

    def test_flashbacks_and_new_foreshadows_preserve_other_entries(self):
        events = [self.event(kind='foreshadow', name='钥匙', status='open'),
                  self.event(2, kind='foreshadow', name='信', status='closed', flashback=True)]
        result = advance_state(self.state, events, self.text)
        self.assertEqual(result['foreshadows'], {'信': 'open', '钥匙': 'open'})
        unknown = self.event(2, name='乙', location='屋')
        self.assertFalse(self.kinds([unknown]))
        self.assertEqual(advance_state(self.state, [unknown], self.text)['characters']['乙'],
                         dict(location='屋', present=True))

    def test_stable_order_foreshadows_and_independent_copy(self):
        events = [self.event(action='leave'), self.event(),
                  self.event(kind='foreshadow', name='信', status='closed')]
        before = copy.deepcopy((self.state, events))
        self.assertFalse(self.kinds(events))
        result = advance_state(self.state, events, self.text)
        self.assertEqual(result['foreshadows'], {'信': 'closed'})
        self.assertTrue(result['characters']['甲']['present'])
        self.assertEqual((self.state, events), before)
        result['relationships'][0]['pair'].append('丙')
        self.assertEqual(self.state['relationships'][0]['pair'], ['甲', '乙'])
        self.assertEqual(advance_state({}, [], ''), dict(characters={}, objects={}, clock=None,
                                                       relationships=[], foreshadows={}))


if __name__ == '__main__':
    unittest.main()
