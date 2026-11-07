# 第 41 条：人物上下文修复、区分度基线与五章 A/B

日期：2026-11-07
分支：`feat/character-41`
结论：代码与基线测量完成；五章 A/B 的真实模型结果见下文。所有源书只从只读副本读取，真实书库 0 改动。判官跨家族配置存在，但 DeepSeek 返回 HTTP 401，因此跨家族结论为 Unknown；同家族 MiniMax 判官结果只作参考，可能高估。

## Evidence：实现

1. 写手上下文现在给出场人物传入 `flaw`、`bottom_line`，不传 `growth_end`。口头禅由规划层按人物和章号确定性排期，同一人物两次至少间隔 5 章；到期时只提示“本章可在合适情境用一次，不强求”。
2. 超预算时先压仪表盘、历史摘要和非出场人物；场景人物的 voice、desire、fear、current 等最后才删。保存的《借兽之暖》第 20 章夹具证明主角 desire/fear 保留，删减仍写入 `dropped_fields`。
3. 可整句照抄的 identity 不再进入写手上下文，改为短 `identity_keywords` 和行为字段；第 40 条说明句检测未放宽。
4. 新增来源可核的人物诊断：20 段/4 人最低门槛的换名盲测、逐章逐字引文核验的人设兑现、反派出场章/名字次数/台词数，并写入 `book_checkup.json.character_diagnostics`。模型判断不拦截。
5. 新增五章 A/B 工具：只复用已保存圣经、阶段计划和前五章章纲；A 使用冻结的 main 代码，B 使用本分支代码；匿名盲评随机甲乙并在模型返回后映射回 A/B。

对应提交：

- `4bb3121a7`：缺失字段与口头禅排期。
- `bea21a385`：预算删减顺序与第 20 章夹具。
- `cacf4826b`：identity 改为关键词。
- `4ad197167`：来源可核人物基线与体检接入。
- `cb4c4c998`：判官回退、直接模式和盲测返回校验。
- `922103415`：五章 A/B 与匿名左右映射。

## Evidence：三本书基线

三本源书先复制到系统临时目录并设为只读；评测写在另一组可写副本。以下判官均为 MiniMax，与写手同家族，原因是配置的 DeepSeek `deepseek-flash` 请求返回 HTTP 401。失败调用和后续同家族调用都保留在各副本 `character-eval/calls.json`。

| 书 | 换名盲测 | 覆盖人物 | flaw | bottom_line | voice | catchphrase |
|---|---:|---:|---:|---:|---:|---:|
| eval-33 | 2/20，10% | 10 | 1/10 | 0/10 | 8/10 | 0/1 |
| eval-35 | 4/20，20% | 9 | 3/9 | 1/9 | 9/9 | 0/1 |
| 《借兽之暖》trial-50 | 6/20，30% | 7 | 2/11 | 3/11 | 5/11 | 0/10 |

分母是圣经中实际存在该字段的人物数；只采用能在所报章节逐字找到的引文。找不到一律为 Unknown。

反派存在感：

- eval-33：姜孟遥 11 章/141 次名字/14 句台词；姬宜秋 10/146/13；裴天骁 13/231/26；赫连如筝 10/120/11；韩嘉屿 11/158/13。
- eval-35：杜木槿 8/79/4；裴正谊 9/95/11；韩柯铭 11/98/10。
- trial-50：敖杉予 4/6/0；缄煦阳 5/37/0；裴洛翡 3/6/0；赫欣妍 5/34/1；韩晏竹 9/17/0。

调用账本：

- eval-33：6 条；prompt 13,662，completion 19,942，total 33,604。包含 1 次 DeepSeek 401、2 次 MiniMax 推理长度失败和随后成功的直接模式结果；失败用量未隐藏。
- eval-35：3 条；prompt 13,521，completion 1,660，total 15,181。
- trial-50：2 条；prompt 14,648，completion 776，total 15,424。

## Evidence：五章 A/B

A、B 使用同一份《借兽之暖》圣经、阶段计划和前五章章纲。A 的执行代码冻结在 main 提交 `643923364`；B 使用本条改动。两侧都写入独立系统临时目录。

