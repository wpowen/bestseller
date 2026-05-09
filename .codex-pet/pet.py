#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


STATE_PATH = Path(__file__).with_name("pet.json")
LOCAL_TZ = timezone.utc


def now() -> datetime:
    return datetime.now().astimezone()


def clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def load_state() -> dict:
    with STATE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state: dict) -> None:
    state["last_updated_at"] = now().isoformat(timespec="seconds")
    with STATE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def age_state(state: dict) -> dict:
    last_updated = datetime.fromisoformat(state["last_updated_at"])
    elapsed_hours = max(0.0, (now() - last_updated).total_seconds() / 3600)
    if elapsed_hours == 0:
        return state

    state["fullness"] = clamp(state["fullness"] - elapsed_hours * 3.5)
    state["mood"] = clamp(state["mood"] - elapsed_hours * 1.8)
    energy_gain = elapsed_hours * (2.5 if state["fullness"] > 25 else 0.8)
    state["energy"] = clamp(state["energy"] + energy_gain)
    return state


def bar(value: int) -> str:
    filled = round(value / 10)
    return "[" + "#" * filled + "." * (10 - filled) + f"] {value:3d}"


def condition(state: dict) -> str:
    if state["fullness"] < 20:
        return "hungry"
    if state["mood"] < 25:
        return "lonely"
    if state["energy"] < 20:
        return "sleepy"
    if min(state["fullness"], state["mood"], state["energy"]) > 75:
        return "thriving"
    return "steady"


def status(state: dict) -> str:
    born = datetime.fromisoformat(state["born_at"])
    age_days = max(0, (now() - born).days)
    lines = [
        f"{state['name']} the {state['species']}",
        f"Condition: {condition(state)}",
        f"Age: {age_days} day(s)",
        f"Care count: {state['care_count']}",
        f"Fullness {bar(state['fullness'])}",
        f"Mood     {bar(state['mood'])}",
        f"Energy   {bar(state['energy'])}",
    ]
    return "\n".join(lines)


def care(state: dict, action: str, args: list[str]) -> str:
    if action == "feed":
        state["fullness"] = clamp(state["fullness"] + 26)
        state["mood"] = clamp(state["mood"] + 4)
        message = f"{state['name']} ate well."
    elif action == "play":
        state["mood"] = clamp(state["mood"] + 24)
        state["energy"] = clamp(state["energy"] - 14)
        state["fullness"] = clamp(state["fullness"] - 6)
        message = f"{state['name']} played for a while."
    elif action == "nap":
        state["energy"] = clamp(state["energy"] + 30)
        state["fullness"] = clamp(state["fullness"] - 4)
        message = f"{state['name']} took a nap."
    elif action == "rename":
        if not args:
            raise SystemExit("Usage: python3 .codex-pet/pet.py rename NewName")
        state["name"] = " ".join(args).strip()[:40] or state["name"]
        message = f"Renamed pet to {state['name']}."
    else:
        raise SystemExit(
            "Usage: python3 .codex-pet/pet.py "
            "status|feed|play|nap|rename NewName"
        )

    state["care_count"] += 1
    save_state(state)
    return message + "\n\n" + status(state)


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else "status"
    state = age_state(load_state())

    if action == "status":
        save_state(state)
        print(status(state))
        return 0

    print(care(state, action, argv[2:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
