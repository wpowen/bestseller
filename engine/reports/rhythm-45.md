# 第 45 条：章型节奏重新规划

日期：2026-11-08  
分支：`feat/rhythm-45`  
真实书只读副本与试写目录：`/private/tmp/bestseller-f45-IHsGGz/`

## 结论

代码已把“程序套固定模板”改为“模型定向重规划”：每个不合规章最多参与两次节奏重规划；仍不合规时恢复该章进入修复链前的原章纲、记录原因并继续写作。章型内容增加确定性检查和每批一次的小模型语义复核；程序内部词会被拦截；体检增加章纲/正文实际章型不一致清单。

离线回归与完整测试通过。计划 15 章的真实试写被 MiniMax 连续两次 `HTTP 422 output new_sensitive (1027)` 阻断：前 4 章完整，第 5 章正文、完章提取和状态已保存，但章末复核未完成。故 15 章分布、一致率和最长连续对抗均为 Unknown，不能宣称真实验收通过。

## Evidence

### 实现

- 删除 `chapter_rhythm.py::_reframe` 固定句改写；代码中不再生成“原章前提”“原定场景”“已登场同伴”等模板文本。
- `outline_diversity.repair_batch` 逐章记录 `rhythm_replan_attempts`：达到 2 次仍失败即 `kept_original` 并锁定，不再让后续通用重写改动该章。
- 重规划提示包含目标章、要求章型、前后章摘要、本阶段已发生事实，并要求两场场景的冲突、障碍、选择和行动后果一致。
- 每批章纲追加一次低 effort、1800 token 上限的 `rhythm-review-v7` 复核；模型只分类，不改写。
- `rhythm_issues` 同时检查章型主要场景和程序内部词；保存数据不含完整章纲字段时不误当章纲检查。
- `book_checkup` 只增加一处导入和一处调用；新模块只读 `writing_outlines/`，输出 `chapter_rhythm.outline_alignment`，不改正文或检查点。
- 成功模型重规划记 `accepted_model_replan`；失败记 `kept_original`，两者都保留尝试次数。

### RED / GREEN 与项目门禁

- RED：新增第 45 条回归文件首次运行 `6 failed`，分别覆盖两次上限、仅改目标章、模板词、模型语义复核、前后章/阶段事实上下文、体检不一致。
- 定向 GREEN：`56 passed`；加入接受回执后相关回归 `36 passed`。
- 完整测试：`1522 passed, 1 skipped, 761 subtests passed`（34.30 秒）。
- `tools/check_scope.py`：PASS；最大 Python 文件 `bookengine/q1_plan.py` 499 行；无 `bestseller` import、无 bookengine/deai 交叉 import。
- Ruff 格式检查覆盖所有改动 Python 文件；报告不参与格式化。

### eval-45 保存数据离线复核

输入是源书 `/books/eval-45` 的系统临时只读副本。新检查器在保存章纲中检出 36 章含程序内部词；源报告中按更窄的固定句口径统计为 19 份，两者不是同一分母。新体检复现了原验证结论：

| 项 | 结果 |
|---|---:|
| 章纲章型分布 | 对抗 7、谋划 3、情感 19、揭秘 10、日常 6、收获 5 |
| 正文实际章型分布 | 对抗 30、揭秘 2、谋划 14、收获 2、关系 2 |
| 章纲/正文一致 | 12/50 |
| 不一致 | 38/50 |
| 正文最长连续对抗 | 7 |
| 章纲内部词命中章 | 36 |

同一只读代码路径也跑了另外两本临时副本：eval-39 一致 24/50、最长连续对抗 4；eval-33 一致 5/50、最长连续对抗 7。它们是旧书诊断基线，不是新流程生成结果。

### 真实模型试写（部分完成）

