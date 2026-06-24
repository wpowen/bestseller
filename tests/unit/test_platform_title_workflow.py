"""Regression tests for platform title workflow title quality.

Covers the 2026-06-03 书名生成回归 fixes:
- T-0: 题材分类（修仙/悬疑…）不得作为书名内容泄漏进标题。
- T-A: 模型/人工写的主标题默认保留，不被模板无条件覆盖。
- F2: 凝练 IP 名（无标点书名）应被评估器识别，而非一律判废。
- token 清洗：原始 sub_genre 的分隔符（·／、）不得进标题。

See docs/书名生成回归-诊断与开发计划-20260603.md.
"""

from __future__ import annotations

import pytest

from bestseller.services.platform_title_workflow import (
    DEFAULT_TITLE_CANDIDATE_COUNT,
    PLATFORM_TITLE_MATRIX_KEYS,
    PLATFORM_TITLE_STYLES,
    evaluate_platform_title_candidate,
    _evaluate_reader_attraction,
    _signal_tokens,
    _title_uses_genre_label,
    is_bare_taxonomy_title,
    build_story_grounded_title_revision_messages,
    build_platform_title_workflow,
    build_story_dna_fallback_title,
    build_title_revision_messages,
    finalize_revised_title,
    select_primary_platform_title,
    should_revise_primary_title,
    title_readability_issue,
)

GENRE_WORDS = ("修仙", "修真", "仙侠", "玄幻", "悬疑", "灵异", "言情", "科幻", "末世")


def _thin_genre_profile() -> dict:
    """A profile whose story DNA is thin: only genre/taxonomy labels, no real hook."""
    return {
        "language": "zh-CN",
        "primary_title": "青云志",
        "primary_category": "仙侠",
        "secondary_category": "修仙",
        "tags": ["仙侠", "修真", "升级流"],
        "logline": "一个少年的修仙之路。",
        "reader_promise": "修仙",
        "main_characters": [{"name": "主角", "role": "主角", "identity": "修士"}],
    }


def _xianxia_revenge_profile() -> dict:
    return {
        "language": "zh-CN",
        "primary_title": "烬骨登天录",
        "primary_category": "玄幻",
        "secondary_category": "修真·复仇·宗门权谋",
        "tags": ["复仇", "宗门", "逆袭", "炼丹", "废柴流"],
        "logline": "废柴弟子被逐出宗门，靠一缕残魂逆命登天，向背叛者复仇。",
        "short_intro": "火尽骨鸣，逆命登天。",
        "reader_promise": "越被打压越爽的逆袭复仇",
        "main_characters": [{"name": "萧烬", "role": "主角", "identity": "被逐废柴弟子"}],
    }


def _all_candidate_titles(profile: dict, platform: str) -> list[str]:
    workflow = build_platform_title_workflow(profile, target_platform=platform)
    return [str(c.get("title")) for c in workflow.get("candidates", [])]


# --- T-0: genre labels must not become title content -----------------------


@pytest.mark.unit
@pytest.mark.parametrize("platform", ["qimao", "qidian", "fanqie", "jinjiang", "feilu", ""])
def test_thin_genre_profile_does_not_emit_genre_label_title(platform: str) -> None:
    """When story DNA is thin, the genre taxonomy must not become the title."""
    profile = _thin_genre_profile()
    result = select_primary_platform_title(profile, target_platform=platform)
    title = str(result.get("title") or "")
    # Final title must not be a bare genre label like 修真之书 / 我在修仙靠修真翻盘.
    for word in GENRE_WORDS:
        assert word not in title, f"genre word {word!r} leaked into title {title!r} ({platform})"


@pytest.mark.unit
def test_thin_genre_profile_prefers_model_title() -> None:
    """Thin DNA → fall back to the model's title (青云志), not a fabricated genre title."""
    profile = _thin_genre_profile()
    result = select_primary_platform_title(profile, target_platform="qimao")
    assert result.get("title") == "青云志"


