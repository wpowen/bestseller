# INBOX40 第 3 项：承诺修复返回分类与根因修复

工作树 `scratch/f40-worktree`，分支 `fix/followup-40`。本报告及人工式语义判断的 authority 为 `agent_editorial_not_human`。

## Evidence：RED（实现前）

先读 AGENTS、README、engine/README、INBOX37–40、PLAN 当前有效说明及 `reports/plot-rhythm-39.md`。
仅本项拥有的 `bookengine/q1_promise_repair.py`、新增专用辅助模块、新增测试/夹具及本报告可写；未执行 git add/commit。

在 `engine/` 执行：

```sh
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/test_promise_repair_40.py
```

实现前结果：**28 failed, 5 passed in 0.20s**。
15 个真实保存事件都在请求前被上下文保护拦下（`observed == []`）；其余失败证明缺少一次有界重试、细分原因和尝试记录。
5 个已通过项包括保存响应盘点、前三章原始验收响应重放、旧完整输入超限验证。没有以夹具装配失败代替 RED。

## Evidence：原始盘点

只读来源：`/var/folders/21/t2f_q3md2ln9l36r_s8j86mw0000gn/T/bestseller-f40-b07odolm/`。
夹具 `tests/fixtures/promise_repair40/trial50.json` 保存圣经、15 个保存事件的 before/正文和路径及 SHA、
28 份 promise-review 原始响应（完整 JSON，含原始 content）及其账本 stage/seq 对应关系。
`calls.json` 中 `promise-repair` 调用为 **0**；因此不存在可引用的“15 份修复模型空返回原文”。
28 份相关响应实际都是验收调用，不是修复调用；没有捏造不存在的修复响应。

本项代码与离线验证完成，等待父 agent 按项提交和最终集成；本 agent 未执行 git add/commit。

## Evidence：15 个保存事件逐项重放

旧实现取 Git blob `bce434c20`，在内存编译重放，未替换工作树文件。before 与正文逐项取保存夹具，用旧 `precise_ask` 捕获真实 `context_unknown.required`；新实现用同一预算函数测量（含第二次失败提示）。
下列 15 项统一分类为 **local_context_rejected（请求前上下文超限）**，每项对应 `trial-50/chapters/NNN.json:opening_promises`，全文定位在 `chapter-NNN.body.md`。P 是从 1 起的段号，@ 是从 0 起的字符偏移，片段仅用于人工查找，夹具存完整原文。

| 章 | 旧 required / 10000 | 新最大 required / 10000 | 允许段 | 原文定位片段 |
|---|---|---|---|---|
| 1 | 22960 | 4734 | 5,49 | P5@207: “你别怕。”他低声说，“哥一定找到你。”；P49@1757: 顾安澜抬起手，碰了碰他的脸：“砚哥，我就知道你没丢下我。” |
| 2 | 22630 | 4471 | 6 | P6@411: 顾安澜望着他：“哥，你说。是先往旧矿道找，还是拿了矿料，从上面的 |
| 3 | 24758 | 5776 | 7,34,36,38 | P7@442: “娘留下的药民笔记里有。”顾安澜抬了抬下巴，“哥，别以为只有你会；P34@1862: 顾安澜没有说话，只把粗粮掰得更小。；P36@1946: 顾安澜眼圈发红：“我那时才六岁，哪能吃焦饼。”；P38@2091: 这一次借力让石屑扎进掌心，他没有再调用感知，只凭雪耳的方向撞开废 |
| 4 | 22737 | 4719 | 50 | P50@2234: “哥，我们从那里走。”顾安澜说，“趁韩晏竹的人还在北坡，把伤者和 |
| 5 | 22003 | 3987 | 15 | P15@961: “哥，右边有人！”顾安澜俯身听了一息，指向石缝，“不是活人的喘气 |
| 6 | 22909 | 4893 | 3 | P3@240: “哥，先别管泥。”顾安澜蹲到他身侧，把一只冻硬的木匣抱到膝上，“ |
| 7 | 22160 | 4144 | 3 | P3@172: 顾安澜背起药箱：“哥，窄口里面有段旧矿道，塌过几次。风一起，碎冰 |
| 8 | 22683 | 4667 | 5 | P5@324: “哥。”顾安澜压低声音，“他们就是循这个味道追来的？” |
| 9 | 22464 | 4446 | 26 | P26@1303: 顾安澜皱眉：“哥，你连刀都快握不住了。” |
| 10 | 22094 | 4075 | 17 | P17@1323: 顾冬岚没有贸然下去。他将九枚灵晶和引晶分成两份，一份系在盐碑背面 |
| 22 | 23065 | 4841 | 33 | P33@1601: “砚哥，后面的人若进暗格，会看见什么？”雪耳问。 |
| 25 | 22442 | 4425 | 18 | P18@878: 她没有把记号留在外面，而是藏进母亲画像下，像是在告诉后来的人：先 |
| 26 | 22356 | 4339 | 11 | P11@674: 匣盖上“哥，雪耳”三个字像针尖一样扎进眼里。母亲的画像斜缺印记压 |
| 47 | 23507 | 5283 | 44 | P44@1707: “所以它一直在等砚哥需要我。” |
| 48 | 22873 | 4653 | 14 | P14@653: 雪耳安静了片刻，忽然道：“砚哥，你其实怕我出事。” |

