"""Safe failures retain executable locations without source or exception text."""

import hashlib
import json
import unittest

from e2e_sim.diagnostics import safe_exception


class SafeStackTests(unittest.TestCase):
    def test_stack_has_message_hash_lengths_and_every_frame_without_locals(self):
        secret = "PRIVATE_PROMPT 正文 token=never-retain"

        def origin():
            private_local = secret
            raise ValueError(private_local)

        try:
            origin()
        except ValueError as error:
            proof = safe_exception(error)
        self.assertEqual(proof["error_type"], "ValueError")
        self.assertEqual(proof["error_sha256"], hashlib.sha256(secret.encode()).hexdigest())
        self.assertEqual(proof["error_message_chars"], len(secret))
        self.assertEqual(proof["error_message_bytes"], len(secret.encode()))
        self.assertEqual(len(proof["safe_stack"]), 2)
        self.assertEqual(proof["safe_stack"][-1]["function"], "origin")
        self.assertTrue(
            all(set(frame) == {"file", "function", "line"} for frame in proof["safe_stack"])
        )
        self.assertNotIn(secret, json.dumps(proof))
        self.assertNotIn("private_local", json.dumps(proof))
        self.assertTrue(all(frame["line"] > 0 for frame in proof["safe_stack"]))

    def test_native_code_and_chained_errors_are_safe_and_not_lost(self):
        class NativeStopError(ValueError):
            code = "execution_provider_rejected"

        try:
            try:
                raise ValueError("hidden body A")
            except ValueError as error:
                raise NativeStopError("hidden body B") from error
        except NativeStopError as error:
            proof = safe_exception(error)
        self.assertIn("execution_provider_rejected", proof["error_codes"])
        self.assertEqual(proof["causes"][0]["error_type"], "ValueError")
        self.assertNotIn("hidden body", json.dumps(proof))

    def test_message_prose_and_arbitrary_tokens_do_not_become_codes(self):
        proof = safe_exception(ValueError("secret_token=PRIVATE_BODY no_machine_code"))
        self.assertEqual(proof["error_codes"], [])
        self.assertEqual(proof["safe_stack"], [])
