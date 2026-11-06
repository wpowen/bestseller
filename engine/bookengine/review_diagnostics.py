"""Read-only design validation and source-grounded saved opening review summaries."""

from collections import Counter

from .address_contracts import address_issues
from .cost_design import cost_issues


def design_findings(bible, chapters):
    costs = cost_issues(bible)
    addresses = address_issues(bible)
    corpus = {c["number"]: c["text"] for c in chapters}
    reviews = []
    for chapter in chapters:
        number = chapter["number"]
        if number > 3:
            continue
        note = chapter["meta"].get("opening_promises")
        note = note if isinstance(note, dict) else {}
        reviewed = note.get("after", {})
        items = reviewed.get("items", []) if isinstance(reviewed, dict) else []
        items = items if isinstance(items, list) else []
        status = note.get("status", "unknown")
        if status in ("checked", "repaired"):
            if any(not isinstance(i, dict) or i.get("status") in ("missing", "unknown") for i in items):
                status = "unknown"
            for item in items:
                if not isinstance(item, dict) or item.get("status") != "fulfilled":
                    continue
                evidence = item.get("evidence", [])
                if not isinstance(evidence, list) or not any(
                    isinstance(e, dict)
                    and type(e.get("chapter")) is int
                    and 1 <= e["chapter"] <= number
                    and isinstance(e.get("quote"), str)
                    and e["quote"].strip()
                    and e["quote"] in corpus.get(e["chapter"], "")
                    for e in evidence
                ):
                    status = "unknown"
                    break
            if not items:
                status = "unknown"
        reviews.append(dict(chapter=number, status=status, saved_status=note.get("status"), items=items))
    return dict(
        cost_design=dict(
            status="invalid" if costs else "checked" if bible.get("volumes") else "unknown",
            invalid_stages=sorted({i["stage"] for i in costs}),
            issues=costs,
        ),
        address_design=dict(
            status="invalid" if addresses else "checked" if bible.get("characters") else "unknown", issues=addresses
        ),
        opening_promises=dict(
            counts=dict(Counter(r["status"] for r in reviews)), chapters=reviews, authority="agent_editorial_not_human"
        ),
    )
