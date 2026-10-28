# P6：导出、阅读与发布链审计 · 2026-10-28

fa14 已有与采用源清单 SHA 一致的正式整篇 Markdown，但“技术完成”还不能等同于“可上传或已发布”。当前上传标记为 false 的直接原因是 10,902 字超过 8,000 字目标的 15% 容差。离线验证还发现 EPUB 目录 XML 无效；DOCX、PDF 能生成。三家中文平台只有代码和模拟测试证据，KDP 提供资料包与人工上传流程。

**范围与证据。** 分支 `par/P6`，基线标签 `e2e17`（`419181a41f29363263cee8a12d1694ace0bcef24`）；证据提交 `1def2823d7fd23c4ba3fc99b2e2c8dbe4b224a9b`。产物均在本 worktree 的 P6 报告目录。来源：主工作区 `output/realistic-life-1792514984/exports/`（fa14）。同名源码 SHA 一致；容器版本未验。

**正式文件与采用记录。** `fanqie-short.md` 去标题后的 SHA 为 `7b82244210cceabf5968beaa012aa2c4b220c309a30aed0a68a8973b50c08198`，与 adopted-source.json 一致；四章版本8/13/7/12，清单内部ID、哈希和 promoted 标记一致。根目录四个 chapter 文件重拼却为 11,187 字、另一 SHA，比正式包多 285 字；根 project.md 仅 770 bytes。这些镜像不能代替正式包。当前数据库采用/知识闭合及完整 delivery_verification 回执未回读，属 **Unknown**；磁盘一致不证明生产闭合。见 [来源核对](source-consistency.json)、[文件 SHA](source-inventory.json)、[末次文件回读](source-final-readback.json)。

**fa14 上传条件逐项核对。** 见 [数值及全部原始 finding code](readiness-summary.json)。

| 条件 | 当前值 / 门槛 | 判断与归类 |
|---|---|---|
| 目标字数容差 | 10,902 / 8,000；差 2,902，36.275%；允许 6,800–9,200 | **唯一未满足的布尔条件**；超上限 1,702 字。内容与目标合同不符；8,000 是项目目标，非本次查证的平台硬限。 |
| word_count_within_10pct | false；10% 上限 8,800，超 2,102 | 检查口径问题：展示字段是10%，上传公式是15%；本书两者都失败。 |
| 三个有效语义门禁 | true / true / true；剩余 blocker=0 | 已满足；原始三项 false 不应重复计作有效阻塞。原始9种critical、2种warning；16条非阻塞裁决。 |
| taboo | 0 / 要求0 | 已满足。 |
| 其他内容/合同债务 | reader_attraction 未解决1项；commercial_passed=false；人工吸引力验收未执行 | 不在 ready 布尔公式内，但不代表商业质量已通过；不能用语义豁免替代。 |

whole_book_complete 与 ready 独立计算。启发式用 len 取前30%（unlock_zone_words=4,046），把整篇当“第1章”，有误报局限；不应重复打开已裁决门禁。

**格式、阅读与发布能力。** 完整16项分类及代码行号见 [export-matrix.csv](export-matrix.csv)、[静态证据](STATIC-EVIDENCE.md)。

- Markdown/DOCX/EPUB/PDF 均接 CLI/API/MCP 并有本地测试。DOCX 保留336条非空输入行，但书名出现2次；PDF 16页，中文序列完整，1/8/16页抽检可读。EPUB 虽生成成功，nav.xhtml 报 `unbound prefix: line 1, column 153`，且无 modified 元数据，不能认定为合格上传包；KDP 复用同一 builder。见 [格式检查](offline-formats.json)、[PDF检查](pdf-extraction.json)。
- 单篇 Web 阅读优先正式 MD，但不重验采用/交付回执或上传标记；Web artifact 路由可取文件。API 下载需 artifact_id，生成响应却只返路径，且短篇正式导出不登记该通用 artifact；阅读器未找到四格式下载按钮，用户交付闭环尚有缺口。
- 番茄/起点/七猫：HTTP适配器与章节调度已接通，测试使用 mock；真实端点、账号与投稿回读 **Unknown**。番茄正式整篇导出与章节投稿是两条链：调度取 is_current 章节，不消费正式整篇包或 ready 标记。KDP 的 CLI validate/package 已接通；自动发布适配器未实现，上传、税务、收款及定价由用户在平台完成。

**建议顺序（本次只审计，未实施）。** ①统一正式来源、阅读与下载身份，查明镜像差异，返回 artifact_id/链接及上传状态。②保持目标则至少减1,702字；调整目标须正式变更合同并重验。③补EPUB XML回归检查并修复，再去重DOCX标题、验收排版。④接单篇投稿、热加载及防重；pending仅flush后跨远程调用、5xx重发存在风险。⑤取得发布授权后验证账号/书籍映射/当前规则，真实投稿回读；KDP人工上传。

**验证与隔离。** 51项纯函数/mock测试通过（`tests-*.log`）；不是生产、平台或读者验收。未使用Docker、数据库或Web接口，未发模型调用，未改应用代码/主工作区已有文件，未合并或推送。转换书稿和抽检图片只留本机 `local-only/`，不入Git。内置盘各步骤均高于5GB；PDF渲染和提交前格式检查的错误码、差异及处理见静态证据。
