"""建书页选项的传导契约(回归网)。

用户报「新建创作时的组合题材和这些选项好像没有起到作用」。审计确认:七个旋钮里
只有「题材→prompt_pack」一条真的接着线,其余多为"一坨 JSON 塞进 prompt、没有任何
门回读"。本文件锁住这一轮修好的每一条,防止再次静默腐烂。

每个测试都对应一个真实病灶(注释里写明修前的行为)。
"""

from __future__ import annotations

import pytest

from bestseller.services.genre_intent_contract import contract_from_selection


# ── 选项必须真的到达下游,而不是只进构思的 JSON blob ────────────────────────


@pytest.mark.unit
def test_tone_preference_reaches_the_writers_style_guide() -> None:
    """修前:tone_preference 全库只到达构思 prompt 里的一坨 JSON;写手真正用的
    style_guide.tone_keywords 来自 preset/模型,与用户选择零关系 ⇒ 选「轻松」
    或「暗黑」对正文毫无影响。"""

    from bestseller.domain.project import ProjectCreate
    from bestseller.services.projects import _tone_keywords_for

    class _Style:
        tone_keywords = ["克制", "紧张"]

    class _Profile:
        style = _Style()

    def _payload(tone: str | None) -> ProjectCreate:
        contract = contract_from_selection(
            {"channel": "male", "genre": "玄幻", "sub_genre": None, "tags": []},
            tone_preference=tone,
        )
        return ProjectCreate(
            slug="tone-probe",
            title="T",
            genre="玄幻",
            target_word_count=100000,
            target_chapters=24,
            metadata={"genre_intent_contract": contract.model_dump(mode="json")},
        )

    # 未选 → 保持 profile 自己的调性
    assert _tone_keywords_for(_payload(None), _Profile()) == ["克制", "紧张"]
    # 选了 → 用户的调性领跑(题材边界内生效,故 profile 的词跟在后面)
    light = _tone_keywords_for(_payload("light"), _Profile())
    assert light[0] == "轻松"
    assert "克制" in light  # 领跑而非替换
    assert _tone_keywords_for(_payload("dark"), _Profile())[0] == "暗黑"
    assert _tone_keywords_for(_payload("hot"), _Profile())[0] == "热血"


@pytest.mark.unit
def test_full_creation_contract_controls_persisted_writer_profile() -> None:
    from bestseller.domain.project import ProjectCreate
    from bestseller.services.writing_profile import resolve_project_create_writing_profile

    genre_intent = contract_from_selection(
        {"channel": "male", "genre": "玄幻", "sub_genre": None, "tags": []},
        tone_preference="light",
    ).model_dump(mode="json")
    payload = ProjectCreate(
        slug="creation-profile-probe",
        title="T",
        genre="玄幻",
        target_word_count=130_000,
        target_chapters=50,
        metadata={
            "genre_intent_contract": genre_intent,
            "creation_intent_contract": {
                "pov": "first_person",
                "tone_preference": "light",
            },
        },
    )

    profile = resolve_project_create_writing_profile(payload)

    assert profile.style.pov_type == "first_person"
    assert profile.style.tone_keywords[:3] == ["轻松", "幽默", "明快"]


@pytest.mark.unit
def test_creativity_direction_reaches_the_real_engine() -> None:
    """修前:真引擎 GenreCreativeDirection 由 payload['creative_key'] 驱动,而
    快速建书 UI 从来不发这个字段(它发的是 story_enhancers.creativity_direction),
    ⇒ direction 恒为 None、creative_hints 恒为 {},用户选的方向只剩一个标签字符串。
    两者 key 空间本就相同,接上即可。"""

    from bestseller.services.genre_creativity import (
        creative_direction_to_user_hints,
        get_genre_creative_direction,
    )
    from bestseller.services.story_enhancers import CREATIVITY_DIRECTIONS

    for key in CREATIVITY_DIRECTIONS:
        direction = get_genre_creative_direction("custom-xuanhuan", key)
        assert direction is not None, f"UI 的 {key} 应能命中真引擎"
        hints = creative_direction_to_user_hints(direction)
        assert hints, f"{key} 应产出非空 creative_hints"
        assert "anti_cliche_guardrails" in hints


# ── 选项不得冒充/不得越权 ──────────────────────────────────────────────────


@pytest.mark.unit
def test_genre_default_tags_are_not_presented_as_user_choices() -> None:
    """修前:选「东方玄幻」会把子题材的 default_tags(废柴逆袭/升级流/血脉觉醒)
    并进 tags,再整体标成【建书页明确选择】喂给模型 —— 等于用用户的名义,向每本
    同子题材的书强推同一批套路(隐性跨书同质化)。"""

    from bestseller.services.conception import _creation_intent_prompt_block

    contract = contract_from_selection(
        {"channel": "male", "genre": "玄幻", "sub_genre": "eastern-xuanhuan", "tags": []},
        audience_orientation="male",
    )
    payload = contract.model_dump(mode="json")
    assert payload["user_tags"] == ()  or list(payload["user_tags"]) == []
    assert "废柴逆袭" in list(payload["default_tags"])

    block = _creation_intent_prompt_block(
        {"genre_intent_contract": payload, "language": "zh-CN"}
    )
    # 用户没点任何 tag → 「明确选择」里就不能出现 tag
    assert '"tags": []' in block
    # 但题材默认标签仍可见,只是被诚实地另行标注
    assert "genre_default_tags" in block


