"""Recall an earlier conversation when the same people must make a further choice."""

from difflib import SequenceMatcher
from pathlib import Path
import re

from .chapter_rhythm import relationship_contract
from .cross_chapter_facts import critical_events
from .stage_transition import _ELAPSED, _SCENE
from .text import _paragraph_spans


GUIDANCE = (
    "把上次交谈当作两个人的共同记忆。一方当时没答或不肯答的事，仍然悬着。"
    "从今天必须作的选择写起；新的证据、代价或改口让旧问题有了新意义时，可以再问。"
)
_SPEECH = re.compile(r"“([^”]+)”|「([^」]+)」")


def _local_turns(paragraphs, anchor, others):
    turns, narration = [], 0
    for part in paragraphs[anchor : anchor + 7]:
        prose = _SPEECH.sub("", part)
        if any(name in prose for name in others) or _ELAPSED.match(prose) or _SCENE.match(prose):
            break
        if part.startswith(("#", "***", "——")):
            break
        if _SPEECH.search(part):
            turns.append(part)
            narration = 0
        else:
            narration += 1
            if narration >= 2 or len(re.findall(r"[。！？!?]", part)) >= 3:
                break
    return turns


def _terms(left, right, people):
    for name in people:
        left, right = left.replace(name, " "), right.replace(name, " ")
    return [
        len(term)
        for block in SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks()
        for term in re.findall(r"[\w]{2,}", left[block.a : block.a + block.size])
    ]


def _exchange(body, people, summary, others):
    """Find a topical spoken turn with both people in context; keep a follow-up question and its actual reply."""
    paragraphs = [body[start:end] for start, end in _paragraph_spans(body)]
    scored = []
    for i, paragraph in enumerate(paragraphs):
        utterances = _SPEECH.findall(paragraph)
        if not utterances or not any(name in _SPEECH.sub("", paragraph) for name in people):
            continue
        nearby = paragraphs[max(0, i - 2) : i + 3]
        if not all(any(name in _SPEECH.sub("", part) for part in nearby) for name in people):
            continue
        speech = " ".join(a or b for a, b in utterances)
        if any(name in _SPEECH.sub("", paragraph) for name in others):
            continue
        matches = _terms(summary, speech, people)
        if matches:
            scored.append((max(matches), sum(matches), -i))
    if not scored:
        return []
    anchor = -max(scored)[2]
    excerpt = [paragraphs[anchor]]
    turns = _local_turns(paragraphs, anchor, others)
    for question, reply in zip(turns, turns[1:]):
        if re.search(r"[？?][”」]\s*$", question):
            return list(dict.fromkeys([*excerpt, question, reply]))
    return excerpt


def relationship_revisit(outline, state, rows, out):
    """Return a current scene plus observed prior context, or None for a first or unverified encounter."""
    contract = relationship_contract(outline)
    if not contract or out is None or type(outline.get("number")) is not int or not isinstance(rows, (list, tuple)):
        return None
    people = contract["people"]
    scenes = [
        scene
        for scene in (outline.get("scenes") or [])
        if isinstance(scene, dict)
        and isinstance(scene.get("characters"), list)
        and all(name in scene["characters"] for name in people)
        and all(
            isinstance(scene.get(key), str) and scene[key].strip() for key in ("goal", "obstacle", "choice", "result")
        )
    ]
    if not scenes:
        return None
    known = state.get("characters") if isinstance(state, dict) else {}
    others = (
        {name for name in known if isinstance(name, str) and name and name not in people}
        if isinstance(known, dict)
        else set()
    )
    completed = {
        row["number"]: row
        for row in rows[-12:]
        if isinstance(row, dict)
        and type(row.get("number")) is int
        and isinstance(row.get("summary"), str)
        and row.get("status") == "complete"
        and row.get("fact_source") == "final_body"
        and row.get("number", 0) < outline["number"]
    }
    ledger = state.get("continuity_ledger") if isinstance(state, dict) else None
    if not isinstance(ledger, dict) or not isinstance(ledger.get("critical_events"), list):
        return None
    body_by_chapter = {}
    current_topic = contract["before"] + " " + str(outline.get("opening_conflict") or "")
    for event in reversed(critical_events(state)):
        if type(event["chapter"]) is not int or not isinstance(event["quote"], str):
            continue
        row = completed.get(event["chapter"])
        summary = event.get("summary")
        if not row or not isinstance(summary, str) or not all(name in summary for name in people):
            continue
        relevant = _terms(current_topic, row["summary"] + " " + summary, people)
        if not relevant or max(relevant) < 3 and len(relevant) < 2:
            continue
        n = row["number"]
        if n not in body_by_chapter:
            try:
                body_by_chapter[n] = (Path(out) / f"chapter-{n:03d}.body.md").read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                body_by_chapter[n] = ""
        body = body_by_chapter[n]
        if not event["quote"].strip() or event["quote"] not in body:
            continue
        exchange = _exchange(body, people, row["summary"], others)
        if exchange:
            return dict(
                people=people,
                chapter=row["number"],
                shared_event=event["summary"],
                prior_exchange=exchange,
                scene=scenes[-1],
            )
    return None
