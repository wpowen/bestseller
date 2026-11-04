"""Offline public CLI, queue, and topic worker contracts."""
import json
import math
import signal
import sys
from types import ModuleType
import unittest
from unittest import mock

from test_webui_contracts import ENGINE, WebFixture, jobs, library, put, snapshot


class QueueTests(WebFixture):
    def test_book_and_polish_spawn_public_clis_with_budget_and_original_output(self):
        for kind, module in [('book', 'bookengine'), ('polish', 'deai')]:
            with self.subTest(kind=kind):
                folder = self.book('webui/' + kind)
                data = library.read(folder / 'web.json'); data['deai_budget'] = .2
                put(folder / 'web.json', data)
                if kind == 'polish': put(folder / 'run.json', {'status': 'complete'})
                before_run = (folder / 'run.json').read_bytes()
                self.queue.launch(folder, library.read(folder / 'web.json'), kind)
                args, kwargs = self.spawn.call_args
                command = args[0]
                self.assertEqual(command[:3], [sys.executable, '-m', module])
                self.assertIn(str(folder), command)
                self.assertIn(str(self.env), command)
                self.assertFalse(kwargs.get('shell', False))
                self.assertTrue(kwargs['start_new_session'])
                if kind == 'book':
                    self.assertEqual(kwargs['env']['BOOKENGINE_BUDGET_ROOT'], str(folder))
                    cap = float(kwargs['env']['BOOKENGINE_BUDGET_USD'])
                else:
                    self.assertFalse(any(k.startswith('BOOKENGINE_BUDGET_') for k in kwargs['env']))
                    self.assertEqual(command[command.index('--budget-root')+1], str(folder / 'final'))
                    cap = float(command[command.index('--budget')+1])
                self.assertTrue(math.isfinite(cap))
                self.assertGreater(cap, 0)
                self.assertLessEqual(cap, .2)
                self.assertEqual((folder / 'run.json').read_bytes(), before_run)

    def test_topic_spawns_authorized_isolated_worker(self):
        result = self.queue.topic({'genre': 'cozy-mystery', 'chapters': 2, 'idea': '找回乘客'})
        command = self.spawn.call_args.args[0]
        self.assertEqual(command[:3], [sys.executable, '-m', 'tools.webui'])
        self.assertIn('--topic-worker', command)
        self.assertIn(str(self.lib.own / '.topics' / result['id']), command)
        self.assertIn(str(self.env), command)
        self.assertFalse(self.spawn.call_args.kwargs.get('shell', False))

    def run_topic_worker(self, reply):
        data = self.topic()
        folder = self.lib.own / '.topics' / data['topic_id']
        topic = library.read(folder / 'topic.json')
        topic.update(status='running', candidates=[], idea='找回乘客')
        put(folder / 'topic.json', topic)
        client = mock.Mock(return_value=reply)
        factory = mock.Mock(return_value=client)
        package = ModuleType('deai')
        package.__path__ = []
        transport = ModuleType('deai.client')
        transport.MiniMax = factory
        parser = ModuleType('deai.io')
        parser.parse_json = json.loads
        with mock.patch.dict(sys.modules, {'deai': package, 'deai.client': transport, 'deai.io': parser}):
            jobs.topic_worker(folder, self.env)
        factory.assert_called_once()
        client.assert_called_once()
        self.assertIn('找回乘客', client.call_args.args[0][0]['content'])
        self.spawn.assert_not_called()
        return library.read(folder / 'topic.json')

    def test_topic_worker_returns_twelve_candidates_from_one_call(self):
        candidates = [{'title': f'潮声{n}', 'premise': f'摆渡人寻找第{n}位乘客。'} for n in range(12)]
        topic = self.run_topic_worker(json.dumps(candidates, ensure_ascii=False))
        self.assertEqual(topic['status'], 'complete')
        self.assertEqual(topic['candidates'], candidates)

    def test_topic_worker_does_not_retry_invalid_json_or_wrong_candidate_count(self):
        for reply in ['not json', '[]', json.dumps([{'title': '缺梗概'}] * 12)]:
            with self.subTest(reply=reply):
                topic = self.run_topic_worker(reply)
                self.assertEqual(topic['status'], 'failed')
                self.assertEqual(topic['candidates'], [])

    def test_pause_and_resume_signal_same_process_without_rewriting_checkpoint(self):
        folder = self.book(status='running')
        put(folder / 'state.json', {'ledger': {'remaining': 'unknown'}, 'chapter': 1})
        put(folder / 'calls.json', [{'usage': None, 'input_bytes': 100, 'output_token_cap': 100}])
        before = {n: (folder / n).read_bytes() for n in ['run.json', 'state.json', 'calls.json']}
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=False)
        self.queue.action('webui/story', 'pause')
        self.killpg.assert_called_once_with(self.child.pid, signal.SIGSTOP)
        self.assertEqual(library.read(folder / 'web.json')['status'], 'paused')
        self.queue.action('webui/story', 'resume')
        self.assertEqual(self.killpg.call_args, mock.call(self.child.pid, signal.SIGCONT))
        self.assertEqual(library.read(folder / 'web.json')['status'], 'running')
        self.spawn.assert_not_called()
        self.assertEqual({n: (folder / n).read_bytes() for n in before}, before)

    def test_stopped_resume_and_retry_leave_every_file_unchanged(self):
        folder = self.book(status='budget_paused')
        put(folder / 'run.json', {'status': 'stopped', 'stop_reason': 'budget', 'checkpoint': 17})
        put(folder / 'state.json', {'chapter': 1, 'ledger': {'unknown': 1}})
        before = snapshot(folder)
        for action in ['resume', 'retry']:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.queue.action('webui/story', action)
            self.assertEqual(snapshot(folder), before)
        self.spawn.assert_not_called()
        self.killpg.assert_not_called()

    def test_tick_preserves_stopped_run_state_and_unknown_receipts(self):
        folder = self.book(status='queued')
        put(folder / 'run.json', {'status': 'stopped', 'stop_reason': 'budget'})
        put(folder / 'state.json', {'chapter': 1, 'unknown': True})
        put(folder / 'calls.json', [{'usage': None}])
        protected = ['run.json', 'state.json', 'calls.json']
        before = {n: (folder / n).read_bytes() for n in protected}
        self.queue.tick()
        self.assertEqual({n: (folder / n).read_bytes() for n in protected}, before)

    def test_unknown_spending_blocks_resume_without_losing_receipt(self):
        folder = self.book(status='paused', budget=.04)
        put(folder / 'calls.json', [{'usage': None, 'input_bytes': 100000,
                                    'output_token_cap': 10000}])
        before = snapshot(folder)
        with self.assertRaises(ValueError):
            self.queue.action('webui/story', 'resume')
        self.assertEqual(snapshot(folder), before)
        self.spawn.assert_not_called()

    def test_budget_tick_freezes_inflight_instead_of_restarting(self):
        folder = self.book(status='running', budget=.04)
        put(folder / 'calls.json', [{'usage': None, 'input_bytes': 100000,
                                    'output_token_cap': 10000, 'status': 'inflight'}])
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=False)
        receipt = (folder / 'calls.json').read_bytes()
        self.queue.tick()
        self.killpg.assert_called_once_with(self.child.pid, signal.SIGSTOP)
        self.assertEqual(library.read(folder / 'web.json')['status'], 'budget_paused')
        self.assertEqual((folder / 'calls.json').read_bytes(), receipt)
        self.spawn.assert_not_called()

    def test_shared_books_are_read_only_for_every_action(self):
        folder = self.book('shared')
        before = snapshot(folder)
        for action in ['pause', 'resume', 'retry']:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.queue.action('shared', action)
        self.assertEqual(snapshot(folder), before)

    def test_create_rejects_nonfinite_budget_and_preserves_topic(self):
        data = self.topic()
        before = snapshot(self.lib.own)
        for value in [float('nan'), float('inf'), -float('inf'), 'NaN', 'Infinity', -.01, .51]:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                self.queue.create(dict(data, budget=value))
        self.assertEqual(snapshot(self.lib.own), before)
        self.spawn.assert_not_called()

    def test_create_reserves_budget_and_duplicate_click_cannot_create_second_book(self):
        data = self.topic()
        result = self.queue.create(data)
        folder = self.lib.folder(result['id'])
        self.assertEqual(library.read(folder / 'web.json')['status'], 'queued')
        self.assertEqual(library.read(folder / 'web.json')['budget'], .2)
        self.assertEqual(library.read(folder / 'book.yaml')['chapters'], 2)
        with self.assertRaises(ValueError):
            self.queue.create(data)
        self.assertEqual(len(list(self.lib.own.glob('*/web.json'))), 1)
        self.spawn.assert_not_called()

    def test_committed_budget_blocks_new_book_before_any_files_change(self):
        self.book('webui/reserved', budget=.4)
        data = self.topic()
        before = snapshot(self.lib.own)
        self.assertGreaterEqual(self.queue.committed(), .4)
        with self.assertRaises(ValueError):
            self.queue.create(dict(data, budget=.2))
        self.assertEqual(snapshot(self.lib.own), before)
        self.spawn.assert_not_called()

    def test_committed_budget_blocks_topic_without_spawning_or_receipt_creation(self):
        self.book('webui/reserved', budget=.49)
        before = snapshot(self.lib.own)
        with self.assertRaises(ValueError):
            self.queue.topic({'genre': 'cozy-mystery', 'chapters': 2})
        self.assertEqual(snapshot(self.lib.own), before)
        self.spawn.assert_not_called()



if __name__ == '__main__':
    unittest.main()
