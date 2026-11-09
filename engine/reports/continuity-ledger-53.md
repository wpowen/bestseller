# 第 53 条：连续性账本验证报告

## 结论

第 53 条开发与验证完成，待监工验收。连续性账本现在以原文引文为准记录人物生死、位置、伤残、身体构造和主角已消耗记忆；写手收到自然语言的当前事实；正文写完后执行确定性检查与一次低推理模型复核，定向修改命中句，复检失败则回滚。整书体检同时报告 10 章窗口内的同类重复事故，章纲提示会要求避开。

真实模型使用 eval-52 的种子在外置临时目录完成前 15 章：连续性门禁命中 4 处，修改后剩余 0；最终正文已逐句确认采用修改。程序检查与模型判断不能替代读者验收。

## 范围与提交

仅修改第 53 条允许的文件；没有修改 `engine/deai/`、`engine/tools/webui/`、第 54/55 条归属文件或真实书库。`q1_chapter.py` 只增加一个 import 和一处调用，`book_checkup.py` 只增加一个 import 和一处注册。

分项提交：

1. `17b70f3f5 feat(engine): add quote-grounded continuity ledger`
2. `4f6ef298c feat(engine): give writers continuity facts`
3. `ca704cbef feat(engine): repair ledger contradictions`
4. `c8dc6ef88 feat(engine): report repeated continuity accidents`
5. 真实响应兼容、崩溃恢复验证、报告与状态更新由本报告所在最终提交交付。

## Evidence：实现

### 1. 账本结构与原文约束

- `continuity_ledger.characters` 为圣经中的主要人物及非人伙伴建立卡片。
- 支持 `life_status`、`location`、`injuries`、`body`；伤情保存部位、左右、程度、起始章、最近章和引文。
- `consumed_memories` 保存人物、摘要、章号和引文。
- 状态变化只有在引用逐字存在于当前正文时才会合并；无引文、未知人物或失败的状态提取不会污染账本。
- 真实模型把伤情返回为 `field=injury, value=肩背碎石擦伤`、把记忆标题误放进 `name` 的响应已通过通用归一化兼容，并有回归用例。

### 2. 写手上下文

- 仅把本章出场人物的现状写成自然语言短句。
- 已消耗记忆以“不能再次献祭或当作完整记忆想起”的清单给出。
- 不向写手暴露 `continuity_ledger`、`life_status`、`travel_status`、`limb_count` 等内部字段名。

### 3. 写后检查、修复和回滚

- 确定性检查覆盖伤情左右、生死、位置、已消耗记忆和肢体数量。
- 有账本事实时，每章最多增加一次低推理模型连续性复核。
- 只允许模型修改带编号的命中句；修改后重新做状态提取和确定性复检。
- 没有实际修改或复检仍有矛盾时保留原文，记录 `rolled_back`。

### 4. 重复事故

- 整书体检按人物、部位和事故原因聚合，在 10 章内再次发生时记录为重复事故。
- `q1_schemas.yaml` 的章纲指令要求规划下一批章节时避开 10 章内同一人物、同一部位、同一种事故。

## Evidence：离线验证

源书只用于复制；验证副本位于外置盘，目录权限为 `0555`：

- `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/f53-resume-65kcy5/books/eval-52`
- `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/f53-resume-65kcy5/books/eval-49`

eval-52 保存数据夹具逐项结果：

| 章节 | 预期问题 | 实际命中 |
| --- | --- | --- |
| 27 | 三只前蹄 / 四足构造 | `limb_count` 1 条 |
| 31 | 随队人物被写成仍在旧井道 | `location_status` 1 条 |
| 35 | 再次献祭“药棚守夜” | `consumed_memory` 1 条 |
| 39 | 右手伤写成左手 | `injury_side` 1 条 |
| 42 | 存活人物被写成“那时还活着” | `life_status` 1 条 |

第 6 章保存行用于建立顾晴棣“存活、随行、右手冻裂”的基准；第 34 章保存行用于建立“药棚守夜”已消耗基准。

只读整书体检：

- eval-52：事故记录 10，10 章窗内重复事故 4。
- eval-49：事故记录 0，重复事故 0，用于无命中回归。
- 真实 15 章试写：事故记录 0，重复事故 0。

## Evidence：真实模型前 15 章

### 运行与恢复

- 工作目录：`/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/f53-resume-65kcy5/trial15/book`
- 种子：由 eval-52 `seed.yaml` 的只读副本改为 15 章；没有向真实书库写入。
- 最终 `run.json`：`status=complete`，`completed_chapters=15`。
- 章文件：15 个 `chapters/*.json` 和 15 个 `chapter-*.body.md`。
- 第 8 章连续性调用写回时曾因系统盘 `ENOSPC` 中断。外置盘恢复时复用了已保存的第 239 次 HTTP 响应，并复用第 8 章草稿、收尾、最终状态和人物修补检查点，没有重写已完成的第 1–7 章，也没有重发第 239 次请求。
- 最终物理调用账本：301 条；最后一条为第 15 章核心证据复核，状态 `ok`。
- 用量：prompt 922,101 tokens，completion 495,921 tokens，总计 1,418,022 tokens，cached 143,845 tokens；缺失用量 0，Unknown 用量 0。
- 估算费用：cache-adjusted USD 0.416448675；保守总计 USD 0.43586775。
- 连续性专用调用尝试 17 次：14 次 `ok`、2 次结构输出失败后重试、1 次磁盘中断后从保存响应恢复；其中账本复核 14 次、修复后状态提取 3 次。

