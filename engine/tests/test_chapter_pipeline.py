"""INBOX 27: offline pipeline, committed sources, and final-only presentation."""
import json
import os
import signal
import subprocess
import sys
import time
from unittest import mock
from types import SimpleNamespace

from test_webui_contracts import ENGINE, WebFixture, jobs, library, put

POPEN = subprocess.Popen
KILL = os.kill


def wait_until(condition, seconds=12):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, 'fixture condition did not become true'
        time.sleep(.02)


class PipelineTests(WebFixture):
    def pipeline_book(self, status='queued'):
        folder = self.book(status=status, final=False, total=3)
        data = library.read(folder / 'web.json')
        data.update(auto_deai=True, polish=True)
        put(folder / 'web.json', data)
        put(folder / 'chapters/003.json', dict(status='writing', title='尚未完成'))
        for n in (1, 2):
            put(folder / f'chapters/{n:03d}.json', dict(status='complete', title=f'潮声{n}'))
        return folder

    def test_new_book_defaults_to_auto_deai(self):
        created = self.queue.create(self.topic())
        self.assertTrue(library.read(self.lib.folder(created['id']) / 'web.json')['auto_deai'])

    def test_writer_and_watcher_launch_together_and_count_as_one_book(self):
        self.pipeline_book()
        self.queue.jobs = 1
        self.queue.tick()
        commands = [c.args[0] for c in self.spawn.call_args_list]
        self.assertEqual([c[2] for c in commands], ['bookengine', 'deai'])
        self.assertIn('--watch', commands[1])
        self.assertIn('--pipeline', commands[1])

    def test_pending_finals_never_count_as_readable_words(self):
        self.pipeline_book()
        meta = self.lib.meta(self.lib.own / 'story')
        self.assertEqual(meta['chapters'], 0)
        self.assertEqual(meta['written_chapters'], 2)
        self.assertEqual(meta['words'], 0)

    def test_retained_is_a_final_artifact_without_claiming_polish_passed(self):
        folder = self.pipeline_book()
        put(folder / 'final/chapter-001.md', '原文保留。')
        put(folder / 'final/chapter-001.deai.json', dict(status='retained', processing_complete=True, complete=False))
        self.assertEqual(self.lib.meta(folder)['chapters'], 1)
        self.assertEqual(self.lib.chapters(folder)[0]['final'], '原文保留。')

    def test_delete_http_needs_no_title_and_rejects_running(self):
        folder = self.pipeline_book(status='running')
        data = dict(id='webui/story', action='delete')
        self.assertEqual(self.request('/api/action', method='POST', data=data)[0], 400)
        put(folder / 'web.json', dict(library.read(folder / 'web.json'), status='paused'))
        self.assertEqual(self.request('/api/action', method='POST', data=data)[0], 200)
        self.assertFalse(folder.exists())

    def test_partial_downloads_use_finals_even_when_draft_is_requested(self):
        folder = self.pipeline_book(status='paused')
        put(folder / 'final/chapter-001.md', '原文保留。')
        put(folder / 'final/chapter-001.deai.json', dict(status='retained', processing_complete=True))
        for fmt in ('md', 'txt', 'docx', 'epub', 'fanqie'):
            code, headers, body = self.download(fmt=fmt, version='draft')
            self.assertEqual(code, 200, (fmt, body))
            self.assertEqual(headers['X-Missing-Chapters'], '2,3')
            if fmt in ('md', 'txt'):
                self.assertIn('原文保留。', body.decode())
                self.assertNotIn('草稿', body.decode())

    def test_polisher_quota_does_not_stop_writer_and_retry_only_restarts_failed_stage(self):
        folder = self.pipeline_book()
        writer, polisher = [mock.Mock(pid=800000 + n, returncode=None) for n in (1, 2)]
        for child in (writer, polisher): child.poll.return_value = None
        self.spawn.side_effect = [writer, polisher]
        self.queue.jobs = 1
        self.queue.tick()
        polisher.poll.return_value = 1
        polisher.returncode = 1
        put(folder / '.pipeline/polish/runtime.json', dict(kind='quota_exhausted', stage='detect', error='账户额度不足'))
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'quota_exhausted')
        self.assertTrue(self.lib.meta(folder)['writer_active'])
        self.assertIs(self.queue.processes['webui/story']['p'], writer)
        self.killpg.assert_not_called()
        replacement = mock.Mock(pid=800003, returncode=None)
        replacement.poll.return_value = None
        self.spawn.side_effect = [replacement]
        self.queue.action('webui/story', 'resume')
        self.queue.tick()
        self.assertEqual(self.spawn.call_count, 3)
        self.assertEqual(self.spawn.call_args.args[0][2], 'deai')
        self.assertIs(self.queue.processes['webui/story']['p'], writer)

    def test_pause_and_lowered_concurrency_apply_to_both_stages(self):
        folder = self.pipeline_book()
        self.queue.tick()
        self.queue.action('webui/story', 'pause')
        self.assertEqual(self.killpg.call_count, 2)
        self.assertTrue(all(i['paused'] for i in self.queue.processes.values()))
        self.queue.action('webui/story', 'resume')
        self.queue.tick()
        self.assertTrue(all(not i['paused'] for i in self.queue.processes.values()))
        self.assertEqual(self.spawn.call_count, 2)

    def test_delete_rejects_live_polisher_even_after_writer_has_exited(self):
        folder = self.pipeline_book(status='error')
        self.queue.processes['webui/story/polish'] = dict(p=self.child, kind='polish', folder=folder, paused=False, pipeline=True)
        with self.assertRaises(ValueError): self.queue.action('webui/story', 'delete')
        self.assertTrue(folder.exists())

    def test_lowering_concurrency_stops_and_resumes_the_same_pair(self):
        first = self.pipeline_book()
        second = self.book('webui/zz-second', final=False, total=3)
        put(second / 'web.json', dict(library.read(second / 'web.json'), auto_deai=True, polish=True))
        children = [mock.Mock(pid=900000 + n, returncode=None) for n in range(4)]
        for child in children: child.poll.return_value = None
        self.spawn.side_effect = children
        self.queue.tick()
        self.assertEqual(self.spawn.call_count, 4)
        self.queue.jobs = 1
        self.queue.tick()
        self.assertEqual(self.killpg.call_count, 2)
        self.assertTrue(all(i['paused'] for i in self.queue.processes.values() if i['folder'] == second))
        for n in (1, 2, 3):
            put(first / f'final/chapter-{n:03d}.md', '原稿保留。')
            put(first / f'final/chapter-{n:03d}.deai.json', dict(status='retained', processing_complete=True))
        put(first / 'run.json', dict(status='complete', book=dict(chapters=3)))
        for child in children[:2]:
            child.poll.return_value = 0
            child.returncode = 0
        self.queue.tick()
        self.assertEqual(self.killpg.call_count, 4)
        self.assertEqual(self.spawn.call_count, 4)
        self.assertTrue(all(not i['paused'] for i in self.queue.processes.values()))

    def test_restart_keeps_failed_stage_stopped_and_resumes_healthy_writer(self):
        folder = self.pipeline_book(status='quota_exhausted')
        put(folder / 'web.json', dict(library.read(folder / 'web.json'), pipeline_version=1,
            pipeline=dict(book=dict(status='running', pid=999991), polish=dict(status='quota_exhausted'))))
        put(folder / '.pipeline/polish/runtime.json', dict(kind='quota_exhausted', stage='recheck', error='充值后继续'))
        self.kill.side_effect = ProcessLookupError
        self.queue.exclusive.close()
        self.queue = jobs.Queue(self.lib, self.env, ENGINE)
        self.addCleanup(self.queue.exclusive.close)
        self.queue.tick()
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0][2], 'bookengine')
        self.assertEqual(self.lib.meta(folder)['status'], 'quota_exhausted')

    def test_paused_surviving_old_process_cannot_be_duplicated_by_resume(self):
        folder = self.pipeline_book(status='paused')
        put(folder / 'web.json', dict(library.read(folder / 'web.json'), pipeline_version=1,
                                    pipeline=dict(polish=dict(status='running', pid=880019))))
        with self.assertRaises(ValueError): self.queue.action('webui/story', 'resume')
        self.spawn.assert_not_called()

    def test_real_two_process_kill_restart_reuses_finals_without_new_calls(self):
        folder = self.lib.own / 'restart'
        put(folder / 'web.json', dict(title='离线流水线', status='queued', chapters=3, auto_deai=True, polish=True))
        put(folder / 'book.yaml', dict(chapters=3))
        script = r'''
import fcntl, json, signal, sys, time
from pathlib import Path
from unittest.mock import patch
from tools.chapter_outputs import has_final
from deai.io import save
book = Path(sys.argv[1])
stage = sys.argv[2]
def checkpoint(n):
    if not (book / f'chapter-{n:03d}.body.md').exists():
        save(book, f'chapter-{n:03d}.body.md', f'她在门边等了{n}天。')
    save(book, f'chapters/{n:03d}.json', dict(status='complete', title=f'灯火{n}'))
if stage == 'book':
    with (book / 'runner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not (book / 'restart-ready').exists():
            save(book, 'run.json', dict(status='running', book=dict(chapters=3)))
            checkpoint(1)
            checkpoint(2)
            while not all(has_final(book, n) for n in (1, 2)): time.sleep(.02)
            save(book, 'restart-ready', 'yes')
            signal.pause()
        checkpoint(3)
        save(book, 'run.json', dict(status='complete', book=dict(chapters=3)))
else:
    from deai.__main__ import main
    from deai.flow import Result
    class FakeClient:
        calls = []
        def __init__(self, out, *a, **k):
            with (book / 'fake-calls').open('a') as log: log.write(Path(out).name + '\n')
        def estimate(self): return 0
    with patch('deai.__main__.MiniMax', FakeClient), patch('deai.polish', lambda text, *a: Result(text=text, status='retained')), patch('urllib.request.urlopen', side_effect=AssertionError('no network')):
        sys.exit(main(['run', str(book), '--watch', '--pipeline']))
'''
        children = []
        def spawn(command, **options):
            stage = 'book' if command[2] == 'bookengine' else 'polish'
            child = POPEN([sys.executable, '-c', script, str(folder), stage], **options)
            children.append(child)
            return child
        def cleanup():
            for child in children:
                if child.poll() is None: KILL(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        self.addCleanup(cleanup)
        self.spawn.side_effect = spawn
        self.kill.side_effect = KILL
        self.queue.tick()
        wait_until(lambda: (folder / 'restart-ready').exists())
        original = {p: p.read_bytes() for p in [folder / 'chapter-001.body.md', folder / 'chapters/001.json',
                    *sorted((folder / 'final').glob('chapter-*'))]}
        for child in children:
            KILL(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
        self.queue.exclusive.close()
        self.queue = jobs.Queue(self.lib, self.env, ENGINE)
        self.addCleanup(self.queue.exclusive.close)
        self.queue.tick()
        self.assertEqual(len(children), 4)
        for child in children[2:]: self.assertEqual(child.wait(timeout=10), 0)
        self.queue.tick()
        self.assertEqual(self.lib.meta(folder)['status'], 'complete')
        self.assertEqual(self.lib.meta(folder)['chapters'], 3)
        self.assertEqual((folder / 'fake-calls').read_text().splitlines(), ['001', '002', '003'])
        for path, body in original.items(): self.assertEqual(path.read_bytes(), body)


def test_pipeline_watcher_never_reads_uncommitted_body_and_finishes(tmp_path):
    from deai.__main__ import main
    from deai.client import STOP
    from deai.flow import Result
    folder = tmp_path
    put(folder / 'run.json', dict(status='running', book=dict(chapters=3)))
    for n in (1, 2, 3):
        put(folder / f'chapter-{n:03d}.body.md', f'她在门口等了{n}天。')
        if n < 3:
            put(folder / f'chapters/{n:03d}.json', dict(status='complete'))
    processed = []
    class FakeClient:
        calls = []
        def __init__(self, *args, **kwargs): pass
        def estimate(self): return 0
    def polish(text, ctx, client):
        processed.append(text)
        return Result(text=text, status='retained')
    def advance(_):
        assert len(processed) == 2
        assert (folder / 'final/chapter-001.md').exists()
        assert (folder / 'final/chapter-002.md').exists()
        assert not (folder / 'final/chapter-003.md').exists()
        put(folder / 'chapters/003.json', dict(status='complete'))
        put(folder / 'run.json', dict(status='complete', book=dict(chapters=3)))
        return False
    try:
        with mock.patch('deai.__main__.MiniMax', FakeClient), mock.patch('deai.polish', polish), \
                mock.patch.object(STOP, 'wait', side_effect=advance):
            assert main(['run', str(folder), '--watch', '--pipeline']) == 0
            assert len(processed) == 3
            original = {p.name: p.read_bytes() for p in (folder / 'final').glob('chapter-*')}
            assert main(['run', str(folder), '--watch', '--pipeline']) == 0
            assert len(processed) == 3
            assert original == {p.name: p.read_bytes() for p in (folder / 'final').glob('chapter-*')}
    finally:
        STOP.clear()


def test_real_q1_writer_reaches_third_chapter_with_first_two_finals(tmp_path, monkeypatch):
    """Real Q1 checkpoint publication plus real watcher/IO; both model boundaries fake."""
    from concurrent.futures import ThreadPoolExecutor
    from bookengine.config import Book
    from deai.__main__ import main
    from deai.flow import Result
    from deai.client import STOP
    from tools.chapter_outputs import has_final
    from test_run_to_finish import q1_fixture, good
    q1, client, config, draft, extracted = q1_fixture(tmp_path, monkeypatch, chapters=3)
    replies = iter([good(draft), good(extracted)] * 3)
    calls = []
    def reply(*args, **kwargs):
        if len(calls) == 4:
            wait_until(lambda: has_final(tmp_path, 1) and has_final(tmp_path, 2))
            assert not has_final(tmp_path, 3)
        calls.append(True)
        return next(replies)
    class FakeClient:
        calls = []
        def __init__(self, *a, **k): pass
        def estimate(self): return 0
    STOP.clear()
    try:
        with mock.patch('urllib.request.urlopen', side_effect=reply), mock.patch('deai.__main__.MiniMax', FakeClient), \
                mock.patch('deai.polish', lambda text, *a: Result(text=text, status='retained')):
            with ThreadPoolExecutor(max_workers=1) as pool:
                writer = pool.submit(q1._run, Book.read(config), tmp_path, tmp_path / 'unused.env',
                                     tmp_path, 180, 'b', lambda: False)
                assert main(['run', str(tmp_path), '--watch', '--pipeline']) == 0
                assert writer.result(timeout=5)['status'] == 'complete'
        assert all(has_final(tmp_path, n) for n in (1, 2, 3))
        assert len(client.calls) == 6
    finally:
        STOP.clear()


def test_disabled_auto_deai_publishes_original_without_constructing_model(tmp_path):
    from deai.__main__ import main
    from deai.client import STOP
    put(tmp_path / 'chapter-001.body.md', '她推开门。\r\n\r\n灯还亮着。')
    put(tmp_path / 'chapters/001.json', dict(status='complete'))
    put(tmp_path / 'run.json', dict(status='complete', book=dict(chapters=1)))
    STOP.clear()
    with mock.patch('deai.__main__.MiniMax', side_effect=AssertionError('must not construct client')):
        assert main(['run', str(tmp_path), '--watch', '--pipeline', '--original']) == 0
    assert (tmp_path / 'final/chapter-001.md').read_bytes() == (tmp_path / 'chapter-001.body.md').read_bytes()
