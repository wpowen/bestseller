"""Shared fixture helper: give a minimal fake design the story layers the design gate requires."""

from copy import deepcopy

KINDS = ("main", "dark", "hidden", "romance", "growth", "support")


def complete_design(response):
    """Return a copy of ``dict(bible=..., threads=...)`` with every required design layer present."""
    result = deepcopy(response)
    bible = result["bible"]
    world = bible.setdefault("world", {})
    world.setdefault("truth_layers", [dict(layer=n, truth=f"第{n}层真相", reveal_not_before=n) for n in (1, 2, 3)])
    world.setdefault("factions", [dict(name=f"势力{n}", interest=f"利益{n}") for n in (1, 2, 3)])
    bible.setdefault(
        "main_foreshadows", [dict(id=f"f0{n}", description=f"第{n}件旧物的来历", close_volume=1) for n in (1, 2, 3)]
    )
    threads = result.get("threads")
    lines = threads.get("threads", []) if isinstance(threads, dict) else threads if isinstance(threads, list) else []
    lines = list(lines)
    present = {line.get("kind") for line in lines if isinstance(line, dict)}
    lines += [
        dict(id=kind, kind=kind, beats=[dict(id=f"{kind}-v1-1", volume=1, description=f"{kind}线的第一件具体事件")])
        for kind in KINDS
        if kind not in present
    ]
    result["threads"] = dict(threads=lines)
    return result
