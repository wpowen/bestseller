"""Read-only boundary diagnostics; lexical fallback is explicitly Inference."""

import re


def _boundary(chapter, side):
    text = chapter["text"]
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    passage = "\n\n".join(paragraphs[:2]) if side == "opening" else text[-1200:]
    meta = chapter["meta"]
    for data in (meta, meta.get("state", {}), meta.get("boundary", {})):
        if not isinstance(data, dict):
            continue
        location, quote = data.get(side + "_location"), data.get(side + "_quote")
        if (
            isinstance(location, str)
            and location
            and isinstance(quote, str)
            and quote
            and location in quote
            and quote in passage
        ):
            return location, quote
    return None, None


def _breaks(chapters):
    issues, unknown = [], []
    for old, new in zip(chapters, chapters[1:]):
        pair = [old["number"], new["number"]]
        if pair[1] != pair[0] + 1:
            unknown.append(dict(chapters=pair, reason="missing_chapters"))
            continue
        previous, end_quote = _boundary(old, "end")
        opening, opening_quote = _boundary(new, "opening")
        if previous and opening:
            head = "\n\n".join(re.split(r"\n\s*\n", new["text"])[:2])
            movement = (
                previous in head
                and opening in head
                and re.search(r"离开|走出|走进|抵达|赶到|来到|穿过|沿.{0,12}(?:走|行)|从.{0,25}(?:到|往|去)", head)
            )
            if previous != opening and not movement:
                issues.append(
                    dict(
                        chapters=pair,
                        previous=previous,
                        opening=opening,
                        end_quote=end_quote,
                        opening_quote=opening_quote,
                        status="Inference",
                        basis="grounded_locations",
                    )
                )
            continue
        tail, head = old["text"][-1600:], new["text"][:350]
        # Missing observations are not a pass. Repeated explicit place nouns provide
        # candidates only; never store them as observed locations in state.
        old_places = place_candidates(old["text"])
        new_places = place_candidates(head)
        if old_places and new_places and not old_places.intersection(new_places):
            from .stage_transition import _transition_quotes

            if not _transition_quotes("\n\n".join(new["text"].split("\n\n")[:2])):
                issues.append(
                    dict(
                        chapters=pair,
                        previous=sorted(old_places),
                        opening=sorted(new_places),
                        end_quote=tail,
                        opening_quote=head,
                        status="Inference",
                        basis="lexical_place_candidates",
                    )
                )
                continue
        outside = re.search(r"天光|阳光|天空|露天|天幕|户外", tail)
        escape = re.search(r"冲出|走出|逃出|跑出|鱼贯|跨过门槛", tail)
        inside = re.search(r"岩壁|矿道|矿轨|运矿|矿车|洞内|地下|隧道|洞顶", head)
        movement = re.search(r"返回|折返|退回|回到|重新进入", head)
        if outside and escape and inside and not movement:
            issues.append(
                dict(chapters=pair, end_quote=tail, opening_quote=head, status="Inference", basis="lexical_boundary")
            )
        else:
            unknown.append(dict(chapters=pair, reason="missing_grounded_boundaries"))
    return dict(count=len(issues), issues=issues, unknown=unknown, status="reference_only")


def place_candidates(text):
    narrative = re.sub(r"“[^”]*”|‘[^’]*’|「[^」]*」", "", text)
    found = re.findall(r"[\u4e00-\u9fff]{2,8}(?:巷|剑阁|剑廊|器室|剑室|客栈|驿站|码头|渡口|矿道|洞窟|城楼)", narrative)
    # Token suffixes are surface evidence, never semantic location truth.
    return {re.split(r"(?:在|从|到|入|出|的|后|了|是|进|过|还|和|与)", word)[-1] for word in found}
