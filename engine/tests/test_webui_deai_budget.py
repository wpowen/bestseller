"""Legacy budget migration and retained stage costs; no models or real signals."""
import signal
from unittest import mock

from test_webui_contracts import WebFixture, jobs, library, put, snapshot


class DeaiBudgetTests(WebFixture):
    def completed(self, *, budget=.2, spent=.19, deai_budget=None, polish=True):
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
        return {k: v for k, v in snapshot(folder).items() if k not in ('web.json', 'web-process.log', 'runtime.json')}

    def test_legacy_exhausted_writer_can_start_deai_without_budget(self):
        folder = self.completed(polish=False)
        before = self.protected(folder)
        self.queue.action('webui/story', 'postprocess')
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual(library.read(folder / 'web.json')['status'], 'polishing')
        self.assertNotIn('deai_budget', library.read(folder / 'web.json'))
        self.assertEqual(self.protected(folder), before)

    def test_deai_cli_has_no_budget_and_drops_all_writer_budget_environment(self):
        folder = self.completed(deai_budget=.12)
        self.deai_cost(folder, .03)
        with mock.patch.dict(jobs.os.environ, {'BOOKENGINE_BUDGET_ROOT': '/inherited',
                'BOOKENGINE_BUDGET_USD': '0.01', 'BOOKENGINE_BUDGET_OTHER': 'stale'}):
            self.queue.launch(folder, library.read(folder / 'web.json'), 'polish')
        command = self.spawn.call_args.args[0]
        self.assertEqual(command[1:5], ['-m', 'deai', 'run', str(folder)])
        self.assertNotIn('--budget-root', command)
        self.assertNotIn('--budget', command)
        self.assertFalse(any(k.startswith('BOOKENGINE_BUDGET_') for k in self.spawn.call_args.kwargs['env']))

    def test_resume_retry_and_manual_start_ignore_exhausted_deai_budget(self):
        folder = self.completed(budget=1, spent=.01, deai_budget=.04)
        self.deai_cost(folder, .02)
        before = self.protected(folder)
        for action in ('postprocess', 'resume', 'retry'):
            with self.subTest(action=action):
                self.queue.action('webui/story', action)
            self.assertEqual(self.protected(folder), before)
        self.queue.launch(folder, library.read(folder / 'web.json'), 'polish')
        self.assertEqual(library.read(folder / 'web.json')['status'], 'polishing')
        self.spawn.assert_called_once()
        self.killpg.assert_not_called()

    def test_unknown_deai_cost_allows_resume_and_stays_byte_identical(self):
        folder = self.completed(deai_budget=.05)
        self.deai_cost(folder, unknown=True)  # $0.021 upper bound, < $0.03 left.
        before = self.protected(folder)
        self.queue.action('webui/story', 'resume')
        self.assertEqual(self.protected(folder), before)
        self.assertEqual(self.lib.meta(folder)['unknown'], 1)

    def test_polish_pause_resume_preserves_manual_control_at_any_cost(self):
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
        self.killpg.assert_not_called()

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
        self.assertNotIn('BOOKENGINE_BUDGET_USD', self.spawn.call_args.kwargs['env'])

    def test_create_discards_default_and_custom_deai_budget(self):
        for value in (None, .12):
            data = dict(self.topic(), polish=True)
            if value is not None: data['deai_budget'] = value
            result = self.queue.create(data)
            web = library.read(self.lib.folder(result['id']) / 'web.json')
            self.assertNotIn('budget', web)
            self.assertNotIn('deai_budget', web)
        self.assertEqual(self.queue.spending(), 0)

    def test_create_ignores_obsolete_deai_budget_even_when_polish_is_disabled(self):
        for value in (float('nan'), float('inf'), -.1, 0, 2.01, 'bad'):
            with self.subTest(value=value):
                result = self.queue.create(dict(self.topic(), deai_budget=value))
                self.assertNotIn('deai_budget', library.read(self.lib.folder(result['id']) / 'web.json'))

    def test_new_books_preserve_auto_deai_choice_without_reservations(self):
        self.queue.create(dict(self.topic(), deai_budget=.5, polish=False))
        self.assertEqual(self.queue.spending(), 0)
        data = dict(self.topic(), polish=True, deai_budget=.3)
        self.queue.create(data)
        self.assertEqual(len(list(self.lib.own.glob('*/web.json'))), 2)
        self.assertEqual(library.read(self.lib.own / '.queue.json'), {'jobs': 2})

    def test_completed_writer_releases_unused_writer_reserve(self):
        folder = self.completed(spent=.05, budget=1, deai_budget=.1)
        self.assertAlmostEqual(self.queue.spending(), .05, places=6)
        self.queue.action('webui/story', 'retry')
        self.assertEqual(library.read(folder / 'web.json')['status'], 'queued_polish')
        self.assertAlmostEqual(self.queue.spending(), .05, places=6)

    def test_global_unknown_does_not_block_deai_start_or_running_process(self):
        folder = self.completed(spent=.49, budget=.5, deai_budget=.1)
        put(folder / 'calls.json', [{'usage': None, 'input_bytes': 3200000, 'max_tokens': 10000}])
        before = self.protected(folder)
        self.queue.action('webui/story', 'resume')
        self.assertEqual(self.protected(folder), before)
        self.queue.processes['webui/story'] = dict(p=self.child, kind='polish', folder=folder, paused=False)
        self.queue.tick()
        self.killpg.assert_not_called()

    def test_other_books_old_reserve_does_not_block_resume_or_launch(self):
        folder = self.completed(spent=.01, deai_budget=.1)
        data = library.read(folder / 'web.json'); data['status'] = 'complete'
        put(folder / 'web.json', data)
        self.book('webui/other', budget=.45)
        before = self.protected(folder)
        self.queue.action('webui/story', 'resume')
        self.assertEqual(self.protected(folder), before)
        self.queue.launch(folder, data, 'polish')
        self.assertEqual(library.read(folder / 'web.json')['status'], 'polishing')
        self.spawn.assert_called_once()

    def test_metadata_exposes_separate_costs_without_limits_or_balances(self):
        folder = self.completed()
        self.deai_cost(folder, .06)
        before = snapshot(folder)
        meta = self.lib.meta(folder)
        self.assertAlmostEqual(meta['writer_cost'], .19, places=6)
        self.assertAlmostEqual(meta['deai_cost'], .06)
        for name in ('budget', 'deai_budget', 'writer_remaining', 'deai_remaining'):
            self.assertNotIn(name, meta)
        self.assertEqual(snapshot(folder), before)

    def test_auto_transition_from_completed_writer_uses_no_deai_budget(self):
        folder = self.completed()
        self.child.poll.return_value = 0; self.child.returncode = 0
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=False)
        before = self.protected(folder)
        self.queue.tick()
        self.spawn.assert_called_once()
        command = self.spawn.call_args.args[0]
        self.assertEqual(command[2], 'deai')
        self.assertNotIn('--budget', command)
        self.assertEqual(self.protected(folder), before)

    def test_concurrency_paused_deai_can_resume_despite_unknown_cost(self):
        folder = self.completed(deai_budget=.04)
        self.deai_cost(folder, unknown=True)
        data = library.read(folder / 'web.json'); data['status'] = 'concurrency_paused'
        put(folder / 'web.json', data)
        before = self.protected(folder)
        self.queue.processes['webui/story'] = dict(p=self.child, kind='polish', folder=folder, paused=True)
        self.queue.tick()
        self.killpg.assert_called_once_with(self.child.pid, signal.SIGCONT)
        self.spawn.assert_not_called()
        self.assertEqual(self.protected(folder), before)

    def test_final_root_ledger_counts_toward_deai_cost_without_blocking(self):
        folder = self.completed(deai_budget=.1)
        put(folder / 'final/http/ledger.json', [self.row(.09)])
        before = self.protected(folder)
        self.queue.launch(folder, library.read(folder / 'web.json'), 'polish')
        self.spawn.assert_called_once()
        self.assertAlmostEqual(self.lib.meta(folder)['deai_cost'], .09)
        self.assertEqual(self.protected(folder), before)
