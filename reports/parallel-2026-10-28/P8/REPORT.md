# P8：EPUB / DOCX 导出修复

**范围。** `par/P8`，基线 `refs/tags/e2e17`（`419181a41f29363263cee8a12d1694ace0bcef24`）。应用代码仅改 `src/bestseller/services/exports.py`，新增合成书稿测试；KDP 自动复用修复后的 builder，无需修改其服务。未改禁改文件、主工作区、生产数据；未合并、推送。

**修复。** XHTML 声明 `epub` 命名空间；EPUB 正文改用 XHTML 序列化，闭合换行/分隔标签并处理 HTML 命名实体。OPF 增加唯一 `dcterms:modified`，使用生成时的 UTC，格式 `YYYY-MM-DDTHH:mm:ssZ`；回归测试固定为 `2026-10-28T01:02:03Z`，不声称默认 ZIP 字节可复现。DOCX 仅去除开头与书名相同的 H1，保留生成的 Title、其他章节标题及正文中的同名文本。

**测试 Evidence。** 先确认 **5 failed / 3 passed**，修复后新增 **8/8 passed**；最终 **105/105 passed**（97 项既有回归 + 8 项新增，6.72s）。直接导出及真实 KDP 文件打包均逐项解析全部 XHTML/OPF/NCX/XML，验证目录命名空间、修改时间、ZIP；DOCX 验证标题次数、正文和粗体保留。测试选择见 [test-selection.txt](test-selection.txt)，结果见 [tests-red.log](tests-red.log)、[tests-final.log](tests-final.log)。全部夹具为合成内容；测试库配置仅解析校验，进程禁止数据库与网络连接。既有 PostgreSQL/SQLite 实库、队列集成测试按本任务禁用数据库/Docker 的要求排除。首次回归为 104 通过、1 项缺 Key 配置失败；最终仅给该配置提供离线占位值，未调用模型。新增测试 Ruff 通过；源码相较基线无新增 Ruff 问题，`git diff --check` 通过。

**fa14 离线 Evidence。** 正式 Markdown 输入 SHA-256：`5ae9a96c10c963a0c770902fe494b2c674a5b7acef5156ff39045a3c3331c1c9`，与 P6 一致且验证前后未变。直接 EPUB 与 KDP EPUB 各 **4 个 XML 成员解析通过**（无 NCX），目录与 modified 检查通过；直接 DOCX 与 KDP DOCX 均 **书名 1 次、非空输入行完整保留、XML 错误 0**。全部产物 SHA 和结果见 [fa14-offline.json](fa14-offline.json)。书稿转换产物仅存仓库外权限 0700 的本机私有目录，未入 Git；报告不含正文。

**边界 / Unknown。** fa14 的 KDP 验证只做格式检查，使用假持久化和 `strict=False`，不代表满足 KDP 投稿条件。PATH 与常见安装目录未发现 EPUBCheck，未联网下载；XML/内容检查不等于 EPUBCheck、平台或人工排版验收。未使用 Docker、数据库、模型调用；内置盘各步骤均高于 5GB。独立只读复核未发现阻塞；初次夹具配置失败日志予以保留。