@pytest.mark.unit
def test_empty_creation_form_injects_nothing() -> None:
    """「空表单零注入」是产品契约。修前 narrative_scale 恒为 "serial"(UI 默认值,
    每次提交都发)使守卫的 any() 恒真 ⇒ 块永远渲染,顺带泄漏 default_tags。"""

    from bestseller.services.conception import _creation_intent_prompt_block

    contract = contract_from_selection(
        {"channel": None, "genre": "玄幻", "sub_genre": None, "tags": []},
        narrative_scale="serial",  # UI 的默认值,并非用户的选择
    )
    block = _creation_intent_prompt_block(
        {"genre_intent_contract": contract.model_dump(mode="json"), "language": "zh-CN"}
    )
    assert block == ""

    # 显式选了「宏大长篇」才算真选择
    explicit = contract_from_selection(
        {"channel": None, "genre": "玄幻", "sub_genre": None, "tags": []},
        narrative_scale="epic",
    )
    assert _creation_intent_prompt_block(
        {"genre_intent_contract": explicit.model_dump(mode="json"), "language": "zh-CN"}
    )


@pytest.mark.unit
def test_comedy_effect_does_not_redefine_the_books_genre() -> None:
    """修前:勾一个 comedy_engine,六个大纲 prompt + 写手都会被告知
    「本书是爽文喜剧」—— 一个复选框改写了整本书的题材身份(在悬疑书上也一样)。"""

    from bestseller.services.story_enhancers import (
        StoryEnhancerSelection,
        render_story_enhancer_contract_block,
    )

    block = render_story_enhancer_contract_block(
        StoryEnhancerSelection(effect_skills=("comedy_engine",)), language="zh-CN"
    )
    assert "本书是爽文喜剧" not in block  # 不得断言题材身份
    assert "已勾选【喜剧效果】" in block  # 只陈述"用户勾了这个效果"
    assert "不得据此把本书改写成喜剧题材" in block  # 明确边界


# ── 目录 ↔ taxonomy 不得再漂成两套词汇表 ────────────────────────────────────


@pytest.mark.unit
def test_every_preset_card_resolves_into_a_genre_contract() -> None:
    """62 张卡张张都必须能建出契约。

    根因是目录与 taxonomy 长成了**两套互不相认的词汇表**:33 个卡片题材词 vs 20 个
    taxonomy 标签只有 4 个字面重合;24 张英文卡(UI 照常渲染)在 zh-only 的 taxonomy
    里完全无解 → build_genre_intent_contract raise → 那些书**根本没有题材契约**,
    于是模型可以随手改写 project.genre(apply_book_spec 只让位于"存在的"契约),
    tone_preference 也因为走契约传导而被丢掉。题材层靠 alias 兜住了(末日科幻→末世),
    子题材层没有 alias 就整层丢失。
    """

    from bestseller.services.writing_presets import list_genre_presets

    cards = [c if isinstance(c, dict) else c.model_dump() for c in list_genre_presets()]
    assert len(cards) >= 60

    failed: list[str] = []
    for card in cards:
        try:
            contract_from_selection(
                {
                    "channel": None,
                    "genre": card["genre"],
                    "sub_genre": card["sub_genre"],
                    "tags": [],
                }
            )
        except Exception as exc:  # noqa: BLE001 - 任何异常都等于这张卡建不出契约
            failed.append(f"{card['key']} ({card['genre']}/{card['sub_genre']}): {exc}")
    assert not failed, (
        "预设卡建不出题材契约 —— 该卡的题材/子题材在 taxonomy 里无解。"
        "给 taxonomy 补 alias 或补条目，不要让目录和 taxonomy 各说各话：\n"
        + "\n".join(failed)
    )


@pytest.mark.unit
def test_preset_card_pack_agrees_with_the_taxonomy_it_resolves_to() -> None:
    """卡片自带 prompt_pack_key,taxonomy 也推导 pack —— 两者不许打架。

    `GenrePreset` 有自己的 `prompt_pack_key`,是与 taxonomy **平行**的一套系统
    (这正是"七个旋钮只有题材→pack 一条真接着线"的由来)。两套都活着就必须一致,
    否则同一张卡按走哪条路会得到不同的物料/包,而这种分叉是静默的。
    """

    from bestseller.services.genre_taxonomy import resolve_selection
    from bestseller.services.writing_presets import list_genre_presets

    mismatches: list[str] = []
    for card in (c if isinstance(c, dict) else c.model_dump() for c in list_genre_presets()):
        declared = card.get("prompt_pack_key")
        if not declared:
            continue  # 卡片没声明 → 由 taxonomy 说了算,无冲突
        sel = resolve_selection(None, card["genre"], card["sub_genre"], [])
        if sel.sub_genre_key is None:
            continue  # 子题材未解析 → 只剩题材级默认包,不构成分叉
        if sel.pack and sel.pack != declared:
            mismatches.append(f"{card['key']}: 卡片={declared} vs taxonomy={sel.pack}")
    assert not mismatches, (
        "预设卡自带的 prompt_pack_key 与它在 taxonomy 里解析出的 pack 不一致：\n"
        + "\n".join(mismatches)
    )


