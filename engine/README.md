# bookengine · S1–S5a

独立的 Python 包；从 mini.py 改造，只读题材与写作知识，不加载旧引擎。
完整阶段计划见 [PLAN.md](PLAN.md)，当前验证 S5a 的三百章与五本并发。

在本 worktree 根目录执行（使用已安装 PyYAML、python-docx 的 Python）：

```sh
PYTHONPATH=engine python3 -m bookengine engine/examples/realistic-life.yaml \
  --env ../../.env --out ../engine-books/s1-realistic-life
```

一本书只需一个 YAML：`prompt_pack`、`chapters`、`words_per_chapter`、`user_input`。
默认从本 worktree 的 `config/` 读取知识，可用 `--assets` 指定只读目录。
调用模型固定为 MiniMax-M3.1-Flash，API 地址与密钥只从 `--env` 读取；密钥只在内存。
默认请求超时 180 秒，可用 `--timeout` 调整。

相同命令重启会复用每一步检查点及已保存的模型回答；YAML 或加载的知识变化时应换输出目录。
中断未返回的请求可重发并计入调用数；服务端未返回的 tokens 无法计量。
格式或空返回每次启动最多三次尝试，空返回降低推理。网络故障独立累计，统一等待 10、60、300、600 秒；
累计退避 970 秒后仍会再试一次，再失败才停止。覆盖瞬间连接失败、断线、超时、408、429 和所有 5xx。
服务端 Retry-After 更长时遵守它。每次实际 sleep 不超过 30 秒；等待截止时间落盘，重启先完成剩余等待。
修复失败原因后必须沿用现有输出目录重启，历史失败继续计入统计。服务端内容拒绝直接停止，不自动重试。

四次规划调用（3 方案、选择、圣经、全部细纲）；每章写、审、可选段落改写、摘要状态；
全书一次连续性阅读直接给出段落修补。正常五章共 20–25 次调用，失败重试另计。
只替换审校点名的段落，未点名段落保留；连续性修改也限定为完整段落；使用章号与段落号定位，空替换表示删除该段，旧原文引用检查点仍可读取。
输出目录有 `book.md`、`book.docx`、分章正文、`steps/`、`responses/`、`calls.json`、`run.json`。
运行统计包含实际发送次数、已知 tokens 和从首次启动开始的耗时（含停机时间）。
书稿及模型回答保留在指定的 scratch 目录；不要加入 Git。

```sh
PYTHONPATH=engine python3 -m unittest discover -s engine/tests -v
python3 engine/tools/check_scope.py
```

S1 不声明 S3 字数带、程序连续性检查或人工阅读质量验收已完成。


## S2 批量命令

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=engine python3 -m bookengine \
  --batch engine/examples/s2/batch.yaml --jobs 6 \
  --env ../../.env --out ../engine-books/s2
```

manifest 引用六个独立的单书 YAML：三题材各 5/10 章，目标每章 2200 汉字。
每本在独立子进程运行，日志追加到对应 `book.log`；每 5 秒显示进度、调用、tokens、耗时、失败/重试和每章字数。
最终汇总写 `batch.json`。相同命令跳过已经完成的书，其余从原检查点继续；修复后不要换目录重写。
字数只计量，不在 S2 修正。任一本失败不会中止其它书；批量命令有未完成书时返回非零。

实跑前已只读核对账户请求速率：M3/M3.1 共用 200 RPM、10,000,000 TPM；
语言模型并发栏为“—”，不代表已经获得独立的无限并发承诺。本例最多六个在途请求。
订阅还有 5 小时/周用量窗口及高峰动态限流；账号其它工具也共享额度。
来源：[账户控制台](https://platform.minimax.cn/console/rate-limits)、
[官方限流说明](https://platform.minimax.cn/docs/guides/rate-limits)、
[订阅用量说明](https://platform.minimax.cn/docs/m-plan/usage-rules)。

成本是[公开按量价格](https://platform.minimax.io/docs/pricing/overview)的美元折算，非订阅实际账单：
每百万 tokens 输入 $0.15、输出 $0.60、缓存读取 $0.015。
同时提供缓存折算和不扣缓存折算；无 usage 的失败调用单列，并以输入 UTF-8 字节数、输出 token 上限保守计价。
只要缺少估价所需字段，就明确保留未知，不把未知当零费用。


## S3 六本质量与长度实跑

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=engine python3 -m bookengine \
  --quality --batch engine/examples/s2/batch.yaml --jobs 6 \
  --env ../../.env --out ../engine-books/s3
```

同一批六份 YAML、同样题材/章数/用户输入；输出另存 s3，修复后仍运行原命令续跑。
正文目标 2200 汉字，范围1870–2530（不含标题、空白和标点），提示约定2–3场。
写、审、可选定点改写、最终摘要与状态仍为每章最多四个正常步骤；超/欠字数仅额外修正一次，
不以修正后仍不合格为理由循环请求。base_calls、length_calls、repair_calls、retry_calls 分开计量。

