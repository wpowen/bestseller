"""Offline public CLI, queue, and topic worker contracts."""
import json
import math
import signal
import sys
from collections import Counter
import threading
import unittest
from unittest import mock

from test_webui_contracts import ENGINE, WebFixture, jobs, library, put, snapshot

THREAD_START = threading.Thread.start


class QueueTests(WebFixture):
    def test_book_and_polish_spawn_public_clis_with_budget_and_original_output(self):
        for kind, module in [('book', 'bookengine'), ('polish', 'deai')]:
            with self.subTest(kind=kind):
                folder = self.book('webui/' + kind)
                before_run = (folder / 'run.json').read_bytes()
                self.queue.launch(folder, library.read(folder / 'web.json'), kind)
                args, kwargs = self.spawn.call_args
                command = args[0]
                self.assertEqual(command[:3], [sys.executable, '-m', module])
                self.assertIn(str(folder), command)
                self.assertIn(str(self.env), command)
                self.assertFalse(kwargs.get('shell', False))
                self.assertTrue(kwargs['start_new_session'])
                self.assertEqual(kwargs['env']['BOOKENGINE_BUDGET_ROOT'], str(folder))
                cap = float(kwargs['env']['BOOKENGINE_BUDGET_USD'])
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

    def topic_worker_folder(self):
        data = self.topic()
        folder = self.lib.own / '.topics' / data['topic_id']
        topic = library.read(folder / 'topic.json')
        topic.update(status='running', candidates=[], idea='找回乘客', seed=17, history=[],
                     market=dict(date='2026-11-04', status='missing', books=[]),
                     features=dict(anchors=[], shell_counts={}),
                     axes=[dict(shell='民俗', variable='渡船', payoff='解谜', desire='守护') for _ in range(24)])
        put(folder / 'topic.json', topic)
        return folder

    def run_topic_worker(self, reply):
        """Exercise the real pipeline with offline outputs and a persisted usage ledger."""
        from ideation import run
        folder = self.topic_worker_folder(); lock = threading.Lock()
        def respond(messages, **options):
            with lock:
                stage = options['stage']
                payload = reply(stage) if callable(reply) else reply
                ledger = library.read(folder / 'http/ledger.json', [])
                ledger.append(dict(stage=stage, model='MiniMax-M3.1-Flash', status='ok',
                                   usage=dict(prompt_tokens=100, completion_tokens=100)))
                # Sibling steps read the ledger concurrently, just as in real transport.
                library.save(folder / 'http/ledger.json', ledger)
                library.save(folder / 'http' / (stage+'.text'), payload)
                return payload
        client = mock.Mock(side_effect=respond)
        client.estimate.side_effect = lambda: library.costs(folder)['cost']
        with mock.patch('deai.client.MiniMax', return_value=client) as factory, \
                mock.patch('ideation.run', wraps=run) as pipeline, \
                mock.patch.object(threading.Thread, 'start', THREAD_START), \
                mock.patch('ideation.snapshot', side_effect=AssertionError('offline snapshot only')) as market:
            jobs.topic_worker(folder, self.env)
        factory.assert_called_once_with(folder, self.env, budget=.05)
        pipeline.assert_called_once_with(folder, client, self.lib.root)
        market.assert_not_called(); self.spawn.assert_not_called()
        stages = [call.kwargs['stage'] for call in client.call_args_list]
        self.assertTrue(all(count == 1 for count in Counter(stages).values()))
        for call in client.call_args_list:
            if call.kwargs['stage'].startswith('generate-'):
                self.assertIn('找回乘客', call.args[0][0]['content'])
        self.assertLessEqual(library.costs(folder)['cost'], .05)
        return library.read(folder / 'topic.json'), folder, client

    def test_topic_worker_uses_pipeline_and_retains_rich_cards_costs_and_steps(self):
        from ideation import SCORES
        from test_ideation_web import card
        titles = ['渡口借命', '雨夜寻灯', '山神让路', '空屋等客', '雪岭藏铃', '竹林问鹤',
                  '铁匠铸梦，一锤惊醒故人', '荒城换日，小兵偷走白昼', '月下织影，织娘缝起伤口',
                  '海底种星，渔夫引回鲸群', '纸鸢送信，邮差追赶未来', '猎户认亲，狼孩寻找生母',
                  '石桥听雷？谁在石头深处说话', '孤岛牧云？云群怎会迷路', '茶馆卖雪？冬天也能泡茶',
                  '古井养鱼？怎么钓回故人的鞋', '村医辨香？谁往香囊添了药', '花市追凶？凶手也来送葬']
        cards = [dict(card(), title=title) for title in titles]
        # Hooks must differ in meaning as well as their opening words.
        for candidate, hook in zip(cards, ['她用余生换回失踪乘客', '守夜人循着灯火找到父亲', '猎户要把山神送回故乡',
            '房客醒来发现门外站满纸人', '姑娘必须在融雪前找齐铜铃', '失语少年靠仙鹤辨认仇人',
            '老铁匠一锤敲开别人的旧梦', '小兵偷走敌军的一整个白昼', '织娘缝起月亮留下的伤口',
            '渔夫种下星星引回迷途鲸群', '邮差追赶寄给未来的信', '被狼养大的孩子寻找亲娘',
            '修桥匠听见石头深处的雷鸣', '岛民驱赶云群为庄稼求雨', '店主把冬雪藏进客人的茶杯',
            '寡妇投下鱼苗却钓回故人的鞋', '郎中闻出香囊里多藏了一味药', '卖花人认出送葬队里的凶手']):
            candidate['hook'] = hook
        def reply(stage):
            if stage == 'judge':
                return json.dumps([dict(id=n, scores=dict.fromkeys(SCORES, 7)) for n in range(18)])
            n = int(stage.rsplit('-', 1)[1])
            return json.dumps(cards[n*6:(n+1)*6], ensure_ascii=False)
        topic, folder, client = self.run_topic_worker(reply)
        self.assertEqual(topic['status'], 'complete')
        self.assertEqual(len(topic['candidates']), 12)
        self.assertEqual(Counter(c.kwargs['stage'] for c in client.call_args_list),
                         Counter({'generate-0':1, 'generate-1':1, 'generate-2':1, 'judge':1}))
        self.assertEqual(topic['calls'], 4)
        self.assertAlmostEqual(topic['cost_usd'], .0003)
        for candidate in topic['candidates']:
            self.assertIn('axes', candidate); self.assertEqual(set(candidate['scores']), set(SCORES))
            self.assertIn('market_note', candidate)
        self.assertEqual({p.stem for p in (folder / 'steps').glob('*.json')},
                         {'generate-0','generate-1','generate-2','judge'})

    def test_topic_worker_malformed_generation_fails_without_retry_and_keeps_calls(self):
        for reply in ['not json', '{"wrong":"object"}', json.dumps([{}]*7)]:
            with self.subTest(reply=reply):
                topic, folder, client = self.run_topic_worker(reply)
                self.assertEqual(topic['status'], 'failed')
                self.assertEqual(topic['candidates'], [])
                # map may cancel generation tasks that have not started after an error.
                self.assertGreaterEqual(client.call_count, 1)
                self.assertLessEqual(client.call_count, 3)
                stages = [call.kwargs['stage'] for call in client.call_args_list]
                self.assertTrue(set(stages) <= {'generate-0', 'generate-1', 'generate-2'})
                self.assertEqual(len(stages), len(set(stages)))
                self.assertIn('ValueError', topic['error'])
                self.assertEqual(topic['seed'], 17)
                self.assertEqual(len(library.read(folder / 'http/ledger.json')), client.call_count)
                self.assertGreater(library.costs(folder)['cost'], 0)
                self.assertEqual(len(list((folder / 'http').glob('*.text'))), client.call_count)

    def test_topic_worker_empty_or_missing_fields_reports_actual_count_after_one_refill(self):
        for reply in ['[]', json.dumps([{'title':'缺字段'}]*6)]:
            with self.subTest(reply=reply):
                topic, folder, client = self.run_topic_worker(reply)
                self.assertEqual(topic['status'], 'complete', topic.get('error'))
                self.assertEqual(topic['candidates'], [])
                self.assertEqual(client.call_count, 4)
                self.assertIn('实际展示0个', topic['notice'])
                self.assertNotIn('judge', [c.kwargs['stage'] for c in client.call_args_list])
                if reply != '[]':
                    self.assertEqual(len(library.read(folder / 'rejected.json')), 24)

    def test_topic_worker_real_transport_budget_refuses_requests_preserving_ledger(self):
        from deai.client import MiniMax
        folder = self.topic_worker_folder()
        put(self.env, 'MINIMAX_API_KEY=offline-test-placeholder\n')
        ledger = [dict(stage='previous', model='MiniMax-M3.1-Flash', status='ok',
                       usage=dict(prompt_tokens=1000000, completion_tokens=0))]
        put(folder / 'http/ledger.json', ledger)
        with mock.patch('deai.client.MiniMax', wraps=MiniMax) as factory, \
                mock.patch.object(threading.Thread, 'start', THREAD_START), \
                mock.patch('urllib.request.urlopen', side_effect=AssertionError('no network')) as network:
            jobs.topic_worker(folder, self.env)
        factory.assert_called_once_with(folder, self.env, budget=.05)
        network.assert_not_called()
        self.assertEqual(library.read(folder / 'http/ledger.json'), ledger)
        topic = library.read(folder / 'topic.json')
        self.assertEqual(topic['status'], 'failed'); self.assertIn('LLMError', topic['error'])
        self.assertEqual(topic['seed'], 17)
        self.assertAlmostEqual(library.costs(folder)['cost'], .15)

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
