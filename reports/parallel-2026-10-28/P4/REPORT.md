# P4 正文数字与清单一致性原型 · 2026-10-28

原型已完成并独立提交，可以提示席数、预算、补货时限和菜品轮次问题，但还不能作为门禁。历史反例四类均有提示，其中三类可确定复现，补货是否影响首轮仍需确认；fa14 正式四章没有告警。库存与追加采购被误认成同一数值，是本次明确保留的误报。

## 结果与证据边界

| 数据 | 观察结果 | 编辑复核 |
|---|---|---|
| 《开席之前》保留导出前三章 | 205 项抽取，3 条提示 | 1 真问题（热荤末轮列素菜／汤）、1 误报（库存与增购）、1 Unknown（补货依赖） |
| 历史 CH1 V2 反例 | 102 项抽取，6 条提示；四类覆盖 4/4 | 3 个独立确定问题、1 条同案重复、1 误报、1 Unknown；确定复现 3/4 |
| fa14 正式采用四章 | 157 项抽取，0 条提示 | 误报 0 条；待逐条判断的告警为 0 项。precision／误报率无有效分母，不能写成“0%误报率” |

典型短例：席数“十桌”→“五十二桌”；总额 `3600−3550=50`，文本写“还差五元”；“最多一个钟头”对“还有八分钟”只提示依赖未明；“四轮热荤”的末轮列包菜、娃娃菜和汤。每桌余 5 元与总额余 50 元分开处理。库存 13 斤／另购 12 斤属于不同事实，不能判为矛盾。

**来源限制：**四类反例的旧记录明确来自未采用的 V2，不能将 4/4 宣称为已采用正文召回率或独立盲测成绩；规则是在已知问题上开发的。原书三章使用与保全指纹一致的本地导出，未访问数据库核验当前采用状态；更早快照中 CH3 仍为 candidate，因此当前生产采用身份保持 Unknown。席数和余额在保留导出中已经修正。fa14 普通 chapter 文件是旧版，首轮评测已作废并留存；有效评测改用正式整书导出，正文 assembly SHA `7b82244210cceabf5968beaa012aa2c4b220c309a30aed0a68a8973b50c08198` 与采用清单一致，四章来源均为 promoted。规范化章节区间与 raw draft SHA 分列，未混用。

本会话作逐条编辑复核，标记 **agent_editorial_not_human**，不冒充真人验收。fa14 负例抽查还核对了旧欠 `867−237=630`、车费增量 `35−12=23`、试机 60 分钟与会面 40 分钟。未建立全量事实金标，抽取召回及漏报未知；零告警不能证明全文一致。

## 交付、验证与接入设计

- 分支：`par/P4`；基线：`refs/tags/e2e17` → `419181a41f29363263cee8a12d1694ace0bcef24`。
- **模块与测试提交：`415a641a1d2f283c64dab703b7e3eb80639dda2f`**。独立模块隔离现有数据库／知识层依赖，仅导入 stdlib 与 CheckerReport；不注册门禁。代码：`scratch/par-worktrees/P4/src/bestseller/services/quantity_consistency_probe.py`；测试同 worktree 的 `tests/unit/test_quantity_consistency_probe.py`。
- 测试先 RED 再实现；最终 **64 passed**（原型 39＋既有 CheckerReport 25），Ruff 与提交 secret scan 通过。证据：`tests-final.log`、`lint-verified.log`；失败底层错误和差异见 `FAILURES.md`。
- 有效结果：`eval-delivery/evaluation-summary.json`、`eval-delivery/input-manifest.json` 及同目录三个 CheckerReport JSON；逐条判断：`JUDGMENTS.json`；复跑脚本：`run_offline_eval.py`。所有路径均相对于本报告目录，代码路径相对于主仓库。
- 将来仅考虑在正文组装并冻结 SHA 后，与 `deterministic_post_write_audit` 并列运行 audit_only，Export 前再作整书审计。与 `common_sense_gate` 的时间算术及 `contradiction` 的数字时间线有交集，须按来源区间去重。详见 `DESIGN.md`。
- 模型仅可能辅助指代、库存／采购角色、日期照应和菜品依赖；加减乘、单位及引用 SHA 仍须确定性复核。此任务没有调用模型。

全过程未用 Docker、未访问数据库或书籍运行接口、未合并推送；代码只在 P4 worktree。内置盘各步均高于 5 GB，交付前约 12.2 GiB。只新增本任务报告目录与隔离 worktree，未修改主工作区已有文件。
