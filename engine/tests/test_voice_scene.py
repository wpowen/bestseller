"""Offline INBOX15/16 fixtures: generic names, no manuscript or blind keys."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tools.voice_scene_checks import chapter_checks, outline_checks, scene_hits


CAST = [
    {'name': '许泊川', 'gender': 'male', 'aliases': ['泊川', '许哥'],
     'forbidden_aliases': ['阿衡']},
    {'name': '柳知夏', 'gender': 'female', 'aliases': ['知夏']},
    {'name': '杜怀安', 'gender': 'male', 'aliases': []},
]


def findings(report):
    return report['confirmed'] + report['candidates']


class VoiceSceneTests(unittest.TestCase):
    def test_scene_rules_group_whole_original_paragraphs_with_unicode_offsets(self):
        text = ('# 第一章\r\n\r\n  雨幕压低了屋檐。灯影舔着墙角。\r\n\r\n'
                '桌上有铜盆、木勺、旧水壶。他把手指挪了半寸。\r\n')
        hits = scene_hits(text)
        self.assertEqual([h['paragraph'] for h in hits], [2, 3])
        self.assertTrue({'opening_empty_shot', 'personified_light', 'generic_imagery'}
                        <= set(hits[0]['kinds']))
        self.assertTrue({'object_list', 'precise_micro_action'} <= set(hits[1]['kinds']))
        for hit in hits:
            self.assertEqual(set(hit), {'paragraph', 'start', 'end', 'kinds', 'text'})
            self.assertEqual(text[hit['start']:hit['end']], hit['text'])
        for phrase in ('他将杯沿转了半圈。', '她的指尖抬起一线。'):
            self.assertIn('precise_micro_action', scene_hits(phrase)[0]['kinds'])
        self.assertIn('object_list', scene_hits('灯箱、摊位、积水铺满街口。')[0]['kinds'])

    def test_only_first_three_body_sentences_can_be_opening_empty_shots(self):
        for prefix, expected in (('', True), ('门开了。人散了。', True),
                                 ('门开了。人散了。车走了。', False)):
            hits = scene_hits('# 第一章 开门\n\n' + prefix + '细雨落在石阶上。')
            self.assertEqual(any('opening_empty_shot' in h['kinds'] for h in hits), expected)
        for text in ('雨下了一整夜。', '风从街口吹来。', '月光铺满窗台。'):
            self.assertIn('opening_empty_shot', scene_hits(text)[0]['kinds'])
        for text in ('她冻得发抖，雨水灌进破鞋。', '“天快下雨了。”',
                     '“细雨落在窗沿。月光照着水面。”',
                     '许泊川望着窗外的灯光，骂了一声。'):
            self.assertFalse(any('opening_empty_shot' in h['kinds'] for h in scene_hits(text)))

    def test_scene_unrelated_normal_examples_and_empty_input(self):
        for text in ('', '粮站老会计抱起算盘，催他把米袋扛进屋。',
                     '他开门、擦桌、烧水。', '墙边放着铜盆、木勺。',
                     '师傅量出半寸的榫头。', '她拉开窗帘，灯光照到桌上。'):
            self.assertEqual(scene_hits(text), [], text)

    def test_headings_allow_one_correct_leading_title_and_flag_internal_titles(self):
        for heading in ('## 第二章 雨棚', '##第二章', '第2章 雨棚', '＃＃ 第２章 雨棚'):
            text = '许泊川进门。\n' + heading + '\n他收好车票。'
            report = chapter_checks(text, CAST)
            hit, = report['confirmed']
            self.assertEqual(hit['kind'], 'internal_chapter_heading')
            self.assertEqual(text[hit['start']:hit['end']], hit['text'])
        self.assertEqual(findings(chapter_checks('\n## 第二章 雨棚\n正文结束。', CAST, 2)), [])
        self.assertTrue(chapter_checks('## 第二章 雨棚\n正文结束。', CAST, 1)['confirmed'])
        self.assertTrue(chapter_checks('正文结束。\n## 第一章', CAST, 1)['confirmed'])
        self.assertEqual(findings(chapter_checks('他问：“第二章叫什么？”', CAST)), [])

    def test_adjacent_and_near_exact_repetition_with_han_and_distance_thresholds(self):
        sentence = '灰色货车从巷口右侧慢慢驶来。'
        for middle in ('', '“走吧。”', '他关上门。她收起伞。'):
            report = chapter_checks(sentence + middle + sentence, CAST)
            hits = [h for h in report['confirmed'] if h['kind'] == 'repeated_sentence']
            self.assertEqual(len(hits), 1, middle)
            self.assertEqual(hits[0]['previous']['text'], sentence)
        for text in ('“你先走吧。”“你先走吧。”', '天色变了。天色变了。',
                     '甲abcde乙fghi。甲abcde乙fghi。',
                     sentence + '他进门。她坐下。车停了。灯灭了。' + sentence):
            self.assertFalse(any(h['kind'] == 'repeated_sentence'
                                 for h in findings(chapter_checks(text, CAST))))

    def test_near_repetition_is_only_a_candidate_not_proof(self):
        text = '灰色货车从巷口右侧慢慢驶来。\n\n他关了门。\n\n灰色货车从巷口右边慢慢驶来。'
        report = chapter_checks(text, CAST)
        hit, = [h for h in report['candidates'] if h['kind'] == 'similar_sentence']
        self.assertGreaterEqual(hit['similarity'], .88)
        self.assertFalse(report['confirmed'])
        self.assertEqual(hit['paragraph'], 3)
        for left, right in (('12', '13'), ('A', 'B')):
            changed = f'灰色货车停在{left}号仓库前面。灰色货车停在{right}号仓库前面。'
            self.assertFalse(chapter_checks(changed, CAST)['confirmed'])
        self.assertEqual(findings(chapter_checks('老会计催他搬米袋。货车绕过桥头开进粮站。', CAST)), [])

    def test_repeated_unknown_explicit_names_and_registered_aliases(self):
        for name in ('赵铭', '陆寒生'):
            text = f'{name}说：“快走。”\n\n{name}把门推开。\n\n{name}走向河岸。'
            hits = chapter_checks(text, CAST)['candidates']
            self.assertEqual({h['name'] for h in hits if h['kind'] == 'unknown_name'}, {name})
        for name in ('许泊川', '泊川', '许哥', '知夏', '周围'):
            text = f'{name}说：“快走。”\n\n{name}把门推开。\n\n{name}走向河岸。'
            self.assertFalse(any(h['kind'] in ('unknown_name', 'unregistered_nickname')
                                 for h in findings(chapter_checks(text, CAST))))
        self.assertFalse(any(h['kind'] == 'unknown_name' for h in
                             findings(chapter_checks('赵铭开门。赵铭走了。', CAST))))

    def test_surname_drift_generalizes_and_never_accuses_other_known_people(self):
        for bad, expected in (('陈知夏', '柳知夏'), ('魏泊川', '许泊川')):
            hit, = [h for h in chapter_checks(bad + '推门进来。', CAST)['candidates']
                    if h['kind'] == 'surname_drift']
            self.assertEqual((hit['name'], hit['expected']), (bad, expected))
        cast = CAST + [{'name': '陈知夏', 'gender': 'female', 'aliases': []},
                       {'name': '欧阳泊川', 'gender': 'male', 'aliases': []}]
        self.assertFalse(any(h['kind'] == 'surname_drift' for h in
                             findings(chapter_checks('陈知夏来了，欧阳泊川举起手。', cast))))

    def test_forbidden_aliases_and_nicknames_have_distinct_evidence(self):
        report = chapter_checks('“阿衡，砚哥来了。”杜怀安说。', CAST)
        self.assertIn('forbidden_alias', {h['kind'] for h in report['confirmed']})
        nickname, = [h for h in report['candidates'] if h['kind'] == 'unregistered_nickname']
        self.assertEqual(nickname['name'], '砚哥')
        allowed = deepcopy(CAST)
        allowed[0]['aliases'].append('砚哥')
        self.assertFalse(any(h['kind'] == 'unregistered_nickname'
                             for h in findings(chapter_checks('砚哥推开门。', allowed))))
        for text in ('大哥和哥哥都回来了。', '柳知夏笑着喊他大哥。', '阿姨牵着小孩走上小路。'):
            self.assertFalse(findings(chapter_checks(text, CAST)), text)

    def test_gender_and_seats_are_candidates_with_no_movement_or_object_inference(self):
        for text in ('柳知夏推开门，他抬起头。', '柳知夏是个男人。',
                     '许泊川在副驾，同时后排传来许泊川的声音。'):
            report = chapter_checks(text, CAST)
            self.assertFalse(report['confirmed'])
            self.assertTrue(report['candidates'], text)
        for text in ('许泊川看见少女时，他笑了。', '柳知夏看见他走了。',
                     '柳知夏看见杜怀安，他点了点头。', '柳知夏说：“他是个男人。”',
                     '“柳知夏是个男人。这是谎话。”',
                     '柳知夏进了屋。\n\n他坐下。',
                     '许泊川从副驾下车，又坐进后排。',
                     '许泊川在副驾。\n\n杜怀安在后排说话。'):
            self.assertFalse(any(h['kind'] in ('gender_conflict', 'seat_conflict')
                                 for h in findings(chapter_checks(text, CAST))), text)

    def test_outline_only_repeats_explicit_final_results_in_the_same_contest(self):
        def scene(opponent='守门人', contest='桥头赛', result='final_win', **extra):
            return dict(opponent=opponent, contest_id=contest, result=result, **extra)
        for result in ('final_win', 'finalwin', 'win', '最终获胜', 'final_loss'):
            outline = {'scenes': [scene(result=result), scene(result=result)]}
            hit, = outline_checks(outline)
            self.assertEqual((hit['kind'], hit['scene'], hit['previous_scene']),
                             ('repeated_final_result', 2, 1))
            self.assertEqual(hit['status'], 'confirmed')
        normal = {'scenes': [scene(result='误判获胜'), scene(result='temporary_win'),
                             scene(result='短时压制'), scene(), scene(contest='河口赛'),
                             scene(opponent='巡夜人'), scene(contest=None), scene(contest=None),
                             scene(contest='旧赛', result='final_win', recap=True),
                             scene(contest='旧赛'), scene(final=False)]}
        self.assertEqual(outline_checks(normal), [])
        self.assertEqual(outline_checks({'scenes': [scene(), scene(result='final_loss')]
                                       })[0]['kind'], 'repeated_final_result')
        self.assertEqual(outline_checks({}), [])

    def test_functions_are_repeatable_json_serializable_and_never_mutate_or_do_io(self):
        text = '柳知夏推门，他抬头。\r\n\r\n灯影咬住窗沿。'
        cast = deepcopy(CAST)
        outline = {'scenes': [{'opponent': '守门人', 'contest_id': '桥头赛', 'result': 'final_win'}]}
        before = deepcopy((text, cast, outline))
        expected = (scene_hits(text), chapter_checks(text, cast), outline_checks(outline))
        with patch('builtins.open', side_effect=AssertionError('runtime I/O')), \
             patch('socket.socket', side_effect=AssertionError('network')):
            actual = (scene_hits(text), chapter_checks(text, cast), outline_checks(outline))
        self.assertEqual(expected, actual)
        self.assertEqual(before, (text, cast, outline))
        self.assertTrue(json.dumps(actual, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
