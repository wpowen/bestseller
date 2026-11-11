"""Ground and compare the semantic step structure of chapter solutions."""

from collections import defaultdict
import json
from pathlib import Path
import re

from .promise_io import precise_ask
from .quote_text import literal_quote
from .solution_substance import chapter_nouns, solution_context, stage_key
from .ability_methods import primary_target
from .storage import load
from .text import _paragraph_spans, _sentences
from tools.book_checkup import clean_prose


STEP_ROLES = ("感知", "判断", "行动", "结果")
STEP_PATTERNS = {
    "感知": {"借用感官", "直接观察", "询问证人", "工具测量", "主动试验", "查阅记录"},
    "判断": {"辨认线索", "比较证据", "推断原因", "识破伪装", "权衡风险", "破解规则"},
    "行动": {"选择路径", "设置机关", "正面对抗", "谈判交换", "救援转移", "操作物件", "联合他人", "隐蔽绕行"},
    "结果": {"识破陷阱", "打开通路", "制伏对手", "救出目标", "取得资源", "脱离危险", "揭开事实", "改变关系"},
}


def validate_solution_sequence(raw, text):
    """Keep a four-step observation only when every step has a literal prose quote."""
    source = raw if isinstance(raw, dict) else {}
    supplied = source.get("steps") if isinstance(source.get("steps"), list) else []
    steps = []
    for expected, row in zip(STEP_ROLES, supplied):
        if (
            not isinstance(row, dict)
            or row.get("role") != expected
            or row.get("pattern") not in STEP_PATTERNS[expected]
        ):
            break
        quote = literal_quote(text, row.get("quote"))
        if not isinstance(quote, str) or not quote.strip() or quote not in text:
            break
        steps.append(dict(role=expected, pattern=row["pattern"], quote=quote))
    grounded = len(supplied) == len(STEP_ROLES) and len(steps) == len(STEP_ROLES)
    return {
        "status": "Evidence" if grounded else "Unknown",
        "steps": steps if grounded else [],
        "signature": [row["pattern"] for row in steps] if grounded else [],
        "surface_nouns": [
            value.strip() for value in source.get("surface_nouns", []) if isinstance(value, str) and value.strip()
        ][:8],
    }


def normalize_planned_sequence(row):
    """Normalize model planning labels without presenting them as prose evidence."""
    supplied = row.get("solution_steps") if isinstance(row, dict) else None
    supplied = supplied if isinstance(supplied, list) else []
    steps = []
    for expected, item in zip(STEP_ROLES, supplied):
        if (
            not isinstance(item, dict)
            or item.get("role") != expected
            or item.get("pattern") not in STEP_PATTERNS[expected]
        ):
            break
        detail = item.get("detail") if isinstance(item.get("detail"), str) else ""
        steps.append(dict(role=expected, pattern=item["pattern"], detail=detail[:80]))
    return {
        "status": "Planning" if len(supplied) == len(STEP_ROLES) and len(steps) == len(STEP_ROLES) else "Unknown",
        "steps": steps if len(steps) == len(STEP_ROLES) else [],
        "signature": [item["pattern"] for item in steps] if len(steps) == len(STEP_ROLES) else [],
    }


def _sequence(row):
    if not isinstance(row, dict):
        return None
    direct = row.get("solution_sequence")
    if isinstance(direct, dict):
        return direct
    if isinstance(row.get("solution_steps"), list):
        return normalize_planned_sequence(row)
    polish = row.get("polish")
    note = polish.get("solution_sequence") if isinstance(polish, dict) else None
    return note.get("after") if isinstance(note, dict) and isinstance(note.get("after"), dict) else None


