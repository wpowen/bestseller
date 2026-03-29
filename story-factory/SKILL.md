# Interactive Fiction Story Factory Skill

## Goal

批量生成可导入 `LifeScript` 的互动式爽文物料，并保证以下四点同时成立：

- 有小说性，不是纯结果判定
- 有交互性，且选择有清晰代价、收益与风险
- 有攻略感，用户能看到公开路线与已走路径
- 有工业化结构，内容可批量扩写、校验和编译

## Output Contract

每次生成都必须产出一个 `story_package.json`，包含这 6 层：

1. `book`
2. `reader_desire_map`
3. `story_bible`
4. `route_graph`
5. `walkthrough`
6. `chapters`

编译后必须得到：

- `book_<id>.json`
- `chapters_<id>.json`
- `walkthrough_<id>.json`
- `qa_report.json`

## Hard Rules

### Product Rules

- 结构采用 `主干 80% + 局部分支 20%`
- 多数分支在 `1-3` 章内回收
- 每章 `2-4` 次交互，默认 `2` 或 `3`
- 每次交互 `2-4` 个选项，默认 `3`
- 每章必须至少推进剧情、关系、信息中的两项
- 每章结尾必须给下一章动机

### Choice Rules

- 选择决定“怎么做”，不是直接决定“赢不赢”
- 每个选项必须填写：
  - `visible_cost`
  - `visible_reward`
  - `risk_hint`
  - `process_label`
- 每个选项必须包含 `result_nodes` 或有效 `result_node_ids`
- 重要选择必须有 `1-3` 个过程 beat
- 选择后必须出现：
  - 立即反应
  - 执行过程
  - 延迟回声

### Readability Rules

- 每个场景只做一件主事
- 每段只服务一个核心情绪
- 信息遮蔽不能代替悬念
- 角色台词必须有区分度
- 用户要始终知道当前问题是什么

### Genre Rules

- 都市逆袭：公开打脸、身份反转、资源跃迁
- 修仙升级：等级门槛、资源争夺、阶段破境
- 悬疑生存：规则压力、信息差、倒计时
- 职场商战：证据链、话语权、策略拆招
- 末日爽文：资源争夺、安全区权力、尸潮倒计时、公开立威、阵营吞并
- 情感关系：边界试探、互相救场、阶段升级

## Chapter Card Format

每章都必须先满足这张卡，再写正文：

```yaml
chapter_goal:
primary_emotion:
public_summary:
ending_hook:
interaction_count:
route_count:
```

## Choice Card Format

```yaml
choice_text:
visible_cost:
visible_reward:
risk_hint:
process_label:
result_nodes:
state_change:
relationship_change:
```

## Validation Checklist

上线前必须逐项检查：

1. 是否有明确核心情绪
2. 是否有明确主问题
3. 是否至少有一次真实差异选择
4. 是否展示了过程，而非秒结算
5. 是否保留了攻略图公开信息
6. 是否存在下一章动机
7. 是否控制在题材允许的节奏内
8. 是否没有超出 `2-4` 选项和 `2-4` 交互的默认边界

## Usage

1. 先复制 `templates/story_package.template.json`
2. 填写完整物料
3. 运行 `scripts/compile_story_package.py`
4. 把编译结果导入 `LifeScript-iOS/Sources/LifeScript/Resources`
