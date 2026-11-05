import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from deai import io


class ReadJsonTransientTests(unittest.TestCase):
    def test_retries_when_existing_file_briefly_disappears(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger.json'
            path.write_text('[{"stage": "a"}]')
            real = io.read_text
            calls = []

            def flaky(target):
                calls.append(target)
                if len(calls) < 3:
                    raise FileNotFoundError(target)
                return real(target)

            with mock.patch.object(io, 'read_text', flaky), mock.patch.object(io.time, 'sleep'):
                self.assertEqual(io.read_json(path, []), [{'stage': 'a'}])
            self.assertEqual(len(calls), 3)

    def test_missing_file_still_returns_default(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(io.read_json(Path(tmp) / 'none.json', []), [])


if __name__ == '__main__':
    unittest.main()
