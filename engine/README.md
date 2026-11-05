# bookengine · 整本生成与独立去 AI 味

在仓库根目录使用 Python 3.10+（已安装 PyYAML、python-docx）运行，正文草稿和最终版分开保存：

```sh
PYTHONPATH=engine python3 -m bookengine engine/examples/realistic-life.yaml --q1 --style b --env .env --out books/my-book
PYTHONPATH=engine python3 -m deai run books/my-book --env .env
# 长篇边写边处理；Ctrl-C 后用同一命令续跑
PYTHONPATH=engine python3 -m deai run books/my-book --watch --pipeline --concurrency 2 --env .env
# 单独的Markdown也可处理，源文件保留；结果在同目录的final/
PYTHONPATH=engine python3 -m deai run chapter.md --env .env
```

`deai` 只读章节草稿及可选人物卡，写入 `final/chapter-NNN.md`、逐章 `.deai.json`、
全书 `final/book.md` 和 `final/SUMMARY.md`。它不导入引擎，也不更新写作状态；`--pipeline` 只读章节完成检查点和整书完成标记，跳过已有终稿，全部完成后自动退出。普通独立处理仍按输入、配置和规则检查缓存。
数字、实体、段落结构、事实与末场画面须保留，字数为原稿 ±10%；不合格的修改回滚，原文成为该章终稿；回执保留未通过润色的真实状态。
相同草稿、人物卡、规则版本和模型已完成时直接跳过；可用 `--chapters 1-10`、`--model MiniMax-M3`。
选题、写作和去 AI 均不设金额预算，费用照常记账和显示；写作与 `final/` 的去 AI 调用记录分别保留，续跑不清零，未知用量仍标为未知。
书一旦开写就持续写完；项目出错、账户额度或 token 用尽时保留检查点，修复或充值后继续。
接口限流、排队、超时或连接错误持续退避重试，不设总等待时长上限；千章级任务开跑前仍须告知用户。
检测分是编辑提示，不能替代人工抽读。故事结构问题列在 SUMMARY，句子润色不能代替补完故事。
常规处理只改命中句；整场对白与演员法用 `--dialogue-mode scene` / `actor` 显式运行。

历史 S1–S5 用法已合并到当前入口：书设定 YAML 指定题材、章数、字数和用户输入，`--q1 --style b` 使用圣经、多线排期、卷章规划和极简正文提示；同目录重跑复用检查点，写作草稿与独立去 AI 终稿分开保存。早期各阶段验收及调用、耗时和质量结论保留在 [PLAN.md](PLAN.md)、[INBOX.md](INBOX.md) 和 [reports/](reports/)，不再使用旧 worktree 路径作为启动说明。当前网页、导出和检查命令见仓库 [README.md](../README.md)。

## 选题工坊（第 24 条已合并，真实盲选待用户验收）

网页“生成新选题”和“换一批”均创建新批次；最近选题只在用户主动恢复时读取。
卡片显示钩子、标签、金手指规则、开场、长篇引擎、市场备注与参考分，可编辑后开书，
也可保留熟壳或新变量继续发散。“两组各 6 个”同时提供这两种方向。
选题不设每批金额上限，调用和费用继续记录；市场获取失败使用缓存或明确标记无数据，不阻断选题。
判官只有当日同题材锚点测试通过后才参与排序，否则多样性轮转；分数不替代读者选择。

```sh
PYTHONPATH=engine python -m ideation --env .env --books books --out books/topics/my-batch --genre suspense-mystery --chapters 100 --channel 男频
```

同一 `--out` 恢复已完成步骤；换批须使用新目录，历史去重查找同一父目录下最近五批。
请求结果未知时保留原调用与费用记录；从已完成步骤恢复，后续重试另记，不覆盖旧账。
生成提示只用类别词与结构要求，榜单原文仅供撞车检查和判官。
真实对照工具为 `engine/tools/ideation_acceptance.py`，需显式指定 `--env`、`--out` 和 `--blind`；
原始响应、调用与费用记录、答案均保存在忽略的书目录，不进入 Git。

## 开发检查（第 26 条）

各包及前端总行数不设上限，代码按职责拆分；单个 Python 文件 ≤500 行，前端单文件 ≤800 行、每行 ≤160 字符。
`tools/check_scope.py` 保留总行数与最大单文件统计、禁止 import `bestseller`、bookengine 与 deai 互不导入、
ideation 不导入 bookengine 及禁用词检查；新增或修改的代码保持正常可读格式。
共享文件 `tools/runtime_support.py` 通过 `tools.runtime_support` 导入，单列 `shared` 行数统计，同样受 Python 单文件 ≤500 行、依赖和禁用词检查约束，不设总量上限；不得反向导入 bookengine 或 deai。

## 第 27 条：逐章终稿与交互

网页默认同时启动写作和去 AI，一个并发名额对应一本书。两阶段的等待、额度和项目错误分别保存，某阶段失败不停止另一阶段；暂停会控制两个进程。旧书点继续只补缺失终稿。书库、详情、默认阅读和五种下载均以终稿为准，导出说明列出缺章；草稿保留在磁盘与阅读设置的修改对照中。

删除采用一次确认，运行中或仍有写作/去 AI 进程的书拒绝删除；仅可管理 `books/webui/` 下的直属书及其归档，操作和费用日志保留。弹窗、菜单与手机目录支持外部点击、Esc 关闭和单一浮层。

共享完成协议 `tools/chapter_outputs.py` 与 `runtime_support.py` 同受 scope 的文件上限和依赖隔离检查。

第 28 条修复：去 AI 子步骤的模型返回耗尽六次格式重试后，整章回滚为原稿终稿，记录失败阶段和原因后继续下一章；额度、接口等待和代码错误仍按各自规则处理。旧书只要开启 `polish` 或 `auto_deai`，服务恢复、继续和调度时都会补齐流水线元数据及缺失 watcher，保留活写手、暂停意图和已有终稿。
