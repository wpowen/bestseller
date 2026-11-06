# 第 39 条：剧情实质去重、核心能力与章型节奏 · 2026-11-06

开发完成，待监工验收。50 章正文与 50 章终稿均完成，边写边去 AI 已实际运行；本书没有通过全部质量目标：手段超额 2 次、最长连续对抗 4 章、核心能力决定结果仅 13/50 章取得可核引文（37 章 Unknown），第 2–5 阶段代价设计仍有提示。程序结果不代替读者验收。

## Evidence：范围、交付与版本

工作树 `scratch/f39-worktree`，分支 `feat/plot-rhythm-39`。只改 bookengine、book_checkup、对应测试、本报告及第 39 条状态；未改 deai、webui、Docker、真实书库，未部署 8787，未 push。

| 产物 | 路径 |
|---|---|
| 完整运行书目录 | [trial-50](/tmp/bestseller-f39-harness-d5ook3ep/trial-50) |
| 完整只读副本 | [full-book-readonly](/tmp/bestseller-f39-harness-d5ook3ep/review/full-book-readonly) |
| 匿名阅读文件 | [anonymous-reading.md](/tmp/bestseller-f39-harness-d5ook3ep/review/anonymous-reading.md) |
| 全部体检字段、逐章明细、引文、账本汇总 | [report39-summary.json](/tmp/bestseller-f39-harness-d5ook3ep/report39-summary.json) |
| 全数值草稿附表 | [draft-report39.md](/tmp/bestseller-f39-harness-d5ook3ep/draft-report39.md) |
| 原稿原始体检快照 | [draft-native](/tmp/bestseller-f39-harness-d5ook3ep/checks/trial-50-draft-native.json) |
| 最终终稿体检 | [trial-50-final.json](/tmp/bestseller-f39-harness-d5ook3ep/checks/trial-50-final.json) |

新书《借兽之暖》，玄幻金手指，50 章、目标每章 2200 汉字，5 阶段各 10 章。原稿总计 114070 汉字；49/50 章在字数带内。`run.status=complete`，写手和 watcher 最后均退出 0，终稿 50/50。原稿程序硬矛盾计数为 0，仅代表被识别的硬矛盾数。匿名文件只添加阅读标题和章号，50 章正文切片逐字校验一致；匿名外评尚未发起。

| 项 | 实现与证据 | 提交 |
|---|---|---|
| 0 衔接复检 | 先对替换段确定性判定；通过后小调用仅重抽边界。缺字段保留 Inference，不调用整章状态抽取；重复、硬冲突、有效末场和后文保真守卫仍在。第 38 条第 3、4 章真实修复稿夹具采用；真断裂回滚。 | 557b91b08 |
| 1 解法实质 | 保存作用对象、手段原词及引文；近 10 章与阶段计数进入规划。对象与手段分栏，三次上限作用于手段；旧类型标签不再作为唯一依据。已完章观察优先于旧计划，缺失为 Unknown。 | 91056810f / 6a9a67a89 |
| 2 核心能力 | 以圣经能力/书名机制为来源，阶段至少一半强制位；阶段用法/代价/限制必须具体且与当前场景动作一致。没有有效操作/结果引文不算通过。 | 9642738d8 / 6a9a67a89 |
| 3 章型节奏 | 生成、缓存复核与校验共用规则；连续对抗最多 3 章后安排收获/关系/揭秘/谋划，每阶段关系主章。重试耗尽后改实际场景意图，并记录前后规划，不只改 type。 | 78fe4272e |
| 4 体检 | 手段分布、阶段超额、能力证据覆盖率、最长对抗段及阶段关系章，均只读统计。 | aff087948 |
| 5 整本 | 真实 50 章与并行去 AI；终稿专用核验 50 章；完整只读副本与匿名阅读文件。 | 本报告及保存产物 |
| 运行修复 | 历史 scenes 是数量而非列表，过渡规划按列表遍历导致 TypeError；通用处理数量/None/列表并识别原生 where 字段。 | 88918d647 |
| 抽取修复 | 补齐旧账本必需 type/content 与新名词 schema；专用观察另存文件、独立账本，原文完整绑定，旧记录不覆盖。 | ee2afaad2 |
| 引文读回 | 仅空白归一定位连续原文，不改字或标点、不跳文；补让字因果；普通犹豫否定不当作能力未用，明确未用/失败仍 Unknown。 | d0b69b5f2 |

