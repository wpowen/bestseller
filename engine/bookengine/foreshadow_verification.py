"""Keep mentions and verified foreshadowing events distinct in the story ledger."""

import json
import re

from .client import ContentRejected
from .promise_io import precise_ask
from .text import _sentences, parse_json

VERIFIED_KINDS = frozenset({"plant", "remind", "payoff", "truth"})
_NON_EVENT = re.compile(
    r"打算|准备要|计划要|想(?:要|着|去|确认|拆开|揭开)|准备(?:拆|找|寻找|查|揭|确认)|计划(?:拆|找|查|揭|确认)|"
    r"如果|假如|倘若|要是|等到明日|听说|传闻|据说|"
    r"(?:还|仍|尚)?未(?:能|曾|拆|见|找|查|揭|确认|认出)|(?:还|仍|尚)?没(?:有)?(?:人|能|去|拆|找|见|拿|揭|确认|认出)|"
    r"(?:还|仍|尚)?不(?:曾|敢|肯|愿|能|知道|知|清楚)|是否|是不是|是谁|是何人|[？?]"
)
_UNREAL_FRAME = re.compile(r"想象|设想|假想|预演|梦见|梦中|计划(?:是|如下)|以下(?:是|只是)计划")
_SCENE_PRESENCE = re.compile(
    r"看见|瞧见|望见|摸到|碰到|捡起|拿起|捏着|攥着|揭开|拆开|接过|递给|掏出|取出|"
    r"压着|刻着|搁着|躺着|放着|摆着|挂着|钉着|映着"
)
_ACTION_OR_RESULT = re.compile(
    r"折成|割开|剖开|劈开|拆开|打开|揭开|扯开|撕开|开启|启封|拔出|抽出|取出|印上|盖上|按下|刺入|"
    r"逼停|挡住|推开|撞开|撑开|照亮|映出|露出|亮出|浮出|浮现|显出|显露|消失|熄灭|碎裂|崩开|"
    r"转向|改道|改变|照着|循着|绕开|改口|认出|认得|看出|看清|发现|确认|得知|揭示|写着|刻着|"
    r"落款|署名|开口承认|说出了|其实|原来|就是|正是"
)
_LETTER_OPEN = re.compile(r"拆开|撕开|启封")
_SENDER = re.compile(r"写信人|寄信人|落款|署名")
_SENDER_REVEAL = re.compile(
    r"(?:认出|认得|得知|确认|看[见出清])[^。！？\n]{0,12}(?:写信人|寄信人)(?:就是|正是|是|为)[^。！？\n]{2,}"
    r"|(?:落款|署名)(?:赫然是|写着|是|为|[:：])[^。！？\n]{2,}"
)


def _plant_proof(duty, quote):
    prefix = ""
    for clause in re.split(r"[，,；;]", quote):
        prefix += clause
        if any(anchor in clause for anchor in duty["anchors"]) and _SCENE_PRESENCE.search(clause):
            return not _NON_EVENT.search(prefix)
    return False


def _proof(duty, quote):
    if duty.get("kind") == "plant":
        return _plant_proof(duty, quote)
    if _NON_EVENT.search(quote):
        return False
    event = duty.get("proof_event")
    event = event.strip(" 。！；，") if isinstance(event, str) else ""
    if event and event in quote:
        return True
    # For an opening-and-identification obligation, ground the action in the named object.
    requirement = event or duty["text"]
    if not (_LETTER_OPEN.search(requirement) and _SENDER.search(requirement)):
        return False
    if _SENDER_REVEAL.search(requirement):
        return False  # A specifically named result must match the supplied proof event.
    sender = _SENDER_REVEAL.search(quote)
    if not sender:
        return False
    anchors = "|".join(map(re.escape, duty["anchors"]))
    return bool(re.search(rf"(?:拆开|撕开|启封)(?:了|那封|这封){{0,2}}(?:{anchors})", quote[: sender.start()]))


def _observation(number, quote, status, verification):
    return dict(
        status=status, chapter=number if quote else None, quote=quote, mentioned=bool(quote), verification=verification
    )


def _framed(text, spans, index):
    start, end = spans[index]
    if _UNREAL_FRAME.search(text[start:end]):
        return True
    if index:
        previous, previous_end = spans[index - 1]
        if "\n\n" not in text[previous_end:start] and _UNREAL_FRAME.search(text[previous:previous_end]):
            return True
    if index + 1 < len(spans):
        following, following_end = spans[index + 1]
        if "\n\n" not in text[end:following] and re.search(
            r"(?:只是|不过是|全是|原是).{0,6}(?:想象|设想|假想|梦|预演)", text[following:following_end]
        ):
            return True
    return False


