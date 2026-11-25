"""Write and resume a private four-chapter sample. Use one fresh system-temp directory per arm."""

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import tempfile

import yaml

from bookengine.batch import usage_report
from bookengine.checks import roll_day
from bookengine.config import Book
from bookengine.continuity_ledger import merge_continuity_delta
from bookengine.contracts import observations, payoff_ok, reduce_growth
from bookengine.life_planning import confirmed_deaths
from bookengine.prose_repetition import clean_repetitions
from bookengine.q1 import QClient
from bookengine.q1_cast import CAST_OBSERVATION_VERSION, previous_cast, seen_in
from bookengine.q1_continuity import advance_boundary_state, boundary_facts, evaluate_handoff
from bookengine.q1_extract import DATA, ask, body_of, clean, extract_state, pack
from bookengine.q1_plan import GROWTH, volume
from bookengine.q1_quality import merge_state, progress
from bookengine.storage import load, save
from bookengine.story_duties import record_story_duties
from bookengine.structure_planning import final_stage_start
from bookengine.text import chinese_count, number_paragraphs
from tools.books_guard import protected
from tools.fiction_trial_inputs import prepare_candidate, prepare_control
from tools.runtime_support import ProjectError, QuotaExhausted, write_runtime


VOLUME = "steps/plan-q1-volume-1-growth2.json"
SOURCES = (
    "book.yaml",
    "story_bible.yaml",
    "story_design.yaml",
    "threads.yaml",
    VOLUME,
    *(f"writing_outlines/{n:03d}.json" for n in range(1, 5)),
)


