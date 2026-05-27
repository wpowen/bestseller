# 方法论驱动的质量管道改造 — 开发计划

**作者**：架构组
**日期**：2026-05-26
**执行人**：Codex
**预计工作量**：14–18 小时
**前置依赖**：本仓库 `main` 分支当前状态（含已应用的 commercial_planning_readiness 修复、LLM judge 异常解包修复等）

---

## 0. 背景与目标

### 当前问题

写作方法论体系存在 4 份资产（`config/writing_methodology.yaml` 683 行、`config/public_emotion_methodology.yaml`、`config/prompt_packs/<genre>.yaml`、`src/bestseller/services/methodology*.py`），但它们没有真正流通到生成链路。具体表现：

1. **大纲 / 细纲生成不读方法论**：
   - `suspense-mystery.yaml` 等 27/29 个 prompt pack **缺失**核心 planner 碎片 `opening_rules`、`character_design`、`reversal_design`。
   - 调 `render_methodology_block(pack, phase="planner")` 在大多数项目下返回**空字符串**。
2. **prewrite plan（写前 manifest）完全不读方法论**：
   - `render_prewrite_plan_prompt`（`src/bestseller/services/chapter_constraint_manifest.py:605`）只把 manifest 压缩成 JSON，无任何方法论上下文。这个 prompt 是 627 次/项目的最热节点。
3. **LLM Judge 假装在评估方法论**：
   - `chapter_llm_quality_judge` 和 `outline_llm_judge` 都列了 `methodology_compliance` 评估维度（阈值 0.80），但**裁判 LLM 根本看不到方法论本体内容**——它在猜分。
4. **用户反馈被固化成硬规则**：
   - 17+ 个 `scripts/repair_qingnang_*.py` 把用户反馈编码成 `scene_cards.forbidden_actions` 硬注入 DB，污染写手 prompt。

### 改造目标

让方法论真正驱动整条管道：

```
writing_methodology.yaml (单一真相源)
    ↓ methodology_bridge（新模块：master fallback）
prompt_pack 碎片（缺失自动回落到 master）
    ↓
卷纲生成（含方法论） → 章纲生成（含方法论） → prewrite plan（含方法论）
    ↓
写手（已有）
    ↓
LLM Judge（注入方法论本体作为评分依据）
```

并且引入新的 **outline_reader_experience_judge**，对 ch1-10 做"模拟新读者"评估，捕捉空间断裂、信息密度爆炸、召唤合理性等 v194 暴露的硬伤。

### 不在本计划范围内

- 不改 conception 阶段
- 不改写手主提示（已经在用方法论）
- 不重新生成已有章节正文（那是后续任务）

---

## 1. 前置检查清单

执行任何任务前，请逐项验证：

```bash
# 1.1 工作目录干净
cd /Volumes/MACSSD/owen-home/Documents/workspace/bestseller
git status

# 1.2 确认关键文件存在
test -f config/writing_methodology.yaml && wc -l config/writing_methodology.yaml
test -f config/prompt_packs/suspense-mystery.yaml
test -f src/bestseller/services/methodology.py
test -f src/bestseller/services/prompt_packs.py
test -f src/bestseller/services/chapter_constraint_manifest.py
test -f src/bestseller/services/chapter_llm_quality_judge.py
test -f src/bestseller/services/outline_llm_judge.py

# 1.3 确认测试基线通过
uv run pytest tests/unit/test_output_validator.py tests/unit/test_review_services.py tests/unit/test_commercial_planning_readiness.py -q --no-cov
# 期望：108 passed

# 1.4 确认容器可重建
docker compose ps
# 期望：所有 service healthy
```

如有任一项失败，**停止**并向用户上报。

---

## 2. 任务依赖图

```
T1（bridge 模块）
  ├── T2（补全 suspense-mystery 包）   ── 可并行
  ├── T3（卷纲/章纲注入方法论）          ── 依赖 T1
  ├── T4（prewrite plan 注入方法论）     ── 依赖 T1
  ├── T5（chapter judge 注入方法论）     ── 依赖 T1
  ├── T6（outline judge 注入方法论）     ── 依赖 T1
T7（新增 reader_experience_judge）      ── 依赖 T6
T8（audit script 方法论合规扫描）        ── 依赖 T5,T6
T9（写测试 + 集成验证）                  ── 全部依赖完成后
T10（Docker 重建 + 端到端验证）          ── 依赖 T9
```

按顺序执行 T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10。

---

## 3. 任务清单

### T1：实现 methodology_bridge 模块

**目标**：当 prompt_pack 缺失某 fragment 时，自动从 `writing_methodology.yaml` 回落对应通用版本。

**文件**：新建 `src/bestseller/services/methodology_bridge.py`

**完整代码**：

