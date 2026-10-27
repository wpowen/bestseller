"""World identity entry and retained-evidence recovery in isolated real SQLite."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_adopted_bible_lineage import db as db
from test_adopted_bible_lineage import knowledge_db as knowledge_db

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    WorldRuleModel,
)
from bestseller.services import llm
from bestseller.services import story_bible as bible
from bestseller.services import story_bible_projection as projection

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    def forbidden(*args, **kwargs):
        raise AssertionError("World identity tests forbid network, PG and provider calls")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.mark.parametrize(
    "names",
    [
        ["山川河海", "风雨雷电"],
        ["共" * 32 + "甲", "共" * 32 + "乙"],
        ["a-b", "a b", "A B"],
    ],
)
async def test_entry_preserves_distinct_full_names(db, names):
    session, project, chapter, _ = db
    assert session.bind.dialect.name == "sqlite" and "test" in session.bind.engine.url.database
    updates = {
        "world_updates": [
            {"rule_or_location": name, "description": f"Description for {name}"} for name in names
        ]
    }
    await bible._apply_adopted_bible_updates(session, project, chapter, updates, {})
    rows = list(await session.scalars(select(WorldRuleModel)))
    assert {r.name for r in rows} == set(names)
    assert len({r.rule_code for r in rows}) == len(names)
    assert all(len(r.rule_code) <= 32 for r in rows)


async def test_exact_legacy_and_authored_code_preserved_and_foreign_id_rejected(db):
    session, project, chapter, _ = db
    row = WorldRuleModel(
        id=uuid4(),
        project_id=project.id,
        rule_code="R-author",
        name="作者规则",
        description="Authored baseline",
        metadata_json={"opaque": [1]},
    )
    session.add(row)
    await session.flush()
    await bible._apply_adopted_bible_updates(
        session,
        project,
        chapter,
        {
            "world_updates": [
                {"rule_or_location": row.name, "description": "Longer adopted observation"}
            ]
        },
        {},
    )
    assert row.rule_code == "R-author" and row.name == "作者规则"
    assert row.metadata_json[projection.BASE]["fields"]["description"] == "Authored baseline"
    other = ProjectModel(
        slug=uuid4().hex, title="Other", genre="test", target_word_count=1, target_chapters=1
    )
    session.add(other)
    await session.flush()
    for project_id, name in [(other.id, row.name), (project.id, "另个规则")]:
        with pytest.raises(ValueError, match="world_rule_identity"):
            await bible._get_world_rule_by_identity(
                session, project_id=project_id, rule_id=row.id, rule_code=row.rule_code, name=name
            )


async def actual_retained_case(db):
    """Import exact historical evidence; only adoption scaffold is synthetic SQLite."""
    session = db[0]
    data = json.loads(
        (
            ROOT / "reports/framework-recovery-2026-10-21/phase8y5-adopted-knowledge-snapshot.json"
        ).read_text()
    )
    project = ProjectModel(
        id=UUID(data["project_id"]),
        slug=uuid4().hex,
        title="Retained",
        genre="test",
        target_word_count=1000,
        target_chapters=4,
        metadata_json={},
    )
    session.add(project)
    await session.flush()
    paths = {
        8: "adopted-chapter-1-v8.md",
        9: "audit-chapter-2-v9-d27f8f88f64e.md",
        12: "audit-chapter-2-v12-8668912c1dbd.md",
    }
    for cid, info in data["tables"]["chapters"].items():
        receipts = info.get("metadata", {}).get("story_bible_update_receipts", [])
        if not receipts:
            continue
        chapter = ChapterModel(
            id=UUID(cid),
            project_id=project.id,
            chapter_number=info["chapter_number"],
            chapter_goal="retained test",
            metadata_json={"story_bible_update_receipts": deepcopy(receipts)},
        )
        session.add(chapter)
        await session.flush()
        for index, receipt in enumerate(receipts):
            source = receipt["source_lineage"]
            body = (
                ROOT
                / "reports/framework-remediation-2026-10-22"
                / paths[source["source_version_no"]]
            ).read_text()
            assert hashlib.sha256(body.encode()).hexdigest() == source["source_body_sha256"]
            session.add(
                ChapterDraftVersionModel(
                    id=UUID(source["source_chapter_draft_version_id"]),
                    project_id=project.id,
                    chapter_id=chapter.id,
                    version_no=source["source_version_no"],
                    content_md=body,
                    is_current=index == len(receipts) - 1,
                    promotion_state="promoted" if index == len(receipts) - 1 else "superseded",
                    promotion_metadata={},
                )
            )
    for rid, info in data["tables"]["world_rules"].items():
        session.add(
            WorldRuleModel(
                id=UUID(rid),
                project_id=project.id,
                rule_code=info["rule_code"],
                name=info["name"],
                description=info["description"],
                story_consequence=info.get("story_consequence"),
                exploitation_potential=info.get("exploitation_potential"),
                metadata_json=deepcopy(info["metadata"]),
            )
        )
    await session.commit()
    return session, project, data


async def retained_raw(session, project):
    rows = list(
        await session.scalars(select(WorldRuleModel).where(WorldRuleModel.project_id == project.id))
    )
    chapters = list(
        await session.scalars(select(ChapterModel).where(ChapterModel.project_id == project.id))
    )
    drafts = list(
        await session.scalars(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.project_id == project.id
            )
        )
    )
    return {
        "rows": {
            str(r.id): [
                r.name,
                r.rule_code,
                deepcopy(r.metadata_json.get(projection.BASE)),
                deepcopy(r.metadata_json.get("story_bible_update_history", [])),
            ]
            for r in rows
        },
        "chapters": {str(c.id): deepcopy(c.metadata_json) for c in chapters},
        "drafts": {str(d.id): [d.content_md, d.promotion_state, d.is_current] for d in drafts},
    }


async def test_actual_collision_rejects_unmapped_projection(db):
    session, project, _ = await actual_retained_case(db)
    before = await retained_raw(session, project)
    with pytest.raises(projection.BibleProjectionAttributionError, match="identity"):
        await projection.reproject_adopted_bible(session, project)
    assert await retained_raw(session, project) == before


async def test_actual_migration_separates_rules_preserves_evidence_and_repeats(db):
    from bestseller.services.world_rule_identity_recovery import (
        apply_world_rule_identity_recovery,
        plan_world_rule_identity_recovery,
        world_rule_effective_history,
    )

    session, project, data = await actual_retained_case(db)
    before = await retained_raw(session, project)
    plan = await plan_world_rule_identity_recovery(session, project_id=project.id)
    assert await retained_raw(session, project) == before
    assert len(plan["mappings"]) == 5  # includes V9 and description_applied=False
    assert any(m["source_lineage"]["source_version_no"] == 9 for m in plan["mappings"])
    result = await apply_world_rule_identity_recovery(
        session, project_id=project.id, expected_plan_sha256=plan["plan_sha256"]
    )
    assert result["provider_calls"] == 0
    assert result["rows_added"] == 5 and result["mappings_added"] == 5
    for rid, image in project.metadata_json["world_rule_identity_recovery_receipts"][0][
        "preimages"
    ].items():
        assert image["description"] == data["tables"]["world_rules"][rid]["description"]
        assert image["metadata"] == data["tables"]["world_rules"][rid]["metadata"]
    await session.commit()
    after = await retained_raw(session, project)
    assert {k: after[k] for k in ("chapters", "drafts")} == {
        k: before[k] for k in ("chapters", "drafts")
    }
    assert {k: after["rows"][k] for k in before["rows"]} == before["rows"]
    rows = {
        r.name: r
        for r in await session.scalars(
            select(WorldRuleModel).where(WorldRuleModel.project_id == project.id)
        )
    }
    receipt = data["tables"]["chapters"]["4902a4a2-36bf-4296-b26c-0bfe21775e4a"]["metadata"][
        "story_bible_update_receipts"
    ][1]
    for update in receipt["candidate_payload"]["world_updates"]:
        row = rows[update["rule_or_location"]]
        # Existing matching address row retains its authored/longest baseline policy.
        if row.name != "南桥路十七号":
            assert row.description == update["description"]
    for name in ("南城东路二十七号", "旧收音机临时播放限制"):
        assert rows[name].description == ""
        effective = await world_rule_effective_history(
            session, project_id=project.id, row_id=rows[name].id
        )
        assert all(e["source_lineage"]["source_version_no"] == 9 for e in effective)
        changes, legacy, active, _ = await projection._plan(session, project.id)
        assert not legacy
        assert all(
            not projection.same_source(
                e["source_lineage"], active.get(e["source_lineage"]["chapter_id"])
            )
            for e in effective
        )
        desired = next(fields for r, fields in changes if r.id == rows[name].id)
        assert desired == rows[name].metadata_json[projection.BASE]["fields"]
        assert projection._fields(rows[name]) == desired
    saved = digest(await retained_raw(session, project)), deepcopy(project.metadata_json)
    assert not await projection.reproject_adopted_bible(session, project)
    again = await plan_world_rule_identity_recovery(session, project_id=project.id)
    assert again["mappings"] == []
    await apply_world_rule_identity_recovery(
        session, project_id=project.id, expected_plan_sha256=again["plan_sha256"]
    )
    await session.commit()
    assert saved == (digest(await retained_raw(session, project)), project.metadata_json)


@pytest.mark.parametrize(
    "damage", ["raw", "duplicate", "unknown_source", "baseline", "not_adopted", "adopted_body"]
)
async def test_ambiguous_migration_fails_closed(db, damage):
    from bestseller.services.world_rule_identity_recovery import plan_world_rule_identity_recovery

    session, project, _ = await actual_retained_case(db)
    chapter = await session.get(ChapterModel, UUID("4902a4a2-36bf-4296-b26c-0bfe21775e4a"))
    if damage in {"raw", "duplicate"}:
        metadata = deepcopy(chapter.metadata_json)
        receipt = metadata["story_bible_update_receipts"][1]
        if damage == "raw":
            receipt["raw_extractions"] = ["{}"]
        else:
            receipt["candidate_payload"]["world_updates"].append(
                deepcopy(receipt["candidate_payload"]["world_updates"][3])
            )
            receipt["raw_extractions"] = [
                json.dumps(receipt["candidate_payload"], ensure_ascii=False)
            ]
        chapter.metadata_json = metadata
    elif damage in {"not_adopted", "adopted_body"}:
        draft = await session.get(
            ChapterDraftVersionModel, UUID("32073a40-6d10-4395-bcd4-c454accadb45")
        )
        if damage == "not_adopted":
            draft.promotion_state = "candidate"
        else:
            draft.content_md += "Changed source body."
    else:
        row = await session.get(WorldRuleModel, UUID("5d7bde39-381a-5083-8088-a1eb0faa6d64"))
        metadata = deepcopy(row.metadata_json)
        if damage == "baseline":
            metadata.pop(projection.BASE)
        else:
            metadata["story_bible_update_history"][1]["source_lineage"]["chapter_id"] = str(uuid4())
        row.metadata_json = metadata
    await session.flush()
    before = await retained_raw(session, project)
    with pytest.raises(projection.BibleProjectionAttributionError):
        await plan_world_rule_identity_recovery(session, project_id=project.id)
    assert await retained_raw(session, project) == before


async def test_migration_reuses_authored_target_and_preserves_foreign_project(db):
    from bestseller.services import world_rule_identity_recovery as recovery

    session, project, _ = await actual_retained_case(db)
    authored = WorldRuleModel(
        id=uuid4(),
        project_id=project.id,
        rule_code="AUTHORED",
        name="临时外接电机播放",
        description="Authored baseline remains opaque.",
        metadata_json={"opaque": ["retained"], "story_bible_update_history": [{"authored": True}]},
    )
    foreign = WorldRuleModel(
        id=uuid4(),
        project_id=db[1].id,
        rule_code="AUTHORED",
        name=authored.name,
        description="Unrelated",
        metadata_json={"foreign": True},
    )
    session.add_all([authored, foreign])
    await session.commit()
    foreign_before = recovery._row_image(foreign)
    plan = await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
    target = next(m for m in plan["mappings"] if m["target_name"] == authored.name)
    assert target["target_row_id"] == str(authored.id) and target["target_rule_code"] == "AUTHORED"
    await recovery.apply_world_rule_identity_recovery(
        session, project_id=project.id, expected_plan_sha256=plan["plan_sha256"]
    )
    await session.commit()
    assert authored.rule_code == "AUTHORED"
    assert authored.metadata_json["opaque"] == ["retained"]
    assert (
        authored.metadata_json[projection.BASE]["fields"]["description"]
        == "Authored baseline remains opaque."
    )
    assert authored.metadata_json["story_bible_update_history"][0] == {"authored": True}
    assert recovery._row_image(foreign) == foreign_before


async def test_plan_detects_foreign_uuid_collision_without_binding_it(db):
    from bestseller.services import world_rule_identity_recovery as recovery

    session, project, _ = await actual_retained_case(db)
    row = WorldRuleModel(
        id=bible.stable_world_rule_id(
            project.id, bible.chapter_world_rule_code("临时外接电机播放")
        ),
        project_id=db[1].id,
        rule_code="foreign",
        name="临时外接电机播放",
        description="Foreign identity must never bind",
        metadata_json={},
    )
    session.add(row)
    await session.commit()
    before = recovery._row_image(row)
    with pytest.raises(projection.BibleProjectionAttributionError):
        await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
    assert recovery._row_image(row) == before


async def test_stale_plan_and_savepoint_failure_leave_all_evidence_unchanged(db):
    from bestseller.services import world_rule_identity_recovery as recovery

    session, project, _ = await actual_retained_case(db)
    plan = await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
    project.metadata_json = {"parent_owned_change": True}
    await session.commit()
    before = await retained_raw(session, project)
    with pytest.raises(projection.BibleProjectionAttributionError):
        await recovery.apply_world_rule_identity_recovery(
            session, project_id=project.id, expected_plan_sha256=plan["plan_sha256"]
        )
    assert await retained_raw(session, project) == before
    # Unattributed unrelated legacy source must block final _plan after additions,
    # rolling back every target and ledger write, rather than granting authority.
    row = await session.get(WorldRuleModel, UUID("4f824da9-f444-552a-84fb-c37fd3a33023"))
    row.metadata_json = {
        **row.metadata_json,
        "source_lineage": {"schema": "adopted-knowledge.v1", "project_id": str(project.id)},
    }
    await session.commit()
    before = await retained_raw(session, project)
    pid = project.id
    plan = await recovery.plan_world_rule_identity_recovery(session, project_id=pid)
    with pytest.raises(projection.BibleProjectionAttributionError):
        await recovery.apply_world_rule_identity_recovery(
            session, project_id=pid, expected_plan_sha256=plan["plan_sha256"]
        )
    project = await session.get(ProjectModel, pid)
    assert await retained_raw(session, project) == before
    assert recovery.LEDGER not in project.metadata_json


@pytest.mark.parametrize(
    "damage", ["mapping", "resigned_mapping", "copy", "raw", "old_history", "orphan"]
)
async def test_replay_rejects_tampered_derived_proof(db, damage):
    from bestseller.services import world_rule_identity_recovery as recovery

    session, project, _ = await actual_retained_case(db)
    plan = await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
    await recovery.apply_world_rule_identity_recovery(
        session, project_id=project.id, expected_plan_sha256=plan["plan_sha256"]
    )
    await session.commit()
    if damage in {"mapping", "resigned_mapping"}:
        metadata = deepcopy(project.metadata_json)
        metadata[recovery.LEDGER][0]["mappings"][0]["evidence"]["payload_index"] = 999
        if damage == "resigned_mapping":
            receipt = metadata[recovery.LEDGER][0]
            mapping = receipt["mappings"][0]
            old_hash = mapping["mapping_sha256"]
            mapping["mapping_sha256"] = digest(
                {k: v for k, v in mapping.items() if k != "mapping_sha256"}
            )
            receipt["plan_sha256"] = digest(
                {k: v for k, v in receipt.items() if k != "plan_sha256"}
            )
            row = await session.get(WorldRuleModel, UUID(mapping["target_row_id"]))
            row_metadata = deepcopy(row.metadata_json)
            for entry in row_metadata["story_bible_update_history"]:
                if entry.get(recovery.ORIGIN) == old_hash:
                    entry[recovery.ORIGIN] = mapping["mapping_sha256"]
            row.metadata_json = row_metadata
        project.metadata_json = metadata
    elif damage == "raw":
        chapter = await session.get(ChapterModel, UUID("4902a4a2-36bf-4296-b26c-0bfe21775e4a"))
        metadata = deepcopy(chapter.metadata_json)
        metadata["story_bible_update_receipts"][1]["raw_extractions"][0] += " "
        chapter.metadata_json = metadata
    else:
        mapping = plan["mappings"][0]
        row = await session.get(
            WorldRuleModel,
            UUID(mapping["old_row_id"] if damage == "old_history" else mapping["target_row_id"]),
        )
        metadata = deepcopy(row.metadata_json)
        history = metadata["story_bible_update_history"]
        if damage == "orphan":
            history.append({"update": {"rule_or_location": row.name}, recovery.ORIGIN: "fake"})
        elif damage == "copy":
            history[-1]["update"]["description"] += "tampered"
        else:
            history[mapping["history_index"]]["description_applied"] = "tampered"
        row.metadata_json = metadata
    await session.commit()
    before = await retained_raw(session, project)
    with pytest.raises(projection.BibleProjectionAttributionError):
        await projection.reproject_adopted_bible(session, project)
    assert await retained_raw(session, project) == before


async def test_unicode_normalization_and_code_fallback_are_identity_checked(db):
    session, project, _, _ = db
    assert bible.chapter_world_rule_code("  café  ") == bible.chapter_world_rule_code("cafe\u0301")
    row = WorldRuleModel(
        id=uuid4(),
        project_id=project.id,
        name="First",
        rule_code="same_code",
        description="Untouched",
        metadata_json={},
    )
    session.add(row)
    await session.flush()
    with pytest.raises(ValueError, match="world_rule_identity_code_conflict"):
        await bible._get_world_rule_by_identity(
            session, project_id=project.id, rule_id=uuid4(), rule_code="same_code", name="Second"
        )
    assert row.name == "First" and row.description == "Untouched"


async def test_migrated_and_existing_same_source_updates_replay_in_original_payload_order(db):
    from bestseller.services import world_rule_identity_recovery as recovery
    from bestseller.services.feedback import validate_adopted_knowledge_source

    session, project, chapter, draft = db
    source, rejection = await validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
    )
    assert rejection is None
    payload = {
        "world_updates": [
            {"rule_or_location": "Target", "description": "BBBB"},
            {"rule_or_location": "Target", "description": "AAAA"},
        ]
    }
    chapter.metadata_json = {
        "story_bible_update_receipts": [
            {
                "status": "ok",
                "source_lineage": source,
                "candidate_payload": payload,
                "raw_extractions": [json.dumps(payload)],
            }
        ]
    }
    target = WorldRuleModel(
        id=uuid4(),
        project_id=project.id,
        rule_code="AUTHORED",
        name="Target",
        description="AAAA",
        metadata_json={},
    )
    wrong = WorldRuleModel(
        id=uuid4(),
        project_id=project.id,
        rule_code="WRONG",
        name="Wrong",
        description="BBBB",
        metadata_json={},
    )
    for row, index in [(target, 1), (wrong, 0)]:
        projection.capture_bible_baseline(row, created=True)
        row.metadata_json = {
            **row.metadata_json,
            "story_bible_update_history": [
                {
                    "source_lineage": source,
                    "description_applied": True,
                    "update": deepcopy(payload["world_updates"][index]),
                }
            ],
        }
    session.add_all([target, wrong])
    await session.commit()
    plan = await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
    await recovery.apply_world_rule_identity_recovery(
        session, project_id=project.id, expected_plan_sha256=plan["plan_sha256"]
    )
    assert target.description == "BBBB"  # strict longer policy: first equal-length value wins


@pytest.mark.parametrize("placement", ["same_history", "other_wrong_row", "correct_target_row"])
@pytest.mark.parametrize("phase", ["plan", "replay"])
async def test_same_raw_occurrence_cannot_justify_multiple_original_contributions(
    db, placement, phase
):
    from bestseller.services import world_rule_identity_recovery as recovery

    session, project, _ = await actual_retained_case(db)
    if phase == "replay":
        plan = await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
        await recovery.apply_world_rule_identity_recovery(
            session, project_id=project.id, expected_plan_sha256=plan["plan_sha256"]
        )
        await session.commit()
    old = await session.get(WorldRuleModel, UUID("5d7bde39-381a-5083-8088-a1eb0faa6d64"))
    entry = deepcopy(old.metadata_json["story_bible_update_history"][1])
    if placement == "same_history":
        old.metadata_json = {
            **old.metadata_json,
            "story_bible_update_history": [
                *projection._history(old),
                entry,
            ],
        }
    elif phase == "replay" and placement == "correct_target_row":
        target = await session.scalar(
            select(WorldRuleModel).where(
                WorldRuleModel.project_id == project.id,
                WorldRuleModel.name == entry["update"]["rule_or_location"],
            )
        )
        target.metadata_json = {
            **target.metadata_json,
            "story_bible_update_history": [
                *projection._history(target),
                entry,
            ],
        }
    else:
        name = (
            "Another wrong row"
            if placement == "other_wrong_row"
            else entry["update"]["rule_or_location"]
        )
        row = WorldRuleModel(
            id=uuid4(),
            project_id=project.id,
            rule_code="DUPLICATE",
            name=name,
            description=entry["update"]["description"],
            metadata_json={},
        )
        projection.capture_bible_baseline(row, created=True)
        row.metadata_json = {**row.metadata_json, "story_bible_update_history": [entry]}
        session.add(row)
    await session.commit()
    before = await retained_raw(session, project)
    with pytest.raises(projection.BibleProjectionAttributionError):
        if phase == "plan":
            await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
        else:
            await recovery.validated_world_rule_identity_mappings(session, project_id=project.id)
    assert await retained_raw(session, project) == before


@pytest.mark.parametrize("target_kind", ["created", "authored"])
async def test_target_baseline_pin_blocks_mutation_and_withdrawal_revival(db, target_kind):
    from bestseller.services import world_rule_identity_recovery as recovery

    session, project, _ = await actual_retained_case(db)
    name = "临时外接电机播放"
    if target_kind == "authored":
        session.add(
            WorldRuleModel(
                id=uuid4(),
                project_id=project.id,
                rule_code="AUTHORED-PIN",
                name=name,
                description="Preserve this authored baseline",
                metadata_json={"opaque": True},
            )
        )
        await session.commit()
    plan = await recovery.plan_world_rule_identity_recovery(session, project_id=project.id)
    await recovery.apply_world_rule_identity_recovery(
        session, project_id=project.id, expected_plan_sha256=plan["plan_sha256"]
    )
    await session.commit()
    target = await session.scalar(
        select(WorldRuleModel).where(
            WorldRuleModel.project_id == project.id, WorldRuleModel.name == name
        )
    )
    metadata = deepcopy(target.metadata_json)
    metadata[projection.BASE]["fields"]["description"] = "Stale truth may never revive"
    target.metadata_json = metadata
    await session.commit()
    before = await retained_raw(session, project)
    with pytest.raises(projection.BibleProjectionAttributionError):
        await projection.reproject_adopted_bible(session, project)
    assert await retained_raw(session, project) == before
    chapter = await session.get(ChapterModel, UUID("4902a4a2-36bf-4296-b26c-0bfe21775e4a"))
    draft = await session.get(
        ChapterDraftVersionModel, UUID("32073a40-6d10-4395-bcd4-c454accadb45")
    )
    block = {
        "draft_id": str(draft.id),
        "body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
        "passed": False,
    }
    chapter.metadata_json = {
        **chapter.metadata_json,
        "continuity_admission_by_source": {
            f"{draft.id}:{block['body_sha256']}": block,
        },
    }
    await session.commit()
    before = await retained_raw(session, project)
    with pytest.raises(projection.BibleProjectionAttributionError):
        await projection.reproject_adopted_bible(session, project)
    assert await retained_raw(session, project) == before