def digest(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def json_hash(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def checkpoint(path, value):
    """Never replace a completed input or a reader's body on resume."""
    if not path.exists():
        save(path, value)
        return
    old = path.read_text(encoding="utf-8") if isinstance(value, str) else load(path)
    if old != value:
        raise ValueError(f"checkpoint mismatch: {path.name}")


class TrialClient(QClient):
    def stage(self, name, prompt, schema=None, **kwargs):
        if "-trial-state-" not in name:
            return super().stage(name, prompt, schema, **kwargs)
        # The production extractor's bounded context may truncate a long chapter's last paragraphs.
        # Preserve every numbered paragraph in a factual read. Draft context keeps the production limit.
        passage = self.trial_fact_passage
        if passage not in prompt:
            heading = "## 编号正文\n"
            before, marker, block = prompt.partition(heading)
            if not marker:
                raise ProjectError(f"{name}: factual extraction has no manuscript section")
            _, after_heading, tail = block.partition("\n\n## ")
            prompt = before + marker + passage + after_heading + tail
            if "## 合法值\n" not in prompt:
                prompt += "\n\n## 合法值\n" + self.trial_fact_menu
        limit = self.context_limit
        self.context_limit = max(limit, len(self.system) + len(prompt) + len(schema or "") + 400)
        try:
            return super().stage(name, prompt, schema, **kwargs)
        finally:
            self.context_limit = limit

    def request(self, stage, prompt, cap, effort, attempt):
        seq = len(self.calls) + 1
        entry = dict(
            seq=seq,
            stage=stage,
            attempt=attempt,
            model=self.model,
            system=self.system,
            prompt=prompt,
            max_completion_tokens=cap,
            reasoning_effort=effort,
        )
        save(self.out / f"inputs/{seq:03d}.json", dict(entry, sha256=json_hash(entry)))
        return super().request(stage, prompt, cap, effort, attempt)


def pinned_inputs(out, source, arm):
    files = {name: (source / name).read_bytes() for name in SOURCES}
    engine = Path(__file__).resolve().parents[1]
    # Freeze the running engine's code and writer/extractor data as well as the historical inputs.
    code = sorted(
        [
            *(engine / "bookengine").glob("*.py"),
            *(engine / "data").glob("*.yaml"),
            *(engine / "tools").glob("fiction_trial*.py"),
        ]
    )
    spec = dict(
        format=1,
        arm=arm,
        book_horizon=50,
        sample_target=4,
        sources={name: digest(data) for name, data in files.items()},
        engine=json_hash({str(p.relative_to(engine)): digest(p.read_bytes()) for p in code}),
    )
    checkpoint(out / "trial.json", spec)
    for name, data in files.items():
        checkpoint(out / "source" / name, data.decode("utf-8"))
    # volume() normalizes the raw planning response against the real 50-chapter timetable.
    checkpoint(out / VOLUME, files[VOLUME].decode("utf-8"))
    return out / "source"


def settle(bible, threads, state, outline, rows, text, raw, out):
    n, canon = outline["number"], bible["canon"]
    state = merge_continuity_delta(state, raw.get("continuity_delta"), text, bible, n)
    merged = merge_state(state, {k: v for k, v in raw["state"].items() if v is not None}, bible, n)
    current, issues = reduce_growth(state, raw, bible, text, n, base=merged)
    current["clock"] = roll_day(state.get("clock"), current.get("clock"), text[-1200:], text)
    current, thread_issues = progress(current, raw, outline, bible, threads, {})
    facts = boundary_facts(raw, text)
    handoff = evaluate_handoff(state, facts, text, bible, outline)
    paid = payoff_ok(raw.get("payoff"), outline.get("payoff_type"), text, canon["payoff_menu"])
    current["pending_payoff"] = outline.get("payoff_type") if outline.get("payoff_due") and not paid else None
    current["handoff_unknown"] = bool(not facts["end_location"] or handoff["missing"])
    current = advance_boundary_state(current, facts, n, text, bible)
    current["seen_characters"] = sorted(set(state["seen_characters"]) | set(seen_in(text, bible["characters"])))
    current["cast_observation_version"] = CAST_OBSERVATION_VERSION
    row = dict(
        number=n,
        title=outline["title"],
        status="complete",
        fact_source="final_body",
        state=current,
        state_failed=False,
        chars=chinese_count(text),
        summary=str(raw.get("summary") or text[:100])[:200],
        type=raw.get("type"),
        hook_type=raw.get("hook_type"),
        payoff=raw.get("payoff"),
        payoff_due=outline.get("payoff_due"),
        payoff_type=outline.get("payoff_type"),
        payoff_fulfilled=paid,
        issues=issues + thread_issues,
        handoff=handoff,
        **facts,
        **observations(raw, text, n),
    )
    row["solutions"] = [s for s in current.get("solutions", []) if s["chapter"] == n]
    row["body_sha256"] = digest(text)
    row["previous_body_sha256"] = rows[-1]["body_sha256"] if rows else None
    if outline.get("story_duties"):
        row["story_duties"] = record_story_duties(out, n, text, outline)
    return row


def write_one(client, arm, book, bible, threads, vol, saved, state, rows, previous, style):
    n, out = saved["number"], client.out
    state = dict(state, seen_characters=previous_cast(out, n, bible, state))
    if arm == "A":
        prepared = prepare_control(client, book, bible, threads, vol, saved, state, rows, previous, style)
    else:
        prepared = prepare_candidate(book, bible, saved, state, rows, previous, out=out)
    outline, instruction, sections = prepared
    checkpoint(out / f"writing_outlines/{n:03d}.json", outline)
    payload = dict(
        arm=arm,
        number=n,
        book_horizon=50,
        instruction=instruction,
        sections=[list(s) for s in sections],
        state=state,
        previous_body_sha256=digest(previous) if n > 1 else None,
    )
    input_hash = json_hash(payload)
    checkpoint(out / f"trial-inputs/{n:03d}.json", dict(payload, sha256=input_hash))
    stage = f"r2-ch{n}-draft"
    stage_prefix = client.prefix
    client.prefix = ""
    try:
        raw_draft = ask(client, stage, instruction, sections, cap=6500, input_limit=10000, max_attempts=1)
    finally:
        client.prefix = stage_prefix
    initial = body_of(raw_draft, client)
    text = clean(clean_repetitions(initial)["cleaned"])
    if not text:
        raise ProjectError(f"{stage}: no readable first draft")
    checkpoint(out / f"chapter-{n:03d}.r2.draft.md", text)
    checkpoint(out / f"chapter-{n:03d}.body.md", text)
    # A failed factual extraction leaves the reader's draft intact, including across process restarts.
    attempts = out / f"extractions/{n:03d}"
    first = len(list(attempts.glob("*.json"))) + 1
    client.trial_fact_passage = number_paragraphs(text)
    client.trial_fact_menu = pack(
        {k: bible["canon"][k] for k in ("ladder", "hook_types", "payoff_menu", "solution_menu")}
    )
    # Extraction is an observation shared by both arms. Stage outcomes and planned chapter
    # contracts must never tell the fact reader what has happened in the manuscript.
    fact_bible = dict(bible, volumes=[])
    fact_outline = dict(number=n, ending=False)
    for attempt in range(first, first + 2):
        state_stage = f"r2-ch{n}-trial-state-{attempt}"
        raw, failed, _ = extract_state(client, state_stage, text, state, fact_bible, fact_outline, [], final=True)
        if not failed:
            row = settle(bible, threads, state, outline, rows, text, raw, out)
            row.update(writer_input_sha256=input_hash, extraction_stage=state_stage)
            checkpoint(out / f"chapters/{n:03d}.json", row)
            return row
        save(attempts / f"{attempt:03d}.json", dict(stage=state_stage, body_sha256=digest(text), valid=False))
    raise ProjectError(f"chapter {n}: fact extraction failed; resume reuses the saved draft")


def run_trial(source, out, env, arm, through=4, timeout=180, client_factory=TrialClient):
    source, out = Path(source).resolve(), Path(out).resolve()
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if (
        not any(root in out.parents for root in temp_roots)
        or protected(out)
        or source.is_relative_to(out)
        or out.is_relative_to(source)
    ):
        raise ValueError("the trial needs a separate private directory under system temp")
    if arm not in ("A", "B") or through not in range(1, 5):
        raise ValueError("choose A or B and a sample of chapters 1–4")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(p.is_symlink() for p in out.rglob("*")):
        raise ValueError("trial files must be private regular files")
    if not (out / "trial.json").is_file() and any(p.name != "trial.lock" for p in out.iterdir()):
        raise ValueError("a new trial requires an empty directory")
    with (out / "trial.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        src = pinned_inputs(out, source, arm)
        book = Book.read(src / "book.yaml")
        bible = yaml.safe_load((src / "story_bible.yaml").read_text(encoding="utf-8"))
        threads = yaml.safe_load((src / "threads.yaml").read_text(encoding="utf-8"))
        design = yaml.safe_load((src / "story_design.yaml").read_text(encoding="utf-8"))
        if book.chapters != 50 or design["total"] != 50 or not bible.get("selected_card"):
            raise ValueError("a selected card, its 50-chapter bible and schedule are required")
        bible = dict(bible, story_design=design)
        bible["canon"] = dict(
            bible["canon"],
            solution_menu=GROWTH["packs"][book.prompt_pack]["solution_menu"],
            solution_aliases=GROWTH.get("solution_aliases", {}),
            final_volume_start=final_stage_start(bible, 50),
        )
        client = client_factory(out, env, "", timeout)
        client.context_limit, client.clip_context, client.relaxed = 10000, True, True
        client.style_b, client.minimal_prose, client.plan_suffix = False, True, "-growth2"
        client.glossary = bible.get("glossary", {})
        client.prefix = pack(dict(reader_expectation=bible.get("reader_expectation"), anti_patterns=[]))[:650]
        state = merge_state({}, {}, bible)
        style = yaml.safe_load(DATA.read_text(encoding="utf-8"))
        run = dict(arm=arm, book_horizon=50, sample_target=4, completed_chapters=0, status="running")
        rows = []
        previous = ""
        try:
            vol = volume(client, bible, threads, 1, 50, state, [], [])
            for n in range(1, 5):
                row_path = out / f"chapters/{n:03d}.json"
                if n > through and not row_path.exists():
                    break
                if row_path.exists():
                    row = load(row_path)
                    body = (out / f"chapter-{n:03d}.body.md").read_text(encoding="utf-8")
                    if (
                        row["body_sha256"] != digest(body)
                        or row["previous_body_sha256"] != (digest(previous) if n > 1 else None)
                        or (out / f"chapter-{n:03d}.r2.draft.md").read_text(encoding="utf-8") != body
                    ):
                        raise ValueError(f"chapter {n}: saved draft chain changed")
                else:
                    saved = load(src / f"writing_outlines/{n:03d}.json")
                    if saved["number"] != n:
                        raise ValueError(f"mismatched source chapter {n}")
                    staged = dict(bible, life_constraints=confirmed_deaths(out, state, chapter=n))
                    row = write_one(client, arm, book, staged, threads, vol, saved, state, rows, previous, style)
                previous = (out / f"chapter-{n:03d}.body.md").read_text(encoding="utf-8")
                rows.append(row)
                state = row["state"]
                save(out / "state.json", state)
                run.update(completed_chapters=n, usage=usage_report(client.calls))
                save(out / "run.json", run)
            run["status"] = "sample_complete" if len(rows) == 4 else "sample_checkpoint"
        except BaseException as exc:
            run.update(status="quota_exhausted" if isinstance(exc, QuotaExhausted) else "interrupted", error=str(exc))
            raise
        finally:
            run["usage"] = usage_report(client.calls)
            save(out / "run.json", run)
            write_runtime(out, run["status"], "fiction-trial")
        return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--arm", choices=["A", "B"], required=True)
    parser.add_argument("--through", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    print(json.dumps(run_trial(**vars(args)), ensure_ascii=False))


if __name__ == "__main__":
    main()
