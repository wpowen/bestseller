"""Cross-book mechanism de-dup at the conception layer.

The concept-level twin of the cast-name de-dup (``_recent_cast_names`` →
``ctx['avoid_names']``): names already had cross-book de-dup, but nothing
stopped book N+1 from re-minting book N's core mechanism — the recurring
debt/ledger golden finger across xianxia-upgrade books. These tests cover the
fetch (same-genre grouping via genre_taxonomy.canonicalize, cross-genre
isolation, fail-open), the prompt block rendering, the prompt embedding, and
the settings kill-switch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bestseller.services import conception as conception_services

# ── fakes ──────────────────────────────────────────────────────────────────


class _FakeExecuteResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def execute(self, _stmt: object) -> _FakeExecuteResult:
        return _FakeExecuteResult(self._rows)


class _BoomSession:
    async def execute(self, _stmt: object) -> object:
        raise RuntimeError("db down")


def _meta(
    premise: str = "",
    golden_finger: str = "",
    tropes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "premise": premise,
        "writing_profile": {
            "character": {"golden_finger": golden_finger},
            "market": {"trope_keywords": tropes or []},
        },
    }


_XIANXIA_DEBT_META = _meta(
    premise="被宣判灵根枯竭的底层杂役纪尘，意外触碎无名界碑，觉醒上古宗运残契。",
    golden_finger="宗债簿——因果律异数，能将人心愿力折算为可支出的债币，每次兑现同步记一笔反欠账。",
    tropes=["升级", "宗门经营", "因果债约", "人心流"],
)

_XIANXIA_DEVOUR_META = _meta(
    premise="外门杂役在矿井深处被围剿，误吞的功法残页当场觉醒。",
    golden_finger="蚀刻之页——可吞噬术法残骸并以自身道骨为基底复刻融合。",
    tropes=["修仙2.0", "宗门黑账考古"],
)

_URBAN_META = _meta(
    premise="社畜江不闲只想躺平，却接手了福报结算系统。",
    golden_finger="福报结算系统——好运自动外溢给身边的人。",
    tropes=["气运团宠"],
)


# ── _recent_core_mechanisms: fetch + genre grouping ────────────────────────


@pytest.mark.asyncio
async def test_recent_core_mechanisms_groups_same_genre_and_excludes_others() -> None:
    rows = [
        ("宗债簿之书", "仙侠升级流·宗门经营·高概念修仙经济学", "宗门逆袭", _XIANXIA_DEBT_META),
        ("福星甩不掉", "都市脑洞", "社畜摆烂·气运团宠", _URBAN_META),
        ("蚀刻之页之书", "仙侠升级", "宗门逆袭", _XIANXIA_DEVOUR_META),
    ]
    out = await conception_services._recent_core_mechanisms(
        _FakeSession(rows),  # type: ignore[arg-type]
        genre="仙侠升级流",
        sub_genre="宗门逆袭",
    )
    titles = [entry["title"] for entry in out]
    # Free-form xianxia genre strings group under one canonical key; the urban
    # book never leaks into the xianxia avoid-list (跨题材不串扰).
    assert titles == ["宗债簿之书", "蚀刻之页之书"]
    assert out[0]["golden_finger"].startswith("宗债簿")
    assert "因果债约" in out[0]["trope_keywords"]


@pytest.mark.asyncio
async def test_recent_core_mechanisms_empty_db_is_noop() -> None:
    out = await conception_services._recent_core_mechanisms(
        _FakeSession([]),  # type: ignore[arg-type]
        genre="仙侠升级流",
        sub_genre="宗门逆袭",
    )
    assert out == []


@pytest.mark.asyncio
async def test_recent_core_mechanisms_is_failure_safe() -> None:
    out = await conception_services._recent_core_mechanisms(
        _BoomSession(),  # type: ignore[arg-type]
        genre="仙侠升级流",
        sub_genre="宗门逆袭",
    )
    assert out == []


@pytest.mark.asyncio
async def test_recent_core_mechanisms_truncates_fields_and_skips_empty() -> None:
    long_premise = "灵" * 200
    long_gf = "指" * 200
    rows = [
        ("超长条目", "仙侠升级流", "宗门逆袭", _meta(premise=long_premise, golden_finger=long_gf, tropes=[f"t{i}" for i in range(12)])),
        ("空机制条目", "仙侠升级流", "宗门逆袭", _meta()),
        ("烂元数据", "仙侠升级流", "宗门逆袭", {"writing_profile": "not-a-dict"}),
    ]
    out = await conception_services._recent_core_mechanisms(
        _FakeSession(rows),  # type: ignore[arg-type]
        genre="仙侠升级流",
        sub_genre="宗门逆袭",
    )
    # entries with no mechanism signal at all are dropped, malformed metadata never raises
    assert [entry["title"] for entry in out] == ["超长条目"]
    assert len(out[0]["premise"]) <= 80
    assert len(out[0]["golden_finger"]) <= 80
    assert len(out[0]["trope_keywords"]) <= 6


@pytest.mark.asyncio
async def test_recent_core_mechanisms_respects_limit() -> None:
    rows = [
        (f"书{i}", "仙侠升级流", "宗门逆袭", _XIANXIA_DEBT_META) for i in range(10)
    ]
    out = await conception_services._recent_core_mechanisms(
        _FakeSession(rows),  # type: ignore[arg-type]
        genre="仙侠升级流",
        sub_genre="宗门逆袭",
        limit=3,
    )
    assert len(out) == 3


@pytest.mark.asyncio
async def test_recent_core_mechanisms_unresolvable_genre_never_leaks_cross_genre() -> None:
    rows = [
        ("宗债簿之书", "仙侠升级流", "宗门逆袭", _XIANXIA_DEBT_META),
        ("同串怪书", "未知题材XYZ", None, _URBAN_META),
    ]
    out = await conception_services._recent_core_mechanisms(
        _FakeSession(rows),  # type: ignore[arg-type]
        genre="未知题材XYZ",
        sub_genre=None,
    )
    # target genre has no canonical key: only exact raw-genre matches qualify,
    # resolvable-but-different genres must not bleed in
    assert [entry["title"] for entry in out] == ["同串怪书"]


# ── _mechanism_dedup_prompt_block: rendering ───────────────────────────────


def _avoid_ctx() -> dict[str, Any]:
    return {
        "avoid_mechanisms": [
            {
                "title": "宗债簿之书",
                "golden_finger": "宗债簿——人心愿力折算为债币",
                "premise": "杂役触碎界碑觉醒宗运残契",
                "trope_keywords": ["因果债约", "宗门经营"],
            },
            {
                "title": "蚀刻之页之书",
                "golden_finger": "蚀刻之页——吞噬术法残骸复刻融合",
                "premise": "",
                "trope_keywords": [],
            },
        ]
    }


def test_mechanism_dedup_block_zh_lists_recent_mechanisms() -> None:
    block = conception_services._mechanism_dedup_prompt_block(_avoid_ctx(), is_en=False)
    assert "机制去重" in block
    assert "宗债簿之书" in block and "蚀刻之页之书" in block
    assert "宗债簿——人心愿力折算为债币" in block
    assert "因果债约" in block
    # the constraint is about mechanism families, not just literal names
    assert "换皮" in block or "同构" in block
    # …and it extends to thematic imagery: a new mechanism wearing the same
    # skin (the recurring 债/账 title-and-imagery problem) is also banned
    assert "意象" in block


def test_mechanism_dedup_block_is_noop_without_entries() -> None:
    assert conception_services._mechanism_dedup_prompt_block({}, is_en=False) == ""
    assert conception_services._mechanism_dedup_prompt_block({"avoid_mechanisms": []}, is_en=True) == ""
    # junk entries are ignored rather than raising
    assert conception_services._mechanism_dedup_prompt_block({"avoid_mechanisms": ["junk", 3]}, is_en=False) == ""


def test_mechanism_dedup_block_en_has_no_zh_header() -> None:
    block = conception_services._mechanism_dedup_prompt_block(_avoid_ctx(), is_en=True)
    assert "宗债簿之书" in block
    assert "机制去重" not in block
    assert "Mechanism de-duplication" in block
    assert "imagery" in block


# ── prompt embedding ───────────────────────────────────────────────────────


def _full_ctx() -> dict[str, Any]:
    return {
        "genre": "仙侠升级流",
        "sub_genre": "宗门逆袭",
        "description": "宗门底层杂役逆袭。",
        "chapter_count": 300,
        "recommended_platforms": ["番茄"],
        "recommended_audiences": ["男频"],
        "trend_keywords": ["升级"],
        "trend_score": 80,
        **_avoid_ctx(),
    }


def test_market_prompt_embeds_mechanism_dedup() -> None:
    prompt = conception_services._market_user_prompt(_full_ctx())
    assert "机制去重" in prompt
    assert "宗债簿之书" in prompt


def test_market_prompt_unchanged_without_avoid_mechanisms() -> None:
    ctx = _full_ctx()
    ctx.pop("avoid_mechanisms")
    assert "机制去重" not in conception_services._market_user_prompt(ctx)


def test_character_prompt_embeds_mechanism_dedup() -> None:
    prompt = conception_services._character_user_prompt(_full_ctx())
    assert "机制去重" in prompt
    assert "宗债簿——人心愿力折算为债币" in prompt


def test_finalize_prompt_embeds_mechanism_dedup() -> None:
    prompt = conception_services._finalize_user_prompt(_full_ctx(), {}, {}, {}, {})
    assert "机制去重" in prompt
    assert "宗债簿之书" in prompt


# ── attach helper + settings kill-switch ───────────────────────────────────


def _settings(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        pipeline=SimpleNamespace(enable_conception_mechanism_dedup=enabled)
    )


@pytest.mark.asyncio
async def test_attach_mechanism_dedup_populates_ctx_when_enabled() -> None:
    rows = [("宗债簿之书", "仙侠升级流", "宗门逆袭", _XIANXIA_DEBT_META)]
    ctx: dict[str, Any] = {"genre": "仙侠升级流", "sub_genre": "宗门逆袭"}
    await conception_services._attach_mechanism_dedup(
        _FakeSession(rows),  # type: ignore[arg-type]
        _settings(enabled=True),  # type: ignore[arg-type]
        ctx,
    )
    titles = [entry["title"] for entry in ctx["avoid_mechanisms"]]
    # Real recent book comes first; the action-progression cold-start cliché
    # baseline (死者归来/灭门/废材/天道漏洞) is appended to fill the avoid-list so a
    # near-empty DB still exerts anti-cliché pressure on this genre family.
    assert titles[0] == "宗债簿之书"
    assert any("死者归来" in t for t in titles)


@pytest.mark.asyncio
async def test_attach_mechanism_dedup_disabled_is_noop() -> None:
    ctx: dict[str, Any] = {"genre": "仙侠升级流", "sub_genre": "宗门逆袭"}
    await conception_services._attach_mechanism_dedup(
        _BoomSession(),  # type: ignore[arg-type]
        _settings(enabled=False),  # type: ignore[arg-type]
        ctx,
    )
    assert "avoid_mechanisms" not in ctx


@pytest.mark.asyncio
async def test_attach_mechanism_dedup_fetch_failure_is_fail_open() -> None:
    ctx: dict[str, Any] = {"genre": "仙侠升级流", "sub_genre": "宗门逆袭"}
    await conception_services._attach_mechanism_dedup(
        _BoomSession(),  # type: ignore[arg-type]
        _settings(enabled=True),  # type: ignore[arg-type]
        ctx,
    )
    # Fail-open = no crash. With the recent-book fetch failed, avoid_mechanisms
    # degrades to the cold-start cliché baseline (never empty for a covered genre
    # family), containing only static platform-cliché entries, no real book.
    titles = [entry["title"] for entry in ctx.get("avoid_mechanisms", [])]
    assert titles
    assert all(t.startswith("（平台俗套") for t in titles)


def test_pipeline_settings_default_enables_mechanism_dedup() -> None:
    from bestseller.settings import PipelineSettings

    assert PipelineSettings().enable_conception_mechanism_dedup is True


# ── echo screen: the avoid-list must not become a mood board ───────────────
#
# Live verification showed the failure mode: with the avoid-list injected, the
# finalize LLM *absorbed* the forbidden vocabulary as material — one book
# opened its premise with a verbatim copy of an old book's opening ("被宣判灵
# 根枯竭的…杂役") and named its golden finger after an old ledger mechanism.
# The deterministic echo screen catches surface-level reuse so the pipeline
# can retry finalize once with the specific collisions named.


_JIWOKAIZONG_ENTRY = {
    "title": "祭我开宗",
    "golden_finger": "宗债簿——一块纪尘亲手触碎的无名界碑所化的因果律异数。不显示属性面板，无系统提示音",
    "premise": "被宣判灵根枯竭的底层杂役纪尘，于青云宗长老当面判他“永不入门”的同一刻，意外触碎一块无名界碑，觉醒上古宗运残契——他能看清每一笔宗门刻意隐瞒的因果旧账",
    "trope_keywords": ["升级", "宗门经营", "高概念修仙", "因果债约"],
}

# Book B's real (bad) finalize output: verbatim premise opening + 旧账 imagery.
_ECHOING_CANDIDATE = {
    "title": "清浊之上",
    "premise": "被宣判灵根枯竭的杂役裴衍舟，看清清浊宗千年欺上瞒下的旧账——每一个被宗门赖掉抚恤的弟子都化作暗债，主角以一己之身为钥匙勾销旧账。",
    "writing_profile": {
        "character": {"golden_finger": "《衍算旧账》——推演功法，将压迫规则反向解析为底层债目与可被勾销的因果缝合缝。"},
        "market": {"trope_keywords": ["宗门经营", "因果业力", "升级流"]},
    },
}

_CLEAN_CANDIDATE = {
    "title": "焰骨行商",
    "premise": "边荒行商之子沿废弃商路收集失传火种，每点燃一座荒驿便唤醒一段被封存的迁徙史，火种映出的旧日亡魂会向他兑换一次护送。",
    "writing_profile": {
        "character": {"golden_finger": "焰骨罗盘——以自身骨温为燃料指向最近的失落火种，透支则暂时失明。"},
        "market": {"trope_keywords": ["行商流", "遗迹探索", "火种复国"]},
    },
}


def test_echo_report_flags_verbatim_span_and_shared_imagery() -> None:
    report = conception_services._mechanism_echo_report(
        _ECHOING_CANDIDATE, [_JIWOKAIZONG_ENTRY], genre="仙侠升级", sub_genre="宗门逆袭"
    )
    assert len(report) == 1
    hit = report[0]
    assert hit["title"] == "祭我开宗"
    # the verbatim premise opening is the smoking gun
    assert len(hit["shared_span"]) >= 5
    assert "灵根枯竭" in hit["shared_span"]


def test_echo_report_passes_genuinely_new_mechanism() -> None:
    report = conception_services._mechanism_echo_report(
        _CLEAN_CANDIDATE, [_JIWOKAIZONG_ENTRY], genre="仙侠升级", sub_genre="宗门逆袭"
    )
    assert report == []


def test_echo_report_noop_without_entries_or_junk() -> None:
    assert conception_services._mechanism_echo_report(_ECHOING_CANDIDATE, [], genre="仙侠升级") == []
    assert conception_services._mechanism_echo_report({}, [_JIWOKAIZONG_ENTRY], genre="仙侠升级") == []
    assert (
        conception_services._mechanism_echo_report(
            _ECHOING_CANDIDATE, ["junk", 42], genre="仙侠升级"
        )
        == []
    )


def test_echo_report_ignores_genre_background_bigrams() -> None:
    # 6 entries all sharing 宗门经营/升级流 tropes: those bigrams are genre
    # background, not homogenization, and must not flag a candidate alone.
    entries = [
        {
            "title": f"背景书{i}",
            "golden_finger": f"独立机制{i}",
            "premise": f"完全不同的前提{i}",
            "trope_keywords": ["宗门经营", "升级流"],
        }
        for i in range(4)
    ]
    candidate = {
        "title": "新书",
        "premise": "另一个独立前提。",
        "writing_profile": {
            "character": {"golden_finger": "全新机制"},
            "market": {"trope_keywords": ["宗门经营", "升级流"]},
        },
    }
    assert (
        conception_services._mechanism_echo_report(candidate, entries, genre="仙侠升级")
        == []
    )


def test_echo_feedback_names_collisions_zh_and_en() -> None:
    report = conception_services._mechanism_echo_report(
        _ECHOING_CANDIDATE, [_JIWOKAIZONG_ENTRY], genre="仙侠升级", sub_genre="宗门逆袭"
    )
    zh = conception_services._render_mechanism_echo_feedback(report, is_en=False)
    assert "祭我开宗" in zh
    assert "灵根枯竭" in zh
    en = conception_services._render_mechanism_echo_feedback(report, is_en=True)
    assert "祭我开宗" in en
    assert conception_services._render_mechanism_echo_feedback([], is_en=False) == ""


def test_hook_candidate_seed_varies_between_runs() -> None:
    # The old seed was sha256(genre_key) — deterministic per genre, so every
    # book of a genre preset got the identical top hook (the recurring
    # mind-reading/fortune-teller hook across all xianxia books). The seed
    # must now vary per run while staying a valid int.
    a = conception_services._hook_candidate_seed("xianxia-upgrade")
    b = conception_services._hook_candidate_seed("xianxia-upgrade")
    assert isinstance(a, int) and isinstance(b, int)
    assert a != b


def test_hook_duplicate_corpus_includes_recent_mechanisms() -> None:
    ctx = {
        "description": "宗门底层杂役逆袭。",
        "premise_seed": "",
        **_avoid_ctx(),
    }
    corpus = conception_services._hook_duplicate_corpus(ctx, {"note": "hint"})
    joined = " ".join(corpus)
    assert "宗门底层杂役逆袭。" in corpus
    # recent same-genre books' mechanism text raises duplicate risk for
    # hooks that echo them (cross-book novelty pressure)
    assert "宗债簿——人心愿力折算为债币" in joined
    assert "蚀刻之页——吞噬术法残骸复刻融合" in joined
    # junk entries and blanks never crash or land in the corpus
    corpus_no_avoid = conception_services._hook_duplicate_corpus(
        {"description": "d", "avoid_mechanisms": ["junk", 3]}, None
    )
    assert corpus_no_avoid == ["d"]


def test_echo_severity_orders_retry_choice() -> None:
    bad = conception_services._mechanism_echo_report(
        _ECHOING_CANDIDATE, [_JIWOKAIZONG_ENTRY], genre="仙侠升级"
    )
    clean = conception_services._mechanism_echo_report(
        _CLEAN_CANDIDATE, [_JIWOKAIZONG_ENTRY], genre="仙侠升级"
    )
    assert conception_services._echo_severity(bad) > conception_services._echo_severity(clean)
    assert conception_services._echo_severity([]) == 0
