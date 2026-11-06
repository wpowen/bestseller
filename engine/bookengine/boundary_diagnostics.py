"""Read-only replay of the shared handoff contract; lexical hints stay Inference."""

from copy import deepcopy
import re

from .q1_continuity import boundary_facts


def _facts(chapter):
    """Verify each saved source independently; never import its cumulative state."""
    meta, text = chapter["meta"], chapter["text"]
    result = boundary_facts({}, text)
    for raw in (meta, meta.get("state", {}), meta.get("boundary", {})):
        if not isinstance(raw, dict):
            continue
        facts = boundary_facts(raw, text)
        for side in ("opening", "end"):
            for kind in ("location", "pov"):
                key = side + "_" + kind
                quote = side + ("_quote" if kind == "location" else "_pov_quote")
                if not result.get(key) and facts.get(key):
                    result.update({key: facts[key], quote: facts.get(quote)})
        if "present" in raw and facts.get("present_status") == "grounded":
            result.update(present=facts["present"], present_status="grounded")
        for row in facts.get("character_locations", []):
            if row not in result["character_locations"]:
                result["character_locations"].append(deepcopy(row))
    return result


def _boundary(chapter, side):
    facts = _facts(chapter)
    return facts.get(side + "_location"), facts.get(side + "_quote")


def _quote(text, word, *, last=False):
    offset = text.rfind(word) if last else text.find(word)
    return text[max(0, offset - 80) : offset + len(word) + 80] if offset >= 0 else None


def _lexical_issue(old, new, previous, facts, bible, outline):
    """Recheck legacy surface candidates with the shared acceptance rule only.

    These are hypotheses, not observations. Never advance replay state with them.
    If any candidate pair can be continuous, ambiguous vocabulary is not a break.
    """
    from .q1_continuity import evaluate_handoff

    if previous.get("end_pov") and facts.get("opening_pov") != previous["end_pov"]:
        return None
    old_places = {previous["end_location"]} if previous.get("end_location") else place_candidates(old["text"])
    head = new["text"][:350]
    new_places = {facts["opening_location"]} if facts.get("opening_location") else place_candidates(head)
    basis = "lexical_place_candidates"
    if not old_places or not new_places:
        # Retain the legacy outdoor/tunnel surface hint without a second set of
        # transfer rules: accepting or rejecting movement belongs to evaluate_handoff.
        tail = old["text"][-1600:]
        outside = re.search(r"天光|阳光|天空|露天|天幕|户外", tail)
        escape = re.search(r"冲出|走出|逃出|跑出|鱼贯|跨过门槛", tail)
        inside = re.search(r"岩壁|矿道|矿轨|运矿|矿车|洞内|地下|隧道|洞顶", head)
        if not (outside and escape and inside):
            return None
        old_places, new_places = {outside[0]}, {inside[0]}
        basis = "lexical_boundary"
    for end in sorted(old_places):
        for opening in sorted(new_places):
            candidate_state = dict(previous, end_location=end)
            candidate_facts = dict(facts, opening_location=opening)
            if not evaluate_handoff(candidate_state, candidate_facts, new["text"], bible, outline).get("issue"):
                return None
    end = sorted(old_places)[0]
    opening = sorted(new_places)[0]
    return dict(
        chapters=[old["number"], new["number"]],
        previous=sorted(old_places),
        opening=sorted(new_places),
        end_quote=_quote(old["text"], end, last=True),
        opening_quote=_quote(head, opening),
        status="Inference",
        basis=basis,
    )


