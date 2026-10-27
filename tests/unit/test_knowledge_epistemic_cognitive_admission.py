"""Pure per-item abstention and real SQLite Bible write/rebuild/history parity."""
# ruff: noqa: RUF001 -- Exact retained and unrelated quotation fixtures.

from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_adopted_bible_lineage import db as db
from test_adopted_bible_lineage import knowledge_db as knowledge_db
from test_adopted_bible_lineage import stub_llm, update

from bestseller.infra.db.models import CharacterModel
from bestseller.services import story_bible_projection as projection
from bestseller.services.character_snapshot_sources import read_character_snapshot_knowledge
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.knowledge_epistemic_admission import (
    POLICY,
    PROMPT_SCHEMA,
    _quotation_spans,
    admit_cognitive_update,
    cognitive_prompt_view,
    derive_snapshot_item_disposition,
    merge_cognitive_admission,
)

pytestmark = pytest.mark.unit


def source(body):
    return {
        "schema": "adopted-knowledge.v1",
        "project_id": str(uuid4()),
        "chapter_id": str(uuid4()),
        "source_chapter_draft_version_id": str(uuid4()),
        "source_version_no": 8,
        "source_body_sha256": sha256(body.encode()).hexdigest(),
        "llm_run_id": str(uuid4()),
        "operation_id": "retained-application",
    }


@pytest.mark.parametrize(
    "name,body,interpretation",
    [
        (
            "周夏",
            "“你昨天说，乔芸寄了盘磁带在里面。”我问。\n\n"
            "他抹了把脸：“我什么时候说过她寄来的？我只说机器里有东西。”\n\n"
            "“你说可能是她寄的磁带。”",
            "曾将父亲所说的“机器里有东西”理解为“乔芸寄来了磁带”，现已由父亲当场纠正；父亲此前只说过机器里有东西。",
        ),
        (
            "Mira",
            '"You said the vault was empty," said Mira.\n\n"I said it was sealed," Jonas replied.',
            "Mira was wrong and Jonas corrected her account of the vault.",
        ),
        (
            "信使",
            "「你说渡船已经离港。」\n\n「我只说它要检修。」",
            "信使关于渡船的判断已被对方纠正。",
        ),
    ],
)
def test_current_fault_and_cross_book_classifications_abstain_without_losing_knowledge(
    name, body, interpretation
):
    origin = source(body)
    candidate = {
        "name": name,
        "false_beliefs": [interpretation],
        "knowledge_gained": ["A separately recorded observation"],
        "resolution_verified": True,
    }  # An untrusted candidate flag is not a witness.
    payload = {"character_updates": [candidate]}
    before = deepcopy((origin, candidate, payload))
    view = admit_cognitive_update(
        update=candidate,
        item_path="/character_updates/0",
        source_lineage=origin,
        source_body=body,
        application_payload=payload,
    )
    assert view["admitted_update"]["false_beliefs"] == []
    assert view["admitted_update"]["knowledge_gained"] == candidate["knowledge_gained"]
    assert view["processing_complete"] and not view["truth_approved"]
    item = view["item_dispositions"][0]
    assert item["interpretation"] == interpretation
    assert item["source_lineage"]["llm_run_id"] == origin["llm_run_id"]
    assert item["raw_binding_status"] == "unknown"
    assert item["paid_origin_status"] == "recorded_reference_only"
    assert item["item_path"] == "/character_updates/0/false_beliefs/0"
    assert view["literal_source_observations"][0]["body_anchors"] == list(
        build_critic_body_anchors(body)
    )
    assert (origin, candidate, payload) == before


def test_view_hash_is_content_policy_source_bound_without_audit_clocks():
    body = '"The observatory is closed," said the guide.'
    origin = source(body)
    candidate = {"name": "Iris", "false_beliefs": ["The guide settled the disputed schedule."]}
    payload = {"character_updates": [candidate]}

    def derive(lineage=origin, application=payload, text=body):
        return admit_cognitive_update(
            update=candidate,
            item_path="/character_updates/0",
            source_lineage=lineage,
            source_body=text,
            application_payload=application,
        )

    original = derive()
    assert original == derive({**origin, "recorded_at": "later", "transport_audit": {"at": "now"}})
    assert original["view_sha256"] != derive({**origin, "llm_run_id": str(uuid4())})["view_sha256"]
    assert (
        original["view_sha256"]
        != derive(application={**payload, "other_original_item": []})["view_sha256"]
    )
    assert original["view_sha256"] != derive(text=body + "Changed body.")["view_sha256"]
    state = merge_cognitive_admission({"knows": ["keep"]}, original)
    assert merge_cognitive_admission(state, original) == state
    assert state["epistemic_admission"]["policy"] == POLICY


