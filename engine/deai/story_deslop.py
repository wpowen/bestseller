"""Run the bundled story-deslop skill inside the chapter finalization pipeline."""

from collections import Counter
from functools import lru_cache
from hashlib import blake2s
from pathlib import Path
import re
import time
import unicodedata

from .adoption import entities, spill_reasons
from .client import ModelResponseError
from .flow import Result, Session
from .hygiene import introduced_types
from .story_deslop_evidence import (
    anchors_for,
    changed_action_outcome,
    changed_operator,
    removed_promise_condition,
    scan_route,
)
from .text import count, invariant, paragraphs, replace
from .validation import records, require

_ROOT = Path(__file__).resolve().parent / "data/story_deslop"
_FILES = ("SKILL.md", "references/anti-ai-writing.md", "references/banned-words.md", "scripts/normalize-punctuation.js")
_GATES = frozenset("ABCDEF")
_TIERS = ("轻度", "中度", "重度")


@lru_cache(maxsize=1)
def sources():
    return tuple((_ROOT / name).read_text(encoding="utf-8") for name in _FILES)


def identity():
    return {
        "name": "story-deslop",
        "version": re.search(r"(?m)^version: (.+)$", sources()[0])[1],
        "adapter_version": 4,
        "sources_blake2s": {name: blake2s(source.encode()).hexdigest() for name, source in zip(_FILES, sources())},
    }


def _section(text, start, end):
    return text[text.index(start) : text.index(end)]


@lru_cache(maxsize=1)
def _lexical_hints():
    source = _section(sources()[2], "## 一级禁用词", "## 二级禁用词")
    return frozenset(term for line in source.splitlines() if "、" in line for term in line.split("、"))


def _word_density(text, protected):
    size = count(text)
    for word in sorted((word for word in protected if word), key=len, reverse=True):
        text = text.replace(word, " " * len(word))
    return round(1000 * sum(text.count(term) for term in _lexical_hints()) / max(1, size), 2)


def _rows(value, key, eligible):
    rows = records(value, key)
    ids = [row.get("id") for row in rows]
    require(all(isinstance(pid, str) and pid in eligible for pid in ids), "ID outside the requested paragraphs")
    require(len(ids) == len(set(ids)), "Duplicate paragraph IDs")
    return rows


def _ground_findings(findings, index):
    grounded = {}
    unmatched = []
    for row in findings:
        pid, quote = row["id"], row["quote"]
        if quote not in index[pid]["text"]:
            matches = [key for key, part in index.items() if quote in part["text"]]
            if len(matches) != 1:
                unmatched.append(pid)
                continue
            pid = matches[0]
        if pid in grounded:
            # Keep competing findings separate; combining witnesses could authorize an unrelated edit.
            unmatched.append(row["id"])
            continue
        grounded[pid] = dict(row, id=pid)
    return list(grounded.values())[:16], unmatched


def validate_stage(stage, value, data):
    if stage == "story-deslop-scan":
        eligible = {row["id"]: row["text"] for row in data["source"]}
        require(value.get("tier") in _TIERS, "Missing skill tier")
        for finding in _rows(value, "findings", eligible):
            gates = finding.get("gates")
            quote = finding.get("quote")
            require(
                isinstance(gates, list) and bool(gates) and all(isinstance(g, str) and g in _GATES for g in gates),
                "Expected story-deslop gates A-F",
            )
            require(isinstance(quote, str) and quote.strip(), "Missing source quote")
            require(isinstance(finding.get("reason"), str) and finding["reason"].strip(), "Missing editorial reason")
    elif stage == "story-deslop-edit":
        eligible = {row["id"]: row["text"] for row in data["source"]}
        for row in _rows(value, "edits", {finding["id"] for finding in data["findings"]}):
            require(row.get("original") == eligible[row["id"]], "Edit must quote its original paragraph")
            require(isinstance(row.get("replacement"), str) and row["replacement"].strip(), "Missing replacement")
    elif stage == "story-deslop-recheck":
        eligible = {row["id"] for row in data["changes"]}
        # The model may comment on unchanged context. It cannot authorize edits there.
        verdicts = [
            row for row in records(value, "verdicts") if isinstance(row.get("id"), str) and row["id"] in eligible
        ]
        require({row["id"] for row in verdicts} == eligible, "Verdicts must cover every proposed edit")
        require(len(verdicts) == len(eligible), "Duplicate review IDs")
        require(type(value.get("facts_preserved")) is bool, "Missing whole-chapter fact verdict")
        require(
            all(
                type(r.get("preserves")) is bool
                and type(r.get("improves")) is bool
                and isinstance(r.get("reason"), str)
                and r["reason"].strip()
                for r in verdicts
            ),
            "Expected a preservation and readability verdict for each edit",
        )
        changes = {e["id"]: e for e in data["changes"]}
        for row in verdicts:
            change = changes[row["id"]]
            anchors = change.get("keep", [])
            if anchors and row["preserves"]:
                witness = row.get("fact_quote")
                if (
                    not isinstance(witness, str)
                    or witness not in change["after"]
                    or any(s not in witness for s in anchors)
                ):
                    row.update(preserves=False, reason="改稿引文没有覆盖受保护的动作或线索")
        value["verdicts"] = verdicts


