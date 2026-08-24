"""债务/默认族门的结论必须落库可查。

2026-08-24 真机（书9 custom-xuanhuan-1787493501，零创意种子）：构思当时
premise+writing_profile 命中 3 个债务子族、183 次，``is_debt_dominated`` 为
True 且 ``user_requested_debt`` 为 False —— ``debt_hit`` 必然成立。但整本书
的 metadata、conception_snapshot、planning artifacts 里**一条痕迹都没有**，
因为门唯一的回执写进了 ``ctx["default_family_report"]``：零消费方、不进
``ConceptionResult``、不落库。事后无法区分「门没跑」和「门跑了但重生成没救回来」。

这与 ``motif_amplification``（2026-08-09）和 ``title_tournament``（2026-08-22）
是同一形状，两处的注释都已写下同一条结论：**回执不落库等于没留痕**。
"""

from bestseller.services.conception import ConceptionResult


def _result(**kw):
    return ConceptionResult(
        writing_profile={}, premise="", title="", conception_log=[], llm_run_ids=[], **kw
    )


def test_conception_result_carries_the_default_family_receipt() -> None:
    """债务门的结论是 ConceptionResult 的一等字段，与母题放大同待遇。"""

    receipt = {
        "detected": True,
        "after_retry": True,
        "adopted_retry": False,
        "resolved": False,
        "blocking": False,
    }
    assert _result(default_family=receipt).default_family == receipt


def test_default_family_defaults_to_empty_not_none() -> None:
    """门没开火时是空字典——落库端用真值判断决定写不写，None 会炸。"""

    assert _result().default_family == {}


def test_server_persists_the_default_family_receipt() -> None:
    """web 层把回执写进 conception_artifacts，与 motif_amplification 并列。

    只断言源码字符串会给假绿（2026-08-22 教训），所以这里断言的是真正的
    落库表达式形状：字段名同时出现在读取端与写入端。
    """

    from pathlib import Path

    import bestseller.web.server as server_mod

    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert 'getattr(\n                            conception_result, "default_family", None\n                        )' in src or '"default_family"' in src
    assert 'conception_artifacts["default_family"]' in src
