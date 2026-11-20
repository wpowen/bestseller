"""Complete missing relationship obligations without replacing cached plans or designs."""

from copy import deepcopy
import json
from pathlib import Path

from .chapter_rhythm import normalize_chapter_type, relationship_contract, relationship_outline_issues
from .longutil import ask
from .promise_io import precise_ask
from .rhythm_model import ask_once
from .q1_schema import integer, rows
from .storage import load, save
from tools.runtime_support import ProjectError, QuotaExhausted


def planned_type_source(outline):
    """Report the real planning field, never an observed/extractor contract override."""
    if relationship_contract(outline):
        return "情感", "outline.relationship_change"
    for key in ("type", "rhythm_type"):
        if kind := normalize_chapter_type(outline.get(key)):
            return kind, "outline." + key
    return "", "unknown"


def _directory(client):
    out = getattr(client, "out", None)
    return Path(out) / "rhythm-revisions" if out else None


def _design_contract(bible, outline, number):
    stages = [s for s in rows(bible.get("volumes")) if integer(s.get("start")) <= number <= integer(s.get("end"))]
    if len(stages) != 1:
        return None, ""
    stage = integer(stages[0].get("number"))
    design = bible.get("story_design") or {}
    known = {c.get("name") for c in rows(bible.get("characters"))}
    cast = {
        name for scene in rows(outline.get("scenes")) for name in scene.get("characters", []) if isinstance(name, str)
    }
    candidates = []
    for i, row in enumerate(rows(design.get("relationships"))):
        for j, step in enumerate(rows(row.get("steps"))):
            if integer(step.get("stage")) != stage:
                continue
            contract = relationship_contract(
                dict(
                    relationship_change=dict(
                        people=row.get("people"),
                        cause=step.get("cause") or step.get("event"),
                        before=step.get("before"),
                        after=step.get("after"),
                    )
                )
            )
            if not contract or (known and not set(contract["people"]) <= known):
                continue
            if cast and not set(contract["people"]) <= cast:
                continue
            candidates.append((contract, f"bible.story_design.relationships[{i}].steps[{j}]"))
    unique = {json.dumps(c, sort_keys=True, ensure_ascii=False): (c, source) for c, source in candidates}
    if len(unique) == 1:
        return next(iter(unique.values()))
    # A pair present in both main scenes is stronger than a supporting cast mention.
    scores = [
        (
            sum(set(c["people"]) <= set(scene.get("characters", [])) for scene in rows(outline.get("scenes"))[:2]),
            c,
            source,
        )
        for c, source in unique.values()
    ]
    best = [row for row in scores if row[0] == max((s[0] for s in scores), default=0)]
    return (best[0][1], best[0][2]) if len(best) == 1 and best[0][0] == 2 else (None, "")


_SCENE_TEXT = ("goal", "obstacle", "choice", "action", "result")


def _cast_named_in_text(scenes, people):
    """Main scenes whose text names a contract person but whose cast list omits them (eval-72 ch14)."""
    result = deepcopy(scenes)
    for scene in rows(result)[:2]:
        cast = scene.get("characters") if isinstance(scene.get("characters"), list) else []
        text = "".join(str(scene.get(key) or "") for key in _SCENE_TEXT)
        missing = [name for name in people if name not in cast and name in text]
        if missing:
            scene["characters"] = cast + missing
    return result


def _scene_patch(original, response, people):
    """Only action/choice/goal/result may change inside existing scene objects."""
    supplied = response.get("scenes")
    if supplied is None:
        return deepcopy(original.get("scenes", []))
    scenes = original.get("scenes")
    if not isinstance(scenes, list) or not isinstance(supplied, list) or len(scenes) != len(supplied):
        return None
    result = _cast_named_in_text(scenes, people)
    for old, new in zip(result, supplied):
        if not isinstance(old, dict) or not isinstance(new, dict):
            return None
        if "characters" in new:
            cast, supplied_cast = old.get("characters") or [], new["characters"]
            # The model may echo the planned cast or add the contract pair; it may not drop or invent anyone.
            if not isinstance(supplied_cast, list) or not set(supplied_cast) <= set(cast) | set(people):
                return None
            old["characters"] = list(dict.fromkeys(cast + supplied_cast))
        for key in ("where", "location"):
            if key in new and new[key] != old.get(key):
                return None
        for key in ("goal", "action", "choice", "result"):
            if key in new:
                if not isinstance(new[key], str) or not new[key].strip():
                    return None
                old[key] = new[key]
    if result and not set(people) <= {n for s in result for n in s.get("characters", [])}:
        return None
    return result