```python
"""Methodology fragment bridge.

Single source of truth: ``config/writing_methodology.yaml``.  Each prompt pack
can override or extend the master methodology; when a pack lacks a fragment we
fall back to a generic version derived from the master file.

This module is the only sanctioned way to obtain methodology text for a given
(pack, phase, fragment_key) tuple.  Direct access to ``pack.fragments`` is
discouraged because it bypasses the master fallback.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from bestseller.services.methodology import load_methodology
from bestseller.services.prompt_packs import PromptPack

Phase = Literal["scene", "review", "planner", "prewrite", "judge"]


# ── Master fallback registry ─────────────────────────────────────────────────
# Each entry maps a (phase, fragment_key) tuple to a renderer that builds the
# generic fragment from writing_methodology.yaml top-level sections.

_MASTER_FALLBACK_BUILDERS: dict[tuple[Phase, str], str] = {}


def _register_fallback(phase: Phase, fragment_key: str, yaml_path: tuple[str, ...]) -> None:
    """Register a (phase, fragment_key) → yaml subtree path."""
    _MASTER_FALLBACK_BUILDERS[(phase, fragment_key)] = "::".join(yaml_path)


# Planner-phase fallbacks
_register_fallback("planner", "opening_rules", ("opening_system",))
_register_fallback("planner", "character_design", ("character_engineering",))
_register_fallback("planner", "reversal_design", ("hook_lifecycle", "reversal_design"))
_register_fallback("planner", "climax_design", ("climax_blueprint",))
_register_fallback("planner", "core_loop", ("core_loop_engine",))

# Scene-phase fallbacks
_register_fallback("scene", "emotion_engineering", ("emotion_engineering",))
_register_fallback("scene", "conflict_stakes", ("conflict_system",))
_register_fallback("scene", "hook_design", ("hook_lifecycle",))
_register_fallback("scene", "core_loop", ("core_loop_engine",))
_register_fallback("scene", "dialogue_rules", ("dialogue_engineering",))
_register_fallback("scene", "visual_writing", ("visual_writing",))
_register_fallback("scene", "pacing_guidance", ("pacing_control",))
_register_fallback("scene", "reaction_amplification", ("reaction_amplification",))

# Review-phase fallbacks (judges and reviewers)
_register_fallback("review", "emotion_engineering", ("emotion_engineering",))
_register_fallback("review", "conflict_stakes", ("conflict_system",))
_register_fallback("review", "hook_design", ("hook_lifecycle",))
_register_fallback("review", "core_loop", ("core_loop_engine",))
_register_fallback("review", "pacing_guidance", ("pacing_control",))

# Prewrite-phase fallbacks (NEW — was previously empty)
_register_fallback("prewrite", "spring_model", ("emotion_engineering", "spring_model"))
_register_fallback("prewrite", "stakes_design", ("conflict_system", "stakes_design"))
_register_fallback("prewrite", "information_density", ("opening_system", "information_density"))

# Judge-phase fallbacks (NEW)
_register_fallback("judge", "opening_rules", ("opening_system",))
_register_fallback("judge", "character_design", ("character_engineering",))
_register_fallback("judge", "spring_model", ("emotion_engineering", "spring_model"))
_register_fallback("judge", "stakes_design", ("conflict_system", "stakes_design"))
_register_fallback("judge", "hook_design", ("hook_lifecycle",))


# ── Public API ───────────────────────────────────────────────────────────────


def get_fragment(
    pack: PromptPack | None,
    *,
    phase: Phase,
    fragment_key: str,
) -> str:
    """Return the best-available methodology fragment text.

    Resolution order:
      1. ``pack.fragments.<fragment_key>`` (genre-specific override)
      2. Master fallback built from ``writing_methodology.yaml``
      3. Empty string

    Never returns None.  The returned string is prompt-ready (no further
    formatting required).
    """
    if pack is not None:
        value = getattr(pack.fragments, fragment_key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    builder_path = _MASTER_FALLBACK_BUILDERS.get((phase, fragment_key))
    if not builder_path:
        return ""
    return _render_master_fragment(builder_path)


def get_fragments_for_phase(
    pack: PromptPack | None,
    *,
    phase: Phase,
) -> dict[str, str]:
    """Return all known fragments for a phase, dict keyed by fragment_key."""
    out: dict[str, str] = {}
    for (registered_phase, key), _ in _MASTER_FALLBACK_BUILDERS.items():
        if registered_phase != phase:
            continue
        text = get_fragment(pack, phase=phase, fragment_key=key)
        if text:
            out[key] = text
    return out


def render_phase_block(
    pack: PromptPack | None,
    *,
    phase: Phase,
    heading: str = "写法方法论指导",
) -> str:
    """Render combined methodology block for a phase, with master fallback.

    Drop-in replacement for ``prompt_packs.render_methodology_block`` that
    fills missing pack fragments from the master yaml.
    """
    fragments = get_fragments_for_phase(pack, phase=phase)
    if not fragments:
        return ""
    sections = [f"【{key}】\n{value}" for key, value in fragments.items()]
    return f"## {heading}\n\n" + "\n\n".join(sections)


# ── Internal helpers ─────────────────────────────────────────────────────────


@lru_cache(maxsize=64)
def _render_master_fragment(path_spec: str) -> str:
    """Render a generic fragment from writing_methodology.yaml.

    ``path_spec`` is a "::"-joined yaml key path (e.g. "emotion_engineering").
    The renderer walks the path and formats the subtree as prompt-ready text.
    """
    master = load_methodology()
    if not master:
        return ""

    keys = path_spec.split("::")
    node = master
    for key in keys:
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
        if node is None:
            return ""

    return _format_yaml_subtree(node)


def _format_yaml_subtree(node: object, depth: int = 0) -> str:
    """Pretty-format a yaml subtree as a prompt-readable bullet list."""
    indent = "  " * depth
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, (int, float, bool)):
        return str(node)
    if isinstance(node, list):
        lines: list[str] = []
        for item in node:
            if isinstance(item, dict):
                # dict-in-list pattern (e.g. stages)
                head = item.get("stage") or item.get("name") or item.get("key") or ""
                detail = item.get("description") or item.get("rule") or ""
                ratio = item.get("ratio")
                line = f"{indent}- "
                if head:
                    line += f"{head}"
                if ratio is not None:
                    line += f" ({ratio:.0%})"
                if detail:
                    line += f": {detail}"
                lines.append(line)
            else:
                lines.append(f"{indent}- {item}")
        return "\n".join(lines)
    if isinstance(node, dict):
        lines: list[str] = []
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                lines.append(f"{indent}{value.strip()}")
                continue
            if isinstance(value, (dict, list)):
                lines.append(f"{indent}■ {key}：")
                lines.append(_format_yaml_subtree(value, depth + 1))
            else:
                lines.append(f"{indent}- {key}: {value}")
        return "\n".join(lines)
    return ""


__all__ = [
    "Phase",
    "get_fragment",
    "get_fragments_for_phase",
    "render_phase_block",
]
```

**单元测试**：新建 `tests/unit/test_methodology_bridge.py`