@pytest.mark.parametrize("body", [None, "A changed source"])
def test_unavailable_or_wrong_body_is_covered_abstention_without_invented_anchors(body):
    origin = source("Original body")
    candidate = {"false_beliefs": ["An unsupported classification"]}
    view = admit_cognitive_update(
        update=candidate,
        item_path="/character_updates/0",
        source_lineage=origin,
        source_body=body,
        application_payload=candidate,
    )
    assert view["processing_complete"] and not view["truth_approved"]
    assert view["admitted_update"]["false_beliefs"] == []
    assert view["literal_source_observations"][0]["body_anchors"] == []


def test_fold_preserves_manual_initial_values_and_excludes_old_derived_resurrection():
    body = '"The gate is open," said the courier. "It is sealed," replied the guard.'
    origin = source(body)
    raw = {
        "name": "Courier",
        "false_beliefs": ["The guard proved the courier was wrong."],
        "knowledge_gained": ["A new observed seal"],
    }
    row = CharacterModel(
        id=uuid4(),
        project_id=uuid4(),
        name="Courier",
        knowledge_state_json={
            "private": {"keep": None},
            "unverified_interpretations": [{"old_body": "must not survive"}],
        },
        metadata_json={},
    )
    baseline = {
        "fields": {
            "knowledge": {
                "knows": ["manual initial fact"],
                "falsely_believes": ["explicit authored initial belief"],
            },
            "metadata": {},
        }
    }
    before = deepcopy((baseline, raw))
    desired = projection._fold(
        row,
        baseline,
        [
            (
                1,
                {
                    "source_lineage": origin,
                    "update": raw,
                    "epistemic_source_body": body,
                    "epistemic_application_payload": {"character_updates": [raw]},
                    "epistemic_item_path": "/character_updates/0",
                },
            )
        ],
    )
    state = desired["knowledge"]
    assert state["knows"] == ["manual initial fact", "A new observed seal"]
    assert state["falsely_believes"] == ["explicit authored initial belief"]
    assert state["private"] == {"keep": None}
    assert all("old_body" not in item for item in state["unverified_interpretations"])
    assert (baseline, raw) == before


@pytest.mark.parametrize(
    "body,quote,expected",
    [
        (
            "The archivist said, “She left seven years ago.”",
            "She left seven years ago",
            "reported_observation",
        ),
        (
            '"The guard said, ‘The bridge fell.’" Mira repeated.',
            "The bridge fell",
            "reported_observation",
        ),
        ("甲说‘乙已经离开七年’。", "乙已经离开七年", "reported_observation"),
        ("甲说'乙已经离开七年'。", "乙已经离开七年", "reported_observation"),
        (
            "'She left seven years ago,' Mira said.",
            "She left seven years ago",
            "reported_observation",
        ),
        ("甲说‘乙回答“已经离开七年”’。", "已经离开七年", "reported_observation"),
        ("甲说'乙回答‘已经离开七年’'。", "已经离开七年", "reported_observation"),
        ("‘It’s seven litres,’ she said.", "seven litres", "reported_observation"),
        ("'It's seven litres,' she said.", "seven litres", "reported_observation"),
        ("John's gauge showed seven litres.", "seven litres", "retain_existing_fact"),
        ("The sailors' gauge showed seven litres.", "seven litres", "retain_existing_fact"),
        ("The sailors’ gauge showed seven litres.", "seven litres", "retain_existing_fact"),
        ("John' left. The gauge showed seven litres.", "seven litres", "abstain"),
        (
            "The gauge showed seven litres.",
            "The gauge showed seven litres.",
            "retain_existing_fact",
        ),
        ('"Seven years," he said. "Seven years," she replied.', "Seven years", "abstain"),
        ("“The quoted sentence never closes.", "sentence", "abstain"),
        ("The bell rang.", None, "abstain"),
    ],
)
def test_shared_snapshot_disposition_quotes_are_not_numeric_truth(body, quote, expected):
    item = {
        "name": "A duration or quantity",
        "value": "7",
        "unit": "units",
        "kind": "elapsed_story_time",
        "source_quote": quote,
    }
    raw_sha = sha256(json.dumps({"facts": [item]}).encode()).hexdigest()
    result = derive_snapshot_item_disposition(
        item=item,
        item_path="/facts/0",
        source_lineage={**source(body), "raw_sha256": raw_sha},
        source_body=body,
        application_payload={"facts": [item], "raw_sha256": raw_sha},
    )
    assert result["disposition"] == expected
    assert result["processing_complete"] and not result["truth_approved"]
    if expected == "retain_existing_fact":
        assert result["admitted_item"] == item
    elif expected == "reported_observation":
        observation = result["resolved_observation"]
        assert observation["text"] == body
        assert observation["embedded_proposition_truth"] == "not_established"
        assert observation["speaker_resolution"] == "not_inferred"