### 最终账本内容

- 人物卡 8 张：顾亦衡、叶以宁、小允禾、陆元铮、石兆川、韩冬岚、邢初棠、姜千岳。
- 有引文的位置状态 6 人；伤情 5 条；已消耗记忆 9 条；事故 0 条。
- 顾亦衡记录了肩背擦伤、手掌/掌心伤，以及弟弟第一次叫“哥”、幼兽吃热肉、逃矿拥抱、雪屋伙伴接纳和叶以宁灯下颜色等已失去或不可完整恢复的记忆。
- 石兆川记录掌心被暗丝勒伤；邢初棠记录腕部浅伤。
- 前 15 章没有带原文变化的生死状态或身体构造更新，因此真实账本中这两类为 0；它们的结构、写手上下文和矛盾检查由 eval-52 离线夹具验证。

### 矛盾命中与采用结果

- 修前：4 条模型复核命中，分布在第 4、5、13 章。
- 修后：确定性复检剩余 0 条。
- 最终正文读回：4 个旧句均不存在，4 个替换句均存在。

修复原文如下（共 4 处，超过要求的 3 处）：

1. 第 4 章，伤处从无依据的“手臂”收紧为已记录的“手掌”。
   - 修前：`手臂伤口被牵动，绷带立刻透出血色。`
   - 修后：`手掌伤口被牵动，绷带立刻透出血色。`
2. 第 5 章，区分肩背擦伤和掌部绷带。
   - 修前：`守兽人拉动锁链，顾亦衡便以肩抵住旧链，绷带彻底染红。`
   - 修后：`守兽人拉动锁链，顾亦衡便以肩抵住旧链，肩背擦伤处渗出血来，掌上的绷带也被牵得彻底染红。`
3. 第 5 章，已消耗的逃亡拥抱不再被再次写成新一次“彻底消失”。
   - 修前：`借力退去的一刻，脑中那点属于逃亡拥抱的温度彻底消失。`
   - 修后：`借力退去的一刻，脑中只剩那场逃亡被雪水冲散后的模糊光影。`
4. 第 13 章，账本没有左右侧证据时去掉擅自增加的“右”。
   - 修前：`他扶起被血浸透绷带的右手，手背不受控制地抖了一下。`
   - 修后：`他扶起被血浸透绷带的手，手背不受控制地抖了一下。`

## Evidence：测试与护栏

- 聚焦测试：`6 passed`（`tests/test_continuity_ledger53.py`）。重启后与下述随机碰撞用例一起复验：`7 passed in 0.29s`。
- 全量命令：`PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/`
- 同一代码状态在重启前全量通过：`1599 passed, 1 skipped, 761 subtests passed in 40.05s`。
- 重启后按用户要求把 `TMPDIR` 指向外置新目录 `bestseller-tmp/f53-final-FjYkuV` 再跑全量：`1598 passed, 1 skipped, 761 subtests passed`，另有 1 个与本条无关的既有 webui 随机碰撞失败，见下一节。
- `python tools/check_scope.py`：PASS；无 `bestseller` import、无 bookengine/deai 交叉 import、文件行数护栏通过。
- `python -m ruff format --check .`：267 files already formatted。

### 并行范围外发现，仅报告未修改

- `test_webui_concurrency.py::ConcurrencyTests::test_lowering_two_to_one_stops_excess_and_resumes_original_pid_when_slot_frees` 使用随机 UUID 作书 ID；本次随机值 `71d407e1-...` 恰好包含安全黑名单子串 `d407`，被 `tools/webui/library.py` 按设计拒绝，导致全量中 1 项失败。换一个随机 ID 单独复验即通过。
- 另一轮重启后复验曾遇到 `test_real_http_five_books_150_chapters_under_300ms` 的 cold 单样本抖动：470.0 ms；单测重试为 435.1 / 694.8 ms。额外 3 次诊断样本为 292.5 / 414.9 / 269.6 ms，warm 最大 106.8 ms。该轮其余 `1598 passed`。本分支未改 webui/cache，也按第 53 条文件归属没有处理这两个范围外问题。

## Inference

- 真实试写的 4 条命中来自低推理模型，属于编辑推断；程序只把它们限制在有账本事实、逐字命中句和复检通过的范围内。
- 9 条已消耗记忆中有语义相近的“逃矿拥抱/温度”记录。它们均有独立正文引文，但当前实现不声称能把所有语义近似记忆自动合并成同一条。
- 重复事故识别是基于人物、部位、原因词与 10 章窗口的保守诊断，不是文学质量结论。

## Unknown

- 没有真人读者验收；可读性、自然度及修复是否为最佳表达仍未知。
- 只真实试写了前 15 章，50 章全程的账本规模、语义近似记忆去重效果和长期误报率未知。
- 没有生产发布或网页运行时读回；本条只建立工作树实现、离线副本和真实模型临时试写证据。
