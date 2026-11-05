"""Offline batch orchestration: real child processes, no model requests."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from bookengine.__main__ import main
from bookengine.batch import read_manifest, read_report, run_batch, usage_report, write_reading_sample


CHILD = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
folder = Path(args[args.index('--out') + 1])
(folder / 'pid.json').write_text(json.dumps(dict(pid=os.getpid(), rate=os.environ['BOOKENGINE_RATE_FILE'])))
deadline = time.monotonic() + 8
while len(list(folder.parent.glob('*/pid.json'))) < 5:
    if time.monotonic() > deadline:
        sys.exit(8)
    time.sleep(.01)
if folder.name == os.environ.get('FIXTURE_FAIL'):
    sys.exit(7)
import yaml
book = yaml.safe_load(Path(args[0]).read_text())
for n in range(1, book['chapters'] + 1):
    (folder / ('chapter-%03d.md' % n)).write_text('## 第%d章\n\n正文。' % n)
    (folder / ('chapter-%03d.body.md' % n)).write_text('正文。')
    (folder / 'chapters').mkdir(exist_ok=True)
    (folder / ('chapters/%03d.json' % n)).write_text(json.dumps(dict(status='complete')))
(folder / 'book.md').write_text('# ' + folder.name + '\n\n非抽读内容')
(folder / 'book.docx').write_bytes(b'fixture')
calls = [dict(stage='ch1-draft', attempt=1, status='transport_error', input_bytes=10, output_token_cap=20),
         dict(stage='ch1-draft', attempt=2, status='ok', usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))]
(folder / 'calls.json').write_text(json.dumps(calls))
(folder / 'run.json').write_text(json.dumps(dict(book=book, mode='Q1', style='b', status='complete')))
'''


class Q1BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        books = []
        for i, chapters in enumerate((300, 10, 10, 10, 10)):
            path = self.root / f'book-{i}.yaml'
            path.write_text(yaml.safe_dump(dict(prompt_pack='realistic-life', chapters=chapters,
                                               words_per_chapter=2200, user_input='本地测试。')))
            books.append(dict(id=f'book-{i}', path=path.name))
        self.manifest = self.root / 'batch.yaml'
        self.manifest.write_text(yaml.safe_dump(dict(books=books)))
        self.out = self.root / 'out'

    def test_q1_manifest_accepts_mixed_counts(self):
        self.assertEqual([e['book']['chapters'] for e in read_manifest(self.manifest)],
                         [300, 10, 10, 10, 10])

    def test_cli_forwards_q1_batch_and_jobs(self):
        with patch.object(sys, 'argv', ['bookengine', '--q1', '--batch', str(self.manifest),
                                       '--env', 'unused', '--out', str(self.out), '--jobs', '5']), \
                patch('bookengine.batch.run_batch', return_value=dict(status='complete')) as run:
            main()
        self.assertEqual(run.call_args.kwargs, {'style': 'b'})
        self.assertEqual(run.call_args.args[5], 5)

    def execute(self, fail='', shared=None):
        real_popen, launches = subprocess.Popen, []

        def launch(command, **kwargs):
            self.assertEqual(command[-3:], ['--q1', '--style', 'b'])
            self.assertTrue(kwargs['start_new_session'])
            launches.append((command, kwargs['env']['BOOKENGINE_RATE_FILE']))
            return real_popen([sys.executable, '-c', CHILD, *command[4:]], **kwargs)

        environment = dict(os.environ, FIXTURE_FAIL=fail)
        if shared:
            environment['BOOKENGINE_RATE_FILE'] = str(shared)
        else:
            environment.pop('BOOKENGINE_RATE_FILE', None)
        with patch.dict(os.environ, environment, clear=True), \
                patch('bookengine.batch.subprocess.Popen', side_effect=launch), \
                contextlib.redirect_stdout(io.StringIO()):
            result = run_batch(self.manifest, self.out, 'unused', 'unused', jobs=5, interval=.05)
            self.assertEqual(os.environ.get('BOOKENGINE_RATE_FILE'), str(shared) if shared else None)
        return result, launches

    def test_five_concurrent_processes_share_env_and_failure_is_isolated(self):
        shared = self.root / 'global-rate.json'
        result, launches = self.execute('book-1', shared)
        self.assertEqual(len(launches), 5)
        self.assertEqual({path for _, path in launches}, {str(shared)})
        pids = [json.loads(p.read_text()) for p in self.out.glob('*/pid.json')]
        self.assertEqual(len({p['pid'] for p in pids}), 5)
        self.assertEqual({p['rate'] for p in pids}, {str(shared)})
        self.assertEqual((result['status'], result['completed_books']), ('degraded', 4))
        self.assertEqual(result['books'][1]['exit_code'], 7)
        self.assertEqual(result['books'][0]['chapter_progress'], dict(complete=300, total=300))
        self.assertEqual((result['books'][0]['calls'], result['books'][0]['failures'],
                          result['books'][0]['retries']), (2, 1, 1))
        self.assertGreater(result['estimated_usd']['conservative_total'], 0)
        self.assertEqual(json.loads((self.out / 'batch.json').read_text()), result)
        self.assertFalse((self.out / 'READ-ME-FIRST.md').exists())

    def test_success_resume_skips_complete_and_default_rate_path_is_stable(self):
        result, launches = self.execute()
        self.assertEqual(result['status'], 'complete')
        path = launches[0][1]
        self.assertTrue(Path(path).is_absolute())
        self.assertNotIn(str(self.out), path)
        saved = {p: p.read_bytes() for p in self.out.glob('*/*') if p.is_file()}
        result, resumed = self.execute()
        self.assertEqual((result['completed_books'], resumed), (5, []))
        self.assertEqual(saved, {p: p.read_bytes() for p in saved})
        self.out = self.root / 'other-out'
        _, second = self.execute()
        self.assertEqual({p for _, p in second}, {path})

    def test_resume_rejects_non_q1_or_changed_style_before_launch(self):
        entry = read_manifest(self.manifest)[0]
        folder = self.out / entry['id']
        folder.mkdir(parents=True)
        (folder / 'book.md').write_text('# 已有正文')
        (folder / 'book.docx').write_bytes(b'fixture')
        for mode, style in ((None, 'b'), ('S4', 'b'), ('Q1', 'legacy')):
            meta = dict(book=entry['book'], mode=mode, style=style, status='complete')
            (folder / 'run.json').write_text(json.dumps(meta))
            saved = {p: p.read_bytes() for p in folder.iterdir()}
            with self.subTest(mode=mode, style=style), \
                    patch('bookengine.batch.subprocess.Popen') as launch:
                with self.assertRaisesRegex(ValueError, 'Q1 checkpoint settings changed'):
                    run_batch(self.manifest, self.out, 'unused', 'unused')
                launch.assert_not_called()
                self.assertEqual(saved, {p: p.read_bytes() for p in saved})

    def test_samples_have_only_book_title_and_required_exported_chapters(self):
        entries = read_manifest(self.manifest)
        expected = []
        for entry in entries:
            folder = self.out / entry['id']
            folder.mkdir(parents=True)
            title = '# ' + entry['id']
            (folder / 'book.md').write_text(title + '\n\n不要收录')
            expected.append(title)
            for n in range(1, entry['book']['chapters'] + 1):
                body = f'## 第{n}章\n\n{entry["id"]}正文{n}。'
                (folder / f'chapter-{n:03d}.md').write_text(body + '\n')
                selected = (1, 50, 51, 75, 100)
                if n in selected:
                    expected.append(body)
        path = write_reading_sample(entries, self.out)
        self.assertEqual(path.read_text(), '\n\n'.join(expected) + '\n')

    def test_q1_body_checkpoint_counts_before_export(self):
        entry = read_manifest(self.manifest)[1]
        folder = self.out / entry['id']
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps(dict(mode='Q1', style='b')))
        (folder / 'chapters').mkdir()
        (folder / 'chapters/001.json').write_text(json.dumps(dict(status='complete', chars=999)))
        (folder / 'chapter-001.body.md').write_text('甲乙。丙丁。')
        row = read_report(entry, self.out)
        self.assertEqual(row['chapter_chinese_chars'], [4] + [None] * 9)
        self.assertEqual(row['chapter_progress'], dict(complete=1, total=10))

    def test_q1_progress_ignores_drafts_exports_and_uncommitted_bodies(self):
        entry = read_manifest(self.manifest)[1]
        folder = self.out / entry['id']
        (folder / 'chapters').mkdir(parents=True)
        for n in range(1, 11):
            for suffix in ('.md', '.draft.md', '.body.md'):
                (folder / f'chapter-{n:03d}{suffix}').write_text('草稿不代表完成。')
        for n, status in ((1, 'complete'), (3, 'failed'), (4, 'running')):
            (folder / f'chapters/{n:03d}.json').write_text(json.dumps(dict(status=status, chars=999)))
        (folder / 'chapter-001.body.md').write_text('甲乙。丙丁。')
        (folder / 'chapters/005.json').write_text('{broken')
        (folder / 'chapters/006.json').write_text('[]')
        (folder / 'chapters/007.json').write_text(json.dumps(dict(status='complete')))
        (folder / 'chapter-007.body.md').unlink()
        (folder / 'run.json').write_text(json.dumps(dict(mode='Q1', chapter_chinese_chars=[999] * 10)))
        before = {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        row = read_report(entry, self.out)
        self.assertEqual(row['chapter_progress'], dict(complete=1, total=10))
        self.assertEqual(row['chapter_chinese_chars'], [4] + [None] * 9)
        self.assertIn('chapters/005.json', row['read_errors'])
        self.assertIn('chapters/006.json', row['read_errors'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_q1_usage_fallback_preserves_failures_retries_and_unknown_cost(self):
        entry = read_manifest(self.manifest)[1]
        folder = self.out / entry['id']
        folder.mkdir(parents=True)
        usage = usage_report([dict(stage='ch1-draft', status='transport_error', attempt=1,
                                   input_bytes=100, output_token_cap=200),
                              dict(stage='ch1-draft', status='ok', attempt=2,
                                   usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))])
        (folder / 'run.json').write_text(json.dumps(dict(usage=usage)))
        (folder / 'calls.json').write_text('{broken')
        row = read_report(entry, self.out)
        for key in ('calls', 'failures', 'retries', 'unknownusage', 'estimated_usd', 'missing_usage'):
            self.assertEqual(row[key], usage[key])
        self.assertEqual(row['read_errors'], ['calls.json'])

    def test_complete_exports_override_stale_batch_failure(self):
        entry = read_manifest(self.manifest)[1]
        folder = self.out / entry['id']
        folder.mkdir(parents=True)
        meta = dict(mode='Q1', style='b', status='complete', started='2026-10-31T00:00:00+00:00',
                    finished='2026-10-31T00:01:00+00:00')
        (folder / 'run.json').write_text(json.dumps(meta))
        (folder / 'book.md').write_text('# 书名\n')
        (folder / 'book.docx').write_bytes(b'fixture')
        stale = dict(status='failed', exit_code=0, finished='2026-10-31T00:00:30+00:00')
        row = read_report(entry, self.out, stale)
        self.assertEqual((row['status'], row['stage'], row['wall_seconds']), ('complete', 'complete', 60))
        self.assertEqual(row['finished'], meta['finished'])
        for missing in ('book.md', 'book.docx'):
            path = folder / missing
            data = path.read_bytes()
            path.unlink()
            with self.subTest(missing=missing):
                self.assertEqual(read_report(entry, self.out, stale)['status'], 'failed')
                self.assertNotEqual(read_report(entry, self.out)['status'], 'complete')
            path.write_bytes(data)
        meta['status'] = 'failed'
        (folder / 'run.json').write_text(json.dumps(meta))
        self.assertEqual(read_report(entry, self.out, stale)['status'], 'failed')

    def test_zero_exit_code_without_complete_metadata_is_degraded(self):
        incomplete_child = CHILD.replace("mode='Q1', style='b', status='complete'", "mode='Q1', style='b', status='incomplete'")
        with patch(__name__ + '.CHILD', incomplete_child):
            result, _ = self.execute()
        self.assertEqual((result['status'], result['completed_books']), ('degraded', 0))
        self.assertTrue(all(row['exit_code'] == 0 and row['status'] == 'degraded' for row in result['books']))

    def test_independently_recovered_book_is_included_in_samples(self):
        from bookengine import batch
        actual_read = batch.read_report

        def recovered(entry, out, state=None, at=None):
            if state and state.get('status') == 'failed':
                state = dict(state, status='failed')
                folder = Path(out) / entry['id']
                meta = json.loads((folder / 'run.json').read_text())
                meta['status'] = 'complete'
                (folder / 'run.json').write_text(json.dumps(meta))
            return actual_read(entry, out, state, at)

        # Simulate a successful independent resume after the original child exited nonzero.
        recovered_child = CHILD.replace("if folder.name == os.environ.get('FIXTURE_FAIL'):", 'if False:')
        recovered_child += "\nif folder.name == os.environ.get('FIXTURE_FAIL'):\n    sys.exit(7)\n"
        with patch(__name__ + '.CHILD', recovered_child), patch.object(batch, 'read_report', side_effect=recovered):
            result, _ = self.execute('book-1')
        self.assertEqual((result['status'], result['completed_books']), ('complete', 5))
        self.assertIn('# book-1\n', (self.out / 'READ-ME-FIRST.md').read_text())

    def test_single_chapter_sample_is_not_duplicated(self):
        folder = self.out / 'only'
        folder.mkdir(parents=True)
        (folder / 'book.md').write_text('# 书名\n')
        (folder / 'chapter-001.md').write_text('## 第1章\n\n正文。')
        path = write_reading_sample([dict(id='only', book=dict(chapters=1))], self.out)
        self.assertEqual(path.read_text(), '# 书名\n\n## 第1章\n\n正文。\n')


if __name__ == '__main__':
    unittest.main()
