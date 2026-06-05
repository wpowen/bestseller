# Gate 盘点(2026-06-05)— R3

> 目标:盘清 85 个 `*_gate*.py` 的真实激活状态,纠正"只有 14 个接线、76 个死 gate"的高估,
> 对真正孤儿给出 wire/archive 建议。

## 0. 结论先行(纠正先前分析)

先前分析称"90+ 闸门只有 14 个接线、其余是伪保障"——**这是高估,予以更正**。
按"是否在生产代码(src/bestseller,排除 tests/docs/config)被 import/调用"重新盘点:

| 分类 | 数量 | 含义 |
|---|---|---|
| **REGISTRY**(运行期可阻断) | 12 | 在 `gate_registry._GATES` 里,失败会 block+rewrite/warn |
| **CALLED**(生产调用,非 registry 阻断) | 67 | 被 pipeline/worker/planner/reviews 等真实调用(诊断/评分/装配) |
| **ORPHAN**(生产代码零引用) | **6** | 定义+测试+文档齐全,但管线从不调用 |

即 **79/85 活跃,真正孤儿只有 6 个**(不是 76)。gate 体系的问题不是"大面积死代码",
而是**分层不清**(哪些该 block vs 哪些只诊断)+ 少量孤儿 + 个别确定性 gate 题材硬编码(见 R1)。

## 1. REGISTRY(12)— 运行期阻断闸门

`write_safety_gate`、`l2_bible_gate`、`fanqie_long_ranking_gate`、`anti_meta_gate`、
`chapter_splice_coherence_gate`、`material_referential_integrity_gate`、
`chapter_outline_readiness_gate`、`chapter_predraft_quality_gate`、`qimao_opening_gate`、
`phase_d_time_gate`、`material_advancement_gate`(core);
`ai_flavor_gate`、`show_dont_tell_gate`、`signature_audit_gate`(advanced,warn-only)。

> 分层(core 阻断 / advanced 仅告警 + local/structural)由 `gate_registry.py` 单源管理,
> 是为修复"青囊 ch1 开篇闸门锁死整本书"的事故而建,方向正确,无需改。

## 2. ORPHAN(6)— 真孤儿,需 wire 或 archive

| Gate | 行数 | 主函数 | 建议 |
|---|---|---|---|
| `book_creation_readiness_gate` | 568 | `evaluate_book_creation_readiness` | **wire 候选**:建书前就绪校验,应接进建书 API/CLI;待产品确认 |
| `book_lifecycle_quality_gate` | 1091 | `evaluate_book_lifecycle_quality` | **wire 候选**:整书生命周期质量,体量大疑为未完成特性;待产品确认 |
| `emotion_contract_gate` | 258 | (无标准 evaluate_) | **review**:情绪契约,可能被 emotion_choreography 取代;确认后 archive 或 wire |
| `entry_system_gate` | 307 | (无标准 evaluate_) | **review**:入场体系;与 entry_system 域是否重复 |
| `material_content_gate` | 16 | — | **archive 候选**:16 行,疑为占位/早期残留 |
| `wave4_gate_suite` | 29 | — | **archive 候选**:29 行套件壳,无生产调用 |

> 处置原则:`material_content_gate`/`wave4_gate_suite` 体量极小且零引用,优先 archive(移到
> `attic/` 或删除);`book_creation_readiness`/`book_lifecycle_quality` 体量大、像未接线的成型特性,
> **不应擅自删**,应由产品决定是否接进建书/整书流程。这是一个**产品决策点**,非纯工程清理。

## 3. CALLED(67)— 生产活跃(节选关键)

`bible_gate`(8)、`premium_book_gate`(11)、`write_gate`(11)、`planning_readiness_gate`(7)、
`quality_gates_config`(8)、`chapter_length_gate`(5)、`outline_specificity_gate`(5)、
`length_stability_gate`(5)、`reverse_outline_gate`(6)、`common_sense_gate`(4)、
`commercial_novel_gate`(4)、`retention_safety_gate`(4)、`timeline_consistency_gate`(4) …

> 这些都在跑。其中 `common_sense_gate`/`hook_echo_gate`/`exposition_density_gate`/
> `commercial_novel_gate`/`character_role_gate` 等含**题材硬编码探案词**(正则/关键词),
> 对非探案书造成**漏检(coverage gap)而非误阻断**——见 R1。

## 4. 行动项

1. **产品决策**(非工程):`book_creation_readiness_gate`/`book_lifecycle_quality_gate` 是否接线。
2. **工程 archive**:`material_content_gate`(16行)、`wave4_gate_suite`(29行)零引用,建议归档。
3. **工程 review**:`emotion_contract_gate`/`entry_system_gate` 是否与现有编排/域重复。
4. **R1**:CALLED 里的题材硬编码确定性 gate 去探案化(独立任务)。

> 复跑盘点脚本:见本次 session 的 gate 分类脚本(grep registry name vs prod import)。
