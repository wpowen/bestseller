# 离线质量评测 harness

`src/bestseller/services/offline_quality_eval.py` 提供一个不依赖 LLM、网络、数据库和人工服务的书稿静态评测器。它读取一个 JSON manifest 中的章节文件与事实清单，生成 `report.json` 和 `report.md`，用于开发期回归与精简提示词/生产提示词 A/B 对照。

## 运行

```bash
python3 scripts/offline_quality_eval.py \
  --manifest path/to/manifest.json \
  --output output/offline-quality/<run-id>
```

章节可以写成：

```json
{
  "arms": {
    "lean": {
      "prompt_variant": "lean",
      "prompt_hash": "sha256:...",
      "chapters": [{"chapter": 1, "path": "lean/001.md"}]
    },
    "production": {
      "prompt_variant": "production",
      "prompt_hash": "sha256:...",
      "chapters": [{"chapter": 1, "path": "production/001.md"}]
    }
  }
}
```

manifest 可选字段：

- `quality.min_chars` / `quality.max_chars`：结构长度范围；默认 1800–3500 字。
- `facts.protagonist`、`facts.required_terms`、`facts.forbidden_terms`：事实一致性检查。
- `facts.characters`：角色到辨识性标记词列表，用于检查标记覆盖和重复。
- `golden_three.hook_keywords`、`golden_three.payoff_keywords`：黄金三章的可核对兑现词（hook 只在第 1–2 章查找，payoff 在第 3 章查找）。
- `reader_promise.keywords` 与 `reader_promise.stages`：读者承诺在开篇/中段等阶段的覆盖率。

## 结果解释

评测包含结构画像、AI 味规则命中、事实一致性、黄金三章、角色辨识度和读者承诺。`inconclusive` 表示证据不足（例如缺章节、没有事实清单），不能当作通过；缺失文件会原样保留在报告的 `chapters[].error`。

`commercial_validation.status` 固定为 `not_tested`。本 harness 不会把静态规则分数冒充真实读者盲读、编辑评审、留存/付费或榜单数据。要验证“行业顶尖榜单级别”，必须另行补充盲读包、编辑评分和真实行为指标。

该报告适合在同一事实、同一章节集合下比较 `lean` 与 `production` 两个版本；A/B 结论只说明静态指标变化，不等同于商业胜负。
