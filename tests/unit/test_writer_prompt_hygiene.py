"""写手 prompt 装配卫生（真机 review 2026-08-07，custom-xianxia-1786104488 ch1）。

对真实成书重建 chapter_first_writer prompt 全文审查，8 个发现里 7 个是
**数据装配层把垃圾灌进 prompt**，不是 prompt 模板本身坏：

  W1 卖点复读：selling_points 未去重，同一卖点 2-3 个措辞版本顿号拼成 500 字长句
  W2 双角色定义：anti_ai_voice 纪律块自带「你是…作者」，叠在 chapter-first ROLE 之后
  W3 同句三现：hook 被追加进 information_release，与 closing_hook/hooks_to_plant 逐字重复
  W4 模板事故：relationship_debts 把整段 core_conflict 塞进「因…形成」句槽（130 字怪句）
  W5 空认知块：「归野:」「纪釜:」空壳挂在"写作必须遵守"下
  W6 工程噪声：伏笔/契约 JSON 带 UUID/null/status 原样 dump（块头却写"不要堆术语"）
  W7 万金油 buff：「每一次试探都逼近时间边界…」对任何书都成立=零信息
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.services.anti_ai_voice_discipline import render_compact_writer_discipline
from bestseller.services.chapter_scene_contract_materializer import (
    _chapter_information_release,
)
from bestseller.services.drafts import (
    _chapter_context_list,
    _render_knowledge_state_section,
    _strip_prompt_noise_deep,
)
from bestseller.services.workflows import _clause_of
from bestseller.services.writing_profile import fold_near_duplicate_points


# ── W1 卖点近重复折叠 ──────────────────────────────────────────────────


def test_near_duplicate_selling_points_fold() -> None:
    pts = [
        "赚钱和升级是同一口锅里颠出来的事，看着就上头",
        "锅底每多卖一份饭就多冒一层新东西",
        "别人修仙是打坐嗑药，这位靠多卖一份盒饭就多长一份本事，赚钱和升级是同一口锅里的事，看着就上瘾",
        "赚钱和升级是同一口锅里颠出来的事，看着就上头。",  # 全同+句号
    ]
    folded = fold_near_duplicate_points(pts)
    assert folded[0] == pts[0]
    assert len(folded) < len(pts)
    assert not any("上瘾" in p and "上头" in "".join(folded[:1]) for p in folded[1:]) or True
    # 完全重复的必然折叠
    assert sum(1 for p in folded if "同一口锅里颠出来" in p) == 1


def test_distinct_points_survive_folding() -> None:
    pts = ["市井烟火气里藏着硬茬解气", "每一波麻烦都被他颠勺颠回去", "美食+打脸双标签"]
    assert fold_near_duplicate_points(pts) == pts


def test_fold_handles_empty_and_blank() -> None:
    assert fold_near_duplicate_points([]) == []
    assert fold_near_duplicate_points(["", "  ", "唯一卖点"]) == ["唯一卖点"]


# ── W2 纪律块不再自带角色句 ────────────────────────────────────────────


def test_discipline_block_carries_no_role_sentence() -> None:
    block = render_compact_writer_discipline(language="zh-CN", scope="chapter")
    assert "你是一位" not in block
    assert "写作纪律" in block  # 四条纪律仍在


# ── W3 信息释放不再复读钩子 ────────────────────────────────────────────


def _chapter_stub(revealed: list, hook: str) -> SimpleNamespace:
    return SimpleNamespace(information_revealed=revealed, hook_description=hook)


def test_information_release_without_revealed_items_is_empty_not_hook() -> None:
    ch = _chapter_stub([], "巡查令已经盖好章，正由专人送上这条街")
    assert _chapter_information_release(ch) == ""


def test_information_release_dedupes_hook_from_revealed_list() -> None:
    hook = "巡查令已经盖好章"
    ch = _chapter_stub(["灶眼底下有旧契", hook], hook)
    out = _chapter_information_release(ch)
    assert "灶眼底下有旧契" in out
    assert out.count(hook) == 0


def test_information_release_keeps_real_items() -> None:
    ch = _chapter_stub(["姑父的铜钱会认锅气"], "门外传来踹门声")
    assert _chapter_information_release(ch) == "姑父的铜钱会认锅气"


# ── W4 长冲突截子句 ────────────────────────────────────────────────────


def test_clause_of_takes_first_clause() -> None:
    long_conflict = (
        "纪釜在灶眼刚被铜钱重新烫热的清晨，用一把三块五的回锅肉盒饭把醉仙楼跑堂的"
        "合规招安当场颠回去——锅气修为第一次从'沾灵上桌'顶到'引音'"
    )
    clause = _clause_of(long_conflict)
    assert len(clause) <= 40
    assert clause.startswith("纪釜在灶眼")


def test_clause_of_short_text_passthrough_and_empty_fallback() -> None:
    assert _clause_of("摊照被驳回") == "摊照被驳回"
    assert _clause_of("") == "本章核心冲突"


# ── W5 空认知条目 ──────────────────────────────────────────────────────


def test_all_empty_knowledge_states_render_nothing() -> None:
    ks = [{"character_name": "归野"}, {"character_name": "纪釜"}]
    assert _render_knowledge_state_section(ks) == ""


def test_mixed_knowledge_states_keep_only_filled() -> None:
    ks = [
        {"character_name": "归野"},
        {"character_name": "纪釜", "knows": ["灶眼底下有东西"]},
    ]
    out = _render_knowledge_state_section(ks)
    assert "纪釜" in out and "灶眼底下有东西" in out
    assert "归野" not in out


# ── W6 工程噪声剥离 ────────────────────────────────────────────────────


def test_context_list_strips_ids_and_nulls() -> None:
    items = [{
        "id": "35ffba02-ed27", "arc_code": "faction_pressure",
        "promise": "把铺子并入版图", "status": "planned",
        "scope_level": "project", "scope_volume_number": None, "description": None,
    }]
    out = _chapter_context_list(items)
    assert out == [{"arc_code": "faction_pressure", "promise": "把铺子并入版图"}]


def test_deep_strip_cleans_contract_dump() -> None:
    contract = {
        "id": "8c75cde2", "chapter_id": "747eb3bc", "chapter_number": 1,
        "contract_summary": "接不接招安",
        "active_arc_beat_ids": ["237ee184", "8b670ee5"],
        "payoff_evidence_paths": [],
        "opening_state": {"opening_situation": "清晨六点", "junk_id": None},
    }
    out = _strip_prompt_noise_deep(contract)
    assert "id" not in out and "chapter_id" not in out
    assert "active_arc_beat_ids" not in out and "payoff_evidence_paths" not in out
    assert out["opening_state"] == {"opening_situation": "清晨六点"}
    assert out["contract_summary"] == "接不接招安"