| 指标 | A：旧人物上下文 | B：第 41 条人物上下文 |
|---|---:|---:|
| 终稿 | 5/5 章 | 5/5 章 |
| 生成调用 | 49 成功，0 失败 | 66 成功，3 次结构返回不合格后重取成功 |
| 生成 token | 237,666 | 294,547 |
| 生成保守估价 | $0.0754812 | $0.0938301 |
| 显式归属对白池 | 5 段、2 人 | 9 段、4 人 |
| 换名盲测 | Unknown，未达 20 段/4 人门槛 | Unknown，未达 20 段门槛 |
| flaw 引文兑现 | 5/11 | 2/11 |
| bottom_line 引文兑现 | 4/11 | 2/11 |
| voice 引文兑现 | 4/11 | 4/11 |
| catchphrase 引文兑现 | 0/10 | 1/10 |
| 口头禅逐字次数 | 0 | 1（第 3 章“先救，再算账。”） |
| 主角缺陷是否坏事 | Unknown | Evidence |

B 的缺陷后果经正文回读：第 1 章妹妹明确说“你答应过我，不再一个人逞强”，主角仍让她闭嘴并独自推石，随后“手臂上的皮肉被石棱撕开，血沿着腕骨流下”，并承受记忆代价。A 前五章能找到嘴硬和受伤，但找不到缺陷导致后果的充分连续证据，因此保留 Unknown。

删减差异：A 在第 2、3、5 章删了出场人物顾安澜的 `current`；B 先压主角 dashboard/continuity/power 和非出场 `fixed_cast`，没有删场景人物 desire/fear/current。B 的唯一逐字口头禅只出现一次，没有形成复读。

匿名盲评：配置的跨家族 DeepSeek 不可用，所以跨家族结果为 Unknown。补充的同家族 MiniMax 匿名评审随机映射为甲=A、乙=B，选择 **A**；两侧都记住顾冬岚、顾安澜、雪耳、韩晏竹、沈夏珩、陆岳霖、石彦棕、白敬原。判官认为 A 的人物行动差异更鲜明，B 的前五章更集中在兄妹救援、雪耳借力和守矿棚。B 没有胜出，未调参数重跑。

A/B 生成、两侧人设兑现、匿名偏好和缺陷后果判定合计 6 组账本：生成 118 条调用，评测 4 条调用；无缓存折扣的保守总估价约 **$0.1931**。所有失败和重取都保留。

## Evidence：验证与边界

- 指定全套命令：`PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/`：1471 passed、1 skipped、761 subtests passed。
- `tools/check_scope.py`：PASS；bookengine/deai 无交叉导入，不 import bestseller，文件行数合规。
- `tools/real_library_smoke.py`：4 books、107 requests、20 exports、20 reader chapters、0 models started、`fixture_unchanged=true`。
- `python -m ruff format --check .`：232 files already formatted；本条涉及的 Python 文件另跑 `ruff check`，All checks passed。
- 三个源目录与只读副本最终执行 `diff -qr` 无差异；只读副本没有用户可写文件。
- 未修改 `engine/deai/`、`engine/tools/webui/`、`docker-compose.yml`、`engine/Dockerfile` 或 `books/`；未 push。

## Inference

- 三本既有书的换名盲测仅 10%–30%，说明候选人物间台词可区分度偏弱；但判官同源，不能把绝对比例当成人类读者正确率。
- voice 的可核兑现明显高于 flaw、bottom_line、catchphrase，支持“先把缺失字段送到写手并做稀疏排期”的方向；是否提升阅读体验仍以 A/B 和人工阅读为准。
- 反派名字出现多不等于反派写得好；现有数字只证明存在和显式台词，不证明威胁感、主动性或记忆度。
- 这次 B 在逐字口头禅和“缺陷造成后果”两个窄指标上改善，但整体兑现计数和同源盲评都没有胜过 A，不能据此宣称人物系统已经提升阅读质量。

## Unknown

- DeepSeek 凭据被服务端拒绝，因此三本基线和 A/B 的真正跨家族判官结果 Unknown。没有把同家族结果冒充跨家族结果。
- 五章正文没有同时提供 20 段显式归属对白，因此 A/B 换名猜对率都是 Unknown；没有降低最低门槛凑数字。
- 程序检测和模型引文判定不能代替真实读者验收。
- 未运行全仓 `ruff check` 作为验收门槛；该仓已有大量历史 lint 报告，项目约定的门槛是 `ruff format --check`。本次涉及文件的新增 lint 问题已清除。
