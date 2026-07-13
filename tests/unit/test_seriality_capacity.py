# ruff: noqa: RUF001
from __future__ import annotations

from bestseller.services.seriality_capacity import evaluate_seriality_capacity


def test_one_shot_premise_cannot_claim_two_hundred_chapters() -> None:
    report = evaluate_seriality_capacity(
        {
            "repeatable_story_unit": "找到等待主角七十年的黑棺",
            "renewal_sources": [],
            "accumulation_tracks": ["距离死亡越来越近"],
            "phase_transitions": ["找到黑棺并揭开身世"],
            "opposing_ecology": ["幕后送葬人"],
            "mystery_ladder": ["黑棺为何等他"],
            "endgame_direction": "决定是否躺进黑棺",
        },
        target_chapters=200,
    )

    assert not report.passed
    assert report.estimated_chapter_ceiling <= 100
    assert "renewal_source_missing" in report.blocking_codes
    assert "phase_transitions_thin" in report.blocking_codes


def test_renewable_engine_can_support_five_hundred_chapters() -> None:
    report = evaluate_seriality_capacity(
        {
            "repeatable_story_unit": "主角接下一桩亡者未来被盗的收殓委托，追查并处置余生归属",
            "renewal_sources": [
                "每天都有新的异常死亡进入殡仪馆",
                "不同势力持续购买、转卖和掠夺余生",
                "主角每次处置都会改变城市的未来资源分配",
            ],
            "accumulation_tracks": [
                "主角可支配的余生权限",
                "明日会暴露的组织层级",
                "主角被消耗的自身未来",
            ],
            "phase_transitions": [
                "替单个死者完成遗愿",
                "介入城市余生黑市",
                "争夺跨城命运基础设施",
                "改写未来资源的分配制度",
            ],
            "opposing_ecology": [
                "购买余生的富豪与掮客",
                "维护既得利益的明日会",
                "认为主角破坏秩序的监管者",
            ],
            "mystery_ladder": [
                "死者为何提前死亡",
                "谁在回收不该消失的未来",
                "主角母亲的余生去了哪里",
                "谁拥有决定全城明天的权限",
            ],
            "endgame_direction": "主角必须决定未来能否继续成为可交易资源",
        },
        target_chapters=500,
    )

    assert report.passed
    assert report.estimated_chapter_ceiling >= 500
    assert report.capacity_tier == "epic"


def test_thousand_chapter_target_requires_more_than_a_five_hundred_chapter_engine() -> None:
    report = evaluate_seriality_capacity(
        {
            "repeatable_story_unit": "主角处理一桩新的余生案件",
            "renewal_sources": ["异常死亡持续出现", "黑市持续制造委托"],
            "accumulation_tracks": ["能力权限", "组织线索"],
            "phase_transitions": ["单案", "城市黑市", "跨城组织", "终局制度"],
            "opposing_ecology": ["掮客", "明日会"],
            "mystery_ladder": ["个案真相", "母亲真相", "组织真相"],
            "endgame_direction": "摧毁余生交易制度",
        },
        target_chapters=1000,
    )

    assert not report.passed
    assert report.estimated_chapter_ceiling == 500
    assert "millennial_capacity_unproven" in report.blocking_codes


def test_phase_count_cannot_fake_five_hundred_chapter_coverage() -> None:
    report = evaluate_seriality_capacity(
        {
            "repeatable_story_unit": "每轮验证一份新旧契并阻断一次拆迁动作",
            "renewal_sources": ["相邻街巷持续滚入新旧契", "新资方持续进入"],
            "accumulation_tracks": ["已立案旧契", "不可撤回异议登记"],
            "phase_transitions": [
                "第1-12章守现场",
                "第13-30章连旧账",
                "第31-45章内外夹击",
                "第46-50章债变资产",
            ],
            "opposing_ecology": ["宗族理事会", "开发商联合体"],
            "mystery_ladder": ["师傅为何藏账", "母亲为何反对", "拆迁红线为何精准"],
            "endgame_direction": "完成整座旧城的旧债清算",
        },
        target_chapters=500,
        require_phase_coverage=True,
    )

    assert not report.passed
    assert report.estimated_chapter_ceiling == 50
    assert "phase_coverage_incomplete" in report.blocking_codes
    assert report.metrics["phase_coverage_max"] == 50


