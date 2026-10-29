"""Safe assertions and a proposed JSON wire contract for the two public APIs."""

from copy import deepcopy
import hashlib
import json

from bestseller.services import reader_judge

ANCHOR_KEY = "reader_revision_anchors"
ANCHOR_SCHEMA = "reader-revision-anchors.v1"
VERIFY_SCHEMA = "reader-revision-preservation.v1"

OPENING = "林秋把卖房合同折好，压在门边的旧收音机下。"
CHOICE = "她把定金退给买家，决定留下来和妹妹修屋。"
DIALOGUE = "妹妹问：‘你这次还走吗？’她说：‘等屋顶不漏了，再一起商量。’"
ENDING = "妹妹把另一把钥匙放进她掌心，两人第一次并肩关上了门。"
SOURCE = OPENING + "\n" + "\n".join(
    f"第{i}只纸箱搬到窗边时，旧木板上露出浅浅的压痕。林秋停下来，听妹妹说起从前。"
    for i in range(220)
) + "\n" + CHOICE + "\n" + DIALOGUE + "\n" + ENDING


def sha(value):
    raw = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def summary(value):
    raw = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return {"type": type(value).__name__, "len": len(raw), "sha256": sha(raw)}


class SafeDifference(AssertionError):
    def __init__(self, field, expected, actual):
        self.code = "reader_revision_test_difference"
        self.differences = [{"field": field, "expected": summary(expected), "actual": summary(actual)}]
        super().__init__(json.dumps({"code": self.code, "differences": self.differences}, sort_keys=True))


def equal(field, expected, actual):
    __tracebackhide__ = True
    if type(expected) is not type(actual) or expected != actual:
        raise SafeDifference(field, expected, actual)


def require(field, value):
    __tracebackhide__ = True
    equal(field, True, bool(value))


def apis():
    __tracebackhide__ = True
    for name in ("freeze_reader_revision_anchors", "verify_reader_revision_preservation"):
        equal("public_api." + name, True, callable(getattr(reader_judge, name, None)))
    return reader_judge


def anchor(body, quote, number=1, kind="choice"):
    start = body.index(quote)
    return {"anchor_id": f"a{number}", "start": start, "end": start + len(quote),
            "quote": quote, "kind": kind,
            "narrative_function": "人物用实际选择承担关系的后果，而非口头概括感情。"}


def anchor_answer(body=SOURCE, anchors=None):
    selected = anchors if anchors is not None else [
        anchor(body, CHOICE, 1), anchor(body, DIALOGUE, 2, "dialogue"),
        anchor(body, ENDING, 3, "relationship"),
    ]
    return {"schema": ANCHOR_SCHEMA, "source_body_sha256": sha(body), "anchors": deepcopy(selected)}


def verify_answer(anchors, candidate, *, lost=(), voice=True, no_intrusion=True):
    def evidence(item):
        quote = item["quote"] if item["quote"] in candidate else (
            "林秋退还定金，留下来和妹妹一起补屋顶。" if item["kind"] == "choice" else ENDING
        )
        start = candidate.index(quote)
        return {"start": start, "end": start + len(quote), "quote": quote}

    return {"schema": VERIFY_SCHEMA, "source_body_sha256": sha(SOURCE),
            "candidate_body_sha256": sha(candidate), "anchors": [
                {"anchor_id": item["anchor_id"], "preserved": item["anchor_id"] not in lost,
                 "evidence": [evidence(item)],
                 "reason": "人物的行动与关系后果仍能在完整正文中读到。"}
                for item in anchors],
            "voice_preserved": voice, "no_system_procedure_intrusion": no_intrusion}


def validate_safe_error(error):
    __tracebackhide__ = True
    require("error.code", isinstance(getattr(error, "code", None), str))
    differences = getattr(error, "differences", None)
    require("error.differences", isinstance(differences, list) and bool(differences))
    for item in differences:
        require("error.field", isinstance(item.get("field"), str) and bool(item["field"]))
        for side in ("expected", "actual"):
            value = item.get(side)
            require("error.safe_summary", isinstance(value, dict))
            require("error.safe_summary.type", isinstance(value.get("type"), str))
            require("error.safe_summary.len", type(value.get("len")) is int)
            require("error.safe_summary.sha256", isinstance(value.get("sha256"), str)
                    and len(value["sha256"]) == 64)
            equal("error.safe_summary.keys", {"type", "len", "sha256"}, set(value))
    safe = str(error) + json.dumps(differences, ensure_ascii=False)
    for forbidden in (SOURCE, CHOICE, DIALOGUE, ENDING, "offline-fixture-not-a-credential"):
        equal("error.source_or_secret_leak", False, forbidden in safe)


async def rejected(call):
    __tracebackhide__ = True
    try:
        await call()
    except SafeDifference:
        raise
    except Exception as error:
        validate_safe_error(error)
        return error
    raise SafeDifference("rejection.required", "typed safe rejection", "returned normally")
