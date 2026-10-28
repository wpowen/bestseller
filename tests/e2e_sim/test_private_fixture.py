"""No manuscript fixture may be imported into the integration Git tree."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from e2e_sim.tapes import document

class PrivateFixtureTests(unittest.TestCase):
    def test_missing_private_fixture_is_explicit(self):
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaisesRegex(ValueError,'SCRIPT_FIXTURE_PRIVATE_PATH_REQUIRED'):
                document()
    def test_repository_fixture_is_rejected(self):
        with patch.dict('os.environ',{'P3_SYNTHETIC_BODY_PATH':str(Path(__file__).resolve())}):
            with self.assertRaisesRegex(ValueError,'SCRIPT_FIXTURE_MUST_STAY_OUTSIDE_REPOSITORY'):
                document()
    def test_wrong_fixture_bytes_are_not_substituted(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as folder:
            p=Path(folder)/'fixture.md';p.write_text('not the fixture')
            with patch.dict('os.environ',{'P3_SYNTHETIC_BODY_PATH':str(p)}):
                with self.assertRaisesRegex(ValueError,'SCRIPT_FIXTURE_SHA_MISMATCH'):
                    document()