@pytest.mark.unit
@pytest.mark.parametrize("platform", ["qimao", "qidian", "fanqie", "general"])
def test_no_candidate_is_genre_dominated(platform: str) -> None:
    profile = _thin_genre_profile()
    for title in _all_candidate_titles(profile, platform):
        assert not _title_uses_genre_label(
            title, _signal_tokens(profile)
        ), f"genre-dominated candidate survived: {title!r}"


# --- T-0b: bare taxonomy/category names must never become titles ------------
# Regression for the 2026-06-23 报告：「都市高武」直接当书名。conception 的兜底曾
# 用 sub_genre[:8]/genre[:8] 当书名，而 _GENRE_LABEL_WORDS 词表又漏掉了
# 高武/末世/脑洞/无限流… 整类品类词，导致题材名一路 pass 到落库。


@pytest.mark.unit
@pytest.mark.parametrize(
    "title",
    [
        "都市高武",
        "高武世界",
        "末世",
        "都市脑洞",
        "无限流",
        "系统",
        "都市末世",
        "高武大陆",
    ],
)
def test_bare_taxonomy_titles_detected(title: str) -> None:
    assert is_bare_taxonomy_title(title), f"bare taxonomy {title!r} not detected"


@pytest.mark.unit
@pytest.mark.parametrize(
    "title",
    [
        "重生1979",
        "末世第一基地",
        "都市之王",
        "蚀漏砚",
        "福星甩不掉",
        "烬骨登天录",
        "青云志",
        "我有一座末世农场",
    ],
)
def test_real_titles_with_trope_words_not_flagged(title: str) -> None:
    # A title that merely *contains* a trope word (重生/末世/都市…) is legitimate.
    assert not is_bare_taxonomy_title(title), f"real title {title!r} wrongly flagged"


@pytest.mark.unit
@pytest.mark.parametrize("title", ["都市高武", "高武世界", "末世", "无限流"])
@pytest.mark.parametrize("platform", ["fanqie", "qimao", "qidian", "general"])
def test_genre_name_seed_does_not_survive_as_title(title: str, platform: str) -> None:
    """A genre-name seed title must be rejected and replaced, never shipped."""
    profile = {
        "language": "zh-CN",
        "primary_title": title,  # the genre name leaked in as the seed
        "primary_category": "东方仙侠+高武升级流",
        "secondary_category": title,
        "tags": ["复仇", "觉醒", "逆袭"],
        "logline": "一个普通人觉醒祭词之力，向灭门旧案的幕后复仇。",
        "reader_promise": "越被打压越爽的逆袭复仇",
        "main_characters": [{"name": "陈砚", "role": "主角", "identity": "觉醒者"}],
    }
    assert evaluate_platform_title_candidate(
        profile, title, target_platform=platform
    )["decision"] == "reject"
    result = select_primary_platform_title(profile, target_platform=platform)
    final = str(result.get("title") or "")
    assert final, "selection returned an empty title"
    assert not is_bare_taxonomy_title(final), f"bare taxonomy survived: {final!r}"
    assert final != title


# --- T-A: model title preserved over mechanical templates -------------------


@pytest.mark.unit
@pytest.mark.parametrize("platform", ["qimao", "qidian", "jinjiang"])
def test_model_title_preserved_over_template(platform: str) -> None:
    """A good model title must win over template candidates (no 复仇神探/宗门案卷)."""
    profile = _xianxia_revenge_profile()
    result = select_primary_platform_title(profile, target_platform=platform)
    assert result.get("title") == "烬骨登天录"


# --- token cleaning: no separator leak --------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("platform", ["fanqie", "qimao", "qidian", "feilu"])
def test_no_separator_leak_in_candidates(platform: str) -> None:
    profile = _xianxia_revenge_profile()
    for title in _all_candidate_titles(profile, platform):
        for sep in ("·", "／", "、"):
            assert sep not in title, f"separator {sep!r} leaked into {title!r}"


# --- F2: concise IP-name titles evaluate sensibly ---------------------------


@pytest.mark.unit
@pytest.mark.parametrize("title", ["烬骨登天录", "青云志", "焚天问道"])
def test_ip_name_reader_attraction_passes(title: str) -> None:
    style = PLATFORM_TITLE_STYLES["qimao"]
    result = _evaluate_reader_attraction(title, style)
    assert result["passed"], f"concise IP name {title!r} wrongly judged unattractive"