@pytest.mark.parametrize("body", ["甲说‘乙已经离开七年’。", "甲说'乙已经离开七年'。"])
def test_cjk_single_quotes_are_balanced_spans_without_apostrophe_exception(body):
    spans, malformed = _quotation_spans(body)
    assert spans == [(2, 11)] and not malformed


@pytest.mark.parametrize("raw_sha", [None, "not-a-sha", "0" * 64])
def test_snapshot_positive_retention_needs_explicit_raw_application_binding(raw_sha):
    body = "The gauge showed seven litres."
    item = {"name": "volume", "kind": "quantity", "value": "7", "source_quote": body}
    origin = {**source(body), "raw_sha256": raw_sha}
    origin.pop("llm_run_id")
    result = derive_snapshot_item_disposition(
        item=item,
        item_path="/facts/0",
        source_lineage=origin,
        source_body=body,
        application_payload={"facts": [item], "raw_sha256": "1" * 64},
    )
    assert result["disposition"] == "abstain"
    assert result["raw_binding_status"] == result["paid_origin_status"] == "unknown"
    assert result["paid_origin_witnesses"] == []


def test_actual_receipt_operation_reference_is_retained_without_audit_clocks():
    body = '"The cellar is dry," said the visitor.'
    candidate = {"false_beliefs": ["The visitor disproved the flood account."]}
    origin = source(body)
    run_id = origin.pop("llm_run_id")
    origin["llm_operations"] = [
        {"llm_run_id": run_id, "operation_id": "retained", "recorded_at": "later"}
    ]
    view = admit_cognitive_update(
        update=candidate,
        item_path="/character_updates/0",
        source_lineage=origin,
        source_body=body,
        application_payload={"character_updates": [candidate]},
    )
    decision = view["item_dispositions"][0]
    assert decision["paid_origin_status"] == "recorded_reference_only"
    assert decision["paid_origin_witnesses"] == [{"llm_run_id": run_id, "operation_id": "retained"}]
    assert decision["raw_binding_status"] == "unknown"


def test_malformed_classification_preserves_exact_original_pointer_without_list_coercion():
    candidate = {"false_beliefs": {"untyped": "An unsupported classification"}}
    view = admit_cognitive_update(
        update=candidate,
        item_path="/character_updates/2",
        source_lineage=source("Body"),
        source_body="Body",
        application_payload={"character_updates": [{}, {}, candidate]},
    )
    assert view["item_dispositions"][0]["item_path"] == "/character_updates/2/false_beliefs"
    assert view["item_dispositions"][0]["interpretation"] == candidate["false_beliefs"]


def test_prompt_view_deduplicates_full_source_without_losing_paragraphs_or_interpretations():
    body = "\n\n".join(
        f"Paragraph {index}: ‘The report is disputed.’\nThe record remains sealed."
        for index in range(1, 45)
    )
    origin = source(body)
    knowledge = {"knows": ["Existing source fact"], "authored_private": {"keep": [None, True]}}
    texts = ["The witness disproved the report.", "The keeper corrected the witness."]
    for text in texts:
        candidate = {"false_beliefs": [text]}
        view = admit_cognitive_update(
            update=candidate,
            item_path="/character_updates/0",
            source_lineage=origin,
            source_body=body,
            application_payload={"character_updates": [candidate]},
        )
        knowledge = merge_cognitive_admission(knowledge, view)
    before = deepcopy(knowledge)
    compact = cognitive_prompt_view(knowledge)
    assert knowledge == before and compact == cognitive_prompt_view(knowledge)
    assert cognitive_prompt_view(compact) == compact
    assert compact["authored_private"] == knowledge["authored_private"]
    assert compact["knows"] == knowledge["knows"]
    catalog = compact["literal_source_observations"]
    assert len(catalog) == 1 and catalog[0]["schema"] == PROMPT_SCHEMA
    assert catalog[0]["source_body_sha256"] == origin["source_body_sha256"]
    assert catalog[0]["paragraphs"] == [
        {"id": anchor["id"], "text": anchor["text"]} for anchor in build_critic_body_anchors(body)
    ]
    for original, projected in zip(
        knowledge["unverified_interpretations"], compact["unverified_interpretations"], strict=True
    ):
        for key in ("interpretation", "classification", "disposition", "reason", "truth_approved"):
            assert projected[key] == original[key]
        assert projected["source_index"] == 0 and "body_anchor_ids" not in projected
    encoded = json.dumps(compact, ensure_ascii=False)
    assert encoded.count(origin["source_body_sha256"]) == 1
    assert len(encoded) < len(json.dumps(knowledge, ensure_ascii=False)) // 2
    assert (
        compact["epistemic_admission"]["view_sha256"]
        != knowledge["epistemic_admission"]["view_sha256"]
    )
    altered = deepcopy(knowledge)
    altered["unverified_interpretations"][0]["interpretation"] += " Unresolved."
    assert (
        cognitive_prompt_view(altered)["epistemic_admission"]["view_sha256"]
        != compact["epistemic_admission"]["view_sha256"]
    )


