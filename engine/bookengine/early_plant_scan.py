"""Scan saved chapter bodies past a durable, contiguous cursor."""

from .foreshadow_verification import plant_observation


def scan_early_plant(out, duty, before, checked):
    """Find the first prior plant, leaving missing chapter bodies eligible for a later retry."""
    for number in range(checked + 1, before):
        try:
            text = (out / f"chapter-{number:03d}.body.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        if number == checked + 1:
            checked = number
        proof = plant_observation(duty, number, text)
        if proof["status"] == "done":
            return checked, proof
    return checked, None
