"""Q1 chapter drafting, local amendment and final-body checkpoint adoption."""

from copy import deepcopy
import time
from .checks import high_confidence, roll_day
from .contracts import choose_contract, valid_extract, payoff_ok, observations, reduce_growth
from .endings import ending_candidates, english_hits, repair_final_closure
from .identity import name_hits, writer_identity_outline
from .hook_momentum import repair_after_draft
from .disclosure_choices import disclosure_context, disclosure_effect
from .q1_draft_prompt import production_prompt, word_band
from .rhythm_writer import writer_hook_type
from .q1_plan import GROWTH
from .character_context import schedule_catchphrases
from .speaker_context import speaker_briefs
from .q1_quality import metrics, merge_state, progress, normalize_scenes
from .q1_extract import ask, clean, body_of, ground, prose_hits, extract_state
from .storage import load, save
from .life_planning import apply_life_constraints
from .scene_repair import repair_scene_consistency
from .rhythm_contract_repair import apply_rhythm_contracts
from .chapter_type_repair import repair_chapter_type
from .text import chinese_count, _paragraph_spans
from .q1_continuity import repair_opening, evaluate_handoff, advance_boundary_state, with_ending_candidate
from .opening_promises import repair_opening_promises, previous_promise_context
from .planning_drift import observe_plan_alignment
from .story_payoffs import prepare_writer_payoffs, repair_story_payoffs
from .solution_sequences import repair_solution_sequence
from .first_appearances import repair_first_appearances
from .explanation_density import repair_explanation_density
from .structure_planning import final_stage_start
from .core_evidence import observe_core_use
from .content_guard import guarded, draft_until_readable, RESTRAINED_DRAFT
from .q1_amend import amend, retained_state_stage, supplement as supplement
from .retired_opponents import repair_retired, repair_exit_tail
from .prose_repetition import clean_repetitions
from .q1_repetition import repair_repetitions
from .q1_cast import chapter_cast, previous_cast, cast_findings, remaining_cast_problems, seen_in
from .q1_cast import CAST_OBSERVATION_VERSION
from .cast_consistency import repair_cast_consistency
from .text_hygiene import repair_text, require_publishable
from .title_promises import writer_title_contract
from .continuity_ledger import repair_continuity_ledger
from .cross_chapter_facts import guard_all_repairs
from .repair_additions import guard_repair_additions
from .name_slips import fix_name_slips
from .life_status import repair_premature_death
from .permanent_facts import repair_permanent_states
from .state_values import repair_state_values
from .plot_progression import repair_plot_replay
from .opening_styles import repair_opening_style
from .prose_similarity import repair_cross_chapter_opening
from .quote_text import repair_unclosed_quotes
from .dialogue_hygiene import repair_dialogue_repetitions
from .model_notes import repair_model_notes
from .story_duties import with_story_duties, record_story_duties
from tools.book_checkup import clean_prose

REVISION = "growth-payoff-3"


def chapter_record(out, number):
    recovered = out / f"chapters/{number:03d}.recovery.json"
    return load(recovered if recovered.exists() else out / f"chapters/{number:03d}.json")


