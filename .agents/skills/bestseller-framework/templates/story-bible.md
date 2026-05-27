# Template · Story Bible 六件套（必做）

> `output/ai-generated/{slug}/story-bible/` 下的六份基础文件骨架。
> 章数 > 50 再加 `act-plan.md`；卷数 > 3 再加 `world-expansion.md`。

---

## 1. premise.md — 前提与承诺

```markdown
# Premise — {书名}

## Logline（一句话）
> [一句，含主角+愿望+阻碍+代价]

## BookSpec

### Protagonist
| 字段 | 值 |
|---|---|
| name | ... |
| archetype | ... |
| external_goal | 外在目标（可被客观判定）|
| internal_need | 内在成长需求 |
| flaw | 致命缺陷 |
| strength | 关键优势 |
| fear | 最怕什么 |

### Themes
1. ...
2. ...
3. ...

### Reader Promise（不可违背）
- ...
- ...

### Stakes
| 层级 | 内容 |
|------|------|
| 个人 | ... |
| 关系 | ... |
| 世界 | ... |

### Three-Act Structure（单卷内部节拍）
| 位置 | 章 | 节拍 |
|------|----|------|
| 0–13% | ... | hook |
| 13–33% | ... | setup |
| 33–53% | ... | escalation |
| 53–73% | ... | twist |
| 73–87% | ... | climax |
| 87–100% | ... | resolution_hook |
```

---

## 2. world.md — 世界设定

```markdown
# World Spec — {书名}

## 1. World Name & Premise
- 世界名：...
- 世界前提：...（一段）

## 2. World Rules
| 名称 | 描述 | 故事后果 | 可利用性 |
|------|------|---------|---------|
| ... | ... | ... | ... |

## 3. Power System
### Tiers
| 等级 | 名称 | 特征 | 代价 |
|------|------|------|------|

### Hard Limits
- ...
- ...

### Protagonist Starting Tier
...

## 4. Locations（地点表）
| 地点 | 卷/章 | 描述 | 戏份 |

## 5. Factions（势力表）
| 名称 | 类型 | 立场 | 活跃卷 | 升级路径 |

## 6. Forbidden Zones（禁区 / 禁忌）
- ...
```

---

## 3. characters.md — 角色

```markdown
# Cast Spec — {书名}

## Protagonist
...（含 knowledge_state / voice_profile / moral_framework）

## Antagonist
### {主反派名}
| 字段 | 值 |
|---|---|
| public_identity | ... |
| true_identity | ... |
| external_goal | ... |
| internal_rationale | ... |（反派视角"我是对的"的理由）|
| flaw | ... |

#### escalation_path（作为 primary_force）
- Ch X：...
- Ch Y：...

## Antagonist Forces
| 名称 | 类型 | 活跃卷 | 升级路径 |

## Supporting Cast
### {配角名}
...（每人附 role / voice_profile / arc_trajectory / fate）

## Relationships / Conflict Map
| A | B | 关系 | 张力源 | 演化点 |
```

---

## 4. plot-arcs.md — 主/副/伏笔

```markdown
# Plot Arcs — {书名}

## 1. Main Arc
| 阶段 | 章 | 核心事件 | 出口状态 |

### 关键节点（ArcBeat）
1. Ch X 节点 A：...

## 2. Subplot A：...
| 阶段 | 章 | 核心事件 |

## 3. Subplot B：...
...

## 4. Clue → Payoff Table
| # | 埋点章 | 埋点内容 | 偿付章 | 偿付形式 |
| 1 | ... | ... | ... | ... |

## 5. Emotion Track（卷级）
| 章范围 | 情绪主调 | emotion_phase |

## 6. Obligatory Scenes（按 prompt_pack）
| 类型 | 章 | 内容 |
```

---

## 5. volume-plan.md — 卷级规划

```markdown
# Volume Plan — {书名}

## Volume 01 · {卷名}

| 字段 | 值 |
|---|---|
| volume_number | 1 |
| title | ... |
| volume_theme | ... |
| chapter_count_target | ... |
| word_count_target | ... |
| opening_state | ... |
| volume_goal | ... |
| volume_obstacle | ... |
| volume_climax | ... |
| volume_resolution | goal_achieved / cost_paid / new_threat_introduced |
| key_reveals | ... |
| foreshadowing_planted | ... |
| foreshadowing_paid_off | ... |
| reader_hook_to_next | ... |
| conflict_phase | ... |
| primary_force_name | ... |

## Win/Loss Rhythm
| 章 | 胜负 | 性质 |

## Chapter Phase Mapping
| 章范围 | 比例 | chapter_phase |

## Pacing Mode per Chapter
| 章 | pacing | emotion_phase |
```

---

## 6. writing-profile.md — 风格基线

```markdown
# Writing Profile — {书名}

## POV / Tense
- POV：limited_third / first_person / omniscient
- 时态：past / present
- 切换约束：...

## Voice & Tone
| 维度 | 描述 |

## Dialogue
| 项 | 值 |
| 对白比例 | 25–45% |
| ... | ... |

## Vocabulary Rules
### 鼓励用
- ...
### 慎用
- ...
### 禁用（taboo）
- ...

## Cultivation Scene Rules  ← 如修真 / 超能题材
- 每次突破必须带代价账
- ...

## Worldbuilding Integration
- 每章渗透 ≥ 2 条世界法则
- ...

## Hook & Ending
### 开场 hook 四选一
- 未答之问 / 时限逼近 / 陌生者 / 身体失控

### 结尾 hook 四选一
- ...

## Chapter Structure Template
- 4 场景为默认；climax/twist 章 4–5；breathe 章 2–3

## Anti-Patterns
1. ...
2. ...

## Per-Chapter Compliance Checklist
- [ ] 字数 ≥ 5000
- [ ] ...
```

---

## 追加：act-plan.md（> 50 章强制）

```markdown
# Act Plan — {书名}

## Act I: {幕名}（ch N–M）
- purpose: ...
- protagonist_arc_stage: ...
- world_state_at_start: ...
- world_state_at_end: ...
- key_scenes: [...]

## Act II: ...
...
```

## 追加：world-expansion.md（> 3 卷强制）

```markdown
# World Expansion — {书名}

## VolumeFrontier
| Vol | 新地域 | 新势力 | 新规则 | 首次出现章 |

## DeferredReveal
| # | 种植章 | 偿付目标章 | 类别 | 内容 | 状态 |
| 1 | ... | ... | mystery / prophecy / relic / lineage | ... | planted / paying-off / paid |

## ExpansionGate
| # | 开闸条件 | 开放的世界模块 |
```