# --- helper unit tests ------------------------------------------------------


@pytest.mark.unit
def test_title_uses_genre_label_helper() -> None:
    signals = _signal_tokens(_thin_genre_profile())
    assert _title_uses_genre_label("修真神探", signals)
    assert _title_uses_genre_label("我在修仙靠修真翻盘", signals)
    assert _title_uses_genre_label("修真之书", signals)
    assert _title_uses_genre_label("修真规则：谁撒谎谁先出局", signals)
    assert not _title_uses_genre_label("青云志", signals)
    assert not _title_uses_genre_label("烬骨登天录", signals)
    # A sanctioned genre channel prefix is allowed when the body is clean.
    assert not _title_uses_genre_label("仙侠：开局复仇我证道")


# --- P2: LLM revision policy (sync, no LLM) ---------------------------------


def _weak_candidate(
    title: str, decision: str, pattern: str = "当前主书名校准"
) -> dict:
    """A model-title candidate by default; pass a template pattern for fallbacks."""
    return {
        "title": title,
        "pattern": pattern,
        "title_evaluation": {"decision": decision, "feedback": {}},
    }


@pytest.mark.unit
def test_should_revise_skips_clean_ip_name() -> None:
    # The model's own concise IP name must never be barker-rewritten.
    assert not should_revise_primary_title(_weak_candidate("烬骨登天录", "revise"))
    assert not should_revise_primary_title(_weak_candidate("青云志", "reject"))


@pytest.mark.unit
def test_should_revise_skips_passing_title() -> None:
    assert not should_revise_primary_title(_weak_candidate("开局复仇我翻盘", "pass"))


@pytest.mark.unit
def test_should_revise_skips_story_bridge_title() -> None:
    # Story-derived public-emotion bridge titles are trusted, not rewritten.
    assert not should_revise_primary_title(
        _weak_candidate("旧榜错判我我用新规则翻案", "revise", pattern="公共情绪桥：value_bridge")
    )


@pytest.mark.unit
def test_should_revise_fires_for_weak_model_soup_title() -> None:
    assert should_revise_primary_title(_weak_candidate("宗门案卷规则破局录", "revise"))


@pytest.mark.unit
def test_should_revise_fires_for_template_fallback_even_if_ip_shaped() -> None:
    # O1 regression: when the model title is rejected, selection falls back to a
    # mechanical template like 「灭门遗孤神探」 (a genre-mismatch). Even though it is
    # short and punctuation-free (IP-shaped), it must still be sent for revision.
    assert should_revise_primary_title(
        _weak_candidate("灭门遗孤神探", "revise", pattern="强职业爽点")
    )


# --- Structural demotion: template never ships as the primary ----------------


def _rejected_model_profile(model_title: str) -> dict:
    return {
        "language": "zh-CN",
        "primary_title": model_title,
        "primary_category": "玄幻",
        "secondary_category": "修真·复仇·宗门权谋",
        "tags": ["灭门遗孤", "复仇", "黑骨印", "九壑宗", "代价升级"],
        "logline": "沈烬在灭门之夜活下来，背上会吞寿元的黑骨印；他为复仇踏入九壑宗。",
        "reader_promise": "每次突破都有真实代价账。",
        "main_characters": [{"name": "沈烬", "identity": "灭门遗孤·负债型复仇者"}],
    }


@pytest.mark.unit
def test_dna_fallback_salvages_model_title_by_stripping_genre() -> None:
    profile = _rejected_model_profile("玄幻修真复仇宗门权谋录")
    assert build_story_dna_fallback_title(profile, target_platform="qimao") == "复仇宗门权谋录"


@pytest.mark.unit
def test_dna_fallback_uses_story_token_when_model_title_is_pure_genre() -> None:
    profile = _rejected_model_profile("修仙")
    fallback = build_story_dna_fallback_title(profile, target_platform="qimao")
    assert fallback
    assert not _title_uses_genre_label(fallback)


