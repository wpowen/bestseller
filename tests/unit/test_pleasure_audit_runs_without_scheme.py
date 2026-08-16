"""爽点分布审计不得因为「没有 hype_scheme」整个 no-op（2026-08-16 定罪）。

真机现象：三本书 84% 的章没有爽点、连续无爽点章远超阈值（max_consecutive_gaps=3），
`PleasureDistributionAudit` 却**一次都没报过**。

根因：守卫开得太宽——

    scheme = invariants.hype_scheme ...
    if scheme is None or scheme.is_empty:
        return []          # ← 整个审计 no-op

而 taxonomy 建的书 scheme 恒空（见 `_synthesized_hype_block` 的修复），
于是这条审计对**当前默认建书路径上的每一本书**都从未工作。

实际只有喜剧密度检查需要 scheme（读 `comedic_beat_density_target`）：
* `PLEASURE_HYPE_GAP` —— 判据是「分类器返回 None 且落库 hype_type 为空」，纯文本
* `PLEASURE_HYPE_HOGS_ENDING` —— 判据是尾段分类 vs 全文分类，纯文本

兄弟审计 `SetupPayoffTrackerAudit` 正是特意做成不依赖 scheme 的
（见 `build_full_audit` 的 docstring），这里对齐它。

⚠️ 这是本轮反复出现的同一元病的第三次发作：**能力存在，但被一道过宽的守卫
挡在我们的书之外**（前两次：爽点配方拿不到、爽点盖戳只活在场景装配路径）。
"""

from __future__ import annotations

import inspect

from bestseller.services.audit_loop import PleasureDistributionAudit


def _scan_source() -> str:
    return inspect.getsource(PleasureDistributionAudit.scan)


def test_scan_does_not_bail_out_on_empty_scheme() -> None:
    """空 scheme 不得导致整个 scan 提前返回。"""

    src = _scan_source()
    assert "scheme_available" in src, "应把 scheme 缺席降级为局部开关，而不是整体 no-op"
    # 旧的整体短路形状不得复活
    assert "if scheme is None or scheme.is_empty:\n            return []" not in src, (
        "整体 no-op 守卫复活了——taxonomy 路径的书会再次全程不被审计"
    )


def test_comedic_check_is_the_only_scheme_dependent_branch() -> None:
    """scheme 只应服务喜剧密度检查；gap 与 hogs 必须与它解耦。"""

    src = _scan_source()
    # scheme 的实际读取只有 comedic_beat_density_target 一处
    assert src.count("scheme.comedic_beat_density_target") == 1
    assert "scheme_available and total_chapters" in src, (
        "喜剧密度检查必须自带 scheme_available 守卫"
    )


def test_gap_and_hogs_codes_still_declared() -> None:
    """两条纯文本判据的 code 仍在（防止修复时误删）。"""

    assert PleasureDistributionAudit.code_gap == "PLEASURE_HYPE_GAP"
    assert PleasureDistributionAudit.code_hogs_ending == "PLEASURE_HYPE_HOGS_ENDING"


# ── 爽点覆盖率告警带（2026-08-16 新增）────────────────────────────────────


def test_coverage_thresholds_are_corpus_calibrated() -> None:
    """阈值必须来自人类语料标定，且**按题材层分组**——不是拍脑袋，也不是单一档。

    2026-08-16 更正：原来的「爽文档 0.29 / 全语料 0.14」双档已退役。
    它按爽文标签分组，但这个指标的读数取决于确定性分类器在该题材上的响应，
    而那个响应差 3 倍（玄幻章命中 73.2%、市井/现实章 24.1%，n=425/79）。
    拿玄幻占多数的样本标出的 0.29 去要求市井书，是在要求它高于该题材人类中位。

    新基线（272 本人类书按书统计覆盖率，取所在层 p10）：
        玄幻向      175 本  p10=0.23  中位=0.42
        市井/现实向   60 本  p10=0.17  中位=0.31
        混合/其它     37 本  p10=0.00  中位=0.38  ← 分层不明退回全语料 p10
    """

    floors = PleasureDistributionAudit.coverage_floor_by_stratum
    assert floors["xuanhuan"] == 0.23
    assert floors["market"] == 0.17
    assert PleasureDistributionAudit.coverage_floor == 0.14
    assert floors["xuanhuan"] > floors["market"] > PleasureDistributionAudit.coverage_floor, (
        "尺子响应越强的题材，门槛必须越高"
    )


def test_retired_single_and_shuangwen_tiers_are_gone() -> None:
    """退役的方案必须真的删掉——两套并存就是「同一事实住两地」。"""

    assert not hasattr(PleasureDistributionAudit, "coverage_floor_shuangwen")
    assert not hasattr(PleasureDistributionAudit, "shuangwen_markers")


def test_both_known_bad_books_still_caught() -> None:
    """换参照系不能放走已知的坏书。

    端盘画神（东方玄幻）实测覆盖 0.16 < 玄幻 p10 0.23；
    市井验证书实测 0.16 < 市井 p10 0.17。两本都仍然报得出。
    """

    ours = 0.16
    floors = PleasureDistributionAudit.coverage_floor_by_stratum
    assert ours < floors["xuanhuan"]
    assert ours < floors["market"]


def test_stratum_markers_use_genre_vocabulary_not_hype_vocabulary() -> None:
    """题材层标记只用来选**参照系**，不该混进爽点词。

    旧的 shuangwen_markers 里有「打脸」「逆袭」「碾压」——那些是爽点词汇，
    既做过判定标记又出现在别处，正是「同一个词同时承担两种职责」的老病。
    新标记只描述题材。
    """

    audit = PleasureDistributionAudit
    all_markers = set(audit.stratum_markers_xuanhuan) | set(audit.stratum_markers_market)
    for hype_word in ("打脸", "逆袭", "碾压", "扮猪吃虎", "无脑爽"):
        assert hype_word not in all_markers, f"爽点词混进了题材标记：{hype_word}"
    for genre_word in ("玄幻", "仙侠", "修真", "市井", "都市", "沙雕"):
        assert genre_word in all_markers, f"缺题材词：{genre_word}"


def test_coverage_check_is_warn_not_block() -> None:
    """无杀权铁律：最强的爽文单指标 AUC 也只有 0.72。"""

    import inspect

    src = inspect.getsource(PleasureDistributionAudit.scan)
    idx = src.find("code_coverage")
    assert idx > 0
    window = src[idx : idx + 300]
    assert 'severity="warn"' in window
    assert "critical" not in window