- 种子：eval-45 `seed.yaml` 的临时 15 章改写版；输出只在 `/private/tmp/bestseller-f45-IHsGGz/trial-15`。
- 第 1–10 章章纲后只调用一次 `plan-q1-rhythm-review-v7-001-010`（10.669 秒）。
- 批次出现 `redo`、`redo2`、`redo3` 三个阶段名，但逐章账本最大值为 2；`redo3` 只处理此前尚未各自用满两次的章。第 4–10 章达到两次后均锁定并恢复原章纲。
- 恢复时第 5–10 章后缀再次每章最多两次；所有 `rhythm_replan_attempts` 均为 2，没有第三次改写同一章。
- 调用账本：143 次；139 `ok`、2 `invalid_stage_output`、2 `http_error`；prompt 389,111 tokens、completion 226,179 tokens、total 615,290 tokens；两次 HTTP 错误无 usage。
- 完整章：4。部分书的正文实际章型为对抗 3、谋划 1；章纲/正文一致 1/4；最长连续对抗 3。这个口径只描述已完整落盘的四章，不能外推 15 章。
- 第一次 422 出现在 `r2-ch5-final-state`；同目录恢复后该阶段成功，但 `r2-ch5-momentum-hook-recheck` 再次返回相同 422。按项目错误边界停止，没有修改归属外的内容审核/重试代码。

### 三份模型重新规划章纲原文

以下直接取自临时响应 `steps/plan-q1-chapters-1-10-diversity-growth2-redo.json`；它们是模型重规划原文，不代表最终采用或读者验收。

```json
{
  "number": 2,
  "title": "断旗引路的三岔矿道",
  "conflict_type": "路径误判",
  "title_keywords": ["断旗", "三岔矿道"],
  "introductions": [],
  "structure": "分路试探",
  "required_event": "顾以宁根据断旗拖痕与裴夏珩留下的脚印选择正确矿道，并安排白元铮、顾冬岚与矿工撤离次序",
  "type": "追踪谋划",
  "payoff": "找到追兵去向并避开正面截杀",
  "hook_type": "人物选择",
  "hook": "顾以宁选择让顾冬岚带矿工先走，自己留下拖延追兵。",
  "ending_type": "两难选择",
  "ending_content": "顾以宁把唯一完好的引火绳塞进顾冬岚手中。",
  "scenes": [
    {
      "where": "塌矿岔口",
      "characters": ["顾以宁", "白元铮", "裴夏珩"],
      "goal": "判断裴夏珩撤往哪条矿道",
      "obstacle": "三路都有脚印，旗绳拖痕互相覆盖",
      "choice": "借白元铮感知追闻寒铁气味",
      "emotion": "冷静中带着试探",
      "kind": "action"
    },
    {
      "where": "废弃运石车",
      "characters": ["顾以宁", "白元铮", "顾冬岚"],
      "goal": "安排矿工撤离与断后次序",
      "obstacle": "主道易堵，支道遍布落石",
      "choice": "让顾冬岚领人走支道",
      "emotion": "牵挂而克制",
      "kind": "action"
    }
  ],
  "threads": ["L2", "L3"],
  "opening_conflict": "裴夏珩的脚印在三岔口同时消失，断旗拖痕却指向相反方向。",
  "callback_events": ["第一章守旗后裴夏珩砍断系绳"],
  "solution_type": null,
  "conflict": "顾以宁必须在错误痕迹中判断追兵去向并保住撤离队伍。",
  "opponent": "裴夏珩",
  "transition": "正确支道尚未打开，矿镇入口已经传来宗门火铃声。",
  "location": "暴雪矿镇矿道",
  "solution_target": "裴夏珩的踪迹",
  "solution_method": "借白元铮的感知辨认寒铁气味并核对拖痕",
  "rhythm_type": "谋划",
  "rhythm_focus": "两场均比较追踪方案、安排撤离分工并承担取舍",
  "rhythm_contract_version": 7,
  "core_application": "顾以宁借白元铮的感知辨认寒铁气味，以此排除伪脚印；感知一停，他便只能依靠残痕猜测。",
  "core_development": {
    "new_use": "在追踪中借用感知判断气味与路径。",
    "new_cost": "再次借力后，他记不起矿工围炉分汤时端碗的手势。",
    "new_limit": "本场只借感知，不能同时加快身法破除落石。"
  },
  "payoff_type": "追踪破局",
  "payoff_due": true,
  "growth_setup": true,
  "upgrade_due": false
}
```

