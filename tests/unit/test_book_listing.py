from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bestseller.services.book_listing import build_book_listing_profile

pytestmark = pytest.mark.unit


def test_build_book_listing_profile_uses_listing_files(tmp_path) -> None:
    listing_dir = tmp_path / "output" / "book-a" / "listing"
    listing_dir.mkdir(parents=True)
    (listing_dir / "book-listing-metadata.json").write_text(
        json.dumps(
            {
                "primary_title": "青囊不语问阴阳",
                "recommended_subtitle": "子时不入镜，否认者先入账",
                "logline": "落魄风水师接下凶宅委托，却被困进一面以否认为食的困魂镜。",
                "primary_category": "悬疑灵异",
                "secondary_category": "民俗悬疑",
                "tags": ["民俗悬疑", "风水师", "规则怪谈", "凶宅", "镜中世界"],
                "short_intro": (
                    "落魄风水师林渊接下十七栋凶宅委托，子时入镜，"
                    "卷入否认者先入账的血字公寓。他必须逼活人认账，"
                    "才能把失踪父亲的线索从镜中夺回来。"
                ),
                "promo_copy": [
                    "子时之后，别照镜子。",
                    "谁不认账，谁先入账。",
                    "他见鬼先看方位。",
                ],
                "main_characters": [{"name": "林渊", "role": "男主角", "identity": "风水师"}],
                "reader_promise": ["规则破局", "单元诡案推进主线"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    title_rows = ["id,title,subtitle,angle,recommendation"]
    title_rows.extend(
        f"{index},标题{index},副标题{index},角度{index},建议{index}" for index in range(1, 21)
    )
    (listing_dir / "title-candidates.csv").write_text(
        "\n".join(title_rows),
        encoding="utf-8",
    )
    project = SimpleNamespace(
        slug="book-a",
        title="旧标题",
        genre="悬疑",
        sub_genre="灵异",
        audience="男频",
        status="planning",
        language="zh-CN",
        metadata_json={},
    )

    profile = build_book_listing_profile(
        project=project,
        writing_profile={},
        story_bible=None,
        output_base_dir=tmp_path / "output",
    )

    assert profile["primary_title"] == "青囊不语问阴阳"
    assert profile["primary_category"] == "悬疑灵异"
    assert len(profile["title_candidates"]) == 20
    assert profile["title_candidates"][0]["title"] == "标题1"
    assert profile["main_characters"][0]["name"] == "林渊"
    assert profile["compliance"]["status"] == "ready"


def test_build_book_listing_profile_generates_required_fallbacks() -> None:
    project = SimpleNamespace(
        slug="book-b",
        title="长夜巡航",
        genre="科幻",
        sub_genre="星际悬疑",
        audience="全站",
        status="planning",
        language="zh-CN",
        metadata_json={"premise": "边境飞船收到一份来自未来的死亡名单。"},
    )
    writing_profile = {
        "market": {
            "platform_target": "番茄小说",
            "reader_promise": "高密度危机、反转和阶段性破局。",
            "selling_points": ["死亡名单", "星际追凶"],
            "trope_keywords": ["时间悖论", "密室飞船"],
            "hook_keywords": ["未来回执"],
        },
        "character": {
            "protagonist_archetype": "冷静的事故调查员",
            "protagonist_core_drive": "查出死亡名单来源",
            "golden_finger": "能读出事故现场残留时间噪声",
        },
        "world": {"setting_tags": ["边境航道"]},
    }

    profile = build_book_listing_profile(
        project=project,
        writing_profile=writing_profile,
        story_bible=None,
    )

    assert profile["primary_title"] == "长夜巡航"
    assert profile["secondary_category"] == "星际悬疑"
    assert len(profile["title_candidates"]) == 20
    assert len(profile["tags"]) >= 5
    assert profile["main_characters"][0]["role"] == "主角"
    assert profile["compliance"]["blocker_count"] == 0