```python
from __future__ import annotations

import pytest

from bestseller.services.methodology_bridge import (
    get_fragment,
    get_fragments_for_phase,
    render_phase_block,
)
from bestseller.services.prompt_packs import resolve_prompt_pack

pytestmark = pytest.mark.unit


def test_bridge_returns_pack_fragment_when_present() -> None:
    pack = resolve_prompt_pack("suspense-mystery")
    # suspense-mystery has dialogue_rules
    text = get_fragment(pack, phase="scene", fragment_key="dialogue_rules")
    assert text and "悬疑对话规则" in text


def test_bridge_falls_back_to_master_when_pack_missing() -> None:
    pack = resolve_prompt_pack("suspense-mystery")
    # suspense-mystery is missing opening_rules at planner phase
    text = get_fragment(pack, phase="planner", fragment_key="opening_rules")
    assert text, "opening_rules must fall back to master"
    # master writing_methodology.yaml has opening_system section
    assert "opening" in text.lower() or "开篇" in text or "黄金" in text


def test_bridge_returns_empty_when_neither_source_has_fragment() -> None:
    pack = resolve_prompt_pack("suspense-mystery")
    text = get_fragment(pack, phase="planner", fragment_key="nonexistent_fragment_xyz")
    assert text == ""


def test_get_fragments_for_phase_returns_all_available() -> None:
    pack = resolve_prompt_pack("suspense-mystery")
    fragments = get_fragments_for_phase(pack, phase="planner")
    # After bridge, all 5 planner fragments should be available (some from pack, some from master)
    assert "opening_rules" in fragments
    assert "character_design" in fragments
    assert "reversal_design" in fragments
    assert "climax_design" in fragments
    assert "core_loop" in fragments


def test_render_phase_block_produces_nonempty_for_planner() -> None:
    pack = resolve_prompt_pack("suspense-mystery")
    block = render_phase_block(pack, phase="planner")
    assert block
    assert "写法方法论指导" in block
    assert "opening_rules" in block or "开篇" in block
```

**验证**：

```bash
uv run pytest tests/unit/test_methodology_bridge.py -v --no-cov
# 期望：5 passed
```

**成功标准**：
- 新文件存在且通过测试
- `from bestseller.services.methodology_bridge import get_fragment` 在仓库其他位置可成功 import

---

### T2：补全 suspense-mystery 包的 planner 碎片

**目标**：给当前项目用的 prompt pack 直接补上 3 个缺失的 planner 碎片，避免每次都靠 master fallback。

**文件**：`config/prompt_packs/suspense-mystery.yaml`

**操作**：在 `fragments:` 顶层 key 下加入以下 3 个键。位置：在现有 `climax_design:`（约 213 行）之后插入。

**完整 YAML 片段**：

```yaml
  # ── Planner-phase: 悬疑/驱魔类型的开篇/角色/反转方法论 ─────────────────
  opening_rules: |
    悬疑/驱魔类型黄金三章开篇规则：
    - 主角第一时间出场，且必须显示"专业身份"：通过物件（铜钱/罗盘/青囊）、动作（验煞/卜算）或他人称谓（X 师傅）建立。
    - 第一章必须包含"召唤合理性"链条：
      * 谁找的主角（具名委托人或口碑推荐人）
      * 为什么找（具体异象描述 + 物业/警察处理失败）
      * 报酬或动机锚点（钱、家族线索、旧债）
    - 前 500 字必须包含：人物名 + 当下困境（具体不超自然描述也可） + 主角动作。
    - 第一章信息密度上限：≤3 个具名角色，≤2 个高概念术语，≤1 个超自然显形。
    - 章末钩子必须自然生长：钩子内容的前提条件必须在本章已建立。
    - 禁忌：纯环境描写开头、回忆/独白开头、电话/短信单一媒介开头无配套现场画面。

  character_design: |
    悬疑/驱魔类型角色设计方法论：
    - 主角必须有"专业能力"+"人性弱点"+"未解债务"三重锚点。专业能力支撑读者信任，弱点制造冲突，债务驱动长线弧线。
    - 委托人/求助者必须有"无法找正规渠道"的合理理由（民俗禁忌、不被警察相信、超自然性质明显）。
    - 反派/真凶必须有清晰动机：贪、嗔、痴、惧、仇 五选一为主线。
    - 第一卷出场角色控制在 7 人以内，每人有独立身份标签和动机。
    - 角色弧线节奏：每 5 章至少推进一个主要角色的认知或选择层。

  reversal_design: |
    悬疑/驱魔类型反转设计方法论：
    - 三种合法反转类型：
      1. 身份反转（受害者是凶手 / 委托人是局中人）
      2. 规则反转（之前理解的超自然规则被推翻，揭示更深层规则）
      3. 关系反转（盟友是敌人 / 敌人有不得已苦衷）
    - 反转铺垫规则：反转点的伏笔必须在反转前至少 2 章已经埋下，且至少有 3 处可被读者复盘到的线索。
    - 反转密度：黄金三章可有 1 次小反转（规则反转），ch4-10 可有 1 次中反转（身份或关系），ch11-30 必须有 1 次大反转。
    - 禁忌：突兀降神（凭空出现的新角色解决问题）、规则乱改（已建立规则被无理由打破）、读者无法预判（毫无线索的纯意外）。
```

**验证**：

```bash
# yaml 语法
uv run python -c "import yaml; yaml.safe_load(open('config/prompt_packs/suspense-mystery.yaml'))"
# 期望：无输出，无报错

# 通过 bridge 取这些 fragment
uv run python -c "
from bestseller.services.prompt_packs import resolve_prompt_pack
from bestseller.services.methodology_bridge import get_fragment
pack = resolve_prompt_pack('suspense-mystery')
for key in ['opening_rules', 'character_design', 'reversal_design']:
    text = get_fragment(pack, phase='planner', fragment_key=key)
    assert text, f'{key} should not be empty'
    print(f'{key}: {text[:60]}...')
"
```

**成功标准**：3 个 fragment 都能从 pack 直接读出（不需要 fallback 到 master）。

---

### T3：在卷纲/章纲生成阶段注入方法论

**目标**：把 `methodology_bridge.render_phase_block(pack, phase="planner")` 接到 planner.py 已有的方法论注入点。当前 `render_methodology_block` 在某些 pack 下返回空字符串，改用 bridge 后必定有内容。

**文件**：`src/bestseller/services/planner.py`

**操作**：

1. **改 import**（约 line 87）

```python
# OLD
from bestseller.services.prompt_packs import (
    render_methodology_block,
    ...
)

# NEW: 仍保留 render_methodology_block 用于 scene/review 阶段；新增 bridge
from bestseller.services.prompt_packs import (
    render_methodology_block,
    ...
)
from bestseller.services.methodology_bridge import (
    render_phase_block as render_methodology_phase_block,
)
```

2. **替换 3 处 planner-phase 调用**（行号约 9898、10635、10960）

```python
# OLD
_methodology_planner_block = render_methodology_block(prompt_pack, phase="planner")

# NEW
_methodology_planner_block = render_methodology_phase_block(
    prompt_pack, phase="planner"
)
```

