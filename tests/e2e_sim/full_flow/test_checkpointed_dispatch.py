from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.checkpoint import PhysicalOrdinalStore
from scripts.full_flow.simulation.contracts import ScriptContractError, TemplateCatalog
from scripts.full_flow.simulation.inventory import INVENTORY_SCHEMA
from scripts.full_flow.simulation.quickstart_builders import BuilderContext
from scripts.full_flow.simulation.runtime_adapter import create_checkpointed_runtime_tape

from .test_contracts import ENDPOINTS, USAGE


class CheckpointedDispatchTests(unittest.TestCase):
    def test_recreated_tape_uses_global_ordinal_not_observers_process_counter(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            private, repo = base / "private", base / "repo"
            private.mkdir(mode=0o700)
            repo.mkdir()
            catalog = TemplateCatalog(
                {
                    "schema": INVENTORY_SCHEMA,
                    "parse_failures": [],
                    "dynamic_sites": [],
                    "templates": [
                        {"template": "conception_logline_regen", "historical_only": False}
                    ],
                }
            )
            body = {"model": "offline", "messages": [{"role": "user", "content": "runtime-token"}]}
            captured = []

            def context(observation, wire, ticket):
                captured.append((observation.call_index, ticket["global_ordinal"]))
                return BuilderContext(
                    {"logline": "runtime-answer"}, call_index=observation.call_index
                )

            for expected in (1, 2):
                store = PhysicalOrdinalStore(
                    private / "ordinals.json",
                    private_root=private,
                    repository_root=repo,
                    run_id="synthetic-run",
                    config_sha256="a" * 64,
                )
                store.initialize()
                tape = create_checkpointed_runtime_tape(
                    checkpoint=store,
                    catalog=catalog,
                    context_resolver=context,
                    usage_resolver=lambda observed, wire: USAGE,
                    allowed_endpoints=ENDPOINTS,
                )
                fields = {
                    **wire_fingerprints(body),
                    "native_operation_key": "1" * 64,
                    "native_input_hash": "2" * 64,
                    "native_attempt_id": uuid4().hex,
                }
                # The native observer restarts at 1; synthetic routing must not.
                observed = NativeObservation("conception_logline_regen", 1, None, None)
                tape._choose(body, fields, observed)
                self.assertEqual(tape.document["entries"][0]["match"]["call_index"], expected)
            self.assertEqual(captured, [(1, 1), (2, 2)])
            # A pure matcher call has not consumed an HTTPX stream: never claim
            # delivery/settlement or close these reserved native identities.
            self.assertEqual(store.inspect()["unresolved_ordinals"], [1, 2])

    def test_missing_normal_sdk_identity_is_rejected_without_allocating_credit(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            private, repo = base / "private", base / "repo"
            private.mkdir(mode=0o700)
            repo.mkdir()
            store = PhysicalOrdinalStore(
                private / "ordinals.json",
                private_root=private,
                repository_root=repo,
                run_id="synthetic-run",
                config_sha256="a" * 64,
            )
            store.initialize()
            catalog = TemplateCatalog(
                {
                    "schema": INVENTORY_SCHEMA,
                    "parse_failures": [],
                    "dynamic_sites": [],
                    "templates": [
                        {"template": "conception_logline_regen", "historical_only": False}
                    ],
                }
            )
            tape = create_checkpointed_runtime_tape(
                checkpoint=store,
                catalog=catalog,
                context_resolver=lambda *args: self.fail("must not dispatch"),
                usage_resolver=lambda *args: USAGE,
                allowed_endpoints=ENDPOINTS,
            )
            body = {"model": "offline", "messages": [{"role": "user", "content": "runtime-token"}]}
            with self.assertRaises(ScriptContractError) as raised:
                tape._choose(
                    body, wire_fingerprints(body), NativeObservation("conception_logline_regen", 1)
                )
            self.assertEqual(raised.exception.code, "SCRIPT_NATIVE_SEND_IDENTITY_REQUIRED")
            self.assertEqual(store.inspect()["next_global_ordinal"], 1)
