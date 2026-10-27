"""Offline exact-language and frozen-default checks; no report/DB/provider fixtures."""

from copy import deepcopy
import hashlib
from itertools import product
import json
import re
import shutil
import subprocess

import pytest

from bestseller.services.commercial_review_tool import (
    _compact_exact_string_enums,
    _exact_string_enum_pattern,
    review_submission_tools,
)
from bestseller.services.commercial_source_evidence import CommercialSources


def _serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _ocean_catalog():
    # An unrelated book: neither runtime chapter IDs nor production report data.
    return {
        "BODY:unrelated-ocean:港口": {"kind": "current", "text": "潜水员归还浮标。"},
        "PRIOR:unrelated-ocean:码头": {"kind": "prior", "text": "潜水员昨夜离港。"},
        "OBS:unrelated-ocean:潮汐": {"kind": "observation", "text": "退潮后才露出通道。"},
        "RULE:unrelated-ocean:视角": {"kind": "constraint", "text": "使用第一人称。"},
    }


def _sources(catalog):
    return CommercialSources(text="", catalog=catalog, receipt={})


@pytest.mark.parametrize(
    ("catalog", "digest"),
    [
        ({}, "a285b539c7fb4eaf9f47358aa0c8cd3a3a2770763de97d834f145b531bb79538"),
        (_ocean_catalog(), "ffc01720fe156f4ae1d105bc0ab32281367079dc617cd794a0f89d51cda793bb"),
    ],
)
def test_default_bytes_match_frozen_pre_compaction_constructor(catalog, digest):
    # Frozen from the actual pre-edit function, not calculated from compact=False.
    original = deepcopy(catalog)
    tools = review_submission_tools(_sources(catalog))
    assert hashlib.sha256(_serialized(tools)).hexdigest() == digest
    assert _serialized(review_submission_tools(_sources(catalog), compact=False)) == _serialized(
        tools
    )
    assert catalog == original


def _assert_only_exact_enum_replacements(before, after):
    if isinstance(before, list):
        assert isinstance(after, list) and len(before) == len(after)
        return sum(
            _assert_only_exact_enum_replacements(a, b) for a, b in zip(before, after, strict=True)
        )
    if not isinstance(before, dict):
        assert before == after
        return 0
    replaced = "enum" in before and "enum" not in after
    if replaced:
        assert before["type"] == after["type"] == "string"
        assert all(type(value) is str for value in before["enum"])
        assert "pattern" not in before
        assert len(_serialized(after)) < len(_serialized(before))
        assert all(re.search(after["pattern"], value) for value in before["enum"])
    assert [key for key in before if not (replaced and key == "enum")] == [
        key for key in after if not (replaced and key == "pattern")
    ]
    return int(replaced) + sum(
        _assert_only_exact_enum_replacements(value, after[key])
        for key, value in before.items()
        if not (replaced and key == "enum")
    )


@pytest.fixture
def large_sources():
    catalog = {}
    for kind, prefix, count in (
        ("current", "BODY", 256),
        ("prior", "PRIOR", 40),
        ("observation", "OBS", 12),
        ("constraint", "RULE", 8),
    ):
        for index in range(count):
            catalog[f"{prefix}:ocean-book:bodyhash-4c1af023:span-{index:04d}"] = {
                "kind": kind,
                "text": f"海洋考察的独立来源 {index}",
            }
    return _sources(catalog)


def test_complete_schema_shrinks_without_changing_other_fields_or_order(large_sources):
    original = deepcopy(large_sources.catalog)
    before = review_submission_tools(large_sources)
    after = review_submission_tools(large_sources, compact=True)
    assert _assert_only_exact_enum_replacements(before, after) > 0
    assert len(_serialized(after)) < len(_serialized(before)) // 2
    assert large_sources.catalog == original
    assert _serialized(review_submission_tools(large_sources)) == _serialized(before)
    assert _serialized(review_submission_tools(large_sources, compact=True)) == _serialized(after)