def test_slow_recurring_event_cannot_fake_story_unit_density() -> None:
    report = evaluate_seriality_capacity(
        {
            "repeatable_story_unit": "每年献祭夜公开替换一个祭祀步骤",
            "unit_families": ["发现", "交易", "关系", "公开博弈"],
            "unit_frequency": "每年一次",
            "unit_count_estimate": 4,
            "renewal_sources": ["新家庭被抽中", "县里档案持续露出旧账"],
            "accumulation_tracks": ["已存档旧签", "公开录像"],
            "phase_transitions": [
                "第1-120章拆穿第一轮抽签",
                "第121-260章追查宗族账",
                "第261-380章争夺祭祀话语权",
                "第381-500章公开清算",
            ],
            "opposing_ecology": ["宗族理事会", "旅游资本"],
            "mystery_ladder": ["河神是否存在", "谁靠祭祀获利", "河下埋了什么"],
            "endgame_direction": "终止献祭制度",
        },
        target_chapters=500,
        require_phase_coverage=True,
    )

    assert not report.passed
    assert "unit_cadence_sparse" in report.blocking_codes
    assert report.metrics["unit_count_is_claim_only"] is True


def test_exact_chapter_divided_by_ten_has_no_epic_safety_margin() -> None:
    report = evaluate_seriality_capacity(
        {
            "repeatable_story_unit": "每8至12章把一条失败技术路线做成产品",
            "unit_families": ["研发", "交易", "团队", "供应链"],
            "unit_frequency": "每8至12章一次",
            "unit_count_estimate": 50,
            "renewal_sources": [
                "持续收购外部失败项目",
                "竞争对手持续封杀产品",
                "客户反馈暴露新的技术问题",
            ],
            "accumulation_tracks": ["产品矩阵", "团队能力", "产业话语权"],
            "phase_transitions": [
                "第1-100章验证产品",
                "第101-220章争夺供应链",
                "第221-360章争夺行业标准",
                "第361-500章改写产业规则",
            ],
            "opposing_ecology": ["垄断基金", "芯片巨头", "摇摆客户"],
            "mystery_ladder": ["谁封杀主角", "谁删除路线", "谁垄断未来"],
            "endgame_direction": "让被删除的技术路线重新成为公共选择",
        },
        target_chapters=500,
        require_phase_coverage=True,
    )

    assert not report.passed
    assert "unit_cadence_sparse" in report.blocking_codes
    assert report.metrics["unit_count_is_claim_only"] is True


def test_zero_declared_units_cannot_pass_strict_five_hundred_gate() -> None:
    report = evaluate_seriality_capacity(
        {
            "repeatable_story_unit": "处理会持续改变关系和资源归属的案件",
            "unit_families": ["调查", "交易", "关系", "公开冲突"],
            "unit_frequency": "每2-4章一次",
            "unit_count_estimate": 0,
            "renewal_sources": ["新冲突", "对手反制", "旧后果回流"],
            "accumulation_tracks": ["权限", "关系", "地盘"],
            "phase_transitions": [
                "第1-100章立足",
                "第101-220章扩城",
                "第221-360章跨域",
                "第361-500章制度争夺",
            ],
            "opposing_ecology": ["旧势力", "新资本", "监管者"],
            "mystery_ladder": ["个案", "组织", "制度"],
            "endgame_direction": "决定制度归属",
        },
        target_chapters=500,
        require_phase_coverage=True,
    )

    assert not report.passed
    assert "unit_count_below_target" in report.blocking_codes


def test_structurally_deep_engine_can_prove_two_thousand_chapters() -> None:
    report = evaluate_seriality_capacity(
        {
            "repeatable_story_unit": "主角改变一个区域的通行秩序并承受各方反制",
            "unit_families": ["探索", "建设", "谈判", "交易", "征战", "治理"],
            "unit_frequency": "每2-4章一次",
            "unit_count_estimate": 700,
            "renewal_sources": ["新地域", "新族群", "新技术", "旧盟友分裂", "制度反制"],
            "accumulation_tracks": ["地盘", "组织", "技术", "关系", "合法性"],
            "phase_transitions": [
                "第1-200章边地立足",
                "第201-400章区域建设",
                "第401-600章多城联盟",
                "第601-800章跨国贸易",
                "第801-1000章制度冲突",
                "第1001-1200章世界扩张",
                "第1201-1400章联盟分裂",
                "第1401-1700章秩序重组",
                "第1701-2000章终局治理",
            ],
            "opposing_ecology": ["旧贵族", "商会", "宗教", "边军", "新联盟"],
            "mystery_ladder": [
                "边地为何封锁",
                "旧路为何消失",
                "谁控制贸易",
                "联盟为何成立",
                "技术从何而来",
                "旧王朝为何崩塌",
                "新秩序由谁承认",
                "世界边界如何形成",
                "最终制度归谁",
            ],
            "endgame_direction": "建立能容纳多族群的长期秩序",
        },
        target_chapters=2000,
        require_phase_coverage=True,
    )

    assert report.passed
    assert report.estimated_chapter_ceiling == 2000
    assert report.capacity_tier == "ultra_2000"
