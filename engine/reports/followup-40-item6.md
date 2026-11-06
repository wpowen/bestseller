# INBOX40 第6项：称呼、起名者与首次出现窗口

结论：第6项指定模块已实现并完成局部验证，交父 agent 集成体检字段；未暂存、未提交、未调用真实模型。判断权限均为 `agent_editorial_not_human`，不代表人类读者验收。

## Evidence：范围与行为

- 基线：`6b8e088864128f1e123c3aaae2cd2b2d6b424d38`；分支 `fix/followup-40`。
- 仅写 `bookengine/address_contracts.py`、`bookengine/opening_promises.py`（传递实际章号的一行）、`tests/test_address_contracts40.py`、`tests/fixtures/address40/` 两本圣经、本报告。
- `address_issues()` 保留姓名派生、显式目标优先及旧卡目标回退规则。非姓名派生称呼的来历中，具名人物有叫/喊/唤/称/起名等动作时，校验该人的卡中包含同一目标、同一称呼；冲突两侧都进入原字段级修补清单。空称呼不能绕过起名者检查，多称呼列表可以包含正确项。仅出现另一人物而没有称呼动作，不认定其为起名者。
- 新增纯函数 `address_checkup()`：从原文章号和正文重建首次出现及其前后一章窗口，不相信旧检查状态；逐人物卡检查，不以同名标签去重掩盖另一卡缺来历。解释须在同一句中同时绑定标签与登记来历，保留否定、疑问和跨句无关说明护栏；本章后面另一个有完整解释的句子可以成立。
- 窗口以外的补解释不能消除首次使用缺漏；窗口内缺章为 Unknown。未完稿且后章尚不存在时为 pending；整本已完成时关闭末章的后章窗口。运行时仍保留当章的定向修补机会，没有把 pending 当作原流程的修补成功。
- 未改 `tools/book_checkup.py` 或 `review_diagnostics.py`；设计问题通过原 `address_issues()` 调用链已可读回。正文窗口统计需父 agent 接入下面的接口。没有改写圣经、正文、检查点、源书或真实 `books/`。

## Evidence：RED → GREEN 与命令

以下命令均在 `engine/` 执行，解释器为主仓库 `.venv`，`PYTHONPATH=.:tools`。

1. 首轮 RED（实现前）：

   ```sh
   PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/test_address_contracts40.py
   ```

   结果：`16 failed, 2 passed`，包括真实“砚哥/哥”设计未检出、修补未触发、缺少窗口接口、远章解释错误放行和本章后段解释漏认。日志 `/tmp/f40-item6-red.log`。

2. 实现后初轮相关检查：同一解释器运行 `tests/test_address_contracts40.py tests/test_opening_promises_37.py tests/test_review_diagnostics_37.py`，`53 passed`。
3. 边界补充 RED：同第1条命令；起名者人物卡 `address=None/""` 时未检出，结果 `2 failed, 21 passed`，日志 `/tmp/f40-item6-edge-red.log`。改为从完整人物卡解析起名者后通过。
4. 最终 GREEN：

   ```sh
   PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/test_address_contracts40.py tests/test_opening_promises_37.py tests/test_review_diagnostics_37.py tests/test_promise_pipeline_37.py tests/test_q1_cast.py
   ```

   结果：最终复跑 `71 passed in 0.41s`，其中本项新测试23项。日志 `/tmp/f40-item6-green.log`。移除章级管线集成文件后的直接回归另为 `68 passed in 0.36s`，日志 `/tmp/f40-item6-final-local.log`。

   保留并行集成过程：上述71项曾先通过；第2项接入能力证据调用后，中间一次复跑出现 `70 passed, 1 failed`，失败点是旧 `CaptureClient` 没有预设 `r2-ch1-core-evidence-review`。没有修改该 agent 的文件。父 agent 随后告知已修复该新增 IO 对 legacy 的回归、其定向31项通过；本项再次运行上述完整71项命令后全部通过，不将中间失败归因于称呼改动。
5. 格式与范围：

   ```sh
   /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m ruff format --check bookengine/address_contracts.py bookengine/opening_promises.py tests/test_address_contracts40.py
   /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python tools/check_scope.py
   git diff --check -- bookengine/address_contracts.py bookengine/opening_promises.py tests/test_address_contracts40.py tests/fixtures/address40
   ```

   格式：`3 files already formatted`；diff 检查通过。scope（共享工作树当次观察，包含其他 agent 改动）：

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

## Evidence：真夹具及只读副本读回