def test_prompt_view_preserves_unrecognized_records_fields_and_nested_authored_data():
    body = "‘The library closed,’ said the messenger.\n\nThe wax remained unbroken."
    candidate = {"false_beliefs": ["The messenger proved the library was closed."]}
    view = admit_cognitive_update(
        update=candidate,
        item_path="/character_updates/0",
        source_lineage=source(body),
        source_body=body,
        application_payload={"character_updates": [candidate]},
    )
    state = merge_cognitive_admission({"unknown_authored": {"audit": "story material"}}, view)
    unknown = deepcopy(state["unverified_interpretations"][0])
    unknown["source_lineage"]["authored_annotation"] = "Keep verbatim"
    state["unverified_interpretations"].append(unknown)
    unknown_observation = deepcopy(state["literal_source_observations"][0])
    unknown_observation["unrecognized_field"] = {"keep": body}
    state["literal_source_observations"].append(unknown_observation)
    state["epistemic_admission"]["authored_note"] = "Do not drop unknown envelope fields"
    before = deepcopy(state)
    compact = cognitive_prompt_view(state)
    assert state == before
    assert compact["unverified_interpretations"][-1] == unknown
    assert compact["literal_source_observations"][-1] == unknown_observation
    assert compact["epistemic_admission"] == state["epistemic_admission"]
    assert compact["unknown_authored"] == state["unknown_authored"]


def test_prompt_view_does_not_authorize_or_compact_tampered_source_catalog():
    body = "The gallery remained dark."
    candidate = {"false_beliefs": ["The clerk disproved the visitor's account."]}
    view = admit_cognitive_update(
        update=candidate,
        item_path="/character_updates/0",
        source_lineage=source(body),
        source_body=body,
        application_payload={"character_updates": [candidate]},
    )
    state = merge_cognitive_admission({}, view)
    state["literal_source_observations"][0]["body_anchors"][0]["text"] += " Altered"
    compact = cognitive_prompt_view(state)
    assert compact["literal_source_observations"] == state["literal_source_observations"]
    assert compact["unverified_interpretations"] == state["unverified_interpretations"]


@pytest.mark.asyncio
async def test_duplicate_identical_raw_updates_cover_each_original_index(db, monkeypatch):
    session, project, chapter, draft = db
    draft.content_md = '"The tunnel is open," said the porter.'
    await session.commit()
    candidate = {"name": "Porter", "false_beliefs": ["The porter disproved the closure."]}
    raw = {"character_updates": [candidate, deepcopy(candidate)]}
    stub_llm(monkeypatch, raw)
    await update(db)
    await session.commit()
    character = await session.scalar(select(CharacterModel).where(CharacterModel.name == "Porter"))
    expected = {"/character_updates/0/false_beliefs/0", "/character_updates/1/false_beliefs/0"}
    assert {
        item["item_path"] for item in character.knowledge_state_json["unverified_interpretations"]
    } == expected
    readback = await read_character_snapshot_knowledge(
        session,
        project_id=project.id,
        character=character,
        before_chapter_number=1,
    )
    assert readback["epistemic_admission"] == character.knowledge_state_json["epistemic_admission"]
    assert {item["item_path"] for item in readback["unverified_interpretations"]} == expected
    assert chapter.metadata_json["story_bible_update_receipts"][-1]["candidate_payload"] == raw


