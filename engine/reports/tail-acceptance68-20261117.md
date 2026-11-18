# 第68条末段与新增段保护：真实存稿回放 · 2026-11-17

结论：发现两处确定缺陷并在获准模块最小修复；三章局部真响应回放的最终采用稿均保留原末段，
eval-65 第15章的重复台词与重复动作一起回滚。新增7项测试通过。
关联测试最终复验为21通过、1失败：该失败是旧测试仍明确要求补丁放在末段之后，需主agent集成更新。
独立第二阶段可以并行；本报告不宣称整章生产链、自然模型触发或读者验收通过。

## Evidence：输入与临时副本

实际读取的两个源书根目录（均未写入）：

- eval-68：`/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web68-books/webui/85ab9fc7-d694-4aba-9c80-7081467bdd47`
- eval-65：`/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web65-books/webui/610ea95f-ecb9-4f83-ab5d-d83c31e566c5`

逐字复制的30个文件绝对路径记录在
`/private/tmp/tail-acceptance68-20261117-final/read-paths.json`。
主要回放输入如下；表内路径相对对应书根目录，不是人工节选：

| 样本 | 完整初稿与候选步骤 | 原始响应对应关系 |
|---|---|---|
| eval-68 ch12 | `chapter-012.r2.draft.md`、`steps/r2-ch12-draft.md`；`steps/r2-ch12-first-appearance-repair.json`、`steps/r2-ch12-momentum-payoff-repair.json`、`steps/r2-ch12-momentum-payoff-recheck.json` | `calls.json` 对应 `responses/135.json`、`137.json`、`138.json` |
| eval-68 ch26 | `chapter-026.r2.draft.md`、`steps/r2-ch26-draft.md`；`steps/r2-ch26-finish.json` | `calls.json` 对应 `responses/275.json` |
| eval-65 ch15 | `chapter-015.r2.draft.md`、`steps/r2-ch15-draft.md`；`steps/r2-ch15-conflict-repair.json` | `calls.json` 对应 `responses/150.json` |

另复制三个样本的 `chapter-NNN.body.md`、`final/chapter-NNN.md`、`chapters/NNN.json`、
`writing_outlines/NNN.json`，用于保留历史对照与规划上下文。
测试按调用stage/seq找到wire响应，解析后核对候选 `patch/edits` 与步骤文件完全一致；
ch12复核响应的payoff原字段也一致，步骤额外保存的quote逐字对应当前正文的pid段。
初稿同时与 `steps/*-draft.md` 完整逐字比对。

测试仅从系统临时 `inputs/` 副本回放，文件权限0444、目录0555；
采用文本只写兄弟目录 `outputs/`，随后重新读取比对。
网络连接在测试内禁止；模型client未创建。所有所需存稿均找到，没有使用报告短片段替代真回放。

## Evidence：回放链与结果

| 样本 | 实际执行的局部链 | 修复后采用文本 |
|---|---|---|
| eval-68 ch12 | 全初稿 → 已存首次登场edit（第42段）→ `repair_after_draft`的已存兑现响应及已存复核 → `guard_repair_additions` → 临时落盘/读回 | 初稿47段；采用48段。after=40重定位到after=46，补丁成为倒数第2段，原第47段仍最后。新增段守卫无命中 |
| eval-68 ch26 | 全初稿 → `q1_chapter.supplement`直接读取真实finish.patch → `guard_repair_additions` → 临时落盘/读回 | 初稿49段；采用50段。补丁在原第48段之后、原第49段之前；其余原段顺序与文字不变 |
| eval-65 ch15 | 全初稿 → 当前`_apply_payoff_edits`逐字校验并应用真实119/120段edit → `guard_repair_additions` → 临时落盘/读回 | 候选与历史`chapter-015.body.md`逐字一致。重复候选尾部回滚后，采用文本与完整初稿逐字一致（120段） |

eval-65明确断言：“所有出口，封死！”和“一只苍蝇也不许飞出去！”候选各2次，采用稿各1次；
“俯身盯着那滴黑水”采用稿只有原来的1次；原最后一段逐字恢复。
另用同一真实全文增加一处独立首段标点修改，确认尾部回滚保留该独立修改；
三个真实初稿原样经过守卫也均不变。

最终文件与判定JSON：
`/private/tmp/tail-acceptance68-20261117-final/outputs/` 下的
`eval68-ch12.adopted.md`、`eval68-ch26.adopted.md`、`eval65-ch15.adopted.md`
及同名样本JSON。JSON记录段数、末段保留、剩余命中及采用稿摘要校验值。

## Evidence：确定缺陷与修复

1. `repair_placement.py` 默认 `keep_final=False`：钩子模块显式传True，但finish.patch的supplement调用漏过，
   真ch26仍会越过原末段。仅把默认改为True，没有编辑禁止的`q1_chapter.py`。
2. `repair_additions.py` 原来只恢复被命中的单段。真ch15的119段被检出复制台词后恢复了，
   紧连的120段仍重复踢算盘、俯身看黑水，且改掉原末段。
   改为同一连续、等段数替换块只要有一段命中便整块恢复；不同位置的独立修改仍保留。
   未增加基于书名、章号、人名或原句的生产特判，也未降低复述阈值。

