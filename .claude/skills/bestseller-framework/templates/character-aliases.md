# character-aliases.yaml Template

> 位置：`output/ai-generated/{slug}/story-bible/character-aliases.yaml`
> 谁读：[character_alias_canon](../../../../src/bestseller/services/character_alias_canon.py)
> 何时写：`PLAN_CHARACTERS` 状态产出 `characters.md` 时，**必须同时**产出本文件。
> 何时更新：任何时候确定新角色 / 引入合法别名 → **追加**新条目（不删旧条目）。
> 何时不动：不要把"未在原文出现过的可能拼写"塞进 aliases；空着即可，将来出现再补。

## 用途

- 锁定每个角色的唯一 canonical 名字
- 列出 critic 允许出现在文本里的所有 alias 拼写
- 显式声明哪些**易混拼写**必须指向其他角色（防止"周元 vs 周元青"那种漂移）

## Schema

```yaml
characters:
  - canonical: <规范名，必填，单一字符串>
    aliases:
      - <别名 1，列表，至少包含 canonical 自身>
      - <别名 2>
    forbidden_collisions:        # 可选
      - <这个拼写不许出现在本角色的别名里>
    notes: ""                    # 可选；备忘字段，不影响校验
```

## 示例（《道种破虚》）

```yaml
characters:
  - canonical: 宁尘
    aliases: [宁尘]
    notes: 主角

  - canonical: 陆沉
    aliases: [陆沉]
    notes: 主角挚友，杂役峰内部消息源

  - canonical: 苏瑶
    aliases: [苏瑶, 苏管事, 苏师姐]
    notes: 杂役峰管事

  - canonical: 周元青
    aliases: [周元青, 周公子]
    forbidden_collisions: [周元]   # 严禁简写为「周元」
    notes: 苏瑶表兄；锦衣纨绔

  - canonical: 周元
    aliases: [周元, 周师兄]
    forbidden_collisions: [周元青] # 与周元青是完全不同的人
    notes: 青云宗年轻一代第一人，筑基后期

  - canonical: 周霸
    aliases: [周霸]

  - canonical: 韩九
    aliases: [韩九]
    notes: 周霸"师兄"，炼气七层

  - canonical: 叶长青
    aliases: [叶长青, 叶长老]
    notes: 筑基期长老
```

## 写作约束（来自 [quality.md § 4.5 C]）

writer 写每一章前必读本文件，**只能使用** canonical + aliases 中的拼写。
critic 调 [validate_chapter_name_canon](../../../../src/bestseller/services/character_alias_canon.py) 校验：
- 文本含 `forbidden_collisions` 集合里的名字 → must_rewrite
- 文本含未登记的 2-3 字 Han 名（出现 ≥ 3 次）→ must_rewrite

如确为新角色：追加 entry 而非放任未登记拼写。