@pytest.mark.unit
@pytest.mark.parametrize("platform", ["qimao", "qidian", "fanqie", "jinjiang"])
def test_rejected_model_title_never_ships_template_as_primary(platform: str) -> None:
    # A genre-soup model title is rejected; the primary must be a clean story-DNA
    # fallback (pattern 故事DNA兜底), never a mechanical template (神探/案卷/诡案录).
    profile = _rejected_model_profile("玄幻修真复仇宗门权谋录")
    primary = select_primary_platform_title(profile, target_platform=platform)
    title = str(primary.get("title") or "")
    assert not any(mark in title for mark in ("神探", "案卷", "诡案", "夜巡人"))
    assert primary.get("pattern") == "故事DNA兜底"
    assert primary.get("requires_llm_revision") is True
    assert should_revise_primary_title(primary)  # provisional → LLM regenerates


@pytest.mark.unit
def test_build_revision_messages_excludes_genre_taxonomy() -> None:
    profile = _xianxia_revenge_profile()
    candidate = _weak_candidate("宗门案卷规则破局录", "revise")
    messages = build_title_revision_messages(profile, candidate, target_platform="qimao")
    assert messages is not None
    system, user = messages
    # The story DNA (logline) must be present; raw genre taxonomy must not be
    # offered as title content.
    assert "逆命登天" in user
    assert "修真·复仇·宗门权谋" not in user


@pytest.mark.unit
def test_build_revision_messages_returns_none_for_clean_ip() -> None:
    profile = _xianxia_revenge_profile()
    candidate = _weak_candidate("烬骨登天录", "revise")
    assert build_title_revision_messages(profile, candidate, target_platform="qimao") is None


@pytest.mark.unit
def test_finalize_revised_title_adopts_valid_revision() -> None:
    profile = _xianxia_revenge_profile()
    adopted, was_revised = finalize_revised_title(
        profile, "宗门案卷规则破局录", "《逆命焚骨录》", target_platform="qimao"
    )
    assert was_revised
    assert adopted == "逆命焚骨录"  # quotes/title-marks stripped


@pytest.mark.unit
def test_finalize_revised_title_rejects_genre_label_revision() -> None:
    profile = _xianxia_revenge_profile()
    adopted, was_revised = finalize_revised_title(
        profile, "宗门案卷规则破局录", "修仙传说", target_platform="qimao"
    )
    assert not was_revised
    assert adopted == "宗门案卷规则破局录"


@pytest.mark.unit
def test_finalize_revised_title_rejects_empty() -> None:
    profile = _xianxia_revenge_profile()
    adopted, was_revised = finalize_revised_title(
        profile, "原标题书名", "", target_platform="qimao"
    )
    assert not was_revised
    assert adopted == "原标题书名"


def _planning_grounded_profile() -> dict:
    profile = _xianxia_revenge_profile()
    profile.update(
        {
            "primary_title": "验房师不开整改单",
            "target_platform": "qimao",
            "story_title_dna": {
                "protagonist": "程恪",
                "identity": "异常灵气场所验房师",
                "opening": "48小时内进入灵气浓度超标的写字楼。",
                "central_action": "签出已触发强制复检的终版验房报告。",
                "conflict": "周庆把程恪每一步专业动作转译成验房师越权。",
                "stakes": "执照扣分、冷门岗位存续审查、职业边界扩展黑档案。",
                "payoff": "用双人交叉验房条款把对手拖进合规台账。",
            },
            "title_anchor_groups": {
                "identity": ["异常灵气场所验房师", "程恪"],
                "action": ["签发", "强制复检", "双人交叉验房", "指出违规"],
                "object": [
                    "验房报告",
                    "岗位说明书",
                    "豁免清单",
                    "合规台账",
                    "执照扣分",
                    "48小时",
                    "写字楼",
                ],
                "stakes": ["执照扣分", "冷门岗位", "职业边界", "曝光度"],
            },
        }
    )
    return profile


