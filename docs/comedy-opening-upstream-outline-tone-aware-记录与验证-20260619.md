# 喜剧开篇上游治本:章纲黄金开篇题材感知 — 记录与验证(2026-06-19)

## 背景(承接写手层修复)
写手层三引擎题材感知(commit f988cc1)+ 禁开篇说明书(cf2e044)部署后,重 seed《福星甩不掉》
invariants 为低压池,**实测发现张力仍在**——ch1 场景卡 `key_dialogue_beats` 写死
「接电话→AI冷读规则→威胁」,细纲 opening_situation=「匿名电话威胁」、hook_type=countdown。
即张力**烤在更上游的章纲生成层**,写手层修复必要但不充分。

## 根因(章纲层黄金开篇硬约束题材盲)
`_outline_prompts` / `_volume_outline_prompts`(planner.py)注入黄金三章硬约束:
- ZH:`render_golden_opening_rules_block()`(webnovel_method_cards.py)读 config
  `golden_chapter_rules`——**「从全书最有冲突的地方开写/身处危机」+ info_release_priority=
  [危机感, 人设, 金手指, 世界观]**(危机优先,题材盲)。
- EN:内联硬规则「Start at the highest-conflict point … crisis > characterization > golden-finger」。

喜剧 pack 的「低压力开篇」指引虽在 prompt 里,但被这些**危机优先**硬约束 + hook_ledger/
anti_commonsense_hook 等张力引擎淹没,LLM 给治愈喜剧生成了倒计时威胁开篇。
且 `key_dialogue_beats` 在**同一个 ChapterOutlineBatch LLM 调用**里生成 → 场景卡 beats 也随之紧张。

## 修复(soft / additive)
- `render_golden_opening_rules_block(low_pressure=False)`:低压力题材返回**喜剧版黄金开篇**——
  暖日常开场/严禁危机倒计时/金手指用具体(最好好笑)事件演出来·禁系统客服AI朗读规则/
  信息优先级=温度·笑点 > 金手指(演) > 人设目标 > 世界观/每章≥3笑点+1治愈/2/3前禁高强度冲突。
  低压块为**硬编码 config 无关**(config 坏也能渲染)。
- `_outline_prompts` + `_volume_outline_prompts`:计算 `_outline_low_pressure =
  is_low_pressure_tone(pack_key, genre, sub_genre)`,传入 ZH 黄金块;EN 内联硬规则同款题材条件化。
- 因 beats 与章纲同批生成,本修复**同时覆盖细纲 opening_situation/hook + 场景卡 key_dialogue_beats**,
  无需单独改场景卡层。

## 三层验证
- **L1**:`test_webnovel_method_cards.py` 新增 3 测试(低压换喜剧规则/默认保留危机优先/低压块抗 config 损坏)。
- **L2**:webnovel 10 passed;planner/outline 相关回归(见提交记录)。
- **L3 真机**:rebuild 部署后重规划《福星》ch1-3,核验新 opening_situation/scene beats 从
  「匿名电话+AI冷读规则」→暖日常+show金手指,A/B(见提交后)。

## 影响面
- 对所有喜剧/治愈/低压力书的**章纲生成**生效(未来新建书从细纲层就不再烤张力)。
- 张力题材(仙侠/悬疑/玄幻)保持危机优先黄金开篇,零回归。
- 配套写手层修复 [[comedy-retention-tone-blind-architecture]]:开篇池+钩子+可见损失+禁说明书。
