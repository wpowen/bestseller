# 第 25 条第二轮执行报告 · 2026-11-05

**结论：本轮实现、隔离浏览器验收及回归完成；已执行，待用户验收。** 接续草稿 `1e0c511de` 和行数口径提交 `190f6ee0f`，仅在 `scratch/ui-worktree` / `feat/bookweb-ui` 本地提交，未 push。

## Evidence

### 实现与边界

- 前端按职责拆成 `shared.js`（API、通用控件与会话数据）、`library.js`、`wizard.js`、`book.js`、`reader.js` 和 `app.js`（路由与刷新）。CSS 每条规则或一组声明一行，JS 分语句排列，HTML 恢复结构缩进；没有新依赖、构建步骤、外部字体或 CDN。
- 归档撤销使用 action 返回的归档 ID；`/api/book` 解析圣经并返回人物卡，支持列表及按姓名映射，损坏 YAML 或非对象根节点不会使详情崩溃。
- 阅读器每 3 秒查询更新；仅内容变化时重绘，保留章节、滚动位置和详情标签。新增路由竞争验证：挂起翻章请求时触发刷新，刷新不会取消导航。书库数据未变时保留菜单节点，空书库仍显示开书卡片。
- 对照用句子序列的最长公共子序列定位插入、删除和改写，在两边标注变化句；同段未改句与重复出现的相同句保持未标。修复版本选中样式误套整块正文、手机目录隐藏后正文落入零宽列两个实测缺陷。
- 浏览器为 Playwright 启动的 Chrome，桌面视口 **1440×1000**、手机视口 **375×812**；实际服务为 `127.0.0.1:18789`。`browser_fixture.py` 使用系统临时目录，禁用调度器及子进程启动，不读取凭据；仅选题生成是明确标注的合成响应，其余详情、管理动作、开书落盘和导出使用实际 webui 代码。
- 模型调用 **0**，本轮费用 **$0**。截图中的 $0.0066 等数字是合成账本的预算显示样例。未改、未删真实 `books/` 书稿，未修改 bookengine、deai 或 ideation 包，未操作 Docker、8787 服务。全量测试仅读取主仓库原始 `books/compare/gemini.md`。

### DESIGN 第 6 节逐项实测

截图均位于本目录 `shots/`，已实际打开检查。结构化记录见 [browser-results.json](browser-results.json)、[controls-results.json](controls-results.json)、[refresh-results.json](refresh-results.json) 和 [fixture-readback.json](fixture-readback.json)。

| 验收项 | 实测结果 | 截图文件名 |
|---|---|---|
| 四视图 URL 直达与刷新 | library / new / book / read，4/4 可直达并刷新；详情人物标签刷新后保留 | `01-library.png`、`02-wizard-direction.png`、`05-book-detail.png`、`06-character-cards.png`、`07-reader-medium.png` |
| 前进、后退、刷新 | 阅读 1→2→后退 1→前进 2→刷新 2；全部通过；挂起导航时刷新也通过 | `07-reader-medium.png`；过程断言见 browser-results |
| 1440px 适中档 | 正文实宽 **756px**，目录保留 | `07-reader-medium.png` |
| 1440px 铺满档 ≥1200px | 正文实宽 **1376px**；重新刷新仍为 **1376px**；选铺满时自动收起桌面目录 | `08-reader-full.png` |
| 1440px 对照每栏 ≥560px | 草稿 **672px**、终稿 **672px**，对照时自动收起桌面目录 | `09-reader-compare.png` |
| 375px 无横向滚动 | 四视图 document.scrollWidth 均 **375px**；阅读器自身 scrollWidth **375px** | `11-mobile-reader.png`、`13-mobile-compare.png` |
| 375px 目录抽屉 | fixed 定位，宽 **307.5px**，遮罩可关闭；对照上下排列 | `12-mobile-drawer.png`、`13-mobile-compare.png` |
| 归档与撤销 | 书库 **5→4→5** 本，归档最终 **0** 本；日志为 archive / restore | `14-archive-undo.png` |
| 永久删除确认与运行中拒删 | 错书名拒绝，正确书名删除临时新书；运行中书即使书名正确也被后端拒绝 | `15-running-delete-rejected.png`、`16-delete-confirmation.png` |
| 断点重试、暂停/继续 | 暂停→paused、继续→queued、失败书重试→queued；两书 state.json 均仍为 checkpoint=3，未改检查点 | `01-library.png`；动作及文件回读见 controls-results / fixture-readback |
| 复制与五种导出 | 本章剪贴板与终稿全文一致；全书包含 5 章；MD / DOCX / TXT / EPUB / 番茄 ZIP 均实际下载；ZIP 类文件 CRC 正常，番茄 ZIP 为 **5** 个章节文件 | `17-exports.png` |
| 去 AI 对照与独立预算 | 同段只有改写的 **1** 句高亮；其余句不标；写作 **$0.24**、去 AI **$0.71** 独立落盘；手动后处理按钮进入 queued_polish | `04-wizard-settings.png`、`09-reader-compare.png`、`17-exports.png` |
| 选题卡片 / 换批 / 方向 / 修改 | 展示 **12** 张；换批 ID 改变；沿方向 mode=both 到达请求记录；改书名后按编辑卡开书成功 | `02-wizard-direction.png`、`03-wizard-topics.png`、`04-wizard-settings.png` |
| 人物卡片 | 详情实际展示 **2** 人，姓名、角色、欲望、口吻可读 | `06-character-cards.png` |
| 阅读偏好和快捷键 | 夜间、22px、2.4 行距、铺满刷新保留；←/→ 翻章、T 目录、Esc 返回详情通过 | `10-reader-night.png`、`11-mobile-reader.png` |
| 阅读器实时刷新 | 外部只改临时数据：追加终稿句并新增第 6 章；页面自动更新正文、目录及进度 max=6；URL 仍为第 **3** 章，scrollTop **500→500px**；再展开目录并滚到新增句截图 | `18-reader-live-refresh.png`、`19-reader-live-updated-content.png` |
| 预算显示 | 顶栏合成累计 **$0.0066 / $20.00**；详情分别显示写作/去 AI 花费、预算和余额 | `01-library.png`、`04-wizard-settings.png`、`05-book-detail.png` |

