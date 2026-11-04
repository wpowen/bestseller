# 数据来源与采用边界

核验日期：2026-11-01。仅从官方仓库原始文件核对内容和 LICENSE；提交由官方 GitHub API 的默认分支提交查询确认，下面均固定到实际读取的 SHA。本文记载文件所声明的许可及本次采用范围，不推断 README 徽章或侧栏能覆盖 LICENSE。

`borrowed_style.yaml` 的中文标签、说明与正则为本次独立撰写的概括，不含第三方小说原段、上游脚本、提示词整段或禁用词表。不引入旧框架流程代码；数据存在不代表核心已接入、模型已遵循或读者已认可。正则只作候选定位，须在上下文复核后局部修订，英文混入另走已有流程。

## 1. PenglongHuang/chinese-novelist-skill

- 固定提交：`f9dc39ddbb7179f40911b327aa6102a050b91665`（官方默认分支 master）；该提交 README 展示版本 `2.3.2`，未据此推断 release tag。
- 实际许可：MIT，Copyright (c) 2026 PenglongHuang。完整许可保留在本文末尾。
- LICENSE：https://raw.githubusercontent.com/PenglongHuang/chinese-novelist-skill/f9dc39ddbb7179f40911b327aa6102a050b91665/LICENSE
- README：https://raw.githubusercontent.com/PenglongHuang/chinese-novelist-skill/f9dc39ddbb7179f40911b327aa6102a050b91665/README.md
- 十三类钩子：https://raw.githubusercontent.com/PenglongHuang/chinese-novelist-skill/f9dc39ddbb7179f40911b327aa6102a050b91665/references/guides/hook-techniques.md
- 对白指南：https://raw.githubusercontent.com/PenglongHuang/chinese-novelist-skill/f9dc39ddbb7179f40911b327aa6102a050b91665/references/guides/dialogue-writing.md
- 文风基准的负面清单出处：https://raw.githubusercontent.com/PenglongHuang/chinese-novelist-skill/f9dc39ddbb7179f40911b327aa6102a050b91665/references/flows/phase2-planning.md
- 负面清单实际内容（AI 写作痕迹清除）：https://raw.githubusercontent.com/PenglongHuang/chinese-novelist-skill/f9dc39ddbb7179f40911b327aa6102a050b91665/references/guides/chapter-guide.md

采用：将十三类章末悬念各自概括成一个新中文标签，写入 `hook_types`；将对白目的、人物区分、潜台词、动作与话语、节奏和权力关系概括成 `dialogue_notes`。把重复解释、空泛总结、堆砌修饰等原则写入 `novel_notes`，据此自写少量窄范围 `sentence_patterns`（含本次自写的俗套意象候选），不是上游自带检测器。该提交明确要求结合上下文判断，故不把常用词、否定句或单句段变为硬禁令。不采用上游小说示例、流程、字数门槛、评分或重写轮次。

## 2. voocel/ainovel-cli

- 固定提交：`1c4fd8b3f9f2ade663b238cb2bf1ea7ac7d43bcf`（官方默认分支 main）。
- LICENSE：https://raw.githubusercontent.com/voocel/ainovel-cli/1c4fd8b3f9f2ade663b238cb2bf1ea7ac7d43bcf/LICENSE
- README：https://raw.githubusercontent.com/voocel/ainovel-cli/1c4fd8b3f9f2ade663b238cb2bf1ea7ac7d43bcf/README.md
- 实际 LICENSE 为 **Apache-2.0**；同提交 README 的许可段写 MIT，存在不一致，本次以 LICENSE 为准。未以侧栏标签作为授权依据，也未推断有可用的 MIT 版本。

仅借鉴设计：确定性流程判断、渐进细化远期规划、按伏笔/人物/状态变化/关系查找相关旧章；以自写的三条概念笔记记入 `design_notes.ainovel_cli`，供核心工作者参考。本次未复制代码、提示词、配置、禁用词表或其他表达性内容，也未读取其禁用词表。该词表未获明确 MIT 确认，**不采用**；这里没有把 Apache 内容重新标为 MIT。

## 3. ExplosiveCoderflome/AI-Novel-Writing-Assistant

- 固定提交：`41b4130fa7abc1d84a552a73dfad494bc4ed5bab`（官方默认分支 main）。
- LICENSE：https://raw.githubusercontent.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant/41b4130fa7abc1d84a552a73dfad494bc4ed5bab/LICENSE
- README：https://raw.githubusercontent.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant/41b4130fa7abc1d84a552a73dfad494bc4ed5bab/README.md
- 实际 LICENSE 开头声明双许可：默认社区许可 **AGPL-3.0-only**，并声明服务式商业使用需另向维护者取得商业授权；文件随后附 AGPL v3 正文。本次只记录原文件声明，不认定已取得商业授权。

