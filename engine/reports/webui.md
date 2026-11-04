# B轨：本地开书、书库与阅读

分支 `feat/book-engine-b-webui`，基线 `9b3f009f2`。只改B轨文件；未改bookengine/deai/exporter或共享入口，未push、未merge、未触及生产库与8787。

实现：标准库本机服务、中文题材、5–2000章设置、可空想法、单调用12选题并强制点选；真实模型能力、默认关闭的独立deai CLI后处理；单书预算/总预算预留、队列并发、暂停/继续/原检查点重试；书库、章目录、草稿/最终/左右对比、剪贴板、MD/DOCX/TXT下载、圣经/章纲/检测与离线 `LIBRARY.html`。正文用 `textContent`，只扫描共享engine-books，历史书只读。没有删除按钮。

## 真实验收

两书都由专用Chromium `engine-b` 页面实际点选创建，写作由公开命令 `python -m bookengine BOOK --q1 --style b --env ENV --out OUT` 完成；默认后处理关闭。未操作用户已有tab、Books或系统关联阅读器。

| 项目 | 悬疑5章 | 玄幻10章 |
|---|---|---|
| 点选题 | 第6项《雨夜证词》 | 第5项《我的宠物正在弑神》 |
| CLI书名 | 《证词里，我是凶手》 | 《猫尾巴里养着一尊伪神》 |
| UUID | e9b3b44e-c77f-453d-a7bb-fe4f0f2d4af4 | 403efb30-77d4-4f12-b572-9f133bc00820 |
| 完成/导出 | 5/5；MD、DOCX已落盘 | 待实跑结束回填 |
| 字数/调用/tokens | 11772 / 16 / 94495 | 待回填 |
| 无缓存折算USD | 0.033271350 | 待回填 |
| usage未知 | 0 | 待回填 |

书根：`/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/scratch/engine-books/webui/`，每书独立UUID，保留全部检查点与原始调用。浏览器下载另存各书 `web-downloads/`。已验证悬疑本章/全书复制与内容一致；MD/DOCX下载逐字节等同公开CLI导出，DOCX包含5章正文。玄幻暂停/继续使用同一PID 587，没有重开。

费用按真实usage以仓库Flash输入$0.15/百万、输出$0.60/百万、不扣缓存折算；不是账户账单。缺usage保留unknown及输入字节/输出上限；损坏账本按不可承受上界阻止新调用。12选题两次共$0.000900300。B轨总计待回填，硬限$0.5。A独立后处理费用单列，不回写/重置原账本；网页若再启动后处理，仅把新增费用归B。

A接手悬疑 `final/`；B不同时启动第二后处理进程。真实最终稿读回、缓存CLI路由及其零新增调用证据待A交接后回填。缓存耗时不作为模型性能证据。

## 验证与边界

- B轨测试当前65项通过；引擎全套最后一次461项通过，最终复跑待回填。
- 原check_scope PASS：核心4984、deai674；它尚未计入tools/webui（显示0）。共享脚本由A统一，本轨另用AST实数检查行数/导入，最终数待回填；各服务文件≤500、总数≤1200。
- 真浏览器桌面1440px、窄屏390px均无横向溢出；截图：[选题](webui/01-create-candidates.png)、[暂停](webui/02-paused.png)、[草稿阅读](webui/03-suspense-draft.png)、[窄屏](webui/04-mobile-create.png)。最终稿/玄幻完成截图待回填。
- 公CLI固定MiniMax M3.1 Flash，没有`--model`，M3/Claude禁用并注明。单章强制重写无公开命令，按钮禁用；重试只恢复原检查点。CLI拒绝恢复已标记stopped的书，网页明确提示，绝不修改run/state绕过。
- 本轨证明页面与公开CLI交付链可用，不据此宣称小说文风/读者质量或模型性能通过。未合入A/C代码，未切换E服务。

## 三种常用方式

1. 在本worktree根启动：`PYTHONPATH=engine ../../.venv/bin/python -m tools.webui --env ../../.env --books ../engine-books --port 8765`；省略env/books同样从worktree推导。只监听 `http://127.0.0.1:8765`，最终服务PID待回填。
2. 页面开书：选题材/章数→想法可空→生成12题→点击候选→预算/并发→开始；书库内暂停、继续或从检查点重试。已付费选题可免费恢复。
3. 页面阅读/复制/下载；离线直接在专用浏览器打开共享 `engine-books/webui/LIBRARY.html`，无服务也可读。此处只验证本地浏览器，不调用任何个人阅读应用。
