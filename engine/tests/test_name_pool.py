"""Bounded parsing/state tests; temporary books and StageFixture, never live generation."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from bookengine import q1_plan
from bookengine.config import Book
from test_q1_plan import ROOT, StageFixture


class NamePoolTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target in ('socket.create_connection', 'socket.socket.connect',
                       'urllib.request.urlopen', 'bookengine.client.Client.request'):
            stack.enter_context(patch(target, side_effect=AssertionError('HTTP forbidden')))
        self.book = Book('suspense-mystery', 25, 2200, '兄妹守住祖宅')
        self.response = {
            'bible': {
                'title': '临时解析样本', 'summary': '陆亦衡护着陆以宁，亦衡称以宁为妹妹。',
                'characters': [
                    dict(name='陆亦衡', role='protagonist', address='亦衡',
                         aliases=['亦衡', '陆亦衡'], voice='陆亦衡自称亦衡，喊以宁先走。', desire='护住妹妹'),
                    dict(name='陆以宁', role='support', address='以宁',
                         aliases=['以宁', '陆以宁'], voice='陆以宁叫亦衡哥哥，以宁不肯走。', desire='守住祖宅'),
                    dict(name='陆清漪', role='antagonist', address='清漪',
                         aliases=['清漪', '陆清漪'], voice='陆清漪让亦衡留下，清漪说话慢。', desire='夺取祖宅'),
                ],
                'volumes': [dict(number=1, start=1, end=25)],
            },
            'threads': {'threads': [dict(id='family', kind='support', beats=[
                dict(id='family-v1', volume=1, description='陆亦衡找到陆以宁，亦衡与以宁回家。')])]},
        }

    def prepare(self, out, response=None):
        fixture = StageFixture(yaml.safe_dump(response or self.response, allow_unicode=True))
        result = q1_plan.prepare(fixture, self.book, out, ROOT / 'config')
        return fixture, result

    def assert_references_and_assigned(self, out, result):
        bible, threads, _ = result
        people = bible['characters']
        names = [person['name'] for person in people]
        given = [name[-2:] for name in names]
        self.assertEqual(len(people), 3)
        self.assertEqual(len(set(names)), 3, f'{out.name}: duplicate full names {names}')
        self.assertEqual(len(set(given)), 3, f'{out.name}: duplicate given names {given}')
        self.assertTrue(all(name.startswith('陆') for name in names), names)
        self.assertEqual([person['role'] for person in people], ['protagonist', 'support', 'antagonist'])
        self.assertTrue(all(person['voice'].strip() and person['desire'].strip() for person in people))
        first, second, third = names
        first_given, second_given, third_given = given
        self.assertEqual(bible['summary'], f'{first}护着{second}，{first_given}称{second_given}为妹妹。')
        self.assertEqual(people[0]['voice'], f'{first}自称{first_given}，喊{second_given}先走。')
        self.assertEqual(people[1]['voice'], f'{second}叫{first_given}哥哥，{second_given}不肯走。')
        self.assertEqual(people[2]['voice'], f'{third}让{first_given}留下，{third_given}说话慢。')
        for person, name, alias in zip(people, names, given):
            self.assertEqual(person['address'], alias)
            self.assertEqual(person['aliases'], [alias, name])
        family = next(line for line in threads['threads'] if line['id'] == 'family')
        beat = next(beat for beat in family['beats'] if beat['id'] == 'family-v1')
        self.assertEqual(beat['description'], f'{first}找到{second}，{first_given}与{second_given}回家。')
        assigned = json.loads((out / 'names.json').read_text())
        self.assertEqual(assigned['assigned'], names)
        stored = yaml.safe_load((out / 'story_bible.yaml').read_text())
        self.assertEqual(assigned['assigned'], [person['name'] for person in stored['characters']])
        self.assertEqual(stored['characters'], people)
        return set(names), set(given)

    def test_two_unwritten_peers_keep_disjoint_family_names_in_either_start_order(self):
        for order in (('book-a', 'book-b'), ('book-b', 'book-a')):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as directory:
                folders = {name: Path(directory) / name for name in order}
                for folder in folders.values():
                    folder.mkdir()
                    self.assertFalse((folder / 'story_bible.yaml').exists())
                response_before = deepcopy(self.response)
                results = {}
                for name in order:
                    fixture, result = self.prepare(folders[name])
                    self.assertEqual(len(fixture.calls), 1)
                    self.assertEqual(fixture.calls[0][0], 'plan-q1-prepare')
                    results[name] = result
                a_names, a_given = self.assert_references_and_assigned(folders['book-a'], results['book-a'])
                b_names, b_given = self.assert_references_and_assigned(folders['book-b'], results['book-b'])
                self.assertTrue(a_names.isdisjoint(b_names), (a_names, b_names))
                self.assertTrue(a_given.isdisjoint(b_given), (a_given, b_given))
                self.assertEqual(self.response, response_before)

    def test_existing_bible_resume_preserves_legacy_names_and_records_actual_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            folders = [Path(directory) / name for name in ('book-a', 'book-b')]
            for folder in folders:
                folder.mkdir()
                (folder / 'story_bible.yaml').write_text(yaml.safe_dump(self.response['bible'], allow_unicode=True))
                (folder / 'threads.yaml').write_text(yaml.safe_dump(self.response['threads'], allow_unicode=True))
            paths = [folder / name for folder in folders for name in ('story_bible.yaml', 'threads.yaml')]
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
            for attempt in range(2):
                with self.subTest(attempt=attempt):
                    fixture = StageFixture()  # Broken output must never be requested for existing YAML.
                    result = q1_plan.prepare(fixture, self.book, folders[1], ROOT / 'config')
                    self.assertEqual(fixture.calls, [])
                    self.assert_references_and_assigned(folders[1], result)
                    assigned = json.loads((folders[1] / 'names.json').read_text())
                    self.assertEqual(assigned['assigned'], ['陆亦衡', '陆以宁', '陆清漪'])
                    peers = {book['book']: book['names'] for book in assigned['books']}
                    self.assertEqual(peers, {'book-a': ['陆亦衡', '陆以宁', '陆清漪'],
                                            'book-b': ['陆亦衡', '陆以宁', '陆清漪']})
                    self.assertTrue(assigned['legacy_names_preserved'])
                    self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}, before)


if __name__ == '__main__':
    unittest.main()
