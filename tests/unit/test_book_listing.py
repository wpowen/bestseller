from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bestseller.services.book_listing import build_book_listing_profile
from bestseller.services.platform_title_workflow import build_platform_title_workflow

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
    assert len(profile["title_candidates"]) == 40
    assert profile["title_workflow"]["candidate_source"] == "platform_title_workflow"
    assert profile["legacy_title_candidates"][0]["title"] == "标题1"
    assert profile["title_candidates"][0]["title"] != "标题1"
    assert all(item.get("display_label") for item in profile["title_candidates"])
    assert all(group["candidate_count"] == 5 for group in profile["title_workflow"]["platform_groups"])
    assert profile["main_characters"][0]["name"] == "林渊"
    script_durations = [
        item["duration_seconds"]
        for item in profile["marketing_assets"]["short_video_scripts"]
    ]
    assert script_durations == [15, 45, 90]
    assert profile["ip_readiness"]["status"] in {"ready", "needs_attention"}
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
    assert len(profile["title_candidates"]) == 40
    assert any(item.get("scope_label") == "全平台" for item in profile["title_candidates"])
    assert len(profile["title_workflow"]["platform_groups"]) == 8
    assert len(profile["tags"]) >= 5
    assert profile["main_characters"][0]["role"] == "主角"
    assert profile["marketing_assets"]["short_video_scripts"][0]["duration_seconds"] == 15
    assert "ip_readiness" in profile
    assert profile["compliance"]["blocker_count"] == 0


def test_fallback_title_candidates_follow_fanqie_workflow() -> None:
    project = SimpleNamespace(
        slug="book-c",
        title="废土回执",
        genre="科幻",
        sub_genre="科幻末世",
        audience="男频",
        status="planning",
        language="zh-CN",
        metadata_json={"premise": "主角被传送进废土, 只能带着样本请求国家支援。"},
    )
    writing_profile = {
        "market": {
            "platform_target": "番茄小说",
            "reader_promise": "废土开局、上交国家、基地升级。",
            "selling_points": ["上交国家", "废土种田"],
            "trope_keywords": ["双穿门", "废土"],
            "hook_keywords": ["上交国家"],
        },
        "character": {
            "protagonist_archetype": "被迫穿越的普通人",
            "protagonist_core_drive": "把废土样本带回现实",
            "golden_finger": "双穿门",
        },
        "world": {"setting_tags": ["废土", "基地建设"]},
    }

    profile = build_book_listing_profile(
        project=project,
        writing_profile=writing_profile,
        story_bible=None,
    )

    titles = [item["title"] for item in profile["title_candidates"]]
    assert profile["title_workflow"]["platform_key"] == "fanqie"
    assert profile["title_workflow"]["candidate_source"] == "platform_title_workflow"
    assert profile["title_workflow"]["candidate_policy"] == "platform_matrix_5_each"
    fanqie_group = next(
        group
        for group in profile["title_workflow"]["platform_groups"]
        if group["platform_key"] == "fanqie"
    )
    assert fanqie_group["candidate_count"] == 5
    assert any(title.startswith(("开局", "我在", "让你", "全民")) for title in titles)
    assert any(item.get("platform_label") == "番茄小说" for item in profile["title_candidates"])
    assert any(item.get("scope_label") == "全平台" for item in profile["title_candidates"])
    assert not any("候选" in title for title in titles)


