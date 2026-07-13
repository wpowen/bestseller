"""跨书私货泄漏守卫（回归网）。

一本书设计过程中的私设（规则名 / 地点 / 人名 / 机制词）绝不能进入通用层。
历史教训：《青囊不语问阴阳》的 镜债/账线/十七栋 等私货曾渗入十几个"通用"
闸门与配置，把后续所有书拽进同一套设定；债务/账本框架曾同时嵌在
修复兜底文案、方法论合同、品类模板、评分词库等五个"非生成"面。

本测试扫描两类面：
1. src/bestseller 的字符串字面量（AST 提取，注释与 docstring 天然豁免——
   历史说明写在注释里是合法的）；
2. 通用 config 的 YAML **值**（yaml.safe_load 解析，注释豁免）。

新增某本书的私设时：写进该书自己的 project 数据或按 pack/char_id 隔离的
私货块，不要写进通用面；若确属"只匹配本书自身物料"的条件分支或检测器
token 且无注入风险，在 ALLOWLIST 登记并注明原因。
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "bestseller"
CONFIG_ROOT = REPO_ROOT / "config"
DATA_ROOT = REPO_ROOT / "data"

# ── 已知单书私货 token（高精度，出现即视为泄漏） ────────────────────────────
SINGLE_BOOK_TOKENS: tuple[str, ...] = (
    "镜债",
    "账线",
    "账清",
    "镜局",
    "困魂镜",
    "青囊",
    "十七栋",
    "旧事馆",
    "天债册",
    "王建业",
    "张建军",
    "林正淳",
    "青崖诡事",
    "归墟会",
    "清水桥义庄",
    "蚀漏砚",
    "三短一长",
)

# ── src 豁免：这些文件里的私货 token 是"只匹配本书自身物料"的条件分支
#    或按 pack_id/char_id 隔离的私货块，已逐一核验不会注入其他书 ──────────
SRC_ALLOWLIST: frozenset[str] = frozenset(
    {
        # qingnang pack 私货块：全部在 `if pack_id == "qingnang"` 之内。
        "services/material_density.py",
        # priority markers 只匹配本书自己的 tags/hook/logline，不命中则不产出。
        "services/platform_title_workflow.py",
        # anchors 以 `if "青囊" in contract` 等条件挂在本书自己的物料上。
        "services/commercial_novel_gate.py",
    }
)

# ── config 豁免：单书样例/休眠段所在文件（已核验 loader 不渲染这些段） ────
CONFIG_ALLOWLIST: frozenset[str] = frozenset(
    {
        # 该书专属 canon，无任何代码加载。
        "canon_tianjilu.md",
        "metadata_glossary_tianjilu.json",
        # sample_profiles 按 char_id 查找，别的书 id 不匹配 → 休眠。
        "character_engine.yaml",
        # audit_sample / examples 段不被 loader 读取，仅作文档。
        "sensory_inventory.yaml",
        # 锚点为按书 opt-in 的风格预设；渲染默认只带 anti_ai_voice。
        "prose_style_anchors.yaml",
        # sample_belief_audit 不被渲染进 prompt。
        "information_choreography.yaml",
    }
)

# ── 债务框架回流检查 ────────────────────────────────────────────────────────
DEBT_TOKENS_GENERIC_LEXICON: tuple[str, ...] = (
    "欠债", "讨债", "逼债", "还债", "房贷", "账本", "欠条", "记账",
)
DEBT_TOKENS_BROAD: tuple[str, ...] = ("债", "账本", "欠账", "记账", "账簿")

# ── 题材中性生成/规划面的债务框架回流网（守卫盲区补网） ──────────────────────
# 高信号中文债务字符；刻意不含裸"账"以放过 账号/账户/账目 等合法技术词。
NEUTRAL_DEBT_TOKENS: tuple[str, ...] = (
    "债", "账本", "账单", "欠账", "旧账", "算账", "记账", "账簿",
    "账清", "收账", "讨债", "逼债", "还债", "欠条", "催收", "结算",
)


def _iter_src_files() -> Iterator[Path]:
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in SRC_ALLOWLIST:
            continue
        yield path


def _iter_string_literals(tree: ast.AST) -> Iterator[str]:
    """Yield non-docstring string constants from a parsed module."""

    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
        ):
            yield node.value


def _iter_yaml_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_yaml_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_yaml_strings(item)


@pytest.mark.unit
def test_no_single_book_tokens_in_src_string_literals() -> None:
    violations: list[str] = []
    for path in _iter_src_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - broken file surfaces elsewhere
            violations.append(f"{path}: unparseable ({exc})")
            continue
        for literal in _iter_string_literals(tree):
            hits = [token for token in SINGLE_BOOK_TOKENS if token in literal]
            if hits:
                rel = path.relative_to(REPO_ROOT).as_posix()
                excerpt = literal.strip().replace("\n", " ")[:60]
                violations.append(f"{rel}: {hits} in {excerpt!r}")
    assert not violations, (
        "单书私货 token 出现在通用 src 字符串里（会随闸门/prompt 泄漏给所有书）。"
        "要么移进该书自己的隔离块并在 SRC_ALLOWLIST 登记，要么删除：\n"
        + "\n".join(violations)
    )


@pytest.mark.unit
def test_no_single_book_tokens_in_universal_config_values() -> None:
    violations: list[str] = []
    for path in sorted(CONFIG_ROOT.rglob("*.yaml")):
        if path.name in CONFIG_ALLOWLIST:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:  # pragma: no cover
            violations.append(f"{path}: unparseable ({exc})")
            continue
        for text in _iter_yaml_strings(data):
            hits = [token for token in SINGLE_BOOK_TOKENS if token in text]
            if hits:
                rel = path.relative_to(REPO_ROOT).as_posix()
                excerpt = text.strip().replace("\n", " ")[:60]
                violations.append(f"{rel}: {hits} in {excerpt!r}")
    assert not violations, (
        "单书私货 token 出现在通用 config 的 YAML 值里。"
        "要么移进该书 project 数据，要么在 CONFIG_ALLOWLIST 登记（须先核验 loader 不渲染）：\n"
        + "\n".join(violations)
    )


@pytest.mark.unit
def test_generic_appeal_lexicon_stays_free_of_money_pressure_words() -> None:
    """金钱/债务压力词只许在 urban/realistic 专属词库；回流 generic 即回归。"""

    data = yaml.safe_load((CONFIG_ROOT / "story_appeal.yaml").read_text(encoding="utf-8"))
    lexicons = data.get("lexicons", {}) if isinstance(data, dict) else {}
    generic = lexicons.get("generic", {}) if isinstance(lexicons, dict) else {}
    violations: list[str] = []
    for key, value in (generic or {}).items():
        for text in _iter_yaml_strings(value):
            hits = [token for token in DEBT_TOKENS_GENERIC_LEXICON if token in text]
            if hits:
                violations.append(f"lexicons.generic.{key}: {hits} in {text!r}")
    assert not violations, (
        "债务/金钱压力词回流到 generic 评分词库（会奖励所有题材写账单剧情）：\n"
        + "\n".join(violations)
    )


@pytest.mark.unit
def test_hook_mechanisms_and_category_templates_stay_debt_free() -> None:
    """hook 机制库与品类骨架模板保持零债务框架（言情'旧怨/亏欠'等替代已落地）。"""

    violations: list[str] = []
    targets = [
        CONFIG_ROOT / "hook_mechanisms.yaml",
        *sorted((CONFIG_ROOT / "novel_categories").glob("*.yaml")),
    ]
    for path in targets:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for text in _iter_yaml_strings(data):
            hits = [token for token in DEBT_TOKENS_BROAD if token in text]
            if hits:
                rel = path.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{rel}: {hits} in {text.strip()[:60]!r}")
    assert not violations, (
        "债务/账本框架回流到 hook 机制库或品类骨架模板（会把债务设定模板化进整个品类）：\n"
        + "\n".join(violations)
    )


@pytest.mark.unit
def test_planning_fallback_strings_stay_debt_free() -> None:
    """修复兜底/规划默认值不得再携带债务话术（上次的头号漏网面）。"""

    forbidden = ("长线债务", "状态债", "线索债", "资源债", "规则债", "回收前文账本", "状态账本")
    files = (
        SRC_ROOT / "services" / "planner.py",
        SRC_ROOT / "services" / "planning_kernel.py",
        SRC_ROOT / "services" / "book_lifecycle_evidence_repair.py",
        SRC_ROOT / "services" / "methodology_overlay.py",
        SRC_ROOT / "services" / "premium_book_gate.py",
    )
    violations: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for literal in _iter_string_literals(tree):
            hits = [token for token in forbidden if token in literal]
            if hits:
                rel = path.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{rel}: {hits} in {literal.strip()[:60]!r}")
    assert not violations, (
        "债务话术回流到规划/修复兜底字符串：\n" + "\n".join(violations)
    )


@pytest.mark.unit
def test_generation_neutral_surfaces_stay_debt_free() -> None:
    """题材中性的生成/规划面保持零债务框架（守卫盲区补网）。

    历史盲区：data/methodology_sources 完全不在扫描内；reference_corpora/generic、
    motif_library、narrative_tree 也无债务网。这些面对所有题材/所有书生效，必须
    保持零债务框架。题材隔离项（wuxia 侠义债 / relationship 情感债 / urban 人情债与
    应收账款）经 resolve_*(genre) 只在本题材渲染，故意不纳入本网。
    """

    violations: list[str] = []

    yaml_targets = [
        *sorted((DATA_ROOT / "methodology_sources").rglob("*.yaml")),
        CONFIG_ROOT / "reference_corpora" / "generic.yaml",
        CONFIG_ROOT / "motif_library.yaml",
    ]
    for path in yaml_targets:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:  # pragma: no cover - 解析问题在别处暴露
            continue
        for text in _iter_yaml_strings(data):
            hits = [token for token in NEUTRAL_DEBT_TOKENS if token in text]
            if hits:
                rel = path.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{rel}: {hits} in {text.strip()[:60]!r}")

    narrative_tree = SRC_ROOT / "services" / "narrative_tree.py"
    tree = ast.parse(narrative_tree.read_text(encoding="utf-8"))
    for literal in _iter_string_literals(tree):
        hits = [token for token in NEUTRAL_DEBT_TOKENS if token in literal]
        if hits:
            rel = narrative_tree.relative_to(REPO_ROOT).as_posix()
            violations.append(f"{rel}: {hits} in {literal.strip()[:60]!r}")

    assert not violations, (
        "债务/账本框架回流到题材中性的生成/规划面（方法论卡 / generic 语料 / "
        "母题库 / 规划节点树），会随 prompt 泄漏给所有题材的书。移进按题材隔离的"
        "块或换非金融隐喻：\n" + "\n".join(violations)
    )
