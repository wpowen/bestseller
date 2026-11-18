# 第二阶段 68/E4：合同补齐、正文章型拉回与恢复（2026-11-17）

状态：代码与定向测试已冻结，等待主执行者全套回归；未部署、未调用真实模型、未修改真实书稿。

## 接缝 API

```python
from bookengine.rhythm_contract_repair import apply_rhythm_contracts

value = apply_rhythm_contracts(client, bible, value, state, chapter=start)
outline = apply_rhythm_contracts(client, bible, outline, state, chapter=idx, kind="writer")
# 随后由主线调用 apply_life_constraints，避免重新加入死者。

from bookengine.chapter_type_repair import repair_chapter_type

text, raw, failed, note = repair_chapter_type(
    client, prefix, text, raw, failed, state, bible, outline, extract_state, ask
)
```

`value` 接受单章、列表或含 `chapters` 的字典，返回独立副本。主执行者已接入 q1_plan 的 normalized 缓存出口和最终出口，以及 q1_chapter 的 with_story_duties 后、写作章纲保存前。写后替换原关系修复的一处调用，保留现有外层 guarded；没有同时再调用旧关系修复。本执行者仅只读核对主线，未编辑 Q1 文件。

## Evidence：实现与边界

- 先从 `bible.story_design.relationships` 取对应阶段的完整 people/event→cause/before/after。候选须与已登记人物、当前场景人物匹配；存在多个候选时，仅两场主戏共同出现的唯一人物对可消歧，不能任取第一对。
- 恢复合同后仍检查两场 action/choice。合同完整而主戏不符合要求时，也须定向补齐必要场景，不能凭关系元数据放行。来自设计的完整合同锁定不被模型替换。
- 无可靠完整来源时，用 engine 正常客户端补齐一个候选，仅采用 relationship_change，以及现有场景 goal/action/choice/result。人物和地点不许换，其他字段保留原值。无效输出明确失败；启用设计层的书在 writer 出口仍不合格则抛 ProjectError，先保存原因。
- 旧书兼容以实际业务约定为边界：没有 story_design 且没有 story_layer_version 时，新增合同补齐返回 `legacy_without_story_design`，不调用模型、不虚构合同、不强加写前阻断，`writer_ready=False`、`fulfilled=False`。旧书有效关系合同仍可走写后修复。纯排期的空 bible 夹具不会获得额外模型调用。
- 规划记录包含真实 `planned_type` 与 `planned_type_source`；合同恢复不等于正文已兑现。正文前后章型仅取提取结果的 `raw.type` 并归一别名；不能用 `chapter_type(raw)` 让 relationship_change 元数据覆盖提取的实际章型。
- 写后保留关系修复，增加对抗、谋划、揭秘、日常、收获的主场景局部修复。只允许指定段落；通常最多选 16 段，末段不提供编辑。一般路径及关系修复的 protected_tail=True 路径均要求至少 min(8, 可改段数) 段真正改变。原关系函数默认路径保持既有行为。
- 复检检查：实际提取章型、提取失败标志、已有关键事实逐字引文、已成立兑现、硬事实冲突、末段、段数、引号及正文清洁、配置字数区间与原稿 ±10%。失败返回原正文、原 raw、原 failed，并记录回滚原因。
- `rhythm_model.py` 使用 engine 原 stage/request/记账/响应与检查点恢复；生成一个有效响应后拒绝第二次候选请求，避免 Client.stage 的最低六次坏返回重试扩大本类修补次数。网络排队/传输等待继续采用 engine 既有等待语义。复提取是独立检查阶段，不冒充免费的本地检查。
- 独立记录位于 `rhythm-revisions/`。合同 revision 保留完整原章纲并按完整字典比较复用。正文采用结果保存完整 source（正文、raw、state、outline、bible、failed）、采用正文、checked、failed、note。同源且当前 `_guard` 仍通过时，新客户端可无模型调用恢复成功结果。
- `attempting/waiting` 可以沿原 stage 名恢复 engine 检查点和额度中断；`rolled_back` 为终态，不重复花费。成功修补后、完章落盘前中断，不会因 already_attempted 而丢失采用稿。
- 未改原设计、旧 steps、原正文或完成记录。新增模型阶段仍使用 engine 标准 steps/responses/calls；独立 revision 不替换历史输入。

## Evidence：真实 eval68 临时副本回放

原隔离书：`/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web68-books/webui/85ab9fc7-d694-4aba-9c80-7081467bdd47`。

