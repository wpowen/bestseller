"""Fill missing detailed chapter plans before a rhythm skeleton can disguise a missing row."""

from .q1_schema import integer, rows


def has_scene_detail(row):
    return any(
        scene.get(key)
        for scene in rows(row.get("scenes"))
        for key in ("goal", "obstacle", "choice", "action", "result")
    )


def has_detail(row):
    return has_scene_detail(row) or any(
        row.get(key) for key in ("opening_conflict", "conflict", "required_event", "hook", "ending_content")
    )


def indexed_rows(raw, expected, *, positional=True):
    source = rows(raw.get("chapters")) if isinstance(raw, dict) else rows(raw)
    # A complete legacy array can use position. A partial array cannot identify omitted positions.
    positional = positional and len(source) == len(expected) and all(row.get("number") is None for row in source)
    result = {}
    for i, row in enumerate(source):
        n = expected[i] if positional else integer(row.get("number"))
        if n in expected and (n not in result or not has_detail(result[n]) and has_detail(row)):
            result[n] = row
    return result


def complete_details(client, ask, raw, start, end, sections, redo):
    expected = list(range(start, end + 1))
    mapped = indexed_rows(raw, expected)
    # Legacy rows can contain only scheduling metadata. Preserve them and diagnose
    # the gap without charging to re-plan a number the batch already returned.
    missing = [n for n in expected if n not in mapped]
    for n, row in mapped.items():
        if not has_detail(row):
            mapped[n] = dict(row, detail_outline_missing=True)
    if not missing:
        return mapped
    known = [
        {k: row.get(k) for k in ("number", "title", "opening_conflict", "ending_content")}
        for n, row in mapped.items()
        if n not in missing
    ]
    targets = [
        ("细纲只补这些章号，逐章返回场景与行动，沿用固定人物及阶段骨架", missing),
        ("已定相邻章，只读", known),
        *((key, value) for key, value in sections if key != "本批完整范围"),
    ]
    key = f"chapters-{start}-{end}-missing-" + "-".join(map(str, missing))
    extra = indexed_rows(ask(client, "outlines", key, targets, redo), missing, positional=False)
    for n in missing:
        if has_scene_detail(extra.get(n, {})):
            mapped[n] = extra[n]
        else:
            mapped[n] = dict(mapped.get(n, {}), detail_outline_missing=True)
    return mapped