@pytest.mark.parametrize(
    "values",
    [
        [""],
        ["", "a", "ab", "aba", "b", "b\n"],
        ["a", "a.", "a*", "a$", "a[", "a\\", "a|"],
        ["id\n", "id\r", "id\r\n", "id\x00", "id\t", "id\u2028", "id\u2029"],
        ["中文", "中文书", "e\u0301", "é", "😀", "😀a", "\uffff", "\U0010ffff"],
        ['quotes:"', "quotes:'", "slash:/", "metas:^$\\.*+?()[]{}|/- #&~!"],
        ["same", "same", "same-prefix", "same-prefix-longer"],
    ],
)
def test_finite_trie_language_matches_enumeration_and_mutations(values):
    pattern = _exact_string_enum_pattern(values)
    assert pattern.startswith("^(?:") and pattern.endswith(r")(?![\s\S])")
    candidates = set(values)
    for length in range(5):
        candidates.update("".join(chars) for chars in product("ab.\n", repeat=length))
    for value in values:
        candidates.update(_mutations(value))
    for candidate in candidates:
        assert bool(re.search(pattern, candidate)) == (candidate in values), repr(candidate)


def _mutations(value):
    yield "foreign:" + value
    yield value + "foreign"
    for suffix in ("\n", "\r", "\r\n", "\u2028", "\u2029", "\x00", '"', "😀", "\ud800", "\udfff"):
        yield value + suffix
    for index in range(len(value)):
        yield value[:index] + value[index + 1 :]
        yield value[:index] + "?" + value[index + 1 :]


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "enum": []},
        {"type": "string", "enum": [1, "long-shared-prefix-a", "long-shared-prefix-b"]},
        {"type": "string", "enum": [None, True, {"x": "y"}]},
        {"type": "integer", "enum": [1, 2]},
        {"enum": ["long-shared-prefix-a", "long-shared-prefix-b"]},
        {"type": "string", "enum": ["a", "b"]},
        {"type": "string", "enum": ["a", "ab"], "pattern": "a"},
        {"type": "string", "enum": ["x\ud800", "x\udfff", "x\ud83d\ude00", "x😀"]},
    ],
)
def test_ineligible_or_unprofitable_enums_remain_exactly_unchanged(schema):
    before = deepcopy(schema)
    assert _compact_exact_string_enums(schema) == schema == before


def test_long_ids_do_not_depend_on_recursion_limit():
    prefix = "long-id:" + "x" * 5000
    values = [prefix + "a", prefix + "b", prefix]
    schema = {"type": "string", "enum": values, "minLength": 2, "description": "retain me"}
    after = _compact_exact_string_enums(schema)
    # A very long finite language stays an enum to avoid regex backend limits.
    assert after == schema
    assert "pattern" not in after
    assert all(value in after["enum"] for value in values)
    assert prefix + "c" not in after["enum"]
    assert prefix + "a\n" not in after["enum"]


def test_profitable_unicode_empty_and_literal_enums_retain_other_constraints():
    prefix = '中文😀:"^$\\.*+?()[]{}|/-\n\x00'
    values = ["", prefix, *[prefix + str(index) for index in range(32)]]
    schema = {"type": "string", "enum": values, "maxLength": 200, "description": "same"}
    after = _compact_exact_string_enums(schema)
    assert _assert_only_exact_enum_replacements(schema, after) == 1
    for value in values:
        for candidate in [value, *_mutations(value)]:
            assert bool(re.search(after["pattern"], candidate)) == (candidate in values)


