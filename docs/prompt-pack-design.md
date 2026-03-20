# Prompt Pack 设计说明

## 结论

可以，而且应该这么做。

长篇小说系统不应该只有一套通用 prompt。更合理的结构是：

- `writing_profile`
  - 决定平台、节奏、卖点、人物引擎、文风
- `prompt_pack`
  - 决定某一类小说特有的写法规则、反例、planner 约束、writer 约束、review 约束

两层叠加，才能既保留通用能力，又让不同题材真正写出“那个类型该有的味道”。

## 参考的 GitHub 设计

### 1. NovelGenerator

仓库：

- <https://github.com/KazKozDev/NovelGenerator>

重点参考了它的三层拆法：

- `genrePrompts`
- `styleConfig`
- `promptRegistry`

对应启发：

- `genrePrompts`
  - 按题材维护 focus areas、writing guidelines、common pitfalls
- `styleConfig`
  - 把禁用词、写作规则、节奏规则集中管理
- `promptRegistry`
  - 统一注册所有 prompt 模板，避免到处散落

这套做法说明：题材规则和风格规则，应该从单个 prompt 文本里抽出来，作为可复用资产管理。

### 2. AI Novel Prompter

仓库：

- <https://github.com/danielsobrado/ainovelprompter>

重点参考了它的产品方向：

- 有“standard prompts”概念
- prompt 是可管理对象，不是硬编码一次性字符串

对应启发：

- prompt 需要有 catalogue / registry
- prompt 需要有面向用户的“可选项”
- prompt 需要能围绕故事元素管理，而不是脱离项目状态单独存在

## 当前系统的实现

这次已经补了 `Prompt Pack`：

- 目录：
  - `config/prompt_packs/`
- 加载器：
  - `src/bestseller/services/prompt_packs.py`
- CLI：
  - `bestseller prompt-pack list`
  - `bestseller prompt-pack show <key>`
- 使用入口：
  - `project create --prompt-pack`
  - `project autowrite --prompt-pack`

每个 pack 文件包含：

- `key / name / version`
- `description`
- `genres / tags`
- `source_notes`
- `anti_patterns`
- `writing_profile_overrides`
- `fragments`

其中 `fragments` 目前支持：

- `global_rules`
- `planner_book_spec`
- `planner_world_spec`
- `planner_cast_spec`
- `planner_volume_plan`
- `planner_outline`
- `scene_writer`
- `scene_review`
- `scene_rewrite`
- `chapter_review`
- `chapter_rewrite`

## 为什么这样设计

### 1. 通用画像不够细

`writing_profile` 可以回答：

- 这本书写给谁
- 节奏快还是慢
- 主角是什么 archetype
- 金手指是什么

但它不能很好回答：

- 末日囤货文前三章一定要怎样立钩
- 仙侠升级文怎样设计“境界压制 -> 抢机缘 -> 立威”
- 女频拉扯文怎样安排关系阶段变化

这部分必须落到 `prompt_pack.fragments`。

### 2. reviewer 也必须题材化

如果只有 writer 是题材化的，而 reviewer 还是通用的，就会出现：

- writer 在努力写“末日囤货”
- reviewer 却只按“连贯、冲突、字数”在看

结果是“像小说，但不像这个类型的好小说”。

所以 pack 也进入了 review / rewrite。

### 3. pack 文件比 if/else 更容易扩展

如果题材越做越多，纯 Python 分支会很快失控。

文件化后：

- 新增一个题材，多数时候只新增一个 yaml
- 评测时可以直接对不同 pack 做 A/B
- 更适合后续做编辑后台或页面配置

## 当前内置的 pack

- `apocalypse-supply-chain`
  - 末日囤货升级流
- `xianxia-upgrade-core`
  - 仙侠升级夺机缘
- `urban-power-reversal`
  - 都市异能反转流
- `romance-tension-growth`
  - 感情拉扯成长流

## 下一步建议

当前这层已经能用，但离“完善”还有三步：

1. 给 Web Studio 加可视化 pack 说明
2. 给 benchmark 增加“同 premise 不同 pack”的对比评测
3. 把 `prompt_version` 从固定值升级成 `pack-version + template-version`

这样 Prompt Pack 才会从“可用”走向“可运营、可评测、可迭代”。 