3. **如果存在 `render_methodology_block(prompt_pack, phase="review")` 的调用，也替换为 bridge 版本**（grep 验证）。

**验证**：

```bash
# 替换完后语法检查
uv run python -c "import ast; ast.parse(open('src/bestseller/services/planner.py').read()); print('OK')"

# grep 确认没有遗漏的 planner-phase 调用
grep -n 'render_methodology_block.*phase="planner"' src/bestseller/services/planner.py
# 期望：无输出（全部已替换）
```

**单元测试**：新建 `tests/unit/test_planner_methodology_injection.py`

```python
from __future__ import annotations

import pytest

from bestseller.services.methodology_bridge import render_phase_block
from bestseller.services.prompt_packs import resolve_prompt_pack

pytestmark = pytest.mark.unit


def test_planner_block_contains_opening_rules_for_suspense() -> None:
    pack = resolve_prompt_pack("suspense-mystery")
    block = render_phase_block(pack, phase="planner")
    assert "opening_rules" in block or "开篇" in block
    assert "黄金三章" in block or "前 500 字" in block


def test_planner_block_contains_character_design() -> None:
    pack = resolve_prompt_pack("suspense-mystery")
    block = render_phase_block(pack, phase="planner")
    assert "character_design" in block or "角色" in block
    assert "三重锚点" in block or "动机" in block


def test_planner_block_nonempty_for_all_supported_packs() -> None:
    """Ensure bridge guarantees nonempty planner block for any pack with master fallback."""
    for pack_name in ["suspense-mystery", "xianxia-upgrade-core", "epic-fantasy"]:
        pack = resolve_prompt_pack(pack_name)
        block = render_phase_block(pack, phase="planner")
        assert block, f"planner block must be nonempty for {pack_name}"
```

**成功标准**：
- 3 个测试通过
- planner.py 的 3 处 planner 调用已迁移到 bridge
- 跑 `uv run pytest tests/unit/test_planner_methodology_injection.py -v --no-cov` 全过

---

### T4：让 prewrite plan 注入方法论

**目标**：`render_prewrite_plan_prompt` 是 627 次/项目调用的最热节点，但当前完全不读方法论。注入弹簧法阶段、信息密度上限、本场景应触发的核心循环节点。

**文件**：`src/bestseller/services/chapter_constraint_manifest.py`

**操作**：

1. **改函数签名**（约 line 605），加入 `pack` 和 `chapter_number` 参数：

```python
# OLD
def render_prewrite_plan_prompt(
    manifest: ChapterConstraintManifest,
    *,
    language: str = "zh-CN",
) -> str:

# NEW
def render_prewrite_plan_prompt(
    manifest: ChapterConstraintManifest,
    *,
    language: str = "zh-CN",
    pack: "PromptPack | None" = None,
    chapter_number: int | None = None,
) -> str:
```

2. **在函数顶部新增方法论上下文构建**（在 `payload = json.dumps(...)` 之前）：

```python
# Methodology context — injected from bridge.  Falls back to master yaml when
# pack lacks the fragment.  When ``pack`` is None we still get master content.
from bestseller.services.methodology_bridge import get_fragment

methodology_lines: list[str] = []

# 信息密度规则（前 10 章特别关键）
density_rule = get_fragment(pack, phase="prewrite", fragment_key="information_density")
if density_rule and (chapter_number is None or chapter_number <= 10):
    methodology_lines.append(f"【信息密度规则】\n{density_rule}")

# 弹簧法阶段提示
spring_rule = get_fragment(pack, phase="prewrite", fragment_key="spring_model")
if spring_rule:
    methodology_lines.append(f"【情绪压缩弹簧法】\n{spring_rule}")

# 筹码设计规则
stakes_rule = get_fragment(pack, phase="prewrite", fragment_key="stakes_design")
if stakes_rule:
    methodology_lines.append(f"【冲突筹码设计】\n{stakes_rule}")

methodology_block = "\n\n".join(methodology_lines) if methodology_lines else ""
```

3. **把方法论 block 注入到 user_prompt 里**（中文/英文分支都要改）：

```python
# 中文分支
if language.lower().startswith("zh"):
    methodology_section = (
        f"\n## 写作方法论参考（用于影响场景计划的字段选择）\n{methodology_block}\n"
        if methodology_block
        else ""
    )
    return (
        "根据以下写前约束清单和方法论参考，先声明本场景写作计划。只输出 JSON，不要输出正文。\n"
        # ...（保持原 prompt 内容）
        f"约束清单：\n```json\n{payload}\n```{methodology_section}"
    )

# 英文分支同理（把 methodology_section 翻译为英文 header）
```

4. **修改调用方**（`src/bestseller/services/drafts.py:7386`）：

```python
# OLD
user_prompt = render_prewrite_plan_prompt(manifest, language=language)

# NEW
user_prompt = render_prewrite_plan_prompt(
    manifest,
    language=language,
    pack=_resolve_pack_for_project(project),  # 见下方辅助函数
    chapter_number=chapter.chapter_number,
)
```

`_resolve_pack_for_project` 已经在 drafts.py 存在或可通过 `from bestseller.services.prompt_packs import resolve_prompt_pack_for_project` 取得；如不存在，加入：

```python
def _resolve_pack_for_project(project: ProjectModel):
    from bestseller.services.prompt_packs import resolve_prompt_pack
    pack_name = (project.metadata_json or {}).get("prompt_pack_name") or _infer_pack_name(project)
    return resolve_prompt_pack(pack_name) if pack_name else None
```

**单元测试**：扩展 `tests/unit/test_chapter_constraint_manifest.py`（如不存在则新建）

```python
def test_prewrite_prompt_includes_methodology_for_early_chapters() -> None:
    from bestseller.services.chapter_constraint_manifest import (
        render_prewrite_plan_prompt,
        ChapterConstraintManifest,
    )
    from bestseller.services.prompt_packs import resolve_prompt_pack

    pack = resolve_prompt_pack("suspense-mystery")
    manifest = ChapterConstraintManifest(...)  # 用合法 minimal fixture
    prompt = render_prewrite_plan_prompt(
        manifest, language="zh-CN", pack=pack, chapter_number=1,
    )
    assert "信息密度" in prompt or "information_density" in prompt
    assert "弹簧法" in prompt or "spring_model" in prompt


def test_prewrite_prompt_omits_density_rule_for_late_chapters() -> None:
    # chapter_number > 10 — density rule should NOT appear
    ...
```