```json
{
  "number": 3,
  "title": "热汤难暖旧袖口",
  "conflict_type": "信任裂痕",
  "title_keywords": ["热汤", "袖口"],
  "introductions": [],
  "structure": "坦白抉择",
  "required_event": "顾冬岚发现顾以宁失去温度记忆，拒绝他继续独自断后；顾以宁坦白借力代价并改变分工",
  "type": "关系转折",
  "payoff": "兄妹重建分工，顾冬岚不再被当作必须保护的对象",
  "hook_type": "关系行动",
  "hook": "顾冬岚把热汤倒回锅里，转身替顾以宁守住退路。",
  "ending_type": "关系裂痕",
  "ending_content": "顾冬岚第一次主动站到顾以宁与追兵之间。",
  "scenes": [
    {
      "where": "支道避风炉棚",
      "characters": ["顾以宁", "顾冬岚", "白元铮"],
      "goal": "让顾冬岚带矿工安全撤离",
      "obstacle": "顾以宁隐瞒了借力会夺走温度记忆",
      "choice": "顾以宁坦白每次借力的代价",
      "emotion": "羞愧而坦白",
      "kind": "action"
    },
    {
      "where": "支道窄口",
      "characters": ["顾以宁", "顾冬岚", "白元铮", "裴夏珩"],
      "goal": "改变原先断后与撤离的安排",
      "obstacle": "裴夏珩已经逼到窄口",
      "choice": "顾以宁借身法换顾冬岚先行",
      "emotion": "紧张中互相信任",
      "kind": "action"
    }
  ],
  "threads": ["L2", "L4"],
  "opening_conflict": "顾冬岚把热汤递来，顾以宁却盯着碗沿发怔，连自己为何手冷都想不起来。",
  "callback_events": ["第一章顾以宁破门后拒绝热汤与御寒衣物"],
  "solution_type": null,
  "conflict": "兄妹必须在隐瞒与坦白、保护与共同承担之间作出选择。",
  "opponent": "裴夏珩",
  "transition": "顾冬岚先行追入谷口，裴夏珩留下的血线却指向另一条路。",
  "location": "暴雪矿镇支道",
  "solution_target": "兄妹之间的信任",
  "solution_method": "顾以宁说明借力代价，顾冬岚据此提出并落实新的分工",
  "rhythm_type": "情感",
  "rhythm_focus": "两场以隐瞒、坦白和站位选择改变兄妹关系与行动分工",
  "rhythm_contract_version": 7,
  "core_application": "顾以宁借白元铮的身法抢先越过窄口，让顾冬岚按新分工撤离；身法中断后，他无法再独自拖住裴夏珩。",
  "core_development": {
    "new_use": "在撤离中以身法换取同伴先行。",
    "new_cost": "他永久失去围炉分汤时闻见热汽的熟悉感。",
    "new_limit": "一次只借身法，不能把速度分给顾冬岚。"
  },
  "payoff_type": "关系和解",
  "payoff_due": true,
  "growth_setup": false,
  "upgrade_due": false
}
```