新路径逐项送达的是假客户端；故意两次返回 `edits=[]`，验证准确记为 `empty_edits` 且保留旧文，不冒充真实模型采用成功。

### responses 原文与账本对应

28 份原始 JSON 逐份用原生 `content_of` 取 content；全部非空，三个章节使用原生 `parse_json` 和 `review_opening` 离线重放。以下是验收响应的定位，不是修复响应：

| seq / responses 文件 | calls stage |
|---|---|
| 26 / `responses/026.json` | `r2-ch1-promise-review` |
| 27 / `responses/027.json` | `r2-ch1-reader-6-chapter-1-promise-review` |
| 28 / `responses/028.json` | `r2-ch1-reader-6-chapter-1-promise-review` |
| 29 / `responses/029.json` | `r2-ch1-reader-7-chapter-1-promise-review` |
| 30 / `responses/030.json` | `r2-ch1-reader-8-chapter-1-promise-review` |
| 31 / `responses/031.json` | `r2-ch1-reader-9-chapter-1-promise-review` |
| 37 / `responses/037.json` | `r2-ch2-promise-review` |
| 38 / `responses/038.json` | `r2-ch2-reader-6-chapter-1-promise-review` |
| 39 / `responses/039.json` | `r2-ch2-reader-6-chapter-2-promise-review` |
| 40 / `responses/040.json` | `r2-ch2-reader-7-chapter-1-promise-review` |
| 41 / `responses/041.json` | `r2-ch2-reader-7-chapter-2-promise-review` |
| 42 / `responses/042.json` | `r2-ch2-reader-8-chapter-1-promise-review` |
| 43 / `responses/043.json` | `r2-ch2-reader-8-chapter-2-promise-review` |
| 44 / `responses/044.json` | `r2-ch2-reader-9-chapter-1-promise-review` |
| 45 / `responses/045.json` | `r2-ch2-reader-9-chapter-2-promise-review` |
| 51 / `responses/051.json` | `r2-ch3-promise-review` |
| 52 / `responses/052.json` | `r2-ch3-reader-6-chapter-1-promise-review` |
| 53 / `responses/053.json` | `r2-ch3-reader-6-chapter-2-promise-review` |
| 54 / `responses/054.json` | `r2-ch3-reader-6-chapter-3-promise-review` |
| 55 / `responses/055.json` | `r2-ch3-reader-7-chapter-1-promise-review` |
| 56 / `responses/056.json` | `r2-ch3-reader-7-chapter-2-promise-review` |
| 57 / `responses/057.json` | `r2-ch3-reader-7-chapter-3-promise-review` |
| 58 / `responses/058.json` | `r2-ch3-reader-8-chapter-1-promise-review` |
| 59 / `responses/059.json` | `r2-ch3-reader-8-chapter-2-promise-review` |
| 60 / `responses/060.json` | `r2-ch3-reader-8-chapter-3-promise-review` |
| 61 / `responses/061.json` | `r2-ch3-reader-9-chapter-1-promise-review` |
| 62 / `responses/062.json` | `r2-ch3-reader-9-chapter-2-promise-review` |
| 63 / `responses/063.json` | `r2-ch3-reader-9-chapter-3-promise-review` |

不能把这些验收响应中的 missing/unknown 当作修复模型空返回；本次 15 个保存事件没有可用于评判修复格式或 original 匹配的模型返回。新增加的格式/定位/空编辑负例均明确是合成回归，不伪称保存的修复响应。

## Evidence：实现及保留的护栏

仅改动或新增下列五个交付文件：

- `bookengine/q1_promise_repair.py`
- `bookengine/promise_repair_edits.py`
- `tests/test_promise_repair_40.py`
- `tests/fixtures/promise_repair40/trial50.json`
- `reports/followup-40-item3.md`

修复输入保留完整本章正文、待修承诺的原始来源/要求、完整能力规则和涉事人物称呼事实，
移除无关卷纲、canon 及重复 opening_contract；没有截断正文、放大输入上限或绕过 `precise_ask`。
15 项旧输入 required=22003–24758，修改后最多 3987–5776，均低于 10000。
段号与完整 original 严格绑定，原文不一致、越界/布尔/重复 ID、多段替换均拒绝；一组 edits 原子采用。

每次事务最多两次逻辑修复尝试，第二次用独立 stage `<prefix>-promise-repair-retry-1`，
携带上一轮细分原因，始终基于同一份旧正文和允许段集合。沿用原生客户端的阶段重试/接口等待约定，未修改网络重试策略。
`attempts` 逐项记录 stage key、目标段及原因；context_unknown 另留 required/limit。
为兼容既有消费者，耗尽后的顶层 reason 仍为 `invalid_or_empty_targeted_edits`，准确原因在 attempts 中。
细分为 context_unknown、invalid_edit_schema、empty_edits、invalid_target_id、empty_replacement、
original_mismatch、multiple_paragraphs、unchanged_edits、valid。