**成功标准**：
- ch1-10 的 prewrite prompt 包含方法论指导
- ch11+ 的 prewrite prompt 不含信息密度规则（仅前 10 章敏感）
- 测试通过

---

### T5：让 chapter_llm_quality_judge 看到方法论本体

**目标**：当前 chapter 裁判 prompt 提到 `methodology_compliance` 维度（阈值 0.80），但裁判看不到方法论是什么。现在把对应章节阶段的方法论原文注入到裁判 prompt 中。

**文件**：`src/bestseller/services/chapter_llm_quality_judge.py`

**操作**：

1. **改函数签名**，新增 `pack` 参数：

```python
async def judge_chapter_commercial_quality(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapter_number: int,
    content_md: str,
    generation_input: Mapping[str, Any] | None = None,
    previous_chapters: Sequence[Mapping[str, Any]] = (),
    workflow_run_id: Any | None = None,
    pack: "PromptPack | None" = None,  # NEW
) -> LLMQualityJudgeResult:
```

2. **构造方法论参考 block**（在 `completion = await complete_text(...)` 之前）：

```python
from bestseller.services.methodology_bridge import get_fragment

methodology_refs: list[str] = []
# 黄金三章：注入开篇规则 + 角色设计
if chapter_number <= 3:
    for key in ("opening_rules", "character_design"):
        text = get_fragment(pack, phase="judge", fragment_key=key)
        if text:
            methodology_refs.append(f"【{key}】\n{text}")
# 所有章节：注入弹簧法 + 筹码设计
for key in ("spring_model", "stakes_design"):
    text = get_fragment(pack, phase="judge", fragment_key=key)
    if text:
        methodology_refs.append(f"【{key}】\n{text}")
# 钩子设计
text = get_fragment(pack, phase="judge", fragment_key="hook_design")
if text:
    methodology_refs.append(f"【hook_design】\n{text}")

methodology_section = (
    "\n## 评估时必须参照的方法论标准\n\n"
    "以下是本作类型的写作方法论原文。"
    "你的 methodology_compliance 评分必须基于本章是否遵循了这些规则，"
    "而不是凭感觉打分。打分时请在 audit_issues 或 blocking_issues 的 evidence 字段"
    "引用具体违反的方法论条款。\n\n"
    + "\n\n".join(methodology_refs)
    if methodology_refs
    else ""
)
```

3. **修改 user_prompt 拼装**，在末尾加入 `methodology_section`：

```python
user_prompt=(
    f"章节：第{chapter_number}章\n"
    # ... 原有内容
    f"{methodology_section}"   # 新增
    f"\n正文：\n{content_md[:18000]}"
),
```

4. **调用方修改**：`src/bestseller/services/reviews.py:6291`

```python
# OLD
llm_judge_result = await judge_chapter_commercial_quality(
    session, settings,
    chapter_number=chapter.chapter_number,
    content_md=draft.content_md,
    generation_input=generation_input,
    workflow_run_id=workflow_run_id,
)

# NEW
from bestseller.services.prompt_packs import resolve_prompt_pack
_pack_name = (project.metadata_json or {}).get("prompt_pack_name")
_pack = resolve_prompt_pack(_pack_name) if _pack_name else None

llm_judge_result = await judge_chapter_commercial_quality(
    session, settings,
    chapter_number=chapter.chapter_number,
    content_md=draft.content_md,
    generation_input=generation_input,
    workflow_run_id=workflow_run_id,
    pack=_pack,  # NEW
)
```

**单元测试**：扩展现有 chapter judge 测试

```python
def test_chapter_judge_prompt_includes_methodology_for_golden_three() -> None:
    """For ch1-3, the judge prompt must reference opening_rules and spring_model."""
    # Mock complete_text to capture the prompt sent, then assert content
    ...

def test_chapter_judge_prompt_excludes_opening_rules_for_chapter_11() -> None:
    """For ch11+, opening_rules should NOT be in the prompt."""
    ...
```

**成功标准**：
- chapter judge 的 user_prompt 在 ch1-3 时长度增加（含开篇规则原文）
- chapter judge 的 user_prompt 在 ch11+ 时仍含 spring_model/stakes_design，但不含 opening_rules
- 测试通过

---

### T6：让 outline_llm_judge 看到方法论本体

**目标**：和 T5 类似。outline 裁判要拿到方法论本体作为评分依据。

**文件**：`src/bestseller/services/outline_llm_judge.py`

**操作**：

1. **`judge_outline_commercial_readiness` 函数加 `pack` 参数**：

```python
async def judge_outline_commercial_readiness(
    session: AsyncSession,
    settings: AppSettings,
    *,
    outline_payload: Mapping[str, Any],
    project_brief: Mapping[str, Any] | None = None,
    threshold: float = 0.82,
    workflow_run_id: Any | None = None,
    pack: "PromptPack | None" = None,  # NEW
) -> LLMQualityJudgeResult:
```

2. **在 system_prompt 之前注入方法论参考**（位于 line 85 之前）：

```python
from bestseller.services.methodology_bridge import get_fragment

methodology_refs: list[str] = []
for key in ("opening_rules", "character_design", "reversal_design",
            "climax_design", "spring_model", "stakes_design"):
    text = get_fragment(pack, phase="judge", fragment_key=key)
    if text:
        methodology_refs.append(f"【{key}】\n{text}")

methodology_reference = (
    "\n\n## 评估时必须参照的方法论标准\n"
    "以下是本作类型的写作方法论原文。"
    "你对 methodology_compliance / opening_pull / commercial_pull 等维度的评分"
    "必须基于大纲是否遵循这些规则。在 blocking_issues 的 evidence 字段中引用具体违反的条款。\n\n"
    + "\n\n".join(methodology_refs)
    if methodology_refs
    else ""
)
```

3. **修改 system_prompt 拼接**，把 methodology_reference 加到末尾：

```python
system_prompt=(
    "你是商业小说总编，只输出严格 JSON。"
    # ... 原有内容
    + methodology_reference  # NEW
),
```

