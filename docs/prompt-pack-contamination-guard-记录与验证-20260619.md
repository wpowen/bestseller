# 跨题材 prompt-pack 污染守卫 — 记录与验证（2026-06-19）

## 背景 / 触发事件
新建书《修仙2.0：现代修仙》（喜剧，平台标题「喜事公关」，slug `urban-xiuxian-2-0-1781786787`，
genre=`都市修真·职场升级流` / sub_genre=`修仙2.0`）跑出来是一本**悬疑权谋探案**：
因果账簿 + 监察二处围剿倒计时 + 红鲱鱼/线索公平 + 源头之谜，**零喜剧**。

用户判断「是不是被污染了，因为这个设定是之前书籍的设定」——经排查，**确认污染**。

## 诊断（file:line + 证据）
- 项目 `metadata.prompt_pack_key = suspense-mystery`、`metadata.genre_skill_profile.profile_key = suspense-mystery`
  （DB 实测）。这是**前作探案书《青囊不语问阴阳》《青崖诡事》同一个悬疑 pack**。
- `category_key = urban-contemporary`（对）、当前路由 `infer_default_prompt_pack_key('都市修真·职场升级流','修仙2.0')
  = urban-cultivation-2.0`（对，该路由 2026-04-17 commit a87fc8e 即已存在）。**只有 prompt_pack_key 被错设成悬疑。**
- 根因：[writing_profile.py](../src/bestseller/services/writing_profile.py) `resolve_writing_profile` 中
  `resolve_prompt_pack(pack_key or auto_prompt_pack_key or preset, ...)`——
  **explicit `pack_key`（构思阶段 LLM 产出的 writing_profile.market.prompt_pack_key）优先级高于 genre 路由**。
  构思 LLM 把 market.prompt_pack_key 漂移成了 `suspense-mystery`（框架历史主导的探案方法论），
  覆盖了本该命中的 `urban-cultivation-2.0`，再经 `build_project_metadata`（writing_profile.py:375/400/406）
  钉进 metadata + genre_skill_profile，**污染全链**：premise / world_spec / cast / 12 卷纲 / 已写 21 章。

## 修复（soft / additive，单点中央汇聚处）
`resolve_writing_profile` 加**跨题材污染守卫**：
- `genre_route_key = auto_prompt_pack_key or preset_prompt_pack_key`（保持原 auto>preset 顺序，不重排非 explicit 路径）。
- 当 explicit `pack_key` 与 `genre_route_key` 矛盾时，**genre 路由权威**，explicit 仅作「无识别路由题材」的兜底；
  记 warning，不抛错（soft）。
- 保留策展 preset 的合法显式 pack（与 genre 路由一致时不动）。

> 取舍：故意让「与本书题材矛盾的 explicit pack」失效——这类 pack 几乎必为跨书污染而非用户主动选择；
> 真要换 pack 应改 genre/sub_genre 或 preset，而非注入矛盾 pack。

## 三层验证
- **L1 单测**（`tests/unit/test_prompt_pack_inference.py` 新增）：
  - 污染场景 `都市修真2.0+suspense-mystery → urban-cultivation-2.0`；
  - 另一 urban 题材 `都市异能+psychological-thriller → urban-power-reversal`（仍归本家族）；
  - genre 一致的 explicit 保留；未识别题材 explicit 兜底保留；无 explicit 走 genre 路由。
- **L2 回归**：`test_prompt_pack_inference / test_writing_presets_services / test_prompt_pack_services /
  test_genre_skill_profiles / test_project_services / test_genre_consistency / test_genre_unbinding /
  test_conception_services` 共 **153 passed**。
  - 关键回归点：`test_rule_survival_writing_profile_uses_suspense_prompt_pack`——
    规则生存书（无 explicit pack）仍正确得 `suspense-mystery`（修复初版误把 preset 提到 auto 前，已纠正）。
- **L3 真机**：在含修复代码的容器内跑**真实建书代码路径** `build_project_metadata`，喂入被污染的
  explicit profile（market.prompt_pack_key=suspense-mystery）+ genre=都市修真·职场升级流/修仙2.0，结果：
  - `metadata.prompt_pack_key = urban-cultivation-2.0` ✅
  - `genre_skill_profile.profile_key = urban-cultivation-2.0` ✅
  - `genre_skill_profile.prompt_pack_key = urban-cultivation-2.0` ✅

  即污染书里被写成 suspense-mystery 的那几个字段，新建链路已全部落正确。
  （部署：需 Docker rebuild 让运行栈用上修复代码——用户惯常的构建流程。）

## 影响面
- 不动既有污染书《喜事公关》（用户指示「只修框架不动这本」）。
- 对所有**未来新建书**生效：任何 explicit/LLM 漂移的 pack 不再能覆盖题材自身路由。