原有承诺复检、最终状态抽取、地点与转场、硬矛盾、退场对手、称呼、重复句、近似复述的回滚条件保留。
按用户追加指令，旧稿和新稿的 `analyze_prose` 都传 `bible=bible`，使第 5 项 identity 计数也参与防回归。
新增 identity RED：未改调用时 **1 failed, 1 passed, 38 deselected**；False 用例证明新增说明句被错误采用；
True 用例保证旧有说明句不会因旧稿漏传 bible 而被误认作新增。补参后两项 GREEN。

## Evidence：验证命令与结果

以下命令工作目录为 `engine/`；解释器均为主仓库 `.venv/bin/python`。

```sh
# 专用 RED/GREEN，同一命令：初始 28 failed / 5 passed，最终扩充后 40 passed in 0.09s
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/test_promise_repair_40.py

# 追加 identity 护栏的 RED
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/test_promise_repair_40.py -k identity_guard

# 相关回归：早期 127 passed；最后一次因并行第 2 项新增调用变为 154 passed / 1 failed
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/test_promise_repair_40.py tests/test_promise_pipeline_37.py tests/test_opening_promises_37.py tests/test_chapter_continuity.py tests/test_q1_cast.py tests/test_retired_opponents_35.py tests/test_prose_similarity_37.py tests/test_identity_prose40_item5.py

# 共享工作树全套，非冻结快照：8 failed, 1424 passed, 1 skipped, 761 subtests passed in 37.13s
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/

# 真实书库形态夹具冒烟（系统临时目录运行）
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python tools/real_library_smoke.py

/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python tools/check_scope.py
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m ruff format --check bookengine/q1_promise_repair.py bookengine/promise_repair_edits.py tests/test_promise_repair_40.py
git diff --check -- bookengine/q1_promise_repair.py bookengine/promise_repair_edits.py tests/test_promise_repair_40.py reports/followup-40-item3.md
```

专用测试 40 项覆盖保存响应重放、15 例上下文预算、精确定位、事务回滚和一次重试、
前缀/clip_context 恢复、超限不发送、最终状态/重复/identity/承诺复检护栏。
格式检查 `3 files already formatted`；diff 空白检查退出 0。
scope 最后输出：

```text
bookengine_lines=10160
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

冒烟结果：books=4、requests=107、exports=20、reader_chapters=20、max_books_ms=95.78、
models_started=0、fixture_unchanged=true。

共享全套当时的 8 个失败分别为：`test_core_evidence40.py` 四项、
`test_handoff_38.py::test_same_place_core_or_subordinate_location[狭巷-巷中]`、
`test_handoff_40_item4.py::test_local_relaxation_keeps_missing_or_unobserved_transfers_as_issues` 两项、
`test_handoff_diagnostics_38.py::test_same_place_uses_shared_equivalence[巷中]`。
最后一次相关回归的唯一失败为
`test_promise_pipeline_37.py::test_first_chapter_adopts_only_rechecked_core_demonstration`：
堆栈到并行修改的 `q1_chapter.py:339 → core_evidence.py:81`，旧 CaptureClient 报
`Unexpected request: r2-ch1-core-evidence-review`。已通知父 agent，不越权修改第 2/4 项或旧测试支持文件。
这不是本项 GREEN 的替代，专用 40 项单独全部通过；也未把共享全套称为通过。

新副本前后逐文件 SHA 清单摘要一致（路径排序后将 `相对路径:SHA\n` 合并再摘要）：

| 副本 | 文件数 | 前后相同摘要 |
|---|---|---|
| trial-50 | 10511 | `f7929ec20982c64efe785f01f43ec79ae4f003e591877306a4f08a58a80335f1` |
| eval-33 | 9446 | `6d468c0e79cb4091b13cefbe5fca56135c5b50f47c2a5e90dc43e41c13e5a8a9` |
| eval-35 | 722 | `8834c27c51e094b2b02cd3d79cc5b8f7abf4704255789b59038209eddde91f05` |

核对命令为主仓库解释器执行 Python 标准库 `Path.rglob` + `hashlib.sha256`，
将三个目录的文件数及摘要与夹具 `source_books` 中开工快照逐项比较，三项均 `same=True`。
没有调用真实模型、写入源书/真实 books、重置历史账本、修改其他 agent 文件或执行 git add/commit/push。

## Inference（agent_editorial_not_human）

15 例一致的预算拒绝、零修复调用和旧实现重放，支持本轮根因是“修复输入过大 + 原因混记”，
不能据此声称模型修复能力差。第 3 章保存验收段号与正文语义可能并不对应；新逻辑提供可核原文，
仍须靠承诺复检和既有护栏决定是否采用，未因这些提示自动改写其他段落。

## Unknown

没有真实模型修复返回，因此真实模型在新窄输入上的编辑有效率和读者质量未知；本轮没有补发模型请求。
称呼字段矛盾及初始段号选择由其他子项/既有流程处理，本项没有按书名、姓名或章号写特例。
共享工作树仍在并行变动，最终全套集成 GREEN 需父 agent 在各项合并稳定后验证；未部署或声称人审通过。