def _breaks(chapters, bible=None):
    """Replay chapter facts through the production API, retaining all unknowns."""
    from .q1_continuity import advance_boundary_state, evaluate_handoff

    bible = bible or {}
    issues, unknown, checks = [], [], []
    state, old = {}, None
    for chapter in chapters:
        number, text = chapter["number"], chapter["text"]
        pair = [old["number"], number] if old else [number - 1, number] if number > 1 else [number]
        gap = bool(old and number != old["number"] + 1)
        if gap:
            state = {}
        facts = _facts(chapter)
        outline = chapter["meta"].get("outline", {})
        outline = dict(outline, number=number) if isinstance(outline, dict) else dict(number=number)
        evaluation = evaluate_handoff(state, facts, text, bible, outline)
        issue, missing = evaluation.get("issue"), evaluation.get("missing", [])
        reference = evaluation.get("reference") or {}
        end_chapter = old["number"] if old and not gap else None
        if reference.get("basis") == "viewpoint_last_location":
            end_chapter = state.get("pov_locations", {}).get(reference.get("viewpoint"), {}).get("chapter")
        status = (
            "Unknown" if gap or missing or evaluation["status"] != "checked" else "Inference" if issue else "Evidence"
        )
        handoff_status = "checked" if status == "Evidence" else "issue" if status == "Inference" else "handoff_unknown"
        row = dict(
            chapter=number,
            chapters=pair,
            status=status,
            pov_status="Evidence" if facts.get("opening_pov") and facts.get("end_pov") else "Unknown",
            handoff_status=handoff_status,
            facts=facts,
            evaluation=deepcopy(evaluation),
            end_quote=reference.get("end_quote"),
            end_chapter=end_chapter,
            opening_quote=facts.get("opening_quote"),
        )
        checks.append(row)
        if status == "Unknown":
            unknown.append(
                dict(
                    chapters=pair,
                    chapter=number,
                    status="Unknown",
                    reason="missing_chapters" if gap else "missing_grounded_boundaries",
                    missing=list(missing),
                    reference=deepcopy(evaluation.get("reference")),
                    end_quote=row["end_quote"],
                    end_chapter=end_chapter,
                    opening_quote=row["opening_quote"],
                )
            )
        stage_issue = isinstance(issue, dict) and (
            issue.get("kind") == "missing_stage_transition" or issue.get("issue") == "missing_stage_transition"
        )
        location_issue = isinstance(issue, dict) and issue.get("kind") == "location_break"
        if stage_issue or (old and not gap and location_issue):
            end_quote = row["end_quote"]
            source_chapter = end_chapter
            if stage_issue and not end_quote and old and not gap:
                end_quote, source_chapter = old["text"][-600:], old["number"]
            issues.append(
                dict(
                    issue,
                    kind="missing_stage_transition" if stage_issue else "location_break",
                    chapters=pair,
                    end_quote=end_quote,
                    end_chapter=source_chapter,
                    opening_quote=facts.get("opening_quote") or next(iter(issue.get("quotes", [])), None),
                    reference=deepcopy(reference),
                    status="Inference",
                    basis="shared_stage_transition" if stage_issue else "shared_handoff",
                )
            )
        elif (
            old
            and not gap
            and not (state.get("end_location") and facts.get("opening_location"))
            and not (not reference.get("end_location") and evaluation.get("transition", {}).get("status") == "present")
        ):
            candidate = _lexical_issue(old, chapter, state, facts, bible, outline)
            if candidate:
                issues.append(candidate)
        state = advance_boundary_state(state, facts, number, text, bible)
        row["end_location_candidate"] = deepcopy(state.get("end_location_candidate"))
        old = chapter
    return dict(
        count=len(issues),
        issues=issues,
        unknown=unknown,
        checks=checks,
        checked=sum(row["handoff_status"] == "checked" for row in checks),
        status="reference_only",
    )


def place_candidates(text):
    narrative = re.sub(r"“[^”]*”|‘[^’]*’|「[^」]*」", "", text)
    found = re.findall(r"[\u4e00-\u9fff]{2,8}(?:巷|剑阁|剑廊|器室|剑室|客栈|驿站|码头|渡口|矿道|洞窟|城楼)", narrative)
    # Token suffixes are surface evidence, never semantic location truth.
    return {re.split(r"(?:在|从|到|入|出|的|后|了|是|进|过|还|和|与)", word)[-1] for word in found}
