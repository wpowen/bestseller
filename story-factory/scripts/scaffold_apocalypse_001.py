#!/usr/bin/env python3
"""
灰烬执政官 (apocalypse_001) — 60 章节末日爽文故事包脚手架

用法：
    python3 story-factory/scripts/scaffold_apocalypse_001.py

输出：
    - story-factory/concepts/apocalypse_001.json
    - story-factory/projects/apocalypse_001/story_package.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from compile_story_package import validate_package


BOOK_ID = "apocalypse_001"
TITLE = "灰烬执政官"
AUTHOR = "命书工作室"


def chapter_plan(
    title: str,
    objective: str,
    conflict: str,
    hook: str,
    emotion: str,
    feature_character_id: str,
) -> dict[str, str]:
    return {
        "title": title,
        "objective": objective,
        "conflict": conflict,
        "hook": hook,
        "emotion": emotion,
        "feature_character_id": feature_character_id,
    }


BOOK_META = {
    "id": BOOK_ID,
    "title": TITLE,
    "author": AUTHOR,
    "cover_image_name": f"cover_{BOOK_ID}",
    "synopsis": (
        "黑雨降临后的第七小时，整座临港城彻底失控。你从一间被弃用的仓储调度室里醒来，"
        "手腕上多了一枚只对你开放的“灰楼权限环”。它能打开封存补给、接管灾备设施，"
        "也让你看见一个更残酷的真相：所谓救援从一开始就被人拿来筛选该活的人。"
        "你要做的，不只是活下来，而是把安全区的规则从别人手里夺过来。"
    ),
    "genre": "末日爽文",
    "tags": ["黑雨末日", "安全区夺权", "资源争霸", "尸潮围城", "公开清算"],
    "interaction_tags": ["高互动", "势力经营", "关系推进", "攻略图"],
    "total_chapters": 60,
    "free_chapters": 20,
    "characters": [
        {
            "id": "char_lin_shuang",
            "name": "林霜",
            "title": "野战医生",
            "avatar_image_name": "avatar_lin_shuang",
            "description": "灾变前是急诊主治，黑雨夜后独自撑起临时医务点。说话冷，手很稳，见过太多人死，所以只相信能扛事的人。",
            "role": "红颜",
        },
        {
            "id": "char_han_ce",
            "name": "韩策",
            "title": "外环武装队长",
            "avatar_image_name": "avatar_han_ce",
            "description": "前城防机动队骨干，灾后迅速拉起私人武装。信奉强者统治，把秩序当生意做，也把你视为最危险的竞争者。",
            "role": "宿敌",
        },
        {
            "id": "char_tang_hai",
            "name": "唐海",
            "title": "灾备工程师",
            "avatar_image_name": "avatar_tang_hai",
            "description": "灰楼老工程师，懂配电、懂水路，也懂这座城每一处灾备暗门。嘴硬心软，最看不得好东西落进蠢人手里。",
            "role": "盟友",
        },
        {
            "id": "char_shen_chong",
            "name": "沈崇",
            "title": "C7 安全区总管",
            "avatar_image_name": "avatar_shen_chong",
            "description": "对外自称救援负责人，实则把配给、床位和出城名单全部变成自己的筹码。笑得越温和，刀子就藏得越深。",
            "role": "反派",
        },
        {
            "id": "char_luo_yue",
            "name": "洛月",
            "title": "信号分析员",
            "avatar_image_name": "avatar_luo_yue",
            "description": "曾在方舟项目外围部门工作，对黑雨前后的异常信号极其敏感。她知道真相的一部分，但更习惯先观察谁值得下注。",
            "role": "中立",
        },
        {
            "id": "char_he_mu",
            "name": "何牧",
            "title": "前应急总教官",
            "avatar_image_name": "avatar_he_mu",
            "description": "你父亲的旧友，也是老一代灾备体系的训练官。沉默寡言，极少出手，但每次开口都像在给人上最后一堂课。",
            "role": "师尊",
        },
        {
            "id": "char_qin_nian",
            "name": "秦念",
            "title": "失踪家人",
            "avatar_image_name": "avatar_qin_nian",
            "description": "你失联的妹妹。黑雨夜后，她的定位从医院名单里消失，只留下一个被人为改写过的转运编号。",
            "role": "家族",
        },
    ],
    "initial_stats": {
        "combat": 9,
        "fame": 6,
        "strategy": 14,
        "wealth": 7,
        "charm": 8,
        "darkness": 0,
        "destiny": 24,
    },
}

CHARACTER_NAME_BY_ID = {character["id"]: character["name"] for character in BOOK_META["characters"]}

READER_DESIRE_MAP = {
    "core_fantasy": "从被尸潮和权力体系同时碾压的底层幸存者，变成重新定义安全区规则的人。",
    "reward_promises": [
        "每次夺资源都不是抽象结算，而是有过程、有压迫、有公开立威",
        "你会一步步从小据点掌控者升级成整座安全区的规则制定者",
        "所有早期受过的气，后面都会在更大的场面里连本带利清算回来",
    ],
    "control_promises": [
        "决定先用铁腕压场，还是先用秩序收编人心",
        "决定把有限资源投给战力、医疗、情报还是关键人物",
        "决定哪些人能成为自己的人，哪些人必须被清理出局",
    ],
    "suspense_questions": [
        "黑雨到底是灾难失控，还是被人提前设计好的筛选计划",
        "父亲留下的灰楼权限，为什么只在你手里能完整启动",
        "妹妹秦念究竟是幸存者，还是被带进了更深的实验核心",
    ],
}

STORY_BIBLE = {
    "premise": "黑雨毁城后，你继承了父亲留下的灰楼权限，从一处小型据点起家，逐步吞并资源、收编人心、掀翻安全区伪秩序，最终夺回整座城市的生存规则。",
    "mainline_goal": "找到秦念、公开黑雨与方舟项目的真相，并在尸潮与人祸夹击中建立一套真正由你掌控的新秩序。",
    "side_threads": [
        "林霜从只想救眼前人，到主动站上秩序前线",
        "唐海把灰楼改造成真正的城市级据点，并逼你面对掌权的代价",
        "洛月在观望、背靠和下注之间反复摇摆，最后决定把钥匙交给谁",
        "韩策从竞争者一步步走向彻底失控，成为你必须公开处决的旧秩序代表",
    ],
    "hidden_truths": [
        "黑雨并非单纯事故，而是方舟项目启动前的人口筛选程序",
        "父亲提前篡改了灰楼权限，把城市灾备主控权藏进你的身份环里",
        "沈崇和韩策从未真正代表救援，他们只是替更高层筛选可利用幸存者的执行者",
        "秦念被转运进方舟内环，不是因为被选中，而是因为她掌握了父亲留下的第二把钥匙",
    ],
}

ROUTE_GRAPH = {
    "mainline": "黑雨爆发 -> 抢下灰楼 -> 渗透 C7 -> 尸潮围城 -> 公开清算 -> 方舟夺权",
    "side_routes": ["林霜信任线", "唐海基建线", "洛月情报线", "韩策对立线"],
    "hidden_routes": ["父亲日志线", "秦念转运线", "方舟净化计划线"],
    "milestones": [
        {"id": "m01", "title": "灰楼开门", "chapter_range": "1-10"},
        {"id": "m02", "title": "灰楼成军", "chapter_range": "11-20"},
        {"id": "m03", "title": "C7 留印", "chapter_range": "21-30"},
        {"id": "m04", "title": "围城立威", "chapter_range": "31-40"},
        {"id": "m05", "title": "C7 易主", "chapter_range": "41-50"},
        {"id": "m06", "title": "新法点亮", "chapter_range": "51-60"},
    ],
}

STAGES = [
    {
        "id": "arc_01",
        "title": "黑雨之夜",
        "summary": "抢下第一处补给点",
        "stage_goal": "在城区失控后的第一天站稳脚跟，并找到灰楼权限的第一把钥匙。",
        "setting": "塌陷商超和雨幕街区",
        "pressure": "黑雨、饥饿和第一波尸变把人群挤成一团，任何犹豫都会让生路少一条。",
        "hidden_route_hint": "父亲留下的权限痕迹正藏在最不起眼的灾备设备里。",
        "visible_routes": [
            {"title": "抢先夺路", "style": "直接爽", "unlock_hint": "偏战力", "payoff": "快速立威", "process_focus": "正面开路"},
            {"title": "暗藏后手", "style": "延迟爽", "unlock_hint": "偏谋略", "payoff": "换更大的后续空间", "process_focus": "资源换位"},
            {"title": "先救关键人", "style": "情感爽", "unlock_hint": "偏魅力", "payoff": "更快收拢人心", "process_focus": "冒险救人"},
        ],
        "chapters": [
            chapter_plan("黑雨落城", "冲出崩溃地铁口并进入封锁商超", "踩踏与第一波尸变同时爆发", "卷帘门后，有人提前守住了仓储通道。", "紧张", "char_lin_shuang"),
            chapter_plan("卷帘门后", "夺下商超后仓的落脚点", "幸存者内斗比尸群更快撕开口子", "你在断货架后，找到一张被雨水泡开的灾备图。", "压迫", "char_tang_hai"),
            chapter_plan("第一把铁钥", "拿到通往城市灰楼的备用钥匙", "钥匙在暴徒头目手里，对方正拿它换人命", "钥匙背面刻着父亲常用的编号。", "狠厉", "char_han_ce"),
            chapter_plan("冷库试刀", "用第一场硬仗把队伍压住", "冷库里的幸存者想趁乱反客为主", "林霜在医务箱里翻出一支只写了“G-3”的注射针。", "决绝", "char_lin_shuang"),
            chapter_plan("断电前夜", "在全区停电前确定撤离路线", "备用发电机只能保一条通道", "通电的监控画面里，灰楼顶灯短暂亮了一次。", "警惕", "char_tang_hai"),
            chapter_plan("楼顶信号", "在雨幕里接到第一段异常广播", "屋顶天线会引来尸群，也可能引来援兵", "杂音尽头，有人念出了秦念的转运编号。", "震惊", "char_qin_nian"),
            chapter_plan("雨幕搜仓", "补齐撤往灰楼前的核心物资", "药品、枪械和净水片只能保一边", "唐海在积水里摸到一枚旧时代的权限卡。", "紧绷", "char_tang_hai"),
            chapter_plan("第一份名单", "决定谁有资格跟你一起离开", "名单一旦公布，落选者就会先动手", "名单末尾，多出一个本不该出现的名字：韩策。", "冷静", "char_han_ce"),
            chapter_plan("血路回车", "把车队从尸潮夹缝里开出去", "前路被货柜堵死，后路被活尸追上", "你看见远处高楼亮起代表灾备站的蓝灯。", "燃起", "char_lin_shuang"),
            chapter_plan("灰楼开门", "首次启动灰楼外围权限", "灰楼门禁认人，不认求救声", "门开的一瞬间，主控屏弹出一条只留给你的遗言。", "震动", "char_he_mu"),
        ],
    },
    {
        "id": "arc_02",
        "title": "灰楼立旗",
        "summary": "把据点从避难所改成自己的地盘",
        "stage_goal": "接管灰楼基础设施，形成稳定班底，并让外面的人知道这里换了新规则。",
        "setting": "灰楼内部和周边封锁带",
        "pressure": "灰楼能救人，也能吞掉资源。人一多，缺口和野心会一起放大。",
        "hidden_route_hint": "旧灾备系统里埋着父亲删不干净的权限日志。",
        "visible_routes": [
            {"title": "硬吃来敌", "style": "碾压爽", "unlock_hint": "偏战力或名望", "payoff": "迅速压住外环掠夺者", "process_focus": "火力压制"},
            {"title": "秩序收编", "style": "延迟爽", "unlock_hint": "偏名望或谋略", "payoff": "低成本扩充人手", "process_focus": "规则整编"},
            {"title": "保住核心人才", "style": "情感爽", "unlock_hint": "偏魅力", "payoff": "提前锁住关键关系", "process_focus": "救人与承诺"},
        ],
        "chapters": [
            chapter_plan("电梯井口", "清出灰楼通往地下配电层的路", "井道里卡着尸体和活人留下的陷阱", "最底层墙面上，有父亲手写的应急密码。", "压迫", "char_tang_hai"),
            chapter_plan("医务室里", "建立第一个稳定救治点", "药量只够保重伤员或保持队伍行动力", "林霜把最后一箱抗凝剂锁进了只给你看的柜子。", "克制", "char_lin_shuang"),
            chapter_plan("老唐的图纸", "把灰楼外围改成可守的防线", "你得决定是加固大门还是先修净水", "唐海翻出一张只有老灾备工程师才见过的总图。", "期待", "char_tang_hai"),
            chapter_plan("立旗之夜", "公开宣布灰楼的第一条规矩", "新规矩一旦立不住，人心会立刻散掉", "你的广播才结束，楼外就响起韩策的车笛。", "燃起", "char_han_ce"),
            chapter_plan("广播试呼", "用城市频道试探外部势力", "回应你的人越多，暴露的位置就越快", "一个女声没有报姓名，只说她知道黑雨前的信号异常。", "疑云", "char_luo_yue"),
            chapter_plan("暗仓账本", "查清灰楼旧仓到底被谁搬空了", "账本牵扯到灾前高层，查得越深越危险", "账本最后一页，出现了沈崇的签字。", "阴沉", "char_shen_chong"),
            chapter_plan("猎队来袭", "扛住第一次成规模试探", "对方不攻楼，只不断切断你外面的手脚", "韩策留下话：下一次来，不会只是试探。", "凶狠", "char_han_ce"),
            chapter_plan("门外谈判", "决定要不要和外环势力做第一笔交易", "一旦开口谈，灰楼就不再只是避难所", "谈判桌上，对方摆出一张 C7 的入区配额表。", "试探", "char_luo_yue"),
            chapter_plan("首次清剿", "清掉灰楼周边会反复聚群的感染源", "出楼风险极高，但不清剿就会被耗死", "尸群后方，你看见了带着 C7 编号的诱导器。", "狂烈", "char_he_mu"),
            chapter_plan("灰楼成军", "把灰楼队伍正式编成战斗与后勤体系", "队伍成军意味着你要开始分配权、罚和生死", "主控台弹出的第二条权限提示，指向 C7 安全区。", "肃杀", "char_he_mu"),
        ],
    },
    {
        "id": "arc_03",
        "title": "安全区暗战",
        "summary": "把手伸进 C7 的配给与名单体系",
        "stage_goal": "摸清 C7 的权力结构，在不被吞掉的前提下留下自己的印记。",
        "setting": "跨河检查点、配给大厅和 C7 内部走廊",
        "pressure": "表面有秩序的地方，往往藏着更干净也更狠的刀。",
        "hidden_route_hint": "越是完整的救援流程，越容易留下被篡改过的痕迹。",
        "visible_routes": [
            {"title": "潜入摸底", "style": "阴谋爽", "unlock_hint": "偏谋略", "payoff": "摸清对方底牌", "process_focus": "安静渗透"},
            {"title": "公开压价", "style": "直接爽", "unlock_hint": "偏名望", "payoff": "逼对方让步", "process_focus": "当众博弈"},
            {"title": "追线拿证", "style": "延迟爽", "unlock_hint": "偏天命值", "payoff": "为后续清算蓄力", "process_focus": "证据积累"},
        ],
        "chapters": [
            chapter_plan("C7来信", "判断 C7 主动邀请的真实意图", "对方既想用你，也想看清你够不够听话", "邀请函背后的编码，和暗仓账本是同一套。", "审视", "char_shen_chong"),
            chapter_plan("过桥名单", "争下第一批跨河通行资格", "名单有限，你给谁过桥就等于给谁未来", "洛月在名单里悄悄划出了秦念的旧编号。", "压抑", "char_luo_yue"),
            chapter_plan("城门试探", "在 C7 外环做第一次露脸", "一旦露怯，灰楼就会被重新定义成附属据点", "韩策在塔楼上看着你，像在评估一件要不要回收的武器。", "锋利", "char_han_ce"),
            chapter_plan("配给大厅", "看穿沈崇如何把物资变成筹码", "大厅里每一袋粮食都能换来一个站队", "你在配给章上，发现了人为加密的第二层印章。", "冷厉", "char_shen_chong"),
            chapter_plan("韩策的局", "拆掉韩策布在外环的套索", "他故意给你留口子，就是想看你怎么钻", "你拆掉套索后，对方反而送来一份更高规格的合作书。", "危险", "char_han_ce"),
            chapter_plan("假救援车", "拦下伪装成救援车的转运队", "车上装的不是药，而是被挑出来的人", "车尾夹层里，有一张写着“内环方舟”的转运票。", "震怒", "char_qin_nian"),
            chapter_plan("洛月开口", "让洛月第一次站出来替你说一句话", "她一旦表态，就再也退不回中立区", "洛月没有答应你，只把一段删减过的信号录音发了过来。", "迟疑", "char_luo_yue"),
            chapter_plan("夜审补给官", "逼出 C7 配给系统的漏洞", "人一进审讯室，背后的保护伞就会立刻动", "补给官吐出的第一个名字，不是沈崇，而是何牧。", "寒意", "char_he_mu"),
            chapter_plan("反咬一口", "顶住 C7 对灰楼的舆论反击", "你若解释过多，就会落进对方设好的叙事里", "一份匿名举报，把灰楼推上了整个外环的广播频段。", "逆风", "char_lin_shuang"),
            chapter_plan("安全区留印", "在 C7 内部留下一个无法抹掉的筹码", "这一步若成，你以后再来就是主人；若败，只能被清场", "你离开时，沈崇第一次在众人面前直呼你的名字。", "高压", "char_shen_chong"),
        ],
    },
    {
        "id": "arc_04",
        "title": "尸潮压城",
        "summary": "在围城战里立威并让旧秩序露底",
        "stage_goal": "扛住城市级尸潮，把灰楼从据点升成真正的战时核心。",
        "setting": "城西防线、临时手术点和围城广场",
        "pressure": "真正的大灾一到，谁在掌权，谁在救人，谁在借灾杀人，会被所有人一起看见。",
        "hidden_route_hint": "二次黑雨会把旧试验数据重新冲上地表。",
        "visible_routes": [
            {"title": "守线反打", "style": "碾压爽", "unlock_hint": "偏战力", "payoff": "在大场面里立威", "process_focus": "正面硬守"},
            {"title": "诱敌消耗", "style": "阴谋爽", "unlock_hint": "偏谋略", "payoff": "让敌人替你挡尸潮", "process_focus": "地形做局"},
            {"title": "抢救民心", "style": "情感爽", "unlock_hint": "偏魅力或名望", "payoff": "战后更快聚拢支持", "process_focus": "先人后线"},
        ],
        "chapters": [
            chapter_plan("尸潮预报", "判断尸潮主攻方向并提前布线", "错误判断一次，就会直接丢掉半个外环", "何牧只在地图上点了三下，说：真正的口子在你最舍不得丢的地方。", "压城", "char_he_mu"),
            chapter_plan("三道防线", "决定把有限人手放在哪三层防线上", "每补一层，另一层就会更薄", "林霜让你先回答一个问题：你要守的是墙，还是人。", "沉重", "char_lin_shuang"),
            chapter_plan("城西火墙", "用火墙拖住第一波尸潮", "火墙一起，外面的人也会被你暂时隔断", "火焰尽头，韩策的人并没有撤，反而在借火抢地。", "灼烈", "char_han_ce"),
            chapter_plan("手术灯下", "保住关键战力和关键民心", "手术台上是灰楼队长，门外是想抢药的人群", "林霜把沾血的手套摘下，说她愿意替你站到前台。", "绷紧", "char_lin_shuang"),
            chapter_plan("断桥诱敌", "用废桥把韩策和尸潮一起拖进你的预设区域", "只要差一秒，诱敌就会变成送命", "唐海在爆破器旁说：这一下，不只是炸桥。", "狠决", "char_tang_hai"),
            chapter_plan("韩策翻脸", "彻底结束和韩策之间最后一点表面合作", "翻脸意味着后面每一步都要见血", "韩策当众扯掉袖章，说以后谁拿到配给，谁就是法。", "爆裂", "char_han_ce"),
            chapter_plan("广场立威", "在最乱的时候给整个外环一个能服众的答案", "你若只杀人，会有人怕你；你若只讲理，也会被人试探", "广场屏幕亮起，匿名账号开始投放沈崇的旧账。", "昂扬", "char_luo_yue"),
            chapter_plan("灰楼反冲", "在尸潮间隙反向夺回失守点", "所有人都以为你会守，你却要在最危险的时候抢", "反冲结束后，外环第一次有人喊你“执政官”。", "燃爆", "char_tang_hai"),
            chapter_plan("黑雨二次降临", "在二次黑雨前保住核心设施与核心人", "黑雨会让旧感染体再暴起，也会冲开被埋的真相", "雨幕里，一段父亲的影像日志被自动唤醒。", "诡压", "char_qin_nian"),
            chapter_plan("围城第一功", "把这场城战的第一笔总账记到你名下", "一旦记成别人的功，之后整座城的声音都会变调", "影像日志只有一句：别让沈崇进内环主控室。", "肃立", "char_he_mu"),
        ],
    },
    {
        "id": "arc_05",
        "title": "执掌秩序",
        "summary": "用账本、名单和公开场面接管 C7",
        "stage_goal": "让旧规则在众目睽睽下崩盘，并把配给权、名单权和审判权收回自己手里。",
        "setting": "C7 议事厅、广播广场和转运中心",
        "pressure": "真正的上位不是打赢，而是让所有人承认以后得按你的规矩活。",
        "hidden_route_hint": "最完整的证据，不在账本里，而在被删改过的转运路径上。",
        "visible_routes": [
            {"title": "当众清算", "style": "直接爽", "unlock_hint": "偏名望", "payoff": "最快夺下话语权", "process_focus": "公开处刑"},
            {"title": "先控流程", "style": "阴谋爽", "unlock_hint": "偏谋略", "payoff": "低风险接管系统", "process_focus": "流程夺权"},
            {"title": "放线钓鱼", "style": "延迟爽", "unlock_hint": "偏天命值", "payoff": "挖出更深幕后", "process_focus": "借敌引敌"},
        ],
        "chapters": [
            chapter_plan("议事厅门开", "第一次带着灰楼的人进 C7 议事厅", "能进去不难，难的是进去后不被当成一次性刀子", "沈崇桌面上，摆着一把只缺最后一枚芯片的主控钥。", "压场", "char_shen_chong"),
            chapter_plan("账本上墙", "把暗仓账本变成公开武器", "账本一上墙，所有利益链都会反咬", "墙上最旧的一笔账，落款是你父亲的名字。", "错愕", "char_he_mu"),
            chapter_plan("沈崇失火", "抓住沈崇第一次真正慌乱的破绽", "老狐狸一慌，往往会先烧最关键的东西", "火场里掉出一枚写着“内环 L2”的徽章。", "猎杀", "char_shen_chong"),
            chapter_plan("配给权杖", "把配给系统从沈崇手里剥下来", "谁控配给，谁就控民心和枪口", "系统后台提示：第二权限持有者是秦念。", "锋压", "char_qin_nian"),
            chapter_plan("阿念来信", "确认秦念仍然活着并留下求救坐标", "坐标真假难辨，可能是救命线，也可能是套索", "信号末尾，秦念说：别信任何直接给你开门的人。", "心震", "char_qin_nian"),
            chapter_plan("名单第七码", "拆开被反复篡改的转运名单", "名单越往后翻，越像在看一份活人拍卖单", "洛月指着第七码说：这里通往的根本不是医院。", "阴寒", "char_luo_yue"),
            chapter_plan("清算直播", "把旧秩序最脏的部分公开给所有人看", "公开后便再无回头路，失败就只能被全城反噬", "广场大屏亮起时，韩策的人开始在外围集结。", "高燃", "char_han_ce"),
            chapter_plan("C7易主", "完成安全区权力转移", "旧班底不会甘心退出，他们只会等你露出第一个错", "主控室灯全亮的那刻，灰楼和 C7 的权限开始并网。", "夺权", "char_tang_hai"),
            chapter_plan("新秩序令", "宣布第一条真正由你制定的城规", "规矩立得太软，人会试；立得太狠，城会裂", "何牧听完后只说：现在你终于不是在求活了。", "肃穆", "char_he_mu"),
            chapter_plan("方舟坐标", "锁定内环方舟的入口与时间窗", "时间窗一过，所有证据和人都可能被彻底带走", "坐标终点，正是你从未被允许靠近的雾港。", "前压", "char_luo_yue"),
        ],
    },
    {
        "id": "arc_06",
        "title": "方舟之门",
        "summary": "冲进内环，拿回真相与最终规则",
        "stage_goal": "救出秦念，击穿净化计划，决定新世界该由谁按什么法则继续运转。",
        "setting": "雾港外环、方舟实验区和主控大厅",
        "pressure": "到了最后，最难的已经不是进门，而是拿到门后那套规则的定义权。",
        "hidden_route_hint": "父亲留给你的不是一条逃生路，而是一份选择谁该掌权的最后问卷。",
        "visible_routes": [
            {"title": "强闯方舟", "style": "碾压爽", "unlock_hint": "偏战力或名望", "payoff": "最快突破外环", "process_focus": "正面攻坚"},
            {"title": "借壳夺权", "style": "阴谋爽", "unlock_hint": "偏谋略", "payoff": "直接切主控链路", "process_focus": "权限反夺"},
            {"title": "救人再翻盘", "style": "情感爽", "unlock_hint": "偏魅力", "payoff": "保住底牌和内核关系", "process_focus": "先救关键人"},
        ],
        "chapters": [
            chapter_plan("雾港启程", "在并网后的第一夜率队出港", "新秩序刚立就远征，稍有闪失就会后院起火", "港口铁门后，停着一列本不该有电的转运列车。", "开锋", "char_tang_hai"),
            chapter_plan("方舟外环", "突破雾港到方舟之间的封锁层", "封锁层不是尸潮，而是完整武装和自动炮塔", "韩策的人出现在对岸，像是在等你先开第一枪。", "冷烈", "char_han_ce"),
            chapter_plan("父亲日志", "完整读出父亲留下的权限日志", "知道得越多，回头路就越少", "日志最后一句写着：如果你看到这里，说明沈崇已经不配活。", "沉震", "char_he_mu"),
            chapter_plan("洛月交钥", "让洛月亲手把她守着的钥匙交出来", "她交的不是钥匙，是立场和命", "洛月说她只赌一次，这次赌你不会重复旧秩序。", "试心", "char_luo_yue"),
            chapter_plan("阿念现身", "在实验区里先把秦念救出来", "救人会打乱节奏，但晚一步人就会被转走", "秦念第一句话不是求救，而是让你去关掉净化倒计时。", "心火", "char_qin_nian"),
            chapter_plan("净化真相", "公开确认黑雨和筛选计划的真实用途", "真相一旦放出，整座城都会跟着失控或觉醒", "主控屏开始全城推送倒计时，沈崇要求和你做最后交易。", "惊怒", "char_shen_chong"),
            chapter_plan("韩策末路", "解决韩策这把悬了很久的刀", "他不死，后面谁都不会真正服你", "韩策临死前说，沈崇背后的人从头到尾都没露面。", "终斩", "char_han_ce"),
            chapter_plan("灰烬之门", "打开方舟核心主控厅", "主控厅开门即是接盘，你要接的是整套生杀逻辑", "主控台弹出父亲最后一道选择题：救城，还是重写城。", "临界", "char_he_mu"),
            chapter_plan("最后的投票", "在救城与重写城之间做出最后决断", "所有人都会把自己的欲望压到你的选择上", "你按下确认键的瞬间，全城的应急灯一盏盏重新亮起。", "终局", "char_lin_shuang"),
            chapter_plan("新法点亮", "为整座临港城点亮第一条真正的新法", "权力已经到手，接下来才是更长的统治与代价", "灯光照亮整座港城时，你终于看见废墟之上的第一条新秩序线。", "昂首", "char_qin_nian"),
        ],
    },
]

CONCEPT = {
    "book_id": BOOK_ID,
    "title": TITLE,
    "genre": "末日爽文",
    "target_chapters": 60,
    "premise": STORY_BIBLE["premise"],
    "protagonist": "前仓储调度员，继承父亲留下的灰楼权限，从末日底层一路夺到安全区规则定义权",
    "core_conflict": "一边是尸潮和黑雨造成的生存极限，一边是沈崇与方舟计划代表的伪秩序筛选；你必须在两种死亡之间，抢回真正的主导权",
    "tone": "高压、狠厉、上位感强，爽点来自资源夺取、秩序重建、公开清算和隐藏真相翻面",
    "arc_structure": [
        "第1-10章：黑雨之夜——冲出坍塌城区，抢下第一批物资与灰楼钥匙",
        "第11-20章：灰楼立旗——接管灰楼、扩编班底，让外部势力第一次承认你的规矩",
        "第21-30章：安全区暗战——摸清 C7 权力链，把手伸进配给与转运系统",
        "第31-40章：尸潮压城——在大场面里守住防线并公开立威，让旧秩序开始露底",
        "第41-50章：执掌秩序——用账本、名单和主控系统接管 C7，拿到通往方舟的坐标",
        "第51-60章：方舟之门——救出秦念、击穿净化计划，决定新世界该如何继续运转",
    ],
    "key_characters": [
        "林霜（红颜）：野战医生，从旁观者走到秩序共建者",
        "韩策（宿敌）：外环武装队长，相信强者统治，最终要被你公开斩断",
        "唐海（盟友）：灰楼工程师，帮你把据点变成真正的战争机器",
        "沈崇（反派）：C7 总管，拿救援和配给筛选该活的人",
        "洛月（中立）：信号分析员，掌握内环情报，但只肯把钥匙交给值得的人",
        "何牧（师尊）：父亲旧友，引你看懂掌权的代价",
        "秦念（家族）：失踪妹妹，也是方舟权限链上的第二把钥匙",
    ],
}


STAT_BY_ROUTE = {
    "action": [("战力", 4), ("名望", 3)],
    "scheme": [("谋略", 5), ("天命值", 4)],
    "people": [("魅力", 4), ("名望", 2)],
    "supplies": [("财富", 4), ("战力", 1)],
    "clue": [("谋略", 4), ("天命值", 3)],
}


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def chapter_id(number: int) -> str:
    return f"{BOOK_ID}_ch{number:04d}"


def stage_for_number(number: int) -> dict[str, Any]:
    for stage in STAGES:
        start = stage_start(stage)
        end = start + len(stage["chapters"]) - 1
        if start <= number <= end:
            return stage
    raise ValueError(f"no stage found for chapter {number}")


def stage_start(stage: dict[str, Any]) -> int:
    offset = 1
    for current in STAGES:
        if current["id"] == stage["id"]:
            return offset
        offset += len(current["chapters"])
    raise ValueError(f"stage {stage['id']} not found")


def make_stat_effects(kind: str) -> list[dict[str, Any]]:
    return [{"stat": stat, "delta": delta} for stat, delta in STAT_BY_ROUTE[kind]]


def make_relationship_effects(character_id: str, dimension: str, delta: int) -> list[dict[str, Any]]:
    return [{"character_id": character_id, "dimension": dimension, "delta": delta}]


def build_visible_routes(number: int, stage: dict[str, Any]) -> list[dict[str, Any]]:
    routes = []
    for index, route in enumerate(stage["visible_routes"], start=1):
        routes.append(
            {
                "id": f"route_{number}_{index}",
                "title": route["title"],
                "style": route["style"],
                "unlock_hint": route["unlock_hint"],
                "payoff": route["payoff"],
                "process_focus": route["process_focus"],
            }
        )
    return routes


def pick_support_character(stage: dict[str, Any], primary_character_id: str) -> str:
    support_order = ["char_tang_hai", "char_luo_yue", "char_lin_shuang", "char_he_mu"]
    for character_id in support_order:
        if character_id != primary_character_id:
            return character_id
    return "char_tang_hai"


def character_name(character_id: str) -> str:
    return CHARACTER_NAME_BY_ID.get(character_id, character_id)


def build_choice_option(
    chapter_key: str,
    choice_slot: str,
    option_key: str,
    text: str,
    description: str,
    satisfaction_type: str,
    visible_cost: str,
    visible_reward: str,
    risk_hint: str,
    process_label: str,
    stat_kind: str,
    primary_character_id: str,
    primary_dimension: str,
    primary_delta: int,
    immediate_text: str,
    process_text: str,
) -> dict[str, Any]:
    return {
        "id": f"{chapter_key}_{choice_slot}_{option_key}",
        "text": text,
        "description": description,
        "satisfaction_type": satisfaction_type,
        "visible_cost": visible_cost,
        "visible_reward": visible_reward,
        "risk_hint": risk_hint,
        "process_label": process_label,
        "stat_effects": make_stat_effects(stat_kind),
        "relationship_effects": make_relationship_effects(primary_character_id, primary_dimension, primary_delta),
        "result_nodes": [
            {
                "text": {
                    "id": f"{chapter_key}_{choice_slot}_{option_key}_r1",
                    "content": immediate_text,
                }
            },
            {
                "text": {
                    "id": f"{chapter_key}_{choice_slot}_{option_key}_r2",
                    "content": process_text,
                }
            },
        ],
        "is_premium": False,
    }


def build_choices(
    chapter_key: str,
    stage: dict[str, Any],
    plan: dict[str, Any],
    support_character_id: str,
) -> list[dict[str, Any]]:
    conflict = plan["conflict"]
    objective = plan["objective"]
    hook = plan["hook"]
    primary_character_id = plan["feature_character_id"]

    first_choice = {
        "choice": {
            "id": f"{chapter_key}_choice_01",
            "prompt": f"{conflict}压到眼前时，你先怎么动手？",
            "choice_type": "keyDecision",
            "choices": [
                build_choice_option(
                    chapter_key,
                    "c01",
                    "a",
                    stage["visible_routes"][0]["title"],
                    f"你选择用最直接的方式撕开口子，先把{objective}做成事实，再让所有人跟上。",
                    stage["visible_routes"][0]["style"],
                    "会先把自己暴露在最危险的位置",
                    stage["visible_routes"][0]["payoff"],
                    "一旦顶不住，队伍士气会直接掉下去",
                    stage["visible_routes"][0]["process_focus"],
                    "action",
                    primary_character_id,
                    "敬畏" if primary_character_id != "char_qin_nian" else "信任",
                    10,
                    f"你没有再等，带人正面顶进 {stage['setting']} 的第一道口子。原本乱成一团的人群被你这一脚踩出了方向。",
                    f"{character_name(support_character_id)}这一线的人被你硬生生拖成了可走的路，连旁边观望的人都开始往你这边靠。",
                ),
                build_choice_option(
                    chapter_key,
                    "c01",
                    "b",
                    stage["visible_routes"][1]["title"],
                    f"你先不抢最后一步，而是利用{conflict}里最容易被忽略的空档，把后手埋进流程和位置里。",
                    stage["visible_routes"][1]["style"],
                    "眼前会显得不够痛快",
                    stage["visible_routes"][1]["payoff"],
                    "布局一旦被看穿，会同时得罪两边",
                    stage["visible_routes"][1]["process_focus"],
                    "scheme",
                    primary_character_id,
                    "信任",
                    12,
                    f"你把最想争的人和物先让出半步，转头卡住真正致命的节点。看懂的人不多，但看懂的人都开始忌惮你。",
                    f"等别人反应过来时，{objective}最关键的钥匙已经落进你的手里，现场的节奏也被你重新排好了。",
                ),
                build_choice_option(
                    chapter_key,
                    "c01",
                    "c",
                    stage["visible_routes"][2]["title"],
                    f"你优先把最关键的人从局里捞出来，让愿意跟你的人先活下来，再用人心带动局面。",
                    stage["visible_routes"][2]["style"],
                    "要先背更多压力和非议",
                    stage["visible_routes"][2]["payoff"],
                    "如果救人失败，你会同时失去局面和信任",
                    stage["visible_routes"][2]["process_focus"],
                    "people",
                    primary_character_id,
                    "好感" if primary_character_id != "char_qin_nian" else "信任",
                    15,
                    f"你先把最该活下来的人拉到自己身后。那一瞬间，周围人对你的判断第一次不再只是‘能打’，而是‘敢担’。",
                    f"有人替你扛起了第二段路，有人开始主动站队。{hook}",
                ),
            ],
        }
    }

    second_choice = {
        "choice": {
            "id": f"{chapter_key}_choice_02",
            "prompt": f"{objective}刚有起色，下一步你优先抓什么？",
            "choice_type": "styleChoice",
            "choices": [
                build_choice_option(
                    chapter_key,
                    "c02",
                    "a",
                    "先锁物资",
                    "趁场面还没彻底翻脸，把最能影响后续生存的物资先扣在手里。",
                    "碾压爽",
                    "会让对面更快察觉你的野心",
                    "立刻补厚队伍底子",
                    "容易引来下一轮抢夺",
                    "先保底盘",
                    "supplies",
                    support_character_id,
                    "信任",
                    8,
                    f"你没有把成果分出去，而是先把仓、药、车和门禁全部抓住。看起来不够体面，却让你接下来的每一步都更像掌权者。",
                    f"等别人想跟你谈条件时，发现可谈的筹码已经不在他们手里了。",
                ),
                build_choice_option(
                    chapter_key,
                    "c02",
                    "b",
                    "先拿证据",
                    "趁乱把能通向更深真相的记录、名单和日志先捞出来，哪怕眼前收益没那么直接。",
                    "阴谋爽",
                    "短时间内拿不到最直观的好处",
                    "能为下一次翻盘提前埋雷",
                    "线索可能是别人故意留下的假饵",
                    "先抓命门",
                    "clue",
                    support_character_id,
                    "敬畏" if support_character_id == "char_he_mu" else "信任",
                    10,
                    f"你把所有人的注意力都让给了场面，自己却转身去拿最会在以后杀人的东西：证据、日志和编号。",
                    f"现在看起来只是多一页纸，等它被你掀到台面上时，可能就是压垮整条利益链的最后一块铁。",
                ),
                build_choice_option(
                    chapter_key,
                    "c02",
                    "c",
                    "先扣关键人",
                    "不急着搬走东西，而是先把最关键的活人扣在自己手里，让下一轮局势必须围着你转。",
                    "扮猪吃虎",
                    "会被人觉得你手段更狠",
                    "能同时稳住线索、人质和主动权",
                    "关键人一旦失控，会当场反噬",
                    "先控活口",
                    "people",
                    primary_character_id,
                    "依赖" if primary_character_id != "char_qin_nian" else "信任",
                    9,
                    f"你没有被物资和纸面证据带偏，而是先把最关键的人扣了下来。局势一下变得很难看，却也因此只剩你能继续往下谈。",
                    f"从这一刻开始，后面每个人想抢节奏，都必须先看你的脸色再决定要不要往前迈。",
                ),
            ],
        }
    }
    return [first_choice, second_choice]


def build_chapter(number: int, stage: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    cid = chapter_id(number)
    support_character_id = pick_support_character(stage, plan["feature_character_id"])
    choice_nodes = build_choices(cid, stage, plan, support_character_id)

    nodes = [
        {
            "text": {
                "id": f"{cid}_01",
                "content": (
                    f"黑雨后的临港城像一头被撕开肚腹的巨兽，{stage['setting']}到处都是被迫停下的人和仍在往前挤的欲望。"
                    f"这一章里，你要做的只有一件事：{plan['objective']}。"
                ),
                "emphasis": "dramatic",
            }
        },
        {
            "text": {
                "id": f"{cid}_02",
                "content": (
                    f"{stage['pressure']} 而现在真正卡住你的是：{plan['conflict']}。"
                    f"你很清楚，在末日里能不能活不是先问天，而是先看谁把第一步走成规矩。"
                )
            }
        },
        {
            "dialogue": {
                "id": f"{cid}_03",
                "character_id": plan["feature_character_id"],
                "content": (
                    f"{plan['objective']}这件事，拖不起。你现在给一句话，我就按你的话走。"
                ),
                "emotion": "压低声音",
            }
        },
        {
            "text": {
                "id": f"{cid}_04",
                "content": (
                    f"你扫了一眼人群、门口和还亮着的指示灯。每个人都在等结果，但没有人能替你承担后果。"
                    f"如果这一轮做对，你得到的不只是眼前的生路，还会多一层以后压人的筹码。"
                )
            }
        },
        choice_nodes[0],
        {
            "text": {
                "id": f"{cid}_05",
                "content": (
                    f"第一轮动作落下后，现场果然开始重新排队。愿意赌你的人变多了，想趁机从你身上撕块肉的人也变多了。"
                    f"你没有停下来，因为真正的好东西往往只在第二次伸手时才会露出来。"
                )
            }
        },
        {
            "dialogue": {
                "id": f"{cid}_06",
                "character_id": support_character_id,
                "content": (
                    f"眼前这口气可以先喘，但{plan['hook']}如果不提前盯住，后面就会长成要命的洞。"
                ),
                "emotion": "提醒",
            }
        },
        choice_nodes[1],
        {
            "text": {
                "id": f"{cid}_07",
                "content": (
                    f"更关键的是，这一轮不是单纯把眼前的危机压过去而已。你已经把{plan['objective']}这件事变成了可复制的样板："
                    f"先看谁在场、谁有筹码、谁会在下一步翻脸，然后把主动权一层一层拽回自己手里。"
                )
            }
        },
        {
            "text": {
                "id": f"{cid}_08",
                "content": (
                    f"你把今天的结果重新算了一遍：剧情推进了，关系有人靠近也有人更恨，最重要的是信息开始向你手里聚。"
                    f"在这座城里，谁能同时攥住这三样东西，谁才配谈秩序。"
                )
            }
        },
        {
            "text": {
                "id": f"{cid}_09",
                "content": plan["hook"],
                "emphasis": "dramatic",
            }
        },
    ]

    return {
        "id": cid,
        "book_id": BOOK_ID,
        "number": number,
        "title": plan["title"],
        "is_paid": number > BOOK_META["free_chapters"],
        "next_chapter_hook": plan["hook"],
        "nodes": nodes,
    }


def build_walkthrough() -> dict[str, Any]:
    stage_entries = []
    chapter_guides = []
    number = 1

    for stage in STAGES:
        chapter_ids = []
        for plan in stage["chapters"]:
            cid = chapter_id(number)
            chapter_ids.append(cid)
            chapter_guides.append(
                {
                    "chapter_id": cid,
                    "stage_id": stage["id"],
                    "public_summary": f"第{number}章：{stage['summary']}阶段，你围绕“{plan['objective']}”继续推进。",
                    "objective": plan["objective"],
                    "estimated_minutes": 6,
                    "interaction_count": 2,
                    "visible_routes": build_visible_routes(number, stage),
                    "hidden_route_hint": stage["hidden_route_hint"],
                }
            )
            number += 1

        stage_entries.append(
            {
                "id": stage["id"],
                "title": stage["title"],
                "summary": stage["summary"],
                "chapter_ids": chapter_ids,
            }
        )

    return {
        "book_id": BOOK_ID,
        "title": f"{TITLE}·命运图谱",
        "stages": stage_entries,
        "chapter_guides": chapter_guides,
    }


def build_chapters() -> list[dict[str, Any]]:
    chapters = []
    number = 1
    for stage in STAGES:
        for plan in stage["chapters"]:
            chapters.append(build_chapter(number, stage, plan))
            number += 1
    return chapters


def build_story_package() -> dict[str, Any]:
    return {
        "book": BOOK_META,
        "reader_desire_map": READER_DESIRE_MAP,
        "story_bible": STORY_BIBLE,
        "route_graph": ROUTE_GRAPH,
        "walkthrough": build_walkthrough(),
        "chapters": build_chapters(),
    }


def write_outputs() -> None:
    root = Path(__file__).resolve().parents[1]
    concept_path = root / "concepts" / f"{BOOK_ID}.json"
    package_path = root / "projects" / BOOK_ID / "story_package.json"

    payload = build_story_package()
    errors, warnings, _ = validate_package(payload)
    if errors:
        raise SystemExit("validation failed:\n- " + "\n- ".join(errors))

    dump_json(concept_path, CONCEPT)
    dump_json(package_path, payload)

    print(f"wrote {concept_path}")
    print(f"wrote {package_path}")
    for warning in warnings:
        print(f"warning: {warning}")


def main() -> None:
    write_outputs()


if __name__ == "__main__":
    main()