保存的 50 份 `writing_outlines` 中有 30 份标注核心能力参与；以共享规则复核，解法、能力强制位、节奏规划剩余违规均为 0。证据：[saved-writing-plan-validation.json](/tmp/bestseller-f39-harness-d5ook3ep/evidence/saved-writing-plan-validation.json)。这不等于正文遵守了全部章纲。

## Evidence：真实失败、恢复与保护

首次写手在 10/50 章后发生一次真实项目错误 TypeError，非限流。副本重放堆栈定位 `complete_transitions → planned_location` 把完章 `scenes=int` 按列表遍历；先复现失败再修复，随后原目录续到 50 章。未重开书、未重置旧账或重写完成章。原日志 `/tmp/f39-model-run.log`，失败/通过重放 `/tmp/f39-runtime-error-replay.log`、`/tmp/f39-runtime-error-green.log`，续跑 `/tmp/f39-model-resume.log`。

| 阶段 | 实际代码/行为 |
|---|---|
| 1–10 章生成 | 6a9a67a89；首轮既有运行源文件前后 0 差异，仅新增未被该写手使用的专用观察模块。 |
| 11–50 章生成 | ee2afaad2；恢复运行既有代码前后 0 差异，最终 writer/watcher 均退出 0。 |
| 50 章终稿专用抽取 | ee2afaad2 原始响应保留，61 条原生调用，50 份 source_text 绑定。 |
| 原始响应只读重放 | d0b69b5f2；不追加模型请求，原保存核心状态 12 verified/38 unknown，读回为 13/37，仅第 25 章改变。 |

不把最终代码说成从第 1 章起已全部加载，也不把后验观察说成已被生成时下一章规划使用。证据：[runtime-code-epochs.json](/tmp/bestseller-f39-harness-d5ook3ep/evidence/runtime-code-epochs.json)、[quote-readback-v2.json](/tmp/bestseller-f39-harness-d5ook3ep/evidence/quote-readback-v2.json)。

原 10 章的 42 个受保护文件及 90 条调用前缀最终完全不变；整本专用观察前后，除新增 `plot-observer/`、`plot-observations/` 外原书文件零改动。随后只重建派生 `book_checkup.json`。完整只读副本与源目录字节一致。真实书库及 eval-33/35 源目录前后清单新增/删除/修改均为 0，详见 [guard-verify.json](/tmp/bestseller-f39-harness-d5ook3ep/evidence/guard-verify.json)；恢复前缀验证在汇总 JSON 的 `completed_prefix_integrity`。

## Evidence：主要目标与实际结果

| 目标 | 终稿实际 | 判定 |
|---|---|---|
| 同阶段同手段最多 3 次 | 第 1、4 阶段蛮力各 4 次；超额章为 9、39，共 2 次 | 未全达标；按要求报告而不硬停 |
| 每阶段能力决定结果 ≥50% | 全书可核 13/50=26%，37 Unknown；只有阶段 4 的 7/10 达证据阈值 | 其余阶段未证明达到；实际使用率仍 Unknown |
| 连续对抗 ≤3 | 最长 4 章 | 未达标 |
| 每阶段至少 1 章关系主章 | 各阶段 1、1、2、1、3 章 | 保存分类下达到；非人审 |
| 能力逐阶段发展 | 设计字段与章纲强制位复核无问题 | 只证明规划约束，正文语义升级需核读 |
| 完整正文及终稿 | 50/50 与 50/50 | 完成 |

能力比例表示「逐字引文核实且模型判为决定结果的章数/全章数」，是可核证据下界，不是直接测得的真实能力使用率。eval-33/35 无这套保存证据：0 个可核实不等于实际没有使用能力。

| 阶段/章号 | 能力可核章 | 可核/计划 | Unknown 数 | 关系章 |
|---|---|---|---|---|
| 1 / 1–10 | [3, 8] | 2/10 | 8 | [3] |
| 2 / 11–20 | [11] | 1/10 | 9 | [18] |
| 3 / 21–30 | [25, 29] | 2/10 | 8 | [23, 24] |
| 4 / 31–40 | [31, 33, 34, 35, 36, 38, 39] | 7/10 | 3 | [40] |
| 5 / 41–50 | [45] | 1/10 | 9 | [44, 46, 49] |

