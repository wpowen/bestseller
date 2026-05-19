# 30章完整故事模型试点

## 目标

用同一本 30 章完结型中文网文样书，对比模型和框架的真实短板。

本试点不做“无限连载开局”，而是强制检查：

- 第 1 章是否快速建立死亡倒计时/核心规则/主角行动动机
- 第 10 章是否完成第一轮规则反杀
- 第 20 章是否揭出关键真相并抬升最终冲突
- 第 30 章是否回收主线、情感线、核心规则和幕后人动机
- 整轮是否发生 LLM fallback；发生 fallback 的结果不能用于模型横向比较

## 默认模型

配置文件：`examples/model_pilots/short_complete_30.yaml`

默认启用：

- `minimax-m27`: `openai/MiniMax-M2.7-highspeed`
- `deepseek-official`: `deepseek/deepseek-reasoner`

官方 DeepSeek 直连使用 `DEEPSEEK_API_KEY` 和 `https://api.deepseek.com/v1`。

两个 planner 在本试点中使用 `max_tokens=32768`，避免 30 章章纲被 16K 输出上限截断。

## 运行

列出试点：

```bash
uv run bestseller model-pilot list
```

跑默认两个模型：

```bash
uv run bestseller model-pilot run short-complete-30 \
  --variant minimax-m27 \
  --variant deepseek-official \
  --slug-prefix pilot30
```

报告输出：

```text
output/model-pilots/short-complete-30-YYYYMMDDHHMMSS.json
```

每个模型对应一个独立项目，slug 会带模型变体和时间戳。

## 评估口径

每个变体记录：

- `chapter_count`: 是否达到 30 章
- `final_verdict`: 整书审校是否 `pass` 或 `attention`
- `review_overall_score`: 整书一致性评分
- `review_resolution_completeness`: 完结收束度，当前基线 `>= 0.85`
- `export_status`: 是否导出 `project.md`
- `usage.fallback_count`: LLM fallback 次数，当前基线必须为 `0`
- `usage.model_counts / role_counts`: 各角色实际调用模型和次数

解释报告时优先看：

1. fallback 是否为 0
2. 30 章是否完整产出
3. 完结收束度是否达标
4. 整书评分和 findings 数
5. 再看正文可读性、人物声线、章节钩子和中段疲劳

## 当前框架优化判断

如果两个模型都差，优先怀疑框架：

- 规划层是否只生成“事件清单”，没有明确每 5-10 章的承诺/兑现/反转
- 30 章完结故事是否仍被长连载 prompt 带偏，导致结尾像“下一卷预告”
- 审校是否偏结构合规，缺少“读者是否想追下一章”的强评估
- rewrite 是否只修局部句子，没能处理章节目标错误或中段节奏塌陷
- 同一模型是否在 planner/writer/critic/editor 全角色自我说服

如果 MiniMax 差、DeepSeek 好，优先调模型或角色分工：

- Planner 用 DeepSeek / Qwen，Writer 用 MiniMax 或 Kimi
- Critic/Editor 固定用不同模型，避免同模型自评
- 保留 MiniMax 做快写，但让强模型做 story bible、章纲和整书诊断

如果 DeepSeek 差、MiniMax 好，优先检查：

- 官方 DeepSeek provider / API 参数是否与模型偏好匹配
- `max_tokens` 是否足够，是否有截断
- 是否需要降低 temperature 或关闭不适合的 streaming

## 下一步质量提升

建议把试点结果反推成三条工程改进：

- **规划前置验收**：先评估 book spec / volume plan / chapter outline，不合格不进入正文。
- **独立盲评审**：新增一个与写作模型不同的 judge 变体，按同一 rubric 评开篇抓力、中段推进、结尾还债、人物声线、设定使用率。
- **完结型故事专用 profile**：30 章需要短篇闭环节奏，不应复用 1000 章连载的扩张逻辑。