def chapter(client, book, bible, threads, profile, vol, outline, state, history, rows, out, style, data):
    idx = outline["number"]
    path = out / f"chapters/{idx:03d}.json"
    if path.exists() and load(path).get("fact_source") == "final_body":
        return chapter_record(out, idx)
    started = time.monotonic()
    calls_before = len(getattr(client, "calls", []))
    prefix = f"r2-ch{idx}"
    state = dict(state, seen_characters=previous_cast(out, idx, bible, state))
    target = book.words_per_chapter
    canon = dict(bible["canon"], final_volume_start=final_stage_start(bible, book.chapters))
    bible = dict(bible, canon=canon)
    low, high = word_band(target)
    previous = (out / f"chapter-{idx - 1:03d}.body.md").read_text() if idx > 1 else ""
    drops = []
    contract = choose_contract(
        canon, rows, idx, state.get("pending_payoff") or outline.get("payoff_type"), state, canon["final_volume_start"]
    )
    contract["hook_type"] = writer_hook_type(outline, contract["hook_type"], canon, idx == book.chapters)
    method = outline.get("solution_type")
    method = (
        method
        if method in canon["solution_menu"] and method not in contract["solution_avoid"]
        else contract["solution_type"]
    )
    outline = dict(
        outline,
        **{k: v for k, v in contract.items() if k not in ("warnings", "solution_type")},
        solution_type=method,
        payoff_criterion=GROWTH.get("payoff_criteria", {}).get(contract["payoff_type"]),
        injury_limit=1,
        ending=idx == book.chapters,
        hook="末章只结清本书已有冲突，常规悬念钩子作废；不得留下新身份、物件来源或追查任务。以既有人物行动或对白收笔。"
        if idx == book.chapters
        else outline.get("hook"),
        growth_target=vol.get("expected_dashboard", {}),
    )
    outline = chapter_cast(outline, bible, state["seen_characters"])
    outline = writer_identity_outline(outline, bible)
    outline = schedule_catchphrases(outline, bible)
    outline = writer_title_contract(outline)
    outline = with_story_duties(prepare_writer_payoffs(outline), bible, out)
    outline = apply_rhythm_contracts(client, bible, outline, state, chapter=idx, kind="writer")
    outline = apply_life_constraints(client, bible, outline, state, chapter=idx)
    outline["speaker_briefs"] = speaker_briefs(outline, bible, state.get("present"), out=out)
    outline.pop("disclosure_context", None)
    if focus := disclosure_context(outline, bible, rows, out):
        outline["disclosure_context"] = focus
    save(out / f"writing_outlines/{idx:03d}.json", outline)
    instruction, sections = production_prompt(
        book, bible, threads, vol, outline, state, history, rows, previous, drops, out, style, data
    )
    cleaning = dict(draft={})

    def write_draft(suffix):
        saved = client.prefix
        client.prefix = ""
        try:
            text = body_of(
                ask(
                    client,
                    prefix + "-draft" + suffix,
                    instruction + (RESTRAINED_DRAFT if suffix else ""),
                    sections,
                    cap=6500,
                    input_limit=10000,
                    writer_drops=drops,
                    max_attempts=1,
                ),
                client,
                cleaning["draft"],
            )
        finally:
            client.prefix = saved
        repetition["draft"] = clean_repetitions(text)
        text = clean(repetition["draft"].pop("cleaned"))
        save(out / f"chapter-{idx:03d}.r2.draft.md", text)
        outline.update(
            actual_chars=chinese_count(text),
            target_chars=target,
            word_range=[low, high],
            dialogue_ratio=metrics(text)["dialogue_ratio"],
        )
        return text

    def finish(suffix, text):
        return extract_state(client, prefix + "-finish" + suffix, text, state, bible, outline, rows)

    repetition = {}
    text, (raw, failed, _), outline["draft_variant"] = draft_until_readable(write_draft, finish)
    initial_failed = failed
    original = text
    original_raw = deepcopy(raw)
    proposed_body = body_of(raw.get("body"), None)
    mixed_finish = bool(proposed_body and proposed_body != original)

    def restore_draft():
        if mixed_finish:
            retained, unavailable, _ = extract_state(
                client, retained_state_stage(out, prefix, original), original, state, bible, outline, [], final=True
            )
            return original, retained, unavailable
        return original, deepcopy(original_raw), initial_failed

    cast_before = cast_findings(original, bible, state, outline)
    cast_target_ids = {
        h["id"]
        for h in prose_hits(original, bible, state)
        if h.get("kind") in ("identity_statement", "unregistered_alias")
    }
    carry = raw.get("previous_delta")
    carry_full = raw.get("_previous_complete")
    text, notes = amend(text, raw, bible, state, outline)
    cast_repaired = any(e["id"] in cast_target_ids for e in notes.get("edits", []))
    repetition_ids = {h["id"] for h in prose_hits(original, bible, state) if h.get("kind") == "prose_repetition"}
    repetition_repaired = any(e["id"] in repetition_ids for e in notes.get("edits", []))
    cleaning["repair_body"] = notes.get("body_cleaning", {}) if notes.get("body_adopted") else {}
    patched = notes["patch"]
    for old, new in (raw.get("rename") if isinstance(raw.get("rename"), dict) else {}).items():
        if (
            old in {h["name"] for h in name_hits(original, bible)}
            and isinstance(new, str)
            and new in {c["name"] for c in bible.get("characters", [])}
        ):
            text = text.replace(old, new)
    text, cleaning["repair"] = clean_prose(text)
    text = clean(text)
    changed = text != original
    failed = failed or bool(raw.get("scene") and not changed)
    if changed:
        stage = (
            retained_state_stage(out, prefix, text)
            if mixed_finish and text != proposed_body
            else prefix + "-final-state"
        )
        raw, failed, _ = extract_state(client, stage, text, state, bible, outline, [], final=True)
        if notes.get("body_adopted") and failed:
            text, raw, failed = restore_draft()
            notes.update(body_adopted=False, length_revision="state_recheck_failed", patch=False)
            patched = False
            cleaning["repair_body"] = {}
            cleaning["repair"] = {}
    elif mixed_finish:
        text, raw, failed = restore_draft()
    before_repetition = clean_repetitions(original, remove_safe=False)["counts"]
    after_repetition = clean_repetitions(text, remove_safe=False)["counts"]
    if repetition_repaired and (
        failed
        or not (
            after_repetition["sentence_repetitions"] + after_repetition["adjacent_paragraph_pairs"]
            < before_repetition["sentence_repetitions"] + before_repetition["adjacent_paragraph_pairs"]
        )
    ):
        text, raw, failed = restore_draft()
        notes["repetition_repair"] = "rolled_back"
    elif repetition_repaired:
        notes["repetition_repair"] = "rechecked"
    if cast_repaired and (
        failed
        or not remaining_cast_problems(cast_findings(text, bible, state, outline))
        < remaining_cast_problems(cast_before)
    ):
        text, raw, failed = restore_draft()
        notes["cast_repair"] = "rolled_back"
        cleaning["repair_body"] = {}
        cleaning["repair"] = {}
    elif cast_repaired:
        notes["cast_repair"] = "rechecked"
    text, raw, failed, boundaries, handoff = repair_opening(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, notes["cross_chapter_opening"] = guarded(repair_cross_chapter_opening)(
        client, prefix, text, raw, failed, state, bible, outline, out, extract_state, ask
    )
    text, raw, failed, notes["opening_style"] = guarded(repair_opening_style)(
        client, prefix, text, raw, failed, state, bible, outline, out, extract_state, ask
    )
    text, raw, failed, opponent_repair = guarded(repair_retired)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    from .q1_continuity import boundary_facts

    boundaries = boundary_facts(raw, text)
    cleaned, cleaning["final"] = clean_prose(text)
    repetition["final"] = clean_repetitions(cleaned)
    cleaned = repetition["final"].pop("cleaned")
    if cleaned != text:
        text = clean(cleaned)
        raw, failed, _ = extract_state(client, prefix + "-clean-state", text, state, bible, outline, [], final=True)
        from .q1_continuity import boundary_facts

        boundaries = boundary_facts(raw, text)
    text, raw, failed, repetition["repair"] = guarded(repair_repetitions)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, exit_tail_repair = guarded(repair_exit_tail)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, notes["first_appearances"] = guarded(repair_first_appearances)(
        client, prefix, text, raw, failed, state, bible, outline, out, extract_state, ask
    )
    text, raw, failed, notes["solution_sequence"] = guarded(repair_solution_sequence)(
        client, prefix, text, raw, failed, state, bible, outline, out, extract_state, ask
    )
    text, raw, failed, story_payoffs = guarded(repair_story_payoffs)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, promise_review = guarded(repair_opening_promises)(
        client, prefix, text, raw, failed, state, bible, outline, previous_promise_context(out, idx), extract_state, ask
    )
    text, raw, failed, _ = guarded(repair_after_draft)(
        client, prefix, text, raw, failed, state, bible, outline, None, rows, extract_state, ask
    )
    # Content repairs first; text hygiene last so it also cleans what they rewrote.
    text, raw, failed, cleaning["model_notes"] = guarded(repair_model_notes)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, notes["plot_progression"] = guarded(repair_plot_replay)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask, out=out
    )
    text, raw, failed, notes["permanent_states"] = guarded(repair_permanent_states)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, notes["state_values"] = guarded(repair_state_values)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, notes["premature_death"] = guarded(repair_premature_death)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, notes["scene_consistency"] = guarded(repair_scene_consistency)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, notes["continuity_ledger"] = guarded(repair_continuity_ledger)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, cleaning["relationship_rhythm"] = guarded(repair_chapter_type)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, cleaning["explanation_density"] = guarded(repair_explanation_density)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, cleaning["final_closure"] = guarded(repair_final_closure)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, cleaning["quotes"] = guarded(repair_unclosed_quotes)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, cleaning["dialogue_repetitions"] = guarded(repair_dialogue_repetitions)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, cleaning["text_hygiene"] = guarded(repair_text)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, raw, failed, notes["cast_consistency"] = guarded(repair_cast_consistency)(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
    text, notes["repair_history"] = guard_all_repairs(ask, client, prefix, original, text, state, idx, bible=bible)
    restored, notes["repair_additions"] = guard_repair_additions(original, text)
    if notes["repair_history"]["status"] == "rolled_back" or restored == original != text:
        text, raw, failed = restore_draft()
    elif notes["repair_history"]["status"] == "partially_rolled_back" or restored != text:
        text = restored
        raw, failed, _ = extract_state(
            client, prefix + "-repair-history-state", text, state, bible, outline, [], final=True
        )
        if failed:
            text, raw, failed = restore_draft()
            notes["repair_history"].update(status="rolled_back", reason="repair_history_state_failed")
    text, notes["name_slips"] = fix_name_slips(text, bible, idx)
    require_publishable(text, idx, out)
    boundaries = boundary_facts(raw, text)
    boundaries = with_ending_candidate(boundaries, text, bible, state)
    final_handoff = evaluate_handoff(state, boundaries, text, bible, outline)
    handoff.update({k: v for k, v in final_handoff.items() if k != "status"})
    if handoff["missing"]:
        handoff["opening_status"] = handoff["status"]
        handoff["status"] = "handoff_unknown"
    cleaning["total"] = sum(counts.get("total", 0) for counts in cleaning.values())
    if rows and rows[-1].get("state_failed") and not rows[-1].get("carry_attempted"):
        last = rows[-1]
        last["carry_attempted"] = True
        prior = (out / f"chapter-{last['number']:03d}.body.md").read_text()
        delta = ground(carry, prior)
        if carry_full and valid_extract(delta, prior, canon["hook_types"], canon["payoff_menu"]):
            state, _ = reduce_growth(
                state,
                delta,
                bible,
                prior,
                last["number"],
                base=merge_state(
                    state, {k: v for k, v in delta["state"].items() if v is not None}, bible, last["number"]
                ),
            )
            last.update(
                state=state,
                state_recovered=True,
                payoff_fulfilled=payoff_ok(delta.get("payoff"), last.get("payoff_type"), prior, canon["payoff_menu"]),
                **{k: delta[k] for k in ("type", "hook_type", "payoff")},
                **observations(delta, prior, last["number"]),
            )
        save(out / f"chapters/{last['number']:03d}.recovery.json", last)
    current = deepcopy(state)
    issues = []
    if not failed:
        current, issues = reduce_growth(
            state,
            raw,
            bible,
            text,
            idx,
            base=merge_state(state, {k: v for k, v in raw["state"].items() if v is not None}, bible, idx),
        )
        current["clock"] = roll_day(state.get("clock"), current.get("clock"), text[-1200:], text)
        current, thread_issues = progress(current, raw, outline, bible, threads, profile)
        issues += thread_issues
    paid = not failed and payoff_ok(raw.get("payoff"), outline["payoff_type"], text, canon["payoff_menu"])
    current["pending_payoff"] = outline["payoff_type"] if outline["payoff_due"] and not paid else None
    current["handoff_unknown"] = bool(not boundaries["end_location"] or handoff.get("missing"))
    current = advance_boundary_state(current, boundaries, idx, text, bible)
    current["seen_characters"] = sorted(set(state["seen_characters"]) | set(seen_in(text, bible.get("characters", []))))
    if not current["seen_characters"]:
        current.pop("seen_characters")
        current.pop("cast_observation_version", None)
    else:
        current["cast_observation_version"] = CAST_OBSERVATION_VERSION
    kinds = normalize_scenes(raw.get("scene_types"))
    events = [e for e in raw.get("events", []) or [] if isinstance(e, dict)]
    row = dict(
        number=idx,
        title=outline.get("title", f"第{idx}章"),
        status="complete" if text else "degraded",
        chars=chinese_count(text),
        within_band=low <= chinese_count(text) <= high,
        summary=str(raw.get("summary") or text[:100])[:200],
        state=current,
        style=metrics(text),
        type=raw.get("type"),
        hook_type=raw.get("hook_type"),
        payoff=raw.get("payoff"),
        payoff_due=outline["payoff_due"],
        payoff_type=outline["payoff_type"],
        payoff_fulfilled=paid,
        payoff_patch=patched,
        state_failed=failed,
        state_initial_failed=initial_failed,
        state_retries=0,
        dropped_fields=drops,
        scene_types=kinds,
        structure=raw.get("structure"),
        r2_events=raw.get("r2_events") or [],
        plan_alignment=observe_plan_alignment(outline, raw),
        meeting_scenes=kinds.count("meeting"),
        scenes=len(kinds),
        hard_conflicts=len(high_confidence(state, events, text)),
        issues=issues + contract["warnings"],
        warnings=[],
        length_correction=chinese_count(original) != chinese_count(text),
        polish=notes,
        dialogue_rewrite=False,
        writer="minimal-b",
        fact_source="final_body",
        revision=REVISION,
        injuries_count=current.get("injuries_count", 0) if not failed else None,
        ending_candidates=ending_candidates(text, bible, outline) if outline["ending"] else [],
        opens_new_mainline=raw.get("opens_new_mainline") if outline["ending"] else False,
        **boundaries,
        handoff=handoff,
        opening_promises=promise_review,
        story_payoffs=story_payoffs,
        core_plan={
            k: outline[k]
            for k in (
                "number",
                "core_required",
                "core_application",
                "core_development",
                "solution_target",
                "solution_method",
            )
            if k in outline
        },
        core_use=observe_core_use(raw.get("core_use"), text, bible, outline, client, prefix, ask),
        opponent_repair=opponent_repair,
        exit_tail_repair=exit_tail_repair,
        cast_findings=cast_findings(text, bible, state, outline),
        cleaning=cleaning,
        repetition=dict(repetition, remaining=clean_repetitions(text, remove_safe=False)["counts"]),
    )
    row.update(
        name_drift_before=len(name_hits(original, bible)), name_drift=name_hits(text, bible), english=english_hits(text)
    )
    row.update(observations(raw, text, idx) if not failed else {})
    if outline.get("disclosure_context"):
        row["disclosure_effect"] = disclosure_effect(outline, raw if not failed else {}, text)
    row["solutions"] = [x for x in current.get("solutions", []) if x["chapter"] == idx]
    row["story_duties"] = record_story_duties(
        out, idx, text, outline, payoff_review=story_payoffs, client=client, prefix=prefix, ask=ask
    )
    row["seconds"] = round(time.monotonic() - started, 3)
    row["outside_calls"] = max(0, len(getattr(client, "calls", [])) - calls_before - 1)
    save(out / f"chapter-{idx:03d}.body.md", text)
    save(path, row)
    return row