新增测试：`engine/tests/test_tail_acceptance68.py`。
修复前正式RED为3失败/4通过（ch26、ch15、保留独立修改的ch15变体）；
修复后同7项全部通过。
两处代码改动以外未编辑共享生产模块、INBOX、原书；没有真实模型调用、部署、新开书或push。

## Evidence：检查与原书未变

本轮命令（`engine/`，主仓库`.venv`）：

```sh
TAIL68_EVIDENCE_DIR=/private/tmp/tail-acceptance68-20261117-final \
PYTHONPATH=.:tools ../.venv/bin/python -m pytest -q --no-cov --tb=short \
  tests/test_tail_acceptance68.py tests/test_repair_additions68.py \
  tests/test_payoff_repair_42.py tests/test_hook_repair68.py
../.venv/bin/python -m ruff format --check \
  tests/test_tail_acceptance68.py bookengine/repair_placement.py bookengine/repair_additions.py
../.venv/bin/python tools/check_scope.py
PYTHONPATH=.:tools ../.venv/bin/python tools/real_library_smoke.py
```

- 关联测试：21通过、1失败。唯一失败
  `test_payoff_repair_42.py::test_finish_extraction_patch_uses_the_same_tail_placement_guard`
  的旧断言要求“第8段 < 兑现结果”，恰好保留了本次发现的缺陷；该文件不属于本次所有权，未改。
- Ruff：上述3文件通过。scope：PASS；最大文件495行，无禁用导入、跨包导入或禁用关键词。
- 真实书库形态smoke：4书、107请求、20次导出、20章阅读；书库最大158.37ms；
  `models_started=0`、`fixture_unchanged=true`。首次沙箱不允许绑定本地端口，获准重跑后通过。
- 源书前后清单：eval-68 **4219文件**、eval-65 **4573文件**；两书均新增0、删除0、改动0。
  只读inputs也新增0、删除0、改动0。清单使用既有`tools.books_guard.manifest/compare`。
  证据为同一final目录的 `sources-before.json`、`sources-after.json`、
  `inputs-before.json`、`source-differences.json`；不是只校验三份章稿。

并发集成注意：后续重跑一度在收集阶段遇到共享`q1_plan.py`已引用但尚未落盘的
`life_planning.py`（ModuleNotFoundError）；没有通过注入stub或覆盖共享文件绕过。
依赖落盘后已在共享当前代码重跑：仍为21通过、1个旧断言失败。该导入中间态已消失。

## Inference

两个遗漏均为框架路径/回滚范围问题。已有真样本证明修复后的局部链能纠正它们；
等段数连续替换块一起回滚较保守，可能连带撤销该块内未单独命中的好改动，优先保全初稿。

## Unknown / 验收边界

- 这是存稿与真响应的**局部回放**，未运行完整`chapter()`，也未重新跑真实模型、状态提取判官或去AI。
  ch12的复核来自旧存档，仅重新验证逐字证据仍成立，不能当作模型对新位置重新评审。
- ch26未把后续首次登场/文字卫生响应套回新段号，ch15未重跑全章兑现与状态复检；
  没有把临时采用稿替换进原书`body/final/`或真实完章检查点。
- `remaining_findings=[]`只证明当前字符片段/台词规则未报复述。ch12补丁仍有对混运、震动、
  编号线索的解释，与已有推断内容重叠；**本轮不能证明所有同义转述都被拦住，也不作读者质量通过结论**。
- 主agent仍需集成上述旧测试契约；共享依赖完整后已复验本轮相关测试，不以本报告冒充全仓测试通过。

## 追加：主线集成后的最终状态（2026-11-17）

本小节覆盖上文“旧断言待集成”的历史状态，RED及当时检查结果保留。

- 主agent已更新 `test_payoff_repair_42.py` 的旧tail断言，并更新 `test_polish_resume.py` 的旧尾后预期。
- 用户同步的主线验证：acceptance69、tail、payoff、additions、hook 合计 **35通过**；
  另含polish/resume的相关集 **22通过、35个subtests通过**。
- 用户同步的主线真实书库smoke：**4书、107请求、20导出，最大书库请求114ms，通过**。
  这是主线执行并回报的结果；本小节没有冒称重新执行该smoke。
- 本agent接手scene后再次执行的受影响集（scene、scene68、repair_ledger、tail、payoff、additions、hook）
  最终 **94通过**，旧tail断言已不再失败。scene专项边界和真实副本证据另见
  `scene-repair70-20261117.md`。
- 主线全量测试由主agent运行；本agent不在这里预报其结果。上述通过不扩大为整章真实模型或读者验收通过。

### 追加：单段末段保护边界（2026-11-17）

主线review发现`keep_final=True`且只有一段时仍可能在该段后插入。
已先用默认值/显式True两例复现失败，再最小修复：返回`invalid/no_room_before_final`、原文不变。
`replace=True`仍按原有替换协议执行，未扩大限制。新增3例均通过。
含scene在内的受影响集最终 **97通过**；三章真实存稿回放仍通过。
这三例属于合成边界回归，不冒称新增真书样本。