# ── 选项不得成为单书私货的注入口 ────────────────────────────────────────────


@pytest.mark.unit
def test_generic_books_never_get_a_single_book_material_pack() -> None:
    """修前:`炼气`(修仙一阶通用词)/`fae`/`romantasy`/`末世异能` 这类裸题材词
    会把某一本参考书的私有世界灌进【每一本】同题材书(道种破虚的杂役峰/二十年前
    旧事、Shadowbound 的 Summer&Winter Court、代价之鸢的方舟城/源初)。"""

    from bestseller.services.material_density import _select_material_pack

    single_book_packs = {
        "qingnang",
        "xianxia_upgrade",
        "english_romantasy",
        "female_no_cp_apocalypse",
        "english_superhero_breaking_point",
        "english_superhero_witness_protocol",
    }
    generic_books = [
        ("少年在宗门炼气三层，靠机缘一路突破", "玄幻", "东方玄幻"),
        ("仙侠升级流，宗门逆袭", "仙侠", None),
        ("a fae romantasy with a chosen one heroine", "Fantasy Romance", None),
        ("末世异能无CP大女主求生", "末世", None),
        ("a superhero named Maya and her rival Cole", "Fantasy", None),
    ]
    for text, genre, sub_genre in generic_books:
        pack_id, _ = _select_material_pack("p", text, genre=genre, sub_genre=sub_genre)
        assert pack_id not in single_book_packs, f"{text!r} 被灌进单书包 {pack_id}"

    # 而真正属于那本书的输入仍应命中它自己的包
    pack_id, _ = _select_material_pack("p", "道种破虚：少年得道种，破虚而上", genre="玄幻")
    assert pack_id == "xianxia_upgrade"


@pytest.mark.unit
def test_cost_laws_are_seed_diverse_and_honour_cost_style() -> None:
    """修前:`_cost_laws_from_formula(formula)` 收了 formula 却一次都没读，直接
    bare-return 两条常量 ⇒ 全系统每本书、每个题材、每个 seed 共享同样的代价律
    ("costs": "关系、寿元、记忆或身份之一")，而它周围的母题/主题全是 seed 多样化的。
    这是跨书「代价/债务」同质化的上游总根。它同时无视 cost_style，使纯爽书的
    schema 范例反过来演示了一遍指令明令禁止的自损代价。"""

    from bestseller.services.ideology_kernel import fallback_ideology_kernel

    combos = {
        tuple(
            law["costs"]
            for law in fallback_ideology_kernel(premise=premise, title=title, volumes=3)[
                "cost_system"
            ]
        )
        for premise, title in [
            ("少年得到吞噬之力", "吞神证我"),
            ("女帝重生复仇", "凤归"),
            ("末世囤货求生", "方舟"),
            ("侦探查连环案", "雾隐"),
        ]
    }
    assert len(combos) >= 3, f"不同书的代价律必须分化，实得 {len(combos)}/4 种"

    # cost_style 必须改变范例本身,而不是只在结尾追加一句相反的指令
    external = fallback_ideology_kernel(
        premise="少年得到吞噬之力", title="吞神证我", volumes=3, cost_style="external"
    )
    blob = " ".join(law["costs"] for law in external["cost_system"])
    for self_harm in ("寿元折损", "记忆被吃掉", "道心裂痕", "血脉灼烧"):
        assert self_harm not in blob, f"external 档不该出现自损代价：{self_harm}"


def test_concept_seed_input_exists_and_is_sent_by_the_form() -> None:
    """concept_seed 断链修复(2026-07-24)。

    后端链路一直就绪:server.py 读 payload["concept_seed"] → user_hints →
    conception 的 explicit_concept_seed → 概念淘汰赛 seed_concept。但快速建书
    表单从未提供输入口,用户只能空题材掷硬币——当日 4 本书全部死在构思门禁,
    空样板 premise 是共同根因之一。钉住:输入框存在 + payload 真的发送该字段。
    """

    from pathlib import Path

    html = Path("src/bestseller/web/novel_quickstart.html").read_text(encoding="utf-8")

    assert 'id="conceptSeedInput"' in html, "创意输入框必须存在于建书表单"

    build_idx = html.index("function buildQuickstartPayload()")
    payload_region = html[build_idx : build_idx + 2500]
    assert "concept_seed" in payload_region, (
        "buildQuickstartPayload 必须发送 concept_seed——输入框存在但不发送,"
        "就是又一个『看得见选不上』的假选项"
    )


def test_concept_seed_server_read_matches_the_form_key() -> None:
    """表单键与服务端读取键必须一致(防止两侧各自改名后静默断链)。"""

    import inspect

    from bestseller.web import server as web_server

    source = inspect.getsource(web_server.WebTaskManager.create_quickstart_task)
    assert 'payload.get("concept_seed")' in source