浏览器主流程 **29/29** 断言、补充控件 **15/15** 断言通过；实时刷新另列。截图证明最终静态状态，导航、复制和拒绝路径由脚本断言与数据回读共同证明。

### 测试与行数

在 `engine/` 执行用户指定的全量命令；工作树没有 `../.venv`，使用授权的主仓库解释器：

```sh
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/
```

结果：**579 passed，844 subtests passed（9.92s）**。新增人物解析及模块 HTTP 回归先失败，再实现通过。已有真实稿测试的固定路径在工作树失效，本轮仅修正测试夹具定位：缺省路径不存在时，从 Git common-dir 找主仓库原件；不复制、不改原稿，不跳过该测试。

`node --check` 全部 6 个 JS 文件、`git diff --check` 通过。[scope-output.txt](scope-output.txt) 的结果为 **PASS**：

| 范围 | 实测 / 上限 |
|---|---:|
| webui Python | **729 / 1200** |
| 前端 JS / HTML / CSS | **1487 / 1500** |
| 前端最大单文件 | **464 / 800**，wizard.js |
| 前端最长行 | **155 / 160** 字符 |
| bookengine / deai / exporter / ideation | 4950 / 786 / 253 / 400 行 |
| Python 最大单文件 | 365 / 500 行 |

前端逐文件：app 65、shared 112、library 190、wizard 464、book 96、reader 168、HTML 213、CSS 179 行。没有通过修改行数规则放宽限制。

复跑浏览器证据：在仓库根目录用主仓库 Python 启动 `browser_fixture.py`；用 Playwright CLI 的 `-s=bookweb-ui open about:blank --headed` 开独立会话，依次 `run-code --filename=.../verify_browser.js`、`verify_controls.js`；最后用同一 Python 运行 `verify_refresh.py`。每次完整复跑应从新的临时 fixture 实例开始。验收后已关闭本轮浏览器会话与 18789 实例，临时书目录随实例退出回收。

## Inference

这些证据支持四视图重构、管理动作、已有预算及选题入口在本地浏览器和实际 HTTP 边界中连通；句子级高亮可用于定位去 AI 改动。它们不证明故事好看或润色质量。当前无显式卷号时仍按每 25 章分组，与原草稿一致。

## Unknown

- 用户视觉与阅读体验验收仍待确认；本报告不替代人工验收。
- 未调用真实模型；实际续写、暂停中的在途模型请求及去 AI 质量不属于本轮浏览器证据。调度器在隔离实例中禁用；既有进程/预算逻辑由现有离线回归覆盖。
- 未部署、未合并、未读取生产页面；没有 Docker 或 8787 的生产验收结论。浏览器覆盖本机 Chrome，未测 Safari / Firefox。
- 原草稿报告中提及的部分删除后磁盘失败、费用汇总损坏恢复，不在本轮四项修复内，未新增验证；保留为未知，不声称已通过。
