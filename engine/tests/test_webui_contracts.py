"""Offline web UI contracts. No DB, paid model calls, or real process signals.

Run from the worktree with:
PYTHONDONTWRITEBYTECODE=1 ../../.venv/bin/python -B -m unittest discover \
    -s engine/tests -p 'test_webui*.py' -v
"""
import ast
from email.parser import BytesParser
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import urlencode
import uuid
import xml.etree.ElementTree as ET
import zipfile


ENGINE = Path(__file__).resolve().parents[1]
WEBUI = ENGINE / 'tools' / 'webui'
sys.path.insert(0, str(WEBUI))
import library
import jobs
import server


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(value, (str, bytes)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding='utf-8')


def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes()
            for p in folder.rglob('*') if p.is_file() and not p.is_symlink()}


class MemorySocket:
    """Feed actual HTTP bytes through BaseHTTPRequestHandler, without binding a port."""
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, *args, **kwargs):
        return self.input

    def sendall(self, data):
        self.output.extend(data)


class WebFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='webui-contract-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {
            'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.lib = library.Library(self.root / 'books')
        self.env = self.root / 'test.env'
        put(self.env, '# deliberately no credentials\n')
        # No background scheduler, paid subprocess, or process signal may escape.
        patches = [mock.patch.object(jobs.threading.Thread, 'start'),
                   mock.patch.object(jobs.subprocess, 'Popen'),
                   mock.patch.object(jobs.os, 'killpg'),
                   mock.patch.object(jobs.os, 'kill')]
        self.thread, self.spawn, self.killpg, self.kill = [p.start() for p in patches]
        for patch in patches:
            self.addCleanup(patch.stop)
        self.child = mock.Mock(pid=812345, returncode=None)
        self.child.poll.return_value = None
        self.spawn.return_value = self.child
        self.queue = jobs.Queue(self.lib, self.env, ENGINE)
        self.addCleanup(self.queue.exclusive.close)
        self.origin = 'http://127.0.0.1:18765'
        self.http = SimpleNamespace(library=self.lib, queue=self.queue,
                                    origin=self.origin, configuration={})

    def book(self, key='webui/story', *, status='queued', budget=.2,
             total=2, final=True):
        folder = self.lib.root / key
        put(folder / 'run.json', {'status': 'running', 'book': {'chapters': total}})
        put(folder / 'web.json', {'title': '潮汐 <归途>', 'status': status,
                                  'budget': budget, 'chapters': total, 'polish': False})
        put(folder / 'book.yaml', {'chapters': total})
        for n in range(1, total + 1):
            put(folder / f'chapter-{n:03d}.body.md', f'草稿{n}：她推开门。<script>危险</script> & 灯光。')
            put(folder / f'chapters/{n:03d}.json', {'title': f'潮声{n}', 'status': 'complete'})
            if final:
                put(folder / f'final/chapter-{n:03d}.md', f'定稿{n}：她听见潮声。<灯> & 船。')
                put(folder / f'final/chapter-{n:03d}.deai.json',
                    {'status': 'polished', 'complete': True})
        put(folder / 'book.md', '# CLI原样导出\n\n草稿总集\n')
        put(folder / 'book.docx', server.docx('CLI草稿总集\n第一章 & <船>'))
        return folder

    def topic(self):
        key = str(uuid.uuid4())
        put(self.lib.own / '.topics' / key / 'topic.json', {
            'id': key, 'status': 'complete', 'chapters': 2,
            'genre': 'cozy-mystery', 'candidates': [
                {'title': '潮声', 'premise': '摆渡人要在涨潮前找回失踪乘客。'}]})
        return {'topic_id': key, 'candidate': 0, 'genre': 'cozy-mystery',
                'chapters': 2, 'model': 'flash', 'budget': .2, 'jobs': 2}

    def request(self, path, *, method='GET', data=None, headers=None, raw=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else b'')
        fields = {'Host': self.origin.removeprefix('http://'), 'Connection': 'close'}
        if method == 'POST':
            fields.update(Origin=self.origin, **{'Content-Type': 'application/json',
                                                'Content-Length': str(len(body))})
        for name, value in (headers or {}).items():
            if value is None:
                fields.pop(name, None)
            else:
                fields[name] = value
        wire = (f'{method} {path} HTTP/1.1\r\n' + ''.join(
            f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n').encode() + body
        sock = MemorySocket(wire)
        server.Handler(sock, ('127.0.0.1', 12345), self.http)
        head, payload = bytes(sock.output).split(b'\r\n\r\n', 1)
        status, header_block = head.split(b'\r\n', 1)
        return int(status.split()[1]), BytesParser().parsebytes(header_block), payload

    def download(self, key='webui/story', fmt='txt', version='draft', **extra):
        return self.request('/download?' + urlencode(dict(
            id=key, format=fmt, version=version, **extra)))


class BoundaryTests(unittest.TestCase):
    def test_ui_import_boundary_allows_only_authorized_topic_transport(self):
        violations = []
        for path in [ENGINE / 'tools/webui.py', *WEBUI.glob('*.py')]:
            tree = ast.parse(path.read_text('utf-8'))
            allowed = set()
            if path == WEBUI / 'jobs.py':
                for function in tree.body:
                    if isinstance(function, ast.FunctionDef) and function.name == 'topic_worker':
                        for node in function.body:
                            if (isinstance(node, ast.ImportFrom) and node.level == 0
                                    and (node.module, tuple(a.name for a in node.names)) in {
                                        ('deai.client', ('MiniMax',)), ('deai.io', ('parse_json',))}):
                                allowed.add(node)
            for node in ast.walk(tree):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    if name.split('.')[0] in {'bookengine', 'deai', 'bestseller'} and node not in allowed:
                        violations.append(f'{path.name}:{node.lineno}: {name}')
        self.assertEqual(violations, [], 'Outside authorized topic transport, use public CLIs: ' + ', '.join(violations))

    def test_import_and_cli_help_work_with_engine_imports_forbidden(self):
        script = """
import importlib.abc, runpy, sys
class DenyEngine(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'bookengine', 'deai', 'bestseller'}:
            raise AssertionError('forbidden engine import: ' + fullname)
sys.meta_path.insert(0, DenyEngine())
sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[1]).parents[1]))
sys.path.insert(0, sys.argv[1])
import library, jobs, server
sys.argv = [sys.argv[2], '--help']
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        result = subprocess.run([sys.executable, '-B', '-I', '-c', script,
                                 str(WEBUI), str(ENGINE / 'tools/webui.py')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('--budget', result.stdout)


class LibraryTests(WebFixture):
    def test_listing_excludes_sensitive_directories_and_symlinks(self):
        self.book('ordinary')
        self.book('nested/visible')
        for key in ['Private/book', 'apiKEY/book', 'd407-copy/book', '.hidden/book',
                    'responses/book', 'steps/book', 'http/book', 'final/book']:
            self.book(key)
        (self.lib.root / 'linked').symlink_to(self.lib.root / 'ordinary', target_is_directory=True)
        self.assertEqual({row['id'] for row in self.lib.listing()}, {'ordinary', 'nested/visible'})

    def test_folder_rejects_traversal_absolute_sensitive_and_symlink_keys(self):
        target = self.book('ordinary')
        (self.lib.root / 'alias').symlink_to(target, target_is_directory=True)
        for key in ['../outside', str(target), '', '.', 'PRIVATE/book', 'some/key-backup',
                    'archive/D407/book', 'alias', 'alias/child']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.lib.folder(key)

    def test_detail_keeps_draft_final_titles_and_reports_separate(self):
        folder = self.book()
        before = snapshot(folder)
        detail = self.lib.detail('webui/story')
        self.assertEqual([r['number'] for r in detail['chapters']], [1, 2])
        self.assertEqual(detail['chapters'][0]['title'], '潮声1')
        self.assertIn('草稿1', detail['chapters'][0]['draft'])
        self.assertIn('定稿1', detail['chapters'][0]['final'])
        self.assertTrue(detail['chapters'][0]['report']['complete'])
        self.assertEqual(snapshot(folder), before)

    def test_legacy_chapters_remain_readable(self):
        folder = self.book(total=0)
        put(folder / 'chapter-003.md', '旧版第三章正文')
        row, = self.lib.detail('webui/story')['chapters']
        self.assertEqual((row['number'], row['draft']), (3, '旧版第三章正文'))

    def test_offline_library_escapes_final_text_without_draft_fallback(self):
        folder = self.book()
        (folder / 'final/chapter-002.md').unlink()
        before = snapshot(folder)
        page = self.lib.static()
        self.assertIn('定稿1', page)
        self.assertNotIn('草稿2', page)
        self.assertIn('本章正在去 AI，完成后显示', page)
        self.assertNotIn('草稿1', page)
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;灯&gt;', page)
        self.assertEqual((self.lib.own / 'LIBRARY.html').read_text('utf-8'), page)
        self.assertEqual(snapshot(folder), before)


class CostTests(WebFixture):
    def test_known_usage_is_not_given_cache_discount(self):
        folder = self.book()
        put(folder / 'calls.json', [{'model': 'MiniMax-M3.1-Flash', 'usage': {
            'prompt_tokens': 1000000, 'completion_tokens': 100000,
            'prompt_tokens_details': {'cached_tokens': 1000000}}}])
        result = library.costs(folder)
        self.assertAlmostEqual(result['known'], .21)
        self.assertEqual(result['cost'], result['known'])
        self.assertEqual(result['unknown'], 0)

    def test_unknown_and_inflight_keep_bounds_across_all_ledger_sources(self):
        folder = self.book()
        put(folder / 'calls.json', [{'usage': {}, 'input_bytes': 100000,
                                    'output_token_cap': 10000, 'status': 'timeout'}])
        put(folder / 'http/ledger.json', [{'usage': None, 'input_bytes': 200000,
                                          'max_tokens': 20000, 'status': 'inflight'}])
        put(folder / 'final/.deai/001/http/ledger.json', [
            {'model': 'MiniMax-M3', 'usage': None, 'input_bytes': 100000,
             'max_tokens': 10000, 'status': 'failed'}])
        before = snapshot(folder)
        result = library.costs(folder)
        self.assertEqual((result['calls'], result['unknown']), (3, 3))
        self.assertAlmostEqual(result['unknown_bound'], .105)
        self.assertEqual(result['cost'], result['unknown_bound'])
        self.assertEqual(library.costs(folder), result)
        self.assertEqual(snapshot(folder), before)

    def test_missing_usage_with_no_caps_is_not_zero_cost(self):
        folder = self.book()
        put(folder / 'calls.json', [{'usage': None}])
        result = library.costs(folder)
        self.assertEqual(result['unknown'], 1)
        self.assertGreater(result['cost'], 0)
        self.assertTrue(math.isfinite(result['cost']))

    def test_partial_usage_does_not_erase_known_tokens_with_smaller_default_bound(self):
        folder = self.book()
        # A legacy receipt can contain known usage while missing input byte caps.
        put(folder / 'calls.json', [{'usage': {'prompt_tokens': 100000},
                                    'output_token_cap': 100}])
        result = library.costs(folder)
        self.assertEqual(result['unknown'], 1)
        self.assertGreaterEqual(result['cost'], .015)




class HttpTests(WebFixture):
    def test_same_origin_json_reaches_queue_once(self):
        data = self.topic()
        status, headers, body = self.request('/api/books', method='POST', data=data,
                                             headers={'Content-Type': 'application/json; charset=utf-8'})
        self.assertEqual(status, 200, body)
        self.assertIn('id', json.loads(body))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.spawn.assert_not_called()

    def test_mutations_reject_wrong_or_missing_origin_host_and_non_json(self):
        data = self.topic()
        before = snapshot(self.lib.own)
        for fields in [{'Origin': None}, {'Origin': 'http://evil.invalid'},
                       {'Host': 'evil.invalid'}, {'Host': 'localhost:18765'},
                       {'Content-Type': 'text/plain'}, {'Content-Type': None}]:
            with self.subTest(headers=fields):
                status, _, _ = self.request('/api/books', method='POST', data=data, headers=fields)
                self.assertEqual(status, 403)
                self.assertEqual(snapshot(self.lib.own), before)
        self.spawn.assert_not_called()

    def test_malformed_json_array_empty_and_oversized_requests_are_rejected(self):
        for raw in [b'{bad', b'[]', b'null', b'', b' ' * 16385]:
            with self.subTest(size=len(raw)):
                status, _, _ = self.request('/api/books', method='POST', raw=raw)
                self.assertEqual(status, 400)
        self.spawn.assert_not_called()

    def test_obsolete_budget_input_is_ignored_through_http(self):
        for value in [float('nan'), float('inf'), -float('inf'), 'NaN']:
            with self.subTest(value=value):
                status, _, body = self.request('/api/books', method='POST', data=dict(self.topic(), budget=value))
                self.assertEqual(status, 200)
                folder = self.lib.folder(json.loads(body)['id'])
                self.assertNotIn('budget', library.read(folder / 'web.json'))
        self.spawn.assert_not_called()

    def test_get_rejects_host_rebinding(self):
        status, _, _ = self.request('/api/books', headers={'Host': 'attacker.invalid'})
        self.assertEqual(status, 403)

    def test_download_rejects_sensitive_symlink_and_traversal_folders(self):
        normal = self.book()
        for key in ['private/book', 'KEY-backup/book', 'archive/d407/book']:
            self.book(key)
        (self.lib.root / 'alias').symlink_to(normal, target_is_directory=True)
        outside = self.root / 'outside'
        put(outside / 'book.md', 'OUTSIDE_SENTINEL')
        (self.lib.root / 'external').symlink_to(outside, target_is_directory=True)
        for key in ['private/book', 'KEY-backup/book', 'archive/d407/book',
                    'alias', 'external', '../outside', str(outside)]:
            for fmt in ['md', 'txt', 'docx']:
                with self.subTest(key=key, fmt=fmt):
                    status, _, body = self.download(key, fmt)
                    self.assertEqual(status, 400)
                    self.assertNotIn(b'OUTSIDE_SENTINEL', body)

    def test_export_file_symlink_cannot_download_external_bytes(self):
        folder = self.book()
        secret = self.root / 'secret.txt'
        put(secret, 'OUTSIDE_SENTINEL')
        for fmt in ['md', 'docx']:
            (folder / ('book.' + fmt)).unlink()
            (folder / ('book.' + fmt)).symlink_to(secret)
            with self.subTest(fmt=fmt):
                status, _, body = self.download(fmt=fmt)
                self.assertEqual(status, 200)
                self.assertNotIn(b'OUTSIDE_SENTINEL', body)

    def test_chapter_symlink_and_parent_symlink_cannot_leak_text(self):
        folder = self.book()
        secret = self.root / 'secret.txt'
        put(secret, 'OUTSIDE_SENTINEL')
        (folder / 'chapter-001.body.md').unlink()
        (folder / 'chapter-001.body.md').symlink_to(secret)
        status, _, body = self.request('/api/book?' + urlencode({'id': 'webui/story'}))
        self.assertEqual(status, 400)
        self.assertNotIn(b'OUTSIDE_SENTINEL', body)
        (folder / 'chapter-001.body.md').unlink()
        put(folder / 'chapter-001.body.md', '正常')
        external = self.root / 'external-final'
        put(external / 'chapter-001.md', 'OUTSIDE_SENTINEL')
        for p in (folder / 'final').iterdir():
            p.unlink()
        (folder / 'final').rmdir()
        (folder / 'final').symlink_to(external, target_is_directory=True)
        status, _, body = self.download(version='final')
        self.assertEqual(status, 400)
        self.assertNotIn(b'OUTSIDE_SENTINEL', body)

    def test_no_generic_file_download_or_format_path_injection(self):
        self.book()
        for path in ['/download?' + urlencode({'id': 'webui/story', 'format': '../../test.env'}),
                     '/download?' + urlencode({'id': 'webui/story', 'format': 'json'}),
                     '/download?' + urlencode({'id': 'webui/story', 'format': 'txt', 'version': '../run.json'}),
                     '/test.env', '/run.json', '/../../test.env']:
            with self.subTest(path=path):
                status, _, _ = self.request(path)
                self.assertIn(status, (400, 404))

    def test_legacy_draft_query_downloads_only_final_outputs(self):
        folder = self.book()
        for fmt in ['md', 'docx']:
            with self.subTest(fmt=fmt):
                status, headers, body = self.download(fmt=fmt)
                self.assertEqual(status, 200)
                self.assertNotEqual(body, (folder / ('book.' + fmt)).read_bytes())
                if fmt == 'md': self.assertIn('定稿1', body.decode())
                self.assertIn('book-final.' + fmt, headers['Content-Disposition'])
                self.assertEqual(int(headers['Content-Length']), len(body))

    def test_draft_txt_and_final_exports_never_mix_versions(self):
        self.book()
        for version, expected, excluded in [('draft', '定稿', '草稿'), ('final', '定稿', '草稿')]:
            with self.subTest(version=version):
                status, _, body = self.download(version=version)
                self.assertEqual(status, 200)
                content = body.decode('utf-8')
                self.assertIn(expected + '1', content)
                self.assertIn(expected + '2', content)
                self.assertNotIn(excluded, content)
                self.assertLess(content.index(expected + '1'), content.index(expected + '2'))

    def test_missing_final_never_silently_downloads_draft(self):
        folder = self.book()
        (folder / 'final/chapter-002.md').unlink()
        for fmt in ['md', 'txt', 'docx']:
            with self.subTest(fmt=fmt):
                status, headers, _ = self.download(fmt=fmt, version='final')
                self.assertEqual(status, 200)
                self.assertEqual(headers['X-Missing-Chapters'], '2')

    def test_retained_or_partial_polish_is_not_published_as_final(self):
        folder = self.book()
        for report in [{'status': 'retained', 'complete': False},
                       {'status': 'partial', 'complete': False}, {'status': 'error'}]:
            put(folder / 'final/chapter-001.deai.json', report)
            with self.subTest(report=report):
                status, headers, _ = self.download(version='final')
                self.assertEqual(status, 200)
                self.assertEqual(headers['X-Missing-Chapters'], '1')

    def test_empty_book_cannot_download_fake_complete_final(self):
        self.book(total=0)
        status, _, _ = self.download(version='final')
        self.assertEqual(status, 400)

    def test_final_docx_is_valid_package_with_escaped_chinese_paragraphs(self):
        self.book()
        status, headers, body = self.download(fmt='docx', version='final')
        self.assertEqual(status, 200)
        self.assertIn('wordprocessingml', headers['Content-Type'])
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertIsNone(archive.testzip())
            for name in ['[Content_Types].xml', '_rels/.rels', 'word/document.xml']:
                ET.fromstring(archive.read(name))
            doc = ET.fromstring(archive.read('word/document.xml'))
            paragraphs = [''.join(p.itertext()) for p in doc.findall(
                './/{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p')]
        text = '\n'.join(paragraphs)
        self.assertIn('潮汐 <归途>', text)
        self.assertIn('定稿1：她听见潮声。<灯> & 船。', text)
        self.assertNotIn('草稿', text)

    def test_http_legacy_stopped_resume_preserves_run_and_state(self):
        folder = self.book(status='budget_paused')
        put(folder / 'run.json', {'status': 'stopped', 'stop_reason': 'budget'})
        put(folder / 'state.json', {'checkpoint': 1})
        before = snapshot(folder)
        status, _, _ = self.request('/api/action', method='POST',
                                     data={'id': 'webui/story', 'action': 'resume'})
        self.assertEqual(status, 200)
        self.assertEqual({k: v for k, v in snapshot(folder).items() if k != 'web.json'},
                         {k: v for k, v in before.items() if k != 'web.json'})
        self.assertEqual(library.read(folder / 'web.json')['status'], 'queued')
        self.spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
