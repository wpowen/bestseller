"""Run-to-finish Web contracts with fake subprocesses and temporary book folders."""
import json
import fcntl
import os
import signal
import subprocess
import sys
import time
from unittest import mock

from test_webui_contracts import ENGINE, WEBUI, WebFixture, jobs, library, put, server, snapshot

POPEN = subprocess.Popen
KILL = os.kill


class RunFinishWebTests(WebFixture):
    def restart(self):
        self.queue.exclusive.close()
        self.queue = jobs.Queue(self.lib, self.env, ENGINE)
        self.http.queue = self.queue
        self.addCleanup(self.queue.exclusive.close)
        return self.queue

    def test_configuration_and_book_response_have_costs_without_budget_fields(self):
        configuration = server.config(self.env)
        self.assertNotIn('budgets', configuration)
        self.book()
        status, _, body = self.request('/api/books')
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertNotIn('limit', result)
        self.assertIn('spending', result)
        for key in ('budget', 'deai_budget', 'total_budget', 'writer_remaining', 'deai_remaining'):
            self.assertNotIn(key, result['books'][0])
        self.assertIn('writer_cost', result['books'][0])

    def test_new_book_ignores_legacy_budgets_and_remembers_auto_deai(self):
        data = dict(self.topic(), budget=0, deai_budget=0, total_budget=0, polish=True)
        result = self.queue.create(data)
        saved = library.read(self.lib.folder(result['id']) / 'web.json')
        self.assertTrue(saved['auto_deai'])
        self.assertTrue(saved['polish'])
        for key in ('budget', 'deai_budget', 'total_budget'):
            self.assertNotIn(key, saved)
        with self.assertRaises(ValueError):
            self.queue.create(data)

    def test_launch_never_passes_monetary_caps_to_either_cli(self):
        with mock.patch.dict(os.environ, {'BOOKENGINE_BUDGET_USD': '.01', 'DEAI_BUDGET_USD': '.01'}):
            for kind in ('book', 'polish'):
                folder = self.book('webui/' + kind, budget=0)
                self.queue.launch(folder, library.read(folder / 'web.json'), kind)
                self.assertEqual(self.spawn.call_count, 1 if kind == 'book' else 2)
                command = self.spawn.call_args.args[0]
                self.assertNotIn('--budget', command)
                self.assertNotIn('--budget-root', command)
                environment = self.spawn.call_args.kwargs['env']
                self.assertFalse(any('_BUDGET_' in key for key in environment))

    def test_high_cost_or_unknown_usage_never_freezes_or_blocks_resume(self):
        folder = self.book(status='running', budget=0)
        put(folder / 'calls.json', [{'usage': None, 'input_bytes': 10**10, 'max_tokens': 10**8}])
        before = (folder / 'calls.json').read_bytes()
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=False)
        self.queue.tick()
        self.killpg.assert_not_called()
        self.queue.action('webui/story', 'pause')
        self.queue.action('webui/story', 'resume')
        self.assertEqual(self.killpg.call_args, mock.call(self.child.pid, signal.SIGCONT))
        self.assertEqual((folder / 'calls.json').read_bytes(), before)

    def test_legacy_budget_pause_is_read_as_manual_pause_and_can_resume_stopped_run(self):
        folder = self.book(status='budget_paused', budget=0)
        put(folder / 'run.json', {'status': 'stopped', 'stop_reason': 'old monetary cap'})
        before = (folder / 'run.json').read_bytes()
        self.assertEqual(self.lib.meta(folder)['status'], 'paused')
        self.queue.action('webui/story', 'resume')
        self.assertEqual(library.read(folder / 'web.json')['status'], 'queued')
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual((folder / 'run.json').read_bytes(), before)

    def test_runtime_waiting_quota_and_error_are_exposed_with_stage_and_retry(self):
        folder = self.book(status='running')
        for kind in ('waiting', 'quota_exhausted', 'error'):
            with self.subTest(kind=kind):
                runtime = dict(kind=kind, stage='ch11-draft', retry=20,
                               next_retry_at='2026-11-05T18:35:00+08:00', error='offline test reason')
                put(folder / 'runtime.json', runtime)
                meta = self.lib.meta(folder)
                self.assertEqual(meta['status'], kind)
                for name in ('stage', 'retry', 'next_retry_at', 'error'):
                    self.assertEqual(meta[name], runtime[name])

    def test_quota_exit_can_resume_and_complete_without_rewriting_checkpoints(self):
        folder = self.book(status='running')
        saved = snapshot(folder)
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=False)
        put(folder / 'runtime.json', dict(kind='quota_exhausted', stage='ch2-draft', error='account empty'))
        self.child.poll.return_value = 1
        self.child.returncode = 1
        self.queue.tick()
        self.assertEqual(library.read(folder / 'web.json')['status'], 'quota_exhausted')
        self.queue.action('webui/story', 'resume')
        self.child.poll.return_value = None
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'running')
        for name in saved:
            if name != 'web.json':
                self.assertEqual((folder / name).read_bytes(), saved[name])
        put(folder / 'run.json', {'status': 'complete'})
        self.child.poll.return_value = 0
        self.child.returncode = 0
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'complete')

    def test_project_exit_preserves_actual_runtime_stage_and_summary(self):
        folder = self.book(status='running')
        put(folder / 'runtime.json', dict(kind='error', stage='bible', retry=6, error='invalid JSON'))
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=False)
        self.child.poll.return_value = 1
        self.child.returncode = 1
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'error')
        self.assertIn('invalid JSON', self.lib.meta(folder)['step'])
        self.assertEqual(self.lib.meta(folder)['stage'], 'bible')

    def test_dead_processes_requeue_clear_old_runner_lock_and_preserve_every_checkpoint(self):
        for status in ('running', 'queued', 'polishing'):
            folder = self.book('webui/' + status, status=status)
            put(folder / 'runner.lock', 'stale lock')
            put(folder / 'state.json', {'chapter': 1, 'unknown': True})
            data = library.read(folder / 'web.json')
            data.update(pid=999999, auto_deai=True)
            put(folder / 'web.json', data)
            if status == 'polishing':
                put(folder / 'run.json', {'status': 'complete'})
        before = snapshot(self.lib.own)
        self.kill.side_effect = ProcessLookupError
        self.restart()
        for status in ('running', 'queued', 'polishing'):
            folder = self.lib.own / status
            self.assertFalse((folder / 'runner.lock').exists())
            data = library.read(folder / 'web.json')
            self.assertEqual(data['status'], 'queued')
            self.assertEqual(data['pipeline_version'], 1)
            self.assertTrue(data['auto_deai'])
            self.assertNotIn('pid', data)
        for name, contents in before.items():
            if name.endswith(('web.json', 'runner.lock')):
                continue
            self.assertEqual((self.lib.own / name).read_bytes(), contents)

    def test_dead_run_status_without_web_running_still_recovers_but_manual_pause_does_not(self):
        folder = self.book(status='interrupted')
        put(folder / 'run.json', {'status': 'running', 'pid': 999999})
        paused = self.book('webui/paused', status='paused')
        put(paused / 'runner.lock', 'keep')
        self.kill.side_effect = ProcessLookupError
        self.restart()
        self.assertEqual(library.read(folder / 'web.json')['status'], 'queued')
        self.assertEqual(library.read(paused / 'web.json')['status'], 'paused')
        self.assertTrue((paused / 'runner.lock').exists())

    def test_live_pid_or_locked_runner_is_never_unlinked_or_launched_twice(self):
        folder = self.book(status='queued')
        put(folder / 'runner.lock', 'live')
        data = library.read(folder / 'web.json')
        data['pid'] = 880001
        put(folder / 'web.json', data)
        self.restart()
        self.queue.tick()
        self.assertEqual(library.read(folder / 'web.json')['status'], 'orphaned')
        self.assertEqual((folder / 'runner.lock').read_text(), 'live')
        self.spawn.assert_not_called()

    def test_auto_deai_survives_restart_and_handoff_after_body_completion(self):
        folder = self.book(status='running')
        data = library.read(folder / 'web.json')
        data.update(auto_deai=True, polish=False)
        put(folder / 'web.json', data)
        self.kill.side_effect = ProcessLookupError
        self.restart()
        self.queue.tick()
        self.assertEqual([c.args[0][2] for c in self.spawn.call_args_list], ['bookengine', 'deai'])
        put(folder / 'run.json', {'status': 'complete'})
        self.child.poll.return_value = 0
        self.child.returncode = 0
        self.queue.tick()
        self.assertEqual(self.spawn.call_args.args[0][2], 'deai')
        self.assertTrue(library.read(folder / 'web.json')['auto_deai'])

    def test_successful_exit_clears_stale_waiting_and_complete_metadata_ignores_old_errors(self):
        folder = self.book(status='running')
        put(folder / 'run.json', {'status': 'complete'})
        put(folder / 'runtime.json', dict(kind='waiting', stage='ch2-draft', retry=20, next_retry_at=time.time()))
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=False)
        self.child.poll.return_value = 0
        self.child.returncode = 0
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'complete')
        self.assertFalse(library.read(folder / 'runtime.json'))
        put(folder / 'runtime.json', dict(kind='error', stage='old-stage', error='stale error'))
        self.assertEqual(self.lib.meta(folder)['status'], 'complete')
        self.assertFalse(self.lib.meta(folder)['error'])

    def test_unexpected_exit_during_wait_reports_error_instead_of_waiting_forever(self):
        folder = self.book(status='running')
        put(folder / 'runtime.json', dict(kind='waiting', stage='ch2-draft', retry=20))
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=False)
        self.child.poll.return_value = 1
        self.child.returncode = 1
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'error')
        self.assertEqual(self.lib.meta(folder)['stage'], 'ch2-draft')

    def test_spawn_error_is_project_error_without_automatic_respawn_loop(self):
        folder = self.book(status='queued')
        self.spawn.side_effect = OSError('offline executable unavailable')
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'error')
        self.queue.tick()
        self.spawn.assert_called_once()

    def test_dead_process_with_durable_quota_is_not_automatically_retried(self):
        folder = self.book(status='running')
        put(folder / 'runtime.json', dict(kind='quota_exhausted', stage='ch2-draft', error='充值后继续'))
        self.kill.side_effect = ProcessLookupError
        self.restart()
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'quota_exhausted')
        self.spawn.assert_not_called()

    def test_held_runner_lock_without_pid_prevents_recovery_from_deleting_the_lock(self):
        folder = self.book(status='queued')
        lockpath = folder / 'runner.lock'
        with lockpath.open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.restart()
            self.queue.tick()
            self.assertEqual(self.lib.meta(folder)['status'], 'orphaned')
            self.assertTrue(lockpath.exists())
            self.spawn.assert_not_called()

    def test_postprocess_restart_keeps_manual_stage_even_when_auto_deai_is_disabled(self):
        folder = self.book(status='polishing')
        put(folder / 'run.json', {'status': 'complete'})
        data = library.read(folder / 'web.json')
        data.update(auto_deai=False, polish=True)
        put(folder / 'web.json', data)
        self.restart()
        self.assertEqual(library.read(folder / 'web.json')['status'], 'queued')
        self.assertTrue(library.read(folder / 'web.json')['auto_deai'])

    def test_chapter_check_findings_survive_polishing_and_are_visible_in_detail(self):
        folder = self.book(status='running')
        put(folder / 'chapters/001.json', dict(title='第一章', within_band=False,
            hard_conflicts=1, remaining=[dict(kind='continuity', reason='人物位置需核对')]))
        detail = self.lib.detail('webui/story')
        first = detail['chapters'][0]
        self.assertFalse(first['checks']['within_band'])
        self.assertEqual(first['checks']['hard_conflicts'], 1)
        self.assertEqual(detail['meta']['quality_issues'][0]['number'], 1)
        self.assertTrue(first['final'], 'polished text remains readable alongside writer findings')

    def test_topic_worker_keeps_quota_and_project_runtime_causes_public(self):
        from tools.runtime_support import ProjectError, QuotaExhausted
        for kind, error in (('quota_exhausted', QuotaExhausted('账户资源用尽')),
                            ('error', ProjectError('six invalid returns'))):
            with self.subTest(kind=kind):
                topic = self.topic()
                folder = self.lib.own / '.topics' / topic['topic_id']
                data = library.read(folder / 'topic.json')
                data.update(status='running', candidates=[])
                put(folder / 'topic.json', data)
                def pipeline(*args):
                    put(folder / 'runtime.json', dict(kind=kind, stage='generate-0', retry=6,
                        error=str(error), updated=time.time()))
                    raise error
                with mock.patch('deai.client.MiniMax'), mock.patch('ideation.run', side_effect=pipeline):
                    jobs.topic_worker(folder, self.env)
                public = self.queue.get_topic(topic['topic_id'])
                self.assertEqual(public['status'], kind)
                self.assertEqual(public['stage'], 'generate-0')
                self.assertEqual(public['retry'], 6)
                self.assertEqual(public['error'], str(error))

    def test_topic_runtime_waiting_is_public_and_success_ignores_old_error(self):
        topic = self.topic()
        folder = self.lib.own / '.topics' / topic['topic_id']
        data = library.read(folder / 'topic.json')
        data['status'] = 'running'
        put(folder / 'topic.json', data)
        put(folder / 'runtime.json', dict(kind='waiting', stage='generate-1', retry=20, next_retry_at=1793874900))
        public = self.queue.get_topic(topic['topic_id'])
        self.assertEqual(public['status'], 'waiting')
        self.assertEqual(public['next_retry_at'], 1793874900)
        data['status'] = 'complete'
        put(folder / 'topic.json', data)
        put(folder / 'runtime.json', dict(kind='error', stage='old', error='stale error'))
        self.assertEqual(self.queue.get_topic(topic['topic_id'])['status'], 'complete')

    def test_topic_process_exit_reconciles_runtime_quota_or_unexpected_waiting(self):
        for kind, expected in (('quota_exhausted', 'quota_exhausted'), ('waiting', 'error')):
            with self.subTest(kind=kind):
                topic = self.topic()
                folder = self.lib.own / '.topics' / topic['topic_id']
                data = library.read(folder / 'topic.json')
                data.update(status='running', candidates=[])
                put(folder / 'topic.json', data)
                put(folder / 'runtime.json', dict(kind=kind, stage='generate-0', error='HTTP test reason'))
                self.queue.processes['topic/' + topic['topic_id']] = dict(
                    p=self.child, kind='topic', folder=folder, paused=False)
                self.child.poll.return_value = 1
                self.child.returncode = 1
                self.queue.tick()
                self.assertEqual(library.read(folder / 'topic.json')['status'], expected)
                self.assertEqual(self.queue.get_topic(topic['topic_id'])['status'], expected)

    def test_waiting_status_frontend_has_retry_time_and_cost_only(self):
        script = """
import {statusLabel, runtimeInfo, bookUsage} from './shared.mjs';
const book = {status:'waiting', stage:'ch11-draft', retry:20,
 next_retry_at:1793874900, error:'HTTP 429', writer_cost:30, deai_cost:40};
if (statusLabel(book) !== '等待接口') throw Error('waiting label');
const detail = runtimeInfo(book);
if (!detail.includes('20') || !detail.includes('下次') || !detail.includes('ch11-draft')) throw Error(detail);
if (bookUsage(book).includes('预算')) throw Error('monetary limit shown');
if (statusLabel({status:'quota_exhausted'}) !== '额度用尽') throw Error('quota label');
"""
        module = self.root / 'shared.mjs'
        module.write_bytes((WEBUI / 'shared.js').read_bytes())
        with mock.patch.object(subprocess, 'Popen', POPEN):
            result = subprocess.run(['/opt/homebrew/bin/node', '--input-type=module', '-e', script],
                                    cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_local_fake_cli_kill_then_rebuild_service_continues_without_changing_saved_chapter(self):
        folder = self.lib.own / 'resume-real-process'
        put(folder / 'book.yaml', {'chapters': 2})
        put(folder / 'web.json', dict(title='恢复测试', status='queued', chapters=2, auto_deai=False))
        script = """
import fcntl, json, os, signal, sys
from pathlib import Path
folder = Path(sys.argv[1])
with (folder / 'runner.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    first = folder / 'chapter-001.body.md'
    if not first.exists():
        first.write_bytes('第一章：留住这盏灯。\\n'.encode())
        (folder / 'run.json').write_text(json.dumps({'status':'running','book':{'chapters':2}}))
        (folder / 'state.json').write_text(json.dumps({'chapter':1}))
        (folder / 'ready').touch()
        signal.pause()
    else:
        assert json.loads((folder / 'state.json').read_text())['chapter'] == 1
        (folder / 'chapter-002.body.md').write_bytes('第二章：灯亮到了天明。\\n'.encode())
        (folder / 'run.json').write_text(json.dumps({'status':'complete','book':{'chapters':2}}))
"""
        children = []
        def spawn(command, **options):
            child = POPEN([sys.executable, '-c', script, str(folder)], **options)
            children.append(child)
            return child
        def cleanup():
            for child in children:
                if child.poll() is None:
                    KILL(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        self.addCleanup(cleanup)
        self.spawn.side_effect = spawn
        self.kill.side_effect = KILL
        self.queue.tick()
        first_process = children[0]
        deadline = time.monotonic() + 5
        while not (folder / 'ready').exists():
            self.assertIsNone(first_process.poll(), 'fake CLI exited before its checkpoint')
            self.assertLess(time.monotonic(), deadline, 'fake CLI did not reach checkpoint')
            time.sleep(.01)
        original = (folder / 'chapter-001.body.md').read_bytes()
        original_state = (folder / 'state.json').read_bytes()
        KILL(first_process.pid, signal.SIGKILL)
        first_process.wait(timeout=5)
        self.restart()
        self.assertFalse((folder / 'runner.lock').exists())
        self.assertEqual(library.read(folder / 'web.json')['status'], 'queued')
        self.queue.tick()
        self.assertEqual(children[1].wait(timeout=5), 0)
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'paused')
        self.assertEqual(self.lib.meta(folder)['chapters'], 0)
        self.assertEqual((folder / 'chapter-001.body.md').read_bytes(), original)
        self.assertEqual((folder / 'state.json').read_bytes(), original_state)
