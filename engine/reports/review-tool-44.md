# 第 44 条：外部评审自动化

结论：匿名阅读包、责编 / 挑剔读者双视角分段评审、逐条引文与人物归属核对、Evidence / Inference / Unknown / 可能捏造分栏报告均已实现。真实模型已对 `eval-33` 与《借兽之暖》副本的前 10 章各跑完整流程；源目录经 `diff -qr` 核对为 0 改动。

## Evidence

### 实现与离线回归

- `tools/review_pack.py`：只从终稿 / 正文与章节目录构造书名、章号、章名、正文；支持全书和 `1-10,15` 章段；拒绝写到系统临时目录之外。真实阅读包未检出模型名、内部状态、检查标记、调用文件或源路径。
- `tools/review_runner.py`：固定责编 6 问、挑剔读者 4 问；每条可核结论必须给章号与不超过 60 字逐字引文，无引文内容转入“印象”；默认每 10 章一段。判官优先选 `.env` 中异家族角色，找不到则明确标记同源。
- `tools/verify_review.py`：保留原有文本评审核对入口，新增结构化结论核对。引文在指定章且人物归属可核为 Evidence；引文存在但章号 / 人物对不上为 Unknown；全书可用正文和目录均不存在才记“可能捏造”。`address` 不当人物别名，台词中的被叫者不当说话人。
- `tools/review_report.py`：按严重度、章号排序问题；单列读者弃书点、人物记忆复述、无引文印象与可能捏造；报告明确“不修改书稿，也不自动转成开发需求”。
- 历史夹具覆盖两次外部评审：`review-eval33-external.md` 的“第 1 章没有借招”判为 Evidence，“主角断臂”因实际人物是裴天骁判为 Unknown；`review-yizhang-external.md` 覆盖真实章名与不存在目录串。
- TDD 过程先观察到缺模块、`/tmp` 路径误拒、legacy MiniMax 模型名 HTTP 400、人物归属误用后文 / `address` / 台词被叫者，再逐项补回归并修复。HTTP 400 请求未产生正文输出，失败调用与 Unknown 用量保留。

### 真实模型（系统临时目录只读副本，前 10 章）

| 书 | 成功调用 / 失败调用 | tokens（已确认） | Evidence | Unknown | 可能捏造 | 挑剔读者弃书点 |
|---|---:|---:|---:|---:|---:|---:|
| eval-33《每借一剑，我就失去一点自己》 | 2 / 1 | 49,417 | 15 | 15 | 1 | 第 4 章 |
| 第 39 条《借兽之暖》 | 2 / 0 | 44,959 | 14 | 4 | 2 | 第 6 章 |

- 两书合计 94,376 个已确认 tokens；失败的 legacy 模型名请求没有 usage，保持 Unknown。按现有记账函数估算：已知约 `$0.01995`，Unknown 上界约 `$0.01694`，保守合计约 `$0.03692`。
- 当前 `.env` 的 writer / critic 都是 MiniMax 家族，故两份报告均标记“同源判官，可能高估”。legacy 配置名 `openai/MiniMax-M3` 不被 engine 直连 API 接受，兼容为已实跑的 `MiniMax-M3.1-Flash`；原配置名和兼容原因写入结果。
- eval-33 可核候选包括第 6 章英文 `twist`、第 3→4 章空间跳转、第 9→10 章残铃去向缺口；《借兽之暖》可核候选包括第 1 章“砚哥”缺来历、第 7 / 9 章同构动作与代价描写、第 10 章灵晶分配口径。这里只证明引文、章号及可判人物归属匹配，不证明编辑结论成立。
- 临时产物：`/tmp/bestseller-f44-review.1OirYB/eval-33-review/`、`/tmp/bestseller-f44-review.1OirYB/trial-50-review/`；各含 `reading-pack.md`、`raw-reviews.json`、`verified-claims.json`、`review-report.md`、调用账本与响应检查点。

## Inference

- 这套工具能显著减少“引文根本不存在、章号错、把别人的动作安到主角身上”进入开发需求的概率；它不能判断一条引文是否足以支撑完整编辑结论。
- 真实运行得到的弃书点、签约判断、人物印象均来自同源模型，只能作为评审意见，不是跨家族独立判断，更不是人类读者验收。

## Unknown

- 当前 `.env` 没有可由 engine 直连客户端使用的异家族 critic 配置，跨家族质量未验证。
- 代词、无说话人标记的短台词与省略主语句保持 Unknown；工具没有为了提高 Evidence 比例猜测人物。
- “可能捏造”表示逐字引文在可用正文 / 目录中不存在，也可能是模型把真实内容改写成了不准确转述；仍需人工查看上下文。

## 门禁

- `PYTHONPATH=.:tools .../.venv/bin/python -m pytest -q --no-cov tests/`：`1470 passed, 1 skipped, 761 subtests passed`。
- `tools/check_scope.py`：PASS；最大 Python 文件 497 行，无 `bestseller` import、无 bookengine / deai 交叉导入。
- `python -m ruff format --check .`：229 files already formatted。
