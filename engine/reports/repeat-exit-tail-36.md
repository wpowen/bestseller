# 第 36 条：重复叙述删除与退场边界复检

状态：开发完成，待监工验收。工作树 `scratch/f36-worktree`，分支 `fix/repeat-exit-tail`；未 push。

## Evidence

- 两项独立提交：`2147377a8`（重复叙述），`94ead0c3d`（退场边界）。分别先运行失败用例：第一项 13 项失败；第二项 10 项失败，最终修复引入复述的追加回归也先失败再通过。
- 重复句根因：旧依赖检查会检查被删句自身与前一句的代词，把第 11 章两句叙述留给模型修复。现在只自动删除忽略空白后逐字一致、规范化长度至少 10 字的非引号句；检查删除链之后的首个保留后句。后句以代词、这/那或省略主语的动作开头时保留并记 `Unknown`，不交模型。空段连同分隔符删除；不同标点、数字、全半角仍保留诊断但不按逐字相同自动删。
- 引号内复述只报告“对白重复，可能有意”，不自动删除或交复述修复轮；相邻段落检查也不能绕过此规则。近似叙述段仍保留原有有限修复与复检。
- 原书保存记录证明：旧检查**已经识别**韩柯铭第 21 章主动出现；该章 `opponent_repair.status=rolled_back`，并非头衔导致漏检。新增的是 exit+1 的静默/坐立/显式位置在场检查，以及正文所有编辑之后的一次专门复检。检测、修复、复检共用规则，别名同查；仍在场、提取失败、地点衔接/阶段过渡倒退均回滚，最终复检还拒绝新增重复句与格式/引号退化。
- 章纲在场名单检查不再以阶段元数据存在为前提；检查协议升至 3，旧版本未来章纲按新规则复检并另存，保留旧缓存文件。已完成正文与完成检查点不迁移、不覆盖。
- `engine/tests/fixtures/repeat_exit_tail_36/` 的第 8、11、21 章逐字复制自 eval-35 的 `final/`；`design.json` 仅复制原圣经的人物、阶段、对手规划。另有独立人名/场景与负例回归，生产实现未硬编码书名、人名或原句。

### 两本书的只读快照与独立候选结果

计数为重复句的**多余出现次数**；退场在场为“对手、章号”的去重组合。对白与叙述分别统计，段落重合不重复累加。

| 书 | 口径 | 叙述重复 | 对白重复 | 退场后主动/边界在场 |
|---|---|---:|---:|---|
| eval-33 | 修改前程序、原文 | 2 | 1 | 3：韩嘉屿 44、45、46 |
| eval-33 | 新程序、同一原文 | 2 | 1 | 3：韩嘉屿 44、45、46 |
| eval-33 | 独立候选，仅确定性清理 | 0 | 1 | 3：韩嘉屿 44、45、46 |
| eval-35 | 修改前程序、原文 | 2 | 2 | 1：韩柯铭 21 |
| eval-35 | 新程序、同一原文 | 2 | 2 | 1：韩柯铭 21；另明确标注 exit+1 |
| eval-35 | 独立候选，清理 + 第 21 章假客户端修复 | 0 | 2 | 0 |

新程序只读分析原文不会让旧问题自行消失。候选结果在系统临时目录的另一份拷贝生成，未采用到真实书库。eval-33 的对手问题保留报告，没有批量重写旧章。

### 第 21 章假客户端单章结果

- 原句：“货运联盟理事韩柯铭站在闸台上，没有阻拦，只远远看着他们。”
- 替换：“众人提起已经离场的货运联盟理事韩柯铭，谁也不愿回头。”
- 使用真实 Client 阶段解析、S 编号编辑与复检流程，HTTP 传输由假客户端提供；一次修复、一次复检。断言只替换命中句，全文其他字符逐字不变；结果 `repaired`，exit+1 在场命中由 1 变 0。完整单章结果及记录：
  - `/var/folders/21/t2f_q3md2ln9l36r_s8j86mw0000gn/T/repeat-exit-tail-36-or3cohvt/fake-ch21/chapter-021.after.md`
  - `/var/folders/21/t2f_q3md2ln9l36r_s8j86mw0000gn/T/repeat-exit-tail-36-or3cohvt/fake-ch21/result.json`
- 入口流水线回归另证明：前一轮修复后，后续编辑若重新引入退场人物，最终 exit+1 复检仍会捕获并修复。复检失败则原文、提取记录和失败标记一并保留。

### 验证与书库保护

- 用户指定全套命令：`cd engine && PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/`。
- 最终结果：**1027 passed, 1 skipped, 761 subtests passed，37.75 秒**。跳过项为 `test_delete_watchers.py` 中仅 Linux `/proc` 可运行的身份检查。第 26–35 条对应回归包含在全套中。
- 首次全套暴露两处修复链兼容问题：将禁止新增复述的守卫限定到终轮（早轮之后仍有确定性清理），为旧假客户端补齐新增终轮响应，并按第 36 条改为断言确定性删除及零复述模型调用；保留过渡不得丢失等守卫断言，重跑全套通过。
- Ruff：`python -m ruff format --check .` 通过，174 个文件已格式化；`git diff --check` 通过。
- `check_scope.py` 输出：

```text
bookengine_lines=7018
deai_lines=1901
webui_lines=1977
exporter_lines=306
ideation_lines=742
shared_lines=124
webui_frontend_lines=1766 (max_file=491 wizard.js)
max_file_lines=491 (deai/flow.py)
imports_bestseller=no
cross_imports=none
forbidden_keywords=none
PASS: file limits and independence
```

- 修改仅在许可的 bookengine、book_checkup、tests 及本报告/INBOX 状态；未改 webui、deai、docker-compose 或 prompt_packs。
- 对两本真实书分别运行前后完整文件清单比对：**新增 0、删除 0、改动 0**。只读快照和候选均位于 `/var/folders/21/t2f_q3md2ln9l36r_s8j86mw0000gn/T/repeat-exit-tail-36-or3cohvt`；复制及回归过程启用书库写保护，未操作 8787、未查进程参数、未调用真实模型，新增模型费用 $0。
- 临时证据：`summary.json`、`eval-33-baseline.json`、`eval-35-baseline.json`、`eval-33-detector-after.json`、`eval-35-detector-after.json`、两本 `*-cleaning.json`、`*-manifest-before.json` 、`final-library-verification.json` 与候选目录的 `book_checkup.json`。全套日志：`/tmp/repeat-exit-tail-36-pytest.log`。

## Inference

新增边界守卫能覆盖本次复现及已有表面规则支持的静默在场；文件与调用结果证明局部处理/回滚协议，程序规则不构成读者验收。

## Unknown

没有额外运行真实模型，因此真实模型是否能一次生成合格的第 21 章修复未知；假客户端通过不作此保证。在场与语义依赖仍采用保守词法规则，未识别的隐含在场、跨句指代及对白意图未知。真实书稿的历史问题没有被改写，人工阅读质量待监工验收。