def _trace(text, ctx, session, report):
    skill, guide, _, _ = sources()
    ps = paragraphs(text)
    if not ps:
        return text, []
    index = {p["pid"]: p for p in ps}
    source = [dict(id=p["pid"], text=p["text"]) for p in ps]
    ending_ids = {p["pid"] for p in ps[-3:]}
    names = [name for name in entities(ctx, {}) if isinstance(name, str)]
    protected = [*names, *ctx.get("deslop_whitelist", [])]
    density = _word_density(text, protected)
    common = dict(source=source, characters=ctx.get("characters", []), protected=protected, setting=ctx.get("setting"))
    scan = session.ask(
        "story-deslop-scan",
        _section(skill, "### 原则 2", "## 自然文本基准")
        + "\n"
        + _section(guide, "### 叠加式描写", "## 核心规则")
        + "\n逐段审读全章，至多列16项，允许空。kind 使用以下分类。"
        "redundant_explanation：对白、动作或内心已经交代过，叙述又解释同一件事。"
        "给出拟改的短原句 quote，和应该保留的另一处 supports（段号 id 和短原句 quote），可在同段。"
        "两处各自逐字、能唯一定位且不重叠；reason 写清已表达了什么。"
        "expression：其他具体的表达问题，指出牵碍了哪句话。仅命中词语、标点、短句、"
        "人物自己的腹诽、时代口吻、正常对白和必要术语解释都应保留。"
        "scene_structure：整场反复或场景选择失效，列原句并交回规划。"
        "insufficient_context：缺前情或事实，列原句及缺哪项。不能指认问题则不列。"
        "gates 用 A词语、B句式、C心理、D节奏、E对白、F结尾标记。"
        "按重点排序时照抄每段在 source 里的 id，不能给发现重新编号。"
        "先据原稿列每段不能随修辞删掉的短原词 keep：尤其是动作由谁用何工具、怎样取得结果、线索指向何物、付何代价。"
        "每项是最长24字的连续原文，选最小必要部分，动作归属可合并成一个短语。末三段命中时至少保留一个支撑结尾的原词。"
        '返回JSON {"tier":"轻度/中度/重度","findings":[{"id":"P1","kind":"redundant_explanation","gates":["C","D"],'
        '"quote":"该段中逐字复制的短引文，不跨句不拼接","supports":{"id":"P2","quote":"已交代相同意思的另一处原文"},"reason":"具体表现","keep":["动作或线索短原词"]}]}。',
        common,
        cap=3200,
        scope="-chapter",
    )
    findings, report["unmatched_quotes"] = _ground_findings(scan["findings"], index)
    report.update(tier=scan["tier"], banned_hits_per_1000=density, findings=findings)
    warnings = []
    eligible = []
    witnesses = {}
    lexical_hints = _lexical_hints().union(protected)
    for item in findings:
        pid = item["id"]
        item["route"] = scan_route(item, index, lexical_hints)
        if item["route"] != "local_edit":
            continue
        anchors, issue = anchors_for(
            index[pid]["text"], item.get("keep"), ctx.get("chapter_actions", {}).get(pid, []), pid in ending_ids
        )
        if issue:
            item["route"] = "insufficient_context"
            warnings.append(dict(pid=pid, reasons=[issue]))
        else:
            eligible.append(dict(item, keep=anchors))
            if item["kind"] == "redundant_explanation":
                witness = item["supports"]
                witnesses.setdefault(witness["id"], set()).add(witness["quote"])
    report["programmatic_rejections"] = warnings
    if not eligible:
        return text, []
    edited = session.ask(
        "story-deslop-edit",
        _section(skill, "### 原则 2", "## 自然文本基准")
        + "\n只给 findings 中可局改的段落一次最小改写，有更自然的原句时可以不给提案。"
        "重复解释只改多余的那一处，保留 supports 的原句。保留有用的内心独白、短对白、必要解释、术语和标点节奏。"
        "保留剧情功能与章末钩子，不擅自换物件、姓名、伤势、行动者、时序、对白意思或已知数字。"
        "原段须有内容，保留段界，只能改标注的段。只删有重复证据的句意；不要把信息挪到别段。"
        "findings.keep 是经核对的原词，可重写周围内容，不能丢掉原词及其行动者、手法、结果之间的关系。"
        "不加比喻、指节泛白等套招，不为凑数改动。全章汉字维持在原稿的90%-110%。"
        '返回JSON {"edits":[{"id":"P1","original":"逐字完整原段","replacement":"完整替换段"}]}。',
        dict(common, findings=eligible, ending=ps[-1]["text"]),
        cap=5000,
        scope="-chapter",
    )
    report["proposed_paragraphs"] = sum(
        row["replacement"].strip() != index[row["id"]]["text"] for row in edited["edits"]
    )
    edits = []
    findings = {f["id"]: f for f in eligible}
    for row in edited["edits"]:
        p = index[row["id"]]
        old = p["text"]
        new = row["replacement"].strip()
        if new == old:
            continue
        reasons = invariant(old, new, ctx, protected) + introduced_types(old, new) + spill_reasons(text, p, new)
        finding = findings[p["pid"]]
        anchors = finding["keep"]
        if finding["kind"] == "redundant_explanation" and finding["quote"] in new:
            reasons.append("untouched_target_quote")
        if any(quote not in new for quote in witnesses.get(p["pid"], ())):
            reasons.append("removed_support_quote")
        reasons.extend("missing_fact_anchor:" + term for term in anchors if term not in new)
        if changed_operator(old, new, anchors, names):
            reasons.append("changed_fact_operator")
        if changed_action_outcome(old, new, anchors):
            reasons.append("changed_fact_outcome")
        condition = removed_promise_condition(old, new)
        if condition:
            reasons.append("removed_promise_condition")
        if reasons:
            warning = dict(pid=p["pid"], reasons=reasons)
            if condition:
                warning["promise_evidence"] = condition
            warnings.append(warning)
            continue
        edits.append(
            dict(
                start=p["start"],
                end=p["end"],
                pid=p["pid"],
                before=old,
                replacement=new,
                types=finding["gates"],
                kind=finding["kind"],
                supports=finding.get("supports") if finding["kind"] == "redundant_explanation" else None,
                reason=finding["reason"],
                keep=anchors,
                source="story-deslop",
                round=1,
            )
        )
    # Model orders candidates by relevance. Reserve the chapter word band for the highest priorities.
    fitting = []
    for item in edits:
        if not invariant(text, replace(text, [*fitting, item]), ctx, protected, whole=True):
            fitting.append(item)
        else:
            warnings.append(dict(pid=item["pid"], reasons=["word_band"]))
    if not fitting:
        return text, []
    review = _recheck(session, text, fitting, ctx, ps[-1]["text"])
    report["review"] = review
    approved = {row["id"] for row in review["verdicts"] if row["preserves"] and row["improves"]}
    verifiable = [edit for edit in fitting if edit["pid"] in approved]
    if verifiable and verifiable != fitting:
        # Review the exact subset since discarded edits may affect nearby decisions.
        review = _recheck(session, text, verifiable, ctx, ps[-1]["text"], scope="-subset")
        report["subset_review"] = review
    if not review["facts_preserved"] or any(not row["preserves"] or not row["improves"] for row in review["verdicts"]):
        report["rollback"] = "recheck"
        return text, []
    candidate = replace(text, verifiable)
    if invariant(text, candidate, ctx, protected, whole=True):
        report["rollback"] = "word_or_fact_guard"
        return text, []
    return candidate, verifiable