## Evidence：第 37、38 条保存检查与回滚

衔接主状态：`{"checked": 21, "rolled_back": 14, "handoff_unknown": 14, "repaired": 1}`。开头单独状态：`{"Unknown": 36, "handoff_unknown": 11, "rolled_back": 3}`。开头修复可能成功但末场仍未知，不能把两层计数相加。

前三章整体承诺修复均 rolled_back，原因均 `invalid_or_empty_targeted_edits`；其原始 core-use 项是保存的模型判断，终稿能力结论仍以有效引文为准。全书开篇/称呼修补共 15 次回滚；该原因不代表 15 次成功发送的模型修复，须结合账本看空返回和上下文保护。代价校验在阶段 2–5 各有一项 `psychological_change`，共 4 项；核心发展设计校验剩余 0。

| 回滚模块 | 唯一事件数 | 保存原因分布 |
|---|---|---|
| handoff | 17 | {"opening_still_disconnected": 16, "invalid_or_unchanged_patch": 1} |
| opening_promises | 15 | {"invalid_or_empty_targeted_edits": 15} |
| polish.cast_repair | 2 | {"Unknown": 2} |

总计 34 个唯一 `(章号, 模块)` 回滚事件；不重复统计 before/after/state 中的嵌套记录。衔接事件 17 个含 3 个最终主状态为 handoff_unknown 的开头回滚，因此不与主状态 rolled_back=14 混同。2 个称呼修复没有细化原因，保留 Unknown。逐章来源路径见汇总 JSON。

## Evidence：用量与去 AI

| 通道 | 调用记录 | 已知 tokens | 本地估计 USD |
|---|---|---|---|
| 写作/规划/检查 | 249 | 1619738 | 0.568878510 |
| 去 AI | 2277 | 14245857 | 2.576710800 |
| 终稿专用观察 | 61 | 312076 | 0.109267440 |
| 合计 | 2587 | 16177671 | 3.254856750 |

以上含失败返回与重试的用量，不是成功逻辑步骤数；新书三通道 Unknown usage 均为 0。按各自原生账本的本地估价汇总，非账户结算，未设金额止损。写作用量仍独立保存；专用观察没有并入写手旧账。去 AI 终态：`{"polished": 38, "unchanged": 10, "retained": 2}`，2 章 retained 是保留原稿成为终稿，不算润色采用成功。

## Evidence：验证

用户指定全套命令：

```sh
cd engine
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/
```

最终 1307 passed、1 skipped、761 subtests passed（38.86s），日志 `/tmp/f39-suite-delivery.log`。跳过项为 Linux /proc Docker survivor 身份检查，本机 macOS。第 0 项相关 105 项通过；完整套件涵盖旧续跑、选题锁定、去 AI、只出终稿及真实书库夹具冒烟。

各项均有 RED→GREEN；真实 TypeError 副本重放、核心失败/非使用、源文本变更、非连续拼引文、改字/改标点、同版缓存与旧计划覆盖实际解法都有回归。引文修订的 48 项相关测试通过；未为凑数字修改旧正文。

scope 输出：

```text
bookengine_lines=9700
deai_lines=2024
webui_lines=1977
exporter_lines=306
ideation_lines=742
shared_lines=124
webui_frontend_lines=1805 (max_file=491 wizard.js)
max_file_lines=497 (deai/flow.py)
imports_bestseller=no
cross_imports=none
forbidden_keywords=none
PASS: file limits and independence
```

Ruff 全库格式：214 files already formatted；`git diff --check` 通过。独立真实书库形态冒烟（临时副本、随机端口）：

```json
{
  "books": 4,
  "requests": 107,
  "max_books_ms": 75.17,
  "exports": 20,
  "reader_chapters": 20,
  "models_started": 0,
  "fixture_unchanged": true
}
```

eval-33/35 的完整只读副本已用最终代码重跑 draft/final 体检；没有向源书写入，检测不等于修稿。

## Evidence：三本手段分布

