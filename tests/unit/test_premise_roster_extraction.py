"""G2 (xianxia benchmark): premise natural-language roster extraction.

The shilouyan-bench premise names its key supporting cast in a natural
"关键配角：…孤女宋拾、…执事关铎、…监工裴萤、…少祭白杪" sentence. The names
are EMBEDDED at the tail of descriptive phrases (role + name), so the old
extractor — which only matched 2-3 char CJK runs after structural markers
like "人物名册" — found none of them, and the cast planner invented
replacements (P-4). Two fixes:

1. Recognise natural roster markers ("关键配角" / "主要人物" / ...).
2. Inside a marker segment, split on list separators and pull a 2-3 char
   name from the tail of each phrase (surname-anchored when possible).
"""

from __future__ import annotations

from bestseller.services.planner import _extract_premise_locked_names

_SHILOUYAN_TAIL = (
    "主角谢迟，浊脉少年，被三家道统黜落。"
    "关键配角：同乡幸存者孤女宋拾（后入雾外楼）、"
    "衡山院落魄执事关铎（知晓内鬼线索）、"
    "栖梧宗药圃监工裴萤（发现谢迟丹草异常的第一人）、"
    "巫祠遗脉少祭白杪。"
)


def test_extracts_embedded_names_after_natural_marker() -> None:
    names = set(_extract_premise_locked_names(_SHILOUYAN_TAIL))
    for expected in ("宋拾", "关铎", "裴萤", "白杪"):
        assert expected in names, f"{expected} missing from {names}"


def test_does_not_emit_role_prefixes() -> None:
    names = set(_extract_premise_locked_names(_SHILOUYAN_TAIL))
    # The role/aside words must not leak in as if they were names.
    for noise in ("孤女", "执事", "监工", "少祭", "后入", "知晓"):
        assert noise not in names, f"role/aside noise {noise} leaked into {names}"


def test_structural_marker_path_still_works() -> None:
    premise = "人物名册（正文必须沿用以下姓名）：周澈、九嶷、林晚。"
    names = set(_extract_premise_locked_names(premise))
    assert {"周澈", "九嶷", "林晚"} <= names


def test_plain_narration_without_marker_is_not_mined() -> None:
    """No roster marker → the tail-extraction pass must stay off so ordinary
    prose doesn't get its phrase tails harvested as names."""
    premise = (
        "他走进药铺，看见柜台后坐着一个打盹的老人，墙上挂着褪色的旧匾。"
        "外面下着雨，街道泥泞，远处传来打更的梆子声。"
    )
    names = _extract_premise_locked_names(premise)
    # The frequency fallback may legitimately find nothing here; the key
    # guarantee is that the tail pass did not fabricate names from prose.
    assert "梆子" not in names and "旧匾" not in names