仅借人物/伏笔状态为后续写作提供参考这一通用概念，写成 `design_notes.ai_novel_writing_assistant` 的一条独立笔记。未复制其代码、文档措辞、提示词、规则表或小说文本，不引入该项目组件。

## 4. lieflat-less-ai-tone：沿本地 NOTICE 定位

先读取主工作区 `../../third_party/lieflat-less-ai-tone/NOTICE.md` 与同目录 `LICENSE`（路径相对于本 worktree 根）。该目录确实只有归因与许可；本地 NOTICE 指出的小说规则实际位于主工作区 `../../src/bestseller/services/novel_expression_guidance.py`。本次只读该文件的小说表达建议，没有 import、复制或调用其旧框架函数。

- 本地 NOTICE 固定上游提交：`27d29232f10124db904ca9c0536d0b67cb3b2833`；本次以此版本核验，不切换到当前默认分支。
- 实际许可：MIT，Copyright (c) 2026 shiujan；本地 LICENSE 与该固定上游 LICENSE 字节一致。完整许可保留在本文末尾。
- LICENSE：https://raw.githubusercontent.com/larashero3-dotcom/lieflat-less-ai-tone/27d29232f10124db904ca9c0536d0b67cb3b2833/LICENSE
- 本地规则不是上述 LICENSE 的内容，而是本仓库已有的小说适配。相应本仓库基准提交为 `7f27dc76b714b060a78b7d65b7a8bb8d83c50459`；实际规则文件：
  https://github.com/wpowen/bestseller/blob/7f27dc76b714b060a78b7d65b7a8bb8d83c50459/src/bestseller/services/novel_expression_guidance.py
  （本次证据为本地只读文件，未声称该 URL 可公开访问。）

采用范围：仅自写概括其中与小说有关的“避免空总结和同义复述、保留人物口吻与必要修辞、创作与表达修订的边界、保留事实条件与段落、歧义不明时不改”，进入 `novel_notes` 与使用说明。未提取上游通用文章规则，未安装上游工具、检测或评分脚本。MIT 声明针对原上游，不将本仓库适配代码另行宣称为 MIT。

## 5. 用户明确授权的本仓库配置副本

来源均为本 worktree 的 `config/`，保持原字节复制到 `engine/data/`，不改源文件，不执行其中旧流程或模型配置。来源基准提交 `7f27dc76b714b060a78b7d65b7a8bb8d83c50459`；复制前这四个源文件均无 Git 改动。沿用本仓库的权利与使用范围，不虚构第三方 MIT 授权。下列 URL 标识本仓库源码位置，未核验公开可访问性。

| 副本文件 | 精确来源 |
| --- | --- |
| `concept_tournament.yaml` | https://github.com/wpowen/bestseller/blob/7f27dc76b714b060a78b7d65b7a8bb8d83c50459/config/concept_tournament.yaml |
| `concept_title_formulas.yaml` | https://github.com/wpowen/bestseller/blob/7f27dc76b714b060a78b7d65b7a8bb8d83c50459/config/concept_title_formulas.yaml |
| `hook_one_liner_formulas.yaml` | https://github.com/wpowen/bestseller/blob/7f27dc76b714b060a78b7d65b7a8bb8d83c50459/config/hook_one_liner_formulas.yaml |
| `appeal_reference_openings.yaml` | https://github.com/wpowen/bestseller/blob/7f27dc76b714b060a78b7d65b7a8bb8d83c50459/config/appeal_reference_openings.yaml |

`appeal_reference_openings.yaml` 当前只有注释和空模板，未包含小说开篇原文；复制没有补造榜单语料，也不代表已有榜单对标证据。若以后加入作品摘录，仍须另核实具体内容的授权。

## 未确认与未采用

ainovel-cli 的 MIT 授权及禁用词表的 MIT 许可未确认，因此未采用该表。AGPL 项目没有取得商业授权，因此仅留通用概念笔记。除 chinese-novelist README 展示的版本文字外，其余来源未据提交推断版本号。实际运行接入、生成质量与读者验收由核心任务验证，本次无模型调用。

## 保留的 MIT 许可

### PenglongHuang/chinese-novelist-skill

```text
MIT License

Copyright (c) 2026 PenglongHuang

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### lieflat-less-ai-tone

```text
MIT License

Copyright (c) 2026 shiujan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