def test_fanqie_short_title_workflow_uses_high_conflict_story_titles() -> None:
    workflow = build_platform_title_workflow(
        {
            "primary_title": "情绪爆改器",
            "target_platform": "番茄短故事",
            "length_type": "番茄短故事 · 单篇完结",
            "primary_category": "都市异能",
            "secondary_category": "番茄短故事",
            "tags": ["职场背锅", "全员群", "贪污犯", "情绪爆改器", "记忆代价"],
            "logline": (
                "全员群把陆渊挂成贪污犯后，他点开情绪爆改器，"
                "让老板在发布会当众自爆。"
            ),
            "main_characters": [
                {"name": "陆渊", "identity": "被公司公开栽赃的前员工"}
            ],
            "reader_promise": [
                "开篇50字金手指生效，前200字完成第一次公开打脸。",
                "每次打脸都要付出记忆代价。",
            ],
        },
        target_platform="番茄短故事",
    )

    fanqie_group = next(
        group
        for group in workflow["platform_groups"]
        if group["platform_key"] == "fanqie"
    )
    fanqie_titles = [item["title"] for item in fanqie_group["candidates"]]

    assert fanqie_titles[0] == "全员群把我挂成贪污犯后，我让老板当众自爆"
    assert any("情绪爆改器" in title for title in fanqie_titles)
    assert any("记忆代价" in title for title in fanqie_titles)
    assert all(len(title) <= 30 for title in fanqie_titles)


def test_fallback_title_candidates_follow_jinjiang_workflow() -> None:
    project = SimpleNamespace(
        slug="book-d",
        title="玫瑰岛来信",
        genre="言情",
        sub_genre="幻想现言",
        audience="女频",
        status="planning",
        language="zh-CN",
        metadata_json={"premise": "女主收到亡夫旧友的来信, 被迫回到玫瑰岛重查旧案。"},
    )
    writing_profile = {
        "market": {
            "platform_target": "晋江文学城",
            "reader_promise": "关系反转、旧案重查、暗恋拉扯。",
            "selling_points": ["暗恋", "破镜重圆"],
            "trope_keywords": ["破镜重圆", "悬疑恋爱"],
            "hook_keywords": ["亡夫旧友"],
        },
        "character": {
            "protagonist_archetype": "回岛调查的遗孀",
            "protagonist_core_drive": "查明旧案真相",
        },
        "world": {"setting_tags": ["海岛", "玫瑰岛"]},
    }

    profile = build_book_listing_profile(
        project=project,
        writing_profile=writing_profile,
        story_bible=None,
    )

    assert profile["title_workflow"]["platform_key"] == "jinjiang"
    jinjiang_group = next(
        group
        for group in profile["title_workflow"]["platform_groups"]
        if group["platform_key"] == "jinjiang"
    )
    jinjiang_titles = [item["title"] for item in jinjiang_group["candidates"]]
    assert jinjiang_group["candidate_count"] == 5
    assert any("协议" in title or "倒计时" in title or "[" in title for title in jinjiang_titles)
    assert any(item.get("platform_label") == "晋江文学城" for item in profile["title_candidates"])
    assert not any(title.startswith("开局") for title in jinjiang_titles)


def test_platform_title_workflow_uses_clickable_identity_and_entry_tokens() -> None:
    workflow = build_platform_title_workflow(
        {
            "primary_title": "青崖诡事",
            "genre": "惊悚灵异",
            "tags": [
                "民国悬疑",
                "南茅北马",
                "灵异惊悚",
                "风水驱魔",
                "单元破案",
                "阴阳眼",
                "驱魔探案综合",
            ],
            "logline": (
                "民国年间, 津门租界巡捕房副捕头沈青崖天生阴阳重瞳."
                "十五年前沈家灭门惨案, 他是唯一幸存者; 幕后黑手正是归墟会."
            ),
        },
        target_platform="番茄小说",
    )

    titles = [item["title"] for item in workflow["candidates"]]
    assert len(titles) == 40
    assert any("巡捕房副捕头" in title for title in titles)
    assert any("灭门旧案" in title for title in titles)
    assert any("归墟会" in title for title in titles)
    assert not any("驱魔探案综合" in title for title in titles)


