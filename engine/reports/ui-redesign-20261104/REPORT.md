# 第 25 条执行报告 · 2026-11-05

**结论：触发行数止损，未完成，不能合并或作为待验收成品。** 用户要求 webui 总行数 ≤1200、写不下就停下；当前 Python、JS、HTML、CSS 实算 1256 行，超出 56 行。已停止实现并保留本地草稿，不压行、不放宽限额。第 25 条保留「待执行」，注明阻塞，未改成「已执行，待验收」。

## Evidence

- 工作区仅 `scratch/ui-worktree`，分支 `feat/bookweb-ui`；未修改主工作区，未 push、未合并，未改 bookengine、deai、ideation、Docker 或 8787。模型调用 0，费用 $0。
- 已通读 DESIGN.md 并以临时静态服务 `127.0.0.1:8789/prototype.html` 实际打开原型，按 1440px 查看截图；原型参考截图仅在 `/tmp/bookweb-prototype.png`，不是实现验收图。静态服务及浏览器会话已关闭。
- 前端草稿：四个 hash 路由、三步向导、书库筛选/搜索/排序、详情标签、独立阅读器/目录/宽度/字号/行距/背景/版本设置、归档/删除对话框。第 24 条选题请求、卡片编辑和发散逻辑沿用原函数，第 22 条两份预算请求字段保留。
- 后端和临时数据测试：archive / restore / delete / rename、`GET /api/archive`、书名确认、活进程与符号链接拒绝、操作日志、删除费用摘要、章节状态与简介、`--read-only` 启动和拒绝写请求。新测试全部使用临时目录，没有对真实书稿执行管理动作。
- 相关回归：`PYTHONPATH=.:tools ../../../.venv/bin/python -m pytest -q --no-cov tests/test_webui*.py tests/test_ideation_web.py`：113 passed，188 subtests passed。
- 全量原命令：576 passed、830 subtests passed、1 failed。唯一失败为工作树缺少测试读取的 `books/compare/gemini.md`。主工作区存在该原件；不复制、不改测试文件，仅在测试进程将 `test_deai_rules.GEMINI` 指向原件后全量复核：**577 passed，835 subtests passed**。解释器 Python 3.11.14。
- 复核命令（工作树 `engine/` 内；只读原始比较稿）：

```sh
PYTHONPATH=.:tools:tests ../../../.venv/bin/python -c 'from pathlib import Path; import test_deai_rules; test_deai_rules.GEMINI = Path("../../../books/compare/gemini.md").resolve(); import pytest; raise SystemExit(pytest.main(["-q", "--no-cov", "tests/"]))'
```

- `node --check engine/tools/webui/app.js`、`git diff --check` 通过。`check_scope` PASS：bookengine 4950、deai 786、webui Python 717、exporter 253、ideation 400；最大 Python 文件 365 行。此工具仅统计 Python，不能证明 webui 所有资产总计合规。

| webui 文件 | 实际行数 |
|---|---:|
| tools/webui.py | 8 |
| tools/webui/jobs.py | 338 |
| tools/webui/library.py | 220 |
| tools/webui/server.py | 151 |
| tools/webui/app.js | 466 |
| tools/webui/index.html | 33 |
| tools/webui/style.css | 40 |
| **合计** | **1256 / 1200** |

## Inference

现有 HTTP 回归支持后端主要正常路径和拒绝路径；不能据此推断整个网页已经可用。前端仅经过语法检查，仍是草稿。后续若继续，应先通过真实结构简化解决行数约束，再处理下面的已知缺口，不能以当前 check_scope 的 Python 计数代替总量验收。

## Unknown / 未验收与已知缺口

- 未启动 8788 的实现服务，未对真实书库做页面集成验收；设计第 6 节要求的实现截图尚未生成，`shots/` 未交付。1440px 铺满 ≥1200、双栏各 ≥560、375px 无横向滚动、URL 刷新及前进后退、快捷键/复制/下载均未实测。
- 已知前端连接缺口：归档撤销仍使用原 ID，后端恢复要求归档 ID `webui/.archive/<id>`；人物卡片读取 `characters`，后端当前仅提供圣经 YAML；阅读正文未接实时刷新，仍需集成验证。停止后未继续修这些问题。
- 对照目前按变化段落高亮，尚未做句级差异核验。卷分组有按 25 章回退值，未验证真实卷纲。
- 删除只覆盖删除前中断和未实际删文件的失败；部分文件已删后的重试、损坏费用汇总账本仍未补充验证，不能宣称预算安全验收完整。
- 浏览器成功、真实写作端到端、读者质量验收与生产回读均未建立；没有模型授权，未执行写作或去 AI 调用。

停止原因来自本次用户明确的「webui 总行数 ≤1200……写不下就停下报告」，不是新增审批要求。保留草稿供后续决定，当前不应部署或合并。