def solution_sequence_issues(bible, history, batch):
    """Flag same-stage repeated four-step signatures, independent of surface nouns."""
    targets = {row.get("number") for row in batch if isinstance(row, dict)}
    combined = {
        row["number"]: row for row in [*history, *batch] if isinstance(row, dict) and type(row.get("number")) is int
    }
    seen = defaultdict(list)
    issues = []
    for number, row in sorted(combined.items()):
        observed = _sequence(row)
        signature = observed.get("signature") if isinstance(observed, dict) else None
        if (
            bible.get("plot_contract_version")
            and number in targets
            and row.get("solution_method")
            and (not isinstance(signature, list) or len(signature) != len(STEP_ROLES))
        ):
            issues.append(
                dict(
                    kind="solution_sequence_missing",
                    targets=[number],
                    requirement="主要解法须给感知、判断、行动、结果四步结构；每步选合法类型并写具体动作",
                )
            )
            continue
        if not isinstance(signature, list) or len(signature) != len(STEP_ROLES):
            continue
        stage = stage_key(bible, number)
        if stage is None:
            continue
        key = stage, tuple(signature)
        seen[key].append(number)
        if len(seen[key]) > 1 and number in targets:
            issues.append(
                dict(
                    kind="solution_sequence_repetition",
                    stage=stage,
                    signature=list(signature),
                    chapters=list(seen[key]),
                    targets=[number],
                )
            )
    return issues


def solution_sequence_context(bible, history, start):
    stage = stage_key(bible, start)
    used = []
    for row in history:
        if stage is None or stage_key(bible, row.get("number", 0)) != stage:
            continue
        sequence = _sequence(row)
        if isinstance(sequence, dict) and len(sequence.get("signature", [])) == len(STEP_ROLES):
            used.append(dict(chapter=row["number"], structure=sequence["signature"]))
    return {
        "patterns": {role: sorted(STEP_PATTERNS[role]) for role in STEP_ROLES},
        "used_in_stage": used,
        "rule": "四步结构相同即重复，换能力名、物件名、地点名仍算同一结构",
    }


def planning_solution_context(bible, history, start):
    return dict(
        solution_context(bible, history, start), step_structures=solution_sequence_context(bible, history, start)
    )


def solution_plot_history(bible, history):
    return [
        dict(
            number=row["number"],
            nouns=chapter_nouns(row, bible),
            target=primary_target(row),
            solution_sequence=normalize_planned_sequence(row),
        )
        for row in history
    ]


_INFERENCE = {
    "感知": [
        ("借用感官", re.compile(r"借.{0,8}(?:鼻|眼|耳|感官|嗅觉|听觉)|按住.{0,8}(?:鼻|颈|背)|涌进.{0,6}感知")),
        ("工具测量", re.compile(r"(?:尺|秤|罗盘|仪器|刻度|测量|称量)")),
        ("询问证人", re.compile(r"(?:问|询问|口供|证词|证人)")),
        ("查阅记录", re.compile(r"(?:查|翻|看).{0,8}(?:账|册|记录|卷宗)")),
        ("主动试验", re.compile(r"(?:试|试探|故意).{0,12}(?:反应|变化|是否)")),
        ("直接观察", re.compile(r"(?:看见|望见|发现|观察|盯着|辨认)")),
    ],
    "判断": [
        ("辨认线索", re.compile(r"(?:辨|分清|辨明|认出).{0,16}(?:气味|痕迹|线索|方向|车辙|脚印)")),
        ("比较证据", re.compile(r"(?:对照|相比|核对|两份|一边.{0,12}另一边)")),
        ("识破伪装", re.compile(r"(?:伪装|诱饵|假(?:的|象|路)|故意误导|陷阱)")),
        ("权衡风险", re.compile(r"(?:权衡|取舍|风险|代价|先.{0,10}还是)")),
        ("破解规则", re.compile(r"(?:规律|规则|机关|机括).{0,12}(?:破解|看懂|明白)")),
        ("推断原因", re.compile(r"(?:说明|意味着|由此|所以|因此|推断|判断)")),
    ],
    "行动": [
        ("选择路径", re.compile(r"(?:选|改走|绕进|转入|指向).{0,12}(?:路|道|岔口|方向|坡|槽)")),
        ("设置机关", re.compile(r"(?:设|布|卡住|横在|钉进|绳索|木桩|机关)")),
        ("救援转移", re.compile(r"(?:救|拖出|转移|护送|接应)")),
        ("谈判交换", re.compile(r"(?:谈判|交换|条件|答应)")),
        ("联合他人", re.compile(r"(?:合力|联手|带人|众人一起)")),
        ("隐蔽绕行", re.compile(r"(?:潜入|隐蔽|绕开|贴地|避开)")),
        ("操作物件", re.compile(r"(?:打开|推动|扳动|插入|压下|砸开)")),
        ("正面对抗", re.compile(r"(?:迎战|扑向|挥刀|出拳|正面)")),
    ],
    "结果": [
        ("识破陷阱", re.compile(r"(?:陷阱|诱饵|假路).{0,12}(?:暴露|落空|被识破)|识破.{0,8}(?:陷阱|诱饵|伪装)")),
        ("打开通路", re.compile(r"(?:门|路|通道|缝隙).{0,8}(?:打开|露出|畅通|弹开)")),
        ("制伏对手", re.compile(r"(?:被擒|被绑|被捕|制伏|押下)")),
        ("救出目标", re.compile(r"(?:救出|脱困|撤出|被拖出)")),
        ("取得资源", re.compile(r"(?:夺回|拿到|取得|入手|到手)")),
        ("脱离危险", re.compile(r"(?:脱险|逃出|甩开|不再追来)")),
        ("改变关系", re.compile(r"(?:和解|信任|决裂|不再怀疑|认可)")),
        ("揭开事实", re.compile(r"(?:真相|事实|原来|证明|果然)")),
    ],
}


