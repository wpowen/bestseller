"""L1 tests for services/blurb_pathology.py (T3 — 简介确定性病理检测器).

真机病例（tracked-rulehorror-v1, 2026-07-09）：
  - synopsis 开头 "保饭碗还是丢工作？" —— 同义反复选择句。
  - reader_promise 是未适配的锦鲤钩子模板（"命运越眷顾"），且是流水长句。
  - synopsis 正文多处机制黑话（"共情被削薄""反写""压制升级"）。

template_residue 的正例改用干净的合成句：手工核对真机 reader_promise 文本后确认
"命运越眷顾" 在其中只是【近似重复】（第二次出现中间插了一个"越"字，不是精确
4-gram 重复），该案例真正命中的是 run_on_sentence；template_residue 需要一个
真正逐字重复的合成句来验证检测逻辑本身是对的。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese fixtures are intentional.
import pytest

from bestseller.services.blurb_pathology import (
    derive_book_jargon_terms,
    detect_blurb_pathology,
    truncate_at_sentence,
)

REAL_BAD_SYNOPSIS = (
    "保饭碗还是丢工作？凶宅试睡员闻雀只剩这一单。\n\n"
    "按下七楼那晚是七点五十九，电梯按键下贴着手写规则：八点后不可停。\n\n"
    "十年夜班让他对异常没反应——别人毛骨悚然，他能照常躺着记报告。这是本事，"
    "也是代价：他连室友去世都没哭过，共情被削薄到几乎为零。\n\n"
    "他以为自己在钻规则漏洞，规则在用他的钻营反写他——守一次，福利到账；"
    "下一次违规的门槛悄悄被调低。\n\n"
    "他必须熬过七天，写出一条新规则压制升级——代价是他再也分不清真实与伪造。"
)

REAL_READER_PROMISE = (
    "先付替人接下重病或灾祸，才换得到临时神运；主角越想被命运眷顾，"
    "越会被迫命运越眷顾，越要替别人接下灾厄；好运不是免费，是要替人扛下等量的祸，"
    "并让被替者不知主角的牺牲持续发酵。"
)

CLEAN_SYNOPSIS = (
    "空调外机铜管藏着1987年的黄纸，穷房东能听懂鬼话，代价是每听懂一句就丢一种"
    "活人的感觉。他靠这门手艺一夜暴富，却发现自己越来越像个死人。"
)


@pytest.mark.unit
class TestTautologyChoice:
    def test_real_bad_opening_hits(self):
        findings = detect_blurb_pathology("保饭碗还是丢工作？")
        codes = [f.code for f in findings]
        assert "tautology_choice" in codes
        f = next(f for f in findings if f.code == "tautology_choice")
        assert f.severity == "fatal"

    def test_genuine_dilemma_does_not_hit(self):
        """两难选择(不同利害的两件事)不应被误判为同义反复。"""

        findings = detect_blurb_pathology("救她，还是保住自己的秘密？")
        codes = [f.code for f in findings]
        assert "tautology_choice" not in codes

    def test_clean_synopsis_has_no_tautology(self):
        findings = detect_blurb_pathology(CLEAN_SYNOPSIS)
        assert not any(f.code == "tautology_choice" for f in findings)


@pytest.mark.unit
class TestJargonLeak:
    def test_book_jargon_hits_fatal_at_threshold(self):
        jargon = ("削薄", "反写", "压制")
        findings = detect_blurb_pathology(REAL_BAD_SYNOPSIS, book_jargon_terms=jargon)
        f = next(f for f in findings if f.code == "jargon_leak")
        assert f.severity == "fatal"
        assert "削薄" in f.excerpt

    def test_single_jargon_hit_is_warn_not_fatal(self):
        findings = detect_blurb_pathology(REAL_BAD_SYNOPSIS, book_jargon_terms=("削薄",))
        f = next(f for f in findings if f.code == "jargon_leak")
        assert f.severity == "warn"

    def test_no_jargon_terms_no_finding(self):
        findings = detect_blurb_pathology(CLEAN_SYNOPSIS, book_jargon_terms=())
        assert not any(f.code == "jargon_leak" for f in findings)

    def test_clean_synopsis_unaffected_by_unrelated_jargon(self):
        findings = detect_blurb_pathology(CLEAN_SYNOPSIS, book_jargon_terms=("削薄", "反写"))
        assert not any(f.code == "jargon_leak" for f in findings)


@pytest.mark.unit
class TestTemplateResidue:
    def test_exact_repeated_phrase_in_one_sentence_hits(self):
        text = "他知道命运越眷顾就要付出代价，可他没想到命运越眷顾的代价这么大。"
        findings = detect_blurb_pathology(text)
        f = next(f for f in findings if f.code == "template_residue")
        assert "命运越眷顾" in f.excerpt

    def test_whitelisted_reduplication_does_not_hit(self):
        text = "他一步一步爬上去，一步一步走向终点，终于站上了城墙。"
        findings = detect_blurb_pathology(text)
        assert not any(f.code == "template_residue" for f in findings)

    def test_clean_synopsis_has_no_residue(self):
        findings = detect_blurb_pathology(CLEAN_SYNOPSIS)
        assert not any(f.code == "template_residue" for f in findings)


@pytest.mark.unit
class TestRunOnSentence:
    def test_real_reader_promise_hits_warn(self):
        findings = detect_blurb_pathology(REAL_READER_PROMISE)
        f = next(f for f in findings if f.code == "run_on_sentence")
        assert f.severity == "warn"

    def test_short_clean_sentence_does_not_hit(self):
        findings = detect_blurb_pathology("他一夜暴富，却越来越像个死人。")
        assert not any(f.code == "run_on_sentence" for f in findings)


@pytest.mark.unit
class TestDetectBlurbPathologyIntegration:
    def test_real_bad_synopsis_has_fatal_findings(self):
        findings = detect_blurb_pathology(
            REAL_BAD_SYNOPSIS, book_jargon_terms=("削薄", "反写", "压制升级")
        )
        assert any(f.severity == "fatal" for f in findings)

    def test_clean_synopsis_has_zero_findings(self):
        assert detect_blurb_pathology(CLEAN_SYNOPSIS, book_jargon_terms=()) == []

    def test_empty_text_returns_empty(self):
        assert detect_blurb_pathology("") == []
        assert detect_blurb_pathology(None) == []  # type: ignore[arg-type]

    def test_config_overrides_thresholds(self):
        # jargon_fatal_hits=1 → 单个词命中即 fatal(默认要 3 个)
        findings = detect_blurb_pathology(
            REAL_BAD_SYNOPSIS,
            book_jargon_terms=("削薄",),
            config={"jargon_fatal_hits": 1},
        )
        f = next(f for f in findings if f.code == "jargon_leak")
        assert f.severity == "fatal"


@pytest.mark.unit
class TestDeriveBookJargonTerms:
    def test_derives_stems_present_in_design_text(self):
        metadata = {
            "golden_finger": {
                "description": (
                    "职业钝化——长期夜班让闻雀对异常的不适感知钝化。这种削薄不可逆，"
                    "规则会根据主角行为反写自身。"
                ),
            },
        }
        terms = derive_book_jargon_terms(metadata)
        assert "削薄" in terms
        assert "反写" in terms

    def test_stem_not_present_in_design_text_is_excluded(self):
        metadata = {"golden_finger": {"description": "普通的金手指描述，没有机制词根。"}}
        terms = derive_book_jargon_terms(metadata)
        assert "削薄" not in terms
        assert "反写" not in terms

    def test_entity_whitelist_excludes_names(self):
        metadata = {
            "golden_finger": {"description": "闻雀的削薄能力来自长期夜班。"},
        }
        terms_without_whitelist = derive_book_jargon_terms(metadata)
        terms_with_whitelist = derive_book_jargon_terms(
            metadata, entity_whitelist=("闻雀",)
        )
        # 削薄 是词根命中，不受主角名白名单影响；主角名本身不应出现在词表里。
        assert "闻雀" not in terms_with_whitelist
        assert "闻雀" not in terms_without_whitelist  # 词根表本就不含人名，双重保险

    def test_non_mapping_metadata_returns_empty(self):
        assert derive_book_jargon_terms(None) == ()  # type: ignore[arg-type]
        assert derive_book_jargon_terms([]) == ()  # type: ignore[arg-type]

    def test_bracketed_terms_extracted_from_design_text(self):
        metadata = {
            "power_system": "系统给他分配了「重写权」，这是「合规溢价」的具象化。",
        }
        terms = derive_book_jargon_terms(metadata)
        assert "重写权" in terms
        assert "合规溢价" in terms


@pytest.mark.unit
class TestTruncateAtSentence:
    def test_short_text_unchanged(self):
        assert truncate_at_sentence("短文本。", 500) == "短文本。"

    def test_truncates_at_sentence_boundary(self):
        text = "第一句话说得很完整。" + "填充字符" * 30 + "。这是被截断前不该出现的内容。"
        result = truncate_at_sentence(text, 20)
        assert result.endswith("。")
        assert "被截断前不该出现" not in result

    def test_falls_back_to_hard_cut_with_ellipsis_when_no_sentence_boundary(self):
        text = "没有任何句子结束标点的一长串纯文本" * 10
        result = truncate_at_sentence(text, 50)
        assert result.endswith("...")
        assert len(result) == 50

    def test_matches_legacy_497_plus_ellipsis_behavior_shape(self):
        # 回归对照：旧行为 text[:497] + "..." 会硬截半句；新行为在句界截断，
        # 不应比旧行为更长，且绝不截出半句(以句末标点或省略号结尾)。
        text = "。".join(["第%d句内容填充一些字符凑长度" % i for i in range(60)]) + "。"
        result = truncate_at_sentence(text, 500)
        assert len(result) <= 500
        assert result.endswith("。") or result.endswith("...")


@pytest.mark.unit
class TestHighConceptJargonSource:
    """2026-07-09《我靠签契改地脉》真机回归：概念淘汰赛冠军的学术词汇(拓扑/语义)
    经 spine/premise 渗入简介,persona 划走理由"名词堆得脑瓜子疼"——冠军概念文本
    必须纳入黑话派生源,让文案淘汰赛把这些词当禁用词逼翻译成大白话。"""

    def test_high_concept_academic_stems_derived(self):
        metadata = {
            "high_concept": {
                "concept": "替两界仙尊做同传，每次翻译都是一次边界战争。",
                "mechanism": "每译出一份契约，等价于改写两界边界条件，地脉拓扑随之重算。",
            },
        }
        terms = derive_book_jargon_terms(metadata)
        assert "拓扑" in terms
        assert "边界条件" in terms

    def test_high_concept_absent_derives_nothing_new(self):
        assert "拓扑" not in derive_book_jargon_terms({"golden_finger": "普通金手指"})