4. **同样改造 `judge_commercial_planning_readiness`**（同文件下面，位于 line 300-450 之间）：把同样的 `methodology_refs` 逻辑接入，pass 方式同上。

5. **调用方修改**：`src/bestseller/services/workflows.py:1219` 和 `src/bestseller/services/pipelines.py:7875` 区域；以及 `pipelines.py` 里 `judge_commercial_planning_readiness` 的调用点。

```python
# 在每个调用点前
from bestseller.services.prompt_packs import resolve_prompt_pack
_pack_name = (project.metadata_json or {}).get("prompt_pack_name")
_pack = resolve_prompt_pack(_pack_name) if _pack_name else None

# 然后传入 pack=_pack 参数
```

**成功标准**：outline judge 和 commercial_planning judge 都在 prompt 中包含方法论原文。

---

### T7：新增 outline_reader_experience_judge

**目标**：模拟"新读者从 ch1 开始读"的视角，检测：
- 空间逻辑断裂（v194 暴露的问题）
- 信息密度爆炸（一章涌入太多角色/术语）
- 召唤合理性是否够
- 章末钩子前提是否已建立

**文件**：新建 `src/bestseller/services/outline_reader_experience_judge.py`

**完整代码**：

```python
"""Reader-experience LLM judge for outline.

Simulates a first-time reader experience for ch1-10.  Specifically validates:
- Spatial coherence (no abrupt teleports between scenes)
- Information density (≤3 named characters, ≤2 new concept terms in ch1)
- Protagonist call-to-action plausibility
- Chapter-end hook prerequisite check
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.llm_quality_judge import (
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
)
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.methodology_bridge import get_fragment
from bestseller.services.outline_llm_judge import _parse_json_object
from bestseller.services.prompt_packs import PromptPack
from bestseller.settings import AppSettings


READER_EXPERIENCE_DIMENSIONS: tuple[str, ...] = (
    "spatial_coherence",
    "information_density",
    "protagonist_call_plausibility",
    "hook_prerequisite_satisfied",
    "motivation_chain_clarity",
    "first_read_followability",
)


async def judge_outline_reader_experience(
    session: AsyncSession,
    settings: AppSettings,
    *,
    chapters_payload: list[Mapping[str, Any]],  # ch1-10 outline data
    project_brief: Mapping[str, Any] | None = None,
    threshold: float = 0.78,
    workflow_run_id: Any | None = None,
    pack: PromptPack | None = None,
) -> LLMQualityJudgeResult:
    """Run a reader-experience judge over ch1-10 outline."""
    # Filter to ch1-10 only
    golden = [
        ch for ch in chapters_payload
        if 1 <= int(ch.get("chapter_number") or ch.get("number") or 0) <= 10
    ]
    if not golden:
        return _empty_result(reason="No chapters in scope")

    # Methodology context
    methodology_refs: list[str] = []
    for key in ("opening_rules", "character_design"):
        text = get_fragment(pack, phase="judge", fragment_key=key)
        if text:
            methodology_refs.append(f"【{key}】\n{text}")
    methodology_block = "\n\n".join(methodology_refs) if methodology_refs else ""

    chapters_text = json.dumps(golden, ensure_ascii=False, indent=2, default=str)
    if len(chapters_text) > 50000:
        chapters_text = chapters_text[:50000] + "\n...TRUNCATED..."

    brief_text = json.dumps(project_brief or {}, ensure_ascii=False, indent=2, default=str)[:3000]

    fallback = json.dumps(
        {
            "pass": True,
            "overall_score": 0.79,
            "dimension_scores": {},
            "blocking_issues": [],
            "audit_issues": [
                {
                    "code": "READER_EXPERIENCE_JUDGE_UNAVAILABLE",
                    "severity": "high",
                    "evidence": "Reader-experience judge fallback.",
                    "required_fix": "重跑评估或人工复核",
                }
            ],
            "rewrite_plan": {"scope": "outline", "preserve": [], "change": [], "instructions": ""},
        },
        ensure_ascii=False,
    )

    system_prompt = (
        "你是一名首次读者，没有任何前置知识。你将看到一份小说的前 10 章细纲，"
        "你需要假装从第 1 章第 1 句开始读，逐章评估你是否能跟上、能不能信任主角、"
        "信息密度是否过载、章末钩子前提是否已建立。"
        "\n\n严格只输出 JSON。你的评分必须基于读者体验而非作者意图。"
        f"\n\n## 评估维度（每项 0-1 分）\n"
        + "\n".join(f"- {dim}" for dim in READER_EXPERIENCE_DIMENSIONS)
        + "\n\n## 硬性卡控（出现任一即 blocking）"
        "\n1. 空间错乱：一章内角色物理位置变化无过渡（例：'外面下雨'→ '楼道灯灭'→ '冲上三楼'缺少进楼描写）"
        "\n2. 信息密度爆炸：第 1 章涌入超过 3 个具名角色，或超过 2 个未解释的高概念术语"
        "\n3. 召唤主角无依据：读者无法在本章看到'为什么是这个主角'的依据"
        "\n4. 钩子前提缺失：章末钩子依赖的设定未在本章建立"
        "\n5. 内部事实矛盾：本章建立的事实被本章自己违反（例：'主角第一次到 303'又写'上次去 303 时'）"
        + (
            f"\n\n## 评估时参照的方法论\n{methodology_block}"
            if methodology_block
            else ""
        )
    )

    user_prompt = (
        f"## 项目简介\n{brief_text}\n\n"
        f"## 黄金十章大纲\n{chapters_text}\n\n"
        "## 输出格式\n"
        '{"pass": bool, "overall_score": float, "dimension_scores": {dim: float}, '
        '"blocking_issues": [{code, severity, evidence, required_fix, chapter_no}], '
        '"audit_issues": [...], '
        '"rewrite_plan": {scope, preserve, change, instructions}}'
    )

    completion = await complete_text(
        session, settings,
        LLMCompletionRequest(
            logical_role="critic",
            model_tier="strong",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response=fallback,
            prompt_template="outline_reader_experience_judge",
            prompt_version="v1",
            workflow_run_id=workflow_run_id,
            metadata={"judge_scope": "reader_experience", "threshold": threshold},
            max_tokens_override=4096,
        ),
    )
    return quality_judge_result_from_mapping(
        _parse_json_object(completion.content),
        scope="reader_experience",
        min_overall=threshold,
        min_dimensions={
            "spatial_coherence": threshold - 0.05,
            "information_density": threshold - 0.05,
            "protagonist_call_plausibility": threshold,
            "hook_prerequisite_satisfied": threshold - 0.05,
        },
        llm_run_id=str(completion.llm_run_id) if completion.llm_run_id else None,
        raw_excerpt=completion.content[:5000],
    )


def _empty_result(*, reason: str) -> LLMQualityJudgeResult:
    return quality_judge_result_from_mapping(
        {"pass": True, "overall_score": 1.0, "blocking_issues": [],
         "audit_issues": [{"code": "OUT_OF_SCOPE", "severity": "low",
                          "evidence": reason, "required_fix": ""}]},
        scope="reader_experience", min_overall=0.0,
    )
```

