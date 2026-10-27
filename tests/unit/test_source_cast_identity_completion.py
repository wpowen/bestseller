"""Synthetic Cast proposals: exact identity leaves only, no model/network/DB."""

from copy import deepcopy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services import llm, planner
from bestseller.services import source_cast_field_repair as fields
from bestseller.services import source_cast_identity_completion as identity
from bestseller.services import source_cast_parts as parts
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.source_bound_planning_content import _hash
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def fixture():
    premise = "Mara, a woman chef, must prepare dinner with Nico and Ada."
    content = {
        "premise": premise,
        "story_spine": {"who": "Mara", "wants": "Serve dinner"},
        "writing_profile": {},
    }
    binding = {"id": str(uuid4()), "content_hash": _hash(content), "content": content}
    hero = {
        "name": "Mara",
        "gender": "female",
        "pronoun_set_zh": "她",
        "pronoun_set_en": "she/her",
        "goal": "Serve dinner",
    }
    project = NS(
        id=uuid4(),
        slug="synthetic-cast-identity",
        title="Synthetic dinner",
        genre="realistic",
        sub_genre="daily",
        language="en",
        target_chapters=4,
        target_word_count=8000,
        metadata_json={
            "premise": premise,
            "book_design_snapshot": {"protagonist": hero},
            "creation_canonical_snapshot_id": binding["id"],
            "creation_canonical_snapshot_hash": binding["content_hash"],
            "llm_model_id": "minimax-m3.1-flash",
        },
        _source_bound_planning_canonical=binding,
    )
    projection = {
        "protagonist": hero,
        "antagonist": {"name": "Nico", "gender": "unknown"},
        "supporting_cast": [{"name": "Ada", "gender": "unknown"}],
        "antagonist_forces": [],
        "conflict_map": [],
    }
    people = parts.roster(projection)
    persons = {p["id"]: deepcopy(p["source"]) for p in people}
    for p in people[1:]:
        persons[p["id"]].update(
            pronoun_set_zh=None,
            pronoun_set_en=None,
            goal="Finish dinner",
            background=None,
            metadata={"unrelated": {"literal": "preserve me"}},
        )
    links = {
        "layout": {
            "protagonist": "person:0",
            "antagonist": "person:1",
            "supporting_cast": ["person:2"],
        },
        "antagonist_forces": [],
        "conflict_map": [],
    }
    payload = parts.assemble(people, persons, links)
    packet = {"approved_sources": {"canonical_snapshot": deepcopy(binding)}}
    documents = {
        p["id"]: {
            "frozen_source_packet": deepcopy(packet),
            "source_packet_sha256": parts._sha(packet),
        }
        for p in people
    }
    documents["links"] = {"retained_links_payload": deepcopy(links)}
    completions = {
        p["id"]: llm.LLMCompletionResult(
            content=json.dumps({"person": persons[p["id"]]}, ensure_ascii=False),
            provider="openai",
            model_name="openai/MiniMax-M3.1-Flash",
            input_tokens=10,
            output_tokens=20,
            finish_reason="stop",
            llm_run_id=uuid4(),
        )
        for p in people
    }
    receipts = [
        {
            "part_id": p["id"],
            "payload_sha256": parts._sha({"person": persons[p["id"]]}),
            "llm_run_id": str(completions[p["id"]].llm_run_id),
        }
        for p in people
    ]
    return NS(
        project=project,
        projection=projection,
        payload=payload,
        people=people,
        persons=persons,
        links=links,
        documents=documents,
        completions=completions,
        receipts=receipts,
        packet=packet,
    )


def test_real_postbind_diagnosis_has_only_two_missing_secondary_identities():
    f = fixture()
    before = deepcopy((f.payload, f.projection, f.packet))
    targets = identity.diagnose(f.project, f.projection, f.payload, f.packet)
    assert {t["name"] for t in targets} == {"Nico", "Ada"}
    assert all(t["fields"] == sorted(identity._FIELDS) for t in targets)
    assert (f.payload, f.projection, f.packet) == before


@pytest.mark.parametrize("fault", ["source_hash", "source_id", "source_content", "packet"])
def test_changed_canonical_source_cannot_authorize_completion(fault):
    f = fixture()
    if fault == "source_hash":
        f.project._source_bound_planning_canonical["content_hash"] = "f" * 64
    elif fault == "source_id":
        f.project.metadata_json["creation_canonical_snapshot_id"] = str(uuid4())
    elif fault == "source_content":
        f.project._source_bound_planning_canonical["content"]["premise"] += " drift"
    else:
        f.packet["approved_sources"]["canonical_snapshot"]["content"]["premise"] += " drift"
    with pytest.raises(ValueError):
        identity.diagnose(f.project, f.projection, f.payload, f.packet)


def test_exact_missing_identity_leaves_are_model_patchable_but_missing_name_is_not():
    person = {"name": "Nico", "source_slot": "antagonist"}
    schema = identity.identity_schema(person, identity._FIELDS)
    value = {"person": {"name": "Nico"}}
    contract = fields.correction_contract(value, schema)
    assert set(contract["required"]) == {"$/person/" + key for key in identity._FIELDS}
    patch = {
        "$/person/gender": "female",
        "$/person/pronoun_set_zh": "她",
        "$/person/pronoun_set_en": "she/her",
    }
    result = fields.apply_corrections(value, schema, contract, patch)
    assert result["person"]["gender"] == "female" and value == {"person": {"name": "Nico"}}
    with pytest.raises(ValueError, match="not_patchable"):
        fields.correction_contract({"person": {}}, schema)


