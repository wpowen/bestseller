"""Actual isolated PG source reads under distinct plans; no adoption authority."""

# ruff: noqa: E402 -- audit hook precedes app imports.
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import traceback
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

HOST = urlsplit(os.environ["BESTSELLER__DATABASE__URL"]).hostname
assert HOST.startswith("int-full-flow-")
ips = {x[4][0] for x in socket.getaddrinfo(HOST, None, socket.AF_INET)}


def network_guard(event, args):
    if event == "socket.getaddrinfo" and args[0] not in {HOST, *ips}:
        raise RuntimeError("e21_external_dns_denied")
    if event == "socket.connect" and (
        not isinstance(args[1], tuple) or args[1][0] not in ips or args[1][1] != 5432
    ):
        raise RuntimeError("e21_external_network_denied")


sys.addaudithook(network_guard)

from f0_ordering_cases.f0_ordering_support import source_rows, uid
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db import models as m
from bestseller.services import context, feedback, llm, story_bible
from bestseller.services.chapter_generation_input_builder import (
    build_chapter_generation_input_bundle,
)
from bestseller.services.commercial_review_context import project_commercial_source
from bestseller.services.retained_review_input_proof import _ReadOnlySession
from bestseller.settings import load_settings
from support.database_safety import install_database_safety, selected_database_url

OUT = Path(os.environ["FULL_FLOW_E21_EVIDENCE"])
SCHEMA = "f0_e21_" + uuid4().hex
assert re.fullmatch(r"f0_e21_[a-f0-9]{32}", SCHEMA)
TABLES = {
    "interpersonal_promises",
    "relationships",
    "characters",
    "expansion_gates",
    "timeline_events",
    "arc_beats",
    "clues",
    "payoffs",
    "plot_arcs",
    "antagonist_plans",
    "retrieval_chunks",
    "reader_knowledge_entries",
    "relationship_events",
}


def encoded(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
    ).encode()


def summary(v):
    return dict(
        type=type(v).__name__,
        length=len(v) if isinstance(v, (str, bytes, list, dict, tuple)) else None,
        sha256=hashlib.sha256(encoded(v)).hexdigest(),
    )


def require(ok, field, expected, actual):
    if not ok:
        err = ValueError("e21_source_contract:" + field)
        err.differences = [dict(field=field, expected=summary(expected), actual=summary(actual))]
        raise err


async def fingerprints(c):
    result = {}
    for table in (
        "projects",
        "chapters",
        "chapter_draft_versions",
        "review_reports",
        "quality_scores",
        "rewrite_tasks",
        "workflow_runs",
        "llm_runs",
        "execution_budgets",
        "execution_operations",
        "execution_attempts",
    ):
        rows = list(
            await c.scalars(
                text(
                    f"SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') FROM public.{table} t ORDER BY 1"
                )
            )
        )
        result[table] = summary(rows)
    return result


