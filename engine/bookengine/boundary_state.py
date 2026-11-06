"""Viewpoint-specific boundary references and immutable state updates."""

from copy import deepcopy
import re

from .handoff_places import location_table


def ending_candidate(text, bible, state, facts):
    """Known literal names in the last 600 characters are candidates, never checked facts."""
    known = [*state.get("known_locations", []), state.get("end_location"), facts.get("opening_location")]
    names = location_table(bible, [n for n in known if n])["names"]
    tail = text[-600:]
    narrative = re.sub(r'“[^”]*(?:”|$)|‘[^’]*(?:’|$)|「[^」]*(?:」|$)|"[^"]*(?:"|$)', lambda m: " " * len(m[0]), tail)
    matches = []
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", narrative):
        sentence = match[0]
        if re.search(r"想起|记得|回忆|曾经|当年|打算|计划|准备|如果|假如|没有|未曾|并未|不曾|没能", sentence):
            continue
        places = [name for name in names if name in sentence]
        for name in places:
            if not any(name != other and name in other for other in places):
                matches.append(dict(location=name, quote=tail[match.start() : match.end()].strip()))
    if not matches:
        return None
    return dict(
        status="Inference",
        locations=list(dict.fromkeys(row["location"] for row in matches)),
        quotes=list(dict.fromkeys(row["quote"] for row in matches)),
        basis="known_literal_name_in_last_600",
    )


def with_ending_candidate(facts, text, bible, state):
    facts = dict(facts)
    if not facts.get("end_location"):
        candidate = ending_candidate(text, bible, state, facts)
        if candidate:
            facts["end_location_candidate"] = candidate
    return facts


def handoff_reference(state, facts):
    old_pov, new_pov = state.get("end_pov"), facts.get("opening_pov")
    result = dict(
        end_location=state.get("end_location"),
        present=state.get("present", []),
        end_quote=state.get("end_quote"),
        viewpoint=new_pov,
        basis="previous_end",
        known_locations=state.get("known_locations", []),
    )
    if (old_pov or new_pov) and old_pov != new_pov:
        observation = state.get("pov_locations", {}).get(new_pov, {}) if new_pov else {}
        result.update(
            end_location=observation.get("location") if observation.get("status") != "Inference" else None,
            end_quote=observation.get("quote"),
            present=[new_pov] if new_pov else [],
            basis="viewpoint_last_location",
            previous_pov=old_pov,
        )
    return result


def advance_boundary_state(state, facts, chapter, text, bible):
    """Only adopted, grounded facts update truth. Missing facts supersede stale ends."""
    current = deepcopy(state)
    facts = with_ending_candidate(facts, text, bible, state)
    for key in ("end_location", "end_quote", "end_pov", "end_pov_quote", "present", "end_location_candidate"):
        current.pop(key, None)
    for key in ("end_location", "end_quote", "end_pov", "end_pov_quote", "end_location_candidate"):
        if facts.get(key):
            current[key] = facts[key]
    if facts.get("present_status") == "grounded":
        current["present"] = facts.get("present", [])
    history = current.setdefault("pov_locations", {})
    for side in ("opening", "end"):
        name, location = facts.get(side + "_pov"), facts.get(side + "_location")
        if name and location:
            history[name] = dict(
                location=location, quote=facts.get(side + "_quote"), chapter=chapter, status="Evidence"
            )
    for row in facts.get("character_locations", []):
        history[row["name"]] = dict(location=row["location"], quote=row["quote"], chapter=chapter, status="Evidence")
    current["known_locations"] = sorted(
        set(current.get("known_locations", []))
        | {facts[k] for k in ("opening_location", "end_location") if facts.get(k)}
    )
    return current