只读输入根目录：`/var/folders/21/t2f_q3md2ln9l36r_s8j86mw0000gn/T/bestseller-f40-b07odolm/`。测试中的 `trial-50-story_bible.yaml`、`eval-33-story_bible.yaml` 是此处圣经逐字节副本，校验一致；未取旧工作目录或真实书库的数据。

| 圣经 | SHA256 |
|---|---|
| trial-50 | `a5257dce360e4650db522f5fc5b412326a580794f01c14f1436a43f057969ba3` |
| eval-33 | `30a173e79ba324dacd2e5a0e29888e044c5822a0489e7c85fd712caf7c155edb` |

基线模块通过 `git show 6b8e088864128f1e123c3aaae2cd2b2d6b424d38:engine/bookengine/address_contracts.py` 在隔离命名空间加载，与新纯函数使用相同圣经及 draft/final 原文比较。详细逐卡窗口、首次章号、证据、计数和输入保护在 `/tmp/f40-item6-readback.json`。

| 只读副本 | draft / final章数 | 旧→新设计问题行 | 旧逐章提示次数（draft / final） | 新首次使用问题行（draft / final） |
|---|---:|---:|---:|---:|
| trial-50《借兽之暖》 | 50 / 50 | 0→2 | 19 / 19 | 2 / 2 |
| eval-33 | 50 / 50 | 7→7 | 62 / 62 | 7 / 7 |
| eval-35（无关题材回归） | 30 / 30 | 2→2 | 7 / 7 | 2 / 2 |

两种正文来源结果一致：

- trial-50：顾冬岚的“砚哥”首次第1章，顾安澜卡中的“哥”首次第1章；两者窗口未找到同句绑定的登记来历。设计新增2条是同一矛盾两侧的可修补问题行，不能声称2个独立矛盾。
- eval-33：沈亦衡/唐兆川卡的“阿砺”首次第3章、陆初棠卡的“砺哥”首次第2章；另有“小子”第1章、“修补匠”第2章、“借来的人”第22章、“候选者”第41章。7行涉及6种标签，均缺可用的圣经来历，不能由旧正文的重复出现消除。
- eval-35：“栓叔”首次第4章、“禾姐”首次第8章，旧来历缺漏仍报，不影响姓名派生敬称。

另以 `from tools.book_checkup import check_book` 对三个给定副本分别执行 `check_book(path, source="draft")` / `check_book(path, source="final")`；原体检接口的 `review_contracts.address_design.issues` 数量分别为2/7/2，与新设计检查一致，输出 `/tmp/f40-item6-integrated-design.json`。没有调用会落盘的 CLI 或 `write_checkup()`。

整书体检读回前后，对三个副本全部文件的相对路径与内容哈希比较：trial-50 10511个、eval-33 9446个、eval-35 722个，全部不变。纯称呼回放另校验261个圣经/正文输入文件，全部不变。

## 父 agent 集成接口

```python
from bookengine.address_contracts import address_checkup

address_first_use = address_checkup(bible, chapters, complete=True)
```

`chapters` 接受体检当前已有的字典列表，每项至少 `{"number": int, "text": str}`，可含其他字段。只统计选定来源的正文。整本完稿传 `complete=True`，进行中传 `False`（默认）；不能将最后一个已有章的暂缺下一章误当永久缺漏。

返回 `authority`、`counts`、`checks`、`findings`。`checks` 逐人物卡包含 `speaker/name/address/origin`、`first_use_chapter`、`chapter`、`start/end/quote`、`evidence=[{chapter, quote}]` 和 `status`；状态为 `explained/missing/pending/Unknown`。`findings` 保留后三种，pending/Unknown都不能作为已解释。建议父 agent 存于 `review_contracts.address_first_use`；现有 `address_design` 无需改调用方式。

## Inference / Unknown

- 旧统计按同一称呼重复出现累计，新接口按人物卡首次出现计算；19→2、62→7不是正文改善率。原书未修稿。
- 字符串首次出现不等于确定的说话人归属：“哥”等短标签可能是普通叙述或另一个称呼的子串，因此这些是需核读的候选（`agent_editorial_not_human`）。同标签不同人物卡保留独立问题，未伪造人物归属证据。
- 为保留旧护栏，解释仍要求同句包含登记来历原文；未引入语义模型，也未把不一致改写或同义转述自动认作通过。具名人物加称呼动作以外的起名者指代（如只有“妹妹”而无真名）无法可靠解析，仍有语义盲区。
- 未执行真实模型、真实建书、生产部署或人类验收。没有重跑整库测试；父 agent 应在并行改动集成后执行完整回归并接入新增正文窗口统计。本子项不将接口交付说成整本体检字段已集成。
