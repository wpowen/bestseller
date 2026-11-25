"""Recall a witnessed disclosure when its listener next has a relationship decision to make."""

from pathlib import Path
import re

from .q1_schema import rows as records
from .relationship_revisit import _terms
from .text import _paragraph_spans


HEADING = "当事人先前听到的话与这次选择"
GUIDANCE = "让听者按自己的利害处置当前关系。战时可以先应战，到能作主时再让旧话影响一次决定；勿重述上次对白。"
_SPOKEN = re.compile(r"[“「]([^“”「」\n]{3,230})[”」]")
_RECOLLECTION = re.compile(r"想起|回想|回忆|仿佛听见|心道|心想|默念|自言自语|传音入密")


def _subject(text, name):
    return re.search(r"(?:^|[，,。！？；：:”」])\s*" + re.escape(name), text)


def _named_turn(paragraph, cast):
    speech = _SPOKEN.search(paragraph)
    if speech is None or len(paragraph) > 300:
        return None
    before = paragraph[: speech.start()]
    after = paragraph[speech.end() :]
    attribution = before if before.strip() else after
    names = [name for name in cast if name in attribution]
    if len(names) != 1 or not _subject(attribution, names[0]) or _RECOLLECTION.search(paragraph):
        return None
    return names[0], speech[1]


def _heard_exchange(body, cast, people, truth):
    paragraphs = [body[a:b] for a, b in _paragraph_spans(body)]
    turns = [_named_turn(paragraph, cast) for paragraph in paragraphs]
    best, score = None, 0
    for i, (speaker, listener) in enumerate(zip(turns, turns[1:])):
        if not speaker or not listener or {speaker[0], listener[0]} != set(people):
            continue
        matches = _terms(truth, speaker[1], people)
        if not matches or (max(matches) < 4 and len(matches) < 2):
            continue
        if sum(matches) > score:
            best = dict(speaker=speaker[0], listener=listener[0], said=speaker[1], reply=listener[1])
            if (
                listener[1].endswith(("？", "?"))
                and i + 2 < len(turns)
                and turns[i + 2]
                and turns[i + 2][0] == speaker[0]
            ):
                best["follow_up"] = paragraphs[i + 2]
            score = sum(matches)
    return best


def _grounded_decision(text, focus, cause, action):
    matches = _terms(focus["said"] + "\n" + focus.get("follow_up", ""), cause, (focus["speaker"], focus["listener"]))
    if not matches or (max(matches) < 3 and len(matches) < 2):
        return False
    spans = _paragraph_spans(text)
    for number, (start, end) in enumerate(spans):
        paragraph = text[start:end]
        if action not in paragraph:
            continue
        actors = [focus["listener"], focus.get("listener_pronoun")]
        preceding_clause = re.split(r"[，,。！？；：:”」]", _SPOKEN.sub("", paragraph[: paragraph.index(action)]))[-1]
        attribution = preceding_clause + _SPOKEN.sub("", action)
        if not any(name and _subject(attribution, name) for name in actors):
            continue
        nearby = text[spans[max(0, number - 2)][0] : spans[min(len(spans) - 1, number + 2)][1]]
        if cause in nearby:
            return True
    return False


def disclosure_context(outline, bible, rows, out):
    """Use a settled truth node to find adjacent, attributable turns in the actual earlier body."""
    n = outline.get("number")
    if out is None or type(n) is not int:
        return None
    duties = [
        d
        for d in records(outline.get("story_duties"))
        if d.get("kind") == "relationship" and isinstance(d.get("names"), list) and len(d["names"]) == 2
    ]
    if not duties:
        return None
    cast = {
        c["name"]: {"男": "他", "女": "她"}.get(c.get("gender"))
        for c in records(bible.get("antagonists")) + records(bible.get("characters"))
        if isinstance(c.get("name"), str) and c["name"]
    }
    truths = {
        f"truth-{layer['layer']}": layer["truth"]
        for layer in records((bible.get("world") or {}).get("truth_layers"))
        if layer.get("layer") and isinstance(layer.get("truth"), str)
    }
    completed = [
        r
        for r in records(rows)
        if type(r.get("number")) is int
        and r["number"] < n
        and r.get("status") == "complete"
        and r.get("fact_source") == "final_body"
        and not r.get("state_failed")
    ]
    for row in sorted(completed, key=lambda r: -r["number"]):
        for record in records(row.get("story_duties")):
            truth = truths.get(record.get("id"))
            if not truth or record.get("kind") != "truth" or record.get("status") != "done":
                continue
            try:
                body = (Path(out) / f"chapter-{row['number']:03d}.body.md").read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            for duty in duties:
                heard = _heard_exchange(body, cast, duty["names"], truth)
                if not heard:
                    continue
                evidence_id = f"{record['id']}:{row['number']}:{heard['listener']}"
                if any(
                    fact.get("id") == evidence_id and fact.get("status") == "evidenced"
                    for r in completed
                    if isinstance((fact := r.get("disclosure_effect")), dict)
                ):
                    continue
                for scene in reversed(records(outline.get("scenes"))):
                    if not all(name in (scene.get("characters") or []) for name in duty["names"]):
                        continue
                    goal = next(
                        (
                            x.get("goal")
                            for x in records(scene.get("speaker_intents"))
                            if x.get("name") == heard["listener"] and x.get("goal")
                        ),
                        None,
                    )
                    if goal:
                        focus = dict(**heard, source=row["number"], id=evidence_id, duty=duty.get("id"), goal=goal)
                        pronoun = cast.get(heard["listener"])
                        others = [cast.get(p) for p in scene["characters"] if p != heard["listener"]]
                        if pronoun and all(p and p != pronoun for p in others):
                            focus["listener_pronoun"] = pronoun
                        return focus
    return None


def disclosure_extraction(outline):
    focus = outline.get("disclosure_context")
    if not focus:
        return "", {}
    prompt = (
        f"\n检查{focus['listener']}是否因第{focus['source']}章亲耳听过的“{focus['said']}”而自己作了决定。"
        + (f"当时接着有这句：{focus['follow_up']}。" if focus.get("follow_up") else "")
        + "disclosure_effect.status填acted、deferred或unknown。acted须分别逐字摘取本章中能看出所听之事影响"
        "判断的cause_quote及其亲自交付、隐瞒、拒绝等实际选择的action_quote，并说明联系。"
        "只有同场、分工或一句表态均不足；没有证据就留空。"
    )
    return prompt, {"disclosure_effect": {"status": "", "cause_quote": "", "action_quote": "", "reason": ""}}


def disclosure_effect(outline, raw, text):
    focus = outline["disclosure_context"]
    note = dict(
        id=focus["id"], duty=focus["duty"], source=focus["source"], listener=focus["listener"], status="unknown"
    )
    verdict = raw.get("disclosure_effect")
    if not isinstance(verdict, dict) or verdict.get("status") not in ("acted", "deferred"):
        return note
    cause, action = verdict.get("cause_quote"), verdict.get("action_quote")
    if not all(isinstance(q, str) and len(q.strip()) >= 6 and "\n" not in q and q in text for q in (cause, action)):
        return note
    reason = verdict.get("reason")
    if cause in action or action in cause or not isinstance(reason, str) or not reason.strip():
        return note
    if _grounded_decision(text, focus, cause, action):
        return dict(
            note,
            status="evidenced" if verdict["status"] == "acted" else "deferred",
            cause_quote=cause,
            action_quote=action,
            interpretation=reason.strip(),
            basis="model_interpretation_with_local_quotes",
        )
    return note