def _recheck(session, text, edits, ctx, ending, scope="-chapter"):
    revised = replace(text, edits)
    return session.ask(
        "story-deslop-recheck",
        "你是复检编辑。依据 story-deslop 的保护规则核对改动，原文与候选都属于同一章。"
        "核对人物、叙事视角、动作归属、关系、物件状态、时序、对话目的、伏笔和最后一个画面。"
        "删掉已经由上下文交代的重复信息可以通过。发现事实变了、心理被编造、语气趋同或只是同义替换就拒绝对应段。"
        "对照 supports，确认它已经表达拟删去的意思，新增的态度、知识和动机不能当成重复；存疑就保留。"
        "同段阅读确有改善才给 improves=true。整章用于核对上下文；verdicts 逐项照抄 changes 的全部 id，不评其他段。"
        "有 keep 时逐项核实是谁用何物、怎样行动、改变了什么或留下什么。只提及物名、交换行动者、把结果写回愿望皆拒绝。"
        "对白含有承诺时，核实谁答应谁、什么条件下才做。条件可换说法；跨句或跨段时回读前后文，无法核实则注明待核。"
        "从本段改稿逐字给出覆盖 keep 且能证明这些关系的连续 fact_quote，无证据给 preserves=false。"
        '返回JSON {"facts_preserved":true,"verdicts":[{"id":"改动项的id","preserves":true,"improves":true,'
        '"fact_quote":"候选动作与结果的原句","reason":"逐项具体结论"}]}。',
        dict(
            changes=[
                dict(
                    id=e["pid"],
                    before=e["before"],
                    after=e["replacement"],
                    gates=e["types"],
                    keep=e["keep"],
                    kind=e["kind"],
                    supports=e["supports"],
                    reason=e["reason"],
                )
                for e in edits
            ],
            chapter=[dict(id=p["pid"], text=p["text"]) for p in paragraphs(revised)],
            characters=ctx.get("characters", []),
            original_ending=ending,
        ),
        cap=2600,
        scope=scope,
    )


