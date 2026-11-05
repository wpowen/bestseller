"""Offline checks of manifest validation and checkpoint statistics only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from bookengine.batch import (format_report, is_complete, read_manifest, read_report,
                              sum_estimates, usage_report, validate_options, write_reading_sample)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.book = dict(prompt_pack='realistic-life', chapters=2,
                         words_per_chapter=2200, user_input='一家人的选择。')
        self.book_path = self.root / 'book.yaml'
        self.book_path.write_text(yaml.safe_dump(self.book, allow_unicode=True), encoding='utf-8')
        self.manifest = self.root / 'batch.yaml'
        self.entry = dict(id='realistic-5', path=self.book_path, book=self.book)
        self.folder = self.root / self.entry['id']
        self.folder.mkdir()

    def write(self, name, value):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def manifest_books(self, books):
        self.manifest.write_text(yaml.safe_dump(dict(books=books)), encoding='utf-8')
        return read_manifest(self.manifest)

    def test_manifest_resolves_from_its_directory(self):
        books = self.manifest_books([dict(id='realistic-5', path='book.yaml')])
        self.assertEqual(books, [self.entry])

    def test_manifest_rejects_unsafe_ids_and_duplicates(self):
        for key in ('', '../x', '/tmp/x', 'a/b', 'A', 'x.y', 'x\ny', '_x', '书', 'x' * 65, None, 1):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.manifest_books([dict(id=key, path='book.yaml')])
        with self.assertRaises(ValueError):
            self.manifest_books([dict(id='same', path='book.yaml')] * 2)

    def test_manifest_rejects_missing_or_invalid_paths_and_books(self):
        for books in ([], None, 'x', [None], [dict(id='x')],
                      [dict(id='x', path='book.yaml', extra=True)], [dict(id='x', path=1)]):
            with self.subTest(books=books), self.assertRaises(ValueError):
                self.manifest_books(books)
        with self.assertRaises(FileNotFoundError):
            self.manifest_books([dict(id='x', path='missing.yaml')])
        with self.assertRaises(ValueError):
            self.manifest_books([dict(id='x', path='.')])
        self.book['chapters'] = 2001
        self.book_path.write_text(yaml.safe_dump(self.book), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.manifest_books([dict(id='x', path='book.yaml')])

    def test_options_reject_invalid_values(self):
        for jobs in (0, -1, 7, True, 1.0, '2', None):
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                validate_options(180, jobs, 5)
        for bad in (0, -1, True, None, '5', float('inf'), float('nan')):
            for values in ((bad, 6, 5), (180, 6, bad)):
                with self.subTest(values=values), self.assertRaises(ValueError):
                    validate_options(*values)
        for jobs in (1, 6):
            validate_options(180, jobs, 0.1)

    def test_complete_requires_both_exports_and_complete_metadata(self):
        meta = dict(status='complete')
        self.assertFalse(is_complete(self.folder, meta))
        (self.folder / 'book.md').write_text('正文', encoding='utf-8')
        self.assertFalse(is_complete(self.folder, meta))
        (self.folder / 'book.docx').write_bytes(b'local fixture')
        self.assertTrue(is_complete(self.folder, meta))
        self.assertFalse(is_complete(self.folder, dict(status='running')))

    def test_usage_costs_with_and_without_cached_discount(self):
        calls = [dict(seq=1, status='ok', attempt=1,
                      usage=dict(prompt_tokens=1_000_000, completion_tokens=100_000,
                                 total_tokens=1_100_000, prompt_tokens_details=dict(cached_tokens=500_000)))]
        original = copy.deepcopy(calls)
        row = usage_report(calls)
        self.assertEqual(calls, original)
        self.assertEqual(row['cached_tokens'], 500_000)
        self.assertEqual(row['estimated_usd'], dict(cache_adjusted=0.1425, no_cache_discount=0.21,
                                                  unknown_upper_bound=0, conservative_total=0.21))
        self.assertEqual(row['unknownusage'], 0)

    def test_network_failures_retries_and_unknown_cost_upper_bound(self):
        calls = [dict(seq=1, stage='01-ideas', status='transport_error', attempt=1,
                      input_bytes=1000, output_token_cap=2000),
                 dict(seq=2, stage='01-ideas', status='http_error', attempt=2, usage={},
                      input_bytes=2000, output_token_cap=3000),
                 dict(seq=3, stage='01-ideas', status='ok', attempt=3,
                      usage=dict(prompt_tokens=100, completion_tokens=50, total_tokens=150))]
        row = usage_report(calls)
        self.assertEqual((row['calls'], row['failures'], row['retries'], row['unknownusage']), (3, 2, 2, 2))
        self.assertEqual([x['seq'] for x in row['missing_usage']], [1, 2])
        self.assertEqual(row['missing_usage'][0]['fields'], ['prompt_tokens', 'completion_tokens', 'total_tokens'])
        self.assertAlmostEqual(row['estimated_usd']['unknown_upper_bound'], 0.00345)
        self.assertAlmostEqual(row['estimated_usd']['conservative_total'], 0.003495)

    def test_missing_or_invalid_caps_make_bound_and_conservative_total_unknown(self):
        for caps in ({}, dict(input_bytes=100), dict(output_token_cap=100),
                     dict(input_bytes=True, output_token_cap=1),
                     dict(input_bytes=1, output_token_cap=-1)):
            with self.subTest(caps=caps):
                row = usage_report([dict(status='pending', attempt=1, **caps)])
                self.assertIsNone(row['estimated_usd']['unknown_upper_bound'])
                self.assertIsNone(row['estimated_usd']['conservative_total'])
                self.assertEqual(row['unknownusage'], 1)
                self.assertEqual(row['failures'], 0)

    def test_partial_usage_explicit_and_conservative_double_count_allowed(self):
        row = usage_report([dict(seq=1, status='failed', attempt=1, input_bytes=1000, output_token_cap=2000,
                                 usage=dict(prompt_tokens=100, completion_tokens=20))])
        self.assertEqual(row['total_tokens'], 120)
        self.assertEqual(row['missing_usage'][0]['fields'], ['total_tokens'])
        self.assertAlmostEqual(row['estimated_usd']['conservative_total'], 0.001377)

    def test_totals_fallback_keeps_missing_usage_unknown(self):
        row = usage_report([], dict(calls=4, prompt_tokens=100, completion_tokens=20,
                                    total_tokens=120, retries=2, missing_usage_calls=3))
        self.assertEqual((row['calls'], row['unknownusage'], row['retries']), (4, 3, 2))
        self.assertIsNone(row['estimated_usd']['conservative_total'])

    def test_usage_separates_base_length_and_transport_retries(self):
        calls = [dict(stage=stage, attempt=attempt, status='ok') for stage, attempt in
                 [('01-ideas', 1), ('ch1-draft', 1), ('ch1-length', 1), ('ch1-length', 2),
                  ('ch1-length', 3), ('ch1-state', 1), ('ch1-state', 2), ('ch2-length', 1)]]
        row = usage_report(calls)
        self.assertEqual((row['base_calls'], row['length_calls'], row['retry_calls']), (3, 2, 3))
        self.assertEqual(row['length_attempts'], 4)
        self.assertEqual(row['calls'], row['base_calls'] + row['length_calls'] + row['retry_calls'])
        self.assertEqual(row['retries'], row['retry_calls'])

    def test_legacy_totals_do_not_invent_stage_categories(self):
        row = usage_report([], dict(calls=8, retries=3))
        self.assertEqual(row['retry_calls'], 3)
        for key in ('base_calls', 'length_calls', 'length_attempts'):
            self.assertIsNone(row[key])
            self.assertEqual(usage_report([])[key], 0)

    def test_reading_sample_contains_only_titles_and_q1_selected_chapters(self):
        entries, expected = [], []
        for i in range(6):
            entry = dict(id=f'book-{i}', book=dict(chapters=3))
            entries.append(entry)
            folder = self.root / entry['id']
            folder.mkdir()
            title = f'# 真实书名 {i}'
            (folder / 'book.md').write_text(f'{title}\n\n此处并非选读正文', encoding='utf-8')
            expected.append(title)
            for n in range(1, 4):
                text = f'## 第{n}章 章节标题 {i}\n\n正文 {i}—{n}。\n\n下一段。'
                (folder / f'chapter-{n:03d}.md').write_text(text + '\n\n', encoding='utf-8')
                if n == 1:
                    expected.append(text)
        before = {p for p in self.root.rglob('*') if p.is_file()}
        path = write_reading_sample(entries, self.root)
        self.assertEqual(path, self.root / 'READ-ME-FIRST.md')
        self.assertEqual(path.read_text(encoding='utf-8'), '\n\n'.join(expected) + '\n')
        self.assertEqual({p for p in self.root.rglob('*') if p.is_file()} - before, {path})

    def test_batch_aggregate_propagates_unknown_and_sums_known_costs(self):
        known = usage_report([dict(status='ok', usage=dict(prompt_tokens=100, completion_tokens=20,
                                                          total_tokens=120))])
        unknown = usage_report([dict(status='transport_error')])
        self.assertEqual(sum_estimates([known, known])['conservative_total'], 0.000054)
        combined = sum_estimates([known, unknown])
        self.assertEqual(combined['no_cache_discount'], 0.000027)
        self.assertIsNone(combined['conservative_total'])
        self.assertIsNone(combined['unknown_upper_bound'])

    def test_read_progress_excludes_retry_files_and_counts_only_saved_body(self):
        quality = dict(chapters=[dict(chapter=1, chars=4, within_band=False, issues=[], state={})],
                       context_limit_chars=48000)
        self.write('run.json', dict(started='2026-10-30T00:00:00+00:00', status='running', quality=quality))
        self.write('calls.json', [dict(seq=1, stage='ch1-state', attempt=1, status='ok', input_chars=48000),
                                  dict(seq=2, stage='ch2-draft', attempt=1, status='pending', input_chars=1234)])
        self.write('steps/01-ideas.json', {})
        self.write('steps/ch1-state.retry.json', dict(failures=4, waited=970))
        self.write('steps/ch2-draft.retry.json', dict(failures=1, waited=10))
        self.write('chapters/001.json', dict(status='complete'))
        (self.folder / 'chapter-001.body.md').write_text('甲乙，丙丁。ABC123', encoding='utf-8')
        (self.folder / 'steps/ch2-draft.md').write_text('未完成章节', encoding='utf-8')
        before = {p: p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        row = read_report(self.entry, self.root, dict(status='running', started='2026-10-31T00:00:00+00:00'),
                          at='2026-10-31T00:00:00+00:00')
        self.assertEqual(row['chapter_chinese_chars'], [4, None])
        self.assertEqual(row['chapter_progress'], dict(complete=1, total=2))
        self.assertEqual(row['completed_stages'], 2)
        self.assertEqual(row['stage'], 'ch2-draft')
        self.assertEqual(row['wall_seconds'], 86400)
        self.assertEqual(row['max_input_chars'], 48000)
        self.assertEqual(row['quality'], quality)
        for word in ('status=', 'stage=', 'chapters=1/2', 'calls=', 'tokens=', 'elapsed=',
                     'failures=', 'base=', 'length=', 'retries=', 'word_counts=', 'unknownusage='):
            self.assertIn(word, format_report(row))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_finished_book_preserves_original_time_and_run_word_counts(self):
        (self.folder / 'book.md').write_text('# 书名\n', encoding='utf-8')
        (self.folder / 'book.docx').write_bytes(b'local fixture')
        self.write('run.json', dict(started='2026-10-30T00:00:00+00:00',
                                    finished='2026-10-30T00:01:00+00:00', status='complete',
                                    chapter_chinese_chars=[2100, 2201]))
        for n, count in enumerate((2100, 2201), 1):
            self.write(f'chapters/{n:03d}.json', dict(status='complete'))
            (self.folder / f'chapter-{n:03d}.body.md').write_text('甲' * count, encoding='utf-8')
        row = read_report(self.entry, self.root, dict(status='complete', finished='2026-11-01T00:00:00+00:00'),
                          at='2026-11-01T00:00:00+00:00')
        self.assertEqual(row['wall_seconds'], 60)
        self.assertEqual(row['finished'], '2026-10-30T00:01:00+00:00')
        self.assertEqual(row['chapter_chinese_chars'], [2100, 2201])

    def test_export_counts_exclude_heading_and_corrupt_reads_are_visible(self):
        self.write('chapters/001.json', dict(status='complete'))
        (self.folder / 'chapter-001.body.md').write_text('## 第一章 标题\n\n甲乙\n\n丙丁', encoding='utf-8')
        (self.folder / 'calls.json').write_text('{broken', encoding='utf-8')
        row = read_report(self.entry, self.root)
        self.assertEqual(row['chapter_chinese_chars'], [4, None])
        self.assertEqual(row['read_errors'], ['calls.json'])
        self.assertIsNone(row['calls'])
        self.assertIsNone(row['unknownusage'])
        self.assertIsNone(row['estimated_usd']['conservative_total'])

    def test_six_examples_match_genres_lengths_and_original_realistic_input(self):
        examples = Path(__file__).resolve().parents[1] / 'examples'
        entries = read_manifest(examples / 's2/batch.yaml')
        original = yaml.safe_load((examples / 'realistic-life.yaml').read_text(encoding='utf-8'))
        self.assertEqual(len(entries), 6)
        self.assertEqual({(e['book']['prompt_pack'], e['book']['chapters']) for e in entries},
                         {(genre, chapters) for genre in ('realistic-life', 'xuanhuan-power-fantasy',
                                                          'suspense-mystery') for chapters in (5, 10)})
        for entry in entries:
            self.assertEqual(entry['book']['words_per_chapter'], 2200)
            if entry['book']['prompt_pack'] == 'realistic-life':
                self.assertEqual(entry['book']['user_input'], original['user_input'])


if __name__ == '__main__':
    unittest.main()
