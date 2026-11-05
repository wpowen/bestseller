"""Local draft regressions: standard library helpers, no model or network calls."""

from copy import deepcopy


import json


from pathlib import Path


import tempfile


import unittest


import yaml


from bookengine.contracts import (
    dashboard_guard,
    power_merge,
    valid_extract,
    observations,
    reduce_growth,
    payoff_ok,
    growth_warnings,
    choose_contract,
)


HOOKS = ["隐瞒", "危机", "选择", "旧事反转", "关系变化"]


MENU = ["升级", "打脸", "收获", "身份抬升", "碾压"]


PACK = {
    "ladder": {
        "strength": ["凡人", "入门", "通脉", "凝元", "化境", "宗师"],
        "wealth_unit": "灵石",
        "status": ["无名", "同门", "执事"],
    },
    "genre_kind": "爽文",
    "payoff_menu": MENU,
    "hook_types": HOOKS,
    "solution_menu": ["抢先夺物", "诱敌离位", "切断退路"],
}


DATA = {"packs": {"xuanhuan-power-fantasy": PACK}}


class ContractsTests(unittest.TestCase):
    pass


class SchedulingTests(unittest.TestCase):
    def canon(self, seed):
        data = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/growth_payoff.yaml").read_text())
        return dict(
            deepcopy(PACK),
            seed=seed,
            scheduling=data.get("scheduling", {}),
            ladder=dict(PACK["ladder"], strength=[f"第{n}阶" for n in range(61)]),
        )

    def plan(self, canon, count=120, actual=False, start=None):
        rows = deepcopy(start or [])
        for n in range(len(rows) + 1, count + 1):
            row = choose_contract(canon, rows, n)
            if actual:
                row["payoff"] = dict(type=row["payoff_type"], fulfilled=True)
            row["reserved_payoff_type"] = row["payoff_type"]
            rows.append(row)
        return rows