@pytest.mark.unit
def test_story_grounded_revision_prompt_uses_outline_dna() -> None:
    system, user = build_story_grounded_title_revision_messages(
        _planning_grounded_profile(),
        current_title="验房师不开整改单",
        target_platform="qimao",
        reason="negative_document_verb_phrase",
    )
    assert "已批准的大纲" in system
    assert "签出已触发强制复检" in user
    assert "双人交叉验房" in user
    assert "修真·复仇·宗门权谋" not in user


@pytest.mark.unit
def test_title_readability_rejects_broken_document_verb_phrase() -> None:
    assert title_readability_issue("验房师不开整改单") == "negative_document_verb_phrase"


@pytest.mark.unit
def test_finalize_revised_title_skips_bad_candidate_and_uses_story_anchor() -> None:
    adopted, was_revised = finalize_revised_title(
        _planning_grounded_profile(),
        "验房师不开整改单",
        "验房师不开整改单\n我用验房报告逼出强制复检",
        target_platform="qimao",
    )
    assert was_revised
    assert adopted == "我用验房报告逼出强制复检"


@pytest.mark.unit
def test_finalize_revised_title_rejects_story_ungrounded_candidate() -> None:
    adopted, was_revised = finalize_revised_title(
        _planning_grounded_profile(),
        "验房师不开整改单",
        "验房师逆袭录",
        target_platform="qimao",
    )
    assert was_revised
    assert adopted == "我用验房报告逼出强制复检"


@pytest.mark.unit
def test_finalize_revised_title_ranks_story_transmission_over_short_label() -> None:
    adopted, was_revised = finalize_revised_title(
        _planning_grounded_profile(),
        "验房师不开整改单",
        "\n".join(
            [
                "验房报告钉死超标",
                "我用验房报告逼出强制复检",
                "48小时验房局",
            ]
        ),
        target_platform="qimao",
    )
    assert was_revised
    assert adopted == "我用验房报告逼出强制复检"


@pytest.mark.unit
def test_finalize_revised_title_uses_story_fallback_when_llm_titles_are_weak() -> None:
    adopted, was_revised = finalize_revised_title(
        _planning_grounded_profile(),
        "验房师不开整改单",
        "\n".join(
            [
                "验房报告钉死超标",
                "我把超标写进验房报告",
            ]
        ),
        target_platform="qimao",
    )
    assert was_revised
    assert adopted == "我用验房报告逼出强制复检"


@pytest.mark.unit
def test_story_grounded_action_title_passes_methodology_evaluation() -> None:
    result = evaluate_platform_title_candidate(
        _planning_grounded_profile(),
        "我用验房报告逼出强制复检",
        target_platform="qimao",
    )
    assert result["decision"] == "pass"
    assert result["checks"]["reader_attraction"]["passed"]
    assert result["checks"]["story_transmission"]["passed"]


@pytest.mark.unit
def test_story_grounded_platform_matrix_fills_every_platform_group() -> None:
    profile = _planning_grounded_profile()
    profile["primary_title"] = "我用验房报告逼出强制复检"
    workflow = build_platform_title_workflow(profile, target_platform="qimao")

    assert workflow["candidate_policy"] == "platform_matrix_5_each"
    assert workflow["platform_count"] == len(PLATFORM_TITLE_MATRIX_KEYS)
    assert workflow["candidate_count"] == DEFAULT_TITLE_CANDIDATE_COUNT
    assert all(group["candidate_count"] == 5 for group in workflow["platform_groups"])
    assert workflow["recommended_primary_title"]["title_evaluation"]["decision"] == "pass"


@pytest.mark.unit
def test_story_grounded_platform_matrix_rejects_topic_or_broken_candidates() -> None:
    profile = _planning_grounded_profile()
    profile["primary_title"] = "我用验房报告逼出强制复检"
    workflow = build_platform_title_workflow(profile, target_platform="qimao")
    candidates = workflow["candidates"]
    titles = [str(candidate.get("title") or "") for candidate in candidates]

    assert len(titles) == DEFAULT_TITLE_CANDIDATE_COUNT
    for candidate in candidates:
        title = str(candidate.get("title") or "")
        assert not title_readability_issue(title), f"broken title survived: {title!r}"
        assert not any(
            bad in title
            for bad in ("灵气复苏", "系统", "神探", "案卷", "之书", "会决定")
        ), f"topic-led or mismatched candidate survived: {title!r}"
        assert candidate["title_evaluation"]["decision"] == "pass", (
            f"weak candidate survived: {title!r}"
        )


