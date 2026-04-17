# Writing — Mode B 写作规则

> writer 角色下笔时的基线。每个 scene / chapter 都必须同时满足 [quality.md](quality.md) 的 critic 检查。

## 1. POV / Tense（writing-profile 默认）

- 默认：**限知第三人称** + **过去式**
- 视角不无故切换。必要时（如揭示反派阴谋）用 < 200 字 "无人知……" 全知短段，独立段落呈现。
- 不同 genre 可覆盖（见 prompt pack `writing_profile_overrides`）。

## 2. Voice & Tone

| 维度 | 规则 |
|------|------|
| 基调 | 由 prompt pack 决定（如 xianxia 倾向冷峻；都市甜宠倾向轻松）|
| 节奏 | 短句为主；长句落在内心独白 / 回忆 |
| 描写 | 白描 + 通感；具体名物代替抽象形容词 |
| 心理 | 用**动作—停顿—动作**的间隙承载，避免"他心里想道"式贴标 |

## 3. Scene Structure

每 scene 遵循"entry_state → 冲突升级 → 转折 → exit_state"：

```
1 200–2 200 字
├─ 入口状态（上一 scene 的 exit_state，或章节 opening_hook）
├─ 冲突升级（信息 / 威胁 / 情绪三选一叠加）
├─ 转折（可以是外部 reveal 或内在抉择）
└─ 出口状态（为下一 scene 留勾）
```

## 4. Chapter Hook Rules

**开场 hook** 四选一：

- 未答之问
- 时限逼近
- 陌生者出现
- 身体失控细节

**结尾 hook** 四选一：

- 未答之问尾扣
- 新变量入场
- 已知事物的颠覆瞬间
- 身体信号异动

**禁用**章末出现 "睡觉 / 第二天清晨" 类松弛尾。

## 5. Cultivation / Power-Up Scene Rules

- 每次境界突破**必须**同时写出代价账（寿元 / 灵力 / 情感 / 关系）
- 功法发动须有可感知的外显：温度 / 光 / 声 / 味之一
- 不得 "速突破"：一次突破过程至少 400 字描写
- 颜色 / 符号应在 story-bible 锁定，全书一致

## 6. Dialogue Rules

| 项 | 值 |
|---|---|
| 对白比例 | 25–45%（按 pacing 调节：climax/reveal 偏低，breathe 偏高）|
| 功能 | 推进情节 70% / 塑造角色 30%；避免纯信息灌注 |
| 语气词 | 收敛；每个角色的 verbal_tics 按 voice_profile 一致 |
| 旁白插入 | 避免 "他心里想道"；改用动作 / 停顿承载心理 |

## 7. Worldbuilding Integration

- 每章至少**渗入 2 条**世界法则细节（非直白解释）
- 由角色动作 / 对白 / 观察自然带出；严禁 "信息倒斗"
- 提及新名物时附**一个具体感官细节**（颜色、重量、气味……）

## 8. Anti-Patterns（主动规避）

1. 括号内解释术语
2. 流水账 "然后……然后……"
3. 长段 "原来如此……" 回忆
4. 主角光环过盛：每战必有真实风险
5. 师父全知但憋着：该告知的要告知
6. 战斗只写招名
7. 用情绪词总结情绪（"愤怒" / "悲伤"）→ 让读者感受而非告知
8. 使用 "内心毫无波动 / 气得浑身发抖 / 仿佛开了挂" 类陈词
9. 穿越 / 系统 / 金手指类文本
10. 用 "主角" 二字指称角色

## 9. Scene Writer Prompt Skeleton

当扮演 writer 时，自我注入以下上下文再下笔（等同于 `build_scene_writer_context_from_models()`）：

```
[Tier 1 · 必带]
- chapter_contract：本章 goal / phase / conflict_phase / pacing / emotion
- scene_contract：本场 hook_type / scene_type / spotlight / entry_state / exit_state / estimated_words
- participant canon facts（仅与本场参与者相关）
- writing_profile：POV / tense / voice / taboo words

[Tier 2 · 视预算]
- 最近 3 场 scene_summary
- emotion_track 当前值
- 反派当前计划与阶段

[Tier 3 · 最低优先级]
- 整部 story bible 相关章节
- 检索命中的相关片段
```

## 10. Chapter Template（frontmatter 必填）

见 [templates/chapter-frontmatter.md](templates/chapter-frontmatter.md)。最小集：

```yaml
volume: <N>
chapter: <N>
title: "..."
slug: "..."
scenes: <N>
word_count: <>= 5000>
status: approved | draft | rework
revision: 1
chapter_phase: hook|setup|escalation|twist|climax|resolution_hook
conflict_phase: survival|political_intrigue|betrayal|faction_war|existential_threat|internal_reckoning
pacing_mode: build|accelerate|climax|breathe
emotion_phase: compress|release
scores: {hook_strength, conflict_clarity, emotional_movement, payoff_density, voice_consistency}
contract: {main_plot_progress, subplot_progress, emotion_shift, hook}
generated_at: ISO-8601
```

## 11. 写完后三步

1. **自查字数** ≥ 5000。不足立即扩写。
2. **自我 critic 打分**（参照 [quality.md](quality.md) 的 5+4 维）。任一维 < 0.70 触发 editor 重写。
3. **追加 canon facts / timeline**（参照 [knowledge.md](knowledge.md)）。
