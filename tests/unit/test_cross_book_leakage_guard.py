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
    "沈清雅",
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


# ── 注入根治回归网 (2026-07-14) —— 债务词银行同步 + 冷启动覆盖 + 死亡模板 ──────
# 证据书「龙椅上坐着我亡夫」「替亡人落最后一笔」连撞两本死亡+讨账,根因是十几个
# 注入面叠加。下面的网锁死修复点,防止回归。


@pytest.mark.unit
def test_debt_ledger_token_bank_is_retired_and_empty() -> None:
    """2026-08-02: 债务词银行连同整套母题警察退役。

    这份词表原本是"单一事实源防漂移"的正确工程做法——错的是它服务的目的：
    用词表判定一本书能不能存在。债务是普通故事材料，框架无权因为它出现就
    毙掉产物；跨书同质化由隔离 + 指纹查重解决。词表保留为空元组，只为让
    残余 import 不炸。"""

    from bestseller.services import anti_default_motif, conception

    assert conception._DEBT_LEDGER_TOKENS is anti_default_motif.DEBT_LEDGER_TOKENS
    assert anti_default_motif.DEBT_LEDGER_TOKENS == ()


@pytest.mark.unit
def test_cold_start_cliche_baseline_covers_action_progression() -> None:
    """玄幻/仙侠/修仙/末日 全解析到 action-progression;冷启动(清库/首本)反俗套
    avoid 清单不得为空,且必须含"死者归来/借尸还魂"这条(否则模型均值回归死亡模板)。"""

    from bestseller.services.conception import _genre_cliche_baseline

    for genre in ("玄幻", "东方玄幻", "仙侠", "修仙"):
        base = _genre_cliche_baseline(genre, None)
        assert base, f"{genre} 冷启动反俗套清单为空"
        assert any(
            ("借尸还魂" in str(e)) or ("死者归来" in str(e)) for e in base
        ), f"{genre} 冷启动清单未覆盖死者归来/借尸还魂"


@pytest.mark.unit
def test_story_appeal_generic_lexicon_stays_free_of_death_words() -> None:
    """死亡/灭门/绝症类词只许在题材专属词库;回流 generic 会奖励所有题材(含喜剧/
    甜宠/治愈)的简介写死亡,造成跨题材同质化——债务词的死亡孪生。"""

    data = yaml.safe_load((CONFIG_ROOT / "story_appeal.yaml").read_text(encoding="utf-8"))
    generic = (data.get("lexicons", {}) or {}).get("generic", {}) if isinstance(data, dict) else {}
    death_words = ("灭门", "被灭", "血海", "死局", "死亡", "绝症", "病危", "跳楼", "屠戮", "屠仙")
    violations: list[str] = []
    for key, value in (generic or {}).items():
        for text in _iter_yaml_strings(value):
            hits = [w for w in death_words if w in text]
            if hits:
                violations.append(f"lexicons.generic.{key}: {hits} in {text!r}")
    assert not violations, "死亡/灭门词回流 generic 评分词库：\n" + "\n".join(violations)


@pytest.mark.unit
def test_genre_emotion_exemplars_do_not_lead_with_death() -> None:
    """finalize 会把首个示例提到 synopsis 最前;xuanhuan/xianxia/generic 的首项
    不得是灭门/血海/屠戮类死亡词,否则每本书都从死亡爆点起手(死者归来模板温床)。"""

    data = yaml.safe_load((CONFIG_ROOT / "story_appeal.yaml").read_text(encoding="utf-8"))
    ex = data.get("genre_emotion_exemplars", {}) if isinstance(data, dict) else {}
    death_lead = ("灭门", "血海", "屠", "血仇", "灭宗", "屠仙")
    for key in ("xuanhuan", "xianxia", "generic"):
        items = ex.get(key) or [""]
        first = str(items[0])
        assert not any(
            w in first for w in death_lead
        ), f"{key} 情绪示例以死亡词领跑：{first}"


@pytest.mark.unit
def test_single_book_material_pack_builders_are_deleted() -> None:
    """2026-07-31 裁决：单书参考包（历史书私有世界）从源码整体删除。此前这里
    检查它们不得回流账本/债务框架；现在的更强保证是——它们不存在。"""

    from bestseller.services import material_density

    for name in (
        "_build_qingnang_pack",
        "_looks_like_qingnang",
        "_xianxia_upgrade_pack_spec",
        "_female_no_cp_pack_spec",
        "_english_romantasy_pack_spec",
        "_breaking_point_pack_spec",
        "_witness_protocol_pack_spec",
    ):
        assert not hasattr(material_density, name), name


@pytest.mark.unit
def test_planner_shape_counter_examples_stay_genre_neutral() -> None:
    """planner 的 world/cast "正确/错误结构示范"只教 JSON 形状,不得写死某题材内容
    或具名角色(王青峰/李墨白/血契/器灵契约/妖族/祖庭/魂池/血脉之争/青萝镇)——否则
    每本书的 world/cast prompt 都被这套修仙示范往修仙腔带。用 〈占位符〉 代替。"""

    from bestseller.services import planner

    forbidden = (
        "王青峰", "李墨白", "血契", "器灵契约", "妖族", "祖庭", "魂池",
        "血脉之争", "青萝镇", "Elena", "Kell", "bloodline rivalry", "Blood Covenant",
    )
    consts = (
        planner._WORLD_SPEC_COUNTER_EXAMPLES_ZH,
        planner._WORLD_SPEC_COUNTER_EXAMPLES_EN,
        planner._CAST_SPEC_COUNTER_EXAMPLES_ZH,
        planner._CAST_SPEC_COUNTER_EXAMPLES_EN,
    )
    violations = [tok for c in consts for tok in forbidden if tok in c]
    assert not violations, (
        "planner 形状示范里出现题材/单书具体内容（会把所有书往该题材带）：" + str(violations)
    )