@pytest.mark.asyncio
async def test_actual_bible_new_write_rebuild_and_history_share_disposition_and_reuse(
    db, monkeypatch
):
    session, project, chapter, draft = db
    body = '"You said the tower was empty," said Ada.\n\n"I said it was sealed," said Basil.'
    draft.content_md = body
    character = CharacterModel(
        project_id=project.id,
        name="Ada",
        role="protagonist",
        knowledge_state_json={"knows": ["Manual baseline"], "private": {"retain": None}},
        metadata_json={"authored_annotation": "keep"},
    )
    session.add(character)
    await session.commit()
    raw = {
        "character_updates": [
            {
                "name": "Ada",
                "knowledge_gained": ["The tower has a seal"],
                "false_beliefs": ["Ada misunderstood and Basil conclusively corrected her."],
            }
        ]
    }
    calls = stub_llm(monkeypatch, raw)
    outcome = await update(db)
    await session.commit()
    assert outcome["extraction_status"] == "ok" and len(calls) == 1
    await session.refresh(character)
    await session.refresh(chapter)
    original_raw = deepcopy(chapter.metadata_json["story_bible_update_receipts"])
    original_history = deepcopy(character.metadata_json["story_bible_update_history"])
    state = deepcopy(character.knowledge_state_json)
    assert state["knows"] == ["Manual baseline", "The tower has a seal"]
    assert state.get("falsely_believes", []) == []
    assert (
        state["unverified_interpretations"][0]["interpretation"]
        == raw["character_updates"][0]["false_beliefs"][0]
    )
    assert state["literal_source_observations"][0]["body_anchors"] == list(
        build_critic_body_anchors(body)
    )
    assert original_raw[-1]["candidate_payload"] == raw
    assert original_history[-1]["update"] == raw["character_updates"][0]
    audit = deepcopy(character.metadata_json["knowledge_epistemic_disposition_history"])
    readback = await read_character_snapshot_knowledge(
        session,
        project_id=project.id,
        character=character,
        before_chapter_number=1,
    )
    assert readback["knows"] == ["The tower has a seal"]
    assert readback.get("falsely_believes", []) == []
    assert readback["epistemic_admission"] == state["epistemic_admission"]
    assert readback["unverified_interpretations"] == state["unverified_interpretations"]
    repeated = await update(db)
    await session.commit()
    assert repeated["extraction_status"] == "already_applied" and len(calls) == 1
    await session.refresh(character)
    await session.refresh(chapter)
    assert character.metadata_json["knowledge_epistemic_disposition_history"] == audit
    assert character.metadata_json["story_bible_update_history"] == original_history
    assert chapter.metadata_json["story_bible_update_receipts"] == original_raw
    assert character.knowledge_state_json == state


@pytest.mark.asyncio
async def test_legacy_current_raw_history_cannot_resurrect_quarantined_false_belief(
    db, monkeypatch
):
    session, project, chapter, draft = db
    draft.content_md = (
        '"The letter arrived yesterday," said the pilot. "You said today," replied Noor.'
    )
    await session.commit()
    raw = {
        "character_updates": [
            {
                "name": "Noor",
                "knowledge_gained": ["A letter is present"],
                "false_beliefs": ["Noor was mistaken; the pilot corrected the delivery date."],
            }
        ]
    }
    stub_llm(monkeypatch, raw)
    await update(db)
    await session.commit()
    character = await session.scalar(select(CharacterModel).where(CharacterModel.name == "Noor"))
    old_raw = deepcopy(chapter.metadata_json["story_bible_update_receipts"])
    old_history = deepcopy(character.metadata_json["story_bible_update_history"])
    baseline = deepcopy(character.metadata_json[projection.BASE])
    # Recreate the historical bad current projection while preserving actual raw/history.
    character.knowledge_state_json = {
        "knows": ["A letter is present"],
        "falsely_believes": raw["character_updates"][0]["false_beliefs"],
    }
    character.metadata_json = {
        key: value
        for key, value in character.metadata_json.items()
        if key != "knowledge_epistemic_disposition_history"
    }
    await session.commit()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Reprojection must not call a model")

    from bestseller.services import llm

    monkeypatch.setattr(llm, "complete_text", forbidden)
    # The history reader is safe even before the parent's durable migration.
    readback = await read_character_snapshot_knowledge(
        session,
        project_id=project.id,
        character=character,
        before_chapter_number=1,
    )
    assert readback.get("falsely_believes", []) == []
    assert readback["unverified_interpretations"]
    changes, _legacy, _active, _witnesses = await projection._plan(session, project.id)
    for row, desired in changes:
        projection._apply_fields(row, desired)
    await session.commit()
    await session.refresh(character)
    assert character.knowledge_state_json.get("falsely_believes", []) == []
    assert character.knowledge_state_json["knows"] == ["A letter is present"]
    assert character.metadata_json[projection.BASE] == baseline
    assert character.metadata_json["story_bible_update_history"] == old_history
    assert chapter.metadata_json["story_bible_update_receipts"] == old_raw
    assert draft.content_md.startswith('"The letter arrived yesterday,"')