def infer_solution_sequence(text):
    """Conservatively route obvious prose; unmatched semantics remain Unknown."""
    spans = _sentences(text)
    found, floor = [], 0
    for role in STEP_ROLES:
        match = None
        for index, (start, end) in enumerate(spans):
            if index < floor:
                continue
            sentence = text[start:end].strip()
            pattern = next((label for label, rule in _INFERENCE[role] if rule.search(sentence)), None)
            if pattern:
                match = (index, dict(role=role, pattern=pattern, quote=sentence))
                break
        if match is None:
            return validate_solution_sequence({}, text)
        floor = match[0]
        found.append(match[1])
    return validate_solution_sequence({"steps": found}, text)


def _saved_sequences(out, number, bible):
    stage = stage_key(bible, number)
    found = []
    for path in sorted((Path(out) / "chapters").glob("[0-9][0-9][0-9].json")):
        try:
            row = load(path)
        except (OSError, ValueError):
            continue
        if row.get("number", 0) >= number or stage_key(bible, row.get("number", 0)) != stage:
            continue
        sequence = _sequence(row)
        if isinstance(sequence, dict) and len(sequence.get("signature", [])) == len(STEP_ROLES):
            found.append(dict(chapter=row["number"], signature=sequence["signature"]))
    return found


def writer_solution_sequence_context(out, number, bible):
    used = _saved_sequences(out, number, bible)
    return [("本阶段已用解法步骤，不能只换名词照搬", used)] if used else []


def _paragraph_targets(text, quotes):
    spans = _paragraph_spans(text)
    return {
        index
        for index, (start, end) in enumerate(spans, 1)
        if any(quote in text[start:end] for quote in quotes if isinstance(quote, str))
    }


