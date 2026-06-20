# BestSeller 代码审计 — 验证与修复报告

> **本文档 v3**：原始审计（v2，13-agent 扫描）的**逐条独立验证 + 已确认问题的修复记录**。
> 原始报告把问题方向找得基本准，但**严重度普遍高估**，且有若干条经核对**不成立**。
> 下面给出每条的核验结论、被修复项的提交、以及被搁置项的理由。

- **验证方法**：44 个 agent 的对抗式复核工作流（每个 P0 一次初判 + 一次独立反驳复核；P1 按域复核）+ 人工抽样 + 对 P0-3 做真机 A/B（throwaway pgvector 容器）。
- **验证时间**：2026-06-20
- **修复提交**：`fadbb58`（batch1）、`44dd3e8`（batch2）、`7cb9cc4`（SC-4）、`33861af`（P0-13/P0-6）、`e9a7c4d`（P0-3）
- **回归测试**：新增 `tests/unit/test_audit_verified_bugfixes.py`（23 条全绿）。单元全量套件 **6921 passed**；本次改动**零回归**——余下 4 个失败均与本次无关：1 个依赖本地未提交 output 文件的环境性用例 + 3 个在基线 `59839fb`（本次工作之前）即已失败的既有用例（来自更早的题材体系提交，目标文件 judge-corpus / `novel_design_dossier.html` 均未被本次触碰）。

---

## 一、总体准确性结论

| 维度 | 原报告 | 核验后 |
|------|--------|--------|
| P0「阻断级」 | 18 项 | 实为 bug 的约 **6/17**；其中**真·P0 仅 2 项**（迁移引导 P0-3、Web 无鉴权 P0-13）；4 项**不成立**；其余 real-but-overstated |
| P1「高优先级」 | 67 项 | 约 **37/67** 为真实 bug；多数 severity 高估；约 12 项不成立 |
| 核心偏差 | — | 报告**机制描述大多准确**，但**忽略了下游守卫/DB 约束/创建期校验**，导致把"可能性"写成了"已发生的数据损坏" |

**一句话**：值得修的真问题存在且已修；但原报告的 P0 数量与"数据损坏/完全失效"措辞夸大，采纳前必须逐条核验下游约束。

---

## 二、P0 逐条核验（17 项）

