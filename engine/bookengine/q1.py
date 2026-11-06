"""Data-led Q1 books: immutable design, bounded memories, observed progress."""

from copy import deepcopy
from dataclasses import replace
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import time
import yaml
from .batch import usage_report
from .checks import high_confidence, roll_day
from .client import Client, SYSTEM
from tools.runtime_support import QuotaExhausted, ProjectError, write_runtime
from .config import Book
from .contracts import TYPES, choose_contract, valid_extract, payoff_ok, observations, reduce_growth, growth_warnings
from .export import export
from .endings import ending_candidates, apply_scene, scene_context, english_hits
from .identity import name_hits
from .precision import locate, _sentences, apply as sentence_edits
from .polish_checks import scan
from .serial_checks import adjacent, catch_hits
from .craft import reference_features, recall_events
from .longutil import ask as raw_ask, recover_fields, long_context
from .q1_plan import prepare, volume, outlines, beat_slice, FIX, DATA as PLAN_DATA, GROWTH
from .q1_quality import metrics, merge_state, progress, volume_issues, normalize_scenes
from .storage import load, now, save
from .text import chinese_count, prose, number_paragraphs, _paragraph_spans
from .q1_extract import (
    DATA,
    EXTRACT,
    pack,
    ask,
    clean,
    body_of,
    context,
    normalize_extract,
    ground,
    prose_hits,
    extract_state,
)
from .q1_chapter import REVISION, supplement, amend, chapter_record, chapter
from .structure_planning import stage_for_chapter, final_stage_start
from tools.book_checkup import write_checkup
from .solution_substance import solution_issues


class QClient(Client):
    """Production writer retains the shared transport and cost ledger."""


def acceptance(rows, history, client, bible, threads, profile, base=0):
    new = [r for r in rows if r["number"] > base]
    return dict(
        generated=sum(r["status"] == "complete" for r in rows),
        chapter_chars=[r["chars"] for r in rows],
        word_band_pass=sum(r["within_band"] for r in rows),
        hard_conflicts=sum(r.get("hard_conflicts", 0) for r in rows),
        format_failures=sum(c["status"] not in ("ok", "pending") for c in client.calls),
        payoff_rate=sum(r.get("payoff_fulfilled", False) for r in new if r.get("payoff_due"))
        / max(1, sum(bool(r.get("payoff_due")) for r in new)),
        classification_empty=sum(any(r.get(k) is None for k in ("type", "hook_type", "payoff")) for r in new),
        dialogue_scope_base=base,
        new_chapters=len(new),
        new_dialogue20_pass=sum(r["style"]["dialogue_ratio"] >= 0.2 for r in new),
    )


