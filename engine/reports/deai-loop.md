# 整本写作与独立去 AI 味实跑 · 2026-11-04

**技术闭环已跑通；质量与单章速度尚未全达标，未标为全部验收通过。**《证词里，我是凶手》5章共12,269汉字已交付。原始5章保留；必要剧情补完另存 `completed-draft/`，deai只读这版并写其 `final/`，保真指标基于补完稿。

| 章 | 汉字 输入→最终 | 检测候选句 前→后 | 实际改句 | 秒 / 请求 |
|---|---:|---:|---:|---:|
| 1 | 2497→2511 | 2→0 | 2 | 158.9 / 69 |
| 2 | 1935→1935 | 4→3 | 1 | 180.0 / 92 |
| 3 | 2523→2523 | 1→1 | 0 | 47.1 / 22 |
| 4 | 2528→2529 | 3→2 | 1 | 116.2 / 58 |
| 5 | 2771→2771 | 4→4 | 0 | 215.0 / 119 |

原生成8分42秒/16次；本版处理11分57秒/360次、1,972,830 tokens、$0.39103。**本书含旧版试验和补完累计$1.229、869次请求，开发调试跨度2小时1分，不能算成20分钟无人值守完成。**全轮已知估价$2.098；保留134条历史无usage请求后保守上界$2.863/$3，未释放未知。采用[MiniMax公开标准价](https://platform.minimax.io/docs/pricing/overview)，不计缓存折扣。

保真5/5：段落/实体/数字检查、逐条修改回放均通过；5章末段逐字保留。0.2.2终检从原检查点应用，撤销第5章无改善的动作扩写，零新增请求；缺字段/空正文/漏列段落的恢复不当成“无问题”。无凭据续跑5章全部cached，请求360→360。目录watch发现新增章、单Markdown路由与干净中断以真实结果离线重放验证。413测试＋637子测试通过；bookengine源码0行改动、两包互不导入。

问题：①候选仅14→10，3/5章实际改动，不能据此宣称读起来已无AI味；原稿的程序办事/推理可信度仍待抽读。②第5章215秒超180秒目标，输入2771字超2700参考上限71字，均保留记录。③整场对白/演员法A/B均回滚，尚未证明收益。盲读13篇已输出，KEY只在private；不改默认写手，不推进页面、生产或千章。

HTTP复核：旧r2为72次422内容拒绝、62次429、0次5xx/413；其中48次为重复拒绝请求。旧记录不能证明实际并发因果；新旧输入不同，不是同条件因果实验。新传输限6并发/120RPM，网络统一10/60/300/600秒退避、至少900秒；422不反复重发同一请求。新1385次HTTP均200；空正文/截断/字段问题单列，未混为HTTP成功后的“零问题”。校准仍为留出4/5、Claude候选0；小样本不是人工质量结论。

[完整终稿](/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/scratch/engine-books/webui/e9b3b44e-c77f-453d-a7bb-fe4f0f2d4af4/completed-draft/final/book.md) · [逐章保真与命中](/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/scratch/engine-books/webui/e9b3b44e-c77f-453d-a7bb-fe4f0f2d4af4/completed-draft/final/SUMMARY.md) · [盲读文件](/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/scratch/engine-books/blind-5/BLIND.md)

```text
bookengine_lines=4984 (limit=5000)
deai_lines=789 (limit=800)
webui_lines=0 (limit=1200)
exporter_lines=0 (limit=400)
max_file_lines=365 (bookengine/batch.py)
imports_bestseller=no
cross_imports=none
forbidden_keywords=none
PASS: package limits and independence
```
