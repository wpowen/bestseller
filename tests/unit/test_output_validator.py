"""Unit tests for the L4 output validator (language signature + length envelope).

These are the two Phase 1 hard checks — they have to reliably block CJK
leakage into English drafts and out-of-envelope chapter length without
false-positive on normal prose.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.services.invariants import (
    LengthEnvelope,
    NamingScheme,
    ProjectInvariants,
)
from bestseller.services.output_validator import (
    EntityDensityCheck,
    LanguageSignatureCheck,
    LengthEnvelopeCheck,
    NamingConsistencyCheck,
    OutputValidator,
    ValidationContext,
    build_phase1_validator,
)

pytestmark = pytest.mark.unit


def _en_invariants(
    min_chars: int = 2000,
    target_chars: int = 3000,
    max_chars: int = 4000,
) -> ProjectInvariants:
    return ProjectInvariants(
        project_id=uuid4(),
        language="en",
        length_envelope=LengthEnvelope(min_chars, target_chars, max_chars),
    )


def _zh_invariants(
    min_chars: int = 2000,
    target_chars: int = 3000,
    max_chars: int = 4000,
) -> ProjectInvariants:
    return ProjectInvariants(
        project_id=uuid4(),
        language="zh-CN",
        length_envelope=LengthEnvelope(min_chars, target_chars, max_chars),
    )


def _chapter_ctx(invariants: ProjectInvariants) -> ValidationContext:
    return ValidationContext(invariants=invariants, chapter_no=1, scope="chapter")


# ---------------------------------------------------------------------------
# LanguageSignatureCheck
# ---------------------------------------------------------------------------


class TestLanguageSignatureCheck:
    def test_clean_english_passes(self) -> None:
        check = LanguageSignatureCheck()
        text = (
            "The princess walked through the courtyard. "
            "Rain tapped on the flagstones as she considered her next move. "
        ) * 20
        violations = list(check.run(text, _chapter_ctx(_en_invariants())))
        assert violations == []

    def test_small_cjk_leak_still_passes_english_threshold(self) -> None:
        # A single CJK character in a 1000-char English passage should not trip
        # the 2% block threshold — we don't want to *block* on stray ideographs.
        # But we DO emit an INFO-severity residue finding so dashboards can
        # surface it without halting production.
        check = LanguageSignatureCheck()
        prose = "The wind shifted as she traced the sigil. " * 40
        text = prose + "你"
        violations = list(check.run(text, _chapter_ctx(_en_invariants())))
        assert len(violations) == 1
        v = violations[0]
        assert v.code == "LANG_RESIDUE_CJK_IN_EN"
        assert v.severity == "info"
        # Residue findings still surface the offending glyphs for follow-up.
        assert "你" in v.prompt_feedback

    def test_no_cjk_at_all_emits_no_residue_finding(self) -> None:
        # Guard against the INFO finding firing when there's literally zero CJK.
        check = LanguageSignatureCheck()
        text = "A perfectly clean English chapter without any ideographs." * 20
        violations = list(check.run(text, _chapter_ctx(_en_invariants())))
        assert violations == []

    def test_residue_severity_does_not_block_write(self) -> None:
        from bestseller.services.output_validator import OutputValidator

        check = LanguageSignatureCheck()
        prose = "The wind shifted as she traced the sigil. " * 40
        text = prose + "你"
        validator = OutputValidator([check])
        report = validator.validate(text, _chapter_ctx(_en_invariants()))
        assert report.has_issues
        # Critical invariant: INFO-severity residue must never block writing.
        assert not report.blocks_write

    def test_heavy_cjk_leak_in_english_is_blocked(self) -> None:
        check = LanguageSignatureCheck()
        # >2% CJK ratio: 120 CJK chars over ~500 total non-whitespace.
        text = "The city woke. " * 10 + "她走进了花园。她看见月光。" * 12
        violations = list(check.run(text, _chapter_ctx(_en_invariants())))
        assert len(violations) == 1
        assert violations[0].code == "LANG_LEAK_CJK_IN_EN"
        assert violations[0].severity == "block"
        # Feedback must be actionable Chinese-language remediation:
        assert "中文" in violations[0].prompt_feedback
        assert "英文" in violations[0].prompt_feedback

    def test_heavy_latin_in_chinese_is_blocked(self) -> None:
        check = LanguageSignatureCheck()
        # Long Latin runs pushing > 10% of total non-whitespace chars.
        text = "她说：" + "MERIDIAN CELESTIAL ORDINANCE DEFENDER " * 8 + "然后就走了。"
        violations = list(check.run(text, _chapter_ctx(_zh_invariants())))
        assert len(violations) == 1
        assert violations[0].code == "LANG_LEAK_LATIN_IN_ZH"
        assert violations[0].severity == "block"

    def test_empty_text_returns_no_violations(self) -> None:
        check = LanguageSignatureCheck()
        assert list(check.run("", _chapter_ctx(_en_invariants()))) == []
        assert list(check.run("   \n\t   ", _chapter_ctx(_en_invariants()))) == []


# ---------------------------------------------------------------------------
# LengthEnvelopeCheck
# ---------------------------------------------------------------------------


class TestLengthEnvelopeCheck:
    def test_in_envelope_passes(self) -> None:
        check = LengthEnvelopeCheck()
        text = "a" * 3000
        violations = list(check.run(text, _chapter_ctx(_en_invariants())))
        assert violations == []

    def test_too_short_is_blocked(self) -> None:
        check = LengthEnvelopeCheck()
        text = "a" * 500
        violations = list(check.run(text, _chapter_ctx(_en_invariants())))
        assert len(violations) == 1
        assert violations[0].code == "LENGTH_UNDER"
        assert violations[0].severity == "block"
        assert "500" in violations[0].detail

    def test_too_long_is_blocked(self) -> None:
        check = LengthEnvelopeCheck()
        text = "a" * 10000
        violations = list(check.run(text, _chapter_ctx(_en_invariants())))
        assert len(violations) == 1
        assert violations[0].code == "LENGTH_OVER"
        assert violations[0].severity == "block"

    def test_scene_scope_is_exempt(self) -> None:
        # Scene drafts shouldn't be judged by the chapter envelope.
        check = LengthEnvelopeCheck()
        ctx = ValidationContext(
            invariants=_en_invariants(), chapter_no=1, scope="scene"
        )
        violations = list(check.run("a" * 100, ctx))
        assert violations == []

    def test_whitespace_ignored_in_length_count(self) -> None:
        # "in envelope" should be based on non-whitespace; padding with newlines
        # must not inflate the count to pass.
        check = LengthEnvelopeCheck()
        text = "\n".join(["a"] * 500)  # 500 chars + ~499 newlines
        violations = list(check.run(text, _chapter_ctx(_en_invariants())))
        assert len(violations) == 1
        assert violations[0].code == "LENGTH_UNDER"


# ---------------------------------------------------------------------------
# OutputValidator orchestrator + Phase 1 factory
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# NamingConsistencyCheck
# ---------------------------------------------------------------------------


def _zh_invariants_with_pool(pool: tuple[str, ...]) -> ProjectInvariants:
    return ProjectInvariants(
        project_id=uuid4(),
        language="zh-CN",
        length_envelope=LengthEnvelope(2000, 3000, 4000),
        naming_scheme=NamingScheme(style="cjk_2char", seed_pool=pool),
    )


def _en_invariants_with_pool(pool: tuple[str, ...]) -> ProjectInvariants:
    return ProjectInvariants(
        project_id=uuid4(),
        language="en",
        length_envelope=LengthEnvelope(2000, 3000, 4000),
        naming_scheme=NamingScheme(style="saxon", seed_pool=pool),
    )


def _ctx_with_allowed(
    invariants: ProjectInvariants, allowed: frozenset[str] = frozenset()
) -> ValidationContext:
    return ValidationContext(
        invariants=invariants,
        chapter_no=1,
        scope="chapter",
        allowed_names=allowed,
    )


class TestNamingConsistencyCheck:
    def test_no_allowlist_is_noop(self) -> None:
        # With no seed pool and no ctx.allowed_names, the check can't decide
        # what's rogue — it must stay silent.
        check = NamingConsistencyCheck()
        text = "林奚看着赵无极，心中五味杂陈。林奚又对赵无极说道。" * 3
        violations = check.run(text, _chapter_ctx(_zh_invariants()))
        assert violations == []

    def test_zh_rogue_name_flagged_when_repeated(self) -> None:
        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("林奚",))
        # 赵无极 appears 3 times — rogue (not in pool), above frequency floor.
        text = (
            "林奚抬眼望去。赵无极站在远处。"
            "林奚叹了口气。赵无极没有说话。"
            "她终于开口：赵无极，你为什么回来？"
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert len(violations) == 1
        assert violations[0].code == "NAMING_OUT_OF_POOL"
        assert "赵无极" in violations[0].detail

    def test_zh_single_occurrence_below_floor(self) -> None:
        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("林奚",))
        # 赵无极 appears once — below frequency_floor=2.
        text = "林奚抬眼望去。赵无极站在远处。林奚叹了口气。"
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == []

    def test_zh_allowed_as_prefix_of_candidate_passes(self) -> None:
        # Regex may grab "林奚说" as a 3-char candidate; since "林奚" is allowed
        # and is a prefix of "林奚说", this must not flag.
        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("林奚",))
        text = "林奚说话时声音低沉。林奚看见月亮。林奚转身离去。"
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == []

    def test_zh_compound_surname_in_pool_passes(self) -> None:
        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("慕容雪",))
        text = (
            "慕容雪看着镜中的自己。慕容雪没有说话。"
            "慕容雪的手指微微颤抖。"
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == []

    def test_zh_ctx_allowed_names_merged_with_pool(self) -> None:
        # Bible adds a character whose name isn't in the seed pool.
        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("林奚",))
        allowed = frozenset({"赵无极"})  # bible roster adds this name
        text = (
            "林奚抬眼望去。赵无极站在远处。"
            "林奚叹了口气。赵无极没有说话。"
        )
        violations = check.run(text, _ctx_with_allowed(inv, allowed))
        assert violations == []

    def test_en_rogue_honorific_flagged(self) -> None:
        check = NamingConsistencyCheck()
        inv = _en_invariants_with_pool(("Jane Smith",))
        text = (
            "Mrs. Parker opened the door. "
            "Mrs. Parker smiled softly. "
            "Jane Smith said nothing. "
            "Mrs. Parker turned away."
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert len(violations) == 1
        assert "Parker" in violations[0].detail

    def test_en_allowed_two_word_name_passes(self) -> None:
        check = NamingConsistencyCheck()
        inv = _en_invariants_with_pool(("Jane Smith",))
        text = (
            "Jane Smith walked in. "
            "Jane Smith turned around. "
            "Jane Smith was home."
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == []

    def test_en_honorific_when_name_allowed_passes(self) -> None:
        # Allowed="Parker" — the honorific regex captures just "Parker", and
        # that exactly matches the allowlist entry.
        check = NamingConsistencyCheck()
        inv = _en_invariants_with_pool(("Parker",))
        text = (
            "Mrs. Parker opened the door. "
            "Mrs. Parker smiled. "
            "Mrs. Parker vanished into the dusk."
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == []

    def test_en_non_name_cap_runs_ignored(self) -> None:
        # "New York" and "Grand Canyon" have non-name words ("New" is not in
        # our non-name set, but "The Grand Canyon" would be). We should at
        # least not flag the weekday + month pattern.
        check = NamingConsistencyCheck()
        inv = _en_invariants_with_pool(("Jane Smith",))
        text = (
            "On Monday March 3rd the sky was clear. "
            "Tuesday April 4th brought rain. "
            "By Friday June 5th everything had changed. "
            "Jane Smith walked in."
        ) * 2
        violations = check.run(text, _ctx_with_allowed(inv))
        # Weekday/month combos filtered by _EN_CAPITAL_NON_NAMES.
        assert violations == []

    def test_empty_text_returns_no_violations(self) -> None:
        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("林奚",))
        assert check.run("", _ctx_with_allowed(inv)) == []

    def test_feedback_lists_rogue_names(self) -> None:
        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("林奚",))
        text = (
            "赵无极与孙小明对视一眼。"
            "赵无极冷笑一声。孙小明后退一步。"
            "林奚静静看着这一切。孙小明还想再说。赵无极挥手制止。"
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert len(violations) == 1
        fb = violations[0].prompt_feedback
        assert "命名池" in fb
        # One of the rogues should be surfaced verbatim.
        assert "赵无极" in fb or "孙小明" in fb

    def test_zh_common_noun_bigrams_are_not_flagged(self) -> None:
        """Surname-initial compound nouns like 时候/方向/成熟/金色 must
        not trip the name check. These are the top false-positive class
        we saw in the 2026-04-22 audit sweep.
        """

        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("林奚",))
        # 时候 时间 方向 方法 成熟 金色 周围 任何 平静 — all common nouns,
        # each surname-initial, each appearing multiple times.
        text = (
            "林奚走进庭院。时候不早了。此时方向未定，金色的阳光洒下来。"
            "周围几株梅树。任何情况下，她都要保持平静。时间一分一秒过去，"
            "方向渐渐明朗，金色的光晕笼罩着四周。时候到了，林奚深吸一口气。"
            "方法其实很简单。成熟的抉择要靠自己做出。平静地说了一句。"
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == [], (
            f"False-positive: {violations[0].detail if violations else '—'}"
        )

    def test_zh_grammatical_tail_is_trimmed_before_pool_lookup(self) -> None:
        """"韩九的" is regex-captured but should be trimmed to "韩九" so that
        a pool entry "韩九" correctly allows the candidate. Without the
        trim, the 3-char "韩九的" wouldn't match the 2-char pool entry and
        we'd false-flag a real name.
        """

        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("韩九",))
        text = (
            "林奚望向远处。韩九的剑还在鞘中。"
            "韩九的目光依旧沉静。韩九的呼吸慢下来。"
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == []

    def test_zh_role_suffix_is_stripped_before_pool_lookup(self) -> None:
        """Cultivation-novel role references like "苏师姐" / "钱管事" /
        "王真人" should be resolved against the surname prefix when the
        surname is in the pool. Without the role-suffix strip, every
        honorific call-out would be a rogue-name finding.
        """

        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("苏", "钱", "王"))
        text = (
            "苏师姐望向远处，钱管事低头记账。王真人轻轻颔首。"
            "苏师姐又说了一句话，钱管事重复了一遍。王真人转身离开。"
            "苏师妹从身后走来，钱管事抬头看她。王真人站定不动。"
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == [], (
            f"False-positive: {violations[0].detail if violations else '—'}"
        )

    def test_zh_role_suffix_with_compound_surname_resolves(self) -> None:
        """Compound-surname + role-suffix ("司马师兄") should resolve to
        the compound surname "司马" when it's in the pool.
        """

        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("司马",))
        text = (
            "司马师兄走上台阶。司马师兄又开了口。"
            "司马师兄眉头微蹙。司马师兄沉默片刻。"
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == [], (
            f"False-positive: {violations[0].detail if violations else '—'}"
        )

    def test_zh_leading_conjunction_is_stripped_for_pool_lookup(self) -> None:
        """"和林鸢" is captured as surname 和 + 林鸢, but 和 is usually a
        conjunction. Stripping it and retrying lets the real name "林鸢"
        resolve against the pool.
        """

        check = NamingConsistencyCheck()
        inv = _zh_invariants_with_pool(("林鸢", "苏瑶"))
        text = (
            "她和林鸢一同走出。和苏瑶相视而笑。"
            "她又和林鸢说了一句。和苏瑶一起离开。"
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == [], (
            f"False-positive: {violations[0].detail if violations else '—'}"
        )

    def test_en_sentence_starter_is_stripped_for_pool_lookup(self) -> None:
        """"And Rowan" / "But Celeste" — sentence-starter + real pool name
        should not be flagged.
        """

        check = NamingConsistencyCheck()
        inv = _en_invariants_with_pool(("Rowan", "Celeste"))
        text = (
            "She turned. And Rowan followed her down the hall. "
            "But Celeste stayed behind. And Rowan waited at the door. "
            "But Celeste had already left."
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == [], (
            f"False-positive: {violations[0].detail if violations else '—'}"
        )

    def test_en_possessive_is_stripped_for_pool_lookup(self) -> None:
        """"Ashford's" → "Ashford" should resolve when "Ashford" is in pool."""

        check = NamingConsistencyCheck()
        inv = _en_invariants_with_pool(("Ashford", "Pale Mother"))
        text = (
            "Ashford's coat hung by the door. Pale Mother's voice echoed. "
            "Ashford's hands trembled. Pale Mother's throne shone cold."
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == [], (
            f"False-positive: {violations[0].detail if violations else '—'}"
        )

    def test_en_sentence_starter_and_possessive_combined(self) -> None:
        """"And Kade Mercer's" resolves to "Kade Mercer" in pool."""

        check = NamingConsistencyCheck()
        inv = _en_invariants_with_pool(("Kade Mercer",))
        text = (
            "She looked up. And Kade Mercer's gaze met hers. "
            "And Kade Mercer's breath held. But Kade Mercer's smile faded."
        )
        violations = check.run(text, _ctx_with_allowed(inv))
        assert violations == [], (
            f"False-positive: {violations[0].detail if violations else '—'}"
        )


# ---------------------------------------------------------------------------
# EntityDensityCheck
# ---------------------------------------------------------------------------


class TestEntityDensityCheck:
    def test_chapter_not_one_is_exempt(self) -> None:
        check = EntityDensityCheck()
        # 12 distinct names would overload, but only chapter 1 is policed.
        text = "\n".join(
            [f"第{i}段：李{i}和王{i}看着陈{i}和张{i}。" for i in range(10)]
        )
        ctx = ValidationContext(
            invariants=_zh_invariants(),
            chapter_no=7,
            scope="chapter",
        )
        violations = check.run(text, ctx)
        assert violations == []

    def test_scene_scope_is_exempt(self) -> None:
        check = EntityDensityCheck()
        text = "李四, 张三, 王五, 赵六, 陈七, 孙八, 周九, 吴十, 郑一, 钱二, 冯零。"
        ctx = ValidationContext(
            invariants=_zh_invariants(), chapter_no=1, scope="scene"
        )
        violations = check.run(text, ctx)
        assert violations == []

    def test_zh_below_threshold_passes(self) -> None:
        check = EntityDensityCheck(max_entities=5)
        # 3 distinct names → well under 5.
        text = (
            "林奚走进庭院，回头看着赵无极。"
            "孙小明在远处喊了一声。林奚没有回应。"
        ) * 10
        ctx = ValidationContext(
            invariants=_zh_invariants(), chapter_no=1, scope="chapter"
        )
        violations = check.run(text, ctx)
        assert violations == []

    def test_zh_over_threshold_blocks(self) -> None:
        check = EntityDensityCheck(max_entities=5)
        # 10 distinct surname-based names in the head.
        text = (
            "林奚看见赵无极。孙小明跟着陈大海走来。"
            "王铁柱和周子楠站在一旁。吴天成和郑小雨低声交谈。"
            "李白露一袭白衣。张云起对她点头致意。"
        )
        ctx = ValidationContext(
            invariants=_zh_invariants(), chapter_no=1, scope="chapter"
        )
        violations = check.run(text, ctx)
        assert len(violations) == 1
        assert violations[0].code == "OPENING_ENTITY_OVERLOAD"
        assert violations[0].severity == "block"
        assert "命名实体" in violations[0].prompt_feedback

    def test_en_below_threshold_passes(self) -> None:
        check = EntityDensityCheck(max_entities=5)
        text = (
            "Jane Smith walked into the courtyard. "
            "Mrs. Parker was waiting. "
            "Elena Vance watched from the balcony. "
        ) * 5
        ctx = ValidationContext(
            invariants=_en_invariants(), chapter_no=1, scope="chapter"
        )
        violations = check.run(text, ctx)
        assert violations == []

    def test_en_over_threshold_blocks(self) -> None:
        check = EntityDensityCheck(max_entities=5)
        text = (
            "Jane Smith met Mr. Parker. Elena Vance noticed Sir Rowan Gale. "
            "Dr. Marcus Chen stepped aside for Lady Claire Ashford. "
            "Lord Darius Rook conferred with Miss Beatrix Lang. "
            "Prof Alistair Moss watched from the corner."
        )
        ctx = ValidationContext(
            invariants=_en_invariants(), chapter_no=1, scope="chapter"
        )
        violations = check.run(text, ctx)
        assert len(violations) == 1
        assert violations[0].code == "OPENING_ENTITY_OVERLOAD"
        # Detail reports the observed entity count (anything > limit is valid).
        observed = int(violations[0].location.split(":")[-1])
        assert observed > 5

    def test_head_lines_truncation(self) -> None:
        # Extra entities after `head_lines` shouldn't be counted.
        check = EntityDensityCheck(head_lines=3, max_entities=5)
        opening_lines = "林奚走进庭院。\n赵无极站在远处。\n孙小明低头不语。\n"
        after_lines = "\n".join([
            "陈大海推门而入。", "王铁柱闻声转头。", "周子楠轻声叹息。",
            "吴天成走向窗边。", "郑小雨靠着廊柱。"
        ])
        text = opening_lines + after_lines
        ctx = ValidationContext(
            invariants=_zh_invariants(), chapter_no=1, scope="chapter"
        )
        violations = check.run(text, ctx)
        # First 3 lines have 3 entities — below threshold, no violation.
        assert violations == []

    def test_empty_text_returns_no_violations(self) -> None:
        check = EntityDensityCheck()
        ctx = ValidationContext(
            invariants=_zh_invariants(), chapter_no=1, scope="chapter"
        )
        assert check.run("", ctx) == []
        assert check.run("   \n\n   ", ctx) == []


# ---------------------------------------------------------------------------
# OutputValidator orchestrator + Phase 1 factory
# ---------------------------------------------------------------------------


class TestOutputValidator:
    def test_report_aggregates_violations(self) -> None:
        validator = build_phase1_validator()
        # English project with CJK leak AND under length.
        text = "Short. 她说。"
        report = validator.validate(text, _chapter_ctx(_en_invariants()))
        codes = {v.code for v in report.violations}
        assert "LANG_LEAK_CJK_IN_EN" in codes
        assert "LENGTH_UNDER" in codes
        assert report.blocks_write
        assert report.has_issues

    def test_regen_feedback_is_composed_in_chinese(self) -> None:
        validator = build_phase1_validator()
        text = "Short. " + "她说。" * 30
        report = validator.validate(text, _chapter_ctx(_en_invariants()))
        feedback = report.feedback_for_regen()
        assert "质量校验" in feedback
        assert "[LANG_LEAK_CJK_IN_EN]" in feedback

    def test_clean_draft_produces_empty_report(self) -> None:
        validator = OutputValidator([LanguageSignatureCheck()])
        # Long enough English prose, no CJK.
        text = "She drew the sigil in the dust. " * 200
        report = validator.validate(text, _chapter_ctx(_en_invariants()))
        assert not report.has_issues
        assert not report.blocks_write
        assert report.feedback_for_regen() == ""