程序扫描含汉字的规范化整句（含短句），第三次及以后重复标出当前段落；九条精确套话从只读
prose_style_anchors.yaml 的反AI声口规则挑选，不按宽泛情绪词一刀切。
审校同时提取带段落号的状态事件，程序用来定位离场未回、异地在场、物品两处、时间倒退。
明确的进入/移动和回忆不按矛盾处理。末尾状态提取按最终正文重新记人物/物品/时钟/关系/伏笔开收。
这些检查覆盖已抽取的明确事件，不等于穷尽正文的所有语义矛盾；引文无法落地的事件单列，不能当已验证。
有问题合并进一次段落改写，全书再一次通读并定点修补；导出前按最终正文重播事件、重查长度与重复。
残留程序问题最多三次局部修复，只发送点名段落及按章去重的旧状态；不再通读或重写整章。
三次后仍有告警就如实保留并报告，不能把“已导出”当成“验收通过”。

所有S3模型输入（含题材、用户输入、正文、说明与JSON结构）不超过48000个Unicode字符，
优先保留本章与当前状态，旧摘要和上一章正文按段落裁减；全书通读不截断必需正文。
实际每次 input_chars 与每书 max_input_chars 可从统计查看，此上限是字符数而非声称精确token数。
run.json 的 quality 保存每章检测结果，chapter-NNN.state.json 与 state.json 为状态表。
六本全完成后生成 READ-ME-FIRST.md，仅含各书首尾章正文、书名与章号，供人工抽读。
程序通过与用户读感验收分别报告。

## S4 一百章长篇

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=engine python3 -u -m bookengine \
  --long engine/examples/s4.yaml --env ../../.env --out ../engine-books/s4
```

全书总纲一次，25章卷纲在十章规划窗口首次需要时才展开；章纲只展开当前十章。
每章草稿、合并审查修改、可选程序定点修复、精简状态至多四个正常步骤；一次字数修正与重试另计。
精简状态不存引文/段落号，状态格式最多两次请求，失败沿用上一章并留下标记。
字数修正只调用一次：模型给独立段落候选，程序实测字数选择所需部分，保留未选段原文；
若审改后才越界且尚未修过字数，再使用这唯一一次机会，并保护已有状态锚点段。
上下文上限30000 Unicode字符，按本章纲、上章结尾、相关状态、近三章摘要、卷纲、其余裁剪；
每十章压缩滚动摘要。失败章节记下后继续，重新运行同一命令恢复已完成步骤。
每十章progress.log写累计耗时/保守公开价成本/本章草稿输入。前30章每十章检查一次：
累计平均大于180秒/章或按当前成本推算100章大于5美元则停止，等待用户决定。
全部完成后导出Markdown/DOCX，并收集第1、2、50、99、100章供抽读。
收尾只发送残留问题章或点名段的相邻上下文，进行一次局部校准；真实事件不能撤销以消除冲突。
事件结构与引文、修后状态仍需程序复检；未解决的问题保留在验收指标，不把导出成功等同于验收通过。
run.json保存S3失败分布、各章输入、状态对照、伏笔开收与实际验收指标。

## S5a 三百章与五本并发

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=engine python3 -u -m bookengine \
  --long --batch engine/examples/s5a/batch.yaml --jobs 5 \
  --env ../../.env --out ../engine-books/s5a
```

书内顺序、书间独立进程。全局滚动60秒限速200RPM/10000000TPM；输入按UTF-8字节上界加输出上限预约，失败重试也预约。
每章写作后提取摘要与状态，第1章、每10章及末章抽检；只有同时异地、死人再出场、时间倒流会触发一次定点修。
审查/状态宽松解析、缺字段默认；无法解析直接降级，不为校验重调模型。状态失败沿用上章，网络继续原退避。
短段仅合并空行后编号，逐字保留；一次字数修正仍单列。其他检查只作提示，冲突靠行动、人物选择和代价推进。
伏笔使用稳定F编号，活跃上限12；每10章压缩时合并同义项，各卷指定要收条目。末10%只收不开，收束必须对应已开。
已闭合伏笔留在serial-meta.json，不进入活跃状态；人物缺席超过20章、失效物品移入archive.json及分章归档。
归档实体重现仍参与程序检查，失效物品不能复活；精简state.json上限12000字符。
本轮长篇前50个新增章节检查平均耗时，超过90秒/章停止；新调用失败率超过10%也停止，短篇不套用长篇耗时阈值。
revision单列新指标，历史计量保留。

当前r3混跑命令将manifest换为engine/examples/s5a/mixed-r3.yaml、jobs设为3，沿用s5a目录。
跨日信号或过夜后的钟点回绕按次日处理。每25章写进度，十章记忆压缩独立保留。
止损按长篇任意滚动50个新增完整章及其调用计算，不把旧章或短篇当长篇速度样本；输入比较改为300/20。
使用同一输出目录续跑，原有正文、失败与费用持续计量；末尾抽读仅含长篇指定五章和短篇首尾正文。