三次上限针对归一后的主要手段，对象另记。旧书只从保存的解法内容作保守词族推断；未识别部分是 Unknown，不把数值减少当成正文改善。eval-33 原始 `solution_history` 49 条，其中 27 条含「阵」、11 条含「借」；逐章主解法保存 47 条。这里主解法词族命中与原始历史计数口径不同。

### eval-33

```json
{
  "借剑": 3,
  "阵": 20
}
```

### eval-35

```json
{
  "证词": 2,
  "账本": 1
}
```

### trial-50

```json
{
  "侧滑": 1,
  "共同决定下一段落脚处": 1,
  "兽骨": 1,
  "冰渣": 1,
  "冻土": 1,
  "刀光": 1,
  "劈断血槽": 1,
  "另一条是退路": 1,
  "导风槽": 1,
  "封蜡": 1,
  "平结": 1,
  "指向": 1,
  "换线": 1,
  "撞了上去": 1,
  "撞向侧门": 1,
  "旧井口": 1,
  "旧旗": 1,
  "极寒感知": 6,
  "爆发": 1,
  "爆发身法": 1,
  "盐痕": 1,
  "短镐": 1,
  "碎牌": 1,
  "窄刃": 1,
  "药": 1,
  "蕴魂感知": 1,
  "蛮力": 11,
  "血": 1,
  "锁脉钉": 1,
  "雪路牌": 1,
  "风槽": 1,
  "驱寒蜡丸": 1,
  "骨索": 1,
  "黑羽箭": 1
}
```

完整对象分布、所有超额章与 Unknown 章均在对应体检 JSON；新书仍含动词短语式手段词，不能把原词归类当成完备的语义去重。

## Evidence：整书体检全部汇总数值并列

以下保留 draft/final 两种来源，含 eval-33/35。JSON 中同时保存每个阶段、对手、章对、原文引文、全部数组及数值；下表数组项为数组长度。基线缺证据以 Unknown/null 保留。

