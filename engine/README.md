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
去 AI 使用独立 `--budget`（默认 $0.50），累计书内 `final/` 的已知及未知用量上界，续跑不清零；`--budget-root` 可显式指定共享计费目录，不计入写作调用。
检测分是编辑提示，不能替代人工抽读。故事结构问题列在 SUMMARY，句子润色不能代替补完故事。
常规处理只改命中句；整场对白与演员法用 `--dialogue-mode scene` / `actor` 显式运行。

历史 S1–S5 用法已合并到当前入口：书设定 YAML 指定题材、章数、字数和用户输入，`--q1 --style b` 使用圣经、多线排期、卷章规划和极简正文提示；同目录重跑复用检查点，写作草稿与独立去 AI 终稿分开保存。早期各阶段验收及调用、耗时和质量结论保留在 [PLAN.md](PLAN.md)、[INBOX.md](INBOX.md) 和 [reports/](reports/)，不再使用旧 worktree 路径作为启动说明。当前网页、导出和检查命令见仓库 [README.md](../README.md)。