def _without_punctuation(text):
    return "".join(char for char in text if not unicodedata.category(char).startswith("P") and not char.isspace())


def polish(text, ctx, llm):
    started = time.monotonic()
    session = Session(llm, ctx)
    before = len(getattr(llm, "calls", []))
    result = Result(text)
    report = identity()
    result.checks["story_deslop"] = report
    try:
        candidate, edits = _trace(text, ctx, session, report)
        # Count actual visible differences. A reviewed punctuation edit is not a prose rewrite.
        punctuation_edits = sum(
            _without_punctuation(e["before"]) == _without_punctuation(e["replacement"]) for e in edits
        )
        report["punctuation_edits"] = punctuation_edits
        report["prose_edits"] = len(edits) - punctuation_edits
        result.text, result.edits = candidate, edits
        result.status = "polished" if result.text != text else "unchanged"
        report.setdefault("proposed_paragraphs", 0)
        report["accepted_paragraphs"] = len(edits)
        if candidate == text:
            report["text_effect"] = "unchanged"
        elif report["prose_edits"]:
            report["text_effect"] = "paragraph_edits"
        else:
            report["text_effect"] = "punctuation_only"
        report["accepted_gates"] = dict(Counter(gate for edit in edits for gate in edit["types"]))
        report["status"] = "reviewed"
    except ModelResponseError as exc:
        report.update(status="retained", failure=dict(stage=exc.stage, reason=exc.reason))
        report.update(text_effect="retained", accepted_paragraphs=0, punctuation_edits=0, prose_edits=0)
        result.status = "retained"
        result.warnings.append(dict(rollback="model_response", **report["failure"]))
    calls = getattr(llm, "calls", None)
    usage = [item.get("usage") or {} for item in calls[before:]] if calls is not None else session.usages
    result.calls = len(calls) - before if calls is not None else session.calls
    result.prompt_tokens = sum(row.get("prompt_tokens") or 0 for row in usage)
    result.completion_tokens = sum(row.get("completion_tokens") or 0 for row in usage)
    result.seconds = round(time.monotonic() - started, 3)
    result.checks.update(original_chars=count(text), final_chars=count(result.text))
    return result
