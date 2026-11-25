"""Ground a supporting character's scheduled choice and its later consequences."""

import re

KINDS = ("support_choice", "support_consequence")
OUTCOMES = {"achieved", "lost", "deferred_with_cost"}
OBSERVATIONS = OUTCOMES | {"stated", "promised", "progressed", "chose"}
SPEECH = re.compile(r"“[^”\n]*(?:”|$)|「[^」\n]*(?:」|$)|『[^』\n]*(?:』|$)")

REVIEW = (
    "配角私愿项另填goal_state：当事人说明愿望或欠缺stated、口头承诺promised、已经有进展progressed、当事人已作取舍chose、"
    "愿望达成achieved、落空lost、已延期且代价实际发生deferred_with_cost，否则unknown。"
    "据具体愿望判断：收到承诺不等于到手，拒绝招揽只证明选择；不把猜想或尚待兑现的条件算结果。"
    "choice核实本人做了什么及当下失去或改变了什么；consequence须看到愿望所指的人或事的实际变化。"
    "先引当事人动作，再引独立的直接后果原句。只有进展也照实列证，留待审读。"
)


def _narrated(quote, text, target):
    """Allow a subject named in the previous paragraph to act by pronoun in the next."""
    previous = ""
    for line in filter(str.strip, text.splitlines()):
        narration = SPEECH.sub(" ", line)
        if quote in narration and (target in narration or (target in previous and re.search("[他她它]", quote))):
            return True
        previous = narration
    return False


def classify(item, state, evidence, text):
    quotes = [proof["quote"] for proof in evidence]
    if not quotes or item["owner"] not in text or not isinstance(state, str) or state not in OBSERVATIONS:
        return "unknown", "unknown"
    if state in ("stated", "promised", "progressed"):
        return "unverified", state
    if (
        len(quotes) != 2
        or min(map(len, quotes)) < 5
        or any("\n" in quote for quote in quotes)
        or quotes[0] in quotes[1]
        or quotes[1] in quotes[0]
        or (state in OUTCOMES and not item.get("target"))
        or not _narrated(quotes[1], text, item["target"] if state in OUTCOMES else "")
    ):
        return "unknown", "unknown"
    return ("fulfilled" if state in OUTCOMES or item["kind"] == "support_choice" else "unverified"), state


def support_entry(duty, number, text, review):
    adopted = review.get("after" if review.get("repair") == "adopted" else "before") or {}
    result = next((r for r in adopted.get("items", []) if r.get("id") == duty["id"]), {})
    evidence = result.get("evidence") or []
    if not all(p["chapter"] == number and p["quote"] in text for p in evidence):
        evidence = []
    status, outcome = classify(
        dict(kind=duty["kind"], owner=duty["names"][0], target=duty.get("target", "")),
        result.get("goal_state"),
        evidence,
        text,
    )
    return dict(
        status="done" if status == "fulfilled" else "unverified",
        chapter=number,
        goal_state=outcome,
        evidence=evidence,
        quote=evidence[-1]["quote"] if evidence else "",
    )