| 字段 | eval-33/draft | eval-33/final | eval-35/draft | eval-35/final | trial-50/draft | trial-50/final |
|---|---:|---:|---:|---:|---:|---:|
| /chapter_count | 50 | 50 | 30 | 30 | 50 | 50 |
| /chapter_rhythm/distribution/关系 | Unknown | Unknown | 1 | 1 | 8 | 8 |
| /chapter_rhythm/distribution/对抗 | 36 | 36 | 7 | 7 | 22 | 22 |
| /chapter_rhythm/distribution/揭秘 | 4 | 4 | 19 | 19 | 4 | 4 |
| /chapter_rhythm/distribution/收获 | 6 | 6 | Unknown | Unknown | 5 | 5 |
| /chapter_rhythm/distribution/日常 | Unknown | Unknown | Unknown | Unknown | 1 | 1 |
| /chapter_rhythm/distribution/谋划 | 4 | 4 | 3 | 3 | 10 | 10 |
| /chapter_rhythm/longest_confrontation | 7 | 7 | 1 | 1 | 4 | 4 |
| /core_ability/ratio（逐字证据通过比例，非实际使用率） | 0.0 | 0.0 | 0.0 | 0.0 | 0.04 | 0.26 |
| /format_residue/total | 257 | 257 | 0 | 0 | 0 | 0 |
| /location_breaks/checked | 8 | 8 | 6 | 6 | 22 | 19 |
| /location_breaks/count | 18 | 18 | 17 | 17 | 17 | 16 |
| /location_regressions/count | 0 | 0 | 0 | 0 | 0 | 0 |
| /prose_similarity/card_explanations | 3 | 3 | 0 | 0 | 0 | 0 |
| /prose_similarity/near_paragraph_pairs | 1 | 1 | 0 | 0 | 0 | 0 |
| /prose_similarity/rules/card_repair_above | 1 | 1 | 1 | 1 | 1 | 1 |
| /prose_similarity/rules/containment_min | 0.7 | 0.7 | 0.7 | 0.7 | 0.7 | 0.7 |
| /prose_similarity/rules/jaccard_min | 0.32 | 0.32 | 0.32 | 0.32 | 0.32 | 0.32 |
| /prose_similarity/rules/min_characters | 10 | 10 | 10 | 10 | 10 | 10 |
| /prose_similarity/rules/ngram | 3 | 3 | 3 | 3 | 3 | 3 |
| /prose_similarity/rules/paragraph_distance | 3 | 3 | 3 | 3 | 3 | 3 |
| /repetition/adjacent_paragraph_pairs | 0 | 0 | 0 | 0 | 0 | 0 |
| /repetition/dialogue_repetitions | 1 | 1 | 2 | 2 | 0 | 0 |
| /repetition/narrative_repetitions | 2 | 2 | 2 | 2 | 0 | 0 |
| /repetition/sentence_repetitions | 3 | 3 | 4 | 4 | 0 | 0 |
| /review_contracts/opening_promises/counts/rolled_back | Unknown | Unknown | Unknown | Unknown | 3 | 3 |
| /review_contracts/opening_promises/counts/unknown | 3 | 3 | 3 | 3 | Unknown | Unknown |
| /schema_version | 1 | 1 | 1 | 1 | 1 | 1 |
| /solution_substance/distribution/侧滑 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/修通侧道 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/借剑 | 3 | 3 | Unknown | Unknown | Unknown | Unknown |
| /solution_substance/distribution/共同决定下一段落脚处 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/共同议定 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/兽骨 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/冰渣 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/冻土 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/刀光 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/刻痕 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/劈断血槽 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/另一条是退路 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/商量 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/导风槽 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/封住 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/封蜡 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/平结 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/指向 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/换线 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/撞了上去 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/撞向侧门 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/断刃 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/旧井口 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/旧旗 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/旧约 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/极寒感知 | Unknown | Unknown | Unknown | Unknown | 3 | 6 |
| /solution_substance/distribution/爆发 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/爆发身法 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/牌 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/盐痕 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/短镐 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/碎牌 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/窄刃 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/药 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/蕴魂感知 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/蛮力 | Unknown | Unknown | Unknown | Unknown | 2 | 11 |
| /solution_substance/distribution/血 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/证词 | Unknown | Unknown | 2 | 2 | Unknown | Unknown |
| /solution_substance/distribution/说开 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/账本 | Unknown | Unknown | 1 | 1 | Unknown | Unknown |
| /solution_substance/distribution/退路 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/锁脉钉 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/阵 | 20 | 20 | Unknown | Unknown | Unknown | Unknown |
| /solution_substance/distribution/雪耳 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/distribution/雪路牌 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/霜脊 | Unknown | Unknown | Unknown | Unknown | 2 | Unknown |
| /solution_substance/distribution/风槽 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/驱寒蜡丸 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/骨索 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/distribution/黑羽箭 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/excess_count | 6 | 6 | 0 | 0 | 0 | 2 |
| /solution_substance/stage_cap | 3 | 3 | 3 | 3 | 3 | 3 |
| /solution_substance/target_distribution/三条岔道 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/三条路 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/下行方向 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/东侧风沟 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/两份备选 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/两只药瓶 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/井壁白影 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/位置 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/侧道 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/侧门 | Unknown | Unknown | Unknown | Unknown | Unknown | 3 |
| /solution_substance/target_distribution/借力 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/假腥气 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/假血 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/假血槽 | Unknown | Unknown | Unknown | Unknown | Unknown | 2 |
| /solution_substance/target_distribution/兽群 | Unknown | Unknown | Unknown | Unknown | 1 | 2 |
| /solution_substance/target_distribution/冰壁 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/冰层 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/冰脊 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/冰齿 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/分工 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/北侧壁面 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/北壁下方的一条曲折窄道 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/埋石 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/壁画 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/外露的冰筋 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/夹道 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/守猎者 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/寒意 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/封蜡 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/封门 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/岩壁 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/左囊 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/左边 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/年签 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/引晶 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/披毛兽群 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/担架 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/斜石 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/断绳 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/断闩 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/旧兽纹 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/旧矿道 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/旧药簿 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/旧道 | Unknown | Unknown | Unknown | Unknown | 1 | 1 |
| /solution_substance/target_distribution/暖枢 | Unknown | Unknown | Unknown | Unknown | 1 | 1 |
| /solution_substance/target_distribution/暗河 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/木匣 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/极寒感知 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/横石 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/油纸 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/灰獠犬 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/灰甲者 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/灰皮猎首 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/灰背兽群 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/灵晶 | Unknown | Unknown | Unknown | Unknown | Unknown | 2 |
| /solution_substance/target_distribution/爪印 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/牵引 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/牵引阵 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/独立兽车 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/猎印 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/猎首 | Unknown | Unknown | Unknown | Unknown | Unknown | 4 |
| /solution_substance/target_distribution/留下 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/皮扣 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/盐布 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/石砖 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/矿架 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/碎石 | Unknown | Unknown | Unknown | Unknown | Unknown | 2 |
| /solution_substance/target_distribution/祖窟兽影 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/祖铃 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/祖骨铃 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/空车 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/窄缝 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/窄道 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/窄门 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/第三枚 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/线索 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/绳头 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/绳索 | Unknown | Unknown | Unknown | Unknown | Unknown | 2 |
| /solution_substance/target_distribution/缄煦阳 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/药尘 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/药民 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/药灰 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/药车 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/药道 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/落石 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/裂口 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/调牌 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/赫欣妍 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/追兵 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/追袭者 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/退路 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/铜扣 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/锁牌 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/锁链 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/门板 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/门闩 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/雪耳 | Unknown | Unknown | Unknown | Unknown | 2 | 3 |
| /solution_substance/target_distribution/雪耳的主动选择 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/霜核 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/霜脊 | Unknown | Unknown | Unknown | Unknown | Unknown | 5 |
| /solution_substance/target_distribution/霜脊碎牌 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/顺序 | Unknown | Unknown | Unknown | Unknown | 1 | Unknown |
| /solution_substance/target_distribution/骨甲 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/骨纤维 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/骨链交汇处 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/骨阵 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/黑孔 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/黑盐 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/黑石 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/黑脊猎首 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /solution_substance/target_distribution/黑骨钉 | Unknown | Unknown | Unknown | Unknown | Unknown | 1 |
| /stages/count | 5 | 5 | 3 | 3 | 5 | 5 |
| /stages/location_count | 5 | 5 | 3 | 3 | 5 | 5 |
| /stages/observed_locations/count | 21 | 21 | 22 | 22 | 44 | 41 |
| /title_keywords/threshold | 3 | 3 | 3 | 3 | 3 | 3 |
| 数组长度 /aliases/issues | 0 | 0 | 0 | 0 | 1 | 1 |
| 数组长度 /antagonists | 5 | 5 | 3 | 3 | 5 | 5 |
| 数组长度 /chapter_rhythm/confrontation_runs | 10 | 10 | 7 | 7 | 14 | 14 |
| 数组长度 /chapter_rhythm/excess_runs | 4 | 4 | 0 | 0 | 1 | 1 |
| 数组长度 /chapter_rhythm/stages | 5 | 5 | 3 | 3 | 5 | 5 |
| 数组长度 /chapter_rhythm/unknown_chapters | 0 | 0 | 0 | 0 | 0 | 0 |
| 数组长度 /core_ability/chapters | 50 | 50 | 30 | 30 | 50 | 50 |
| 数组长度 /core_ability/development_issues | 5 | 5 | 3 | 3 | 0 | 0 |
| 数组长度 /core_ability/not_decisive_chapters | 0 | 0 | 0 | 0 | 1 | 0 |
| 数组长度 /core_ability/stages | 5 | 5 | 3 | 3 | 5 | 5 |
| 数组长度 /core_ability/unknown_chapters | 50 | 50 | 30 | 30 | 47 | 37 |
| 数组长度 /core_ability/verified_chapters | 0 | 0 | 0 | 0 | 2 | 13 |
| 数组长度 /format_residue/chapters | 3 | 3 | 0 | 0 | 0 | 0 |
| 数组长度 /format_residue/heading_chapters | 0 | 0 | 0 | 0 | 0 | 0 |
| 数组长度 /introductions/introduced | 2 | 2 | 4 | 4 | 3 | 3 |
| 数组长度 /introductions/missing | 8 | 8 | 5 | 5 | 8 | 8 |
| 数组长度 /limitations | 4 | 4 | 4 | 4 | 4 | 4 |
| 数组长度 /location_breaks/checks | 50 | 50 | 30 | 30 | 50 | 50 |
| 数组长度 /location_breaks/issues | 18 | 18 | 17 | 17 | 17 | 16 |
| 数组长度 /location_breaks/unknown | 38 | 38 | 17 | 17 | 14 | 17 |
| 数组长度 /location_regressions/issues | 0 | 0 | 0 | 0 | 0 | 0 |
| 数组长度 /plot_observations/applied_chapters | 0 | 0 | 0 | 0 | 0 | 50 |
| 数组长度 /prose_similarity/chapters | 1 | 1 | 0 | 0 | 0 | 0 |
| 数组长度 /repetition/chapters | 2 | 2 | 2 | 2 | 0 | 0 |
| 数组长度 /review_contracts/address_design/issues | 7 | 7 | 2 | 2 | 0 | 0 |
| 数组长度 /review_contracts/cost_design/invalid_stages | 5 | 5 | 3 | 3 | 4 | 4 |
| 数组长度 /review_contracts/cost_design/issues | 22 | 22 | 15 | 15 | 4 | 4 |
| 数组长度 /review_contracts/opening_promises/chapters | 3 | 3 | 3 | 3 | 3 | 3 |
| 数组长度 /solution_substance/chapters | 50 | 50 | 30 | 30 | 50 | 50 |
| 数组长度 /solution_substance/excess | 3 | 3 | 0 | 0 | 0 | 2 |
| 数组长度 /solution_substance/unknown_chapters | 28 | 28 | 27 | 27 | 32 | 1 |
| 数组长度 /stage_transitions/checks | 4 | 4 | 2 | 2 | 4 | 4 |
| 数组长度 /stage_transitions/missing | 1 | 1 | 0 | 0 | 0 | 0 |
| 数组长度 /stage_transitions/unknown | 0 | 0 | 0 | 0 | 0 | 0 |
| 数组长度 /stages/distribution | 5 | 5 | 3 | 3 | 5 | 5 |
| 数组长度 /stages/locations | 5 | 5 | 3 | 3 | 5 | 5 |
| 数组长度 /stages/observed_locations/chapters | 24 | 24 | 22 | 22 | 44 | 41 |
| 数组长度 /stages/observed_locations/locations | 21 | 21 | 22 | 22 | 44 | 41 |
| 数组长度 /stages/observed_locations/unknown_chapters | 26 | 26 | 8 | 8 | 6 | 9 |
| 数组长度 /title_keywords/repeated/下 | Unknown | Unknown | Unknown | Unknown | 3 | 3 |
| 数组长度 /title_keywords/repeated/伏 | Unknown | Unknown | Unknown | Unknown | 3 | 3 |
| 数组长度 /title_keywords/repeated/侧 | Unknown | Unknown | Unknown | Unknown | 3 | 3 |
| 数组长度 /title_keywords/repeated/兽 | Unknown | Unknown | Unknown | Unknown | 4 | 4 |
| 数组长度 /title_keywords/repeated/分 | Unknown | Unknown | Unknown | Unknown | 5 | 5 |
| 数组长度 /title_keywords/repeated/剑 | 3 | 3 | Unknown | Unknown | Unknown | Unknown |
| 数组长度 /title_keywords/repeated/夺 | 3 | 3 | Unknown | Unknown | Unknown | Unknown |
| 数组长度 /title_keywords/repeated/暖 | Unknown | Unknown | Unknown | Unknown | 3 | 3 |
| 数组长度 /title_keywords/repeated/暗 | Unknown | Unknown | Unknown | Unknown | 3 | 3 |
| 数组长度 /title_keywords/repeated/甲 | 3 | 3 | Unknown | Unknown | Unknown | Unknown |
| 数组长度 /title_keywords/repeated/白 | 4 | 4 | Unknown | Unknown | Unknown | Unknown |
| 数组长度 /title_keywords/repeated/耳 | Unknown | Unknown | Unknown | Unknown | 3 | 3 |
| 数组长度 /title_keywords/repeated/路 | Unknown | Unknown | Unknown | Unknown | 7 | 7 |
| 数组长度 /title_keywords/repeated/道 | Unknown | Unknown | Unknown | Unknown | 3 | 3 |
| 数组长度 /title_keywords/repeated/门 | Unknown | Unknown | Unknown | Unknown | 4 | 4 |
| 数组长度 /title_keywords/repeated/问 | 3 | 3 | Unknown | Unknown | Unknown | Unknown |
| 数组长度 /title_keywords/repeated/雪 | Unknown | Unknown | Unknown | Unknown | 7 | 7 |
| 数组长度 /title_keywords/repeated/雪耳 | Unknown | Unknown | Unknown | Unknown | 3 | 3 |
| 数组长度 /unknown | 0 | 0 | 0 | 0 | 0 | 0 |

