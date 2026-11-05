"""Pure prose/state checks; extracted events do not establish semantic completeness."""

from collections import Counter
from copy import deepcopy
import re
import unicodedata

from .text import _paragraph_spans
from .longmemory import place_only


def length_band(target) -> tuple[int, int]:
    """Inclusive 85%–115% band, rounded inward without float rounding errors."""
    if type(target) is not int or target < 0:
        raise ValueError("target must be a nonnegative integer")
    return (17 * target + 19) // 20, 23 * target // 20


def _paragraphs(text):
    return [text[start:end] for start, end in _paragraph_spans(text)]


def _issue(kind, paragraph, quote, suggestion, hard=False, **details):
    return dict(
        kind=kind,
        paragraph=paragraph,
        quote=quote,
        suggestion=f"仅修复第{paragraph}段「{quote}」：{suggestion}",
        hard=hard,
        **details,
    )


def _sentences(paragraph):
    quotes = "\"'“”‘’「」『』«»‹›＂＇"
    pattern = r"[^。！？!?\r\n]+[。！？!?]*(?:[”」』][^。！？!?“「『\r\n]{0,12}(?:说|问|道|答|喊|提醒)[。！？!?]+)?"
    for match in re.finditer(pattern, paragraph):
        original = match[0].strip()
        normalized = "".join(c for c in original if not c.isspace() and c not in quotes)
        while normalized and unicodedata.category(normalized[0]).startswith("P"):
            normalized = normalized[1:]
        while normalized and unicodedata.category(normalized[-1]).startswith("P"):
            normalized = normalized[:-1]
        if len(re.sub(r"[^\w]", "", normalized)) >= 8 and re.search(
            r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]", normalized
        ):
            yield normalized, original


def text_issues(text, banned_phrases=(), previous_texts=()) -> list[dict]:
    """Report banned phrases and third-or-later sentences of at least 8 chars.

    Emit at most one issue per paragraph and kind; evidence retains every
    offending original sentence occurrence, including duplicates in one paragraph.
    Internal punctuation is significant; spaces and quotes are not.
    """
    counts = Counter(key for old in previous_texts for p in _paragraphs(old) for key, _ in _sentences(p))
    issues = []
    for number, paragraph in enumerate(_paragraphs(text), 1):
        banned = list(dict.fromkeys(p for p in banned_phrases if p and p in paragraph))
        if banned:
            issues.append(
                _issue("banned_phrase", number, paragraph, f"改写禁用原词 {banned!r} 所在语句。", evidence=banned)
            )
        repeated = []
        for key, original in _sentences(paragraph):
            counts[key] += 1
            if counts[key] > 2:
                repeated.append(original)
        if repeated:
            issues.append(
                _issue(
                    "repeated_sentence",
                    number,
                    paragraph,
                    f"删改第三次及以后重复的原句 {repeated!r}。",
                    evidence=repeated,
                )
            )
    return issues


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _event_error(event, paragraphs, quote_match=True):
    if not isinstance(event, dict):
        return "事件必须是对象。"
    number, quote = event.get("paragraph"), event.get("quote")
    if type(number) is not int or not 1 <= number <= len(paragraphs):
        return "段落编号无效。"
    if quote_match and (not _nonempty(quote) or quote not in paragraphs[number - 1]):
        return "引文必须逐字出现在指定段落。"
    for flag in ("flashback", "remote", "projected"):
        if type(event.get(flag, False)) is not bool:
            return f"{flag} 必须是布尔值。"
    kind = event.get("kind")
    if kind in ("character", "object"):
        if not all(_nonempty(event.get(k)) for k in ("name", "location")):
            return "姓名或物品名、地点必须是非空字符串。"
        actions = ("enter", "leave", "move", "stay") if kind == "character" else ("move", "stay")
        if event.get("action") not in actions:
            return "动作无效。"
        if kind == "character" and "present" in event:
            if type(event["present"]) is not bool:
                return "present 必须是布尔值。"
            if event["action"] in ("enter", "leave") and event["present"] != (event["action"] == "enter"):
                return "进出动作与 present 不一致。"
        if kind == "object" and not isinstance(event.get("owner"), str):
            return "owner 必须是字符串，无主人用空字符串。"
    elif kind == "death":
        if not _nonempty(event.get("name")):
            return "死亡事件须有姓名。"
    elif kind == "time":
        if (
            type(event.get("day")) is not int
            or event["day"] < 0
            or type(event.get("minute")) is not int
            or not 0 <= event["minute"] < 1440
        ):
            return "day 必须是非负整数，minute 必须是 0 到 1439 的整数。"
    elif kind == "foreshadow":
        if not _nonempty(event.get("name")) or event.get("status") not in ("open", "closed"):
            return "伏笔须有名称和 open/closed 状态。"
    else:
        return "事件类型无效。"
    return None


