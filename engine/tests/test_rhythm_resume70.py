"""E4 recovery and effective-call limits with the real engine stage and offline HTTP."""

from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from bookengine.chapter_type_repair import repair_chapter_type
from bookengine.rhythm_contract_repair import apply_rhythm_contracts
from bookengine.longutil import ask as engine_ask
from test_rhythm_completion70 import OfflineClient, CONTRACT, outline, bible, enabled, body_case, repair_case
from test_run_to_finish import client as real_client, good
from tools.runtime_support import QuotaExhausted


@pytest.mark.parametrize("kind", ["情感", "揭秘"])
def test_real_engine_malformed_repair_spends_only_one_http_call(tmp_path, kind):
    source, text, raw = body_case(kind)
    if kind == "情感":
        source["relationship_change"] = CONTRACT
    client = real_client(tmp_path)
    with patch("urllib.request.urlopen", return_value=good("not a JSON candidate")) as transport:
        result = repair_chapter_type(
            client,
            "r2-ch34",
            text,
            raw,
            False,
            {},
            {},
            source,
            lambda *a, **k: pytest.fail("bad candidate cannot be extracted"),
            engine_ask,
        )
    assert transport.call_count == len(client.calls) == 1
    assert client.calls[0]["status"] == "invalid_stage_output"
    assert result[0] == text and result[-1]["status"] == "rolled_back"
    with patch("urllib.request.urlopen", side_effect=AssertionError("no second output")):
        repeated = repair_chapter_type(
            real_client(tmp_path),
            "r2-ch34",
            text,
            raw,
            False,
            {},
            {},
            source,
            lambda *a, **k: pytest.fail("no re-extract"),
            engine_ask,
        )
    assert repeated[-1]["reason"] == "already_attempted"


def test_real_engine_contract_malformed_spends_one_http_call(tmp_path):
    client = real_client(tmp_path)
    with patch("urllib.request.urlopen", return_value=good("not JSON")) as transport:
        result = apply_rhythm_contracts(client, enabled(), outline(), {})
    assert transport.call_count == len(client.calls) == 1
    assert result["rhythm_contract_repair"]["reason"] == "source_model_failed:response_limit_exhausted"


def test_quota_resume_uses_the_same_prose_stage_and_then_reuses_adoption(tmp_path):
    source, text, raw = body_case()
    client = real_client(tmp_path)
    from bookengine.chapter_type_repair import _targets

    response = dict(edits=[dict(row, text=row["original"].replace("端详", "核对")) for row in _targets(text)])
    stages = []

    def extract(*args, **kwargs):
        return dict(raw, type=source["type"]), False, 0

    quota = good()
    quota.value = dict(base_resp=dict(status_code=1008, status_msg="insufficient balance"))
    with patch("urllib.request.urlopen", return_value=quota):
        with pytest.raises(QuotaExhausted):
            repair_chapter_type(client, "r2-ch34", text, raw, False, {}, {}, source, extract, engine_ask)
    pending = json.loads((tmp_path / "rhythm-revisions/type-attempt-034.json").read_text())
    assert pending["status"] == "waiting"
    resumed = real_client(tmp_path)
    with patch("urllib.request.urlopen", return_value=good(json.dumps(response, ensure_ascii=False))) as transport:
        adopted = repair_chapter_type(resumed, "r2-ch34", text, raw, False, {}, {}, source, extract, engine_ask)
    assert transport.call_count == 1 and adopted[-1]["adopted"] is True and adopted[0] != text
    assert len({row["stage"] for row in resumed.calls}) == 1
    with patch("urllib.request.urlopen", side_effect=AssertionError("adopted result must resume offline")):
        restored = repair_chapter_type(
            real_client(tmp_path),
            "r2-ch34",
            text,
            raw,
            False,
            {},
            {},
            source,
            lambda *a, **k: pytest.fail("adoption is already checked"),
            engine_ask,
        )
    assert restored[:3] == adopted[:3] and restored[-1]["reused"] is True


def test_contract_quota_resumes_original_stage(tmp_path):
    client = OfflineClient(tmp_path, [QuotaExhausted("offline quota")])
    with pytest.raises(QuotaExhausted):
        apply_rhythm_contracts(client, enabled(), outline(), {})
    resumed = OfflineClient(tmp_path, [dict(relationship_change=CONTRACT)])
    result = apply_rhythm_contracts(resumed, enabled(), outline(), {}, kind="writer")
    assert result["rhythm_contract_repair"]["writer_ready"] is True
    assert client.calls[0]["stage"] == resumed.calls[0]["stage"]


def test_relationship_declared_edits_must_actually_change_main_scene(tmp_path):
    source, text, raw = body_case("情感")
    source["relationship_change"] = CONTRACT

    def ask(_client, key, instruction, sections, schema, **kwargs):
        rows = dict(sections)["可改主场景段"]
        assert len(rows) == 13
        return dict(
            edits=[
                dict(row, text=row["original"].replace("端详", "查看") if i == 0 else row["original"])
                for i, row in enumerate(rows)
            ]
        )

    result = repair_chapter_type(
        OfflineClient(tmp_path),
        "r2-ch34",
        text,
        raw,
        False,
        {},
        {},
        source,
        lambda *a, **k: (dict(raw, type="情感"), False, 0),
        ask,
    )
    assert result[0] == text and result[-1]["status"] == "rolled_back"
    assert result[-1]["reason"] == "insufficient_main_scene_edits"


def test_legacy_without_design_does_not_invent_contract_or_call_model(tmp_path):
    client = OfflineClient(tmp_path)
    source = outline()
    result = apply_rhythm_contracts(client, {}, source, {}, kind="writer")
    assert not client.calls and result["relationship_change"] == {}
    assert result["rhythm_contract_repair"]["reason"] == "legacy_without_story_design"
    assert result["rhythm_contract_repair"]["writer_ready"] is False