def run_q1(book_path, out, env, assets, timeout=180, style="b", extend_to=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (out / "runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stopped = [False]

        def stop(signum, frame):
            stopped[0] = True

        handlers = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            book = Book.read(book_path)
            if extend_to:
                meta = load(out / "run.json")
                old = Book(**meta["book"])
                if replace(old, chapters=book.chapters).record() != book.record() or extend_to < old.chapters:
                    raise ValueError("extension requires the original book and a larger chapter target")
                if extend_to > old.chapters:
                    meta.update(book=replace(old, chapters=extend_to).record(), status="interrupted")
                    save(out / "run.json", meta)
            return _run(
                replace(book, chapters=extend_to) if extend_to else book,
                out,
                env,
                Path(assets),
                timeout,
                style,
                lambda: stopped[0],
            )
        except (ValueError, RuntimeError) as exc:
            kind = "quota_exhausted" if isinstance(exc, QuotaExhausted) else "error"
            runtime = load(out / "runtime.json") if (out / "runtime.json").exists() else {}
            write_runtime(out, kind, runtime.get("stage", "run-q1"), error=str(exc))
            if (out / "run.json").exists():
                meta = load(out / "run.json")
                meta.update(status=kind, stop_reason=str(exc))
                save(out / "run.json", meta)
            raise
        finally:
            for s, h in handlers.items():
                signal.signal(s, h)


def _run(book, out, env, assets, timeout, style, stop):
    path = out / "run.json"
    meta = (
        load(path)
        if path.exists()
        else dict(mode="Q1", book=book.record(), started=now(), style=style, status="running")
    )
    if meta.get("mode") != "Q1" or Book(**meta["book"]).record() != book.record() or meta.get("style") != style:
        raise ValueError("Q1 checkpoint settings changed")
    if meta["status"] == "complete":
        if not (out / "book_checkup.json").exists():
            write_checkup(out, source="draft")
        return meta
    if meta.get("revision", {}).get("name") != REVISION:
        meta.setdefault("previous_runs", []).append(
            dict(status=meta["status"], stop_reason=meta.get("stop_reason"), wall_seconds=meta.get("wall_seconds", 0))
        )
        meta["revision"] = dict(
            name=REVISION,
            started=now(),
            base_chapters=meta.get("completed_chapters", 0),
            base_seconds=meta.get("execution_seconds", meta.get("wall_seconds", 0)),
        )
    elif meta["status"] == "interrupted":
        meta["revision"].update(started=now(), base_seconds=meta.get("execution_seconds", meta.get("wall_seconds", 0)))
    revision = meta["revision"]
    revision.setdefault("initial_seconds", revision["base_seconds"])
    meta.pop("stop_reason", None)
    meta["status"] = "running"
    save(path, meta)
    data = yaml.safe_load(DATA.read_text())
    client = QClient(out, env, "", timeout)
    client.context_limit = 10000
    client.clip_context = True
    client.relaxed = True
    client.style_b = False
    client.minimal_prose = True
    client.stop_requested = stop
    try:
        bible, threads, profile = prepare(client, book, out, assets, design=True)
    except (QuotaExhausted, ProjectError):
        raise
    except (ValueError, RuntimeError) as exc:
        meta.update(status="failed", stop_reason=str(exc), usage=usage_report(client.calls))
        save(path, meta)
        raise
    canon = dict(
        bible["canon"],
        solution_menu=GROWTH["packs"][book.prompt_pack]["solution_menu"],
        solution_aliases=GROWTH.get("solution_aliases", {}),
        final_volume_start=final_stage_start(bible, book.chapters),
    )
    bible = dict(bible, canon=canon)
    profile = dict(profile, canon=canon)
    client.glossary = bible.get("glossary", {})
    client.plan_suffix = "-growth2"
    client.prefix = pack(
        dict(reader_expectation=bible.get("reader_expectation"), anti_patterns=profile.get("anti_patterns", []))
    )[:650]
    rows = []
    history = []
    state = merge_state({}, {}, bible)
    vol = {}
    batch = []
    before = {}
    feedback = []
    alerts = []
    for idx in range(1, book.chapters + 1):
        stage = stage_for_chapter(bible, idx, book.chapters)
        vn = stage["number"]
        saved = out / f"chapters/{idx:03d}.json"
        cached_record = saved.exists() and load(saved).get("fact_source") == "final_body"
        cached = cached_record and idx <= meta.get("completed_chapters", 0)
        if cached:
            row = chapter_record(out, idx)
            rows.append(row)
            state = row["state"]
            for p in (out / "volumes").glob("*.json"):
                m = load(p)
                if m.get("end") == idx:
                    history = [h for h in history if h["number"] != m["number"]] + [m]
            continue
        if not vol or vol.get("number") != vn:
            before = deepcopy(
                next(
                    (r["state"].get("dashboard", {}) for r in rows if r["number"] == stage["start"] - 1),
                    bible.get("initial_dashboard", {}) if vn == 1 else state.get("dashboard", {}),
                )
            )
            vol = volume(client, bible, threads, vn, book.chapters, state, history, feedback)
            if history and history[-1].get("volume_issues"):
                vol = volume(client, bible, threads, vn, book.chapters, state, history, feedback, redo=True)
        if (
            not batch
            or not any(c.get("number") == idx for c in batch)
            or solution_issues(bible, rows, [c for c in batch if c.get("number", 0) >= idx])
        ):
            end = min(idx + 9, vol.get("end", stage["end"]), book.chapters)
            batch = outlines(client, bible, threads, profile, vol, idx, end, state, rows[-29:], history, alerts)
        if rows and not state.get("realm_progress"):
            level = state.get("dashboard", {}).get("strength")
            at = (
                next(
                    (r["number"] for r in reversed(rows) if r["state"].get("dashboard", {}).get("strength") != level), 0
                )
                + 1
            )
            state["realm_progress"] = dict(level=level, upgrade_at=at, displayed_at=None)
        outline = next(
            (c for c in batch if c.get("number") == idx), dict(number=idx, title=f"第{idx}章", scenes=[], threads=[])
        )
        row = chapter(client, book, bible, threads, profile, vol, outline, state, history, rows, out, style, data)
        rows.append(row)
        state = row["state"]
        alerts = row["issues"]
        meta.setdefault("dropped_fields", {})[str(idx)] = row.get("dropped_fields", [])
        save(out / "state.json", state)
        if idx == vol.get("end") or idx == book.chapters:
            memory_path = out / f"volumes/{vn:02d}-{idx:03d}.json"
            if memory_path.exists() and load(memory_path).get("fact_source") == "final_body":
                memory = load(memory_path)
            else:
                issues = volume_issues(before, state.get("dashboard", {}), vol, history) + growth_warnings(
                    state, before, state.get("dashboard", {}), bible["canon"], vol["start"], idx
                )
                memory = dict(
                    number=vn,
                    start=vol.get("start", stage["start"]),
                    end=idx,
                    summary="；".join(r["summary"][:60] for r in rows if vol["start"] <= r["number"] <= idx)[-1500:],
                    before_dashboard=before,
                    after_dashboard=state.get("dashboard", {}),
                    volume_issues=issues,
                    fact_source="final_body",
                )
                save(memory_path, memory)
            history.append(memory)
            feedback = [*memory.get("feedback", []), *memory.get("volume_issues", []), *row.get("issues", [])]
        summary = acceptance(rows, history, client, bible, threads, profile, revision["base_chapters"])
        revision["seconds"] = (
            dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(revision["started"])
        ).total_seconds()
        seconds = revision["base_seconds"] + revision["seconds"]
        meta["execution_seconds"] = seconds
        if not cached_record:
            row["run_elapsed"] = seconds
            save(out / f"chapters/{idx:03d}.json", row)
        recent = [r for r in rows if r["number"] > revision["base_chapters"]]
        if len(recent) >= 50:
            average = sum(r["seconds"] for r in recent[-50:]) / 50
            calls = [
                c
                for c in client.calls
                if c["stage"].startswith("r2-ch") and int(re.findall(r"\d+", c["stage"])[0]) >= idx - 49
            ]
            rate = sum(c["status"] not in ("ok", "pending") for c in calls) / max(1, len(calls))
            if average > 100 or rate > 0.1:
                meta.setdefault("warnings", []).append(dict(chapter=idx, average_seconds=average, failure_rate=rate))
        meta.update(
            completed_chapters=sum(r["status"] == "complete" for r in rows),
            wall_seconds=round(seconds, 3),
            usage=usage_report(client.calls),
            quality=summary,
            updated=now(),
        )
        save(path, meta)
        print(
            f"chapter={idx} chars={row['chars']} dialogue={row['style']['dialogue_ratio']:.3f} elapsed={seconds:.1f}s",
            flush=True,
        )
        if idx % 25 == 0 and idx > revision["base_chapters"]:
            log = out / "progress.log"
            save(
                log,
                (log.read_text() if log.exists() else "")
                + f"chapter={idx} seconds={seconds:.1f} cost_usd={meta['usage']['estimated_usd']['conservative_total']} input_chars={max((c['input_chars'] for c in client.calls if c['stage'] == f'g-ch{idx}-draft'), default=0)} active_lines={sum(bool(v.get('last')) for v in state.get('ledger', {}).values())} format_rate={summary['format_failures'] / max(1, len(client.calls)):.4f}\n",
            )
        if stop():
            meta["status"] = "interrupted"
            save(path, meta)
            return meta
    meta["quality"] = acceptance(rows, history, client, bible, threads, profile, revision["base_chapters"])
    export(
        out, bible.get("title", "Q1"), rows, [(out / f"chapter-{r['number']:03d}.body.md").read_text() for r in rows]
    )
    meta.update(
        status="complete" if all(r["status"] == "complete" for r in rows) else "incomplete",
        finished=now(),
        acceptance=meta["quality"],
    )
    save(path, meta)
    write_checkup(out, source="draft")
    return meta