def _local(duty, number, text, spans, framed):
    anchors = duty.get("anchors") or []
    mentions = [
        (i, text[start:end]) for i, (start, end) in enumerate(spans) if any(a in text[start:end] for a in anchors)
    ]
    if not mentions:
        return _observation(number, "", "missing", "anchor_missing"), False
    for index, quote in mentions:
        if not framed[index] and _proof(duty, quote):
            return _observation(number, quote, "done", "deterministic"), False
    return _observation(number, mentions[0][1], "unverified", "mention_only"), True


def plant_observation(duty, number, text):
    """Recognize an earlier plant only if the object was actually present in a scene."""
    if not any(anchor in text for anchor in duty["anchors"]):
        return _observation(number, "", "missing", "anchor_missing")
    spans = _sentences(text)
    framed = [_framed(text, spans, index) for index in range(len(spans))]
    return _local(duty, number, text, spans, framed)[0]


def _names_anchor(duty, quote):
    """A full anchor, or a two-character piece of one (“半截焦黑灯芯” for 灯芯残片)."""
    anchors = [a for a in duty.get("anchors", []) if isinstance(a, str)]
    return any(a in quote or any(a[i : i + 2] in quote for i in range(len(a) - 1)) for a in anchors)


def _review_quote(item, number, duty, allowed_spans):
    """Trust the reviewer's verbatim quote; reject only what the quote itself shows is not an event.

    The 84 real run (e09db97a) rejected seven correct verdicts on a verb list, a full-anchor match and
    a same-paragraph rule. Each verb list missed the next real wording, so only a payoff, the one kind
    a mere sighting can fake, still needs an action or result. Truth reveals often never name the anchor.
    """
    if not isinstance(item, dict) or item.get("chapter") != number or item.get("status") != "fulfilled":
        return ""
    quote = item.get("quote")
    if not isinstance(quote, str) or not quote or _NON_EVENT.search(quote):
        return ""
    if duty["kind"] != "truth" and not _names_anchor(duty, quote):
        return ""
    if duty["kind"] == "payoff" and not _ACTION_OR_RESULT.search(quote):
        return ""
    # Reject snippets that could omit a preceding negation or make a hypothetical event sound real.
    if quote in allowed_spans:
        return quote
    # A tail or middle of a sentence counts only if the whole sentence carries no negation or plan.
    return quote if any(quote in span and not _NON_EVENT.search(span) for span in allowed_spans) else ""


def _review_result(raw):
    try:
        return parse_json(raw)
    except ValueError:
        # Treat unreadable content as an unknown observation and save the review result.
        return {"items": [], "reason": "unreadable_review"}


def verify_foreshadows(client, prefix, ask, number, text, duties):
    """Check final prose locally first, with one batched review for unresolved mentions."""
    watched = [duty for duty in duties if duty.get("kind") in VERIFIED_KINDS]
    if not watched:
        return {}
    spans = _sentences(text)
    framed = [_framed(text, spans, index) for index in range(len(spans))]
    results, pending = {}, []
    for duty in watched:
        result, ambiguous = _local(duty, number, text, spans, framed)
        results[duty["id"]] = result
        if ambiguous:
            pending.append(duty)
    if not pending or ask is None:
        return results
    items = [
        dict(id=duty["id"], kind=duty["kind"], requirement=duty["text"], anchors=duty["anchors"]) for duty in pending
    ]
    try:
        response = precise_ask(
            ask,
            client,
            prefix + "-foreshadow-verification",
            "逐项核对义务是否在本章实际发生。埋设须在一场戏里出现，提醒须起具体作用；回收或揭示须写出要求的动作或真相。"
            "仅提及同一物品、否定、计划、假设、回述或尚未发生的事均不能记fulfilled。"
            "只有正文能证明整条义务时才记fulfilled，引用同一处一到两句连续的完整原句（保留标点，不改字）；"
            "无法确定就记unknown。回答中的章号必须等于输入章号。",
            [("义务", json.dumps(items, ensure_ascii=False)), ("本章正文", text), ("章号", str(number))],
            '{"items":[{"id":"义务ID","chapter":1,"status":"fulfilled或unknown","quote":"本章完整原句"}]}',
            parser=_review_result,
        )
    except ContentRejected:
        return results
    supplied = response.get("items") if isinstance(response, dict) else None
    if not isinstance(supplied, list):
        return results
    allowed = {text[start:end] for index, (start, end) in enumerate(spans) if not framed[index]}
    for index, ((start, end), (next_start, next_end)) in enumerate(zip(spans, spans[1:])):
        if not (framed[index] or framed[index + 1]):
            allowed.add(text[start:next_end])
    for duty in pending:
        ident = duty["id"]
        matching = [item for item in supplied if isinstance(item, dict) and item.get("id") == ident]
        if len(matching) == 1 and (quote := _review_quote(matching[0], number, duty, allowed)):
            results[ident] = _observation(number, quote, "done", "grounded_review")
    return results
