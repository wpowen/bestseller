"""爽点覆盖率的**题材层**阈值——阈值必须和尺子的响应同一个参照系。

2026-08-16 定罪：确定性分类器 `classify_hype` 的 94 个中文词重度玄幻偏向
（突破/晋阶/真身/金光/神识/机缘/认主/碾压/镇压…）。同一批人类出版章分层实测：

    玄幻向      n=425   命中率 73.2%
    市井/现实向  n= 79   命中率 24.1%      ← 3 倍差距

差的是尺子不是书。真机佐证：一本市井喜剧第 1 章写了教科书式的三段律结算
（有名字的对手被当众「手滑」少打了肉、第三方在场、主角全程没出手），
分类器返回 None——那句话里没有一个字在词表上。

因此**旧的「爽文档 0.29 / 全语料 0.14」双档退役**：它按爽文标签分组，
而这个指标的读数取决于尺子在该题材上的响应，必须按题材层分组。
两套并存就是「同一事实住两地」——本项目反复定罪的元病。

新基线（272 本人类书按书统计覆盖率，取所在层 p10）：

    层            本数   p10    中位
    玄幻向         175   0.23   0.42
    市井/现实向     60   0.17   0.31
    混合/其它       37   0.00   0.38   ← 分层不明退回全语料 p10 0.14

⚠️ 分层只是**缓解**不是根治：词表尺子读不懂市井爽点。根治要靠语义判官
（reader_judge 的 payoff_density 已换成读者三段律 v1.2）。这一点写进
finding 文案里，免得读报告的人把市井书的低读数当成书的问题。
"""

from __future__ import annotations

import pytest

from bestseller.services.audit_loop import PleasureDistributionAudit


class _FakeProject:
    """只带选参照系需要的三个字段——与 audit_loop 读的是同一组属性名。

    ``metadata_json`` 是 Python 属性名，映射到 DB 里叫 ``metadata`` 的列
    （SQLAlchemy 上 ``metadata`` 是保留名）。写这个测试时先怀疑过它拼错，
    实际是对的；留此注释免得下次再查一遍。
    """

    def __init__(self, genre="", sub_genre="", tags=None):
        self.genre = genre
        self.sub_genre = sub_genre
        self.metadata_json = {"tags": list(tags or [])}


def _floor_for(project) -> float:
    """与 audit_loop 选参照系段同源的判定。"""

    audit = PleasureDistributionAudit
    decl = " ".join(
        str(x or "")
        for x in (
            getattr(project, "genre", ""),
            getattr(project, "sub_genre", ""),
            " ".join((project.metadata_json or {}).get("tags") or [])
            if getattr(project, "metadata_json", None)
            else "",
        )
    )
    if any(m in decl for m in audit.stratum_markers_xuanhuan):
        stratum = "xuanhuan"
    elif any(m in decl for m in audit.stratum_markers_market):
        stratum = "market"
    else:
        stratum = "unknown"
    return audit.coverage_floor_by_stratum.get(stratum, audit.coverage_floor)


def test_retired_shuangwen_tier_is_gone():
    """旧双档必须真的删掉，不能留着和新方案并存。"""

    assert not hasattr(PleasureDistributionAudit, "coverage_floor_shuangwen")
    assert not hasattr(PleasureDistributionAudit, "shuangwen_markers")


def test_strata_thresholds_match_corpus_calibration():
    audit = PleasureDistributionAudit
    assert audit.coverage_floor_by_stratum["xuanhuan"] == pytest.approx(0.23)
    assert audit.coverage_floor_by_stratum["market"] == pytest.approx(0.17)
    assert audit.coverage_floor == pytest.approx(0.14)
    # 玄幻的尺子响应更强，门槛必须更高——反过来就是拿市井的读数要求玄幻，过松。
    assert (
        audit.coverage_floor_by_stratum["xuanhuan"]
        > audit.coverage_floor_by_stratum["market"]
        > audit.coverage_floor
    )


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("genre", "东方玄幻", 0.23),
        ("sub_genre", "修真", 0.23),
        ("tags", ["宗门", "剑修"], 0.23),
        ("genre", "搞笑沙雕", 0.17),
        ("genre", "都市日常", 0.17),
        ("tags", ["市井", "职场"], 0.17),
        ("genre", "悬疑推理", 0.14),
        ("tags", [], 0.14),
    ],
)
def test_stratum_picks_its_own_reference(field, value, expected):
    project = _FakeProject(**{field: value})
    assert _floor_for(project) == pytest.approx(expected)


def test_xuanhuan_wins_when_both_strata_match():
    """同时命中两层按玄幻算——用低的那条会过松。"""

    both = _FakeProject(genre="都市修真", tags=["市井", "宗门"])
    assert _floor_for(both) == pytest.approx(0.23)


def test_tags_reach_the_selector_through_metadata_json():
    """标签必须真的能走到选参照系的逻辑里。

    这是链路上最容易断的一环：属性名写错时 ``getattr`` 静默返回 None，
    标签段变成空串，永远落到兜底档，而审计只是安静下来。
    """

    only_in_tags = _FakeProject(genre="轻小说", sub_genre="", tags=["宗门"])
    assert "宗门" not in f"{only_in_tags.genre}{only_in_tags.sub_genre}"
    assert _floor_for(only_in_tags) == pytest.approx(0.23)


def test_both_known_bad_books_still_caught():
    """退役旧双档不能放走已知的坏书。

    端盘画神（东方玄幻）实测覆盖 0.16；市井验证书实测 0.16。
    """

    assert 0.16 < _floor_for(_FakeProject(genre="东方玄幻"))
    assert 0.16 < _floor_for(_FakeProject(genre="搞笑沙雕", tags=["市井"]))


def test_market_floor_stays_below_that_genre_human_median():
    """告警带不是及格线：不能要求一本市井书高于同题材人类中位（0.31）。"""

    assert PleasureDistributionAudit.coverage_floor_by_stratum["market"] < 0.31
    assert PleasureDistributionAudit.coverage_floor_by_stratum["xuanhuan"] < 0.42
