# P4 尝试与差异记录

- worktree 创建：exit 255，EPERM / Operation not permitted，不能创建 par/P4.lock；环境授权后第二次成功。
- 基础 RED：exit 2，ModuleNotFoundError（新模块不存在）。首轮实现 exit 1，24 passed / 3 failed：半点被读作0.5分钟、合计标签泄漏到分项、鱼还需的实体错误。第二轮 27 passed。
- 语料缺口 RED：exit 1，27 passed / 4 failed：省略席数、口语预算余额、未绑定补货、序数／泛账名误报。补实现一次 31 passed。
- 抽取复核 RED：exit 1，32 passed / 7 failed：点被中文小数吞掉、复合小时切碎、块布误作钱、分之一误作时长、总到货上界误取首家。实现第一轮 38 passed / 1 failed，第二轮 39 passed。
- 本地来源诊断一次 TypeError: unhashable type: slice（字典误当列表）；改为读取真实键，不重试数据库。
- fa14 正式来源核对一次 AssertionError：完整 manifest 仅 project 展示元数据不同。确认 sources、project_id、assembly SHA 及实际正式正文全部一致后，仅以这些来源身份字段绑定。规范化章节按独特开头分段；raw SHA 单独保留。
- 初轮评测误选普通 fa14 chapter 文件（虽与保全指纹相同，却是旧稿），因此根目录与 eval-final 的 fa14_exports 结果作废；保留记录。有效结果仅 eval-delivery/fa14_adopted_formal.json。
- Ruff 首查有格式、注解、中文标点及行长问题；格式／注解修正，中文标点按正文语法有针对性豁免。保留 lint-initial/lint-final 失败记录；最终 lint-verified.log 通过。
- 最终 64 passed（39 原型 + 25 CheckerReport 契约），日志 tests-final.log；无数据库连接、Docker 或 provider 调用。未产生 SQLite 文件。

所有代码行为缺口均在各自 RED 后修复，单组最多两次实现测试；不把预期 RED 当作环境故障反复重试。

## 补充采用来源回读

首次直连被沙箱拒绝：PermissionError / errno=1，未发出数据库查询；第二次经环境授权成功。固定 localhost/bestseller，启动 default_transaction_read_only=on；事务 READ ONLY、statement_timeout=30s，三条显式 SELECT 后 rollback。实际采用 V9/V3/V1；前两章导出落后，旧候选状态不得当作实时状态。238 项事实仅 1 条库存7斤／补货缺口3斤误报。未保存正文或凭证，保留 sources.json 和 CheckerReport。
