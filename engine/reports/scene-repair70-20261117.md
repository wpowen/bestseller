# 68/E7 安全收紧 · 2026-11-17

结论：受缚动作仅留体检reference；自动修复只接收明确同物、同名持有人的窄材质句型，
由代码确定性地把后一处later材质词替换为first，不请求模型改写。
末段在资格阶段排除，构造后再次逐字比对。未放开全部Inference，未硬编码人物或书名。

## Evidence：四项review如何闭合

| 风险 | 最终行为 |
|---|---|
| 受缚者/出拳者归属错误 | 删除受缚自动修复分支。现有`restraint_findings`和体检不变；两处原句仍可呈现为reference |
| 材质同物/同持有人不足 | 两处必须是相邻、唯一的句子，显式相同登记姓名；前句是完整的持有陈述，后句含“同一根/原先那根”等明确指代。否定、引语、比喻、换物、不同人物、中间插句、同类多物件等均不入选 |
| 换人、代词、删行动结果、补写解缚逃检 | 取消自由候选入口。只构造`later_quote.replace(later, first, 1)`；其他字符、姓名、代词、动作结果、标点、段落分隔不变。传入ask不被调用 |
| 末段被改写 | 请求/构造前排除末段内任何句子；构造后比较末段及原有结尾换行；测试把采用稿写入临时文件后按字节读回比较 |

资格是刻意保守的格式白名单。例如“雷九握着这根铁棍。雷九举起原先那根木棍，撞开门板。”
且后面还有原末段时才可能采用；人物名取自传入圣经，测试另换姓名也通过。
“他手里的木棍”“那根木棍”均不足以自动判同物；“铁棍模型”等词内片段也不入选。

仍保留已有状态复检与高置信连续性检查，失败就回滚。
已兑现payoff要求旧逐字引文仍在，复核引文非空且在正文中；不会接受空字符串充当证据。
这可能保守地拒绝材质词恰好位于旧payoff引文中的修正，不扩大语义等价判断。

`repair_scene_consistency`保留原调用签名；ask只是兼容参数，没有修复请求stage，
所以不存在把`max_attempts=1`当作真实一次请求的承诺，也不依赖E4的`rhythm_model.py`或其helper。
状态复检仍委托既有extract接口，本模块不改变它的客户端重试协议。

## Evidence：测试

修改文件仅本次接手的：

- `engine/bookengine/scene_repair.py`
- `engine/tests/test_scene_repair70.py`

测试先RED：原实现对新增安全契约 **32失败、28通过**。
补充词内物件歧义后先复现2失败，再收紧词界。
最终scene测试 **63通过**，覆盖误绑对象、否定/行李误判、宾语当主语、模糊物件/持有人、
同物精确正例、换人/删姓名/代词/否定结果/删结果/新增解缚/越界编辑等旧模型返回无入口，
末段请求前排除、LF/CRLF落盘读回、失败状态回滚、空payoff证据拒绝。
额外用不允许任何stage的假client和禁止调用的ask验证：精确替换也不发起修复模型请求。

受影响集：scene、scene68、repair_ledger、tail、payoff、additions、hook，
scene冻结时 **94通过**；随后补主线发现的单段tail边界后，同集 **97通过**。
Ruff所改文件通过；`check_scope.py` PASS（最大文件495行，无禁用导入、跨包导入或禁用关键词）。
主线全量由主agent在各模块冻结后运行，本报告不预报其结果。

## Evidence：真实只读副本验证

脚本：`/private/tmp/scene-repair70-replay-20261117.py`。
最终证据目录：`/private/tmp/scene-repair70-20261117-cfeffy06/`。
真实源目录：

1. `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web59c-ch50-copy`
2. `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web60n-ch50-copy`
3. `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web65n-ch50-copy`
4. `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/books/webui/c6b8556a-15f7-4de2-b4d3-df2d30b53a20`
5. `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/books/webui/6c18dfd5-ad35-4347-8271-f1d4ac48238b`

每本读取完整50章`chapter-NNN.r2.draft.md`、50章`chapter-NNN.body.md`和`story_bible.yaml`，
共 **505份输入文件、500份完整章文本、250章双口径**，不是人工短片段。
绝对读取路径在`read-paths.json`；复制后逐字节比对，输入文件0444、目录0555。
回放当前资格函数及`repair_scene_consistency`，禁止网络连接；未创建真实客户端。
500份结果写在独立outputs目录后重新按字节读回，完整文本和原末段均逐字比较。

| 样本 | 全文数 | reference材质/受缚 | 自动资格/采用/调用 |
|---|---:|---:|---:|
| 木偶师、领主与古琴、散修丹途、万族使者 | 各100 | 各0/0 | 各0/0/0 |
| 武馆新客 | 100 | 2/2 | 0/0/0 |

武馆的2/2是同一第1章在初稿、正文各出现一次：
材质原句“铁棍正中胸骨，沉闷一响像石梁砸进矿洞。” / “他手腕一翻，木棍撤向胸前，准备再补一记。”
没有显式同名持有人，安全留在reference；受缚出拳同样只留reference。
这些真实文本均不符合新自动资格，因此没有合成响应被实际调用，也没有自动采用。

两次完整源书清单使用既有`tools.books_guard.manifest/compare`：
五书分别5388、4778、4572、4543、4760个文件，共 **24,041文件**，新增/删除/改动全部0；
临时505份输入也全部0。证据为`sources-before.json`、`sources-after.json`、`inputs-before.json`、
`changes.json`，以及包含代码摘要、逐书统计的`summary.json`和两份真实全文的`findings.json`。

## Inference / Unknown

- Evidence支持当前窄资格不会自动改写这五本历史书，且人工反例中的自由改写入口已移除。
- **真实副本上精确替换采用数为0**，成功替换仅有合成纯函数测试；不冒充真实模型自然触发或读者验收。
- 保守白名单会漏掉不同句式的真实矛盾，受缚问题这轮不自动修；这些是安全边界，不通过放开Inference补召回。
- 没有真实模型调用、部署、新开书、INBOX编辑、原书修改或push；未改`q1_chapter.py`等共享集成文件。
