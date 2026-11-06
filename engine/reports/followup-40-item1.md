# 第 40 条第 1 项：分离能力与普通手段

Evidence：新增保存数据夹具 `tests/fixtures/ability_methods40.json`，取自新的系统临时副本，包含实际解法引文与圣经能力声明。RED：新增 3 项测试全部按预期失败；GREEN：本项及 plot_checkup39、chapter_rhythm39、core_ability39、q1_plan 共 47 项通过。

修复覆盖规划上下文、规划校验、体检三处：圣经 name/object、显式子能力列表及“能力：……”声明与普通手段分开。普通手段阶段上限 3；能力按“对象原词 + 能力组合”阶段上限 2。无对象不伪造重复；阶段独立计数。trial-50 普通手段“蛮力”11 次转入能力使用分布，旧的两组普通手段超额消失。

Inference：归一化能力名称和对象原词仍是有限词面判定，未登记的同义词和对象类别无法作语义合并；新的能力组合超额是候选提示，不等于读者判定。authority=agent_editorial_not_human。

Unknown：测试和只读重算不证明已改善正文，未重写旧书。