def test_platform_title_workflow_uses_project_public_emotion_bridge() -> None:
    workflow = build_platform_title_workflow(
        {
            "primary_title": "镜城夜行",
            "genre": "悬疑",
            "tags": ["悬疑", "规则破局"],
            "public_emotion_kernel": {
                "target_segments": [
                    {
                        "id": "segment-a",
                        "group_label": "被误判的人",
                        "life_context": "虚构城中旧案。",
                        "public_emotion": "不甘。",
                        "unsaid_sentence": "凭什么一句话定案？",
                        "desired_compensation": "翻案。",
                    }
                ],
                "emotion_bridges": [
                    {
                        "bridge_id": "bridge-a",
                        "bridge_type": "value_bridge",
                        "public_anchor": "被误判",
                        "genre_translation": "虚构案卷规则。",
                        "story_hook": "主角用新证据翻案。",
                        "reader_payoff": "旧判断被推翻。",
                        "title_hook": "旧榜错判我，我用新规则翻案",
                    }
                ],
            },
            "compliance_boundary_kernel": {
                "policy_pack_key": "cn-mainland-general",
                "allowed_translations": ["转译为虚构规则"],
                "forbidden_translations": ["不得指向真实群体"],
            },
        },
        target_platform="番茄小说",
    )

    first_group = workflow["platform_groups"][0]["candidates"]
    assert first_group[0]["title"] == "旧榜错判我，我用新规则翻案"
    assert first_group[0]["emotion_bridge_id"] == "bridge-a"
    assert first_group[0]["risk_flags"] == []
    assert "旧榜错判我，我用新规则翻案" in [
        item["title"] for item in workflow["candidates"]
    ]


def test_platform_title_workflow_filters_high_risk_public_emotion_title() -> None:
    workflow = build_platform_title_workflow(
        {
            "primary_title": "镜城夜行",
            "genre": "悬疑",
            "tags": ["悬疑", "规则破局"],
            "public_emotion_kernel": {
                "target_segments": [
                    {
                        "id": "segment-a",
                        "group_label": "被误判的人",
                        "life_context": "虚构城中旧案。",
                        "public_emotion": "不甘。",
                        "unsaid_sentence": "凭什么一句话定案？",
                        "desired_compensation": "翻案。",
                    }
                ],
                "emotion_bridges": [
                    {
                        "bridge_id": "bridge-risk",
                        "bridge_type": "value_bridge",
                        "public_anchor": "现实报复",
                        "genre_translation": "不安全转译。",
                        "story_hook": "报复现实。",
                        "reader_payoff": "不安全。",
                        "title_hook": "被真实学校欺负后，我要报复现实所有人",
                    }
                ],
            },
            "compliance_boundary_kernel": {
                "policy_pack_key": "cn-mainland-general",
                "allowed_translations": ["转译为虚构规则"],
                "forbidden_translations": ["不得指向真实群体"],
            },
        },
        target_platform="番茄小说",
    )

    titles = [item["title"] for item in workflow["candidates"]]
    assert "被真实学校欺负后，我要报复现实所有人" not in titles
    assert all(item.get("risk_blocked") is not True for item in workflow["candidates"])
    assert len(workflow["candidates"]) == 40
    assert all(group["candidate_count"] == 5 for group in workflow["platform_groups"])


def test_public_emotion_title_does_not_leak_between_projects() -> None:
    first = build_platform_title_workflow(
        {
            "primary_title": "甲书",
            "genre": "悬疑",
            "tags": ["悬疑"],
            "public_emotion_kernel": {
                "target_segments": [
                    {
                        "id": "segment-a",
                        "group_label": "甲书读者",
                        "life_context": "甲书处境。",
                        "public_emotion": "甲书情绪。",
                        "unsaid_sentence": "甲书句子。",
                        "desired_compensation": "甲书补偿。",
                    }
                ],
                "emotion_bridges": [
                    {
                        "bridge_id": "bridge-a",
                        "bridge_type": "value_bridge",
                        "public_anchor": "甲书锚点",
                        "genre_translation": "甲书规则",
                        "story_hook": "甲书故事",
                        "reader_payoff": "甲书补偿",
                        "title_hook": "甲书专属翻案",
                    }
                ],
            },
        },
        target_platform="番茄小说",
    )
    second = build_platform_title_workflow(
        {
            "primary_title": "乙书",
            "genre": "都市",
            "tags": ["都市"],
        },
        target_platform="番茄小说",
    )

    assert "甲书专属翻案" in [item["title"] for item in first["candidates"]]
    assert "甲书专属翻案" not in [item["title"] for item in second["candidates"]]
