"""Strict wire endpoints follow runtime routes without editing native requests."""

import unittest

from e2e_sim.tapes import endpoints_from_environment


class TapeEndpointTests(unittest.TestCase):
    def test_explicit_original_route_maps_exact_chat_endpoint(self):
        env = {
            "BESTSELLER__LLM__WRITER__API_BASE": '"https://provider.invalid/v1/"',
            "BESTSELLER__LLM__CRITIC__API_BASE": "https://provider.invalid/v1",
        }
        endpoints = endpoints_from_environment(env)
        self.assertEqual(
            endpoints,
            [
                {
                    "scheme": "https",
                    "host": "provider.invalid",
                    "port": 443,
                    "path": "/v1/chat/completions",
                }
            ],
        )

    def test_credentials_query_and_non_http_routes_fail_before_service_start(self):
        for base in (
            "https://u:PRIVATE@provider.invalid/v1",
            "https://provider.invalid/v1?key=PRIVATE",
            "file:///private/payload",
            "https://*.invalid/v1",
        ):
            with self.subTest(index=len(base)), self.assertRaises(ValueError):
                endpoints_from_environment({"BESTSELLER__LLM__WRITER__API_BASE": base})
