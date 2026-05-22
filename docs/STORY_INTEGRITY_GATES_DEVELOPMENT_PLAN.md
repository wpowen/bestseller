# 故事完整性 Gate — 框架补漏开发计划

> **缘起**：用户在 ch1 审稿中发现三类问题——**时间线自相矛盾**（同一章里"三年前/二十三年前/十七年前"并存）、**场景跳跃无过渡**（17 号楼 → 旧事馆 → 17 号楼，无时间标记）、**角色定位漂移**（风水师莫名变侦探）。现有框架 **没有任何一个 gate 检测这三类问题**——必须补。
>
> **目标**：让框架在每章写完后自动捕获这三类问题，触发 auto-repair，确保整本书发表前不会再让人读出"故事不连贯"。
>
> **执行方**：可由具备 Python 3.11+ 与 BestSeller 架构知识的 LLM/工程师接手。所有任务必须配单元测试 + 通过 `pytest tests/unit/`。
>
> **验收方**：人工 + 自动审核 ch1 重审，确认三个新 gate 全部命中本次发现的具体问题。

---

## 现状盘点

### 已有可用数据（但 gate 没读它）

| 文件 | 内容 | 当前使用情况 |
|---|---|---|
| `story-bible/cast-and-promises.md` | 每个角色的"外显能力 / 内在伤口 / 读者承诺 / 禁止"四要素 | 仅供 prompt 拼接，未做事后校验 |
| `story-bible/event-state-ledger.md` | 历章末事件状态表 | 仅文档参考，未做章节校验 |
| `story-bible/canon-guardrails.json` | 禁用词/禁用规则 + 角色出场阈值 | ✅ 已接入 prompt + 写后 cast 校验 |

### 数据缺口（必须补）

| 缺失文件 | 用途 |
|---|---|
| **`story-bible/timeline-canon.md`** | 一份 single-source-of-truth 的时间表：每个角色每次重要事件的时间锚（"X年前 / Y 岁 / 戊子年"），供 Timeline Gate 用来核对每章 |

### 检测缺口（必须建）

| Gate | 当前状态 | 必须建 |
|---|---|---|
| 时间一致性 | ❌ 完全没有 | ✅ Task T |
| 场景连贯性 | ❌ 完全没有 | ✅ Task S |
| 角色定位漂移 | ⚠ canon_guardrails 只检测"禁止出场"，不检测"角色行为是否符合身份" | ✅ Task R |

---

## Task T — Timeline Consistency Gate

### 目标
检测章节内出现的"X 年前 / Y 年 / Z 岁 / 戊子年"等时间锚，与 `timeline-canon.md` 做交叉校验，**同时**检测同章内同一事件的不同时间锚是否互相矛盾。

### 数据：先建 `timeline-canon.md`

新文件 `output/<slug>/story-bible/timeline-canon.md`，YAML front-matter + Markdown 表格。示例（青囊不语问阴阳）：

```yaml
---
present_year: 2025          # 故事当下的年份
protagonist:
  name: 林渊
  current_age: 30
events:
  - id: lin_zhengchun_first_entry
    label: 林正淳第一次走进十七栋
    anchor_years_ago: 23
    anchor_year_name: 戊子年
    related_subjects: [林正淳, 林渊]
    notes: 林渊当时 7 岁，在门口看见父亲进入
  - id: lin_zhengchun_re_entry
    label: 林正淳再次入镜（对外死亡）
    anchor_years_ago: 3
    related_subjects: [林正淳, 林渊]
    notes: 至今未归
  - id: lin_jiahui_repair_mirror
    label: 林家辉补镜
    anchor_years_ago: 30
    related_subjects: [林家辉]
    notes: 留下康熙铜钱
  - id: lin_yuanshan_seal_mirror
    label: 林远山封困魂镜
    anchor_years_ago: 300
    related_subjects: [林远山, 三族]
    notes: 三族契约源头
forbidden_anchors:
  # 这些"年前数字"在 ch1 之前不得出现（用来卡住LLM 凭空发明的额外时间点）
  - years_ago: 17
    reason: 不在正典时间线，曾被错误生成
  - years_ago: 10
    reason: 不在正典时间线
---
```