def _bureaucratic_cultivation_profile() -> dict:
    return {
        "language": "zh-CN",
        "primary_title": "临聘仙官从工单考编开始",
        "primary_category": "都市修仙",
        "secondary_category": "职业升级流",
        "target_platform": "番茄小说",
        "tags": ["灵务署", "考编", "岗位权限", "公务工单", "临聘巡检"],
        "logline": "沈砚是灵务署临聘巡检员，靠岗位权限和公务工单在修仙公共系统里考编升级。",
        "reader_promise": [
            "读者追看沈砚用最低岗位权限签高危工单，当众打脸卡编制的人。",
            "每个公务案件都兑现一次考编积分、灵石配额或岗位权限升级。",
        ],
        "main_characters": [{"name": "沈砚", "identity": "灵务署临聘巡检员"}],
        "title_anchor_groups": {
            "identity": ["沈砚", "灵务署", "临聘巡检"],
            "action": ["签工单", "考编", "岗位权限"],
            "object": ["公务工单", "灵石配额", "审批黑箱"],
            "stakes": ["扣考编分", "妹妹配额", "转正资格"],
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize("bad_title", ["证道录", "只准证道，我偏要签证道"])
def test_bureaucratic_cultivation_rejects_ungrounded_zhengdao_titles(
    bad_title: str,
) -> None:
    result = evaluate_platform_title_candidate(
        _bureaucratic_cultivation_profile(),
        bad_title,
        target_platform="番茄小说",
    )

    assert result["decision"] == "reject"
    assert result["checks"]["story_transmission"]["passed"] is False


@pytest.mark.unit
def test_bureaucratic_cultivation_title_candidates_keep_story_anchors() -> None:
    workflow = build_platform_title_workflow(
        _bureaucratic_cultivation_profile(),
        target_platform="番茄小说",
    )

    titles = [str(candidate.get("title") or "") for candidate in workflow["candidates"]]
    approved_anchors = (
        "灵务署",
        "考编",
        "临聘",
        "工单",
        "岗位权限",
        "沈砚",
        "审批黑箱",
        "转正资格",
        "扣考编分",
        "妹妹配额",
        "灵石配额",
    )
    assert titles
    assert not any(title in {"证道录", "只准证道，我偏要签证道"} for title in titles)
    assert all(
        any(anchor in title for anchor in approved_anchors)
        for title in titles[:10]
    )


def test_detective_title_templates_gated_by_genre() -> None:
    """Batch B: 案卷/诡案/神探/奇案 must only appear for genuine detective
    genres, not be stamped onto every 起点/七猫 book."""
    from bestseller.services.platform_title_workflow import (
        _is_detective_title_genre,
        _platform_template_specs,
    )

    s = {
        "title": "示例", "threat": "魔头", "setting": "天南", "object": "飞剑",
        "hook": "灵气复苏", "hook2": "废灵根", "entry": "宗门", "origin": "杂役",
        "action": "翻盘", "identity": "外门弟子", "protagonist": "主角",
    }
    detective_words = ("案卷", "诡案", "神探", "奇案")

    for key in ("qidian", "qimao"):
        neutral = " ".join(t for t, *_ in _platform_template_specs(key, s, detective=False))
        assert not any(w in neutral for w in detective_words), neutral
        detective = " ".join(t for t, *_ in _platform_template_specs(key, s, detective=True))
        assert any(w in detective for w in detective_words)

    assert _is_detective_title_genre({"genre": "都市修真", "sub_genre": "修仙2.0"}) is False
    assert _is_detective_title_genre({"genre": "悬疑", "sub_genre": "探案"}) is True
    assert _is_detective_title_genre({"genre": "都市", "tags": ["灵异", "怪谈"]}) is True
