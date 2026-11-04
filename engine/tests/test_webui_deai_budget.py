"""Independent stage budgets, offline: never call models or signal real processes."""
import signal
from unittest import mock

from test_webui_contracts import WebFixture, jobs, library, put, snapshot


class DeaiBudgetTests(WebFixture):
    def completed(self, *, budget=.2, spent=.19, deai_budget=None, polish=True):
        self.queue.limit = 2
        folder = self.book(status='budget_paused', budget=budget)
        put(folder / 'run.json', {'status': 'complete', 'book': {'chapters': 2}})
        put(folder / 'calls.json', [self.row(spent)])
        data = library.read(folder / 'web.json')
        data['polish'] = polish
        if deai_budget is not None:
            data['deai_budget'] = deai_budget
        put(folder / 'web.json', data)
        return folder

    @staticmethod
    def row(cost):
        return {'usage': {'prompt_tokens': round(cost / .15 * 1e6), 'completion_tokens': 0}}

    def deai_cost(self, folder, cost=None, unknown=False):
        row = {'usage': None, 'input_bytes': 100000, 'max_tokens': 10000} if unknown else self.row(cost)
        put(folder / 'final/.deai/001/http/ledger.json', [row])

    def protected(self, folder):
        return {k: v for k, v in snapshot(folder).items() if k not in ('web.json', 'web-process.log')}

    def test_legacy_exhausted_writer_can_start_deai_with_default_budget(self):
        folder = self.completed(polish=False)
        before = self.protected(folder)
        self.queue.action('webui/story', 'postprocess')
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual(library.read(folder / 'web.json')['status'], 'polishing')
        self.assertEqual(library.read(folder / 'web.json')['deai_budget'], .5)
        self.assertEqual(self.protected(folder), before)

    def test_deai_cli_uses_final_budget_root_and_drops_all_writer_budget_environment(self):
        folder = self.completed(deai_budget=.12)
        self.deai_cost(folder, .03)
        with mock.patch.dict(jobs.os.environ, {'BOOKENGINE_BUDGET_ROOT': '/inherited',
                'BOOKENGINE_BUDGET_USD': '0.01', 'BOOKENGINE_BUDGET_OTHER': 'stale'}):
            self.queue.launch(folder, library.read(folder / 'web.json'), 'polish')
        command = self.spawn.call_args.args[0]
        self.assertEqual(command[1:5], ['-m', 'deai', 'run', str(folder)])
        self.assertEqual(command[command.index('--budget-root') + 1], str(folder / 'final'))
        self.assertAlmostEqual(float(command[command.index('--budget') + 1]), .12)
        self.assertFalse(any(k.startswith('BOOKENGINE_BUDGET_') for k in self.spawn.call_args.kwargs['env']))

    def test_resume_retry_and_manual_start_reject_exhausted_deai_budget(self):
        folder = self.completed(budget=1, spent=.01, deai_budget=.04)
        self.deai_cost(folder, .02)
        before = snapshot(folder)
        for action in ('postprocess', 'resume', 'retry'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.queue.action('webui/story', action)
            self.assertEqual(snapshot(folder), before)
        self.queue.launch(folder, library.read(folder / 'web.json'), 'polish')
        self.assertEqual(library.read(folder / 'web.json')['status'], 'budget_paused')
        self.spawn.assert_not_called()
        self.killpg.assert_not_called()

    def test_unknown_deai_cost_blocks_resume_and_stays_byte_identical(self):
        folder = self.completed(deai_budget=.05)
        self.deai_cost(folder, unknown=True)  # $0.021 upper bound, < $0.03 left.
        before = snapshot(folder)
        with self.assertRaises(ValueError):
            self.queue.action('webui/story', 'resume')
        self.assertEqual(snapshot(folder), before)
        self.assertEqual(self.lib.meta(folder)['unknown'], 1)

    def test_polish_pause_resume_and_budget_monitor_use_only_deai_budget(self):
        folder = self.completed(deai_budget=.1)
        self.deai_cost(folder, .02)
        before = self.protected(folder)
        self.queue.processes['webui/story'] = dict(p=self.child, kind='polish', folder=folder, paused=False)
        self.queue.action('webui/story', 'pause')
        self.queue.action('webui/story', 'resume')
        self.assertEqual(self.killpg.call_args, mock.call(self.child.pid, signal.SIGCONT))
        self.killpg.reset_mock()
        self.queue.tick()
        self.killpg.assert_not_called()
        self.assertEqual(self.protected(folder), before)
        self.deai_cost(folder, .08)
        self.queue.tick()
        self.killpg.assert_called_once_with(self.child.pid, signal.SIGSTOP)

    def test_concurrency_paused_deai_resumes_despite_exhausted_writer(self):
        folder = self.completed(deai_budget=.1)
        data = library.read(folder / 'web.json'); data['status'] = 'concurrency_paused'
        put(folder / 'web.json', data)
        self.queue.processes['webui/story'] = dict(p=self.child, kind='polish', folder=folder, paused=True)
        self.queue.tick()
        self.killpg.assert_called_once_with(self.child.pid, signal.SIGCONT)
        self.spawn.assert_not_called()

    def test_deai_spending_does_not_block_writer_budget(self):
        folder = self.completed(budget=.2, spent=.01, deai_budget=.04)
        self.deai_cost(folder, .2)
        put(folder / 'run.json', {'status': 'running'})
        self.queue.launch(folder, library.read(folder / 'web.json'), 'book')
        self.spawn.assert_called_once()
        self.assertAlmostEqual(float(self.spawn.call_args.kwargs['env']['BOOKENGINE_BUDGET_USD']), .2)

    def test_create_persists_default_and_custom_deai_budget_and_reserves_both(self):
        self.queue.limit = 2
        for value in (None, .12):
            data = dict(self.topic(), polish=True)
            if value is not None: data['deai_budget'] = value
            result = self.queue.create(data)
            web = library.read(self.lib.folder(result['id']) / 'web.json')
            self.assertEqual(web['budget'], .2)
            self.assertEqual(web['deai_budget'], .5 if value is None else value)
        self.assertAlmostEqual(self.queue.committed(), 1.02)

    def test_create_validates_deai_budget_even_when_polish_is_disabled(self):
        self.queue.limit = 2
        data = self.topic(); before = snapshot(self.lib.own)
        for value in (float('nan'), float('inf'), -.1, 0, 2.01, 'bad'):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                self.queue.create(dict(data, deai_budget=value))
            self.assertEqual(snapshot(self.lib.own), before)

    def test_new_book_reserves_deai_only_if_enabled(self):
        self.queue.create(dict(self.topic(), deai_budget=.5, polish=False))
        self.assertAlmostEqual(self.queue.committed(), .2)
        data = dict(self.topic(), polish=True, deai_budget=.3)
        before = snapshot(self.lib.own)
        with self.assertRaises(ValueError):
            self.queue.create(data)
        self.assertEqual(snapshot(self.lib.own), before)
        self.assertEqual(len(list(self.lib.own.glob('*/web.json'))), 1)
        self.assertEqual(library.read(self.lib.own / '.queue.json'), {'jobs': 2})

    def test_completed_writer_releases_unused_writer_reserve(self):
        folder = self.completed(spent=.05, budget=1, deai_budget=.1)
        self.assertAlmostEqual(self.queue.committed(), .15, places=6)
        self.queue.action('webui/story', 'retry')
        self.assertEqual(library.read(folder / 'web.json')['status'], 'queued_polish')
        self.assertAlmostEqual(self.queue.committed(), .15, places=6)

    def test_global_unknown_blocks_deai_start_and_running_process(self):
        folder = self.completed(spent=.49, budget=.5, deai_budget=.1)
        self.queue.limit = .5
        put(folder / 'calls.json', [{'usage': None, 'input_bytes': 3200000, 'max_tokens': 10000}])
        before = snapshot(folder)
        with self.assertRaises(ValueError): self.queue.action('webui/story', 'resume')
        self.assertEqual(snapshot(folder), before)
        self.queue.processes['webui/story'] = dict(p=self.child, kind='polish', folder=folder, paused=False)
        self.queue.tick()
        self.killpg.assert_called_once_with(self.child.pid, signal.SIGSTOP)

    def test_other_books_reserve_blocks_resume_and_launch(self):
        folder = self.completed(spent=.01, deai_budget=.1)
        data = library.read(folder / 'web.json'); data['status'] = 'complete'
        put(folder / 'web.json', data)
        self.book('webui/other', budget=.45)
        self.queue.limit = .5
        before = snapshot(folder)
        with self.assertRaises(ValueError): self.queue.action('webui/story', 'resume')
        self.assertEqual(snapshot(folder), before)
        self.queue.launch(folder, data, 'polish')
        self.assertEqual(library.read(folder / 'web.json')['status'], 'budget_paused')
        self.spawn.assert_not_called()

    def test_metadata_exposes_separate_limits_balances_and_legacy_default(self):
        folder = self.completed()
        self.deai_cost(folder, .06)
        before = snapshot(folder)
        meta = self.lib.meta(folder)
        self.assertEqual(meta['budget'], .2)
        self.assertEqual(meta['deai_budget'], .5)
        self.assertAlmostEqual(meta['writer_remaining'], .01)
        self.assertAlmostEqual(meta['deai_remaining'], .44)
        self.assertEqual(snapshot(folder), before)

    def test_auto_transition_from_completed_writer_uses_legacy_deai_default(self):
        folder = self.completed()
        self.child.poll.return_value = 0; self.child.returncode = 0
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=False)
        before = self.protected(folder)
        self.queue.tick()
        self.spawn.assert_called_once()
        command = self.spawn.call_args.args[0]
        self.assertEqual(command[2], 'deai')
        self.assertEqual(float(command[command.index('--budget')+1]), .5)
        self.assertEqual(self.protected(folder), before)

    def test_concurrency_paused_deai_cannot_resume_when_its_unknown_is_exhausted(self):
        folder = self.completed(deai_budget=.04)
        self.deai_cost(folder, unknown=True)
        data = library.read(folder / 'web.json'); data['status'] = 'concurrency_paused'
        put(folder / 'web.json', data)
        before = snapshot(folder)
        self.queue.processes['webui/story'] = dict(p=self.child, kind='polish', folder=folder, paused=True)
        self.queue.tick()
        self.killpg.assert_not_called()
        self.spawn.assert_not_called()
        self.assertEqual(snapshot(folder), before)

    def test_final_root_ledger_counts_toward_deai_and_cli_lifetime_limit(self):
        folder = self.completed(deai_budget=.1)
        put(folder / 'final/http/ledger.json', [self.row(.09)])
        before = self.protected(folder)
        self.queue.launch(folder, library.read(folder / 'web.json'), 'polish')
        self.spawn.assert_not_called()
        self.assertAlmostEqual(self.lib.meta(folder)['deai_remaining'], .01)
        self.assertEqual(self.protected(folder), before)