**集成到 workflow**：在 `src/bestseller/services/workflows.py` 中，紧接 `judge_outline_commercial_readiness` 调用之后，新增 reader-experience judge 调用（约 line 1280 附近）：

```python
if (
    getattr(settings.pipeline, "enable_outline_reader_experience_judge", True)
    and _validation_batch.chapters
):
    from bestseller.services.outline_reader_experience_judge import (
        judge_outline_reader_experience,
    )
    _re_result = await judge_outline_reader_experience(
        session, settings,
        chapters_payload=[ch.model_dump(mode="json") for ch in _validation_batch.chapters],
        project_brief={...},  # 同 commercial judge 的 brief
        pack=_pack,
        workflow_run_id=workflow_run.id if workflow_run.id else None,
    )
    # persist + handle blocking ...
```

**新设置**：`src/bestseller/settings.py` 加入：

```python
enable_outline_reader_experience_judge: bool = True
outline_reader_experience_judge_block_on_failure: bool = True
outline_reader_experience_judge_threshold: float = 0.78
```

**成功标准**：
- 新 judge 可独立调用并返回结果
- 接入 workflow 后大纲生成时会跑这个 judge
- 跑 v120 卷大纲（已有数据）时能检测出 v194 暴露的空间断裂问题

---

### T8：方法论合规审计脚本

**目标**：写一个独立 CLI 脚本，可以扫描任意章节，运行所有 LLM judge，输出合规报告。

**文件**：新建 `scripts/audit_chapter_methodology_compliance.py`

**完整代码（骨架，Codex 补全）**：

```python
"""Audit existing chapters against the methodology pipeline.

Usage:
    uv run python scripts/audit_chapter_methodology_compliance.py \\
        --project-slug exorcist-detective-1778051012 \\
        --chapters 1-10 \\
        --output reports/audit-ch1-10.json
"""

import argparse
import asyncio
import json
from pathlib import Path

# ... import setup similar to other scripts ...

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--chapters", required=True, help="e.g. '1-10' or '1,3,5'")
    parser.add_argument("--output", required=True, help="JSON output path")
    parser.add_argument(
        "--judges", default="chapter,outline,reader_experience",
        help="Comma-separated judges to run"
    )
    args = parser.parse_args()

    # Parse chapter ranges
    chapter_numbers = _parse_range(args.chapters)

    findings = {"by_chapter": {}, "summary": {}}

    async with session_scope() as session:
        project = await _load_project(session, args.project_slug)
        for ch_no in chapter_numbers:
            chapter, draft = await _load_chapter_and_current_draft(session, project, ch_no)
            ch_findings = {}

            if "chapter" in args.judges:
                # Run chapter_llm_quality_judge
                from bestseller.services.chapter_llm_quality_judge import judge_chapter_commercial_quality
                pack = _resolve_pack(project)
                result = await judge_chapter_commercial_quality(
                    session, settings,
                    chapter_number=ch_no,
                    content_md=draft.content_md,
                    pack=pack,
                )
                ch_findings["chapter_judge"] = result.model_dump(mode="json")

            if "outline" in args.judges:
                # Run outline judge over the chapter's outline subset
                ...

            if "reader_experience" in args.judges and ch_no <= 10:
                # Run reader-experience judge on ch1-10 outline batch
                ...

            findings["by_chapter"][str(ch_no)] = ch_findings

    # Summary stats: pass/block count, average score, top recurring issues
    findings["summary"] = _summarize(findings["by_chapter"])

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(findings, ensure_ascii=False, indent=2))
    print(f"Audit complete. Report saved to {args.output}")


def _parse_range(spec: str) -> list[int]:
    """Parse '1-10' or '1,3,5' into sorted list of ints."""
    out: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if "-" in token:
            a, b = map(int, token.split("-"))
            out.update(range(a, b + 1))
        else:
            out.add(int(token))
    return sorted(out)


def _summarize(by_chapter: dict) -> dict:
    """Produce summary stats: pass rate, blocking issue frequency by code."""
    pass_count = 0
    block_count = 0
    issue_freq: dict[str, int] = {}
    for ch_no, ch_data in by_chapter.items():
        for judge_name, judge_result in ch_data.items():
            if judge_result.get("passed"):
                pass_count += 1
            else:
                block_count += 1
            for issue in judge_result.get("blocking_issues", []):
                code = issue.get("code", "unknown")
                issue_freq[code] = issue_freq.get(code, 0) + 1
    return {
        "total_judge_runs": pass_count + block_count,
        "pass_count": pass_count,
        "block_count": block_count,
        "pass_rate": pass_count / max(pass_count + block_count, 1),
        "top_issues": sorted(issue_freq.items(), key=lambda x: -x[1])[:10],
    }


if __name__ == "__main__":
    asyncio.run(main())
```

**单元测试**：跳过单元测试，验证方式是手动跑 audit on ch1。

**成功标准**：

```bash
uv run python scripts/audit_chapter_methodology_compliance.py \
    --project-slug exorcist-detective-1778051012 \
    --chapters 1 \
    --output /tmp/audit-ch1.json

cat /tmp/audit-ch1.json | jq '.summary'
# 期望：有 pass/block count，top_issues 列出 v194 的硬伤代码
```

---

### T9：综合测试 + 集成验证

**目标**：在重建 Docker 之前，确保所有新代码本地能跑、有测试覆盖。

**操作**：