@pytest.mark.parametrize("fault", ["name", "background", "metadata", "extra", "wrong_pair"])
def test_patch_cannot_change_other_fields_or_create_conflicting_identity(fault):
    f = fixture()
    before = {"person": deepcopy(f.persons["person:1"])}
    after = deepcopy(before)
    after["person"].update(gender="female", pronoun_set_zh="她", pronoun_set_en="she/her")
    if fault == "wrong_pair":
        after["person"]["pronoun_set_en"] = "he/him"
    elif fault == "extra":
        after["person"]["new_backstory"] = "forbidden"
    else:
        after["person"][fault] = "changed"
    with pytest.raises(ValueError):
        identity.check_patch(before, after, {"name": "Nico", "fields": list(identity._FIELDS)})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["valid", "unknown", "wrong_pair", "prior_correction", "broken_chain"]
)
async def test_only_model_authored_leaves_change_and_unknown_has_no_retry(monkeypatch, mode):
    f = fixture()
    if mode in {"prior_correction", "broken_chain"}:
        current = {"person": deepcopy(f.persons["person:1"])}
        current["person"]["life_history"] = {"education": "no additional past claimed"}
        raw = deepcopy(current)
        raw["person"]["life_history"]["education"] = ["wrong typed value"]
        f.completions["person:1"].content = json.dumps(raw, ensure_ascii=False)
        f.persons["person:1"] = current["person"]
        f.payload = parts.assemble(f.people, f.persons, f.links)
        prior = next(r for r in f.receipts if r["part_id"] == "person:1")
        prior.update(
            payload_sha256=parts._sha(current),
            field_correction_receipt={
                "schema": fields.POLICY,
                "base_llm_run_id": str(f.completions["person:1"].llm_run_id),
                "base_payload_sha256": parts._sha(raw),
                "corrected_payload_sha256": parts._sha(current),
                "correction_llm_run_id": str(uuid4()),
            },
        )
        if mode == "broken_chain":
            prior["field_correction_receipt"]["base_payload_sha256"] = "f" * 64
    before = deepcopy((f.payload, f.persons, f.links))
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        if mode == "unknown":
            raise ExecutionBudgetError("execution_result_unknown", "synthetic")
        doc = json.loads(request.user_prompt)
        name = doc["identity_completion_contract"]["target"]["name"]
        if mode == "prior_correction" and name == "Nico":
            assert (
                fields._sha(json.loads(doc["retained_part"]["raw_json"]))
                == doc["retained_part"]["payload_sha256"]
                == parts._sha(raw)
            )
            assert doc["current_part_input"]["payload"] == current
            assert doc["current_part_input"]["payload_sha256"] == parts._sha(current)
            assert doc["current_part_input"]["not_original_provider_payload"] is True
            encoded = doc.pop("derived_source_encoding")
            restored = deepcopy(doc)
            for reference in encoded["references"]:
                target = restored
                for key in reference["to"][2:].split("/"):
                    target = target[key]
                assert fields._sha(target) == reference["value_sha256"]
                parent = restored
                tokens = reference["at"][2:].split("/")
                for key in tokens[:-1]:
                    parent = parent[key]
                parent[tokens[-1]] = deepcopy(target)
            assert fields._sha(restored) == encoded["logical_document_sha256"]
            assert restored["current_part_input"]["lineage"]["raw_json"] == (
                f.completions["person:1"].content
            )
            assert restored["current_part_input"]["lineage"]["prior_part_receipt"] == prior
            assert encoded["lossless"] is True and encoded["truncated"] is False
        values = (
            {"gender": "female", "pronoun_set_zh": "她", "pronoun_set_en": "she/her"}
            if name == "Nico"
            else {"gender": "nonbinary", "pronoun_set_zh": "他", "pronoun_set_en": "they/them"}
        )
        if mode == "wrong_pair":
            values["pronoun_set_en"] = "he/him"
        return llm.LLMCompletionResult(
            content=json.dumps(
                {"$/person/" + key: value for key, value in values.items()}, ensure_ascii=False
            ),
            provider="openai",
            model_name="openai/MiniMax-M3.1-Flash",
            input_tokens=10,
            output_tokens=20,
            finish_reason="stop",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(llm, "complete_text", complete)
    monkeypatch.setattr(planner, "_heartbeat_planner_workflow", AsyncMock())
    args = dict(
        project=f.project,
        projection=f.projection,
        payload=f.payload,
        people=f.people,
        persons=f.persons,
        receipts=f.receipts,
        completions=f.completions,
        documents=f.documents,
        packet=f.packet,
        recipe_hash="a" * 64,
        workflow_run_id=uuid4(),
        before_dispatch=AsyncMock(),
    )
    session = NS(commit=AsyncMock())
    if mode in {"valid", "prior_correction"}:
        result, receipt = await identity.complete_missing(session, load_settings(), **args)
        assert len(requests) == 2 and result["antagonist"]["gender"] == "female"
        assert result["supporting_cast"][0]["gender"] == "nonbinary"
        assert (
            receipt["new_whole_person_generation_calls"] == 0
            and receipt["quality_approval_granted"] is False
        )
        assert not identity.diagnose(f.project, f.projection, result, f.packet)
        assert result["protagonist"] == f.payload["protagonist"]
        if mode == "prior_correction":
            proof = receipt["repairs"][0]["field_correction_receipt"]
            assert proof["base_payload_sha256"] == parts._sha(raw)
            assert proof["correction_input_payload_sha256"] == parts._sha(current)
            assert proof["not_original_provider_payload"] is True
            assert result["antagonist"]["life_history"] == current["person"]["life_history"]
    else:
        with pytest.raises(
            (
                ValueError,
                ExecutionBudgetError,
            )
        ):
            await identity.complete_missing(session, load_settings(), **args)
        assert len(requests) == (0 if mode == "broken_chain" else 1)
    assert (f.payload, f.persons, f.links) == before
