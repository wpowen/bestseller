"""Small S4 request packing, loose object parsing and accounting."""

import json
import re
import warnings

import yaml

from .batch import usage_report
from .client import SYSTEM
from .longmemory import long_context
from .text import pack, parse_json


WRITER_CALLBACK_HEADING = "本章点名的跨卷回调原文"


class Recovered(dict):
    recovered_output = True


def normalize_sections(sections):
    """Serialize structured section bodies before the text-only context packer."""
    if isinstance(sections, dict):
        sections = sections.items()
    try:
        supplied = iter(sections)
    except TypeError:
        return []
    result = []
    for section in supplied:
        if not isinstance(section, (list, tuple)) or len(section) != 2:
            continue
        heading, body = section
        if not isinstance(heading, str):
            continue
        if not isinstance(body, str):
            try:
                body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                continue
        if body.strip():
            result.append((heading, body))
    return result


def recover_fields(raw):
    decoder = json.JSONDecoder()
    result = {}
    keys = "body scene rename ending_reason injuries realm_displayed realm_display_quote realm_display_pid previous_delta paragraph_edits patch tail opens_new_mainline type payoff gains costs solutions demotion_event structure edits summary state advances events scene_types actual_type actual_payoff actual_hook_type hook_type double_or_reawakening terms opens_new_mainline reason actual_structure voice_issues r2_events ai_examples cards paragraphs replacements optional_deletions characters objects object_aliases foreshadows clock dashboard bible threads title premise reader_expectation selling_points power world volumes main_foreshadows initial_dashboard feedback scores reasons".split()
    for key in keys:
        match = re.search(r'(?<!\\)"' + key + r'"\s*:\s*', raw)
        if match:
            try:
                result[key] = decoder.raw_decode(raw[match.end() :])[0]
            except ValueError:
                pass
    if "replacements" not in result and '"replacements"' in raw:
        pairs = {}
        for match in re.finditer(r'"(\d+)"\s*:\s*("(?:[^"\\]|\\.)*")', raw):
            try:
                pairs[match[1]] = json.loads(match[2])
            except ValueError:
                pass
        if pairs:
            result["replacements"] = pairs
    fields = {
        k: result[k]
        for k in ("characters", "objects", "object_aliases", "foreshadows", "clock", "dashboard")
        if k in result
    }
    if "state" not in result and fields:
        result["state"] = fields
    if not result:
        raise ValueError("Expected a JSON/YAML object")
    return result


def loose_object(raw):
    recovered = False
    try:
        value = parse_json(raw)
        if isinstance(value, dict) and not raw.rstrip().endswith(("}", "]")):
            value = {**recover_fields(raw), **value}
            recovered = True
    except ValueError:
        cleaned = re.sub(r"^```(?:json|yaml)?\s*|\s*```$", "", raw.strip())
        try:
            value = yaml.safe_load(cleaned)
        except yaml.YAMLError:
            value = recover_fields(cleaned)
            recovered = True
        if not isinstance(value, (dict, list)):
            raise ValueError("Expected an object")

    def normalize(item):
        if isinstance(item, list):
            return [normalize(x) for x in item]
        if not isinstance(item, dict):
            return item if isinstance(item, (str, int, float, bool, type(None))) else None
        result = {k: normalize(v) for k, v in item.items()}
        for key in ("number", "start", "end", "day", "minute", "paragraph", "index", "event_index", "chapter"):
            if isinstance(result.get(key), str) and result[key].isdigit():
                result[key] = int(result[key])
        if isinstance(result.get("present"), str) and result["present"].lower() in ("true", "false"):
            result["present"] = result["present"].lower() == "true"
        return result

    result = normalize(value)
    return Recovered(result) if recovered and isinstance(result, dict) else result


