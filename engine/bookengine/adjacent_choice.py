"""Evidence for resolving a choice left open at the preceding chapter ending."""

REVIEW = (
    "这次先独立审上章章末两难的兑现，再审是否重演。章纲只用于选取检查，事实由两章正文决定。"
    "找出上章最后仍受困的人或物及两条互斥出路，沿这个人或物追到本章第一次处境改变的地方。"
    "是哪位当事人采取了哪一个动作才改变其处境，另一条路实际怎样了？只认正文给出的因果。"
    "余波、响动、换地点、受困者在下一场已经行动自如，或后来另有一场相似的选择，均不能代作此次解围。"
    "若当事人已经安然做别的事、找不到脱困动作，判skipped_choice，在finding列三处原文。"
    "若clear，另给choice_trace：shown=本章现场兑现或稍后揭晓，already_shown=上章已兑现，"
    "这两类的action_quote和effect_quote分别引同一事件中造成转变的动作及其实际后果。"
    "shown另引first_state_quote：从章首顺读，前章受困者首次已脱困的原句。若早于解围动作，"
    "须引出本章后来回到前一事件、解释当时经过的bridge_quote。后来的另一场脱困不算回叙。"
    "still_open=仍未解决、视角暂离或明示留待揭晓，no_choice=前章正文没有该选择，"
    "这两类只需action_quote引支持理由的原文。不要仅根据有新冲突就放行。不确定则unknown。\n"
)


def clear_trace(trace, previous, current):
    if not isinstance(trace, dict):
        return False
    kind = trace.get("kind")
    if kind not in ("shown", "already_shown", "still_open", "no_choice"):
        return False
    source = previous[-1200:] if kind in ("already_shown", "no_choice") else current
    action, effect = trace.get("action_quote"), trace.get("effect_quote")
    if not isinstance(action, str) or len(action.strip()) < 8 or "\n" in action or action not in source:
        return False
    if kind in ("still_open", "no_choice"):
        return True
    if kind == "shown":
        first = trace.get("first_state_quote")
        if not isinstance(first, str) or len(first.strip()) < 8 or "\n" in first or first not in current:
            return False
        if current.index(first) < current.index(action):
            bridge = trace.get("bridge_quote")
            window = current[current.index(first) + len(first) : current.index(action) + len(action) + 240]
            if (
                not isinstance(bridge, str)
                or len(bridge.strip()) < 8
                or bridge in (action, effect)
                or bridge not in window
            ):
                return False
    return (
        isinstance(effect, str)
        and len(effect.strip()) >= 8
        and "\n" not in effect
        and effect in source
        and action not in effect
        and effect not in action
    )


REPAIR = (
    "只替换圈出的开场，把上一章搁置的选择接到本章已有的结果。写在场人物究竟怎样选，"
    "让读者看见他为此错过什么机会或改变了谁的处境。只用两章已经给出的行动条件，"
    "保留下文承认的处境、人物、资源、伤势和事件，也保留下文有意延迟的揭晓。"
    "先辨认缺的那一步。让原来受困的人从原位置经这一步到达下一段的位置，"
    "改掉原稿里会让他再次进场的句子；只保留真正影响后文的物件和处境。"
    "圈内对白若引出圈外的回答，保住问题或来由。检查改后每人只到场一次，接缝两边的问答能接上。"
    "不要重演上一章的犹豫或用旁白宣布付出代价。若证据容不下这样的动作则返回空text。"
    "新增分段最多两处，只交替换区正文。"
)
RECHECK = (
    "逐字对照上一章的未决选择和本章改写。读者能否看清当事人实际采取的行动，以及它造成的"
    "取舍或可见后果？分别摘出替换区两处独立原文作new_action_quote和consequence_quote。"
    "模糊回忆、泛称作出牺牲或虽脱险却不交代如何脱险，都用skipped_choice。"
    "通读保留的后文，若后文原已揭晓选择、补写将破坏悬念，或引入无据的新能力、伤亡、物件，"
    "preserves_facts为false。只核实替换区，不修改后文，无法核实返回unknown。"
)
