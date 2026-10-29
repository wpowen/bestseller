"""Pure SDK fixture controls; no app imports, DB, HTTP or native adoption proof."""

import ast
from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def fixture_api():
    path = ROOT / "scripts/full_flow/e05b_native_handoff.py"
    tree = ast.parse(path.read_bytes())
    names = {"encoded", "sha", "summary", "need", "snapshot_payload", "SDKScript"}
    nodes = [
        n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names
    ]
    namespace = {"json": json, "hashlib": hashlib, "Counter": Counter}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    source = ast.parse((ROOT / "src/bestseller/services/continuity.py").read_bytes())
    prompt = next(
        n.value
        for n in source.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_EXTRACTION_SYSTEM_PROMPT" for t in n.targets)
    )
    return namespace, ast.literal_eval(prompt)


def request(system):
    return {
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "第 4 章 《测试输入》\n\n（无 — 这是第一章）\n\n"
                    "=== 本章正文开始 ===\n纯合成 SDK 测试输入。\n=== 本章正文结束 ===\n\n"
                    "请严格按照 system prompt 规定的 JSON 格式输出本章结束时的硬事实状态。"
                ),
            },
        ]
    }


def test_exact_snapshot_prompt_has_explicit_synthetic_payload_without_grade(fixture_api):
    api, system = fixture_api
    assert api["snapshot_payload"](request(system), system) == {
        "facts": [],
        "time_anchor": None,
        "chapter_time_span": None,
    }


@pytest.mark.parametrize("change", ["other_system", "repair_suffix", "other_tool"])
def test_snapshot_does_not_admit_other_or_repair_templates(fixture_api, change):
    api, system = fixture_api
    body = request(system)
    if change == "other_system":
        body["messages"][0]["content"] = "feedback extraction"
    elif change == "repair_suffix":
        body["messages"][1]["content"] += "\nRepair the previous response."
    else:
        body["tools"] = [{"function": {"name": "uncovered_tool"}}]
    assert api["snapshot_payload"](body, system) is None


def test_snapshot_is_one_separate_slot_after_the_five_reviews(fixture_api):
    api, _ = fixture_api
    script = api["SDKScript"]()
    script.calls = [{"family": f} for f in ["commercial"] * 3 + ["window", "primary"]]
    script.check_slot("snapshot")
    assert len(script.calls) == 5  # Admission check does not fabricate a response.
    script.calls.append({"family": "snapshot"})
    with pytest.raises(ValueError, match="sdk_family_limit"):
        script.check_slot("snapshot")


@pytest.mark.parametrize(
    "family,prior",
    [
        ("commercial", ["commercial"] * 3),
        ("snapshot", []),
        ("feedback", ["commercial"] * 3 + ["window", "primary", "snapshot"]),
    ],
)
def test_family_caps_and_snapshot_order_remain_closed(fixture_api, family, prior):
    api, _ = fixture_api
    script = api["SDKScript"]()
    script.calls = [{"family": f} for f in prior]
    with pytest.raises(ValueError, match="e05b:sdk_"):
        script.check_slot(family)
