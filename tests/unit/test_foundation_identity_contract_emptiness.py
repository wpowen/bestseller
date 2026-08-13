"""身份契约门必须在 cast_spec 当场拦住空名册，而不是空真放行。

真机 2026-08-08（deai-verify-20260808）：foundation 阶段报
``CastSpec produced an empty identity manifest``（ensure_project_identity_manifest），
但那已经是真因之后好几步了。``validate_foundation_identity_contract`` 只校验
「存在的条目」的身份锁——条目为空时天然全过：

    >>> validate_foundation_identity_contract({"protagonist": None, "supporting_cast": []})
    NarrativeContractReport(violations=(), warnings=())

于是空壳一路通过，报错位置与真因差好几步、整本书失败。门必须自己判非空。
"""

from __future__ import annotations

from typing import Any

from bestseller.services.narrative_contracts import (
    validate_foundation_identity_contract,
)


def _character(name: str, *, gender: str = "female") -> dict[str, Any]:
    pronouns = ("她/她的", "she/her") if gender == "female" else ("他/他的", "he/him")
    return {
        "name": name,
        "gender": gender,
        "pronoun_set_zh": pronouns[0],
        "pronoun_set_en": pronouns[1],
    }


def _codes(content: dict[str, Any] | None) -> set[str]:
    report = validate_foundation_identity_contract(content)
    return {violation.code for violation in report.blocking_violations}


def test_missing_protagonist_blocks() -> None:
    """只有配角、没有主角 —— 旧实现零违规放行。"""

    content = {
        "protagonist": None,
        "antagonist": _character("赵峰", gender="male"),
        "supporting_cast": [_character("林小雨")],
    }

    assert "FOUNDATION_CAST_PROTAGONIST_MISSING" in _codes(content)


def test_nameless_protagonist_blocks() -> None:
    content = {
        "protagonist": {"name": "   ", "gender": "female"},
        "supporting_cast": [_character("林小雨")],
    }

    assert "FOUNDATION_CAST_PROTAGONIST_MISSING" in _codes(content)


def test_empty_supporting_cast_warns_but_never_blocks() -> None:
    """名册空 = warning，绝不 block（2026-08-09 当天降级）。

    上线当天真机误杀：《废脉炉子天天骂我》(custom-xianxia-1786282198) 的
    cast_spec 由 source-bound 编译器确定性产出，supporting_cast=[] 是该路径的
    正常形态（配角活在 premise/画像里），重试永远撞同一堵墙直到书死。反例在案：
    2026-08-08 的烂账书同形 cast_spec 写完了 50 章。该检查针对的空壳案
    (deai-verify-20260808) 连主角都没有——上面的主角检查已经拦住它。
    """

    content = {
        "protagonist": _character("林晚秋"),
        "antagonist": _character("赵峰", gender="male"),
        "supporting_cast": [],
    }

    report = validate_foundation_identity_contract(content)
    assert report.passed, "空名册不得阻断建书"
    assert report.blocking_violations == ()
    assert "FOUNDATION_CAST_ROSTER_EMPTY" in {w.code for w in report.warnings}


def test_populated_cast_passes() -> None:
    """修复不许收紧到误伤：正常 cast 依旧零阻断。"""

    content = {
        "protagonist": _character("林晚秋"),
        "antagonist": _character("赵峰", gender="male"),
        "supporting_cast": [_character("林小雨"), _character("陈师傅", gender="male")],
    }

    report = validate_foundation_identity_contract(content)
    assert report.blocking_violations == ()
    assert report.passed


def test_absent_cast_spec_stays_clean() -> None:
    """「本次没有提供 cast_spec」不等于「cast_spec 是空的」。"""

    report = validate_foundation_identity_contract(None)
    assert report.passed
    assert report.violations == ()
