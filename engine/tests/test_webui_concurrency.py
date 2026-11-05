"""Offline concurrency changes preserve processes, pause intent, and queue settings."""
import json
import signal
import unittest
from unittest import mock

from test_webui_contracts import ENGINE, WebFixture, jobs, library, put, snapshot


class ConcurrencyTests(WebFixture):
    def launch_pair(self, second_kind='book', second_budget=.1):
        children = [mock.Mock(pid=880101, returncode=None),
                    mock.Mock(pid=880102, returncode=None)]
        for child in children:
            child.poll.return_value = None
        self.spawn.side_effect = children
        for index, (kind, budget) in enumerate([('book', .1), (second_kind, second_budget)]):
            folder = self.book(f'webui/active-{index}', status='queued', budget=budget, final=kind != 'polish')
            put(folder / 'state.json', {'checkpoint': index, 'ledger': 'preserve'})
            put(folder / 'calls.json', [])
            if kind == 'polish':
                put(folder / 'run.json', {'status': 'complete', 'book': {'chapters': 2}})
                data = library.read(folder / 'web.json'); data.update(polish=True, deai_budget=budget)
                put(folder / 'web.json', data)
            self.queue.launch(folder, library.read(folder / 'web.json'), kind)
        self.assertEqual(self.spawn.call_count, 2)
        self.spawn.reset_mock(side_effect=True)
        return children

    def create_with_jobs(self, count):
        data = dict(self.topic(), jobs=count, budget=.1)
        status, _, body = self.request('/api/books', method='POST', data=data)
        self.assertEqual(status, 200, body)
        return json.loads(body)['id']

    def paused_item(self):
        paused = [item for item in self.queue.processes.values() if item['paused']]
        self.assertEqual(len(paused), 1)
        return paused[0]

    def assert_engine_files_preserved(self, folder, before):
        self.assertEqual({k: v for k, v in snapshot(folder).items() if k != 'web.json'},
                         {k: v for k, v in before.items() if k != 'web.json'})

    def test_lowering_two_to_one_stops_excess_and_resumes_original_pid_when_slot_frees(self):
        self.launch_pair(second_kind='polish')
        before = {key: snapshot(item['folder']) for key, item in self.queue.processes.items()}
        new_key = self.create_with_jobs(1)
        self.assertEqual(self.queue.jobs, 1)
        self.queue.tick()
        paused = self.paused_item()
        process = paused['p']
        self.killpg.assert_called_once_with(process.pid, signal.SIGSTOP)
        self.assertEqual(library.read(paused['folder'] / 'web.json')['status'], 'concurrency_paused')
        self.assertEqual(sum(not item['paused'] for item in self.queue.processes.values()), 1)
        self.assertEqual(library.read(self.lib.folder(new_key) / 'web.json')['status'], 'queued')
        self.queue.tick()
        self.assertEqual(self.killpg.call_count, 1, 'Repeated tick must not STOP twice')
        for key, item in self.queue.processes.items():
            self.assert_engine_files_preserved(item['folder'], before[key])
        active = next(item for item in self.queue.processes.values() if not item['paused'])
        active['p'].poll.return_value = 0
        active['p'].returncode = 0
        put(active['folder'] / 'run.json', {'status': 'complete', 'book': {'chapters': 2}})
        paused_before = snapshot(paused['folder'])
        self.queue.tick()
        self.assertEqual(self.killpg.call_args, mock.call(process.pid, signal.SIGCONT))
        self.assertEqual(self.killpg.call_count, 2)
        self.assertIs(paused['p'], process)
        self.assertFalse(paused['paused'])
        expected = 'polishing' if paused['kind'] == 'polish' else 'running'
        self.assertEqual(library.read(paused['folder'] / 'web.json')['status'], expected)
        self.assert_engine_files_preserved(paused['folder'], paused_before)
        self.spawn.assert_not_called()

    def test_raising_limit_resumes_frozen_process_before_spawning_new_queued_books(self):
        self.launch_pair()
        self.create_with_jobs(1)
        self.queue.tick()
        paused = self.paused_item()
        original = paused['p']
        self.create_with_jobs(2)
        self.killpg.reset_mock()
        self.queue.tick()
        self.killpg.assert_called_once_with(original.pid, signal.SIGCONT)
        self.assertIs(paused['p'], original)
        self.assertEqual(sum(not item['paused'] for item in self.queue.processes.values()), 2)
        self.spawn.assert_not_called()

    def test_manual_pause_stays_paused_while_other_expensive_process_keeps_running(self):
        self.launch_pair(second_budget=.04)
        self.queue.action('webui/active-0', 'pause')
        budget_folder = self.lib.folder('webui/active-1')
        put(budget_folder / 'calls.json', [{'usage': None, 'input_bytes': 100000,
                                           'output_token_cap': 10000}])  # $0.021 upper bound.
        self.queue.tick()
        self.assertEqual(library.read(budget_folder / 'web.json')['status'], 'running')
        before = snapshot(self.lib.own)
        self.killpg.reset_mock()
        self.queue.tick()
        self.queue.tick()
        self.killpg.assert_not_called()
        self.spawn.assert_not_called()
        self.assertEqual(snapshot(self.lib.own), before)
        self.assertEqual(sum(item['paused'] for item in self.queue.processes.values()), 1)
        self.assertEqual(library.read(self.lib.folder('webui/active-0') / 'web.json')['status'], 'paused')

    def test_concurrency_pause_auto_resumes_even_with_large_unknown_cost(self):
        self.launch_pair()
        self.create_with_jobs(1)
        self.queue.tick()
        paused = self.paused_item()
        put(paused['folder'] / 'calls.json', [{'usage': None, 'input_bytes': 500000,
                                              'output_token_cap': 10000}])  # $0.081 upper bound.
        active = next(item for item in self.queue.processes.values() if not item['paused'])
        active['p'].poll.return_value = 0
        active['p'].returncode = 0
        put(active['folder'] / 'run.json', {'status': 'complete', 'book': {'chapters': 2}})
        before = snapshot(paused['folder'])
        self.killpg.reset_mock()
        self.queue.tick()
        self.killpg.assert_called_once_with(paused['p'].pid, signal.SIGCONT)
        self.assertFalse(paused['paused'])
        self.assert_engine_files_preserved(paused['folder'], before)
        self.assertLessEqual(sum(not item['paused'] for item in self.queue.processes.values()), 1)

    def test_successful_creation_persists_jobs_and_fresh_queue_reads_setting(self):
        self.assertEqual(self.queue.jobs, 2)
        self.create_with_jobs(1)
        settings = self.lib.own / '.queue.json'
        self.assertEqual(library.read(settings), {'jobs': 1})
        before = snapshot(self.lib.own)
        self.queue.exclusive.close()
        restored = jobs.Queue(self.lib, self.env, ENGINE)
        self.addCleanup(restored.exclusive.close)
        self.assertEqual(restored.jobs, 1)
        self.assertEqual({k: v for k, v in snapshot(self.lib.own).items() if not k.endswith('web.json')},
                         {k: v for k, v in before.items() if not k.endswith('web.json')})
        self.spawn.assert_not_called()

    def test_rejected_creation_never_changes_memory_or_persisted_jobs(self):
        self.create_with_jobs(3)
        self.book('webui/reserved', budget=.39)
        data = dict(self.topic(), jobs=1, budget=.1)
        before = snapshot(self.lib.own)
        for invalid in [dict(data, model='unsupported'), dict(data, candidate=-1),
                        dict(data, jobs=float('nan'))]:
            with self.subTest(model=invalid['model'], candidate=invalid['candidate'], jobs=invalid['jobs']):
                status, _, _ = self.request('/api/books', method='POST', data=invalid)
                self.assertEqual(status, 400)
                self.assertEqual(self.queue.jobs, 3)
                self.assertEqual(library.read(self.lib.own / '.queue.json'), {'jobs': 3})
                self.assertEqual(snapshot(self.lib.own), before)
        self.spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
