"""默认族在**展开那一步**出生——过滤点却在它出生之前。

2026-08-24 真机（书9 custom-xuanhuan-1787493501，零创意种子）逐层量：

    一句话胚子   55字   子族0  命中  0  → 沉底门放行（母题此时还不存在）
    展开后卡片 1247字   子族3  命中 39  → 已经支配
    构思终稿   8861字   子族3  命中183  → advisory，只挣一次重生

冠军胚子写的是「借力/还力」——零个债务词。是**展开**把「还力」翻译成了
账本/债主/还债。胚子层的沉底判据结构上不可能抓到它。

卡片层曾有一道硬门，2026-08-02 因误杀退役，理由正确：「选了仙侠不等于
禁止出现葬礼」。所以这里恢复的**不是杀权**，是本仓库验证过的安全形状：
同族卡片把展开位让给干净卡片，**绝不清空池**（全池同族时照常放行）。
"""

from bestseller.services.concept_tournament import (
    ConceptTournamentResult,
    _demote_default_family_cards,
)

_DIRTY = {
    "one_liner": "他手里那本旧账本自己翻页，还清一笔债，下一个债主就更强",
    "golden_finger": "替人还力——账本上欠谁的真气，一掌拍回原主",
    "story_motion": "每还清一笔账，讨账的人就换一个更狠的",
}
_CLEAN = {
    "one_liner": "他能听见铁器还记得自己被锻打时的形状",
    "golden_finger": "唤醒金属的记忆，让断刃复原成锻造那天的样子",
    "story_motion": "每唤醒一件兵器，它前主人的死法就在他手上重演一次",
}


def _run(approved, kernels, seed=""):
    result = ConceptTournamentResult()
    kept = _demote_default_family_cards(
        approved, prebuilt_kernels=kernels, seed_concept=seed, result=result
    )
    return kept, result


def test_dominated_card_yields_its_slot_to_a_clean_one() -> None:
    approved = [("A", "s1"), ("B", "s2")]
    kernels = {("A", "s1"): _DIRTY, ("B", "s2"): _CLEAN}
    kept, result = _run(approved, kernels)
    assert kept == [("B", "s2")]
    assert any(
        r.get("failed_axes") == ["default_family_after_expansion"]
        for r in result.engine_rejections
    )


def test_pool_is_never_emptied() -> None:
    """全池同族时照常放行——清空池比留下同族更坏（2026-08-06 定案）。"""

    approved = [("A", "s1"), ("B", "s2")]
    kernels = {("A", "s1"): _DIRTY, ("B", "s2"): dict(_DIRTY)}
    kept, result = _run(approved, kernels)
    assert kept == approved
    assert not result.engine_rejections


def test_user_named_family_is_never_demoted() -> None:
    """用户自己写了债务题材=用户的选择，框架不许替他改主意。"""

    approved = [("A", "s1"), ("B", "s2")]
    kernels = {("A", "s1"): _DIRTY, ("B", "s2"): _CLEAN}
    kept, _ = _run(approved, kernels, seed="我要写一本讨债人替亡母清算旧账的书")
    assert kept == approved


def test_unknown_kernel_is_treated_as_clean() -> None:
    """没展开过的卡片读不到内核——未知不等于有罪，不许凭空降权。"""

    approved = [("A", "s1"), ("B", "s2")]
    kernels = {("A", "s1"): _DIRTY}
    kept, _ = _run(approved, kernels)
    assert kept == [("B", "s2")]


def test_all_clean_pool_is_untouched_and_leaves_no_noise() -> None:
    approved = [("A", "s1"), ("B", "s2")]
    kernels = {("A", "s1"): _CLEAN, ("B", "s2"): dict(_CLEAN)}
    kept, result = _run(approved, kernels)
    assert kept == approved
    assert not result.engine_rejections


def test_real_book9_champion_card_is_caught_here() -> None:
    """真机对照：书9 的冠军卡在这一层必须被判为同族（胚子层量的是 0）。"""

    from bestseller.services.anti_default_motif import is_debt_dominated

    book9_card = {
        "one_liner": "江湖上人人练的是『借力』——向他人借来一缕真气化为己用，"
        "唯独这个新手练的是『还力』：别人借走了，他替人还回去。",
        "abnormality": "账本上欠谁的真气，他一掌拍去就能让真气当场飞回原主",
        "protagonist": "亡父留下的少年账房学徒陆折，手里攥一本无人想接的旧账本",
        "story_motion": "他每还清一笔账，账本就自己翻开下一页，"
        "新一页的债越来越重，债主级别也越来越高",
    }
    assert not is_debt_dominated(book9_card["one_liner"])  # 胚子层：干净
    approved = [("A", "s1"), ("B", "s2")]
    kept, _ = _run(approved, {("A", "s1"): book9_card, ("B", "s2"): _CLEAN})
    assert kept == [("B", "s2")]
