# P4 正文数字与清单一致性原型 · 2026-10-28

原型已完成并提交，可提示席数、预算、补货时限和菜品轮次问题，但还不能作为门禁。已自行找到原书并补齐生产只读回读：实际采用前三章只有一条误报；历史反例四类均有提示，其中三类可确定复现、补货依赖仍需确认；fa14 正式四章零告警。

## 评测结论

| 数据 | 抽取／提示 | 逐条编辑复核 |
|---|---|---|
| 《开席之前》实际采用 V9／V3／V1 | 238 项／1 条 | 库存7斤与补货缺口3斤被当作矛盾，属误报 |
| 历史 CH1 V2（未采用） | 102 项／6 条 | 3 个独立确定问题、1 同案重复、1 误报、1 Unknown |
| fa14 正式采用四章 | 157 项／0 条 | 误报0条；告警集合为空，precision及误报率无有效分母 |

历史四类覆盖为 **4/4 有提示、3/4 确定复现**，不是盲测或已采用正文召回率。典型短例：席数“十桌”→“五十二桌”；`3600−3550=50`，却写“还差五元”；“最多一个钟头”对“还有八分钟”只能提示依赖未明；“四轮热荤”的末轮实际列素菜和汤。库存13斤与另购12斤是另一条明确误报，未用白名单消掉。

原书项目为 `d407ca85-9a9b-4564-a033-6bc529e0dd76`。生产回读确认前三章均为 promoted，正文 SHA 与既有快照完全一致；更早快照的 CH3 candidate 状态已过时。前两章普通导出落后于实际采用稿，初轮导出结果仅作对照。采用 CH1 已明确十桌、总成本3550／每桌355余5、首轮冷盘不等补货，且不再宣称四轮均为热荤；对应旧四类没有确定复现。

fa14 普通 chapter 文件也是旧稿，初轮结果已作废并保留。有效输入改用正式整书导出，与采用清单的 assembly SHA、四章来源逐项匹配；规范化分章区间和 raw draft SHA 分列。负例抽查核对了旧欠 `867−237=630`、车费增量 `35−12=23`、试机60分钟及会面40分钟。未建立全量事实金标，抽取召回和漏报未知，零告警不能证明全文一致。

判断标记为 **agent_editorial_not_human**，不冒充真人验收。原书已采用稿仅有的告警已逐条核为误报；fa14待逐条复核的告警为0项。

## 交付与验证

- 分支 `par/P4`，基线 `refs/tags/e2e17`（`419181a4`）。模块与测试提交 **`415a641a1d2f283c64dab703b7e3eb80639dda2f`**；初始报告提交 `1604fccf`。
- 模块：P4 worktree 内 `src/bestseller/services/quantity_consistency_probe.py`；测试：`tests/unit/test_quantity_consistency_probe.py`。独立模块避免引入现有数据库／知识层依赖，未注册门禁。
- 测试先RED再实现；最终 **64 passed**（原型39＋既有CheckerReport 25），Ruff、提交secret scan通过。日志 `tests-final.log`、`lint-verified.log`；失败差异和底层错误见 `FAILURES.md`。
- 实际采用回读：`promoted-readback-20261028T044052Z/sources.json` 及同目录 `checker-report.json`；历史与fa14：`eval-delivery/`；全部逐条判断：`JUDGMENTS.json`。输入SHA、版本、规范化区间均在相应manifest中，不附正文。
- 来源脚本 `read_promoted_sources.py`；离线复跑 `run_offline_eval.py`。以上证据路径相对于本报告；完整设计见 `DESIGN.md`。

未来仅建议在正文组装并冻结SHA后，与 `deterministic_post_write_audit` 并列作 **audit_only**，Export前再审计整书。与 `common_sense_gate` 时间算术、`contradiction` 数字时间线重叠，须按来源区间去重。指代、库存／采购角色、日期照应和菜品依赖可能需要模型辅助；算术、单位和来源仍由确定性代码验证。本任务没有调用模型。

生产仅三条显式SELECT：REPEATABLE READ／READ ONLY事务，statement_timeout=30s，最终rollback；正文仅驻内存，未保存凭证。未用Docker或书籍运行接口，未合并推送。代码只改P4 worktree，报告只新增于指定P4目录；未修改主工作区已有文件。各步内置盘均高于5 GB，交付时约12.2 GiB。
