"""Offline postprocess HTTP and writer/external/web-owned cost attribution."""
import json
import math
import signal
import sys
import unittest

from test_webui_contracts import WebFixture, library, put, snapshot


class PostprocessTests(WebFixture):
    def completed_book(self, key='webui/story', *, budget=.2, deai_budget=.2):
        folder = self.book(key, status='complete', budget=budget)
        data = library.read(folder / 'web.json'); data['deai_budget'] = deai_budget
        put(folder / 'web.json', data)
        put(folder / 'run.json', {'status': 'complete', 'book': {'chapters': 2}})
        put(folder / 'state.json', {'chapter': 2, 'ledger': {'unresolved': 'preserve'}})
        # Writer: $0.015. External A postprocess: $0.30, above B's book budget.
        put(folder / 'calls.json', [self.known_row(100000)])
        put(folder / 'final/.deai/001/http/ledger.json', [self.known_row(2000000)])
        return folder

    @staticmethod
    def known_row(prompt_tokens):
        return {'model': 'MiniMax-M3.1-Flash', 'usage': {
            'prompt_tokens': prompt_tokens, 'completion_tokens': 0}, 'status': 'ok'}

    @staticmethod
    def unknown_row():
        # Conservative bound $0.021; no usage may be invented by the UI.
        return {'model': 'MiniMax-M3.1-Flash', 'usage': None, 'input_bytes': 100000,
                'max_tokens': 10000, 'status': 'inflight'}

    def append_web_cost(self, folder, row):
        ledger = folder / 'final/.deai/001/http/ledger.json'
        put(ledger, [*library.read(ledger), row])

    def postprocess(self, key='webui/story', **kwargs):
        return self.request('/api/action', method='POST',
                            data={'id': key, 'action': 'postprocess'}, **kwargs)

    def assert_only_web_metadata_changed(self, folder, before):
        after = snapshot(folder)
        self.assertEqual({k: v for k, v in after.items() if k != 'web.json'},
                         {k: v for k, v in before.items() if k != 'web.json'})

    def test_external_final_cost_is_visible_but_not_charged_to_b(self):
        folder = self.completed_book()
        before = snapshot(folder)
        meta = self.lib.meta(folder)
        self.assertAlmostEqual(meta['writer_cost'], .015)
        self.assertAlmostEqual(meta['postprocess_cost'], .3)
        self.assertAlmostEqual(meta['cost'], .315)
        self.assertAlmostEqual(self.queue.charge(folder), .015)
        self.assertAlmostEqual(self.queue.spending(), .015)
        self.assertAlmostEqual(self.queue.spending(), .015)
        self.assertEqual(snapshot(folder), before)

    def test_postprocess_public_route_records_external_baseline_without_touching_ledgers(self):
        folder = self.completed_book()
        before = snapshot(folder)
        status, _, body = self.postprocess()
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {'ok': True})
        data = library.read(folder / 'web.json')
        self.assertEqual(data['status'], 'queued_polish')
        self.assertIs(data['polish'], True)
        self.assertAlmostEqual(data['external_polish_cost'], .3)
        self.assertAlmostEqual(self.queue.charge(folder), .015)
        self.assertAlmostEqual(self.queue.spending(), .015)
        self.assert_only_web_metadata_changed(folder, before)
        self.spawn.assert_not_called()

    def test_postprocess_scheduler_spawns_public_deai_once_after_duplicate_clicks(self):
        folder = self.completed_book()
        before = snapshot(folder)
        for _ in range(2):
            self.assertEqual(self.postprocess()[0], 200)
        self.queue.tick()
        self.queue.tick()
        self.spawn.assert_called_once()
        command = self.spawn.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, '-m', 'deai', 'run'])
        self.assertIn(str(folder), command)
        self.assertIn(str(self.env), command)
        self.assertFalse(self.spawn.call_args.kwargs.get('shell', False))
        self.assertTrue(self.spawn.call_args.kwargs['start_new_session'])
        before_repeat = snapshot(folder)
        self.assertEqual(self.postprocess()[0], 400)
        self.assertEqual(snapshot(folder), before_repeat)
        self.assertEqual(self.spawn.call_count, 1)
        for name, content in before.items():
            if name != 'web.json':
                self.assertEqual((folder / name).read_bytes(), content, name)

    def test_running_or_paused_book_and_polish_processes_reject_postprocess(self):
        folder = self.completed_book()
        for kind in ['book', 'polish']:
            for paused in [False, True]:
                self.queue.processes['webui/story'] = {
                    'p': self.child, 'kind': kind, 'folder': folder, 'paused': paused}
                before = snapshot(folder)
                with self.subTest(kind=kind, paused=paused):
                    self.assertEqual(self.postprocess()[0], 400)
                    self.assertEqual(snapshot(folder), before)
        self.spawn.assert_not_called()
        self.killpg.assert_not_called()

    def test_incomplete_and_stopped_writer_cannot_postprocess_or_rewrite_state(self):
        folder = self.completed_book()
        for state in ['running', 'stopped', 'failed']:
            put(folder / 'run.json', {'status': state, 'checkpoint': 2})
            before = snapshot(folder)
            with self.subTest(state=state):
                self.assertEqual(self.postprocess()[0], 400)
                self.assertEqual(snapshot(folder), before)
        self.spawn.assert_not_called()

    def test_external_shared_book_is_read_only_even_for_postprocess(self):
        folder = self.completed_book('shared/story')
        before = snapshot(folder)
        self.assertEqual(self.postprocess('shared/story')[0], 400)
        self.assertEqual(snapshot(folder), before)
        self.spawn.assert_not_called()

    def test_postprocess_still_requires_same_origin_json(self):
        folder = self.completed_book()
        before = snapshot(folder)
        for headers in [{'Origin': None}, {'Origin': 'http://evil.invalid'},
                        {'Host': 'evil.invalid'}, {'Content-Type': 'text/plain'}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.postprocess(headers=headers)[0], 403)
                self.assertEqual(snapshot(folder), before)
        self.spawn.assert_not_called()

    def test_web_owned_increment_is_charged_without_reassigning_external_history(self):
        folder = self.completed_book()
        self.assertEqual(self.postprocess()[0], 200)
        self.append_web_cost(folder, self.known_row(200000))
        before = snapshot(folder)
        self.assertAlmostEqual(self.queue.charge(folder), .045)
        self.assertAlmostEqual(self.queue.spending(), .045)
        meta = self.lib.meta(folder)
        self.assertAlmostEqual(meta['writer_cost'], .015)
        self.assertAlmostEqual(meta['postprocess_cost'], .33)
        self.assertAlmostEqual(meta['cost'], .345)
        self.assertAlmostEqual(self.queue.spending(), .045)
        # Another click must not snapshot B's own increment as external A cost.
        self.assertEqual(self.postprocess()[0], 200)
        self.assertAlmostEqual(library.read(folder / 'web.json')['external_polish_cost'], .3)
        self.assertAlmostEqual(self.queue.charge(folder), .045)
        self.assert_only_web_metadata_changed(folder, before)

    def test_auto_polish_without_external_baseline_charges_all_its_own_final_cost(self):
        folder = self.book(status='polishing')
        data = library.read(folder / 'web.json')
        data['polish'] = True
        put(folder / 'web.json', data)
        put(folder / 'calls.json', [self.known_row(100000)])
        put(folder / 'final/.deai/001/http/ledger.json', [self.known_row(200000)])
        before = snapshot(folder)
        self.assertAlmostEqual(self.queue.charge(folder), .045)
        self.assertAlmostEqual(self.queue.spending(), .045)
        self.assertEqual(snapshot(folder), before)

    def test_unknown_external_history_remains_visible_and_byte_identical(self):
        folder = self.completed_book()
        self.append_web_cost(folder, self.unknown_row())
        before = snapshot(folder)
        self.assertEqual(self.postprocess()[0], 200)
        data = library.read(folder / 'web.json')
        self.assertAlmostEqual(data['external_polish_cost'], .321)
        self.assertAlmostEqual(self.queue.charge(folder), .015)
        self.assertEqual(self.lib.meta(folder)['unknown'], 1)
        self.assertAlmostEqual(self.lib.meta(folder)['postprocess_cost'], .321)
        self.assert_only_web_metadata_changed(folder, before)

    def test_web_owned_unknown_is_counted_and_allows_retry_without_losing_ledger(self):
        folder = self.completed_book(budget=.05, deai_budget=.04)
        self.assertEqual(self.postprocess()[0], 200)
        self.append_web_cost(folder, self.unknown_row())
        before = snapshot(folder)
        self.assertAlmostEqual(self.queue.charge(folder), .036)
        self.assertEqual(self.lib.meta(folder)['unknown'], 1)
        for action in ['postprocess', 'retry']:
            with self.subTest(action=action):
                status, _, _ = self.request('/api/action', method='POST',
                    data={'id': 'webui/story', 'action': action})
                self.assertEqual(status, 200)
                self.assert_only_web_metadata_changed(folder, before)
        self.spawn.assert_not_called()

    def test_postprocess_ignores_old_local_budget_and_saves_external_baseline(self):
        folder = self.completed_book(budget=.04, deai_budget=.02)
        before = snapshot(folder)
        self.assertEqual(self.postprocess()[0], 200)
        self.assert_only_web_metadata_changed(folder, before)
        self.assertAlmostEqual(library.read(folder / 'web.json')['external_polish_cost'], .3)
        self.spawn.assert_not_called()

    def test_postprocess_high_global_spending_preserves_all_receipts_and_queues(self):
        folder = self.completed_book()
        other = self.completed_book('webui/finished')
        put(other / 'calls.json', [self.known_row(3200000)])  # $0.48 already spent.
        before = snapshot(self.lib.own)
        self.assertGreater(self.queue.spending(), .47)
        self.assertEqual(self.postprocess()[0], 200)
        self.assertEqual({k: v for k, v in snapshot(self.lib.own).items() if k != 'story/web.json'},
                         {k: v for k, v in before.items() if k != 'story/web.json'})
        self.spawn.assert_not_called()

    def test_postprocess_ignores_other_books_old_reserved_budget(self):
        self.completed_book()
        self.book('webui/reserved', budget=.46)
        before = snapshot(self.lib.own)
        self.assertEqual(self.postprocess()[0], 200)
        self.assertEqual({k: v for k, v in snapshot(self.lib.own).items() if k != 'story/web.json'},
                         {k: v for k, v in before.items() if k != 'story/web.json'})
        self.spawn.assert_not_called()

    def test_tick_counts_only_b_increment_and_keeps_unknown_inflight_running(self):
        folder = self.completed_book(budget=.05, deai_budget=.04)
        self.assertEqual(self.postprocess()[0], 200)
        self.queue.tick()
        self.spawn.assert_called_once()
        self.killpg.assert_not_called()
        self.append_web_cost(folder, self.unknown_row())
        receipt = snapshot(folder)
        self.queue.tick()
        self.killpg.assert_not_called()
        self.assertEqual(library.read(folder / 'web.json')['status'], 'polishing')
        self.assertEqual(self.spawn.call_count, 1)
        self.assert_only_web_metadata_changed(folder, receipt)

    def test_retry_after_finished_polish_preserves_original_external_baseline(self):
        folder = self.completed_book()
        self.assertEqual(self.postprocess()[0], 200)
        self.queue.tick()
        self.append_web_cost(folder, self.known_row(200000))
        self.child.poll.return_value = 0
        self.child.returncode = 0
        self.queue.tick()
        self.assertEqual(library.read(folder / 'web.json')['status'], 'complete')
        before = snapshot(folder)
        self.assertEqual(self.postprocess()[0], 200)
        self.assertAlmostEqual(library.read(folder / 'web.json')['external_polish_cost'], .3)
        self.assertAlmostEqual(self.queue.charge(folder), .045)
        self.assert_only_web_metadata_changed(folder, before)

    def test_http_metadata_reports_complete_cost_split_and_finite_score(self):
        folder = self.completed_book()
        for n, score in [(1, 7.25), (2, 8.75)]:
            put(folder / f'final/chapter-{n:03d}.deai.json',
                {'status': 'polished', 'complete': True, 'after_score': score})
        before = snapshot(folder)
        status, _, body = self.request('/api/book?id=webui%2Fstory')
        self.assertEqual(status, 200)
        meta = json.loads(body)['meta']
        self.assertAlmostEqual(meta['writer_cost'], .015)
        self.assertAlmostEqual(meta['postprocess_cost'], .3)
        self.assertAlmostEqual(meta['cost'], .315)
        self.assertEqual(meta['score'], 8)
        status, _, body = self.request('/api/books')
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertAlmostEqual(result['spending'], .015)
        self.assertAlmostEqual(result['books'][0]['postprocess_cost'], .3)
        self.assertEqual(snapshot(folder), before)

    def test_metadata_score_stays_unknown_without_finite_numeric_evidence(self):
        folder = self.completed_book()
        for score in [None, '8', float('nan'), float('inf'), -float('inf')]:
            for n in [1, 2]:
                put(folder / f'final/chapter-{n:03d}.deai.json',
                    {'status': 'polished', 'complete': True, 'after_score': score})
            with self.subTest(score=score):
                status, _, body = self.request('/api/book?id=webui%2Fstory')
                self.assertEqual(status, 200)
                self.assertIsNone(json.loads(body)['meta']['score'])
        self.assertTrue(math.isfinite(self.queue.charge(folder)))


if __name__ == '__main__':
    unittest.main()