async def seed(session):
    p = m.ProjectModel(
        id=uid(1000),
        slug="e21-ordinary-source-rows",
        title="Ordinary source rows",
        genre="test",
        language="en",
        target_word_count=8000,
        target_chapters=80,
        metadata_json={},
    )
    prev = m.ChapterModel(
        id=uid(1001),
        project_id=p.id,
        chapter_number=59,
        title="Earlier",
        chapter_goal="Prior observations",
        metadata_json={},
    )
    ch = m.ChapterModel(
        id=uid(1002),
        project_id=p.id,
        chapter_number=60,
        title="Current",
        chapter_goal="Compare source ordering",
        metadata_json={},
    )
    sc = m.SceneCardModel(
        id=uid(1003),
        project_id=p.id,
        chapter_id=ch.id,
        scene_number=1,
        scene_type="setup",
        title="Current scene",
        participants=["Survivor 3", "Survivor 1", "Survivor 2"],
        purpose={"story": "Compare source ordering"},
        entry_state={},
        exit_state={},
        target_word_count=1500,
        metadata_json={},
    )
    session.add(p)
    await session.flush()
    session.add_all([prev, ch])
    await session.flush()
    session.add(sc)
    await session.flush()
    case = SimpleNamespace(project=p, chapter=ch, previous=prev, scene=sc)
    arcs, beats, events, clues, payoffs = source_rows(case)
    for b in beats:
        b.scope_chapter_number = 60
    for e in events:
        e.story_order = 59.1
    for payoff in payoffs:
        payoff.target_chapter_number = 60
    beats += [
        m.ArcBeatModel(
            id=uid(1210 + i),
            project_id=p.id,
            plot_arc_id=arcs[0].id,
            beat_order=1,
            scope_level=level,
            scope_volume_number=vol,
            scope_chapter_number=chap,
            scope_scene_number=scene_no,
            beat_kind="turn",
            summary=f"cross-scope-{i}",
            status="planned",
            metadata_json={},
        )
        for i, (level, vol, chap, scene_no) in enumerate(
            [
                ("project", None, None, None),
                ("volume", 1, None, None),
                ("scene", 1, 60, 1),
                ("chapter", 1, 59, None),
            ],
            1,
        )
    ]
    session.add_all(arcs)
    await session.flush()
    session.add_all([*beats, *events, *clues, *payoffs])
    await session.flush()
    deceased = [
        m.CharacterModel(
            id=uid(2200 + i),
            project_id=p.id,
            name=f"Deceased {i}",
            role="support",
            alive_status="deceased",
            death_chapter_number=57,
            metadata_json={},
        )
        for i in range(1, 22)
    ]
    protected = [
        m.CharacterModel(
            id=uid(2300 + i),
            project_id=p.id,
            name=f"Protected {i}",
            role="support",
            alive_status="alive",
            death_chapter_number=80,
            metadata_json={},
        )
        for i in range(1, 22)
    ]
    survivors = [
        m.CharacterModel(
            id=story_bible.stable_character_id(p.id, f"Survivor {i}"),
            project_id=p.id,
            name=f"Survivor {i}",
            role="support",
            alive_status="alive",
            metadata_json={},
        )
        for i in range(1, 6)
    ]
    restricted = [
        m.CharacterModel(
            id=uid(2500 + i),
            project_id=p.id,
            name=f"Offstage {i}",
            role="support",
            alive_status="alive",
            metadata_json={"lifecycle_status": {"kind": "missing", "since_chapter": 2}},
        )
        for i in range(1, 4)
    ]
    session.add_all([*deceased, *protected, *survivors, *restricted])
    await session.flush()
    relations = [
        m.RelationshipModel(
            id=uid(2600 + i),
            project_id=p.id,
            character_a_id=deceased[0].id,
            character_b_id=survivor.id,
            relationship_type="ally",
            strength=0.9,
            tension_summary=f"Memory {i}",
            metadata_json={},
        )
        for i, survivor in enumerate(survivors, 1)
    ]
    relations += [
        m.RelationshipModel(
            id=uid(2700 + i),
            project_id=p.id,
            character_a_id=survivors[a].id,
            character_b_id=survivors[b].id,
            relationship_type=f"present-{i}",
            strength=0.8,
            tension_summary=f"Present {i}",
            metadata_json={},
        )
        for i, (a, b) in enumerate([(0, 1), (0, 2), (1, 2)], 1)
    ]
    promises = [
        m.InterpersonalPromiseModel(
            id=uid(2800 + i),
            project_id=p.id,
            promisor_label="A",
            promisee_label="B",
            content=f"promise-{i}",
            status="active",
            metadata_json={},
        )
        for i in range(1, 14)
    ]
    gates = [
        m.ExpansionGateModel(
            id=uid(2900 + i),
            project_id=p.id,
            gate_code=f"gate-{i}",
            label=f"gate-{i}",
            condition_summary="A condition",
            unlocks_summary="Next frontier",
            unlock_chapter_number=70,
            unlock_volume_number=3,
            metadata_json={},
        )
        for i in range(1, 3)
    ]
    plans = [
        m.AntagonistPlanModel(
            id=uid(3000 + i),
            project_id=p.id,
            antagonist_label="Opponent",
            plan_code=f"plan-{i}",
            title=f"plan-{i}",
            goal="Goal",
            current_move="Current",
            next_countermove="Later",
            status="active",
            metadata_json={},
        )
        for i in range(1, 4)
    ]
    session.add_all([*relations, *promises, *gates, *plans])
    # F0-19: cap+1 same-chapter observations, inserted in reverse identity order.
    reader_rows = [m.ReaderKnowledgeEntryModel(
        id=uid(3200+i), project_id=p.id, chapter_number=59,
        knowledge_item=f"reader-{i}", audience="reader_only", metadata_json={})
        for i in range(11, 0, -1)]
    milestones = [m.RelationshipEventModel(
        id=uid(3300+i), project_id=p.id, chapter_number=59, scene_number=1,
        character_a_label="Survivor 1", character_b_label="Survivor 2",
        event_description=f"milestone-{i}", relationship_change="A visible shift",
        is_milestone=True, metadata_json={}) for i in range(11, 0, -1)]
    session.add_all([*reader_rows, *milestones])
    await session.commit()
    return case