### 对手跨度与退场后候选

| 书 | 对手 | tier / exit | 首次–末次 / span | 主动退场后章 | 在场退场后章 |
|---|---|---|---|---|---|
| eval-33 | 姜孟遥 | 4 / 40 | 2–41 / 40 | [] | [] |
| eval-33 | 姬宜秋 | 5 / 50 | 41–50 / 10 | [] | [] |
| eval-33 | 裴天骁 | 2 / 20 | 11–32 / 22 | [] | [] |
| eval-33 | 赫连如筝 | 3 / 30 | 21–30 / 10 | [] | [] |
| eval-33 | 韩嘉屿 | 1 / 10 | 1–46 / 46 | [44, 45, 46] | [44, 45, 46] |
| eval-35 | 杜木槿 | 1 / 10 | 1–13 / 13 | [] | [] |
| eval-35 | 裴正谊 | 3 / 30 | 21–30 / 10 | [] | [] |
| eval-35 | 韩柯铭 | 2 / 20 | 7–21 / 15 | [21] | [21] |
| trial-50 | 敖杉予 | 2 / 20 | 11–17 / 7 | [] | [] |
| trial-50 | 缄煦阳 | 5 / 50 | 41–50 / 10 | [] | [] |
| trial-50 | 裴洛翡 | 4 / 40 | 34–36 / 3 | [] | [] |
| trial-50 | 赫欣妍 | 3 / 30 | 23–28 / 6 | [] | [] |
| trial-50 | 韩晏竹 | 1 / 10 | 3–39 / 37 | [] | [] |