```json
{
  "number": 6,
  "title": "矿车改道出镇口",
  "conflict_type": "封锁抢路",
  "title_keywords": ["矿车", "镇口"],
  "introductions": [],
  "structure": "借道突围",
  "required_event": "矿镇出口被裴夏珩封死，顾以宁改用废弃矿车冲开封锁并带矿工离开",
  "type": "突围行动",
  "payoff": "带领矿工与顾冬岚突破宗门封锁",
  "hook_type": "人物行动",
  "hook": "顾冬岚推着矿车挡住驶向雾骨兽谷的追兵。",
  "ending_type": "悬停动作",
  "ending_content": "矿车滚轮仍转，顾冬岚横刀站在车辕前。",
  "scenes": [
    {
      "where": "矿镇北门",
      "characters": ["顾以宁", "白元铮", "裴夏珩"],
      "goal": "打开被封死的矿镇出口",
      "obstacle": "宗门阵桩锁死闸门",
      "choice": "放弃破门，拆矿车撞阵桩",
      "emotion": "急迫中迅速取舍",
      "kind": "action"
    },
    {
      "where": "矿镇外坡",
      "characters": ["顾以宁", "白元铮", "顾冬岚"],
      "goal": "护送矿工冲出外坡",
      "obstacle": "阵桩爆出的寒气冻结轨道",
      "choice": "借身法将矿车推入下坡",
      "emotion": "紧绷而坚定",
      "kind": "action"
    }
  ],
  "threads": ["L2", "L4"],
  "opening_conflict": "宗门阵桩已经封死北门，矿工身后只剩一辆卸空矿石的旧车。",
  "callback_events": ["第二章安排顾冬岚先行", "第一章矿工获救"],
  "solution_type": null,
  "conflict": "顾以宁必须在不使用蛮力强破的情况下带矿工突破出口封锁。",
  "opponent": "裴夏珩",
  "transition": "矿车冲出矿镇后，顾冬岚发现裴夏珩并未追出镇口。",
  "location": "暴雪矿镇北门",
  "solution_target": "北门阵桩",
  "solution_method": "拆下矿车撞击阵桩，再借身法将脱轨车辆推入下坡",
  "rhythm_type": "对抗",
  "rhythm_focus": "两场围绕破封、冲坡和护送行动完成突围",
  "rhythm_contract_version": 7,
  "core_application": "顾以宁借白元铮的身法推动矿车越过冻结轨道；身法一停，他便无法阻止矿车侧翻。",
  "core_development": {
    "new_use": "将身法用于推动重物与紧急改道。",
    "new_cost": "他永久失去矿工围炉分汤时递碗给顾冬岚的画面。",
    "new_limit": "只能改变自身动作，不能把身法直接赋予矿车或矿工。"
  },
  "payoff_type": "突围救人",
  "payoff_due": true,
  "growth_setup": false,
  "upgrade_due": false
}
```

## Inference

- 删除固定模板并新增内部词检查，能防止程序再次自行制造已知模板句；真实模型仍可能生成别的空洞内容，需依靠内容校验和读者验收。
- `outline_alignment` 使用保存的正文章型提取结果，属于参考性 Inference，不是人工语义判定。它能稳定列出章号和两侧标签，但不能单独证明哪一侧“正确”。
- 部分试写显示模型在两次定向重规划后仍常未满足关系主章要求；“失败保留原纲且不阻断”行为已证实，但 15 章质量改善尚未证实。

## Unknown

- 15 章完整章纲分布、正文实际章型分布、一致章数、最长连续对抗：真实试写未完成，Unknown。
- 第 5 章以后正文质量、整本节奏改善：Unknown。
- 跨家族或人工读者验收：未做，Unknown。
- 422 内容审核是否会在同一素材的后续随机重试中消失：未继续付费重试，Unknown。

## 源书保护与边界

- 所有 eval-45、eval-39、eval-33 与第 42 条目录均先复制到新系统临时目录；检查和试写只使用副本。
- 未修改真实 `books/`，未修改 `engine/deai/`、`engine/tools/webui/`、人物/称呼或能力证据文件。
- 公共 `engine/tools/book_checkup.py` 仅增加一处导入和一处调用；具体逻辑在本条新模块。
- 未 push。
