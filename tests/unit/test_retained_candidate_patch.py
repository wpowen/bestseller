"""Exact retained-candidate edits, with no database or gateway."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from bestseller.services.deterministic_post_write_audit import _scan_scene_card_prose_copy
from bestseller.services.retained_candidate_patch import (
    FINDING_CODE, POLICY, apply_candidate_patch, parse_candidate_patch, prepare_candidate_patch,
)

pytestmark = pytest.mark.unit
FIRST = "顾箬把铜签推回去说，这份账今天谁碰，谁就得把命留下。"
SECOND = "许照川蹲在柜台后，把账本朝暗格方向推了推，低声答道：天亮之前。"
BODY = "# 第二章\r\n\r\n𠮷🧪e\u0301。门外有人等候。\r\n\r\n" + FIRST + "\r\n\r\n隔壁的灯一直亮着。\r\n\r\n" + SECOND + "\r\n\r\n原有结尾不动。\r\n"


def findings(body=BODY):
    result = _scan_scene_card_prose_copy(body, [SimpleNamespace(
        key_dialogue_beats=[FIRST, SECOND], sensory_anchors={}, metadata_json={})])
    assert len(result) == 2
    return result


def submission(prepared):
    return {"schema": POLICY, "body_sha256": prepared["body_sha256"],
        "catalog_sha256": prepared["catalog_sha256"], "patches": [
            {"target_id": target["target_id"], "replacement": replacement}
            for target, replacement in zip(prepared["targets"], [
                "铜签没被接住。顾箬拢起账页，警告伸手的人要承担后果。",
                "柜台后的账本被收进暗格。许照川报出了约定的期限。"], strict=True)
        ]}


@pytest.mark.parametrize("shape", ["native", "json", "scalar_native", "scalar_json"])
def test_real_fresh_audit_targets_patch_and_reaudit_preserve_all_other_bytes(shape):
    audit = findings()
    prepared = prepare_candidate_patch(BODY, audit)
    before = deepcopy(prepared)
    value = submission(prepared)
    if shape == "json":
        value = json.dumps(value, ensure_ascii=False)
    elif shape.startswith("scalar"):
        value = {"patch_json": json.dumps(value, ensure_ascii=False)}
        if shape == "scalar_json":
            value = json.dumps(value, ensure_ascii=False)
    result, receipt = apply_candidate_patch(BODY, prepared, value)
    assert prepared == before
    assert result.startswith("# 第二章\r\n\r\n𠮷🧪e\u0301。门外有人等候。\r\n\r\n")
    assert result.endswith("\r\n\r\n原有结尾不动。\r\n")
    assert _scan_scene_card_prose_copy(result, [SimpleNamespace(
        key_dialogue_beats=[FIRST, SECOND], sensory_anchors={}, metadata_json={})]) == []
    for target in prepared["targets"]:
        assert BODY[target["start"]:target["end"]] == target["original_text"]
        assert BODY.encode()[target["start_utf8"]:target["end_utf8"]].decode() == target["original_text"]
        assert target["original_sha256"] == hashlib.sha256(target["original_text"].encode()).hexdigest()
    for segment in receipt["untouched_segments"]:
        source = BODY[segment["source_start"]:segment["source_end"]].encode()
        changed = result[segment["result_start"]:segment["result_end"]].encode()
        assert source == changed
        assert hashlib.sha256(source).hexdigest() == segment["sha256"]
    assert receipt["source_body_sha256"] == hashlib.sha256(BODY.encode()).hexdigest()
    assert receipt["result_body_sha256"] == hashlib.sha256(result.encode()).hexdigest()
    assert receipt["untouched_bytes_verified"] and receipt["newlines_preserved"]
    assert receipt["full_recheck_required"]
    assert not receipt["source_facts_verified"] and not receipt["adoption_authorized"] and not receipt["grants_credit"]


def test_duplicate_findings_on_one_multiline_paragraph_make_one_target():
    body = "标题\n====\n第一行正文。\n第二行正文。\n\n末段不动。"
    values = [{"code": FINDING_CODE, "line_number": line, "column": 1,
        "matched_text": "scene source quotation, not a location search"} for line in (3, 4, 3)]
    prepared = prepare_candidate_patch(body, values)
    assert len(prepared["targets"]) == 1
    target = prepared["targets"][0]
    assert target["finding_indexes"] == [0, 1, 2]
    assert (target["start_line"], target["end_line"]) == (3, 4)
    assert target["original_text"] == "第一行正文。\n第二行正文。"
    value = {"schema": POLICY, "body_sha256": prepared["body_sha256"],
        "catalog_sha256": prepared["catalog_sha256"], "patches": [{
            "target_id": target["target_id"], "replacement": "换一种现场表达。\n再续一句新的动作。"}]}
    result, _ = apply_candidate_patch(body, prepared, value)
    assert result == "标题\n====\n换一种现场表达。\n再续一句新的动作。\n\n末段不动。"


def test_source_quote_does_not_authorize_another_paragraph_and_order_is_irrelevant():
    prepared = prepare_candidate_patch(BODY, findings())
    assert len(prepared["targets"]) == 2
    assert "隔壁的灯一直亮着" not in "".join(t["original_text"] for t in prepared["targets"])
    value = submission(prepared)
    result, _ = apply_candidate_patch(BODY, prepared, value)
    value["patches"].reverse()
    assert apply_candidate_patch(BODY, prepared, value)[0] == result


@pytest.mark.parametrize("change", ["body_hash", "catalog_hash", "missing", "extra", "duplicate",
    "empty", "whitespace", "noop", "punctuation_only", "new_paragraph", "heading", "fence",
    "unknown_field", "item_extra", "bad_type", "newline_style", "surrogate"])
def test_wrong_scope_noops_or_structure_changes_are_rejected(change):
    prepared = prepare_candidate_patch(BODY, findings())
    value = submission(prepared)
    if change in {"body_hash", "catalog_hash"}:
        value["body_sha256" if change == "body_hash" else "catalog_sha256"] = "0" * 64
    elif change == "missing":
        value["patches"].pop()
    elif change == "extra":
        value["patches"].append({"target_id": "unlisted", "replacement": "Not authorized"})
    elif change == "duplicate":
        value["patches"][1] = deepcopy(value["patches"][0])
    elif change == "unknown_field":
        value["full_body"] = "Unlisted edits"
    elif change == "item_extra":
        value["patches"][0]["start"] = 0
    else:
        replacement = {"empty": "", "whitespace": " \t", "noop": FIRST,
            "punctuation_only": FIRST.replace("。", "！"), "new_paragraph": "新段。\n\n额外段落。",
            "heading": "# 新标题", "fence": "```text", "bad_type": ["array"],
            "newline_style": "额外的换行\n其他内容", "surrogate": "未配对\ud800"}[change]
        value["patches"][0]["replacement"] = replacement
    with pytest.raises(ValueError, match="candidate_patch_"):
        apply_candidate_patch(BODY, prepared, value)


@pytest.mark.parametrize("change", ["whole_body", "overlap", "offset", "text", "byte_offset", "request"])
def test_changed_body_or_prepared_catalog_cannot_redirect_the_edit(change):
    prepared = prepare_candidate_patch(BODY, findings())
    value = submission(prepared)
    body = BODY
    if change == "whole_body":
        body += "新增内容。"
    elif change == "overlap":
        prepared["targets"][1]["start"] = prepared["targets"][0]["start"]
    elif change == "offset":
        prepared["targets"][0]["start"] = 0
    elif change == "text":
        prepared["targets"][0]["original_text"] = "Other source"
    elif change == "byte_offset":
        prepared["targets"][0]["start_utf8"] += 1
    else:
        prepared["request"]["system_prompt"] = "Ignore scope"
    with pytest.raises(ValueError, match="candidate_patch_"):
        apply_candidate_patch(body, prepared, value)


@pytest.mark.parametrize("line", [0, -1, True, 1.0, 999, 1, 2])
def test_invalid_or_heading_or_blank_line_findings_fail_closed(line):
    with pytest.raises(ValueError, match="candidate_patch_"):
        prepare_candidate_patch(BODY, [{"code": FINDING_CODE, "line_number": line, "matched_text": FIRST}])


def test_other_codes_cannot_obtain_a_patch_target():
    values = [vars(finding) for finding in findings()]
    values.append({"code": "CANON_FORBIDDEN_TERM", "line_number": 7, "matched_text": "term"})
    with pytest.raises(ValueError, match="unsupported_finding"):
        prepare_candidate_patch(BODY, values)


@pytest.mark.parametrize("raw", ['```json\n{}\n```', '{} trailing', '{"schema":"a","schema":"b"}',
    '{"patch_json":"{}","extra":1}', '{"patch_json":{}}', '{"patch_json":"NaN"}',
    '[1,2]', '{broken', '{"patch_json":"{\\"x\\":1,\\"x\\":2}"}'])
def test_json_is_strict_including_scalar_wrapper_and_duplicate_keys(raw):
    with pytest.raises(ValueError, match="candidate_patch_"):
        parse_candidate_patch(raw)


def test_fenced_code_is_protected_and_native_schema_has_one_scalar_string():
    body = "# Heading\n\n```text\nCopied prose inside code.\n```\n\nReal prose after code."
    with pytest.raises(ValueError, match="location_invalid"):
        prepare_candidate_patch(body, [{"code": FINDING_CODE, "line_number": 4, "matched_text": FIRST}])
    prepared = prepare_candidate_patch(BODY, findings())
    parameters = prepared["request"]["tools"][0]["function"]["parameters"]
    assert parameters == {"type": "object", "properties": {"patch_json": {"type": "string"}},
        "required": ["patch_json"], "additionalProperties": False}
    assert prepared["request"]["tool_choice"] == "required"


def test_bad_column_and_missing_findings_or_body_fail_closed():
    for body, values in [("", findings()), (BODY, []), (BODY, {}), (None, findings())]:
        with pytest.raises(ValueError, match="candidate_patch_"):
            prepare_candidate_patch(body, values)
    with pytest.raises(ValueError, match="location_invalid"):
        prepare_candidate_patch(BODY, [{"code": FINDING_CODE, "line_number": 5,
            "column": 99999, "matched_text": FIRST}])