### 阶段与地点分布

| 书 | 阶段 | 范围 | 计划地点 | 保存章数 | 可核末场地点种数 |
|---|---|---|---|---|---|
| eval-33 | 1 | 1–10 | 残兵巷 | 10 | 0 |
| eval-33 | 2 | 11–20 | 九霄宗论武场 | 10 | 4 |
| eval-33 | 3 | 21–30 | 边荒古战场 | 10 | 7 |
| eval-33 | 4 | 31–40 | 大晟王庭兵器库 | 10 | 6 |
| eval-33 | 5 | 41–50 | 天外兵祖庭 | 10 | 6 |
| eval-35 | 1 | 1–10 | 停运渡口 | 10 | 7 |
| eval-35 | 2 | 11–20 | 货运联盟水仓 | 10 | 6 |
| eval-35 | 3 | 21–30 | 旧泵站水门 | 10 | 9 |
| trial-50 | 1 | 1–10 | 黑石矿镇与雪崩矿洞 | 10 | 9 |
| trial-50 | 2 | 11–20 | 雾骨兽谷 | 10 | 10 |
| trial-50 | 3 | 21–30 | 边境猎场 | 10 | 7 |
| trial-50 | 4 | 31–40 | 王城与外雪堡 | 10 | 7 |
| trial-50 | 5 | 41–50 | 雪原祖窟 | 10 | 8 |

## Inference / Unknown

- 新书对抗章为 22/50（eval-33 为 36/50），关系章 8/50（eval-33 为 0），最长连续对抗 4（eval-33 为 7）；这是保存分类的差异，不能由一次不同选题试写推出因果改善或读者喜好。
- 手段归一词表与场景意图校验是有限启发式；未覆盖同义词、模型命名和语义判断仍可能漏报。实际用法逐阶段升级、引文的因果解释仍属 agent_editorial_not_human。
- 新书终稿仍有 16 处地点断裂候选；代价提示、承诺回滚、称呼及未达阈值均保留。没有把回滚原稿当修复采用，也没有把缺证据当质量通过。
- 37 章核心 Unknown 不表示没用能力；eval-33/35 的全 Unknown 也不能当实际使用率 0%。人审与外部匿名评审、8787 部署、账户结算均未完成或不在本轮范围。

