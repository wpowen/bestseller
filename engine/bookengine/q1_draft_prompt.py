"""Assemble the Q1 writer input used for production chapters and matched sample runs."""

from .adjacent_conflicts import previous_conflict_context
from .chapter_callbacks import writer_callback_section
from .choice_cost import choice_cost_context
from .compact_writer import compact_prompt
from .core_ability import chapter_ability_view
from .craft import reference_features
from .disclosure_choices import GUIDANCE as DISCLOSURE_GUIDANCE, HEADING as DISCLOSURE_HEADING
from .first_appearances import writer_first_appearance_instruction
from .identity import writer_identity_bible
from .life_status import writer_survival_instruction
from .nonhuman_speech import speech_context, writer_speech_bible
from .opening_choice import opening_choice
from .opening_styles import writer_opening_style_context
from .prose_similarity import with_completed_action_context
from .q1_extract import context, pack
from .relationship_revisit import relationship_revisit
from .rhythm_writer import writer_main_scene_contract, writer_rhythm_instruction
from .solution_sequences import writer_solution_sequence_context
from .speaker_context import GUIDANCE as SPEAKER_GUIDANCE, HEADING as SPEAKER_HEADING, voice_once
from .story_duties import writer_instruction, writer_sections
from .story_payoffs import writer_payoff_instruction


def word_band(target):
    return (1870, 2700) if target == 2200 else (round(target * 0.85), round(target * 1.15))


def production_prompt(book, bible, threads, vol, outline, state, history, rows, previous, drops, out, style, data):
    outline = chapter_ability_view(bible, outline)
    idx = outline["number"]
    target = book.words_per_chapter
    low, high = word_band(target)
    writer_bible = writer_identity_bible(writer_speech_bible(bible))
    contract = writer_main_scene_contract(outline)
    sections = context(
        writer_bible, threads, vol, outline, state, history, rows, previous, drops, scene_contract=contract
    )
    sections.insert(0, ("本章主场景硬合同，章型、两场行动与末钩必须逐项落实", pack(contract)))
    sections[1:1] = writer_sections(outline, bible)
    if outline.get("speaker_briefs"):
        sections.insert(1, (SPEAKER_HEADING, SPEAKER_GUIDANCE + "\n" + pack(voice_once(outline["speaker_briefs"]))))
    prior = rows[-1] if rows else {}
    saved_outcome = (
        prior.get("number") == idx - 1
        and prior.get("fact_source") == "final_body"
        and prior.get("status") == "complete"
        and not prior.get("state_failed")
    )
    if not saved_outcome:
        sections[1:1] = previous_conflict_context(out, idx)
    sections = with_completed_action_context(sections, out, idx, saved_outcome=saved_outcome, choice_is_reference=True)
    sections[1:1] = choice_cost_context(out, prior, previous, outline)
    sections.extend(writer_opening_style_context(out, idx))
    sections.extend(speech_context(bible, idx))
    sections.extend(writer_solution_sequence_context(out, idx, bible))
    if book.style_reference:
        sections.append(("授权参考的统计风格特征", pack(reference_features(book.style_reference))))
    revisit = relationship_revisit(outline, state, rows, out)
    if focus := outline.get("disclosure_context"):
        sections.insert(1, (DISCLOSURE_HEADING, pack(focus) + "\n" + DISCLOSURE_GUIDANCE))
    if revisit:
        sections.insert(0, ("上次交谈的原话", "\n".join(revisit["prior_exchange"])))
    callback = writer_callback_section(out, bible, vol, outline, getattr(book, "chapters", idx))
    pending = opening_choice(out, prior, previous, outline) if saved_outcome else None
    if getattr(book, "writer_input", "full") == "compact":
        instruction, compact = compact_prompt(book, outline, contract, sections, state, previous, pending, low, high)
        instruction += writer_first_appearance_instruction(out, idx, bible, outline)
        instruction += writer_survival_instruction(bible, idx)
        return instruction, [*compact, *callback]
    instruction = (
        writer_rhythm_instruction(outline, revisit=revisit)
        + "\n"
        + data[style]
        + f"\n目标{target}汉字，合格{low}—{high}，本章正文{2000 if target == 2200 else low}—{2400 if target == 2200 else high}汉字，只写两场戏，遵循已有章纲，长短随人物选择与后果分配，用空行转场，不加小标题、分隔线或emoji；最多新增1处伤病/代价。到本章情节完成立即收笔，直接输出正文。"
    )
    instruction += writer_payoff_instruction(outline) + writer_instruction(outline, bible)
    instruction += writer_first_appearance_instruction(out, idx, bible, outline)
    instruction += writer_survival_instruction(bible, idx)
    if pending:
        instruction = pending + "\n" + instruction
    return instruction, [*sections, *callback]
