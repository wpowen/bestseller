"""Carry an explicitly unresolved, two-way choice into a continuing first scene."""

from pathlib import Path
import re

from .q1_schema import rows
from .storage import load


HEADING = "首场先接住的未决选择"
_IF = r"(?:如果|倘若|要是|若是|若|只要|要么)"
_BRANCH = _IF + r"([^，,。！？；;\n]{1,32})[，,]([^；;。！？\n]{2,82})"
_PAIR = re.compile(_BRANCH + r"[；;。]\s*(?:但|可|而)?(?:他|她)?" + _BRANCH)
_PENDING = re.compile(
    r"迟迟没(?:有)?(?:再|能)?(?:迈|动|下手|出手|进)[^。！？]{0,10}$|"
    r"(?:还没(?:有)?|未曾|尚未|仍未|没有)(?:选定|作出决定|做出决定|决定)(?:走哪条路|选哪一边|下来)?$|"
    r"没(?:有)?说出[^。！？]{0,12}选哪一[边条]|两难[^。！？]{0,22}落到[^。！？]{1,10}脚下"
)
_SETTLED = re.compile(r"(?:于是|终于|已经|随即|当即|径直)(?:选|决定|动手|出手)|(?:他|她)选了|决定先|拿定了主意")
_DELAY = re.compile(
    r"转[到向].{0,9}视角|换.{0,5}视角|从.{1,8}的视角(?:开篇|切入)|暂不(?:揭晓|揭示|交代)|"
    r"(?:稍后|后文|第二场).{0,10}(?:揭晓|揭示|交代)|留待.{0,10}(?:揭晓|再叙|交代)"
)


def continues_choice_view(outline, scene, actor):
    if (outline.get("opening_pov") or scene.get("pov") or actor) != actor:
        return False
    cues = [
        outline.get("opening_conflict"),
        outline.get("transition"),
        *(scene.get(k) for k in ("goal", "where", "action", "choice")),
    ]
    return not _DELAY.search(" ".join(value for value in cues if isinstance(value, str)))


def opening_choice(out, prior, previous, outline):
    """Only promote a choice supported by the saved ending, its last beat and the first scene's cast."""
    number = outline["number"] - 1
    if out is None or number < 1 or not previous.strip() or not (scenes := rows(outline.get("scenes"))):
        return ""
    actor = prior.get("end_pov")
    first = scenes[0]
    cast = first.get("characters")
    if not isinstance(actor, str) or not actor or not isinstance(cast, list) or actor not in cast:
        return ""
    if not continues_choice_view(outline, first, actor):
        return ""
    try:
        plan = load(Path(out) / f"writing_outlines/{number:03d}.json")
        if not isinstance(plan, dict) or plan.get("number") != number or plan.get("ending_type") != "两难选择":
            return ""
        actual = (Path(out) / f"chapter-{number:03d}.body.md").read_text(encoding="utf-8").strip()
    except (OSError, ValueError, UnicodeError):
        return ""
    if actual != previous.strip():
        return ""
    tail = actual[-1200:]
    quote = prior.get("end_pov_quote")
    if not isinstance(quote, str) or actor not in quote or quote not in tail:
        return ""
    last = re.split(r"\n\s*\n", tail)[-1]
    final_sentence = re.split(r"[。！？]", last.rstrip("。！？"))[-1]
    if not _PENDING.search(final_sentence) or actor not in last:
        return ""
    options = list(_PAIR.finditer(tail))
    if not options:
        return ""
    pair = options[-1]
    if "迟迟" in final_sentence and not (
        re.search(r"之间|中间|岔路|岔口|两条路|两边", final_sentence)
        or any(term in final_sentence for term in (pair[1], pair[3]))
    ):
        return ""
    prefix = tail[: pair.start()].rsplit("\n", 1)[-1]
    if (
        prefix.count("“") > prefix.count("”")
        or prefix.count("「") > prefix.count("」")
        or re.search(r"想起|记得|回忆", prefix)
    ):
        return ""
    remaining = tail[pair.end() :]
    if len(remaining) > 380 or _SETTLED.search(remaining):
        return ""
    a, cost_a, b, cost_b = pair.groups()
    for stated in (a, b):
        if re.search(re.escape(stated) + r"(?:了|。|，)", remaining):
            return ""
    affected = [
        p for p in cast if isinstance(p, str) and p != actor and (p in a or p in b) and (p in cost_a or p in cost_b)
    ]
    if any(name not in cast and name in pair[0] for name in prior.get("present") or [] if isinstance(name, str)):
        return ""
    goal = first.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        return ""
    intent = next((x.get("goal") for x in rows(first.get("speaker_intents")) if x.get("name") == actor), None)
    line = f"{HEADING}（第{number}章收笔）。{actor}面前还有两条路。选「{a}」的后果是「{cost_a}」，选「{b}」的后果是「{cost_b}」。"
    if isinstance(intent, str) and intent.strip():
        line += f"本场{actor}自己的打算是「{intent.rstrip('。')}」。"
    scene_target = "、".join(affected) or "自己"
    return line + (
        f"在首场目标「{goal.rstrip('。')}」展开前，写出{actor}如何改变{scene_target}眼下的处境，"
        "交代另一条路因此失去了什么。把这个动作接进首场，不重述两难。后面另一次处境相似的行动不能代作这一次。"
    )