def test_full_submission_equivalence_and_invalid_late_ids(large_sources):
    jsonschema = pytest.importorskip("jsonschema")
    schemas = [
        review_submission_tools(large_sources, compact=compact)[0]["function"]["parameters"]
        for compact in (False, True)
    ]
    validators = []
    for schema in schemas:
        jsonschema.Draft202012Validator.check_schema(schema)
        validators.append(jsonschema.Draft202012Validator(schema))
    ids = {
        kind: [key for key, row in large_sources.catalog.items() if row["kind"] == kind]
        for kind in ("current", "prior", "observation", "constraint")
    }
    props = schemas[0]["properties"]
    payload = {
        "wire_version": props["wire_version"]["enum"][0],
        "pass": False,
        "overall_score": 0.5,
        "dimension_scores": dict.fromkeys(props["dimension_scores"]["required"], 0.5),
        "issues": [],
        "rewrite_plan": {"scope": "scene", "preserve": [], "change": [], "instructions": "修改"},
        "checks": {
            key: {"result": "PASS", "body_id": ids["current"][-1], "reason": "已有证据"}
            for key in ids["constraint"]
        },
    }
    for kind, origin in (
        ("narrative_quality", None),
        ("current_fact_conflict", ids["current"][-1]),
        ("prior_fact_conflict", ids["prior"][-1]),
        ("prior_fact_conflict", ids["observation"][-1]),
        ("constraint_violation", ids["constraint"][-1]),
    ):
        issue = {
            "code": "late-source",
            "severity": "high",
            "assessment_kind": kind,
            "body_id": ids["current"][-1],
            "reason": "潮汐与出港时序冲突",
            "required_fix": "核对海洋考察来源",
        }
        if origin is not None:
            issue["source_id"] = origin
        if kind in {"current_fact_conflict", "prior_fact_conflict"}:
            issue["temporal_relation"] = "unexplained_transition"
        payload["issues"].append(issue)
    assert all(validator.is_valid(payload) for validator in validators)

    invalid = []
    for index, issue in enumerate(payload["issues"]):
        for field in ("body_id", "source_id"):
            if field not in issue:
                continue
            value = issue[field]
            for bad in [
                *_mutations(value),
                value.replace("bodyhash-4c1af023", "bodyhash-foreign"),
                value.rsplit("-", 1)[0] + "-9999",
                ids["constraint"][0] if field == "body_id" else 'foreign:".*',
            ]:
                if bad in ids["current"] and field == "body_id":
                    continue
                changed = deepcopy(payload)
                changed["issues"][index][field] = bad
                invalid.append(changed)
    for field in schemas[0]["required"]:
        changed = deepcopy(payload)
        del changed[field]
        invalid.append(changed)
    for action in ("rewrite_chapter", "adopt_draft", "execute_tool"):
        changed = deepcopy(payload)
        changed[action] = True
        invalid.append(changed)
    changed = deepcopy(payload)
    changed["checks"][ids["constraint"][-1]]["body_id"] += "\n"
    invalid.append(changed)
    changed = deepcopy(payload)
    del changed["checks"][ids["constraint"][-1]]
    invalid.append(changed)
    for changed in invalid:
        assert [validator.is_valid(changed) for validator in validators] == [False, False]


def test_patterns_are_exact_in_ecmascript_with_and_without_unicode_flag():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable; exhaustive Python language proof still runs")
    values = ["", "a", "ab", "a\n", "中文😀", "\U0010ffff", 'literal:"^$\\.*+?()[]{}|/- #&~!']
    candidates = sorted(set(values + [bad for value in values for bad in _mutations(value)]))
    data = json.dumps(
        {"pattern": _exact_string_enum_pattern(values), "values": values, "candidates": candidates}
    )
    script = """
const fs = require('fs');
const {pattern, values, candidates} = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const flags of ['', 'u']) {
  const regex = new RegExp(pattern, flags);
  for (const value of candidates) {
    if (regex.test(value) !== values.includes(value)) throw Error(JSON.stringify({flags, value}));
  }
}
"""
    result = subprocess.run(
        [node, "-e", script], input=data, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("keyword", ["const", "default", "examples", "enum", "x-authored"])
def test_data_keywords_never_reinterpret_schema_looking_objects(keyword):
    from bestseller.services.commercial_review_tool import _compact_exact_string_enums
    value = {"type": "string", "enum": ["long-observatory-prefix-A", "long-observatory-prefix-B"]}
    payload = [value] if keyword in {"examples", "enum"} else value
    schema = {"type": "object", keyword: payload,
              "properties": {"actual": value}}
    result = _compact_exact_string_enums(schema)
    assert result[keyword] == schema[keyword]
    assert "pattern" in result["properties"]["actual"]
    assert schema[keyword] == payload


@pytest.mark.parametrize("values", [["x" * n for n in range(1, 501)],
                                     ["D" * 50000 + "1", "D" * 50000 + "2"]])
def test_complex_enum_keeps_exact_original_without_creating_unusable_regex(values):
    from bestseller.services.commercial_review_tool import _compact_exact_string_enums
    schema = {"type": "string", "enum": values}
    assert _compact_exact_string_enums(schema) == schema
