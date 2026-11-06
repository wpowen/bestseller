"""Viewpoint-specific boundary references and immutable state updates."""

from copy import deepcopy


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
    for key in ("end_location", "end_quote", "end_pov", "end_pov_quote", "present", "end_location_candidate"):
        current.pop(key, None)
    for key in ("end_location", "end_quote", "end_pov", "end_pov_quote"):
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