测试复制该书设计、原 steps、章纲、规划缓存、完章记录和正文到系统临时目录，复制前后及测试结束逐文件比较原字节；真实来源和临时副本里的旧文件保持不变。禁止网络连接；模型采用离线客户端，部分复检走真正的 `q1_extract.extract_state` 与 engine 客户端控制流。

| 样本 | 保存的规划/提取 | 本轮验证 |
|---|---|---|
| 34 | 情感 / 谋划；关系合同 `{}` | 从阶段 4 唯一两场共同人物对恢复周铁匠、唐岚合同；原主戏仍偏离，使用一次离线定向场景补齐；保留完整合同与其他字段 |
| 44 | 情感 / 对抗；关系合同 `{}` | 阶段 5 多对人物不能可靠消歧，使用一次离线定向补齐及必要场景；写前复用，不重复调用 |
| 1 | 对抗 / 谋划 | 实际 raw.type 不匹配则回滚 |
| 6 | 揭秘 / 谋划 | 同上；保留跨段事实引文 |
| 8 | 收获 / 谋划 | 同上；保留兑现与事实 |
| 32 | 日常 / 对抗 | 采用保存的 continuity-state，不能误用较早 finish 的情感提取 |
| 34、44 写后 | 补齐合同后实际章型仍分别为谋划、对抗 | 用保存的实际提取回放；即使规划合同完整也不能伪装情感，均回滚 |

这些写后回放特意只做表面文字变化，复提取继续返回历史实际章型，从而证明修补不能靠标签或合同蒙混采用；不是新正文已修好的质量证明。此书没有 planned=谋划的章节，谋划目标的正例由独立夹具覆盖。

只读侧查另发现 `rhythm_skeleton.py` 的字段绑定可用骨架空合同覆盖详细规划的关系信息；已向主执行者报告，本执行者未修改该文件。新增 helper 在出口恢复有效合同，不把旧缓存整体重写。

## Evidence：TDD 与检查结果

初始空实现：16 个行为用例失败，覆盖空合同、正文不变、回滚与来源；随后实现。独立复核的“13 条合法声明编辑但仅 1 段实改”负例先复现错误采用，再增加实际改段数检查后通过。

最终定向检查：

```text
PYTHONPATH=.:tools ../.venv/bin/python -m pytest -q --no-cov \
  tests/test_rhythm_completion70.py tests/test_rhythm_resume70.py \
  tests/test_relationship_rhythm59.py tests/test_fix100.py \
  tests/test_outline_diversity.py tests/test_minimal_b.py \
  -k 'not test_minimal_b or noncombat'
76 passed, 10 deselected, 14 subtests passed

PYTHONPATH=.:tools ../.venv/bin/python -m pytest -q --no-cov \
  tests/test_rhythm_content50.py tests/test_chapter_rhythm39.py tests/test_rhythm_replanning45.py
44 passed

PYTHONPATH=.:tools ../.venv/bin/python tools/check_scope.py
PASS；forbidden_keywords=none；imports_bestseller=no；cross_imports=none

../.venv/bin/python -m ruff format --check \
  bookengine/rhythm_contract_repair.py bookengine/chapter_type_repair.py \
  bookengine/relationship_rhythm.py bookengine/rhythm_model.py \
  tests/test_rhythm_completion70.py tests/test_rhythm_resume70.py
6 files already formatted
```

真实 `Client` + 离线 HTTP 验证了非关系和关系修复 malformed 输出各仅产生一次 HTTP 调用与一条 calls 记录；重启后不重复。合同 malformed 同样仅一次。真实客户端额度错误后保存 waiting，新客户端充值语义回放沿同一 stage 采用候选；再次重启无 HTTP、无复提取地恢复采用结果。

自有 Python 文件行数：合同 helper 282，单次响应适配 54，正文 helper 302，原关系 helper 146；两份测试 443、148，均小于 500。为旧夹具报错而扩展授权的三个测试文件无需修改，失败由真实旧书兼容边界消除；其他执行者的测试变更未覆盖。

## Inference

定向证据支持本轮接缝、边界、次数约束及恢复机制有效；完整书的结构改善仍依赖模型能在事实和末段约束内写出正确主场景。限制不允许改标签或失败后无限重试，因此部分章可能保留原稿并明确记录修补未成功。

## Unknown

没有进行真实付费调用、新书生成、部署或读者验收。离线 approved 正例不等于新正文有可读性；对真实模型修补成功率与误判率未知。全套回归由主执行者在冻结版本上执行，本报告不把定向 120 项计作全套通过。
