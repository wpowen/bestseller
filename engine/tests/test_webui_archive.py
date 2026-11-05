"""Archive/API safety and read-only startup, exclusively using temporary books."""
import json
import os
import subprocess
import sys
from unittest import mock
from urllib.parse import urlencode

from test_webui_contracts import ENGINE, WebFixture, jobs, library, put, server, snapshot


class ArchiveTests(WebFixture):
    def action(self, action, key='webui/story', title=None):
        return self.queue.action(key, action, title=title)

    def test_archive_restore_and_http_listing_preserve_manuscripts(self):
        folder = self.book(status='paused')
        before = snapshot(folder)
        self.action('archive')
        self.assertFalse(folder.exists())
        self.assertEqual(self.lib.listing(), [])
        status, _, body = self.request('/api/archive')
        self.assertEqual(status, 200)
        row, = json.loads(body)['books']
        self.assertEqual(row['id'], 'webui/.archive/story')
        self.assertTrue(row['owned'])
        self.assertEqual(snapshot(self.lib.folder(row['id'])), before)
        self.action('restore', row['id'])
        self.assertEqual(snapshot(folder), before)
        self.assertEqual(len(self.lib.listing()), 1)
        entries = [json.loads(line) for line in (self.lib.own / '.ops.log').read_text().splitlines()]
        self.assertEqual([r['action'] for r in entries], ['archive', 'restore'])
        self.spawn.assert_not_called()

    def test_delete_requires_current_display_title_and_logs(self):
        folder = self.book(status='complete')
        put(folder / 'story_bible.yaml', 'title: 圣经旧书名\n')
        before = snapshot(self.lib.root)
        for title in (None, '', '潮汐 <归途>', 'wrong'):
            with self.subTest(title=title), self.assertRaises(ValueError):
                self.action('delete', title=title)
            self.assertEqual(snapshot(self.lib.root), before)
        status, _, body = self.request('/api/action', method='POST', data={
            'id': 'webui/story', 'action': 'delete', 'title': '圣经旧书名'})
        self.assertEqual(status, 200, body)
        self.assertFalse(folder.exists())
        self.assertEqual(json.loads((self.lib.own / '.ops.log').read_text())['action'], 'delete')

    def test_all_mutations_reject_shared_nested_or_unmanaged_books(self):
        for key in ('shared', 'webui/nested/book', 'webui/.archive/nested/book'):
            self.book(key, status='complete')
            before = snapshot(self.lib.root)
            for action in ('archive', 'restore', 'delete', 'rename'):
                with self.subTest(key=key, action=action), self.assertRaises(ValueError):
                    self.action(action, key, title='潮汐 <归途>')
                self.assertEqual(snapshot(self.lib.root), before)
        folder = self.book(status='complete')
        (folder / 'web.json').unlink()
        with self.assertRaises(ValueError):
            self.action('delete', title='story')

    def test_live_paused_orphaned_and_running_books_reject_moves_and_delete(self):
        folder = self.book()
        for state in ('running', 'polishing', 'orphaned', 'paused', 'budget_paused', 'complete'):
            data = library.read(folder / 'web.json')
            data.update(status=state, pid=812345)
            put(folder / 'web.json', data)
            before = snapshot(self.lib.root)
            for action in ('archive', 'delete', 'rename'):
                with self.subTest(state=state, action=action), self.assertRaises(ValueError):
                    self.action(action, title='潮汐 <归途>')
                self.assertEqual(snapshot(self.lib.root), before)
        data.pop('pid'); data['status'] = 'paused'; put(folder / 'web.json', data)
        self.queue.processes['webui/story'] = dict(p=self.child, kind='book', folder=folder, paused=True)
        with self.assertRaises(ValueError):
            self.action('archive')

    def test_stale_pid_can_archive_but_restore_conflict_cannot_overwrite(self):
        folder = self.book(status='paused')
        data = library.read(folder / 'web.json'); data['pid'] = 812345
        put(folder / 'web.json', data)
        self.kill.side_effect = ProcessLookupError
        self.action('archive')
        self.book(status='complete')
        before = snapshot(self.lib.root)
        with self.assertRaises(ValueError):
            self.action('restore', 'webui/.archive/story')
        self.assertEqual(snapshot(self.lib.root), before)

    def test_display_title_prefers_rename_then_bible_then_topic_card(self):
        folder = self.book(status='complete')
        self.assertEqual(self.lib.detail('webui/story')['meta']['title'], '潮汐 <归途>')
        put(folder / 'story_bible.yaml', 'title: 《写作中定下的书名》\n')
        self.assertEqual(self.lib.detail('webui/story')['meta']['title'], '《写作中定下的书名》')
        self.action('rename', title='用户改的名')
        self.assertEqual(self.lib.detail('webui/story')['meta']['title'], '用户改的名')
        self.assertEqual(json.loads((folder / 'web.json').read_text())['title'], '潮汐 <归途>')

    def test_rename_changes_web_title_only_and_checks_length(self):
        folder = self.book(status='complete')
        put(folder / 'story_bible.yaml', 'title: 原始设定\n')
        before = snapshot(folder)
        for title in (None, '', '  ', 3, 'x' * 81, 'name\nlog'):
            with self.subTest(title=title), self.assertRaises(ValueError):
                self.action('rename', title=title)
        self.action('rename', title=' 新书名 ')
        self.assertEqual(self.lib.detail('webui/story')['meta']['title'], '新书名')
        after = snapshot(folder)
        self.assertEqual({k: v for k, v in after.items() if k != 'web.json'},
                         {k: v for k, v in before.items() if k != 'web.json'})
        with self.assertRaises(ValueError):
            self.action('delete', title='潮汐 <归途>')
        self.action('delete', title='新书名')

    def test_symlinks_and_paths_cannot_escape_or_modify_targets(self):
        folder = self.book(status='complete')
        outside = self.root / 'outside'
        put(outside / 'sentinel', 'untouched')
        for key in ('../outside', str(outside), 'webui/../story', 'webui/.archive/../story'):
            for action in ('archive', 'delete', 'restore', 'rename'):
                with self.subTest(key=key, action=action), self.assertRaises(ValueError):
                    self.action(action, key, title='潮汐 <归途>')
        for path in (folder / 'web.json', folder / 'final', self.lib.own / '.archive',
                     self.lib.own / '.ops.log', self.lib.own / '.spent.json'):
            # Fresh book for each symlink attack; never dereference the target.
            import shutil
            if path.is_dir(): shutil.rmtree(path)
            elif path.exists(): path.unlink()
            path.symlink_to(outside, target_is_directory=True)
            before = snapshot(self.lib.root)
            for action in ('archive', 'delete', 'rename'):
                with self.subTest(path=path, action=action), self.assertRaises(ValueError):
                    self.action(action, title='潮汐 <归途>')
                self.assertEqual(snapshot(self.lib.root), before)
            path.unlink()
            self.book(status='complete')
        self.assertEqual((outside / 'sentinel').read_text(), 'untouched')

    def test_archive_restore_delete_keep_spending_unknown_and_topic_costs(self):
        folder = self.book(status='paused')
        put(folder / 'calls.json', [{'usage': None, 'input_bytes': 100000, 'max_tokens': 10000}])
        put(folder / 'final/.deai/001/http/ledger.json', [{'usage': None}])
        data = library.read(folder / 'web.json'); data.update(polish=True)
        put(folder / 'web.json', data)
        put(self.lib.own / '.topics/topic/calls.json', [{'usage': {'prompt_tokens': 100000, 'completion_tokens': 0}}])
        original = self.queue.spending()
        for _ in range(2):
            self.action('archive')
            self.assertAlmostEqual(self.queue.spending(), original)
            self.action('restore', 'webui/.archive/story')
            self.assertAlmostEqual(self.queue.spending(), original)
        self.action('archive')
        self.action('delete', 'webui/.archive/story', '潮汐 <归途>')
        self.assertAlmostEqual(self.queue.spending(), original)
        paid = library.read(self.lib.own / '.spent.json')['webui/story']
        self.assertEqual(paid['unknown'], 2)
        self.assertGreater(paid['unknown_bound'], 0)
        self.assertAlmostEqual(self.queue.committed(), original)
        with self.assertRaises(ValueError):
            self.action('delete', 'webui/.archive/story', '潮汐 <归途>')
        self.assertAlmostEqual(self.queue.spending(), original)
        self.spawn.assert_not_called()

    def test_summary_written_before_interrupted_delete_is_not_double_counted(self):
        folder = self.book(status='complete')
        put(folder / 'calls.json', [{'usage': None}])
        original = self.queue.spending()
        with mock.patch('jobs.shutil.rmtree', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.action('delete', title='潮汐 <归途>')
        self.assertAlmostEqual(self.queue.spending(), original)
        self.action('delete', title='潮汐 <归途>')
        self.assertAlmostEqual(self.queue.spending(), original)

    def test_metadata_includes_synopsis_chapter_status_words_and_yaml(self):
        folder = self.book(status='complete')
        put(folder / 'story_bible.yaml', 'title: 旧标题\npremise: 摆渡人寻找乘客。\ncharacters:\n  - name: 林舟\n    role: 主角\n    desire: 找回失踪乘客\n')
        (folder / 'final/chapter-002.md').unlink()
        data = library.read(folder / 'run.json'); data['book']['chapters'] = 3
        put(folder / 'run.json', data)
        detail = self.lib.detail('webui/story')
        self.assertEqual(detail['meta']['synopsis'], '摆渡人寻找乘客。')
        self.assertIn('name: 林舟', detail['bible'])
        self.assertEqual(detail['characters'][0]['name'], '林舟')
        self.assertEqual([c['status'] for c in detail['meta']['chapter_info']], ['final', 'draft', 'pending'])
        self.assertTrue(all(c['words'] > 0 for c in detail['chapters']))
        self.assertEqual(detail['meta']['chapter_info'][2]['words'], 0)

    def test_characters_accept_named_mapping_and_damaged_bible(self):
        folder = self.book(status='complete')
        for source, expected in [
            ('characters:\n  林舟:\n    role: 主角\n', [{'name': '林舟', 'role': '主角'}]),
            ('characters: [林舟, null, 2]\n', []),
            ('[broken', []), ('- invalid root', []),
        ]:
            put(folder / 'story_bible.yaml', source)
            with self.subTest(source=source):
                code, _, body = self.request('/api/book?id=webui/story')
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body)['characters'], expected)

    def test_frontend_modules_are_served_without_path_traversal(self):
        for asset in ('shared.js', 'library.js', 'wizard.js', 'book.js', 'reader.js'):
            with self.subTest(asset=asset):
                code, headers, body = self.request('/' + asset)
                self.assertEqual(code, 200)
                self.assertIn('javascript', headers['Content-Type'])
                self.assertTrue(body)
        self.assertEqual(self.request('/../jobs.py')[0], 404)


