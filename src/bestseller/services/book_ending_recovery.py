"""Finite, body-bound ending repair; no literary-score polishing or source retcon."""

from copy import deepcopy
from pathlib import Path
import hashlib
from bestseller.services import draft_continuity as dc
from bestseller.services.prompt_source_policy import content_hash


async def _input(session, project, book, draft_for):
    selected = deepcopy(book.metadata_json["selected"])
    total = book.metadata_json["binding"]["target_chapters"]
    if set(selected) != set(map(str, range(1, total + 1))):
        raise ValueError("book_ending_missing_chapters")
    bodies = []
    for n in range(1, total + 1):
        _, draft = await draft_for(session, project, book, n)
        if (
            str(draft.id) != selected[str(n)]["draft_id"]
            or dc.body_hash(draft.content_md) != selected[str(n)]["body_sha256"]
        ):
            raise ValueError("book_ending_working_body_not_selected")
        bodies.append(
            {
                "chapter_number": n,
                "body_sha256": dc.body_hash(draft.content_md),
                "body": draft.content_md,
            }
        )
    from bestseller.services import book_native

    evaluator_policy = content_hash(
        {
            Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in (__file__, book_native.__file__)
        }
    )
    payload = {
        "selected_manifest": selected,
        "manifest_sha256": content_hash(selected),
        "ending_evaluator_policy_sha256": evaluator_policy,
        "full_selected_manuscript": bodies,
        "task": "核查全书主要冲突的实际结局与人物最终状态。普通文风/商业问题写debts；只有已证实的缺结局或来源/状态硬矛盾写hard_blockers。不猜缺失来源，无法核实写unknown。已知问题的repair_targets必须指出对应原文chapter_number与body_sha256、覆盖的hard_blockers零起始索引；若缺实际结局，标ending_incomplete=true并指向末章。无法定位就不要捏造目标。成功时repair_targets必须为空。",
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "manifest_sha256",
            "ending_complete",
            "hard_blockers",
            "unknown",
            "debts",
            "repair_targets",
        ],
        "properties": {
            "manifest_sha256": {"type": "string", "enum": [content_hash(selected)]},
            "ending_complete": {"type": "boolean"},
            **{
                k: {"type": "array", "items": {"type": "string"}}
                for k in ("hard_blockers", "unknown", "debts")
            },
            "repair_targets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "chapter_number",
                        "body_sha256",
                        "blocker_indexes",
                        "ending_incomplete",
                    ],
                    "properties": {
                        "chapter_number": {"type": "integer", "minimum": 1, "maximum": total},
                        "body_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                        "blocker_indexes": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0},
                            "uniqueItems": True,
                        },
                        "ending_incomplete": {"type": "boolean"},
                    },
                },
            },
        },
    }
    return payload, schema


def _positive(data):
    return (
        data["ending_complete"] is True
        and not data["hard_blockers"]
        and not data["unknown"]
        and not data["repair_targets"]
    )


async def verify(session, settings, *, project, book, draft_for):
    from bestseller.services.book_native import call

    payload, schema = await _input(session, project, book, draft_for)
    data, evidence = await call(
        session,
        settings,
        project=project,
        template="book_ending",
        payload=payload,
        schema=schema,
        allow_generate=False,
    )
    if not _positive(data):
        raise ValueError("book_ending_existing_evidence_not_positive")
    return {
        "ending_verified": True,
        "manifest_sha256": payload["manifest_sha256"],
        "evidence": evidence,
        "debts": data["debts"],
    }


async def close(session, settings, *, project, book, draft_for, review, knowledge):
    from bestseller.services.book_native import call
    from bestseller.services.book_hard_repair import repair

    total = book.metadata_json["binding"]["target_chapters"]
    while True:
        payload, schema = await _input(session, project, book, draft_for)
        data, evidence = await call(
            session,
            settings,
            project=project,
            template="book_ending",
            payload=payload,
            schema=schema,
        )
        if data["unknown"]:
            raise ValueError("book_ending_unknown")
        if _positive(data):
            return {
                "ending_verified": True,
                "manifest_sha256": payload["manifest_sha256"],
                "evidence": evidence,
                "debts": data["debts"],
            }
        if data["ending_complete"] and not data["hard_blockers"]:
            raise ValueError("book_ending_verdict_conflict")
        targets = data["repair_targets"]
        chapter, draft = await draft_for(session, project, book, total)
        if (
            len(targets) != 1
            or targets[0]["chapter_number"] != total
            or targets[0]["body_sha256"] != dc.body_hash(draft.content_md)
        ):
            raise ValueError("book_ending_hard_block_requires_prior_or_unscoped_repair")
        target = targets[0]
        if sorted(target["blocker_indexes"]) != list(range(len(data["hard_blockers"]))) or target[
            "ending_incomplete"
        ] != (not data["ending_complete"]):
            raise ValueError("book_ending_hard_evidence_coverage_missing")
        old_manifest = deepcopy(book.metadata_json["selected"])
        old_review = deepcopy(book.metadata_json["outputs"].get(f"{total}:review"))
        old_knowledge = deepcopy(book.metadata_json["outputs"].get(f"{total}:knowledge"))
        # Durable intention and the original terminal proof survive every substep.
        active = {
            "old_manifest": old_manifest,
            "ending_evidence": evidence,
            "ending_report": data,
            "body_sha256": dc.body_hash(draft.content_md),
            "phase": "known_hard_repair",
        }
        book.metadata_json = {**book.metadata_json, "ending_recovery_active": active}
        await session.commit()
        await repair(
            session,
            settings,
            project=project,
            chapter=chapter,
            draft=draft,
            book=book,
            evidence={
                "schema": "book-ending-known-hard.v1",
                "manifest_sha256": payload["manifest_sha256"],
                "physical_ending_evidence": evidence,
                "assessment": data,
            },
        )
        result = await review(session, project, book, {"chapter_number": total})
        source = result.get("qualified_source")
        if not source:
            raise ValueError("book_ending_repair_source_unqualified")
        book.metadata_json = {
            **book.metadata_json,
            "selected": {**book.metadata_json["selected"], str(total): source},
        }
        fresh_knowledge = await knowledge(session, project, book, {"chapter_number": total})
        history = {
            **active,
            "phase": "source_qualified_and_reindexed",
            "new_manifest": deepcopy(book.metadata_json["selected"]),
            "old_review": old_review,
            "old_knowledge": old_knowledge,
            "new_review": result,
            "new_knowledge": fresh_knowledge,
        }
        book.metadata_json = {
            **book.metadata_json,
            "ending_recovery_active": None,
            "outputs": {
                **book.metadata_json["outputs"],
                f"{total}:review": result,
                f"{total}:knowledge": fresh_knowledge,
            },
            "ending_recovery_history": [
                *book.metadata_json.get("ending_recovery_history", []),
                history,
            ],
        }
        await session.commit()
        # A changed manifest requires a real new ending review. It is not a pass
        # copied from the previous body, and repeated requests consume the finite pool.
