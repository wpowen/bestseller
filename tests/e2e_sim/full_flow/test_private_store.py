from pathlib import Path
import stat
import tempfile
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.contracts import CallIdentity, ScriptContractError, build_document
from scripts.full_flow.simulation.private_store import write_private_document

from .test_contracts import ENDPOINTS, catalog


class PrivateStoreTests(unittest.TestCase):
    def document(self):
        return build_document(
            catalog(),
            [(CallIdentity("chapter_review", 1, project_id=str(uuid4())), [{"mode": "timeout"}])],
            allowed_endpoints=ENDPOINTS,
        )

    def test_private_file_is_exclusive_0600_and_checkout_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            repo, private = base / "repository", base / "private"
            repo.mkdir()
            (repo / ".git").mkdir()
            private.mkdir(mode=0o700)
            target = private / "tape.json"
            write_private_document(
                self.document(), target, private_root=private, repository_root=repo
            )
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with self.assertRaises(ScriptContractError):
                write_private_document(
                    self.document(), target, private_root=private, repository_root=repo
                )
            with self.assertRaises(ScriptContractError):
                write_private_document(
                    self.document(), repo / "tape.json", private_root=repo, repository_root=repo
                )

    def test_symlink_and_production_checkout_ancestor_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            repo, private = base / "integration", base / "production/private"
            repo.mkdir()
            private.mkdir(mode=0o700, parents=True)
            (private.parent / ".git").mkdir()
            with self.assertRaises(ScriptContractError):
                write_private_document(
                    self.document(),
                    private / "tape.json",
                    private_root=private,
                    repository_root=repo,
                )
            link = base / "private_link"
            link.symlink_to(private, target_is_directory=True)
            with self.assertRaises(ScriptContractError):
                write_private_document(
                    self.document(), link / "tape.json", private_root=link, repository_root=repo
                )
