# bookengine · 整本生成与独立去 AI 味

在仓库根目录使用 Python 3.10+（已安装 PyYAML、python-docx）运行，正文草稿和最终版分开保存：

```sh
PYTHONPATH=engine python3 -m bookengine engine/examples/realistic-life.yaml --q1 --style b --env .env --out books/my-book
PYTHONPATH=engine python3 -m deai run books/my-book --env .env
# 长篇边写边处理；Ctrl-C 后用同一命令续跑
PYTHONPATH=engine python3 -m deai run books/my-book --watch --concurrency 2 --env .env
# 单独的Markdown也可处理，源文件保留；结果在同目录的final/
PYTHONPATH=engine python3 -m deai run chapter.md --env .env
```

`deai` 只读章节草稿及可选人物卡，写入 `final/chapter-NNN.md`、逐章 `.deai.json`、
全书 `final/book.md` 和 `final/SUMMARY.md`。它不读取或更新引擎状态，也不会打开阅读软件。
数字、实体、段落结构、事实与末场画面须保留，字数为原稿 ±10%；不合格的修改回滚。
相同草稿、人物卡、规则版本和模型已完成时直接跳过；可用 `--chapters 1-10`、`--model MiniMax-M3`。
检测分是编辑提示，不能替代人工抽读。故事结构问题列在 SUMMARY，句子润色不能代替补完故事。
常规处理只改命中句；整场对白与演员法用 `--dialogue-mode scene` / `actor` 显式运行。

历史 S1–S5 用法已合并到当前入口：书设定 YAML 指定题材、章数、字数和用户输入，`--q1 --style b` 使用圣经、多线排期、卷章规划和极简正文提示；同目录重跑复用检查点，写作草稿与独立去 AI 终稿分开保存。早期各阶段验收及调用、耗时和质量结论保留在 [PLAN.md](PLAN.md)、[INBOX.md](INBOX.md) 和 [reports/](reports/)，不再使用旧 worktree 路径作为启动说明。当前网页、导出和检查命令见仓库 [README.md](../README.md)。

## 选题工坊（第 24 条，独立分支待验收）

网页“生成新选题”和“换一批”均创建新批次；最近选题只在用户主动恢复时读取。
卡片显示钩子、标签、金手指规则、开场、长篇引擎、市场备注与参考分，可编辑后开书，
也可保留熟壳或新变量继续发散。“两组各 6 个”同时提供这两种方向。
每批上限 $0.05；市场获取失败使用缓存或明确标记无数据，不阻断选题。
判官只有当日同题材锚点测试通过后才参与排序，否则多样性轮转；分数不替代读者选择。

```sh
PYTHONPATH=engine python -m ideation --env .env --books books --out books/topics/my-batch --genre suspense-mystery --chapters 100 --channel 男频
```

同一 `--out` 恢复已完成步骤；换批须使用新目录，历史去重查找同一父目录下最近五批。
请求已发出但结果未知时停止并保留调用记录，不自动重发；需先核对请求与费用。
生成提示只用类别词与结构要求，榜单原文仅供撞车检查和判官。
真实对照工具为 `engine/tools/ideation_acceptance.py`，需显式指定 `--env`、`--out` 和 `--blind`；
原始响应、预算和答案均保存在忽略的书目录，不进入 Git。