def _writer_context(sections, budget, drops):
    """Keep the whole scene, facts and obligations. Sacrifice advisory sections first."""
    blocks = [(name, f"## {name}\n{body}") for name, body in sections]
    optional = (
        "授权参考的统计风格特征",
        "章首句式类别约束",
        "本阶段已用解法步骤，不能只换名词照搬",
        # This comparison uses the prior plan. Keep the separate factual handoff.
        "上章冲突及写作避重约束",
        "人物内里，只用来决定他怎么想、怎么说、怎么做，不要照抄成句子",
    )

    def record_drop(name):
        if drops is None:
            warnings.warn(f"写手输入省略 {name}", RuntimeWarning, stacklevel=4)
        elif name not in drops:
            drops.append(name)

    for name in optional:
        if sum(len(block) + 2 for _, block in blocks) - 2 <= budget:
            break
        if any(heading == name for heading, _ in blocks):
            blocks = [(heading, block) for heading, block in blocks if heading != name]
            record_drop(name)
    if sum(len(block) + 2 for _, block in blocks) - 2 > budget:
        callbacks = [(name, block) for name, block in blocks if name.startswith(WRITER_CALLBACK_HEADING)]
        blocks = [(name, block) for name, block in blocks if not name.startswith(WRITER_CALLBACK_HEADING)]
        size = sum(len(block) + 2 for _, block in blocks) - 2
        for name, block in callbacks:
            if len(block) + 2 <= budget - size:
                blocks.append((name, block))
                size += len(block) + 2
            else:
                record_drop(name)
    context = "\n\n".join(block for _, block in blocks)
    if len(context) > budget:
        sizes = "、".join(f"{name} {len(block)}" for name, block in blocks)
        return "", f"写手必要材料需要 {len(context)} 字符，只剩 {budget}；各节字符数：{sizes}"
    return context, ""


def ask(
    client,
    stage,
    instruction,
    sections,
    schema=None,
    input_limit=30000,
    *,
    writer_drops=None,
    parser=loose_object,
    **kwargs,
):
    sections = normalize_sections(sections)
    writer = any(name == "本章章纲" for name, _ in sections)
    limit = min(30000, input_limit)
    if writer:
        limit = min(limit, getattr(client, "context_limit", None) or limit)
    system = getattr(client, "system", SYSTEM) if writer else SYSTEM
    budget = limit - len(client.prefix) - len(system) - len(instruction) - len(schema or "") - 350
    if writer:
        context, error = _writer_context(sections, budget, writer_drops)
        if error:
            kwargs["input_error"] = error
    else:
        context = long_context(sections, max(0, budget))
    return client.stage(stage, instruction + "\n" + context, schema, parser=parser, **kwargs)


def costs(client):
    return usage_report(client.calls)


def paragraph_edits(value, text):
    from .text import _paragraph_spans

    edits, seen = [], set()
    source = value.get("edits", value.get("patches", []))
    if not isinstance(source, list):
        source = []
    for item in source:
        if not isinstance(item, dict):
            continue
        number = item.get("paragraph", item.get("paragraph_id"))
        if isinstance(number, str) and number.isdigit():
            number = int(number)
        replacement = item.get("text", item.get("replacement"))
        if (
            type(number) is int
            and 1 <= number <= len(_paragraph_spans(text))
            and number not in seen
            and isinstance(replacement, str)
            and replacement.strip()
        ):
            edits.append(dict(paragraph=number, text=replacement.strip()))
            seen.add(number)
    return dict(value, edits=edits)


def memory_sections(chapter, previous_end, state, summaries, volume, canon, rolling):
    names = pack(chapter)
    characters = state.get("characters", {})
    relevant = {n: v for n, v in characters.items() if n in names or v.get("present")}
    open_threads = {n: v for n, v in state.get("foreshadows", {}).items() if v == "open"}
    selected = dict(
        characters=relevant,
        objects={n: v for n, v in state.get("objects", {}).items() if n in names},
        clock=state.get("clock"),
        relationships=state.get("relationships", []),
        foreshadows=open_threads,
    )
    # Stable slots prevent later chapters from receiving an ever-growing history.
    return [
        ("本章大纲", pack(chapter)[:2500]),
        ("上章结尾", previous_end[-1400:]),
        ("相关人物及物品时间伏笔当前状态", pack(selected)[:4500]),
        ("近三章摘要", pack(summaries[-3:])[:1400]),
        ("当前卷纲", pack(volume)[:2500]),
        ("固定人物设定与能力边界", pack(canon)[:3200]),
        ("滚动记忆", rolling[:900]),
    ]
