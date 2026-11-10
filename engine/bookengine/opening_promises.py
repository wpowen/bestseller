"""Opening obligations and evidence-bound review; no lexical hit implies quality."""

import json
import re
from pathlib import Path

from .address_contracts import address_findings
from .promise_io import precise_ask

REVIEW_SCHEMA = json.dumps(
    dict(items=[dict(id="", status="unknown", evidence=[dict(chapter=1, quote="")], paragraphs=[1], reason="")]),
    ensure_ascii=False,
)


class PromiseContext(dict):
    """Saved bodies plus grounded review state for one chapter transaction."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fulfilled = {}
        self.retry_ids = None


def _saved_fulfilled(note, bodies):
    found = {}

    def visit(value):
        if isinstance(value, dict):
            rows = value.get("items")
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict) or row.get("kind") != "reader" or row.get("status") != "fulfilled":
                        continue
                    evidence = row.get("evidence", [])
                    grounded = [
                        item
                        for item in evidence
                        if isinstance(item, dict)
                        and type(item.get("chapter")) is int
                        and item["chapter"] in bodies
                        and isinstance(item.get("quote"), str)
                        and item["quote"].strip()
                        and item["quote"] in bodies[item["chapter"]]
                    ]
                    if grounded and isinstance(row.get("id"), str):
                        found[row["id"]] = dict(row, evidence=grounded, paragraphs=[])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(note)
    return found


def previous_promise_context(out, number):
    """Read saved bodies and grounded promise evidence without rewriting checkpoints."""
    context = PromiseContext(
        {
            int(p.name.split("-")[1].split(".")[0]): p.read_text(encoding="utf-8")
            for p in sorted(Path(out).glob("chapter-*.body.md"))
            if p.name.split("-")[1].split(".")[0].isdigit() and int(p.name.split("-")[1].split(".")[0]) < number
        }
    )
    for chapter in context:
        path = Path(out) / "chapters" / f"{chapter:03d}.json"
        if not path.exists():
            continue
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        context.fulfilled.update(_saved_fulfilled(saved.get("opening_promises", {}), context))
    return context


def opening_contract(bible):
    """Keep source text alongside every opening obligation; later arcs stay later."""
    power = bible.get("power", {})
    core = json.dumps(dict(title=bible.get("title", ""), power=power), ensure_ascii=False)
    result = []
    has_source = bool(str(bible.get("title", "")).strip()) or bool(
        any(str(v).strip() for v in power.values()) if isinstance(power, dict) else power
    )
    for chapter in range(1, 4) if has_source else ():
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
    return (
        kind != "core_use"
        or len(re.findall(r"[\u3400-\u9fff]", quote)) >= 8
        and not re.search(r"没有|未曾|并未|不曾|尚未|不敢|不能|不打算|如果|假如|打算|准备|[？?]", quote)
    )


def _review_batch(ask, client, key, items, text, chapter):
    output_cap = min(7000, max(2500, len(items) * 400 + 500))
    return precise_ask(
        ask,
        client,
        key,
        "逐项核对承诺，不确定返回unknown。必须引用原文章号和逐字证据，不得编造。"
        "仅核验给定完整章节，核心能力必须实际使用，代价必须已经可见。"
        "核心能力须引用完整肯定叙述，说明实际操作及造成的结果；至少8个汉字，响声或拿起道具的短片段不算证据。"
        "缺失项给出本章可定向修补段号（从1起）。状态仅fulfilled/missing/unknown。",
        [("逐项验收", json.dumps(items, ensure_ascii=False)), ("本章正文", text), ("验收章号", str(chapter))],
        REVIEW_SCHEMA,
        cap=output_cap,
    )


def _verified_row(response, item, text, chapter):
    supplied = response.get("items", []) if isinstance(response, dict) else []
    matching = (
        [r for r in supplied if isinstance(r, dict) and r.get("id") == item["id"]] if isinstance(supplied, list) else []
    )
    row = matching[0] if len(matching) == 1 else {}
    evidence = row.get("evidence", [])
    grounded = (
        [
            e
            for e in evidence
            if isinstance(e, dict)
            and type(e.get("chapter")) is int
            and e["chapter"] == chapter
            and isinstance(e.get("quote"), str)
            and e["quote"].strip()
            and e["quote"] in text
            and _positive_quote(e["quote"], item["kind"])
        ]
        if isinstance(evidence, list)
        else []
    )
    status = (
        "fulfilled"
        if row.get("status") == "fulfilled" and grounded
        else "missing"
        if row.get("status") == "missing"
        else "unknown"
    )
    return dict(
        item,
        status=status,
        evidence=grounded,
        paragraphs=row.get("paragraphs", []),
        reason=row.get(
            "reason",
            response.get("context_unknown", "缺少可核实证据") if isinstance(response, dict) else "缺少可核实证据",
        ),
    )


def review_opening(client, prefix, text, bible, number, earlier, ask):
    """Check every due item; absent/ungrounded model evidence remains Unknown."""
    addresses = address_findings(text, bible, earlier, number=number)
    if not 1 <= number <= 3 and not addresses:
        return dict(status="not_due", items=[])
    # Derive from current source, never trust an old materialized contract.
    contract_items = (
        [i for i in opening_contract(bible) if i["chapter"] == number or i["kind"] == "reader"] if number <= 3 else []
    )
    retry_ids = getattr(earlier, "retry_ids", None) if "-recheck" in prefix else None
    items = [item for item in contract_items if retry_ids is None or item["id"] in retry_ids]
    if not items and not addresses:
        return dict(status="not_due", items=[], reason="no_opening_source")
    local = [i for i in items if i["kind"] != "reader"]
    response = _review_batch(ask, client, prefix + "-promise-review", local, text, number) if local else {}
    corpus = {**earlier, number: text}
    checked = []
    checked.extend(_verified_row(response, item, text, number) for item in local)
    readers = [item for item in items if item["kind"] == "reader"]
    saved_fulfilled = getattr(earlier, "fulfilled", {})
    resolved = {
        item["id"]: dict(saved_fulfilled[item["id"]], **item)
        for item in readers
        if item["id"] in saved_fulfilled
        and saved_fulfilled[item["id"]].get("source") == item.get("source")
        and saved_fulfilled[item["id"]].get("requirement") == item.get("requirement")
    }
    reports = {item["id"]: [] for item in readers}
    unresolved = [item for item in readers if item["id"] not in resolved]
    for chapter in range(1, number + 1):
        if not unresolved:
            break
        if chapter not in corpus:
            for item in unresolved:
                reports[item["id"]].append(
                    dict(item, status="unknown", evidence=[], paragraphs=[], reason="missing_saved_chapter")
                )
            continue
        reply = _review_batch(
            ask,
            client,
            f"{prefix}-reader-chapter-{chapter}-promise-review",
            unresolved,
            corpus[chapter],
            chapter,
        )
        remaining = []
        for item in unresolved:
            row = _verified_row(reply, item, corpus[chapter], chapter)
            if chapter != number:
                row["paragraphs"] = []
            reports[item["id"]].append(row)
            if row["status"] == "fulfilled":
                resolved[item["id"]] = row
            else:
                remaining.append(item)
        unresolved = remaining
    for item in readers:
        item_reports = reports[item["id"]]
        row = resolved.get(item["id"])
        if row is None:
            row = next(
                (report for report in item_reports if report["status"] == "unknown"),
                item_reports[-1],
            )
        checked.append(row)
    for index, row in enumerate(checked):
        item = next(item for item in items if item["id"] == row["id"])
        if item["chapter"] > number and row["status"] != "fulfilled":
            row = dict(row, status="pending", observed_status=row["status"])
            checked[index] = row
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
    if isinstance(earlier, PromiseContext):
        earlier.retry_ids = {item["id"] for item in checked if item["status"] in ("missing", "unknown")}
    return dict(
        status="unfulfilled"
        if any(i["status"] in ("missing", "unknown") for i in checked)
        else "pending"
        if any(i["status"] == "pending" for i in checked)
        else "checked",
        items=checked,
        authority="agent_editorial_not_human",
    )


def repair_opening_promises(client, prefix, text, raw, failed, state, bible, outline, earlier, extract, ask):
    """Entry point matching other chapter repair helpers; caller persists note."""
    from .q1_promise_repair import repair

    return repair(client, prefix, text, raw, failed, state, bible, outline, earlier, extract, ask)
