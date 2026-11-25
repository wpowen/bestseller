"""Prepare matched opening samples from immutable production planning inputs."""

from bookengine.character_context import schedule_catchphrases
from bookengine.continuity_ledger import writer_continuity_facts
from bookengine.contracts import choose_contract
from bookengine.identity import writer_identity_outline
from bookengine.life_planning import apply_life_constraints
from bookengine.q1_cast import chapter_cast
from bookengine.q1_draft_prompt import production_prompt
from bookengine.q1_extract import pack
from bookengine.q1_plan import GROWTH
from bookengine.rhythm_contract_repair import apply_rhythm_contracts
from bookengine.speaker_context import speaker_briefs
from bookengine.story_duties import with_story_duties
from bookengine.story_payoffs import prepare_writer_payoffs
from bookengine.title_promises import writer_title_contract


def prepare_control(client, book, bible, threads, vol, saved, state, rows, previous, data):
    """Rebind the saved planned scenes to the observations in this run before writing."""
    out, number, canon = client.out, saved["number"], bible["canon"]
    outline = {k: v for k, v in saved.items() if k not in ("story_duties", "side_names", "speaker_briefs")}
    contract = choose_contract(
        canon,
        rows,
        number,
        state.get("pending_payoff") or outline.get("payoff_type"),
        state,
        canon["final_volume_start"],
    )
    method = outline.get("solution_type")
    outline.update({k: v for k, v in contract.items() if k != "warnings"})
    if method in canon["solution_menu"] and method not in contract["solution_avoid"]:
        outline["solution_type"] = method
    outline.update(
        payoff_criterion=GROWTH.get("payoff_criteria", {}).get(contract["payoff_type"]),
        injury_limit=1,
        ending=number == book.chapters,
        growth_target=vol.get("expected_dashboard", {}),
    )
    outline = writer_title_contract(
        schedule_catchphrases(
            writer_identity_outline(chapter_cast(outline, bible, state["seen_characters"]), bible), bible
        )
    )
    outline = with_story_duties(prepare_writer_payoffs(outline), bible, out)
    outline = apply_rhythm_contracts(client, bible, outline, state, chapter=number, kind="writer")
    outline = apply_life_constraints(client, bible, outline, state, chapter=number)
    outline["speaker_briefs"] = speaker_briefs(outline, bible, state.get("present"), out=out)
    instruction, sections = production_prompt(
        book, bible, threads, vol, outline, state, [], rows, previous, [], out, "b", data
    )
    return outline, instruction, sections


def prepare_candidate(book, bible, saved, state, rows, previous, out=None):
    """Project only this chapter's dramatic material. No stage targets or historic book observations."""
    # The control is frozen from an earlier revision that does not have this candidate interface.
    from bookengine.core_ability import chapter_ability_view
    from bookengine.relationship_revisit import GUIDANCE, relationship_revisit

    saved = chapter_ability_view(bible, saved)
    number = saved["number"]
    scenes = saved["scenes"]
    people = {name for scene in scenes for name in scene.get("characters", [])}
    people.update(c["name"] for c in bible["characters"] if c.get("role") == "protagonist")
    cast = [
        {k: c[k] for k in ("name", "identity", "gender", "voice") if c.get(k)}
        for c in bible["characters"]
        if c.get("name") in people
    ]
    card = bible["selected_card"]
    premise = {"书名": card["title"], "处境": card["premise"], "能力规则": card["golden_finger_rule"]}
    if number == 1:
        premise["起点"] = card["opening"]
    else:
        # After the opening, the premise supplies canon only. Its opening situation is already past.
        premise.pop("处境")
    action = saved.get("core_application") if saved.get("core_required") else None
    action = action or scenes[-1]["action"]
    consequence = scenes[-1]["result"]
    # Some projected applications include their result after the operation. Show that outcome once.
    if consequence and action.endswith(consequence):
        action = action[: -len(consequence)].rstrip("。；， ")
    local = {
        "眼下的冲突": saved["opening_conflict"],
        "人物想得到的东西": scenes[0]["goal"],
        "眼前要付的代价或要越过的阻碍": scenes[-1]["obstacle"],
        "当事人可作的选择": scenes[-1]["choice"],
        "本章可用的动作": action,
        "走出这一章时可能发生的改变": consequence,
    }
    opening_action = scenes[0].get("action", "").strip("。； ")
    if number == 1 and opening_action and opening_action not in action:
        local["开篇处境中的动作"] = opening_action
    revisit = relationship_revisit(saved, state, rows, out)
    if revisit:
        scene = revisit["scene"]
        local = {
            "这次两人要决定的事": scene["goal"],
            "谁愿意做什么，条件是什么": scene["choice"],
            "这次要承担的代价": scene["obstacle"],
            "可用的动作": scene.get("action") or scene["choice"],
            "选择会怎样改变眼下的处境": scene["result"],
        }
    # Earlier detailed plans can contain an unassigned future victory in required_event.
    # Carry only an explicit chapter promise that was also bound into this saved plan.
    event = saved.get("required_event")
    if number <= 3 and isinstance(event, str) and event.strip():
        from bookengine.rhythm_skeleton import assigned_opening_events

        planned = {line.strip().rstrip("。；;") for line in event.splitlines()}
        due = [
            item
            for item in assigned_opening_events(bible.get("opening_promises"), number)
            if item.rstrip("。；;") in planned
        ]
        if due:
            local = {"本章排定要发生的事": due, **local}
    outline = dict(number=number, title=saved["title"], ending=False, local_brief=local)
    instruction = (
        f"写长篇小说《{card['title']}》第{number}章正文。全书{book.chapters}章，本章约{book.words_per_chapter}个汉字。"
        "从人物眼下真正想办的事写起。谁肯帮忙，谁会变卦，让读者从他们怎么做、怎么说看出来。"
        "从上章留下的处境往前走。已发生的事以所附原稿和事实为准，"
        "本章素材里与它冲突或已经发生的部分，由你根据人物的选择接下去。只交小说正文。"
    )
    sections = [("本章素材", pack(local)), ("故事设定", pack(premise)), ("人物", pack(cast))]
    if revisit:
        sections.insert(
            1,
            (
                "这两个人上次谈到了哪里",
                pack(
                    {
                        "章节": revisit["chapter"],
                        "已经发生": revisit["shared_event"],
                        "上次实际说过的话，仅供认清彼此知道什么": revisit["prior_exchange"],
                    }
                ),
            ),
        )
        instruction += GUIDANCE
    if previous:
        sections.extend(
            [
                ("前文已发生", pack([dict(chapter=r["number"], summary=r["summary"]) for r in rows])),
                ("上章末尾原文", previous[-650:]),
            ]
        )
        facts = writer_continuity_facts(state, bible, dict(outline, scenes=[{"characters": sorted(people)}]))
        if facts:
            sections.append(("此前仍有效的事实", "\n".join(facts)))
    return outline, instruction, sections