### Gate 实现

新文件 `src/bestseller/services/timeline_consistency_gate.py`

```python
@dataclass(frozen=True)
class TimelineFact:
    label: str
    years_ago: int | None
    year_name: str | None  # 戊子年 / 庚午年
    subjects: tuple[str, ...]

@dataclass(frozen=True)
class TimelineViolation:
    severity: str  # "critical" | "high"
    detail: str
    found_anchor: str   # 在章节里找到的时间锚原文
    canonical_anchor: str | None  # canon 中对应的时间锚（若有）

@dataclass(frozen=True)
class TimelineReport:
    chapter_position: int
    violations: tuple[TimelineViolation, ...]
    @property
    def passed(self) -> bool:
        return not self.violations

def load_timeline_canon(path: Path) -> tuple[TimelineFact, ...]:
    """Parse story-bible/timeline-canon.md (YAML front-matter)."""

def check_timeline_consistency(
    chapter_text: str,
    chapter_position: int,
    canon: tuple[TimelineFact, ...],
    forbidden_anchors: tuple[int, ...] = (),
) -> TimelineReport:
    """
    1. Extract anchors from chapter:
       - regex r"([零一二三四五六七八九十百千万\d]+)\s*年前"
       - regex r"([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])年"
       - regex r"([零一二三四五六七八九十\d]+)\s*岁"
    2. For each anchor:
       a. Hit forbidden_anchors → critical violation
       b. If subject context (within ±200 chars) names a canonical subject:
          - the anchor must match canon's anchor for that subject
          - mismatch → critical
       c. If multiple anchors point to same subject within same paragraph but
          different values → high severity contradiction
    3. Return TimelineReport
    """
```

### 与 retention_safety_gate 集成

在 `retention_safety_gate.evaluate_retention_safety` 中加：
```python
if not skip_timeline and timeline_canon:
    report = check_timeline_consistency(...)
    if not report.passed:
        # critical → add TIMELINE_INCONSISTENT to auto_repair codes
```

新增 block code：`TIMELINE_INCONSISTENT` 加入 `AUTO_REPAIR_RETENTION_CODES`。

### 与 prompt 集成

在 `chapter_orchestrator.prepare_chapter_context` 渲染 `timeline_canon_block`：
```
【时间锚 — 必须遵守的全书时间线】
- 林正淳第一次进十七栋：二十三年前（戊子年），林渊 7 岁
- 林正淳再次入镜：三年前
- 林家辉补镜：三十年前
- 林远山封镜：三百年前
- 严禁出现："十七年前"等不在表中的时间锚
- 同一事件在本章内的时间表述必须一致
```

注入 `drafts.py` 和 `reviews.build_scene_rewrite_prompts` 的 user_prompt（紧跟 canon_guardrails_block 之后）。

### 测试 (`tests/unit/test_timeline_consistency_gate.py`)

至少 10 个测试，包括：
- 章节里出现 "二十三年前 + 三年前" 都属 canon → passed
- 章节里出现 "十七年前" (forbidden_anchors) → critical
- 章节里同一段落里说"父亲十七年前进十七栋" + "父亲二十三年前进十七栋" → critical contradiction
- 章节里说"七岁那年" + 同句"二十三年前" + protagonist age 30 → passed (consistency check 30-23=7)
- 章节里说"五岁那年" + 同句"二十三年前" + protagonist age 30 → critical (5 != 30-23)
- 无 canon → 跳过（向后兼容）

### 验收
- 用当前 ch1 历史版本（有"十七年前"）跑 gate → 必须 critical 命中
- 用最新 ch1（已修复）跑 gate → 必须 passed

---

## Task S — Scene Coherence Gate

