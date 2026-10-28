# ruff: noqa: RUF001
"""Explicit synthetic outputs; these confer no grade/adoption/closure authority."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from e2e_sim.transport import TAPE_SCHEMA, Tape

SCORE_FIELDS = (
    "overall",
    "goal",
    "coverage",
    "coherence",
    "continuity",
    "main_plot_progression",
    "subplot_progression",
    "style",
    "hook",
    "ending_hook_effectiveness",
    "volume_mission_alignment",
    "pacing_rhythm",
    "character_voice_distinction",
    "thematic_resonance",
    "contract_alignment",
    "duplication_score",
)


def endpoints_from_environment(env):
    endpoints = {}
    for role in ("PLANNER", "WRITER", "CRITIC", "SUMMARIZER", "EDITOR"):
        raw = env.get("BESTSELLER__LLM__" + role + "__API_BASE")
        if raw is None:
            continue
        try:
            base = json.loads(raw)
        except (ValueError, TypeError):
            base = raw
        if not isinstance(base, str) or not base:
            raise ValueError("harness_route_base_required")
        url = urlsplit(base)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or "*" in url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("harness_route_base_unsafe")
        endpoint = {
            "scheme": url.scheme,
            "host": url.hostname,
            "port": url.port or (443 if url.scheme == "https" else 80),
            "path": url.path.rstrip("/") + "/chat/completions",
        }
        endpoints[json.dumps(endpoint, sort_keys=True)] = endpoint
    return list(endpoints.values()) or [
        {"scheme": "https", "host": "par-p3.invalid", "port": 443, "path": "/v1/chat/completions"}
    ]


def private_fixture_body():
    import hashlib
    raw_path = os.environ.get("P3_SYNTHETIC_BODY_PATH")
    if not raw_path:
        raise ValueError("SCRIPT_FIXTURE_PRIVATE_PATH_REQUIRED")
    fixture = Path(raw_path)
    if not fixture.is_absolute() or fixture.is_symlink() or fixture.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("SCRIPT_FIXTURE_MUST_STAY_OUTSIDE_REPOSITORY")
    raw = fixture.read_bytes()
    if hashlib.sha256(raw).hexdigest() != "3f0b6c78ce9bdee1c9cabfeed5f451fa93442e53e015457bb0121f27571a822c":
        raise ValueError("SCRIPT_FIXTURE_SHA_MISMATCH")
    return raw.decode("utf-8")


def document(scenario="chapter_adoption"):
    body = private_fixture_body()
    submission = {
        "verdict": "pass",
        "scores": dict.fromkeys(SCORE_FIELDS, 0.88),
        "rewrite_direction": "",
        "commentary": "修船付出代价，旧船票让父子关系显现。",
        "citations": [
            {"paragraph_number": 1, "dimension": "opening", "reason": "漏水与救治形成压力"},
            {"paragraph_number": 7, "dimension": "cost", "reason": "拆柜付出具体代价"},
            {"paragraph_number": 24, "dimension": "hook", "reason": "对岸关门留下行动障碍"},
        ],
    }
    entries = []

    def entry(name, template, spec, index=1):
        entries.append(
            {
                "id": name + ":" + template + ":" + str(index),
                "match": {
                    "project_id": "fa14e681-2bd9-44f5-93d1-7f2ef9900d69",
                    "prompt_template": template,
                    "call_index": index,
                },
                "responses": [spec],
            }
        )

    def response(content="", **kw):
        return {
            "mode": "auto",
            "content": content,
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
            **kw,
        }

    for name in (scenario,):
        entry(name, "chapter_first_writer", response(body))
        critic = response(
            "",
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "type": "function",
                    "function": {
                        "name": "submit_chapter_critique",
                        "arguments": json.dumps(submission, ensure_ascii=False),
                    },
                }
            ],
        )
        if name == "review_length":
            # The actual malformed physical return is never repaired to a pass.
            critic = response("VERDICT: pass\nSCORES: {", finish_reason="length")
        entry(name, "chapter_review", critic)
        # Explicit empty, typed model outputs are still validated by native KG code.
        entry(
            name, "chapter_feedback_extraction", response(json.dumps({"facts": [], "events": []}))
        )
        entry(
            name,
            "chapter_state_snapshot",
            response(json.dumps({"characters": [], "facts": [], "open_threads": []})),
        )
    value = {
        "schema": TAPE_SCHEMA,
        "simulation_only": True,
        "allowed_endpoints": endpoints_from_environment(os.environ),
        "entries": entries,
    }
    Tape(value)  # Validate before any infrastructure or service starts.
    return value


def write_tape(path, *, scenario="chapter_adoption"):
    Path(path).write_text(json.dumps(document(scenario), ensure_ascii=False, indent=2) + "\n")