def _complete(client, bible, original, state, number, kind):
    result = deepcopy(original)
    planned, origin = planned_type_source(original)
    note = dict(
        status="unchanged",
        planned_type=planned,
        planned_type_source=origin,
        source=origin,
        writer_ready=planned != "情感"
        or bool(relationship_contract(original) and not relationship_outline_issues(original)),
        fulfilled=False,
        model_attempted=False,
    )
    if planned == "情感" and relationship_contract(original) and isinstance(result.get("scenes"), list):
        result["scenes"] = _cast_named_in_text(result.get("scenes", []), relationship_contract(original)["people"])
    if planned != "情感" or note["writer_ready"]:
        result.setdefault("rhythm_contract_repair", note)
        return result
    if not (bible.get("story_design") or bible.get("story_layer_version")):
        note.update(status="skipped", reason="legacy_without_story_design")
        result["rhythm_contract_repair"] = note
        return result
    source_outline = {k: v for k, v in original.items() if k != "rhythm_contract_repair"}
    directory = _directory(client)
    attempt = directory / f"contract-attempt-{number:03d}.json" if directory else None
    revisions = directory / "contracts" if directory else None
    memory = getattr(client, "_rhythm_contract_attempts", {})
    if client is not None and not hasattr(client, "_rhythm_contract_attempts"):
        client._rhythm_contract_attempts = memory
    previous = load(attempt) if attempt and attempt.is_file() else memory.get(number)
    contract = relationship_contract(original)
    source = origin
    if not contract:
        contract, source = _design_contract(bible, original, number)
    locked_contract = contract
    if contract:
        result["relationship_change"] = contract
        result["scenes"] = _cast_named_in_text(result.get("scenes", []), contract["people"])
        note.update(status="restored", source=source, writer_ready=not relationship_outline_issues(result))
    if not note["writer_ready"] and previous and previous["note"].get("status") not in ("waiting", "attempting"):
        note.update(previous["note"])
        if previous.get("original") == source_outline:
            result.update(deepcopy(previous.get("patch", {})))
            note["reused"] = True
        else:
            note.update(
                status="failed",
                reason="already_attempted",
                writer_ready=False,
                source_failure_reason=previous["note"].get("reason"),
            )
            saved = relationship_contract(
                dict(relationship_change=previous.get("patch", {}).get("relationship_change"))
            )
            if saved and not relationship_contract(result):
                result["relationship_change"] = saved
    elif not note["writer_ready"]:
        note.update(
            status="attempting",
            source="engine.targeted_planning",
            model_attempted=True,
            reason="source_model_interrupted",
            contract_source=source or "unavailable",
        )
        memory[number] = dict(original=source_outline, note=deepcopy(note), patch={})
        if attempt:
            save(attempt, memory[number])

        # A single engine stage, using the normal client/accounting and explicit complete input.
        def bounded_ask(*args, **kwargs):
            return ask_once(ask, *args, **kwargs)

        try:
            if client is None:
                raise ValueError("source_client_unavailable")
            response = precise_ask(
                bounded_ask,
                client,
                f"rhythm-contract-{number}-repair",
                (
                    "relationship_change已定（只读，原样返回）；只改两场主戏的goal/action/choice/result，"
                    "让主要动作和选择落在这两人的关系变化上，可把这两人补进该场characters；"
                    if locked_contract
                    else "只补该章空的relationship_change：两名已登记且本章可出场人物、起因、不同的before/after。"
                    "仅在必要时改现有scenes的goal/action/choice/result，使两场主戏落实关系变化；"
                )
                + "返回相同数量的scenes，不删改已有人物、不改地点、标题、章型、兑现、末钩及其他字段。不把计划标为已兑现。",
                [
                    ("目标章纲完整只读", result),
                    (
                        "固定人物",
                        [
                            {k: card.get(k) for k in ("name", "identity", "role")}
                            for card in rows(bible.get("characters"))
                        ],
                    ),
                    (
                        "本阶段关系设计",
                        [
                            dict(
                                people=row.get("people"),
                                steps=[
                                    step
                                    for step in rows(row.get("steps"))
                                    if any(
                                        integer(stage.get("start")) <= number <= integer(stage.get("end"))
                                        and integer(step.get("stage")) == integer(stage.get("number"))
                                        for stage in rows(bible.get("volumes"))
                                    )
                                ],
                            )
                            for row in rows(bible.get("story_design", {}).get("relationships"))
                        ],
                    ),
                    ("当前实际状态只读", {k: state.get(k) for k in ("dashboard", "_dead", "end_location")}),
                ],
                '{"relationship_change":{"people":["人物甲","人物乙"],"cause":"起因",'
                '"before":"变化前","after":"变化后"},"scenes":[]}',
                cap=2500,
                required_sources=[json.dumps(result, ensure_ascii=False, separators=(",", ":"))],
            )
            response = response if isinstance(response, dict) else {}
            contract = relationship_contract(dict(relationship_change=response.get("relationship_change")))
            if locked_contract:
                # The outline already decided the relationship; a reworded copy from the model is not a new
                # decision and must not void its scene fixes (eval-72: the book stopped at chapter 14).
                contract = locked_contract
            known = {c.get("name") for c in rows(bible.get("characters"))}
            if contract and known and not set(contract["people"]) <= known:
                contract = None
            scenes = _scene_patch(original, response, contract["people"]) if contract else None
            note["status"] = "failed"
            if response.get("rhythm_model_failure"):
                note["reason"] = "source_model_failed:" + response["rhythm_model_failure"]
            elif response.get("context_unknown") or response.get("status") == "skipped":
                note.update(reason="incomplete_source_context", model_attempted=False)
            elif not contract:
                note["reason"] = "invalid_model_contract"
            elif scenes is None:
                note["reason"] = "model_scene_scope_violation"
            elif relationship_outline_issues(dict(result, relationship_change=contract, scenes=scenes)):
                note["reason"] = "model_relationship_scenes_invalid"
            else:
                result.update(relationship_change=contract, scenes=scenes)
                note.update(status="repaired", writer_ready=True)
                note.pop("reason", None)
            if note["status"] == "failed" and contract:
                # Instruction 79: keep a usable contract even when the scenes miss the check; the writer
                # still gets the obligation and the shortfall stays on record.
                result["relationship_change"] = contract  # the rejected scene text is not applied
                note["degraded"] = True
        except (QuotaExhausted, ProjectError) as exc:
            note.update(status="waiting", reason="source_model_failed:" + type(exc).__name__)
            memory[number] = dict(original=source_outline, note=deepcopy(note), patch={})
            if attempt:
                save(attempt, memory[number])
            raise
        except InterruptedError:
            note.update(status="waiting", reason="source_model_interrupted")
            memory[number] = dict(original=source_outline, note=deepcopy(note), patch={})
            if attempt:
                save(attempt, memory[number])
            raise
        except (ValueError, TypeError) as exc:
            note["status"] = "failed"
            note["reason"] = "source_model_failed:" + type(exc).__name__
        patch = (
            {key: result[key] for key in ("relationship_change", "scenes") if key in result}
            if note["writer_ready"] or note.get("degraded")
            else {}
        )
        memory[number] = dict(original=source_outline, note=deepcopy(note), patch=patch)
        if attempt:
            save(attempt, memory[number])
    result["rhythm_contract_repair"] = note
    if revisions:
        saved = list(revisions.glob(f"{number:03d}-*.json"))
        if not any(load(path).get("original") == source_outline for path in saved):
            revision = revisions / f"{number:03d}-{len(saved) + 1:03d}.json"
            save(revision, dict(original=source_outline, outline=result, note=note))
    if kind == "writer" and not note["writer_ready"] and relationship_contract(result):
        # A contract exists; only the main-scene wording missed the check. Write with the obligation and
        # keep the shortfall on record instead of stopping a book that has to be finished.
        note.update(writer_proceeded_with_contract=True, degraded=True)
        result["rhythm_contract_repair"] = note
    elif kind == "writer" and not note["writer_ready"]:
        # Instruction 79 (user 2026-11-20: “修补失败就降级续写”): a book has to be finished, so a failed
        # relationship repair is recorded and the chapter is written from its plan instead of stopping.
        note.update(writer_proceeded_without_contract=True, degraded=True)
        result["rhythm_contract_repair"] = note
    return result


def apply_rhythm_contracts(client, bible, value, state, *, chapter=None, kind="outlines"):
    """Accept a single outline/list/chapters envelope. Run life constraints AFTER this helper.

    Use kind='writer' for the pre-draft fallback: an exhausted empty contract raises a recorded
    ProjectError rather than silently writing without a relationship obligation.
    """
    if isinstance(value, list):
        return [apply_rhythm_contracts(client, bible, row, state, kind=kind) for row in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value.get("chapters"), list):
        result = deepcopy(value)
        result["chapters"] = apply_rhythm_contracts(client, bible, value["chapters"], state, kind=kind)
        return result
    number = integer(value.get("number"), integer(chapter))
    return _complete(client, bible, value, state, number, kind)