| # | 文件 | 核验结论 | 是否 bug | 修正 severity | 处置 |
|---|------|----------|:--:|------|------|
| P0-1 | drafts.py version_no | **部分属实/不成真损坏** — 存在 read-then-insert 竞态，但 `uq_chapter_draft_version` 唯一约束 + `uq_chapter_draft_current` 偏索引在 DB 层兜底，最坏是 IntegrityError 任务失败，非"版本损坏" | 否 | P2 | 不修（DB 约束已防护）|
| P0-2 | models.py `lock_version` | **属实** — 字段全仓零读写，乐观锁未接线 | 是（死脚手架）| P2/P3 | 暂留（行为无变化，属设计意图未落地）|
| **P0-3** | migrations/0001 | **属实 · 真 P0** — 0001 渲染当前全量 metadata，fresh `upgrade head` 在后续迁移撞 DuplicateColumn | 是 | **P0** | ✅ 已修 `e9a7c4d`（真机 A/B 验证）|
| P0-4 | models.py 11 FK 缺 ondelete | **不成立** — 所有相关表都带 `project_id ON DELETE CASCADE`，整库删项目走级联，不会 ForeignKeyViolation | 否 | — | 不修 |
| P0-5 | contradiction.py chapter_number | **属实** — 引用不存在列 `chapter_number`/`description` → AttributeError 被吞 → 时间线数值矛盾检测**长期静默失效**（但仅哑火，不误报、不阻断）| 是 | P1 | ✅ 已修 `fadbb58`（JOIN ChapterModel）|
| P0-6 | world_expansion target=0 | **部分属实/输入不可达** — 算术属实，但所有建书路径强制 `target_chapters>0`，唯一直建路径 clamp≥30 | 否 | P3 | ✅ 加防御性 clamp `33861af` |
| P0-7 | retrieval.py `hash()` | **属实** — `hash(str)` 每进程加盐，重启后存量 embedding 与查询 embedding 不可比；但 hybrid 评分有 lexical 兜底，非全失效 | 是 | P1 | ✅ 已修 `fadbb58`（zlib.crc32 稳定哈希）|
| P0-8 | retrieval.py 全量加载 | **部分属实** — 确无 ANN 预过滤，500 章性能问题真实，但延迟数字夸大 | 是 | P2（perf）| 搁置（架构级，见 §五）|
| P0-9 | context.py history N+1 | **部分属实** — N+1 真实，但"6004 次查询"为夸大估算 | 是 | P2（perf）| 搁置（架构级）|
| P0-10 | 双 GateVerdict | **部分属实/非 bug** — 两份定义并存，但实际未观察到产生矛盾结论 | 否 | P2 | 搁置（统一契约，见 §五）|
| **P0-11** | retention_safety_gate fail-open | **部分属实** — 12 子检查全异常时确实静默放行（fail-open）；但需 12 个独立检查同时炸，且只影响章节质检不涉数据安全 | 是 | P2 | ✅ 已修 `44dd3e8`（错误计数 + 可见降级哨兵，告警而非硬阻断以免拖垮自动跑书）|
| P0-12 | world_law_gate 局部 schema | **部分属实/非 bug** — 局部类型与全局不完全一致，但 scorecard 实际可消费 | 否 | P2 | 搁置 |
| **P0-13** | web/server.py 无鉴权 | **属实 · 真 P0** — stdlib http 服务零鉴权，docker-compose 暴露 0.0.0.0:8787，任何人可建/删项目、触发管线 | 是 | **P0** | ✅ 已修 `33861af`（可选共享密钥门 + 默认 127.0.0.1 发布）|
| P0-14 | context.py compute_* 无测试 | **属实但非 bug** — 是测试缺口，不是缺陷 | 否（测试债）| P2 | 搁置（测试补全）|
| P0-16 | reviews.py 评分基线 | **部分属实/非 bug** — 基线偏移存在，但阈值与下游门控吸收，未观察到低质内容因此过关 | 否 | P2 | 搁置（需 A/B 校准，勿轻改）|
| P0-17 | reviews.py audit report=None | **不成立** — `is not None` 是**有意守卫**；正常 `passed=False` 仍会 block；report=None 仅在 audit 自身抛异常时（fail-open on exception），且另有独立 gate 兜底 | 否 | — | 不修 |
| P0-18 | reviews.py 重写死循环 | **不成立** — 函数非循环，每个调用方都有 `max_chapter_revisions`/`auto_repair_cap` 硬上限 + 收敛早退，耗尽即转人工 | 否 | — | 不修 |

---

## 三、P1 核验摘要（67 项 → 约 37 真实 bug）

**已修的 P1 真实 bug：**

| # | 问题 | 修复 |
|---|------|------|
| P1-EH-2 | 自动签约 contract+debt 分别 flush → 孤儿 contract | ✅ `44dd3e8` per-proposal `begin_nested()` 存档点 |
| P1-EH-3 | reviews 身份/POV 检查 `except: pass` 崩溃也给满分 | ✅ `44dd3e8` 加告警日志（不再静默）|
| P1-EH-5 | methodology_profile `except: return ""` 静默吞 YAML 错 | ✅ `44dd3e8` 加告警日志 |
| P1-EH-6 | methodology `mapped` UnboundLocalError | ✅ `fadbb58` 缩进修正 |
| P1-EH-9 | knowledge `dict(None)`/`list(None)` 崩快照持久化 | ✅ `fadbb58` 两分支 coerce |
| P1-EH-13 | continuity 倒计时只认 ASCII 数字，中文数字全绕过 | ✅ `fadbb58` 中文数字解析 |
| P1-IC-8 | `block_below_target_length` parser 默认 True ≠ dataclass False | ✅ `fadbb58` 对齐为 False |
| P1-IL-5 | event_dedup_unresolved 无消费者（实已有 WARN 日志+repair_history）| ✅ `44dd3e8` 订正误导性注释 |
| P1-SC-1 | publish_now IDOR（不校验 schedule 归属）| ✅ `44dd3e8` 校验 project_id |
| P1-SC-4 | 管线并发 TOCTOU | ✅ `7cb9cc4` Redis NX 启动预占（120s，best-effort 降级）|

