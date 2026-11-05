"""Q1 chapter drafting, local amendment and final-body checkpoint adoption."""

from copy import deepcopy
import time
from .checks import high_confidence, roll_day
from .contracts import choose_contract, valid_extract, payoff_ok, observations, reduce_growth
from .endings import ending_candidates, english_hits, apply_scene
from .identity import name_hits
from .precision import _sentences, apply as sentence_edits
from .craft import reference_features
from .q1_plan import GROWTH
from .q1_quality import metrics, merge_state, progress, normalize_scenes
from .q1_extract import pack, ask, clean, body_of, context, ground, prose_hits, extract_state
from .storage import load, save
from .text import chinese_count, _paragraph_spans
from .q1_continuity import repair_opening
from .structure_planning import final_stage_start

REVISION = "growth-payoff-3"


def supplement(text, value):
    spans = _paragraph_spans(text)
    idx = value.get("after")
    patch = value.get("text")
    if (
        type(idx) is int
        and 1 <= idx <= len(spans)
        and isinstance(patch, str)
        and 0 < len(patch.strip()) <= 400
        and "\n" not in patch
    ):
        a, b = spans[idx - 1]
        return clean(
            text[:a] + patch.strip() + text[b:]
            if value.get("replace") is True
            else text[:b] + "\n\n" + patch.strip() + text[b:]
        ), True
    return text, False


def amend(text, raw, bible, state, outline):
    hits = prose_hits(text, bible, state)
    detail = dict(edits=[], hits_before=len(hits), adoption_failed=False, remaining=[], patch=False)
    if outline.get("ending") and raw.get("scene"):
        changed, accepted = apply_scene(text, raw["scene"])
        return changed, dict(detail, patch=accepted)
    low, high = outline.get("word_range", (1870, 2700))
    body = body_of(raw.get("body", ""), None)
    count, amount = chinese_count(text), chinese_count(body)
    if body and (count > high and low <= amount < count or count < low and count < amount <= high):
        return body, dict(detail, patch=True)
    spans = _sentences(text)
    changed, edits = sentence_edits(
        text,
        raw.get("edits", []),
        [dict(id=i, start=a, end=b) for i, (a, b) in enumerate(spans, 1) if i in {h["id"] for h in hits}],
        allow_empty=True,
        allow_style=True,
    )
    bad = (
        not isinstance(raw.get("edits", []), list)
        or len(edits) != len(raw.get("edits", []))
        or any(abs(changed.count(a) - changed.count(b)) > abs(text.count(a) - text.count(b)) for a, b in ("“”", "「」"))
    )
    changed = text if bad else changed
    changed, patched = supplement(changed, raw["patch"]) if isinstance(raw.get("patch"), dict) else (changed, False)
    return changed, dict(detail, edits=[] if bad else edits, adoption_failed=bad, patch=patched)


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
    target = book.words_per_chapter
    canon = dict(bible["canon"], final_volume_start=final_stage_start(bible, book.chapters))
    bible = dict(bible, canon=canon)
    low, high = (1870, 2700) if target == 2200 else (round(target * 0.85), round(target * 1.15))
    previous = (out / f"chapter-{idx - 1:03d}.body.md").read_text() if idx > 1 else ""
    drops = []
    contract = choose_contract(
        canon, rows, idx, state.get("pending_payoff") or outline.get("payoff_type"), state, canon["final_volume_start"]
    )
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
    save(out / f"writing_outlines/{idx:03d}.json", outline)
    sections = context(bible, threads, vol, outline, state, history, rows, previous, drops)
    if book.style_reference:
        sections.append(("授权参考的统计风格特征", pack(reference_features(book.style_reference))))
    saved = client.prefix
    client.prefix = ""
    try:
        text = body_of(
            ask(
                client,
                prefix + "-draft",
                data[style]
                + f"\n目标{target}汉字，合格{low}—{high}，本章正文{2000 if target == 2200 else low}—{2400 if target == 2200 else high}汉字，只写两场戏，各约{round(target * 0.46)}—{round(target * 0.5)}字，用---分隔；最多新增1处伤病/代价。到本章情节完成立即收笔，直接输出正文。",
                sections,
                cap=6500,
                input_limit=10000,
                max_attempts=1,
            ),
            client,
        )
    finally:
        client.prefix = saved
    save(out / f"chapter-{idx:03d}.r2.draft.md", text)
    outline.update(
        actual_chars=chinese_count(text),
        target_chars=target,
        word_range=[low, high],
        dialogue_ratio=metrics(text)["dialogue_ratio"],
    )
    raw, failed, _ = extract_state(client, prefix + "-finish", text, state, bible, outline, rows)
    initial_failed = failed
    original = text
    carry = raw.get("previous_delta")
    carry_full = raw.get("_previous_complete")
    text, notes = amend(text, raw, bible, state, outline)
    patched = notes["patch"]
    for old, new in (raw.get("rename") if isinstance(raw.get("rename"), dict) else {}).items():
        if (
            old in {h["name"] for h in name_hits(original, bible)}
            and isinstance(new, str)
            and new in {c["name"] for c in bible.get("characters", [])}
        ):
            text = text.replace(old, new)
    changed = text != original
    failed = failed or bool((raw.get("scene") or raw.get("body")) and not changed)
    if changed:
        raw, failed, _ = extract_state(client, prefix + "-final-state", text, state, bible, outline, [], final=True)
    text, raw, failed, boundaries, handoff = repair_opening(
        client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
    )
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
    current.pop("end_location", None)
    current.pop("present", None)
    if boundaries["end_location"]:
        current["end_location"] = boundaries["end_location"]
    if boundaries["present_status"] == "grounded":
        current["present"] = boundaries["present"]
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
        seconds=round(time.monotonic() - started, 3),
        outside_calls=max(0, len(getattr(client, "calls", [])) - calls_before - 1),
        injuries_count=current.get("injuries_count", 0) if not failed else None,
        ending_candidates=ending_candidates(text, bible, outline) if outline["ending"] else [],
        opens_new_mainline=raw.get("opens_new_mainline") if outline["ending"] else False,
        **boundaries,
        handoff=handoff,
    )
    row.update(
        name_drift_before=len(name_hits(original, bible)), name_drift=name_hits(text, bible), english=english_hits(text)
    )
    row.update(observations(raw, text, idx) if not failed else {})
    row["solutions"] = [x for x in current.get("solutions", []) if x["chapter"] == idx]
    save(out / f"chapter-{idx:03d}.body.md", text)
    save(path, row)
    return row
