# 第二阶段 68/E1：保留死亡事实，修订未写规划

## Evidence

- 新增 `bookengine/life_planning.py`、`life_planning_facts.py`；改动自有 `life_status.py`、`q1.py`；测试在 `tests/test_life_planning70.py`。没有编辑 `q1_plan.py`、`q1_chapter.py`、INBOX、生产书库或部署。
- 死亡事实从历史已完成且 `fact_source=final_body` 的完章记录读取，逐字对照所引章号的已存最终 body。保留最早死亡，兼容 `location.travel_status=死亡`；不依赖 `run.completed_chapters`，后续 snapshot 漏记死亡也不会丢失。引文不在 body 或没有已完成记录时不采用。
- 原圣经、`story_design`、旧步骤、已写正文和完章记录不覆盖。未写卷纲/章纲的修订源、死亡证据、两轮候选、拒绝原因及采用结果另存 `life-planning/*.json`。相同原规划与死亡证据恢复时复用已采用修订，不再次调用模型。
- 固定接口：`apply_life_constraints(client,bible,value,state,*,chapter,kind='outlines') -> value`，支持列表、单章与 `kind='volume'`；`validate_life_constraints(value,deaths) -> list[dict]` 返回冲突 path。所有定向重排经 engine 客户端；每次最多两轮候选，格式错误沿用客户端既有重试协议，原始调用与响应正常保留。
- 仅允许改死亡相关的未来字符串字段；人物列表中的死亡姓名仅能以 `value:null` 精确删除，按索引倒序应用。拒绝换名、遗体替换名单、空串、复活、假死、尸体站起、借背景词遮蔽发令、越界改其他字段。无有效修订抛 `ProjectError("项目规划无有效结果…")`，原检查点和候选保留，不把该规划交给写手。
- 义务 `id`、`*_id`、`*_ids`、字符串 thread 标识、历史 quote/source/status/evidence 与 callbacks 保留；对应义务 `text`、事件与行动仍接受检查。实际 `with_story_duties` 生成的 `villain_move` 已覆盖，保留义务 ID 并移除死亡名单、修订行动文本。
- Q1 每次选章前重新约束卷纲与内存 batch；已确认死亡者不再出现在存活/脱身要求中。进度滞后但完章记录存在时，仅补派生结算，不重新做创意规划，不覆盖完章记录；既有结算回归通过。

## TDD 与验证

- RED：先新增测试，模块缺失导致 collection error；后续负例分别复现背景词绕过、陌生姓名硬替换、死亡参与者无法删除、稳定 ID/历史引文被误当行动。实现后对应测试转绿。
- 最终定向回归：`test_life_planning70.py`、`test_life_status68.py`、`test_run_to_finish.py`、`test_chapter_pipeline.py`、`test_polish_resume.py`、`test_polish_resume_part2.py`、`test_hook_planning_42.py`，**102 passed / 35 subtests passed**（12.03s）；其中新增 life planning **28 项**。
- `check_scope.py` PASS；自有 Python 文件 Ruff format 检查通过，各文件均不超过 500 行。
- 真实书库 smoke：4 本 / 107 次请求 / 20 种导出 / 20 章阅读，`models_started=0`、`fixture_unchanged=true`，书库接口最大 88.74ms。仅在临时副本启动随机本地端口。
- eval65 实存证据：只读 `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web65n-ch50-copy` 的第 31 章 body 与完章记录，复制至 pytest 系统临时目录。确认顾夏珩死亡引文与 body 一致；在临时实例修订第 33 章的笑声行动。走真实 engine `Client.stage/request`，仅用离线传输响应替换 HTTP；验证 `calls.json` 和 `responses/001.json` 落盘，原始两份文件逐字不变。模拟 usage 不表示真实消耗，没有模型调用。
- 已只读核对主 agent 的四处接缝：normalized 缓存返回、最终章纲返回、卷纲返回、`with_story_duties` 后；最后一道生命约束位于 writing outline 保存和写手上下文构造之前。卷纲接口使用卷首章号时，中段死亡由 Q1 主循环当前 `idx` 再应用兜底。

## Inference / Unknown

- Inference：这些离线回归证明已知死亡和旧规划冲突会修订或阻断，且修订可恢复复用；并不代表模型会每次生成可采用的文学规划。
- Unknown：未运行付费模型、未新写整书、未部署，未做读者验收。语义与文学质量仍需真实续写和阅读判断；程序检查不是读者验收。
