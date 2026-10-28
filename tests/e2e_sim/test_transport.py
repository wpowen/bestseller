"""Standalone tests: python -m unittest discover -s tests/e2e_sim -v."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

try:
    from .transport import (
        MARKER,
        TAPE_SCHEMA,
        NativeObservation,
        ScriptedTransportError,
        Tape,
        install_scripted_http,
        wire_fingerprints,
    )
except ImportError:
    from transport import (
        MARKER,
        TAPE_SCHEMA,
        NativeObservation,
        ScriptedTransportError,
        Tape,
        install_scripted_http,
        wire_fingerprints,
    )

URL = "https://provider.invalid/v1/chat/completions"
BODY = {"model": "synthetic-model", "messages": [{"role": "user", "content": "SECRET_PROMPT"}]}


def answer(**changes):
    return {
        "content": "SECRET_RESPONSE甲😀",
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        **changes,
    }


def document(body=None, responses=None, match=None):
    return {
        "schema": TAPE_SCHEMA,
        "simulation_only": True,
        "allowed_endpoints": [
            {
                "scheme": "https",
                "host": "provider.invalid",
                "port": 443,
                "path": "/v1/chat/completions",
            }
        ],
        "entries": [
            {
                "id": "SECRET_ENTRY",
                "match": match or wire_fingerprints(body or BODY),
                "responses": responses or [answer()],
            }
        ],
    }


def events(text):
    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


class TransportTests(unittest.TestCase):
    def setUp(self):
        # A real accidental network attempt fails before DNS/socket connection.
        self.dns = patch.object(
            socket, "getaddrinfo", side_effect=AssertionError("network forbidden")
        )
        self.connect = patch.object(
            socket.socket, "connect", side_effect=AssertionError("network forbidden")
        )
        self.dns.start()
        self.connect.start()
        self.addCleanup(self.dns.stop)
        self.addCleanup(self.connect.stop)

    def test_json_real_client_hooks_hash_audit_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            tape = Tape(document(), audit_path=path)
            singles = (httpx.Client._send_single_request, httpx.AsyncClient._send_single_request)
            originals = (
                httpx.HTTPTransport.handle_request,
                httpx.AsyncHTTPTransport.handle_async_request,
            )
            observed = []
            with install_scripted_http(tape):
                with httpx.Client(
                    trust_env=False, event_hooks={"request": [lambda r: observed.append(r.content)]}
                ) as client:
                    response = client.post(
                        URL, json=BODY, headers={"authorization": "Bearer SECRET_KEY"}
                    )
            result = response.json()
            self.assertEqual(result["choices"][0]["message"]["content"], answer()["content"])
            self.assertEqual(result["usage"]["total_tokens"], 5)
            self.assertEqual(result["model"], BODY["model"])
            self.assertTrue(result["id"].startswith("v3-synthetic-"))
            self.assertEqual(response.headers["x-v3-synthetic-transport"], MARKER)
            self.assertEqual(len(observed), 1)
            self.assertEqual(
                singles, (httpx.Client._send_single_request, httpx.AsyncClient._send_single_request)
            )
            self.assertEqual(
                originals,
                (httpx.HTTPTransport.handle_request, httpx.AsyncHTTPTransport.handle_async_request),
            )
            audit = path.read_text()
            self.assertEqual([json.loads(x) for x in audit.splitlines()], tape.events)
            for secret in ("SECRET_PROMPT", "SECRET_RESPONSE", "SECRET_KEY", "SECRET_ENTRY", URL):
                self.assertNotIn(secret, audit)
            self.assertEqual(tape.summary()["consumed"], 1)

    def test_sequence_and_fresh_tool_ids(self):
        spec = answer(
            content=None,
            tool_calls=[{"id": "OLD", "function": {"name": "submit", "arguments": "{}"}}],
        )
        tape = Tape(document(responses=[spec, spec]))
        with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
            values = [client.post(URL, json=BODY).json() for _ in range(2)]
            with self.assertRaisesRegex(ScriptedTransportError, "tape_exhausted"):
                client.post(URL, json=BODY)
        ids = [v["choices"][0]["message"]["tool_calls"][0]["id"] for v in values]
        self.assertNotEqual(*ids)
        self.assertTrue(all(i.startswith("v3-synthetic-") for i in ids))
        self.assertEqual(tape.summary()["consumed"], 2)

    def test_async_sse_unicode_tools_usage_and_finish_reasons(self):
        async def run(finish):
            body = {**BODY, "stream": True}
            spec = answer(
                finish_reason=finish,
                chunk_characters=1,
                tool_calls=[{"function": {"name": "submit", "arguments": '{"好":true}'}}],
            )
            tape = Tape(document(body, [spec]))
            hooks = []

            async def hook(request):
                hooks.append(request.content)

            with install_scripted_http(tape):
                async with httpx.AsyncClient(
                    trust_env=False, event_hooks={"request": [hook]}
                ) as client:
                    response = await client.post(URL, json=body)
            self.assertEqual(len(hooks), 1)
            self.assertTrue(response.text.endswith("data: [DONE]\n\n"))
            values = events(response.text)
            self.assertEqual(
                "".join(
                    e["choices"][0]["delta"].get("content", "") for e in values if e["choices"]
                ),
                spec["content"],
            )
            calls = [
                c
                for e in values
                if e["choices"]
                for c in e["choices"][0]["delta"].get("tool_calls", [])
            ]
            self.assertEqual("".join(c["function"].get("name", "") for c in calls), "submit")
            self.assertEqual("".join(c["function"]["arguments"] for c in calls), '{"好":true}')
            self.assertEqual(values[-2]["choices"][0]["finish_reason"], finish)
            self.assertEqual(values[-1]["usage"]["total_tokens"], 5)

        for finish in ("stop", "length", "content_filter", "tool_calls"):
            with self.subTest(finish=finish):
                asyncio.run(run(finish))

    def test_real_openai_sdk_json_and_stream_normalization(self):
        from openai import OpenAI

        for streaming in (False, True):
            body = {**BODY, "stream": streaming}
            tape = Tape(
                document(
                    body,
                    match={
                        "messages_tools_sha256": wire_fingerprints(body)["messages_tools_sha256"]
                    },
                )
            )
            with install_scripted_http(tape):
                with OpenAI(
                    api_key="offline-only",
                    base_url="https://provider.invalid/v1",
                    max_retries=0,
                    http_client=httpx.Client(trust_env=False),
                ) as client:
                    result = client.chat.completions.create(**body)
                    if streaming:
                        self.assertEqual(
                            "".join(c.choices[0].delta.content or "" for c in result if c.choices),
                            answer()["content"],
                        )
                    else:
                        self.assertEqual(result.choices[0].message.content, answer()["content"])

    def test_timeout_and_partial_disconnect_have_no_done_or_ledger_mutation(self):
        tape = Tape(document(responses=[{"mode": "timeout"}]))
        with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
            with self.assertRaises(httpx.ReadTimeout):
                client.post(URL, json=BODY)
        self.assertEqual(tape.summary()["consumed"], 1)
        body = {**BODY, "stream": True}
        tape = Tape(
            document(
                body, [answer(mode="disconnect", disconnect_after_chunks=2, chunk_characters=1)]
            )
        )

        async def run():
            chunks = []
            with install_scripted_http(tape):
                async with httpx.AsyncClient(trust_env=False) as client:
                    with self.assertRaises(httpx.ReadError):
                        async with client.stream("POST", URL, json=body) as response:
                            async for chunk in response.aiter_bytes():
                                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(run())
        self.assertEqual(len(chunks), 2)
        self.assertNotIn(b"[DONE]", b"".join(chunks))
        self.assertNotIn("settled", json.dumps(tape.events))

    def test_unmapped_ambiguous_wrong_endpoint_and_bad_json_never_consume(self):
        tape = Tape(document())
        urls = [
            "https://elsewhere.invalid/v1/chat/completions",
            URL + "?key=SECRET_KEY",
            "https://provider.invalid/v1/other",
            "http://provider.invalid/v1/chat/completions",
            "https://provider.invalid:444/v1/chat/completions",
            "https://name:SECRET_KEY@provider.invalid/v1/chat/completions",
        ]
        with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
            for url in urls:
                with (
                    self.subTest(url=url),
                    self.assertRaisesRegex(ScriptedTransportError, "unsupported_endpoint"),
                ):
                    client.post(url, json=BODY)
            for raw in (b'{"messages":[],"messages":[]}', b"NaN", b"{broken SECRET_PROMPT", b"[]"):
                with self.assertRaisesRegex(ScriptedTransportError, "unsupported_request_json"):
                    client.post(URL, content=raw)
            with self.assertRaisesRegex(ScriptedTransportError, "no_unique_entry"):
                client.post(URL, json={**BODY, "model": "changed"})
        doc = document()
        doc["entries"].append({**deepcopy(doc["entries"][0]), "id": "other"})
        other = Tape(doc)
        with install_scripted_http(other), httpx.Client(trust_env=False) as client:
            with self.assertRaisesRegex(ScriptedTransportError, "no_unique_entry"):
                client.post(URL, json=BODY)
        self.assertEqual(tape.summary()["consumed"] + other.summary()["consumed"], 0)
        self.assertNotIn("SECRET", json.dumps(tape.events))

    def test_template_call_index_is_observed_not_guessed_from_wire_or_cursor(self):
        match = {"prompt_template": "scene_writer", "call_index": 7}
        tape = Tape(document(match=match))
        with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
            with self.assertRaisesRegex(ScriptedTransportError, "no_unique_entry"):
                client.post(URL, json={**BODY, "prompt_template": "scene_writer", "call_index": 7})
        observed = []

        def observer(request, native_call):
            observed.append(native_call)
            return NativeObservation(prompt_template="scene_writer", call_index=7)

        tape = Tape(document(match=match), template_observer=observer)
        with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
            client.post(URL, json=BODY)
            with self.assertRaisesRegex(ScriptedTransportError, "tape_exhausted"):
                client.post(URL, json=BODY)
        self.assertEqual(observed, [None, None])
        self.assertEqual(tape.events[0]["call_index"], 7)

    def test_async_read_only_template_observer_and_failure_redaction(self):
        async def observer(request, call):
            return NativeObservation("scene_writer", 2)

        tape = Tape(
            document(match={"prompt_template": "scene_writer", "call_index": 2}),
            template_observer=observer,
        )

        async def run():
            with install_scripted_http(tape):
                async with httpx.AsyncClient(trust_env=False) as client:
                    await client.post(URL, json=BODY)

        asyncio.run(run())

        def broken(request, call):
            raise ValueError("SECRET_PROMPT SECRET_KEY")

        tape = Tape(document(), template_observer=broken)
        with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
            with self.assertRaisesRegex(ScriptedTransportError, "observer_failed") as caught:
                client.post(URL, json=BODY)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(tape.summary()["consumed"], 0)

    def test_native_counter_read_only_guard_cannot_be_overridden(self):
        class ReadOnlyContext:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        for count in (None, [0], [2], [True], [], [1]):
            with self.subTest(count=count):
                call = SimpleNamespace(
                    attempts=[{"effective_input_hash": "a" * 64, "attempt_id": "c" * 32}],
                    claim=SimpleNamespace(key="b" * 64),
                )
                module = SimpleNamespace(
                    _execution_call_var=ReadOnlyContext(call),
                    _transport_send_count=ReadOnlyContext(count),
                )
                before = deepcopy(call.__dict__)
                tape = Tape(
                    document(), template_observer=lambda *_: NativeObservation("scene_writer", 1)
                )
                with patch.dict(sys.modules, {"bestseller.services.llm": module}):
                    with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
                        if count == [1] and type(count[0]) is int:
                            client.post(URL, json=BODY)
                            self.assertEqual(tape.events[0]["native_attempt_id"], "c" * 32)
                        else:
                            with self.assertRaisesRegex(
                                ScriptedTransportError, "native_reserved_send_not_observed"
                            ):
                                client.post(URL, json=BODY)
                self.assertEqual(call.__dict__, before)
                self.assertEqual(module._transport_send_count.get(), count)
        module._execution_call_var = ReadOnlyContext(None)
        module._transport_send_count = ReadOnlyContext([0])
        with patch.dict(sys.modules, {"bestseller.services.llm": module}):
            with install_scripted_http(Tape(document())), httpx.Client(trust_env=False) as client:
                with self.assertRaisesRegex(
                    ScriptedTransportError, "native_reserved_send_not_observed"
                ):
                    client.post(URL, json=BODY)

    def test_request_hook_failure_is_preserved_before_transport(self):
        tape = Tape(document())

        def guard(request):
            raise RuntimeError("native guard rejection")

        with (
            install_scripted_http(tape),
            httpx.Client(trust_env=False, event_hooks={"request": [guard]}) as client,
        ):
            with self.assertRaisesRegex(RuntimeError, "native guard rejection"):
                client.post(URL, json=BODY)
        self.assertEqual(tape.events, [])

    def test_validation_rejects_fallback_fixtures_wildcards_bad_indices_and_responses(self):
        mutations = [
            lambda d: d.update(allow_fallback=True),
            lambda d: d["entries"][0].update(fallback=True),
            lambda d: d["entries"][0]["responses"][0].update(fixture_key="private"),
            lambda d: d["allowed_endpoints"][0].update(host="*"),
            lambda d: d["allowed_endpoints"][0].pop("port"),
            lambda d: d["entries"][0].update(match={"prompt_template": "scene_writer"}),
            lambda d: d["entries"][0].update(
                match={"prompt_template": "scene_writer", "call_index": 0}
            ),
            lambda d: d["entries"][0].update(
                match={"prompt_template": "scene_writer", "call_index": True}
            ),
            lambda d: d["entries"][0]["responses"][0].update(
                usage={"prompt_tokens": True, "completion_tokens": 1}
            ),
            lambda d: d["entries"][0]["responses"][0].update(chunk_characters=0),
            lambda d: d["entries"][0]["responses"][0].update(finish_reason="made_up"),
        ]
        for mutate in mutations:
            doc = document()
            mutate(doc)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                Tape(doc)

    def test_file_loader_duplicate_keys_and_stream_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tape.json"
            path.write_text(json.dumps(document()))
            tape = Tape.from_file(path)
            self.assertEqual(tape.summary()["consumed"], 0)
            path.write_text('{"schema":1,"schema":2}')
            with self.assertRaises(ValueError):
                Tape.from_file(path)
        with (
            install_scripted_http(Tape(document(responses=[answer(mode="sse")]))),
            httpx.Client(trust_env=False) as client,
        ):
            with self.assertRaisesRegex(ScriptedTransportError, "stream_contract_mismatch"):
                client.post(URL, json=BODY)

    def test_project_uuid_selectors_isolate_template_ordinals_and_audit(self):
        project_ids = [
            str(uuid5(NAMESPACE_URL, "bestseller:e2e_sim:" + name))
            for name in ("chapter_adoption", "review_length")
        ]
        workflow = str(uuid5(NAMESPACE_URL, "offline-workflow"))
        doc = document(
            match={
                "project_id": project_ids[0],
                "prompt_template": "chapter_review",
                "call_index": 1,
            }
        )
        doc["entries"].append(
            {
                "id": "length",
                "match": {
                    "project_id": project_ids[1],
                    "prompt_template": "chapter_review",
                    "call_index": 1,
                },
                "responses": [answer(finish_reason="length")],
            }
        )
        state = iter(project_ids)
        tape = Tape(
            doc,
            template_observer=lambda *_: NativeObservation(
                "chapter_review", 1, next(state), workflow
            ),
        )
        with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
            values = [client.post(URL, json=BODY).json() for _ in project_ids]
        self.assertEqual([v["choices"][0]["finish_reason"] for v in values], ["stop", "length"])
        self.assertEqual([e["project_id"] for e in tape.events], project_ids)
        self.assertTrue(all(e["workflow_run_id"] == workflow for e in tape.events))

    def test_observer_transport_error_is_redacted_and_async_requires_async_client(self):
        def observer(*_):
            raise ScriptedTransportError("SECRET_KEY SECRET_PROMPT")

        tape = Tape(document(), template_observer=observer)
        with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
            with self.assertRaisesRegex(ScriptedTransportError, "observer_failed") as caught:
                client.post(URL, json=BODY)
        self.assertNotIn("SECRET", str(caught.exception))

        async def async_observer(*_):
            return NativeObservation("scene_writer", 1)

        tape = Tape(document(), template_observer=async_observer)
        with install_scripted_http(tape), httpx.Client(trust_env=False) as client:
            with self.assertRaisesRegex(
                ScriptedTransportError, "async_observer_requires_async_transport"
            ):
                client.post(URL, json=BODY)
        self.assertEqual(tape.summary()["consumed"], 0)

    def test_concrete_native_observer_reads_parent_tasks_without_ledger_or_context_writes(self):
        import dataclasses
        import types

        try:
            from .transport import NativeTemplateObserver
        except ImportError:
            from transport import NativeTemplateObserver

        @dataclasses.dataclass
        class Request:
            prompt_template: str
            project_id: UUID
            workflow_run_id: UUID

        module = types.ModuleType("bestseller.services.llm")
        module.LLMCompletionRequest = Request
        # A native-shaped fixture frame, NOT a real guard/ledger integration.
        exec(
            "async def _call_litellm_once(request, execution_call, ready, release):\n"
            "    ready.set()\n"
            "    await release.wait()\n",
            module.__dict__,
        )
        project = uuid5(NAMESPACE_URL, "bestseller:e2e_sim:chapter_adoption")
        other = uuid5(NAMESPACE_URL, "bestseller:e2e_sim:review_length")
        workflow = uuid5(NAMESPACE_URL, "workflow")
        request = Request("chapter_review", project, workflow)

        async def run():
            observer = NativeTemplateObserver()
            call = object()  # identity only; no fake ledger or native counter
            ready, release = asyncio.Event(), asyncio.Event()
            task = asyncio.create_task(module._call_litellm_once(request, call, ready, release))
            await ready.wait()
            try:
                first = observer(httpx.Request("POST", URL), call)
                second = observer(httpx.Request("POST", URL), call)
                self.assertEqual(
                    (
                        first.prompt_template,
                        first.call_index,
                        first.project_id,
                        first.workflow_run_id,
                    ),
                    ("chapter_review", 1, str(project), str(workflow)),
                )
                self.assertEqual(second.call_index, 2)
                # A different active project starts its own template ordinal.
                request.project_id = other
                self.assertEqual(observer(httpx.Request("POST", URL), call).call_index, 1)
                with self.assertRaises(ValueError):
                    observer(httpx.Request("POST", URL), object())
                return observer, call
            finally:
                release.set()
                await task

        with patch.dict(sys.modules, {"bestseller.services.llm": module}):
            observer, call = asyncio.run(run())
            with self.assertRaises(ValueError):
                observer(httpx.Request("POST", URL), call)

    def test_concrete_native_observer_rejects_ambiguous_parent_identities(self):
        import types

        try:
            from .transport import NativeTemplateObserver
        except ImportError:
            from transport import NativeTemplateObserver

        class Request:
            def __init__(self, template):
                self.prompt_template = template
                self.project_id = uuid5(NAMESPACE_URL, "project")
                self.workflow_run_id = None

        module = types.ModuleType("bestseller.services.llm")
        module.LLMCompletionRequest = Request
        exec(
            "async def native(request, execution_call, ready, release):\n"
            "    ready.set()\n"
            "    await release.wait()\n",
            module.__dict__,
        )

        async def run():
            call = object()
            release = asyncio.Event()
            ready = [asyncio.Event(), asyncio.Event()]
            tasks = [
                asyncio.create_task(module.native(Request(template), call, event, release))
                for template, event in zip(("scene_writer", "chapter_review"), ready, strict=True)
            ]
            await asyncio.gather(*(event.wait() for event in ready))
            try:
                with self.assertRaisesRegex(ValueError, "native_template_not_unique"):
                    NativeTemplateObserver()(httpx.Request("POST", URL), call)
            finally:
                release.set()
                await asyncio.gather(*tasks)

        with patch.dict(sys.modules, {"bestseller.services.llm": module}):
            asyncio.run(run())

    def test_install_overlap_idempotence_exception_restore_and_ownership(self):
        original = httpx.HTTPTransport.handle_request
        handle = install_scripted_http(Tape(document()))
        with self.assertRaisesRegex(RuntimeError, "already_installed"):
            install_scripted_http(Tape(document()))
        patched = httpx.HTTPTransport.handle_request

        def replacement(*_):
            return None

        httpx.HTTPTransport.handle_request = replacement
        try:
            with self.assertRaisesRegex(RuntimeError, "ownership_changed"):
                handle.uninstall()
            self.assertIs(httpx.HTTPTransport.handle_request, replacement)
        finally:
            httpx.HTTPTransport.handle_request = patched
            handle.uninstall()
        handle()
        self.assertIs(httpx.HTTPTransport.handle_request, original)
        with self.assertRaisesRegex(RuntimeError, "body failure"):
            with install_scripted_http(Tape(document())):
                raise RuntimeError("body failure")
        self.assertIs(httpx.HTTPTransport.handle_request, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