def event_structure_valid(events, text):
    paragraphs = _paragraphs(text)
    return bool(events) and all(_event_error(e, paragraphs, quote_match=False) is None for e in events)


def _location(location, state):
    """An exact character name can denote that character's current place."""
    return state["characters"].get(location, {}).get("location", location)


def same_place(left, right):
    left, right = place_only(left), place_only(right)
    if left == right:
        return True
    short, long = sorted((left, right), key=len)
    if not long.startswith(short):
        return False
    detail = long[len(short) :]
    return detail.startswith(
        (
            "内",
            "里",
            "门内",
            "墙上",
            "墙砖上",
            "窗边",
            "桌边",
            "中央",
            "角落",
            "营帐",
            "帐篷",
            "阵台",
            "石阶",
            "桌上",
            "地面",
            "操作台",
            "工作台",
            "操作间",
        )
    )


def _physical(name, place, sentence):
    if re.search(
        r"计划|打算|准备|安排|希望|想象|设想|将|要|若|如果|假如|可能|回忆|记忆|想起|梦|曾|昨日|昨天|明日|明天|后来|此前|先前|刚才|电话|视频|通话|远程|投影|屏幕|画面|照片|画像|传音|说|问|道|[“”「」\"]|并非|不|没有|遗体|尸|离开|走到|来到|随后|然后|之后|接着|片刻|转身|等到|待到|再|才|又|(?:分钟|小时|天|年)后",
        sentence,
    ):
        return False
    return bool(
        re.search(
            re.escape(name)
            + r"(?:正|仍|却|还|同时)?(?:出现|站|坐|留|待|守|躺|跪)?在"
            + re.escape(place)
            + r"(?=$|[，,；;：:\s]|[站坐等守看听吃喝走跑跪躺])",
            sentence,
        )
    )


def roll_day(old, current, paragraph, between=""):
    """Normalize an implicit overnight rollover; never mutate either clock."""
    if not isinstance(old, dict) or not isinstance(current, dict):
        return current
    if any(type(c.get(k)) is not int for c in (old, current) for k in ("day", "minute")):
        return current
    if current["day"] * 1440 + current["minute"] >= old["day"] * 1440 + old["minute"]:
        return current
    signal = re.search(r"次日|第二天|翌日|清晨|早上|早晨|黎明", paragraph)
    overnight = current["minute"] < old["minute"] and re.search(
        r"睡(?:下|去|着|醒|过|了|眠)|入睡|一觉|过夜|一夜|天亮", between
    )
    return dict(current, day=old["day"] + 1) if signal or overnight else current