### 目标
检测章节内的**位置跳转**是否有显式过渡（时间标记 + 移动动作）。比如 ch1 历史版本里"在 17 号楼"突然变"快步走向城北旧货市场"没有过渡，gate 应命中。

### Gate 实现

新文件 `src/bestseller/services/scene_coherence_gate.py`

```python
@dataclass(frozen=True)
class SceneJump:
    from_location: str
    to_location: str
    paragraph_index: int
    transition_marker_found: bool
    severity: str  # critical 若无过渡; high 若过渡薄弱

@dataclass(frozen=True)
class SceneCoherenceReport:
    chapter_position: int
    jumps: tuple[SceneJump, ...]
    @property
    def passed(self) -> bool:
        return not any(j.severity == "critical" for j in self.jumps)

# 已知位置词典（从 story-bible 或 hardcoded common locations）
_LOCATION_TOKENS = (
    "十七栋", "23 层", "二十三层", "303", "305", "306",
    "城南旧事馆", "城北旧货市场", "清水桥",
    "太平间", "ICU", "病房", "医院", "停尸柜",
    "义庄", "茅山",
    # 通用位置词
    "客厅", "卧室", "卫生间", "楼梯口", "门外",
)

_TRANSITION_TIME_MARKERS = (
    "半小时后", "二十分钟后", "片刻后", "X 分钟后",
    "零点已过", "一炷香后",
    "几日后", "三日后", "次日",
    "他赶到", "他抵达", "他冲下", "他转身退出",
    "走向", "驶向", "回到", "返回",
)

def check_scene_coherence(
    chapter_text: str,
    chapter_position: int,
) -> SceneCoherenceReport:
    """
    1. Split text into paragraphs
    2. For each paragraph, identify dominant location (or none)
    3. Detect transitions: paragraph_i in location_A, paragraph_i+1 in location_B
    4. For each transition, scan ±2 paragraphs for transition markers
    5. If no markers found → critical SceneJump
    6. If weak marker (just one word like "走") → high
    7. Return report
    """
```

### 集成 + Prompt

- Block code: `SCENE_JUMP_UNRESOLVED` → AUTO_REPAIR_RETENTION_CODES
- Prompt block: `scene_coherence_block` 渲染发现的跳转 + 应该补的过渡描述

### 测试 (`tests/unit/test_scene_coherence_gate.py`)

至少 8 个测试：
- 单一场景章节 → passed
- 17 号楼 → 旧事馆带"二十分钟后他停在旧事馆门口" → passed
- 17 号楼 → 旧事馆**无任何过渡** → critical
- 三个场景 + 完整过渡 → passed
- 三个场景 + 中间过渡薄弱（只有"走"）→ high

### 验收
- 用 ch1 历史版本（17号楼 → 旧事馆 无过渡）跑 → 必须 critical
- 用最新 ch1（已加过渡）跑 → 必须 passed

---

## Task R — Character Role Compliance Gate

### 目标
读取 `cast-and-promises.md`，对每个 POV 角色，校验本章中其行为是否符合"外显能力"+"读者承诺"+"禁止"三项。

### Gate 实现

新文件 `src/bestseller/services/character_role_gate.py`

```python
@dataclass(frozen=True)
class CharacterProfile:
    name: str
    abilities: tuple[str, ...]     # 外显能力 line items
    inner_wound: str
    reader_promise: str
    forbidden_patterns: tuple[str, ...]  # 禁止 items + LLM-likely-drift phrases

@dataclass(frozen=True)
class RoleDriftFinding:
    character: str
    severity: str
    drift_type: str  # "uses_unlisted_ability" | "matches_forbidden_pattern" | "tone_mismatch"
    detail: str
    evidence: str  # 命中的章节片段

@dataclass(frozen=True)
class CharacterRoleReport:
    chapter_position: int
    findings: tuple[RoleDriftFinding, ...]
    @property
    def passed(self) -> bool:
        return not any(f.severity == "critical" for f in self.findings)

def parse_cast_promises(path: Path) -> tuple[CharacterProfile, ...]:
    """Parse cast-and-promises.md into structured profiles."""

def check_character_role_compliance(
    chapter_text: str,
    chapter_position: int,
    profiles: tuple[CharacterProfile, ...],
) -> CharacterRoleReport:
    """
    For each profile that has on-page presence (subject name appears ≥ N times):
    1. Run forbidden_patterns regex/substring match → critical if hit
    2. Detect "abilities not in list" — find verb patterns associated with
       common feng-shui/maoshan/detective abilities; if all abilities used
       by character in chapter are outside their listed abilities → high
    3. Tone mismatch: detect "detective/police-procedural" language for
       characters whose role is supernatural — substring like "破案" "侦查"
       "审讯" when character profile lacks "推理/侦查" — high
    """
```

