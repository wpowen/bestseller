from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.checkpoint import PhysicalOrdinalStore
from scripts.full_flow.simulation.contracts import ScriptContractError


class CheckpointTests(unittest.TestCase):
    def store(self, base, **overrides):
        private, repo = base / "private", base / "repo"
        private.mkdir(mode=0o700, exist_ok=True)
        repo.mkdir(exist_ok=True)
        return PhysicalOrdinalStore(
            private / "ordinals.json",
            private_root=private,
            repository_root=repo,
            run_id="synthetic-run",
            config_sha256=overrides.get("config", "a" * 64),
        )

    def claim(self, store, template="concept_tournament_candidate", attempt=None):
        return store.claim_send(
            template=template,
            native_operation_key="1" * 64,
            native_input_hash="2" * 64,
            wire_sha256="3" * 64,
            native_attempt_id=attempt or uuid4().hex,
        )

    def test_restart_preserves_global_sequence_native_identity_and_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            first = self.store(base)
            first.initialize()
            row = self.claim(first)
            first.record_reply(row["global_ordinal"], response_sha256="4" * 64)
            second = self.store(base)
            second.initialize()  # verifies; never resets.
            self.assertEqual(second.inspect()["unresolved_ordinals"], [1])
            self.assertEqual(self.claim(second, "conception_market")["global_ordinal"], 2)
            self.assertEqual(
                second.inspect()["sends"][0]["native_attempt_id"], row["native_attempt_id"]
            )

    def test_duplicate_attempt_and_new_config_cannot_reset_the_store(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            store = self.store(base)
            store.initialize()
            row = self.claim(store)
            with self.assertRaises(ScriptContractError) as raised:
                self.claim(store, attempt=row["native_attempt_id"])
            self.assertEqual(raised.exception.code, "SCRIPT_PHYSICAL_ATTEMPT_REUSED")
            with self.assertRaises(ScriptContractError):
                self.store(base, config="b" * 64).initialize()
            self.assertEqual(store.inspect()["next_global_ordinal"], 2)

    def test_concurrent_claims_have_one_shared_global_order(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            store = self.store(base)
            store.initialize()
            with ThreadPoolExecutor(max_workers=4) as pool:
                rows = list(pool.map(lambda _: self.claim(self.store(base)), range(8)))
            self.assertEqual(sorted(r["global_ordinal"] for r in rows), list(range(1, 9)))
            self.assertEqual(store.inspect()["next_global_ordinal"], 9)

    def test_wire_completion_is_transport_only_and_history_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(Path(directory).resolve())
            store.initialize()
            row = self.claim(store)
            store.record_reply(1, response_sha256="4" * 64)
            store.record_transport(1, outcome="wire_complete")
            report = store.inspect()
            self.assertFalse(report["native_authority"])
            self.assertEqual(report["unresolved_ordinals"], [])
            self.assertEqual(
                [e["event"] for e in report["sends"][0]["events"]],
                ["reserved", "reply_authored", "wire_complete"],
            )
            self.assertEqual(report["sends"][0]["native_attempt_id"], row["native_attempt_id"])
