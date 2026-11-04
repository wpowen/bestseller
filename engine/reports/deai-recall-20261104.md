# 去 AI 味召回 · 2026-11-04（第22条）

结论：校准门槛通过，《证词》5章重跑及南瓜4、5章处理完成；**修改量、全轮≤180秒目标未达，质量待盲读**。bookengine 未改，未 push。

| 章节 | 候选→余留 | 采纳句 / 类型 | 本轮去AI $ | 全轮墙钟秒 / ≤180 |
|---|---:|---|---:|---:|
| 证词1 | 16→9 | 7 / 结1 声1 神1 签2 动1 动密2 | 0.0683 | 877 / 否 |
| 证词2 | 7→5 | 2 / 签1 白1 | 0.0395 | 687 / 否 |
| 证词3 | 10→4 | 6 / 签3 副2 神1 白1 | 0.0726 | 720 / 否 |
| 证词4 | 7→4 | 3 / 神1 签2 | 0.0668 | 731 / 否 |
| 证词5 | 12→6 | 6 / 喻1 签3 白2 | 0.0722 | 653 / 否 |
| 南瓜4 | 14→14 | 0 / 原文保留 | 0.0462 | 402 / 否 |
| 南瓜5 | 4→1 | 3 / 结1 神1 签1 | 0.0723 | 721 / 否 |

类型可重叠：声=音量反差，神=神态，动/动密=动作，签=对白情绪标签，副=副词密度，白=书面对白，喻=比喻，结=总结。墙钟含本轮缓存续跑/等待，不拿末次缓存秒数充当冷跑；《证词》首次处理73–187秒（1章>180）。每章15–40处目标未达；《证词》共采纳24句，余留28候选，南瓜4因保真/新风格检查回滚，保留原文。

**Evidence：**新增规则/词表/阈值均在 `engine/data/deai_recall.yaml`，泛化语音反差、否定、神态/动作/气氛、情绪标签、段尾总结；词频只定位超额部分。此前多数词表只统计、非 certain 规则不执行，且“不高”未覆盖。同一草稿的规则候选4→36；合并判读候选52→28。完整测试含11,772字整书、blind-2/3 MiniMax稿和三份 compare；点名7/7、留出4/5=80%、Claude候选0，MiniMax稿密度均>0（零分母不伪造有限倍数），未知判读0。参照 [oh-story lint 分类](https://github.com/zenstory-ai/oh-story-claudecode/blob/main/skills/story-deslop/scripts/check-ai-patterns.js) 与 [MIT许可](https://github.com/zenstory-ai/oh-story-claudecode/blob/main/LICENSE)，独立实现。

独立预算 CLI `--budget`、网页默认均 **$0.50**；累计 final/，Unknown及在途保守计入，续跑不清零，不占写作额度。情绪标签直接删且对白原句保留，事实检查仍保护事件/数字/人物/段落/末画面；无效新尝试保留此前有效终稿。本轮 **809次请求，已知估算$0.636140 + Unknown上界$0.240680 = $0.876820 ≤ $1**，含校准$0.438934；保留18次DNS失败、27次422，未把它们算作未命中。两书原始正文、南瓜前三章终稿逐字保留；容器缓存续跑4、5章成功，最终均complete。

**Inference：**按7章本轮新增均值$0.06256推算，100/1000章约 **$6.26/$62.56**，不含一次性校准；只按南瓜两章均值$0.05926则$5.93/$59.26。样本小且含重试，不是报价保证。

盲读：[七篇随机稿（证词第1/3/5章草稿及终稿+Claude锚点）](../../books/deai-recall-20261104/BLIND.md)；KEY仅在该目录private/，本报告不列对应关系。[独立预算界面](deai-recall-8787/independent-budget.jpg)。规则/程序检查与逐段事实判读最终通过；510项测试+762子测试通过，待用户读感验收。

此前 Codex 误导入的测试 EPUB 为《潮汐书店的第十杯茶》：源 `books/s5a/cozy-10/exports/final/book.epub`（旧路径scratch/engine-books/…）；个人图书位置 `/Users/owen/Library/Mobile Documents/iCloud~com~apple~iBooks/Documents/潮汐书店的第十杯茶.epub`。Books.plist记录2026-11-04 17:04:31；中转 `~/Library/Containers/com.apple.iBooksX/Data/Library/Caches/Inbox/book.epub` 当前不存在。此轮仅核查记录，未打开或删除；iCloud同步/其他副本Unknown，由用户决定删除。

本轮一次容器重启中断了另开的《一掌镇乾坤》，已按用户确认用原预算从5章恢复，费用不算入本条；之后只同步deai源码，未再次重启容器。

```text
bookengine_lines=4950 (limit=5000)
deai_lines=786 (limit=800)
webui_lines=600 (limit=1200)
exporter_lines=253 (limit=400)
max_file_lines=365 (bookengine/batch.py)
imports_bestseller=no
cross_imports=none
forbidden_keywords=none
PASS: package limits and independence
```