def _conflict(state, event, paragraph):
    kind = event["kind"]
    name, place = event.get("name"), event.get("location")
    if kind == "character" and event["action"] != "leave" and event.get("present", True):
        old = state["characters"].get(name, {})
        for sentence in re.split(r"[。！？!?\n]", paragraph):
            if not _physical(name, place, sentence):
                continue
            if old.get("dead") is True or name in state.get("_dead", ()):
                return "dead_character", f"{name}已经死亡；改正其当前在场行为。"
            previous = old.get("location")
            if (
                previous
                and not same_place(previous, place)
                and not (previous in place or place in previous)
                and re.search(r"同一(?:时间|时刻)|与此同时|同时", sentence)
                and _physical(name, previous, sentence)
            ):
                return "simultaneous_locations", f"{name}同一时刻在{previous}和{place}；改正同时在场矛盾。"
    if kind == "time" and state["clock"] is not None:
        source = paragraph + str(event.get("source", ""))
        if re.search(r"最早|预约|预计|明日|明天|计划|打算|准备|将|未来|次日|第二天|翌日", source):
            return None
        if not re.search(
            r"第[零〇一二两三四五六七八九十百\d]+天|[零〇一二两三四五六七八九十百\d]+(?:点|时|分)|凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|深夜|午夜",
            paragraph,
        ):
            return None
        old = state["clock"]
        if event["day"] * 1440 + event["minute"] < old["day"] * 1440 + old["minute"]:
            return "time_reversal", f"当前已到第{old['day']}天第{old['minute']}分钟；改正时间或明确这是回忆。"
    return None


def _apply(state, event):
    kind, name = event["kind"], event.get("name")
    if kind == "character":
        action, place = event["action"], event["location"]
        state["characters"][name] = dict(
            state["characters"].get(name, {}), location=place, present=event.get("present", action != "leave")
        )
        if action in ("enter", "move"):
            for obj in state["objects"].values():
                if obj["owner"] == name:
                    obj["location"] = place
    elif kind == "death":
        state["characters"].setdefault(name, {}).update(dead=True, present=False)
    elif kind == "object":
        state["objects"][name] = dict(location=_location(event["location"], state), owner=event["owner"])
    elif kind == "time":
        state["clock"] = dict(day=event["day"], minute=event["minute"])
    elif kind == "foreshadow":
        state["foreshadows"][name] = event["status"]


def _replay(previous_state, events, text, quote_match=True):
    paragraphs, state = _paragraphs(text), deepcopy(previous_state)
    for key, default in (
        ("characters", {}),
        ("objects", {}),
        ("clock", None),
        ("relationships", []),
        ("foreshadows", {}),
    ):
        state.setdefault(key, default)
    issues, accepted = [], []
    for index, event in enumerate(events):
        error = _event_error(event, paragraphs, quote_match=quote_match)
        if error:
            number = event.get("paragraph") if isinstance(event, dict) else None
            valid_number = type(number) is int and 1 <= number <= len(paragraphs)
            paragraph = paragraphs[number - 1] if valid_number else ""
            issues.append(
                _issue(
                    "ungrounded_event",
                    number if valid_number else 1,
                    paragraph,
                    error + "重新提取事件，未将其写入状态。",
                    event_index=index,
                )
            )
        else:
            accepted.append((event["paragraph"], index, event))
    last_clock = 0
    for number, index, event in sorted(accepted, key=lambda item: item[:2]):
        if event.get("flashback") or event.get("projected") or event.get("remote"):
            continue
        if event["kind"] == "time":
            event = roll_day(state["clock"], event, paragraphs[number - 1], "\n".join(paragraphs[last_clock:number]))
            last_clock = number
        conflict = _conflict(state, event, paragraphs[number - 1])
        if conflict:
            kind, suggestion = conflict
            issues.append(
                _issue(
                    kind,
                    number,
                    paragraphs[number - 1],
                    suggestion,
                    True,
                    event_index=index,
                    evidence=event.get("quote", paragraphs[number - 1]),
                )
            )
        else:
            _apply(state, event)
    issues.sort(key=lambda item: (item["paragraph"], item["event_index"]))
    return state, issues


def check_events(previous_state, events, text) -> list[dict]:
    """Grounded issues in paragraph/input order; rejected claims never advance."""
    return _replay(previous_state, events, text)[1]


def high_confidence(state, events, text) -> list[dict]:
    """Three hard kinds only; state['_dead'] lists names; quote is optional."""
    return [issue for issue in _replay(state, events, text, quote_match=False)[1] if issue["hard"]]


def advance_state(previous, events, text) -> dict:
    """Copy state; skip invalid/conflicting, projected, remote and flashback events."""
    return _replay(previous, events, text)[0]
