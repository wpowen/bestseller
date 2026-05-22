from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.character_role_gate import (
    CHARACTER_ROLE_DRIFT_BLOCK_CODE,
    CharacterProfile,
    check_character_role_compliance,
    load_character_profiles,
    render_character_role_block,
    render_character_role_violations_block,
)

pytestmark = pytest.mark.unit


def _lin_yuan_profile() -> CharacterProfile:
    return CharacterProfile(
        name="林渊",
        abilities=(
            "阴阳眼", "罗盘", "青囊秘卷", "符法基础", "方位判断", "账页推理",
        ),
        inner_wound="不愿承认父亲可能为自己入镜",
        reader_promise="每次破局都更接近父亲真相，但也更接近自己欠下的债",
        forbidden_phrases=(
            "被鬼追着跑",
            "纯粹受害者",
        ),
        expected_tone_markers=("阴阳眼", "罗盘", "青囊", "符", "账"),
        conflicting_tone_markers=("破案", "审讯", "查案", "立案"),
    )


def test_empty_text_passes() -> None:
    report = check_character_role_compliance(
        "", chapter_position=1, profiles=[_lin_yuan_profile()]
    )
    assert report.passed
    assert report.findings == ()


def test_no_profiles_passes() -> None:
    report = check_character_role_compliance(
        "林渊用罗盘指向坤位", chapter_position=1, profiles=[]
    )
    assert report.passed


def test_character_not_on_page_skipped() -> None:
    # 林渊 mentioned only once, below threshold of 2
    text = "夜风吹过。林渊抬头看月亮。镜子里没有他的影子。"
    report = check_character_role_compliance(
        text, chapter_position=1, profiles=[_lin_yuan_profile()]
    )
    # threshold=2, 林渊 occurs 1 time → skipped
    assert report.passed


def test_forbidden_phrase_critical() -> None:
    text = (
        "林渊被鬼追着跑了整条街。\n"
        "林渊喘着气，手里没有罗盘也没有符。\n"
        "林渊知道自己已经成了纯粹受害者。\n"
    )
    report = check_character_role_compliance(
        text, chapter_position=1, profiles=[_lin_yuan_profile()]
    )
    assert not report.passed
    codes = {f.drift_type for f in report.findings}
    assert "forbidden_pattern" in codes


def test_abilities_used_passes() -> None:
    text = (
        "林渊踏进十七栋。\n"
        "他从青囊秘卷里抽出黄符，咬破指尖滴血。\n"
        "罗盘指向坤位偏西三十度。\n"
        "林渊用阴阳眼看清那张脸。\n"
    )
    report = check_character_role_compliance(
        text, chapter_position=1, profiles=[_lin_yuan_profile()]
    )
    # 多个能力使用，且没有 forbidden / 冲突词
    assert report.passed


def test_no_abilities_used_high_severity() -> None:
    text = (
        "林渊在十七栋走廊。\n"
        "林渊看了看手机。\n"
        "林渊说话了。\n"
        "林渊往前走。\n"
    )
    # 林渊 出场 4 次，无任何 ability 词
    report = check_character_role_compliance(
        text, chapter_position=1, profiles=[_lin_yuan_profile()]
    )
    # 不是 critical（被 forbidden 触发），但应有 high severity
    codes = {f.drift_type for f in report.findings}
    assert "ability_absent" in codes


def test_tone_mismatch_high_severity() -> None:
    text = (
        "林渊抵达旧事馆，开始查案。\n"
        "林渊审讯王老板，问了三个问题。\n"
        "林渊判断这是凶杀案，必须立案。\n"
        "林渊掌心紧。\n"
    )
    # 全章用侦探腔，无任何 阴阳眼/罗盘/符
    report = check_character_role_compliance(
        text, chapter_position=1, profiles=[_lin_yuan_profile()]
    )
    codes = {f.drift_type for f in report.findings}
    assert "tone_mismatch" in codes or "ability_absent" in codes


def test_proper_role_zero_findings() -> None:
    text = (
        "林渊踏进十七栋。\n"
        "他从青囊秘卷取出黄符，用罗盘核了方位。\n"
        "阴阳眼一开，看见七张脸。\n"
        "林渊低声念了一句镇魂咒。\n"
        "他不慌不忙——他不是来破案的，是来翻账的。\n"
    )
    # 多个 abilities + 即使有"破案"也被否定句包裹
    report = check_character_role_compliance(
        text, chapter_position=1, profiles=[_lin_yuan_profile()]
    )
    # 期望词存在 + 能力使用 → 即使"破案"出现也不算 tone_mismatch
    # because expected_hits > 0
    findings_tone = [
        f for f in report.findings if f.drift_type == "tone_mismatch"
    ]
    assert not findings_tone


def test_block_code_constant() -> None:
    assert CHARACTER_ROLE_DRIFT_BLOCK_CODE == "CHARACTER_ROLE_DRIFT"


def test_render_role_block_zh() -> None:
    block = render_character_role_block([_lin_yuan_profile()])
    assert "角色定位锁定" in block
    assert "林渊" in block
    assert "阴阳眼" in block or "罗盘" in block
    assert "被鬼追着跑" in block


def test_render_violations_block_zh() -> None:
    text = "林渊被鬼追着跑过整条街，林渊在街角喘气，林渊抬头看月亮。"
    report = check_character_role_compliance(
        text, chapter_position=1, profiles=[_lin_yuan_profile()]
    )
    block = render_character_role_violations_block(report)
    assert "角色定位门禁" in block


def test_render_violations_block_passed_empty() -> None:
    text = "夜色如墨，山风扑过。"
    report = check_character_role_compliance(
        text, chapter_position=1, profiles=[_lin_yuan_profile()]
    )
    assert render_character_role_violations_block(report) == ""


def test_load_character_profiles_real_file() -> None:
    path = Path(
        "output/exorcist-detective-1778051012/story-bible/cast-and-promises.md"
    )
    if not path.exists():
        pytest.skip("cast-and-promises.md not present")
    profiles = load_character_profiles(path)
    assert profiles
    names = {p.name for p in profiles}
    assert "林渊" in names
    lin_yuan = next(p for p in profiles if p.name == "林渊")
    assert lin_yuan.abilities
    assert "阴阳眼" in " ".join(lin_yuan.abilities)


def test_load_character_profiles_missing() -> None:
    profiles = load_character_profiles("/nonexistent/cast.md")
    assert profiles == ()