**经核验为"非 bug / 不成立"的 P1（节选）**：P1-EH-11（`begin_nested` 实为正确的存档点隔离，非 session 损坏）、P1-EH-14（`str(exc)` 之外另有 traceback 日志）、P1-PF-7/8/10/11（已有批量/缓存/事务，描述不成立）、P1-IC-3（structural 静态分是有意的来源先验，非浪费）、P1-IC-6/RV-3/RV-6/IL-4、P1-SC-3（API key 走 sha256+DB 查找，无 Python 端密钥比较，时序攻击不适用）、P1-WK-2、P1-TS-2/3/4（这些路径其实有测试）。

**真实但搁置的 P1**（perf/架构/测试债，见 §五）：P1-PF-1/2/3/4/5/6/9/12、P1-DB-1/2/3/4、P1-WK-1、P1-CF-2、P1-TS-1/5/6 等。

---

## 四、已修复清单（5 提交，含验证）

| 提交 | 范围 | 验证 |
|------|------|------|
| `fadbb58` | P0-5, P0-7, EH-6, EH-9, EH-13, IC-8 | L1 单测 + 跨进程哈希确定性实证 |
| `44dd3e8` | SC-1, EH-2, P0-11, EH-5, EH-3, IL-5 | L1 单测（含 fake-session/redis/raise 注入）|
| `7cb9cc4` | SC-4 | L1 单测（序列化 + redis-down 降级）|
| `33861af` | P0-13, P0-6 | L1 单测（鉴权逻辑 + 零目标 clamp）|
| `e9a7c4d` | P0-3 | **L3 真机 A/B**：fix→fresh upgrade 成功(72 表/stamp head)+幂等 no-op；no-fix→DuplicateColumn 失败 |

**修复原则**：只删具体缺陷，保留机制骨架；fail-closed 改造对自动跑书取"可见告警"而非"硬阻断"以免单检查 bug 拖垮整本书；安全默认（鉴权可选开启、端口默认 loopback）不破坏本地单用户用法。

---

## 五、搁置项与理由（真实但不在本轮修复范围）

这些是**真实但属架构级/需 A/B 校准/纯测试债**的项，单点"修"反而有风险，建议按独立任务排期：

1. **检索系统 ANN 化（P0-8）**：全量 chunk 加载 → pgvector ANN 预过滤 + DB 侧 lexical + Python 精排。需 schema/索引改造 + 真机基准。
2. **Context build 并发化（P0-9 / P1-PF-1/3/9）**：35+ 串行查询 → 分组并发 + 章级缓存 + 全局 token 预算。需端到端性能回归。
3. **巨型文件拆分（P1-PF-12）**：planner.py(22k)/pipelines.py(12k)/drafts.py/reviews.py。纯重构，应独立 PR 以免与功能改动混淆。
4. **JSONB schema 校验（P1-DB-2）/ HNSW 调参（P1-DB-4）/ ChapterModel 索引（P1-DB-1）**：需迁移 + 真机验证（DB-2 还需确认上游 Pydantic 覆盖面）。
5. **评分基线校准（P0-16）/ 双 GateVerdict 统一（P0-10）**：改动会移动质量阈值，必须跨家族判官 A/B，勿凭直觉调。
6. **测试覆盖补全（P0-14 / P1-TS-1/5/6）**：reviews.py/context.py 核心函数、巨型测试文件拆分、集成测试扩容。
7. **`lock_version` 乐观锁（P0-2）**：要么接线 `version_id_col`，要么删字段；当前零行为影响。

---

## 六、给采纳者的提醒

- 原报告的 **file:line 多已漂移**（生成于 2026-06-19，分支后续有提交），核验须按符号/模式定位而非行号。
- 凡报告写"**完全失效/数据损坏/无法删除**"的，先查**下游守卫**（DB 约束、级联、创建期校验、独立 gate）——本轮 4 条 P0「不成立」全因此。
- 真正值得立刻处理的高危项只有两类且已修：**新部署起不来（P0-3）**、**Web 端裸奔（P0-13）**。

---

**文档版本**：v3（验证 + 修复版）
**最后更新**：2026-06-20