# 单书参考包：其 spec 内容是那一本 demo 书的私有世界，只许被【本书专属标识】唤醒。
_SINGLE_BOOK_PACK_IDS: frozenset[str] = frozenset(
    {
        "qingnang",
        "english_romantasy",
        "english_superhero_breaking_point",
        "english_superhero_witness_protocol",
        "female_no_cp_apocalypse",
        "xianxia_upgrade",
    }
)

# 裸题材词 / 体系通用术语 / 常见人名：任何一个都不足以证明"这就是那本书"。
_NON_IDENTIFYING_TRIGGERS: frozenset[str] = frozenset(
    {
        # 修仙体系通用术语——几乎每本修仙书的设定里都有
        "炼气", "筑基", "金丹", "元婴", "结丹", "灵根", "宗门", "杂役",
        # 裸题材词
        "仙侠升级", "宗门逆袭", "修仙", "仙侠", "玄幻", "末世", "末世异能",
        "无cp", "无CP", "大女主", "romantasy", "fae", "chosen one",
        "urban fantasy", "cultivation", "xianxia", "apocalypse",
        # 常见人名
        "maya", "cole", "kade", "elena", "sophie", "marcus",
    }
)


def _single_book_pack_triggers() -> dict[str, list[str]]:
    """从 `_select_material_pack` 的 AST 里提取 {pack_id: [触发词...]}。

    只认 `if _has_any(hay, (...)): return "<pack_id>", ...` 这一种形状——它正是
    全部单书路由的写法。用 AST 而非正则，改了写法会在这里显性失败而不是静默漏检。
    """

    source = (SRC_ROOT / "services" / "material_density.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_select_material_pack"
    )
    triggers: dict[str, list[str]] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Call):
            continue
        call = node.test
        if not (isinstance(call.func, ast.Name) and call.func.id == "_has_any"):
            continue
        if len(call.args) < 2 or not isinstance(call.args[1], ast.Tuple):
            continue
        returns = [n for n in node.body if isinstance(n, ast.Return)]
        if not returns or not isinstance(returns[0].value, ast.Tuple):
            continue
        first = returns[0].value.elts[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        needles = [
            e.value
            for e in call.args[1].elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        triggers.setdefault(first.value, []).extend(needles)
    return triggers


@pytest.mark.unit
def test_single_book_pack_triggers_are_all_book_identifying() -> None:
    """单书参考包的触发词必须条条都是【本书专属标识】。

    修前 xianxia_upgrade 挂着 `炼气`(修仙一阶通用术语)+ 裸题材词 `仙侠升级`/`宗门逆袭`,
    于是【每一本】修仙书都被灌进这一本参考书的私有世界(杂役峰/废灵根旧事/二十年前
    旧事/三个月大考)——即"修仙题材跨书同质化"的物料源。english_romantasy 同理挂过
    裸 `romantasy`/`fae`。样例输入的测试只能证伪已知输入,这条从源头锁住触发词本身。
    """

    triggers = _single_book_pack_triggers()
    assert triggers, "未能从 _select_material_pack 提出任何触发词——路由写法变了，请更新本守卫"

    violations: list[str] = []
    for pack_id, needles in sorted(triggers.items()):
        if pack_id not in _SINGLE_BOOK_PACK_IDS:
            continue  # 题材级通用包(如 english_superhero_progression)本就该用题材词唤醒
        for needle in needles:
            if needle.strip().lower() in {t.lower() for t in _NON_IDENTIFYING_TRIGGERS}:
                violations.append(f"{pack_id} ← {needle!r}")
    assert not violations, (
        "单书参考包被裸题材词/体系通用术语/常见人名唤醒 —— 任意同题材书都会被灌进"
        "那一本 demo 书的私有世界。改用本书专属标识(书名/金手指专名/独有地名)：\n"
        + "\n".join(violations)
    )


@pytest.mark.unit
def test_single_book_material_packs_not_triggered_by_generic_names() -> None:
    """单书参考包(青囊/Breaking Point/Witness Protocol/代价之鸢)绝不能被裸通用词或
    常见人名(maya/cole/kade/末世异能/无CP/superhero)路由命中——否则任意带这些词的
    书都会被灌进那一本 demo 书的世界(单书私货注入)。"""

    from bestseller.services.material_density import _select_material_pack

    generic_inputs = [
        "一个叫 Maya 的女孩在末世异能无CP设定里挣扎求生",  # maya + 末世异能 + 无CP
        "Cole is a superhero in an urban power fantasy",  # cole + superhero
        "Kade must survive; a normal urban story",  # kade
    ]
    single_book_packs = {
        "qingnang",
        "english_superhero_breaking_point",
        "english_superhero_witness_protocol",
        "female_no_cp_apocalypse",
    }
    violations: list[str] = []
    for text in generic_inputs:
        pack_id, _ = _select_material_pack("proj-test", text)
        if pack_id in single_book_packs:
            violations.append(f"{text!r} → {pack_id}")
    assert not violations, (
        "通用词/常见人名路由命中了单书专属包（单书世界被注入无关书）：\n"
        + "\n".join(violations)
    )
