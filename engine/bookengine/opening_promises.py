"""Opening obligations and evidence-bound review; no lexical hit implies quality."""

import json
import re
from pathlib import Path

from .address_contracts import address_findings

REVIEW_SCHEMA = json.dumps(
    dict(items=[dict(id="", status="unknown", evidence=[dict(chapter=1, quote="")], paragraphs=[1], reason="")]),
    ensure_ascii=False,
)


def previous_promise_context(out, number):
    """Read saved bodies, never rewrite them or trust derived seen-address state."""
    return {
        int(p.name.split("-")[1].split(".")[0]): p.read_text(encoding="utf-8")
        for p in sorted(Path(out).glob("chapter-*.body.md"))
        if p.name.split("-")[1].split(".")[0].isdigit() and int(p.name.split("-")[1].split(".")[0]) < number
    }


def opening_contract(bible):
    """Keep source text alongside every opening obligation; later arcs stay later."""
    power = bible.get("power", {})
    core = json.dumps(dict(title=bible.get("title", ""), power=power), ensure_ascii=False)
    result = []
    for chapter in range(1, 4):
        result.extend(
            [
                dict(
                    id=f"core-{chapter}",
                    chapter=chapter,
                    kind="core_use",
                    source=core,
                    requirement="本章至少实际使用一次书名或设定中的核心能力，造成实际结果；否定、设想、解释不算使用",
                ),
                dict(
                    id=f"cost-{chapter}",
                    chapter=chapter,
                    kind="visible_cost",
                    source=core,
                    requirement="本章使用核心能力时付出可见代价，展示具体损失或后果；预告代价不算兑现",
                ),
            ]
        )
    sources = [
        ("reader_expectation", bible.get("reader_expectation", "")),
        ("opening_promises", bible.get("opening_promises", [])),
    ]
    for field, value in sources:
        values = value if isinstance(value, list) else [value]
        for value in values:
            if not isinstance(value, str):
                continue
            for clause in re.split(r"[；;。\n]", value):
                clause = clause.strip()
                if not clause or re.search(r"此后|终局|终章|后续|每十章|每\d+章", clause):
                    continue
                # Preserve the original clause as source, split conjunctions so
                # a success in one half cannot hide an unfulfilled other half.
                for atom in re.split(r"[与及、]|以及", clause):
                    if atom.strip():
                        result.append(
                            dict(
                                id=f"reader-{len(result)}",
                                chapter=3,
                                kind="reader",
                                source_field=field,
                                source=clause,
                                requirement=atom.strip(),
                            )
                        )
    return result


def _positive_quote(quote, kind):
    return kind != "core_use" or not re.search(
        r"没有|未曾|并未|不曾|尚未|不敢|不能|不打算|如果|假如|打算|准备|[？?]", quote
    )


def review_opening(client, prefix, text, bible, number, earlier, ask):
    """Check every due item; absent/ungrounded model evidence remains Unknown."""
    addresses = address_findings(text, bible, earlier)
    if not 1 <= number <= 3 and not addresses:
        return dict(status="not_due", items=[])
    # Derive from current source, never trust an old materialized contract.
    items = (
        [i for i in opening_contract(bible) if i["chapter"] == number or i["kind"] == "reader"] if number <= 3 else []
    )
    response = (
        ask(
            client,
            prefix + "-promise-review",
            "逐项核对承诺，不确定返回unknown。必须引用原文章号和逐字证据。"
            "核心能力必须本章实际使用，代价必须本章可见；reader条目可引用此前章节。"
            "缺失项给出本章可定向修补的段号（从1起）。状态仅fulfilled/missing/unknown。",
            [
                ("逐项验收", json.dumps(items, ensure_ascii=False)),
                ("本章正文", text),
                ("此前正文", json.dumps({n: t for n, t in earlier.items() if n <= 3}, ensure_ascii=False)),
            ],
            REVIEW_SCHEMA,
            cap=2500,
            input_limit=14000,
        )
        if items
        else {}
    )
    supplied = response.get("items", []) if isinstance(response, dict) else []
    supplied = supplied if isinstance(supplied, list) else []
    corpus = {**earlier, number: text}
    checked = []
    for item in items:
        matching = [r for r in supplied if isinstance(r, dict) and r.get("id") == item["id"]]
        row = matching[0] if len(matching) == 1 else {}
        evidence = row.get("evidence", [])
        evidence = evidence if isinstance(evidence, list) else []
        grounded = [
            e
            for e in evidence
            if isinstance(e, dict)
            and type(e.get("chapter")) is int
            and 1 <= e["chapter"] <= number
            and isinstance(e.get("quote"), str)
            and e["quote"].strip()
            and e["quote"] in corpus.get(e["chapter"], "")
            and (item["kind"] == "reader" or e["chapter"] == number)
            and _positive_quote(e["quote"], item["kind"])
        ]
        status = (
            "fulfilled"
            if row.get("status") == "fulfilled" and grounded
            else "missing"
            if row.get("status") == "missing"
            else "unknown"
        )
        if item["chapter"] > number and status != "fulfilled":
            status = "pending"
        checked.append(
            dict(
                item,
                status=status,
                evidence=grounded,
                paragraphs=row.get("paragraphs", []),
                reason=row.get("reason", "缺少可核实证据"),
            )
        )
    for finding in addresses:
        checked.append(
            dict(
                finding,
                id="address-" + finding["address"],
                chapter=number,
                kind="address_origin",
                status="missing",
                requirement=finding["message"],
                paragraphs=[],
                evidence=[],
            )
        )
    return dict(
        status="unfulfilled" if any(i["status"] in ("missing", "unknown") for i in checked) else "checked",
        items=checked,
        authority="agent_editorial_not_human",
    )


def repair_opening_promises(client, prefix, text, raw, failed, state, bible, outline, earlier, extract, ask):
    """Entry point matching other chapter repair helpers; caller persists note."""
    from .q1_promise_repair import repair

    return repair(client, prefix, text, raw, failed, state, bible, outline, earlier, extract, ask)