async def source_view(session, settings, case):
    scene = await context.build_scene_writer_context_from_models(
        session, settings, case.project, case.chapter, case.scene
    )
    chapter = await context.build_chapter_writer_context(session, settings, case.project.slug, 60)
    inputs = await feedback.load_feedback_prompt_context(
        session, project_id=case.project.id, chapter=case.chapter
    )
    prompts = feedback._build_feedback_extraction_prompts(
        chapter=case.chapter, chapter_md="An ordinary current manuscript.", **inputs
    )
    bundle = build_chapter_generation_input_bundle(
        project=case.project,
        chapter=case.chapter,
        scenes=[case.scene],
        context_packet=chapter,
        target_word_count=2000,
    ).model_dump(mode="json")
    commercial = project_commercial_source(
        bundle,
        chapter_number=60,
        content_md="An ordinary current manuscript.",
        previous_chapters=[],
    )
    return {
        "scene_packet": scene.model_dump(mode="json"),
        "chapter_packet": chapter.model_dump(mode="json"),
        "generation_bundle": bundle,
        "commercial_projection": commercial,
        "feedback_prompts": prompts,
    }


async def main():
    require(
        selected_database_url().database == "full_flow_s01_20261028_test_db",
        "database",
        "full_flow_s01_20261028_test_db",
        selected_database_url().database,
    )
    cleanup = install_database_safety()
    settings = load_settings()
    settings.generation.prompt_context_scenes = 2
    base = create_async_engine(settings.database.url)
    engine = create_async_engine(
        settings.database.url,
        execution_options={"schema_translate_map": {None: SCHEMA}},
        connect_args={"server_settings": {"search_path": SCHEMA + ",public"}},
    )
    counts = Counter()
    captured = []
    mode = "setup"
    no_writes = False
    model_calls = 0
    observations = []
    plan_files = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def audit(conn, cursor, statement, params, ctx, many):
        first = statement.lstrip().split(None, 1)[0].upper()
        counts[first] += 1
        if no_writes:
            require(
                first in {"SELECT", "SHOW", "SET", "EXPLAIN", "SAVEPOINT", "RELEASE", "ROLLBACK"},
                "readonly_sql",
                "read/control SQL without row mutations",
                first,
            )
        if (
            mode in ("index", "sequential")
            and first == "SELECT"
            and re.search(r"\bFROM\b", statement, re.I)
        ):
            if any(re.search(r"\b" + t + r"\b", statement) for t in TABLES):
                captured.append((statement, params))

    async def deny(*a, **k):
        nonlocal model_calls
        model_calls += 1
        raise RuntimeError("e21_model_calls_forbidden")

    llm.complete_text = deny
    feedback.complete_text = deny
    result = dict(
        schema="full-flow-e21-native-source-plans.v1",
        status="失败",
        at_utc=datetime.now(timezone.utc).isoformat(),
        isolated_schema=SCHEMA,
        production_access=False,
        paid_calls=0,
        failed_workflow_type="E21_RF",
        failed_workflow_step="setup",
        root_exception_type="Unknown",
        root_error_code="Unknown",
        last_successful_llm_template="Unknown: zero calls",
        script_consumed_index=0,
        traceback_path="Unknown",
        boundary="Ordinary persistent source rows; no grade, adopted draft, review or paid authority seeded. No complete-book-prefix claim.",
    )
    before = None
    (OUT / "plans").mkdir(exist_ok=True)
    try:
        async with base.connect() as c:
            before = await fingerprints(c)
        async with engine.begin() as c:
            await c.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
            await c.run_sync(m.Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            case = await seed(s)
            result["failed_workflow_step"] = "warm_native_context_caches"
            await source_view(s, settings, case)
            await s.commit()
        observations = []
        payloads = []
        for chosen in ("index", "sequential"):
            mode = "configure"
            captured.clear()
            no_writes = True
            async with factory() as s:
                await s.execute(text("SET TRANSACTION READ ONLY"))
                for key, value in {
                    "enable_seqscan": "off" if chosen == "index" else "on",
                    "enable_indexscan": "on" if chosen == "index" else "off",
                    "enable_indexonlyscan": "on" if chosen == "index" else "off",
                    "enable_bitmapscan": "off",
                }.items():
                    await s.execute(text(f"SET LOCAL {key}={value}"))
                case = SimpleNamespace(
                    project=await s.get(m.ProjectModel, uid(1000)),
                    chapter=await s.get(m.ChapterModel, uid(1002)),
                    scene=await s.get(m.SceneCardModel, uid(1003)),
                )
                mode = chosen
                result["failed_workflow_step"] = chosen + "_native_full_source"
                payload = await source_view(_ReadOnlySession(s), settings, case)
                require(
                    not s.new and not s.dirty and not s.deleted,
                    "session_pending",
                    False,
                    bool(s.new or s.dirty or s.deleted),
                )
                mode = "explain"
                c = await s.connection()
                queries = {}
                for statement, params in captured:
                    key = hashlib.sha256(encoded([statement, params])).hexdigest()
                    queries.setdefault(key, (statement, params))
                query_evidence = []
                for key, (statement, params) in queries.items():
                    plan = (
                        await c.exec_driver_sql("EXPLAIN (FORMAT JSON) " + statement, params)
                    ).scalar_one()
                    nodes = []

                    def walk(n):
                        nodes.append(n["Node Type"])
                        for child in n.get("Plans", []):
                            walk(child)

                    walk(plan[0]["Plan"])
                    # Persist every received plan BEFORE assertions or subsequent reads.
                    directory = OUT / "plans" / chosen
                    directory.mkdir(exist_ok=True)
                    plan_file = directory / (key + ".json")
                    plan_record = {
                        "mode": chosen,
                        "query_key": key,
                        "sql_sha256": hashlib.sha256(statement.encode()).hexdigest(),
                        "parameters": summary(params),
                        "nodes": nodes,
                        "full_explain": plan,
                    }
                    plan_file.write_text(
                        json.dumps(plan_record, ensure_ascii=False, indent=2, default=str) + "\n"
                    )
                    plan_files.append(
                        {
                            "mode": chosen,
                            "query_key": key,
                            "path": str(plan_file.relative_to(OUT)),
                            "nodes": nodes,
                            "sha256": hashlib.sha256(plan_file.read_bytes()).hexdigest(),
                        }
                    )
                    (OUT / "EXPLAIN-MANIFEST.json").write_text(
                        json.dumps(plan_files, ensure_ascii=False, indent=2) + "\n"
                    )
                    # Constant-false/empty subqueries need no table scan; evidence records them.
                    require(
                        any("Index" in n for n in nodes)
                        if chosen == "index"
                        else ("Seq Scan" in nodes or nodes == ["Result"]),
                        "query_plan." + key,
                        chosen,
                        nodes,
                    )
                    values = [
                        dict(row._mapping)
                        for row in (await c.exec_driver_sql(statement, params)).all()
                    ]
                    query_evidence.append(
                        {
                            "query_key": key,
                            "tables": sorted(
                                t for t in TABLES if re.search(r"\b" + t + r"\b", statement)
                            ),
                            "plan_nodes": nodes,
                            "rows": summary(values),
                            "row_count": len(values),
                            "explicit_order": bool(re.search(r"\bORDER BY\b", statement)),
                        }
                    )
                # Complete arrays remain ordered; only JSON object keys are canonical.
                raw = encoded(payload)
                (OUT / (chosen + "-source.json")).write_bytes(raw)
                observations.append(
                    {
                        "mode": chosen,
                        "source_bytes": {
                            "type": "bytes",
                            "length": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                        },
                        "queries": query_evidence,
                    }
                )
                payloads.append(payload)
                (OUT / (chosen + "-OBSERVATIONS.json")).write_text(
                    json.dumps(observations[-1], ensure_ascii=False, indent=2) + "\n"
                )
                await s.rollback()
        for key, event_limit, clue_limit, payoff_limit in [
            ("scene_packet", 2, 6, 4),
            ("chapter_packet", 4, 8, 6),
        ]:
            packet = payloads[0][key]
            bible = packet["story_bible"]
            require(
                [x["content"] for x in bible["interpersonal_promises"]]
                == [f"promise-{i}" for i in range(1, 13)],
                key + ".promises.cap12",
                [f"promise-{i}" for i in range(1, 13)],
                bible["interpersonal_promises"],
            )
            for field, prefix in [
                ("deceased_characters", "Deceased"),
                ("protected_characters", "Protected"),
            ]:
                require(
                    [x["name"] for x in bible[field]] == [f"{prefix} {i}" for i in range(1, 21)],
                    key + "." + field + ".cap20",
                    [f"{prefix} {i}" for i in range(1, 21)],
                    bible[field],
                )
            require(
                [x["survivor_name"] for x in bible["memory_recall_cues"]]
                == [f"Survivor {i}" for i in range(1, 5)],
                key + ".memory.cap4",
                [f"Survivor {i}" for i in range(1, 5)],
                bible["memory_recall_cues"],
            )
            require(
                bible["next_expansion_gate"]["gate_code"] == "gate-1",
                key + ".gate.cap1",
                "gate-1",
                bible["next_expansion_gate"],
            )
            require(
                [x["event_name"] for x in packet["recent_timeline_events"]]
                == [f"event-{i}" for i in range(5, 5 - event_limit, -1)],
                key + ".timeline.cap",
                [f"event-{i}" for i in range(5, 5 - event_limit, -1)],
                packet["recent_timeline_events"],
            )
            require(
                [x["clue_code"] for x in packet["unresolved_clues"]]
                == [f"clue-{i}" for i in range(1, clue_limit + 1)],
                key + ".clue.cap",
                [f"clue-{i}" for i in range(1, clue_limit + 1)],
                packet["unresolved_clues"],
            )
            require(
                [x["payoff_code"] for x in packet["planned_payoffs"]]
                == [f"payoff-{i}" for i in range(1, payoff_limit + 1)],
                key + ".payoff.cap",
                [f"payoff-{i}" for i in range(1, payoff_limit + 1)],
                packet["planned_payoffs"],
            )
            require(
                packet["structure_beat_name"] == "structure-1",
                key + ".structure.first_wins",
                "structure-1",
                packet["structure_beat_name"],
            )
        require(
            len(payloads[0]["scene_packet"]["active_arc_beats"]) == 6,
            "scene.arc_beats.cap6",
            6,
            len(payloads[0]["scene_packet"]["active_arc_beats"]),
        )
        result["failed_workflow_step"] = "compare_complete_source_bytes"
        if encoded(payloads[0]) != encoded(payloads[1]):
            differences = []

            def compare(a, b, path):
                if type(a) is not type(b):
                    differences.append(
                        {"field": path, "expected": summary(a), "actual": summary(b)}
                    )
                elif isinstance(a, dict):
                    for name in sorted(a.keys() | b.keys()):
                        if name not in a or name not in b:
                            differences.append(
                                {
                                    "field": path + "." + name,
                                    "expected": summary(a.get(name)),
                                    "actual": summary(b.get(name)),
                                }
                            )
                        else:
                            compare(a[name], b[name], path + "." + name)
                elif isinstance(a, list):
                    if len(a) != len(b):
                        differences.append(
                            {
                                "field": path + ".length",
                                "expected": summary(len(a)),
                                "actual": summary(len(b)),
                            }
                        )
                    for i, (left, right) in enumerate(zip(a, b)):
                        compare(left, right, path + "[" + str(i) + "]")
                elif a != b:
                    differences.append(
                        {"field": path, "expected": summary(a), "actual": summary(b)}
                    )

            compare(payloads[0], payloads[1], "$")
            (OUT / "SOURCE-DIFFERENCES.json").write_text(
                json.dumps(differences, ensure_ascii=False, indent=2) + "\n"
            )
            error = ValueError("e21_source_contract:complete_source_bytes")
            error.differences = differences
            raise error
        for key, cap in (("scene_packet", 8), ("chapter_packet", 10)):
            require([row["knowledge_item"] for row in payloads[0][key]["reader_knowledge_entries"]]
                    == [f"reader-{i}" for i in range(1,11)], key+".reader.cap10",
                    [f"reader-{i}" for i in range(1,11)], payloads[0][key]["reader_knowledge_entries"])
            require([row["event_description"] for row in payloads[0][key]["relationship_milestones"]]
                    == [f"milestone-{i}" for i in range(1,cap+1)], key+".milestones.cap",
                    [f"milestone-{i}" for i in range(1,cap+1)], payloads[0][key]["relationship_milestones"])
        q0 = {q["query_key"]: q["rows"] for q in observations[0]["queries"] if q["explicit_order"]}
        q1 = {q["query_key"]: q["rows"] for q in observations[1]["queries"] if q["explicit_order"]}
        require(q0 == q1, "explicitly_ordered_source_query_rows", q0, q1)
        seen = {t for q in observations[0]["queries"] for t in q["tables"]}
        require(seen == TABLES, "source_tables", sorted(TABLES), sorted(seen))
        async with base.connect() as c:
            after = await fingerprints(c)
        require(before == after, "original_snapshot_fingerprints", before, after)
        require(model_calls == 0, "model_calls", 0, model_calls)
        result.update(
            status="通过",
            root_exception_type="none",
            root_error_code="none",
            observations=observations,
            complete_source_equal=True,
            explicitly_ordered_source_query_rows_equal=True,
            original_snapshot_unchanged=True,
            model_calls=0,
            coverage="F0-01..11,13,14,17,19 source composition; F05 includes actual ordered roster and native empty eligible-snapshot result. Non-empty qualified snapshot warning ranking has separate unit evidence.",
        )
    except Exception as e:
        (OUT / "traceback.txt").write_text("".join(traceback.format_exception(e)))
        differences = getattr(e, "differences", None)
        if not differences:
            frames = traceback.extract_tb(e.__traceback__)
            differences = [
                {
                    "field": (frames[-1].filename + ":" + str(frames[-1].lineno))
                    if frames
                    else "runtime",
                    "expected": summary("successful native operation"),
                    "actual": summary(type(e).__name__ + ":" + str(e)),
                }
            ]
        result.update(
            root_exception_type=type(e).__name__,
            root_error_code=str(e),
            traceback_path=str(OUT / "traceback.txt"),
            failure_differences=differences,
        )
    finally:
        result["sql_counts"] = dict(counts)
        result["model_calls"] = model_calls
        result["observations"] = observations
        result["plan_files"] = plan_files
        result["explain_manifest_path"] = "EXPLAIN-MANIFEST.json" if plan_files else None
        if before is not None:
            async with base.connect() as c:
                result["original_snapshot_unchanged"] = before == await fingerprints(c)
        (OUT / "RESULT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        await engine.dispose()
        await base.dispose()
        cleanup()
    raise SystemExit(0 if result["status"] == "通过" else 1)


asyncio.run(main())