class ReadOnlyTests(WebFixture):
    def test_library_read_only_creates_nothing_and_static_is_memory_only(self):
        missing = self.root / 'missing'
        lib = library.Library(missing, read_only=True)
        self.assertFalse(missing.exists())
        self.assertEqual(lib.listing(), [])
        self.assertIn('我的书库', lib.static())
        self.assertFalse(missing.exists())
        self.book(status='running')
        before = snapshot(self.lib.root)
        lib = library.Library(self.lib.root, read_only=True)
        self.assertEqual(len(lib.listing()), 1)
        lib.static()
        self.assertEqual(snapshot(self.lib.root), before)
        with self.assertRaises(ValueError):
            jobs.Queue(lib, self.env, ENGINE)

    def test_http_read_only_gets_work_and_all_posts_fail_without_queue(self):
        self.book(status='running')
        self.http.library = library.Library(self.lib.root, read_only=True)
        self.http.queue = None
        self.http.configuration = server.config(self.root / 'absent.env')
        before = snapshot(self.lib.root)
        for path in ('/api/books', '/api/archive', '/api/config', '/api/book?id=webui/story', '/LIBRARY.html'):
            code, _, body = self.request(path)
            self.assertEqual(code, 200, body)
        for path in ('/api/books', '/api/topics', '/api/action', '/anything'):
            code, _, _ = self.request(path, method='POST', data={})
            self.assertEqual(code, 403)
        self.assertEqual(snapshot(self.lib.root), before)

    def test_main_read_only_does_not_start_queue_or_create_files(self):
        books = self.root / 'untouched'
        class StubServer:
            def __init__(self, *args): pass
            def serve_forever(self): pass
            def server_close(self): pass
        with mock.patch.object(server, 'Queue') as queue, mock.patch.object(server, 'ThreadingHTTPServer', StubServer), \
             mock.patch.object(server.signal, 'signal'), mock.patch.object(sys, 'argv', [
                 'webui', '--read-only', '--books', str(books), '--env', str(self.root / 'none.env'), '--port', '8788']):
            server.main()
        queue.assert_not_called()
        self.assertFalse(books.exists())