### 林渊 的 expected pattern 示例

从 cast-and-promises.md 解析：
```yaml
character: 林渊
abilities:
  - 阴阳眼
  - 罗盘
  - 青囊秘卷
  - 符法基础
  - 方位判断
  - 账页推理
forbidden_patterns:
  - "被鬼追着跑"  # cast 明文禁止
  - "被动应对"
  - "纯粹受害者"
tone_must_include:  # 至少 2 个之一
  - 阴阳眼
  - 罗盘
  - 符
  - 账
```

ch1 末尾原本写"今夜起，他就要把那笔账查清"——如果 gate 检测到全章没有"阴阳眼/罗盘/符/账"作为破局动作（只有"查清"这种侦探腔），就告警。当前 ch1 已被修复，包含"按这套法子 / 阴阳眼辨明 / 罗盘定方位 / 青囊推回旧债"，gate 应 passed。

### 集成 + Prompt

- Block code: `CHARACTER_ROLE_DRIFT` → AUTO_REPAIR_RETENTION_CODES
- Prompt block: `character_role_block` 渲染所有出场角色的能力 + 禁止清单

### 测试 (`tests/unit/test_character_role_gate.py`)

至少 8 个测试：
- 林渊用"阴阳眼" + "罗盘"破局 → passed
- 林渊"被鬼追着跑" → critical（命中 forbidden）
- 林渊全章只"查案"无任何驱魔/账法 → high
- 林渊用"罗盘+青囊+阴阳眼"组合 → passed

### 验收
- 用 ch1 历史末尾"今夜起，他就要把那笔账查清" + 全章无术法描写 → 应 high
- 用最新 ch1 末尾"按这套法子 / 罗盘 / 阴阳眼 / 青囊" → 应 passed

---

## 集成总览

### retention_safety_gate.py 修改

加入三个新 gate 的调用：

```python
def evaluate_retention_safety(
    *,
    # 现有参数...
    timeline_canon: tuple[TimelineFact, ...] | None = None,
    character_profiles: tuple[CharacterProfile, ...] | None = None,
    skip_timeline: bool = False,
    skip_scene_coherence: bool = False,
    skip_character_role: bool = False,
) -> RetentionGateReport:
    findings = []
    auto_repair = []
    # ... existing gates ...

    if not skip_timeline and timeline_canon:
        tr = check_timeline_consistency(chapter_text, chapter_position, timeline_canon)
        if not tr.passed and any(v.severity == "critical" for v in tr.violations):
            auto_repair.append("TIMELINE_INCONSISTENT")
            findings.append(... evidence with all violations ...)

    if not skip_scene_coherence:
        sr = check_scene_coherence(chapter_text, chapter_position)
        if not sr.passed:
            auto_repair.append("SCENE_JUMP_UNRESOLVED")
            findings.append(...)

    if not skip_character_role and character_profiles:
        cr = check_character_role_compliance(chapter_text, chapter_position, character_profiles)
        if not cr.passed:
            auto_repair.append("CHARACTER_ROLE_DRIFT")
            findings.append(...)

    return RetentionGateReport(...)
```

### prepare_chapter_context 修改