```bash
# 1. 跑所有相关测试
uv run pytest \
    tests/unit/test_methodology_bridge.py \
    tests/unit/test_planner_methodology_injection.py \
    tests/unit/test_chapter_constraint_manifest.py \
    tests/unit/test_commercial_planning_readiness.py \
    tests/unit/test_review_services.py \
    tests/unit/test_output_validator.py \
    -v --no-cov

# 期望：全过

# 2. 全量回归（敏感单测）
uv run pytest tests/unit/ -k "not slow" -q --no-cov 2>&1 | tail -5
# 期望：no new failures vs baseline

# 3. 语法检查
for f in src/bestseller/services/methodology_bridge.py \
         src/bestseller/services/outline_reader_experience_judge.py \
         src/bestseller/services/chapter_llm_quality_judge.py \
         src/bestseller/services/outline_llm_judge.py \
         src/bestseller/services/chapter_constraint_manifest.py \
         src/bestseller/services/planner.py \
         src/bestseller/services/reviews.py \
         src/bestseller/services/workflows.py; do
    uv run python -c "import ast; ast.parse(open('$f').read())" && echo "$f: OK"
done
```

**成功标准**：全部通过。

---

### T10：Docker 重建 + 端到端验证

**操作**：

```bash
# 1. 重建（必须三个服务一起）
docker compose build --no-cache api worker scheduler

# 2. 重启
docker compose up -d api worker scheduler
sleep 10
docker compose ps --format "table {{.Name}}\t{{.Status}}" | grep -c healthy
# 期望：16

# 3. 验证 settings 加载新字段
docker compose exec api uv run python -c "
from bestseller.settings import load_settings
s = load_settings()
print('reader_experience:', getattr(s.pipeline, 'enable_outline_reader_experience_judge', None))
print('reader_experience_threshold:', getattr(s.pipeline, 'outline_reader_experience_judge_threshold', None))
"
# 期望：True / 0.78

# 4. 验证 bridge 在容器内可用
docker compose exec api uv run python -c "
from bestseller.services.methodology_bridge import get_fragment
from bestseller.services.prompt_packs import resolve_prompt_pack
pack = resolve_prompt_pack('suspense-mystery')
text = get_fragment(pack, phase='planner', fragment_key='opening_rules')
print('opening_rules length:', len(text))
print('first 100 chars:', text[:100])
"
# 期望：长度 > 0，包含中文方法论文本

# 5. 跑 audit script 对 ch1 v194 出报告
docker compose exec api uv run python scripts/audit_chapter_methodology_compliance.py \
    --project-slug exorcist-detective-1778051012 \
    --chapters 1 \
    --output /app/output/audit-ch1-v194.json

docker compose cp api:/app/output/audit-ch1-v194.json ./audit-ch1-v194.json
cat audit-ch1-v194.json | jq '.summary, .by_chapter."1".chapter_judge.blocking_issues'

# 期望：能看到方法论合规分数 + 具体硬伤代码列表
```

---

## 4. 整体成功验收清单

请逐项勾选：

- [ ] T1：`methodology_bridge.py` 存在，5 测试全过
- [ ] T2：`suspense-mystery.yaml` 含 `opening_rules`/`character_design`/`reversal_design` 三个 planner 碎片
- [ ] T3：`planner.py` 3 处 planner-phase 调用改用 bridge，测试通过
- [ ] T4：`render_prewrite_plan_prompt` 包含方法论上下文，ch1-10 验证看到信息密度规则
- [ ] T5：chapter judge prompt 注入方法论原文，ch1-3 测试能看到 opening_rules
- [ ] T6：outline judge 和 commercial_planning judge 都注入方法论，原文出现在 system_prompt 末尾
- [ ] T7：`outline_reader_experience_judge.py` 存在，集成到 workflow，settings 加入新字段
- [ ] T8：`audit_chapter_methodology_compliance.py` 可对 ch1 出报告
- [ ] T9：全部相关单元测试通过
- [ ] T10：Docker 重建 + 容器内 audit ch1 出报告

---

## 5. 注意事项

### 5.1 不要做的事

- ❌ **不要写新的 `scripts/repair_qingnang_*.py`**——任何修复都应通过 LLM judge 反馈循环
- ❌ **不要直接 SQL UPDATE `scene_cards.forbidden_actions`**——硬规则不再是用户反馈落地方式
- ❌ **不要把方法论复制到代码里**——所有方法论文本统一从 yaml 来
- ❌ **不要跳过测试**——每个改动必须有对应测试

### 5.2 边界条件

- **pack 为 None 时**：所有接口必须能优雅 fallback 到 master yaml，不能崩
- **fragment 都缺失时**：返回空字符串，不要返回 None 或抛异常
- **裁判 LLM 异常时**：用 fallback_response，记 `logger.exception`，不要静默吞噬
- **chapter_number 超出 1-N 范围时**：方法论注入按 chapter_number 条件分支（黄金三章 vs 后续）

### 5.3 性能考虑

- `_render_master_fragment` 用 `lru_cache` 缓存（最多 64 条，足够覆盖所有 phase × fragment）
- yaml 文件用 `load_methodology` 的 lru_cache 加载，多次调用零开销
- 注入到 prompt 的方法论文本不超过 5000 字符；超过则需裁剪关键段

### 5.4 回滚策略

如果 T1–T10 任一步骤导致生产线路异常：

```bash
# 撤销代码改动
git stash

# 回滚 Docker
docker compose build --no-cache api worker scheduler
docker compose up -d api worker scheduler

# 验证生产可用
curl http://localhost:8000/health
```

YAML 改动不会影响 DB，无需 DB 回滚。

---

## 6. 完成后的下一阶段（不在本计划内）

T10 完成并验证 audit 报告后，进入下一阶段：

- **Phase 2A**：根据 audit 报告，重生 ch1-3（清掉旧 canon_facts，让 v194 内容重新登记）
- **Phase 2B**：对 ch4-10 跑 audit，按 blocking 排序，逐章修复
- **Phase 2C**：把 audit 接入 worker 的 post-chapter hook，自动评估每章
- **Phase 2D**：autowrite 启动 ch11-500 连续生成

这些都依赖本计划 T1–T10 完成。

---

**完。**

Codex：请按 T1 → T10 顺序执行。每完成一个 task，输出该 task 的成功标准截图/日志，再开始下一个。遇到歧义或修改超出本计划描述时，**停止并请求明确指示**，不要自行扩展范围。