def repair_solution_sequence(client, prefix, text, raw, failed, state, bible, outline, out, extract, ask):
    """Rewrite a grounded repeated sequence once; otherwise add no model call."""
    before = infer_solution_sequence(text)
    previous = _saved_sequences(out, outline["number"], bible)
    duplicates = [row for row in previous if row["signature"] == before.get("signature")]
    note = {"status": before["status"], "before": before, "after": before, "duplicates": duplicates, "edits": []}
    if before["status"] != "Evidence" or not duplicates:
        return text, raw, failed, note
    spans = _paragraph_spans(text)
    targets = _paragraph_targets(text, [step["quote"] for step in before["steps"]])
    allowed = [
        dict(id=index, original=text[start:end]) for index, (start, end) in enumerate(spans, 1) if index in targets
    ]
    response = precise_ask(
        ask,
        client,
        prefix + "-solution-sequence-repair",
        "只改允许段，把主要破局换成不同的感知、判断、行动、结果因果链；保留人物、地点、必须事件和直接结果。"
        "不能只换能力名、物件名或路线名。返回逐段原文和替换后的完整自然段。",
        [("重复结构", json.dumps([*duplicates, before["signature"]], ensure_ascii=False)), ("允许修改段", allowed)],
        '{"edits":[{"id":1,"original":"原段","text":"替换段"}]}',
        cap=5000,
        required_sources=[row["original"] for row in allowed],
    )
    if isinstance(response, dict) and response.get("status") == "skipped":
        return text, raw, failed, dict(note, status="skipped", reason=response.get("reason"))
    edits = response.get("edits") if isinstance(response, dict) else None
    if not isinstance(edits, list) or not edits:
        return text, raw, failed, dict(note, status="rolled_back", reason="invalid_edits")
    candidate = text
    by_id = {row["id"]: row for row in allowed}
    for edit in sorted(edits, key=lambda row: row.get("id", 0), reverse=True):
        source = by_id.get(edit.get("id"))
        if not source or edit.get("original") != source["original"] or not isinstance(edit.get("text"), str):
            return text, raw, failed, dict(note, status="rolled_back", reason="invalid_edit_target")
        start, end = spans[edit["id"] - 1]
        candidate = candidate[:start] + edit["text"].strip() + candidate[end:]
    after = infer_solution_sequence(candidate)
    note.update(after=after, edits=edits)
    if (
        clean_prose(candidate)[0] != candidate
        or after["status"] != "Evidence"
        or any(row["signature"] == after["signature"] for row in previous)
    ):
        return text, raw, failed, dict(note, status="rolled_back", reason="sequence_recheck_failed")
    checked, invalid, _ = extract(
        client, prefix + "-solution-final-state", candidate, state, bible, outline, [], final=True
    )
    if invalid:
        return text, raw, failed, dict(note, status="rolled_back", reason="final_state_failed")
    return candidate, checked, False, dict(note, status="checked")


def solution_sequence_checkup(bible, chapters, book=None):
    """Summarize source-bound saved observations without inventing missing semantics."""
    observed = []
    unknown = []
    for chapter in chapters:
        raw = _sequence(chapter.get("meta", {}))
        if not isinstance(raw, dict) and book is not None:
            from .plot_observations import load_plot_observation

            source = "final" if str(chapter.get("path", "")).startswith("final/") else "draft"
            saved = load_plot_observation(Path(book), chapter, source)
            raw = saved.get("solution_sequence") if isinstance(saved, dict) else None
        if not isinstance(raw, dict):
            unknown.append(chapter["number"])
            continue
        value = raw if raw.get("status") == "Evidence" else validate_solution_sequence(raw, chapter.get("text", ""))
        if value["status"] != "Evidence":
            unknown.append(chapter["number"])
            continue
        observed.append(dict(number=chapter["number"], solution_sequence=value))
    groups = defaultdict(list)
    for row in observed:
        stage = stage_key(bible, row["number"])
        if stage is not None:
            groups[stage, tuple(row["solution_sequence"]["signature"])].append(row["number"])
    duplicates = [
        dict(stage=stage, signature=list(signature), chapters=numbers)
        for (stage, signature), numbers in groups.items()
        if len(numbers) > 1
    ]
    return {
        "duplicate_groups": sorted(duplicates, key=lambda row: (row["stage"], row["chapters"])),
        "observed_chapters": [row["number"] for row in observed],
        "unknown_chapters": unknown,
        "authority": "grounded_saved_observation",
    }
