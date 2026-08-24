"""构思产出的冲突势力表不许被落库契约静默吃掉。

2026-08-24 真机（书9 custom-xuanhuan-1787493501）：

    conception_snapshot.writing_profile.character.conflict_forces  → 7 个
    projects.metadata.writing_profile.character.conflict_forces    → **字段整个消失**

CharacterEngineConfig 是固定字段表、pydantic 默认 extra="ignore"，于是：
- 构思 prompt 明确要过它（conception.py:3005 定义了 name/force_type/
  active_volumes/threat_description/escalation_path/relationship_to_protagonist）
- 评审判官有一条 conflict_force_review 轴专门审它
- 模型产出了 7 个（杂役班借力成风同辈／内门借力网段缁衣／坊市黑市霍帖骨…）
- 落库时一个不剩

与 title_tournament（2026-08-22，写进 writing_profile.market 被 extra=ignore
吃掉）逐字同形。这条让 world_spec 的派系只能退回「主角的当前行动单元」这类
戏剧功能位——**接线测试（拿真实项目行跑整个编译器）才抓到，helper 层的
单测全绿**：我测的是快照里的 profile，生产读的是项目行的。
"""

from bestseller.domain.project import CharacterEngineConfig

_FORCES = [
    {
        "name": "杂役班借力成风同辈",
        "force_type": "faction",
        "active_volumes": [1],
        "threat_description": "杂役班里人人靠互相借真气攒修为",
        "escalation_path": "他每还清一个同辈的借债，那人就被反噬一次",
    },
    {"name": "内门借力网段缁衣", "force_type": "faction", "active_volumes": [1, 2]},
]


def test_conflict_forces_survive_the_contract() -> None:
    cfg = CharacterEngineConfig(conflict_forces=_FORCES)
    assert len(cfg.conflict_forces) == 2
    assert cfg.conflict_forces[0]["name"] == "杂役班借力成风同辈"


def test_all_declared_subfields_survive() -> None:
    """整条记录原样保留——只留 name 等于换个地方丢材料。"""

    cfg = CharacterEngineConfig(conflict_forces=_FORCES)
    first = cfg.conflict_forces[0]
    for key in (
        "name",
        "force_type",
        "active_volumes",
        "threat_description",
        "escalation_path",
    ):
        assert key in first, key


def test_absent_means_empty_not_none() -> None:
    assert CharacterEngineConfig().conflict_forces == []


def test_malformed_entries_are_dropped_not_crashing() -> None:
    """模型偶尔吐字符串/None——不许因此炸掉整份 profile。"""

    cfg = CharacterEngineConfig(conflict_forces=["不是字典", None, _FORCES[0], 42])
    assert [f["name"] for f in cfg.conflict_forces] == ["杂役班借力成风同辈"]


def test_a_non_list_is_tolerated() -> None:
    assert CharacterEngineConfig(conflict_forces="乱七八糟").conflict_forces == []
    assert CharacterEngineConfig(conflict_forces=None).conflict_forces == []


def test_round_trip_through_dump_keeps_them() -> None:
    """落库走的是 model_dump——这一步丢了等于没修。"""

    dumped = CharacterEngineConfig(conflict_forces=_FORCES).model_dump(mode="json")
    assert len(dumped["conflict_forces"]) == 2
    assert dumped["conflict_forces"][0]["name"] == "杂役班借力成风同辈"