加入 4 个新 fields：
```python
@dataclass(frozen=True)
class ChapterContext:
    # 现有 fields...
    timeline_canon_block: str | None = None
    scene_coherence_block: str | None = None
    character_role_block: str | None = None
    timeline_canon_facts: tuple[TimelineFact, ...] | None = None  # 给 retention 用
    character_profiles: tuple[CharacterProfile, ...] | None = None  # 给 retention 用
```

在 `prepare_chapter_context` 内加载这些数据 + 渲染对应 block。

### drafts.py / reviews.py 修改

在 `build_scene_draft_prompts` 和 `build_scene_rewrite_prompts` / `build_chapter_rewrite_prompts` 中拼入：
```python
# 新增 prompt blocks
timeline_canon_block: str | None = None,
character_role_block: str | None = None,
```

拼到 user_prompt 紧跟 canon_guardrails_block 之后（与它同等优先级）。

---

## 任务清单 + 工作量估算

| Task | 内容 | 工作量 | 难度 |
|---|---|---|---|
| T-data | 建 timeline-canon.md（含 schema 设计） | 0.3 天 | 低 |
| T-gate | Timeline Consistency Gate + 10 测试 | 1.5 天 | 中 |
| T-int | 集成到 retention + prompt + tests | 0.5 天 | 低 |
| S-gate | Scene Coherence Gate + 8 测试 | 1.5 天 | 中 |
| S-int | 集成 | 0.3 天 | 低 |
| R-data | cast-and-promises.md parser + schema | 0.5 天 | 中 |
| R-gate | Character Role Compliance Gate + 8 测试 | 1.5 天 | 中 |
| R-int | 集成 | 0.3 天 | 低 |
| Audit | 用 ch1 历史版本 + 最新版本验证三个 gate 都正确判断 | 0.5 天 | 低 |
| Doc | 更新 framework skill 文档 | 0.3 天 | 低 |
| **合计** | | **7-8 天** | |

---

## 验收清单

完成后，跑下面命令必须满足：

```bash
# 1. 全量单测过
.venv/bin/python -m pytest tests/unit/ --no-cov -q
# 期望: ≥ 4700 passed（4643 + 30 新）

# 2. ch1 历史版本（含"十七年前"+ 无场景过渡 + "查清"）
.venv/bin/python /tmp/audit_ch1_with_new_gates.py history
# 期望:
#   Timeline gate: critical (TIMELINE_INCONSISTENT, 命中"十七年前")
#   Scene gate:    critical (SCENE_JUMP_UNRESOLVED, 17号楼→旧事馆无过渡)
#   Role gate:     high     (CHARACTER_ROLE_DRIFT, 全章侦探腔无术法)

# 3. ch1 最新版本（已修复）
.venv/bin/python /tmp/audit_ch1_with_new_gates.py current
# 期望:
#   Timeline gate: ✓ passed
#   Scene gate:    ✓ passed
#   Role gate:     ✓ passed
```

---

## 风险与限制

1. **正则匹配限制**：时间锚提取用正则，可能漏掉非常规表述（"那是十七个年头之前的事"）。后续可升级为 LLM-judge 模式。

2. **场景识别**：通用位置词典是 hardcoded 的子集，遇到新书必须扩充。建议每本书的 story-bible 加一个 `locations.md` 文件。

3. **角色定位 tone 匹配**：以 substring 检测"破案/侦查"等词，可能误伤。可以引入 LLM-as-critic 作为二次确认。

4. **timeline-canon 维护成本**：每本书必须手动建一份。但好处是：一次性投入换长期可校验性，比反复在章节里修 bug 划算得多。

---

## 完成后给我（验收方）的产物清单

1. 全量单测通过截图（≥ 4700 passed）
2. `/tmp/audit_ch1_with_new_gates.py history|current` 两种模式的输出对照
3. `output/exorcist-detective-1778051012/story-bible/timeline-canon.md` 内容
4. 跑 ch2 retention 流程，含三个新 gate 的 trace JSON
5. 文档更新（framework skill / README）的 diff
