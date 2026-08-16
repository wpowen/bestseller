"""爽点覆盖率的**双档地板选择**——阈值必须按声明的题材分组，单档会漏判。

标定自 .distillation_private（按书统计「有可读出爽点结算的章占比」）：

    爽文向 216 本   p10 = 0.29   中位 0.60
    文学/译作 199 本 p10 = 0.10   中位 0.25
    全语料          p10 = 0.14   中位 0.42

单档（全语料 p10=0.14）**测不出我们的书**：实测覆盖率 0.16，不触发；
而三档读者独立盲评都判它「50 章零爽点」。用含大量文学与译作的分布去
要求一本自称爽文的书，必然过松。故爽文向另立 0.29 档。

这里锁的是**选档链路**，不是阈值数值本身：声明字段 → _decl 拼接 →
标记命中 → 地板选择。这条链断过一次同形的（爽点配方只挂在旧 preset 上、
盖戳只活在场景装配路径上），断了以后表现是「审计安静」，
而「从不报警」和「没问题」长得一模一样。
"""

from __future__ import annotations

import pytest

from bestseller.services.audit_loop import PleasureDistributionAudit


class _FakeProject:
    """只带选档需要的三个字段——与 audit_loop 读的是同一组属性名。

    ``metadata_json`` 是 Python 属性名，映射到 DB 里叫 ``metadata`` 的列
    （SQLAlchemy 上 ``metadata`` 是保留名）。写这个测试时先怀疑过它拼错，
    实际是对的；留此注释免得下次再查一遍。
    """

    def __init__(self, genre="", sub_genre="", tags=None):
        self.genre = genre
        self.sub_genre = sub_genre
        self.metadata_json = {"tags": list(tags or [])}


def _floor_for(project) -> float:
    """与 audit_loop.py 选档段同源的判定。"""

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
    is_shuangwen = any(m in decl for m in audit.shuangwen_markers)
    return audit.coverage_floor_shuangwen if is_shuangwen else audit.coverage_floor


def test_two_tiers_are_distinct_and_ordered():
    """爽文档必须严格高于全语料档，否则分档没有意义。"""

    assert PleasureDistributionAudit.coverage_floor == pytest.approx(0.14)
    assert PleasureDistributionAudit.coverage_floor_shuangwen == pytest.approx(0.29)
    assert (
        PleasureDistributionAudit.coverage_floor_shuangwen
        > PleasureDistributionAudit.coverage_floor
    )


@pytest.mark.parametrize(
    "field, value",
    [
        ("tags", ["打脸"]),
        ("tags", ["无脑爽", "沙雕"]),
        ("tags", ["废柴逆袭", "成长升级"]),
        ("genre", "都市爽文"),
        ("sub_genre", "扮猪吃虎"),
    ],
)
def test_declared_shuangwen_takes_the_higher_floor(field, value):
    """三个声明字段任一命中标记都要抬到爽文档——标签是最常见的入口。"""

    project = _FakeProject(**{field: value})
    assert _floor_for(project) == pytest.approx(0.29)


def test_tags_reach_the_selector_through_metadata_json():
    """标签必须真的能走到选档逻辑里。

    这是链路上最容易断的一环：属性名写错时 ``getattr`` 静默返回 None，
    标签段变成空串，爽文档**永远选不上**，而审计只是安静下来。
    真机三本书（搞笑沙雕/澡堂/东方玄幻）此处实测分别命中
    ['打脸'] / ['爽文','打脸','爽'] / ['逆袭']，全部走 0.29。
    """

    only_in_tags = _FakeProject(genre="轻小说", sub_genre="搞笑沙雕", tags=["打脸"])
    assert "打脸" not in f"{only_in_tags.genre}{only_in_tags.sub_genre}"
    assert _floor_for(only_in_tags) == pytest.approx(0.29)


def test_non_shuangwen_book_keeps_the_corpus_wide_floor():
    """不自称爽文的书不按爽文量——否则文学向会被系统性误判。"""

    literary = _FakeProject(
        genre="现实主义", sub_genre="家族史", tags=["群像", "年代", "乡土"]
    )
    assert _floor_for(literary) == pytest.approx(0.14)


def test_bare_char_marker_is_a_known_precision_risk():
    """单字标记「爽」是**已知**的精度风险，此处只做留痕不做拦截。

    本项目被裸字词表坑过：裸字「门」把内门/门槛/门帘全计为钩子，
    91% 的钩子是幻影。「爽」同样会吃到「清爽」「凉爽」。

    但这里**故意不改**：①它只扫 genre/sub_genre/tags 这三个短声明字段，
    不扫正文，量级完全不同；②误判后果是多一条 audit_only 建议，不夺权；
    ③目前零误伤证据。按「门禁误杀」那条教训，没有证据不预先改门。
    这个测试把风险钉在这里——真出现误伤时，是它先变红。
    """

    breezy = _FakeProject(genre="治愈日常", tags=["清爽", "夏日"])
    assert _floor_for(breezy) == pytest.approx(0.29), (
        "裸字「爽」吃到了「清爽」。若此处开始造成真实误伤，"
        "应当收紧标记表而不是放宽地板。"
    )
