#!/usr/bin/env python3
"""
弃徒逆天 (xianxia_001) — 1000章节故事包生成器
前20章：完整散文质量
第21-1000章：结构完整骨架（可用 generate_story.py 扩写）

用法：
    python3 scaffold_xianxia_001.py
    # 输出到 story-factory/projects/xianxia_001/story_package.json
"""
import json
import sys
from pathlib import Path

BOOK_ID = "xianxia_001"

# ─────────────────────────────────────────────
# 书籍元数据
# ─────────────────────────────────────────────
BOOK_META = {
    "id": BOOK_ID,
    "title": "弃徒逆天",
    "author": "命书工作室",
    "cover_image_name": "cover_xianxia_001",
    "synopsis": "你本是天青宗最有潜力的弟子，却在入宗大典上被诊断为\"死灵根\"，遭掌门当众驱逐。流落三年，你在一处古洞中得到了上古洪荒至尊根脉的传承——那个所谓的\"死灵根\"，不过是封住一切的枷锁。带着压抑三年的怒火，你踏上归途。当年欠下的，一笔一笔清算。",
    "genre": "修仙升级",
    "tags": ["废材逆袭", "上古传承", "打脸宗门", "父辈旧案", "洪荒血脉"],
    "interaction_tags": ["高互动", "战力成长", "关系推进", "攻略图"],
    "total_chapters": 1000,
    "free_chapters": 20,
    "characters": [
        {
            "id": "char_song_xuan",
            "name": "宋玄",
            "title": "天青宗掌门",
            "avatar_image_name": "avatar_song_xuan",
            "description": "天青宗现任掌门，仙风道骨，深受弟子敬仰。三年前以\"维护宗门纯洁\"为名当众驱逐你。表面公正，实则是当年构陷你父亲的主谋之一。",
            "role": "反派"
        },
        {
            "id": "char_li_qingyun",
            "name": "李青云",
            "title": "天青宗首席弟子",
            "avatar_image_name": "avatar_li_qingyun",
            "description": "与你同届入宗的师兄，七阶风雷双灵根，号称百年一遇天才。驱逐那天是他第一个落井下石。随你崛起，嫉恨一步步将他逼向疯狂。",
            "role": "宿敌"
        },
        {
            "id": "char_chen_xue",
            "name": "陈雪",
            "title": "天青宗内门弟子",
            "avatar_image_name": "avatar_chen_xue",
            "description": "你入宗时最亲近的同门，曾一起习剑、一起看日落。驱逐那天她沉默了，没有为你说一句话。三年来，这份愧疚从未消散。",
            "role": "红颜"
        },
        {
            "id": "char_shi_po",
            "name": "石婆婆",
            "title": "山间隐者",
            "avatar_image_name": "avatar_shi_po",
            "description": "天青山脚下的神秘老人，实为上古强者\"开天老祖\"的最后一名弟子，在山中等待有缘人继承传承，已等了三千年。",
            "role": "盟友"
        },
        {
            "id": "char_second_elder",
            "name": "凌峰",
            "title": "天青宗二长老",
            "avatar_image_name": "avatar_second_elder",
            "description": "宗门二长老，与掌门不和多年。当年驱逐你时，他是唯一投了反对票的人。他知道的比任何人都多，却一直在等待时机。",
            "role": "师尊"
        },
        {
            "id": "char_third_elder",
            "name": "韩铁",
            "title": "天青宗三长老",
            "avatar_image_name": "avatar_third_elder",
            "description": "掌管宗门惩戒堂，宋玄的左膀右臂，心狠手辣。当年构陷你父亲，是宋玄棋局里最得力的棋子。",
            "role": "反派"
        }
    ],
    "initial_stats": {
        "combat": 5,
        "fame": 5,
        "strategy": 20,
        "wealth": 5,
        "charm": 10,
        "darkness": 0,
        "destiny": 30
    }
}

READER_DESIRE_MAP = {
    "core_fantasy": "从被所有人认定的废物，一步步变成让旧规则为你让路的人。",
    "reward_promises": ["每次打脸都有完整的公开过程", "力量成长清晰可见", "旧账一笔一笔当众清算"],
    "control_promises": ["决定用什么打法和节奏崛起", "决定哪些关系值得维护或利用", "决定何时揭开身份"],
    "suspense_questions": ["父亲的死究竟是谁的手笔", "掌门当年为何非要驱逐你", "古戒里封印的究竟是什么"]
}

STORY_BIBLE = {
    "premise": "一个被封印了真实天赋的弃徒，在得到上古传承后回宗清算，顺带揭开父辈旧案。",
    "mainline_goal": "重返宗门，一层层揭开宋玄的真面目，为父报仇，并找到上古封印的真相。",
    "side_threads": ["陈雪的救赎之路", "凌峰的隐忍与抉择", "李青云从嫉恨到彻底堕落", "石婆婆与开天老祖的未竟之事"],
    "hidden_truths": [
        "主角的灵根被人为封印，幕后是宋玄勾结外部势力",
        "父亲并非走火入魔而死，而是发现了宋玄的秘密后被灭口",
        "古戒不只是传承容器，它本身就是开天老祖留给宗门的最后审判"
    ]
}

ROUTE_GRAPH = {
    "mainline": "觉醒 -> 归来 -> 宗门内战 -> 出走历练 -> 旧案揭露 -> 天道归一",
    "side_routes": ["陈雪关系线", "凌峰立场线", "李青云对立线"],
    "hidden_routes": ["父亲旧案线", "开天老祖遗嘱线", "宋玄背后主谋线"],
    "milestones": [
        {"id": "m01", "title": "传承觉醒", "chapter_range": "1-10"},
        {"id": "m02", "title": "大比亮相", "chapter_range": "11-30"},
        {"id": "m03", "title": "身份公开", "chapter_range": "31-50"},
        {"id": "m04", "title": "宗门倾覆", "chapter_range": "351-400"},
        {"id": "m05", "title": "父仇得报", "chapter_range": "451-500"},
        {"id": "m06", "title": "天道至尊", "chapter_range": "951-1000"},
    ]
}

# ─────────────────────────────────────────────
# 20卷弧线定义（每卷50章）
# ─────────────────────────────────────────────
ARC_DEFS = [
    {"id": "arc_01", "title": "觉醒篇", "start": 1, "end": 50, "realm": "炼气期",
     "beats": [
         (1,  10,  "逐出与漂泊", "大典被驱逐，三年流浪，古戒指引"),
         (11, 20,  "传承觉醒",   "发现古洞，得到上古传承，真正灵根苏醒"),
         (21, 30,  "归来初战",   "化名参加宗门大比，第一次公开实力"),
         (31, 40,  "身份危机",   "宋玄起疑，三长老追杀，暗流涌动"),
         (41, 50,  "第一次清算", "当众揭开身份，凌峰出手，完成第一卷对决"),
     ],
     "chars": ["char_song_xuan", "char_li_qingyun", "char_chen_xue", "char_shi_po", "char_second_elder"]},

    {"id": "arc_02", "title": "宗门暗战", "start": 51, "end": 100, "realm": "筑基期",
     "beats": [
         (51, 60,  "宗门风波",   "身份公开后的连锁反应，各方势力重新站队"),
         (61, 70,  "筑基突破",   "突破筑基期，实力大幅跃升"),
         (71, 80,  "内部暗斗",   "宗门长老们的利益博弈，你成为棋子与猎手"),
         (81, 90,  "李青云堕落", "嫉恨驱使李青云走向魔功，第一次真正的生死对决"),
         (91, 100, "掌门退让",   "宋玄被迫第一次让步，但阴谋从未停止"),
     ],
     "chars": ["char_song_xuan", "char_li_qingyun", "char_chen_xue", "char_second_elder", "char_third_elder"]},

    {"id": "arc_03", "title": "破境天玄", "start": 101, "end": 150, "realm": "金丹期",
     "beats": [
         (101, 110, "秘境历练",   "宗门组织进入秘境，危机四伏"),
         (111, 120, "秘境深处",   "遭遇古代禁区，发现父亲遗迹"),
         (121, 130, "金丹突破",   "在极端压力下突破金丹期"),
         (131, 140, "秘境变故",   "秘境崩塌，生死边缘"),
         (141, 150, "旧案线索",   "带回第一条关于父亲死因的真实线索"),
     ],
     "chars": ["char_li_qingyun", "char_chen_xue", "char_second_elder", "char_shi_po"]},

    {"id": "arc_04", "title": "真相初现", "start": 151, "end": 200, "realm": "金丹期",
     "beats": [
         (151, 160, "线索追查",   "根据遗迹线索追查父亲旧案"),
         (161, 170, "三长老的秘密", "韩铁的秘密开始暴露"),
         (171, 180, "宋玄的布局", "意识到宋玄掌控的范围远超想象"),
         (181, 190, "抉择时刻",   "必须决定是继续忍耐还是提前摊牌"),
         (191, 200, "第一次正面冲突", "与三长老韩铁的正面对决"),
     ],
     "chars": ["char_song_xuan", "char_third_elder", "char_chen_xue", "char_second_elder"]},

    {"id": "arc_05", "title": "历练天下", "start": 201, "end": 250, "realm": "元婴期初期",
     "beats": [
         (201, 210, "离宗出走",   "宗门局势僵化，主动离开寻找更大的舞台"),
         (211, 220, "江湖初探",   "第一次接触宗门外的世界，格局大开"),
         (221, 230, "奇遇连连",   "历练途中遇到各路强者，积累实力"),
         (231, 240, "结交盟友",   "在外界结识真正志同道合的修士"),
         (241, 250, "元婴突破",   "一次生死战役中突破元婴期"),
     ],
     "chars": ["char_chen_xue", "char_second_elder", "char_shi_po"]},

    {"id": "arc_06", "title": "联盟博弈", "start": 251, "end": 300, "realm": "元婴期",
     "beats": [
         (251, 260, "势力角逐",   "外界各大势力对你的关注与拉拢"),
         (261, 270, "借势而行",   "利用各方矛盾为己所用"),
         (271, 280, "宗门传信",   "天青宗内部出现新的变故"),
         (281, 290, "两线并行",   "外界历练与宗门暗局同时推进"),
         (291, 300, "回归前夕",   "积累足够筹码，准备真正的回归"),
     ],
     "chars": ["char_song_xuan", "char_li_qingyun", "char_chen_xue", "char_second_elder"]},

    {"id": "arc_07", "title": "旧案重启", "start": 301, "end": 350, "realm": "元婴期",
     "beats": [
         (301, 310, "归来震动",   "带着元婴修为归来，整个宗门震动"),
         (311, 320, "证据收集",   "系统性收集父亲旧案的证据"),
         (321, 330, "宋玄的底牌", "宋玄开始动用隐藏多年的力量"),
         (331, 340, "凌峰的真相", "二长老凌峰的真实目的终于揭晓"),
         (341, 350, "旧案大白",   "父亲死因当众揭露，宗门陷入震荡"),
     ],
     "chars": ["char_song_xuan", "char_second_elder", "char_third_elder", "char_chen_xue"]},

    {"id": "arc_08", "title": "宗门倾覆", "start": 351, "end": 400, "realm": "化神期初期",
     "beats": [
         (351, 360, "清算开始",   "以父仇和旧案为由，开始正式清算"),
         (361, 370, "宋玄真面目", "宋玄彻底撕破伪装"),
         (371, 380, "化神突破",   "对决中突破化神期"),
         (381, 390, "宗门之战",   "天青宗内战，各派势力最终站队"),
         (391, 400, "宋玄伏法",   "宋玄被彻底清算，天青宗易主"),
     ],
     "chars": ["char_song_xuan", "char_li_qingyun", "char_second_elder", "char_third_elder", "char_chen_xue"]},

    {"id": "arc_09", "title": "新的棋局", "start": 401, "end": 450, "realm": "化神期",
     "beats": [
         (401, 410, "天青宗重建", "接手天青宗，面临重建与外部威胁"),
         (411, 420, "背后黑手",   "发现宋玄背后还有更大的幕后势力"),
         (421, 430, "势力扩张",   "以天青宗为基，开始构建更大的势力网络"),
         (431, 440, "真正的敌人", "幕后势力开始正式出手"),
         (441, 450, "父仇终结",   "最后的父仇得到彻底清算"),
     ],
     "chars": ["char_song_xuan", "char_chen_xue", "char_second_elder", "char_shi_po"]},

    {"id": "arc_10", "title": "父仇雪恨", "start": 451, "end": 500, "realm": "化神期后期",
     "beats": [
         (451, 460, "上界信息",   "得知父亲在上界留有后手"),
         (461, 470, "旧势力残余", "清除所有与构陷父亲有关的势力"),
         (471, 480, "传承完整",   "获得父亲留下的最后传承"),
         (481, 490, "第一卷终章", "彻底清算所有旧账，站在新的起点"),
         (491, 500, "上界呼唤",   "感受到来自上界的呼唤，新的征途开始"),
     ],
     "chars": ["char_chen_xue", "char_second_elder", "char_shi_po"]},

    {"id": "arc_11", "title": "上界初临", "start": 501, "end": 550, "realm": "合体期",
     "beats": [
         (501, 510, "渡劫飞升",   "突破渡劫期，准备飞升上界"),
         (511, 520, "上界降临",   "第一次踏上上界，格局再度颠覆"),
         (521, 530, "上界规则",   "了解上界的秩序与势力格局"),
         (531, 540, "身份重塑",   "在上界重新建立自己的名声"),
         (541, 550, "合体突破",   "突破合体期，真正立足上界"),
     ],
     "chars": ["char_chen_xue", "char_shi_po"]},

    {"id": "arc_12", "title": "诸域纷争", "start": 551, "end": 600, "realm": "合体期",
     "beats": [
         (551, 560, "上界势力图", "了解上界各大势力的角力"),
         (561, 570, "利益角逐",   "被卷入上界最大的势力博弈"),
         (571, 580, "开天传承",   "在上界发现开天老祖传承的延续"),
         (581, 590, "上界旧案",   "父亲在上界的遭遇真相"),
         (591, 600, "新的盟友",   "在上界结识真正可以信赖的同行者"),
     ],
     "chars": ["char_chen_xue", "char_shi_po"]},

    {"id": "arc_13", "title": "天机浮现", "start": 601, "end": 650, "realm": "大乘期初期",
     "beats": [
         (601, 610, "大乘突破",   "突破大乘期，窥见天道"),
         (611, 620, "天机感应",   "开始感应到更深层的天道运转"),
         (621, 630, "洪荒印记",   "洪荒血脉完全激活，吸引各方关注"),
         (631, 640, "上古阴谋",   "发现上古时期遗留下来的更大阴谋"),
         (641, 650, "天机线索",   "追踪天机，获得关键线索"),
     ],
     "chars": ["char_chen_xue", "char_shi_po"]},

    {"id": "arc_14", "title": "天劫磨砺", "start": 651, "end": 700, "realm": "大乘期",
     "beats": [
         (651, 660, "天劫降临",   "遭遇非常规天劫，九死一生"),
         (661, 670, "渡劫之战",   "在天劫中对抗上界最强敌"),
         (671, 680, "劫后余生",   "渡过天劫，实力质变"),
         (681, 690, "天道痕迹",   "在天劫中感受到天道有意为之"),
         (691, 700, "更强之敌",   "在渡劫后立刻面临更强的挑战"),
     ],
     "chars": ["char_chen_xue"]},

    {"id": "arc_15", "title": "九域争锋", "start": 701, "end": 750, "realm": "大乘期后期",
     "beats": [
         (701, 710, "九域格局",   "了解九域的整体布局和力量对比"),
         (711, 720, "域间纷争",   "被卷入九域最大的领土争夺"),
         (721, 730, "多线博弈",   "同时在多个域间布局"),
         (731, 740, "洪荒之力",   "洪荒血脉在九域争锋中彻底爆发"),
         (741, 750, "九域定鼎",   "九域秩序重新确立"),
     ],
     "chars": ["char_chen_xue", "char_shi_po"]},

    {"id": "arc_16", "title": "洪荒觉醒", "start": 751, "end": 800, "realm": "渡劫期",
     "beats": [
         (751, 760, "血脉共鸣",   "洪荒血脉与上古存在发生共鸣"),
         (761, 770, "渡劫期突破", "突破渡劫期，站在飞升门槛"),
         (771, 780, "洪荒真相",   "洪荒时代的真相开始揭晓"),
         (781, 790, "上古战争",   "上古时代遗留的战争余波"),
         (791, 800, "洪荒传承完整", "得到完整的洪荒传承"),
     ],
     "chars": ["char_chen_xue", "char_shi_po"]},

    {"id": "arc_17", "title": "天门之战", "start": 801, "end": 850, "realm": "渡劫期后期",
     "beats": [
         (801, 810, "天门显现",   "传说中的天门开始出现"),
         (811, 820, "天门争夺",   "各大势力疯狂争夺进入天门的资格"),
         (821, 830, "天门之内",   "进入天门，面对终极试炼"),
         (831, 840, "天门决战",   "在天门内与最强对手决战"),
         (841, 850, "天道认可",   "通过天门试炼，获得天道认可"),
     ],
     "chars": ["char_chen_xue"]},

    {"id": "arc_18", "title": "大道之争", "start": 851, "end": 900, "realm": "仙人期",
     "beats": [
         (851, 860, "飞升仙界",   "突破仙人境，正式飞升仙界"),
         (861, 870, "仙界格局",   "仙界的势力比上界更加复杂"),
         (871, 880, "大道感悟",   "在仙界开始真正感悟大道"),
         (881, 890, "道统之争",   "被卷入仙界最深层的道统争夺"),
         (891, 900, "大道抉择",   "面临道路的根本抉择"),
     ],
     "chars": ["char_chen_xue"]},

    {"id": "arc_19", "title": "封印之秘", "start": 901, "end": 950, "realm": "仙人期",
     "beats": [
         (901, 910, "封印溯源",   "追溯当年封印自己灵根的真正原因"),
         (911, 920, "上古秘密",   "封印背后是一个跨越时代的秘密"),
         (921, 930, "开天老祖真相", "开天老祖留下传承的真实目的"),
         (931, 940, "封印破除",   "彻底破除所有封印，真正的自我觉醒"),
         (941, 950, "宿命对决",   "与命运本身的对决"),
     ],
     "chars": ["char_chen_xue", "char_shi_po"]},

    {"id": "arc_20", "title": "天道至尊", "start": 951, "end": 1000, "realm": "天道期",
     "beats": [
         (951, 960,  "天道之路",   "踏上成为天道的最后一段路"),
         (961, 970,  "最终试炼",   "天道给予的最终考验"),
         (971, 980,  "宿命终结",   "彻底了结所有的宿命纠葛"),
         (981, 990,  "天道归位",   "天道回归正轨"),
         (991, 1000, "新的开始",   "以天道之身，开启全新的篇章"),
     ],
     "chars": ["char_chen_xue"]},
]

# ─────────────────────────────────────────────
# 前20章完整内容
# ─────────────────────────────────────────────
def ch(num):
    return f"{BOOK_ID}_ch{num:04d}"

FULL_CHAPTERS = [
    # ── 第1章 ──────────────────────────────────
    {
        "id": ch(1), "book_id": BOOK_ID, "number": 1, "title": "逐出师门",
        "is_paid": False,
        "next_chapter_hook": "离开天青山的那一夜，掌心的古铜戒指第一次真正地发烫。仿佛有什么东西在等待，等待你准备好。",
        "nodes": [
            {"text": {"id": f"{ch(1)}_01", "content": "天青宗。大典日。三百余名弟子整齐列队于宗门广场，晨雾未散，青石地板映出每个人的影子。", "emphasis": "dramatic"}},
            {"text": {"id": f"{ch(1)}_02", "content": "你站在队列中间，心跳平稳。入宗两年，你的修为不算出众，却也从未落后太多。你以为，今天只是一次普通的验证。"}},
            {"text": {"id": f"{ch(1)}_03", "content": "测灵石悬浮于主台中央，乳白色光芒流转。每当有人触碰，它便显示对方灵根的阶位与属性。李青云走上台，七阶风雷双灵根，测灵石放出耀眼白光，广场上爆发雷鸣般的喝彩。"}},
            {"text": {"id": f"{ch(1)}_04", "content": "轮到你了。你将手掌贴上石面——冰凉的触感，然后是漫长的沉默。一秒，两秒，五秒。测灵石毫无反应。", "emphasis": "dramatic"}},
            {"dialogue": {"id": f"{ch(1)}_05", "character_id": "char_li_qingyun", "content": "哈——还真是天生废料。两年，连一点痕迹都留不下。", "emotion": "讥笑"}},
            {"text": {"id": f"{ch(1)}_06", "content": "笑声从四面涌来。你站在台上，手仍然贴着那块石头。右手无名指上，父亲临终前给你的古铜戒指，第一次泛出一丝微弱的温热。"}},
            {"dialogue": {"id": f"{ch(1)}_07", "character_id": "char_song_xuan", "content": "死灵根，不可修炼。为维护宗门纯洁，本门不能留存无根之人。——给你三日打点，之后离山。", "emotion": "平静"}},
            {"text": {"id": f"{ch(1)}_08", "content": "就这样。没有犹豫，没有给你辩解的空间。三百双眼睛看着你——有嘲笑的，有同情的，有看热闹的。还有一双是陈雪的，站在队列最后，嘴唇微微颤动，却没有说话。"}},
            {"choice": {
                "id": f"{ch(1)}_choice_01",
                "prompt": "三百人看着你，掌门的判决落地。此刻，你——",
                "choice_type": "keyDecision",
                "choices": [
                    {"id": f"{ch(1)}_c01_a", "text": "当场质疑，要求重测",
                     "description": "这块石头一定是对的？当众要求重新验证，让所有人看清楚——你不认输。",
                     "satisfaction_type": "直接爽",
                     "visible_cost": "直接与掌门对立",
                     "visible_reward": "展示不屈气节",
                     "risk_hint": "可能加重处罚",
                     "process_label": "正面抗争",
                     "stat_effects": [{"stat": "名望", "delta": 5}],
                     "relationship_effects": [{"character_id": "char_song_xuan", "dimension": "敌意", "delta": 15}, {"character_id": "char_chen_xue", "dimension": "好感", "delta": 10}],
                     "result_nodes": [{"text": {"id": f"{ch(1)}_r01_a", "content": "你站直身体，抬起头看向宋玄——那双眼睛里没有惶恐，只有一种冷静的锋芒。广场上的笑声戛然而止。"}}],
                     "is_premium": False},
                    {"id": f"{ch(1)}_c01_b", "text": "低头接受，暗记每一张脸",
                     "description": "今天不是时候。弯下腰，行礼，转身。但你把今天每一张笑脸、每一声嘲讽，全都刻进记忆。",
                     "satisfaction_type": "延迟爽",
                     "visible_cost": "暂时受辱",
                     "visible_reward": "保存实力，伺机而动",
                     "risk_hint": "被认为真的认命",
                     "process_label": "隐忍蛰伏",
                     "stat_effects": [{"stat": "谋略", "delta": 10}, {"stat": "天命值", "delta": 10}],
                     "relationship_effects": [{"character_id": "char_shi_po", "dimension": "信任", "delta": 10}],
                     "result_nodes": [{"text": {"id": f"{ch(1)}_r01_b", "content": "你弯下腰，行了一个标准的弟子礼。广场上爆发出新一轮的嘲笑。你什么都没说，但每一张脸，你都看清楚了。"}}],
                     "is_premium": False},
                    {"id": f"{ch(1)}_c01_c", "text": "平静地看向掌门，一字一句：'好。'",
                     "description": "不愤怒，不哭泣，不求情。用最平静的声音接受这个结果——这份平静，比任何反应都更令人心悸。",
                     "satisfaction_type": "扮猪吃虎",
                     "visible_cost": "被认为软弱",
                     "visible_reward": "在暗处留下更深的印象",
                     "risk_hint": "无",
                     "process_label": "以静制动",
                     "stat_effects": [{"stat": "谋略", "delta": 15}, {"stat": "魅力", "delta": 10}, {"stat": "天命值", "delta": 15}],
                     "relationship_effects": [{"character_id": "char_song_xuan", "dimension": "敬畏", "delta": 10}, {"character_id": "char_chen_xue", "dimension": "好感", "delta": 15}],
                     "result_nodes": [{"text": {"id": f"{ch(1)}_r01_c", "content": "你转向宋玄，发音清晰：'好。' 就这一个字。宋玄微微皱眉，广场上不知为何也静了一瞬。"}}],
                     "is_premium": False},
                ]
            }},
            {"text": {"id": f"{ch(1)}_09", "content": "三天后。宗门山门口。\n你背着行囊，站在青石台阶上，山风把发丝吹乱。身后是两年生活的地方，前面是你从未走过的路。"}},
            {"text": {"id": f"{ch(1)}_10", "content": "脚步声从身后传来。陈雪追了出来，发丝散乱，眼眶微红。她站在你三步外，张了张嘴，一个字也说不出来。"}},
            {"choice": {
                "id": f"{ch(1)}_choice_02",
                "prompt": "陈雪追了出来，大典那天她没有开口，如今又追出来——你选择——",
                "choice_type": "characterPref",
                "choices": [
                    {"id": f"{ch(1)}_c02_a", "text": "回头，对她说：'等我回来。'",
                     "description": "不是安慰，是承诺。",
                     "satisfaction_type": "情感爽",
                     "visible_cost": "暴露情感",
                     "visible_reward": "建立深层羁绊",
                     "risk_hint": "承诺有重量",
                     "process_label": "真诚表态",
                     "stat_effects": [{"stat": "魅力", "delta": 10}],
                     "relationship_effects": [{"character_id": "char_chen_xue", "dimension": "好感", "delta": 20}, {"character_id": "char_chen_xue", "dimension": "信任", "delta": 10}],
                     "result_nodes": [{"text": {"id": f"{ch(1)}_r02_a", "content": "你回过头，对上她的眼睛，说了三个字。她愣了一下，然后眼眶里的泪终于滚落下来。"}}],
                     "is_premium": False},
                    {"id": f"{ch(1)}_c02_b", "text": "什么都不说，转身下山",
                     "description": "有些话说出来会变质。留给她一个背影——和一个问题。",
                     "satisfaction_type": "延迟爽",
                     "visible_cost": "让她难受",
                     "visible_reward": "在她心里留下更深的印记",
                     "risk_hint": "关系可能冷却",
                     "process_label": "以沉默说话",
                     "stat_effects": [{"stat": "谋略", "delta": 5}],
                     "relationship_effects": [{"character_id": "char_chen_xue", "dimension": "依赖", "delta": 15}],
                     "result_nodes": [{"text": {"id": f"{ch(1)}_r02_b", "content": "你没有回头。一步，两步，山路拐弯处，天青宗的山门从视野里消失。身后没有追来的声音。"}}],
                     "is_premium": False},
                ]
            }},
            {"text": {"id": f"{ch(1)}_11", "content": "你走下山，没有回头。身后，山门缓缓合拢。\n掌心里，那枚古铜戒指的温度，比你记忆中任何时候都要高。", "emphasis": "dramatic"}},
        ]
    },
    # ── 第2章 ──────────────────────────────────
    {
        "id": ch(2), "book_id": BOOK_ID, "number": 2, "title": "漂泊三年",
        "is_paid": False,
        "next_chapter_hook": "第三年的最后一夜，你蜷缩在客栈最便宜的草铺上，掌心古戒忽然发出有方向感的温热——朝着天青山的方向，一点一点往上升。",
        "nodes": [
            {"text": {"id": f"{ch(2)}_01", "content": "三年。凡人计时，一千多个日夜。", "emphasis": "dramatic"}},
            {"text": {"id": f"{ch(2)}_02", "content": "你做过镖师的力夫，替药铺搬运草药，在码头卸过货，在山谷里挖过矿石。没有灵力可用，你就用凡人的方式活着。修炼早已停了，但身体在劳作中变得出奇地结实。"}},
            {"text": {"id": f"{ch(2)}_03", "content": "有时候，在深夜的草铺上，你会想起天青宗，想起那块测灵石，想起李青云的笑声。那些记忆不会让你痛苦，它们只是让你更清醒——你有账要算，但不是现在。"}},
            {"dialogue": {"id": f"{ch(2)}_04", "character_id": "char_shi_po", "content": "（半年前，你在山脚下的茶摊第一次见到这个老妪。她用浑浊的眼睛看着你，说：'你手上的那个东西，我认识。'）", "emotion": "意味深长"}},
            {"text": {"id": f"{ch(2)}_05", "content": "石婆婆是唯一一个没有嘲笑过你的人。她偶尔会留你吃饭，说一些听不懂的话，比如——'有些根脉，越封越深，越深越烈。等时候到了，你就明白了。'"}},
            {"choice": {
                "id": f"{ch(2)}_choice_01",
                "prompt": "三年里，面对凡人世界的重重困苦，你如何保存自己的志向？",
                "choice_type": "styleChoice",
                "choices": [
                    {"id": f"{ch(2)}_c01_a", "text": "每天默默重复修炼动作，保持手感",
                     "description": "没有灵力，就只练动作。三年下来，身体记忆里藏着的东西，比很多人练功还要深。",
                     "satisfaction_type": "延迟爽",
                     "visible_cost": "被人嘲笑无意义",
                     "visible_reward": "积累扎实的武学基础",
                     "risk_hint": "无",
                     "process_label": "日复一日",
                     "stat_effects": [{"stat": "战力", "delta": 10}, {"stat": "谋略", "delta": 5}],
                     "relationship_effects": [],
                     "result_nodes": [{"text": {"id": f"{ch(2)}_r01_a", "content": "三年里，每天天亮前，你都会在没人的角落把所有记得的剑法和拳法走一遍。没有灵力，动作却越来越精准。"}}],
                     "is_premium": False},
                    {"id": f"{ch(2)}_c01_b", "text": "观察凡人世界的生存规则，研究人心",
                     "description": "修炼可以暂停，但看人这件事，一刻都没停过。三年，你把各色人等看了个透。",
                     "satisfaction_type": "阴谋爽",
                     "visible_cost": "显得冷漠",
                     "visible_reward": "超强的人心洞察力",
                     "risk_hint": "无",
                     "process_label": "冷眼旁观",
                     "stat_effects": [{"stat": "谋略", "delta": 15}, {"stat": "魅力", "delta": 5}],
                     "relationship_effects": [],
                     "result_nodes": [{"text": {"id": f"{ch(2)}_r01_b", "content": "三年，你看过无数次利益交换、背叛与忠诚。修仙界那些所谓的道义，不过是凡人世界的浓缩版本罢了。"}}],
                     "is_premium": False},
                    {"id": f"{ch(2)}_c01_c", "text": "将愤怒转化为动力，从不停止思考报仇之法",
                     "description": "愤怒是最持久的燃料，只要控制好，就不会烧掉自己。",
                     "satisfaction_type": "直接爽",
                     "visible_cost": "情绪负担",
                     "visible_reward": "极度清晰的目标感",
                     "risk_hint": "可能被仇恨扭曲",
                     "process_label": "以恨驱动",
                     "stat_effects": [{"stat": "天命值", "delta": 10}, {"stat": "黑化值", "delta": 5}],
                     "relationship_effects": [],
                     "result_nodes": [{"text": {"id": f"{ch(2)}_r01_c", "content": "三年里，你在无数个深夜把当年所有的细节重新推演。每推一次，你的路就更清晰一点。"}}],
                     "is_premium": False},
                ]
            }},
            {"text": {"id": f"{ch(2)}_06", "content": "三年最后一个冬天，你在一座小城打短工。某一天，一个穿着绸缎的年轻修士路过，无意间和你撞了个正着。他皱着眉头，居高临下地看你：'没长眼睛吗？'"}},
            {"text": {"id": f"{ch(2)}_07", "content": "三年前，这种眼神会让你心里泛起一阵苦涩。三年后，你只是平静地抬起眼睛，目光和他对了一秒——那个修士没来由地退了半步，然后快步走开了。"}},
            {"text": {"id": f"{ch(2)}_08", "content": "他大概不知道，他刚才对上的那双眼睛里，藏着三年没有被磨掉的、比他强大十倍的东西。", "emphasis": "system"}},
            {"choice": {
                "id": f"{ch(2)}_choice_02",
                "prompt": "石婆婆今晚把你叫去，说：'你准备好了吗？那个地方，等你等了很久了。'",
                "choice_type": "keyDecision",
                "choices": [
                    {"id": f"{ch(2)}_c02_a", "text": "问清楚是什么地方，再做决定",
                     "description": "谨慎从来不是懦弱，是一种习惯。",
                     "satisfaction_type": "延迟爽",
                     "visible_cost": "多花时间",
                     "visible_reward": "知己知彼",
                     "risk_hint": "无",
                     "process_label": "问清再动",
                     "stat_effects": [{"stat": "谋略", "delta": 5}],
                     "relationship_effects": [{"character_id": "char_shi_po", "dimension": "信任", "delta": 5}],
                     "result_nodes": [{"text": {"id": f"{ch(2)}_r02_a", "content": "石婆婆笑了：'问得好。这才是活得长的样子。明天，我带你去。'"}}],
                     "is_premium": False},
                    {"id": f"{ch(2)}_c02_b", "text": "直接说：'走吧。'",
                     "description": "等了三年，这句话你已经等够了。",
                     "satisfaction_type": "直接爽",
                     "visible_cost": "没有充分准备",
                     "visible_reward": "展示决心",
                     "risk_hint": "无",
                     "process_label": "即刻出发",
                     "stat_effects": [{"stat": "天命值", "delta": 5}],
                     "relationship_effects": [{"character_id": "char_shi_po", "dimension": "好感", "delta": 10}],
                     "result_nodes": [{"text": {"id": f"{ch(2)}_r02_b", "content": "石婆婆愣了一秒，然后点头：'好。就这种劲儿。明天天亮前出发。'"}}],
                     "is_premium": False},
                ]
            }},
            {"text": {"id": f"{ch(2)}_09", "content": "你收拾了行囊——本来就没有多少东西。月光把你的影子拉长，投在土路上。\n\n三年了。真正的路，刚刚要开始。", "emphasis": "dramatic"}},
        ]
    },
    # ── 第3章 ──────────────────────────────────
    {
        "id": ch(3), "book_id": BOOK_ID, "number": 3, "title": "古洞传承",
        "is_paid": False,
        "next_chapter_hook": "石婆婆临别时说：'封印彻底碎裂还需三个月。这三个月，不要让任何人看见你修炼。' 她没说为什么。但你心里已经有了猜测——宋玄，当年不是唯一的主导者。",
        "nodes": [
            {"text": {"id": f"{ch(3)}_01", "content": "天青山西麓。一挂七丈高的瀑布后面，有一个洞口。", "emphasis": "dramatic"}},
            {"text": {"id": f"{ch(3)}_02", "content": "你跟着石婆婆翻过西麓，穿过瀑布的水雾，踏入洞内——外面震耳的水声在洞口处被完全隔绝。这里异乎寻常的静，像是被什么更古老的力量隔绝了外部的一切。"}},
            {"text": {"id": f"{ch(3)}_03", "content": "洞壁上，密密麻麻铭刻着古老的修炼文字，线条细如发丝，却入石三分。光源从哪里来的？看不出来，但整个洞府亮如白昼。"}},
            {"dialogue": {"id": f"{ch(3)}_04", "character_id": "char_shi_po", "content": "这是开天老祖留下的传承洞府。他三千年前飞升，临行前将毕生所悟封入这处洞天，等待有缘人。那枚戒指——是引路的钥匙，也是封印你灵根的锁。", "emotion": "郑重"}},
            {"text": {"id": f"{ch(3)}_05", "content": "封印。你僵在原地。测灵石说你死灵根——不是因为你没有灵根，而是因为灵根被人为封住了。"}},
            {"dialogue": {"id": f"{ch(3)}_06", "character_id": "char_shi_po", "content": "洪荒至尊根脉，普通测灵石测出来只有两种结果：一是测爆，二是石沉大海。那块石头没炸，所以显示'死灵根'。封印你的人，知道你有什么，所以才要封。", "emotion": "平静"}},
            {"choice": {
                "id": f"{ch(3)}_choice_01",
                "prompt": "洞壁上显现出三条传承之路的纹路——三种不同的道：",
                "choice_type": "styleChoice",
                "choices": [
                    {"id": f"{ch(3)}_c01_a", "text": "剑道·破山——以绝对力量碾碎一切",
                     "description": "力量是最直接的语言。你要让所有人清楚地看见，你有多强。",
                     "satisfaction_type": "碾压爽",
                     "visible_cost": "修炼过程极为痛苦",
                     "visible_reward": "最快的战力提升",
                     "risk_hint": "底牌暴露快",
                     "process_label": "以力破局",
                     "stat_effects": [{"stat": "战力", "delta": 30}, {"stat": "名望", "delta": 5}],
                     "relationship_effects": [{"character_id": "char_shi_po", "dimension": "信任", "delta": 10}],
                     "result_nodes": [{"text": {"id": f"{ch(3)}_r01_a", "content": "传承的力量如山崩般冲入你的意识，剑意裂开你的经脉，又重新填满。洪荒至尊根脉在剑道下颤动觉醒。"}}],
                     "is_premium": False},
                    {"id": f"{ch(3)}_c01_b", "text": "心道·观天——看穿一切的谋算者",
                     "description": "没有人能压制一个看透一切的人。",
                     "satisfaction_type": "阴谋爽",
                     "visible_cost": "战力提升较慢",
                     "visible_reward": "超强的感知与谋算",
                     "risk_hint": "近战时相对吃亏",
                     "process_label": "以智御世",
                     "stat_effects": [{"stat": "谋略", "delta": 30}, {"stat": "魅力", "delta": 10}],
                     "relationship_effects": [{"character_id": "char_shi_po", "dimension": "信任", "delta": 15}],
                     "result_nodes": [{"text": {"id": f"{ch(3)}_r01_b", "content": "心道的传承像一场无声的洗礼，你感觉脑海变得前所未有的清晰——像有一双眼睛睁开了，能看见之前看不见的东西。"}}],
                     "is_premium": False},
                    {"id": f"{ch(3)}_c01_c", "text": "同时触碰三道纹路，强行融合",
                     "description": "石婆婆说只能选一个——但谁说洪荒至尊根脉只能选一个？",
                     "satisfaction_type": "扮猪吃虎",
                     "visible_cost": "极大的身体负荷",
                     "visible_reward": "三道合一，无可限量",
                     "risk_hint": "险些身体崩溃",
                     "process_label": "三道融合",
                     "stat_effects": [{"stat": "战力", "delta": 15}, {"stat": "谋略", "delta": 15}, {"stat": "天命值", "delta": 20}],
                     "relationship_effects": [{"character_id": "char_shi_po", "dimension": "敬畏", "delta": 30}],
                     "result_nodes": [{"text": {"id": f"{ch(3)}_r01_c", "content": "石婆婆在你身后倒退了一步，一个三千岁的老人，第一次露出了真正的惊愕。然后，她缓缓露出一个笑容：'有趣。'"}}],
                     "is_premium": False},
                ]
            }},
            {"text": {"id": f"{ch(3)}_07", "content": "传承的洪流冲入脑海，古老的意志与你三年积压的渴望在这一刻撞击、融合。你感觉到了——那个'死灵根'的外壳，正在从内部被一点一点地撑裂。", "emphasis": "dramatic"}},
            {"text": {"id": f"{ch(3)}_08", "content": "不知过了多久，你睁开眼。石婆婆坐在洞口，面前多了一碗热粥。她看你的眼神，第一次有了温度。"}},
            {"choice": {
                "id": f"{ch(3)}_choice_02",
                "prompt": "石婆婆把一件事告诉了你——封印你灵根的人，不只有宋玄一个。幕后还有一个你从未听说过的名字。你——",
                "choice_type": "keyDecision",
                "choices": [
                    {"id": f"{ch(3)}_c02_a", "text": "先不追这个名字，专注修炼",
                     "description": "有些真相，需要先有实力才有资格知道。",
                     "satisfaction_type": "延迟爽",
                     "visible_cost": "暂时对幕后一无所知",
                     "visible_reward": "修炼更纯粹",
                     "risk_hint": "无",
                     "process_label": "聚焦当下",
                     "stat_effects": [{"stat": "天命值", "delta": 5}],
                     "relationship_effects": [{"character_id": "char_shi_po", "dimension": "信任", "delta": 10}],
                     "result_nodes": [{"text": {"id": f"{ch(3)}_r02_a", "content": "石婆婆点点头：'知道自己什么时候该知道什么——这比大多数天才更难得。'"}}],
                     "is_premium": False},
                    {"id": f"{ch(3)}_c02_b", "text": "追问那个名字",
                     "description": "越早知道，准备越充分。",
                     "satisfaction_type": "阴谋爽",
                     "visible_cost": "可能消化不了",
                     "visible_reward": "提前掌握信息",
                     "risk_hint": "知道后可能分心",
                     "process_label": "追根溯源",
                     "stat_effects": [{"stat": "谋略", "delta": 5}, {"stat": "黑化值", "delta": 3}],
                     "relationship_effects": [{"character_id": "char_shi_po", "dimension": "敬畏", "delta": 5}],
                     "result_nodes": [{"text": {"id": f"{ch(3)}_r02_b", "content": "石婆婆沉默了片刻。然后她说出了一个名字——三个字，却让你感觉整个洞天都沉了一沉。"}}],
                     "is_premium": True},
                ]
            }},
            {"text": {"id": f"{ch(3)}_09", "content": "天青山就在不远处。三个月后，你会回去。那时候的你，将和今天完全不同。\n\n窗外，天青山的轮廓在夜色中沉默如故。但你清楚地感觉到——它在等你。", "emphasis": "dramatic"}},
        ]
    },
]

# ─────────────────────────────────────────────
# 第4-20章骨架（完整结构，比stubs更有内容）
# ─────────────────────────────────────────────
CHAPTER_SEEDS_4_20 = [
    (4,  "洪荒根脉",   "第一次真正感受到洪荒根脉运转的力量，修为开始飞速提升",  "灵根封印碎裂了三分之一。石婆婆说：还差两个月。但今晚你突破了第一道气墙。"),
    (5,  "急速突破",   "连续三次小境界突破，速度惊人，超出石婆婆预计",           "第二道气墙在今天黎明前被强行撑开。石婆婆第一次正色：'你不是在修炼，你是在燃烧。'"),
    (6,  "出关前夕",   "三个月修炼接近尾声，感知前所未有的清晰",                 "石婆婆在临走前压低声音说：'宗门大比十天后开始，今年对外开放三十个游历散修名额。'"),
    (7,  "化名入局",   "以游历散修身份报名宗门大比，踏入天青城",                 "在城门口，你看见了陈雪的背影。她在买什么东西，没有发现你。"),
    (8,  "旧日故人",   "在城中偶遇多名旧识，他们都没认出你，却对这个陌生散修印象深刻",  "李青云的声音从茶馆二楼传来，他在和人谈论大比的预测——你的名字被当成笑话提起。"),
    (9,  "大比前夕",   "完成报名，了解大比规则和参赛者情况",                     "抽签结果出来了。你第一轮的对手——是三年前大典上，笑得最大声的那个外门弟子高远。"),
    (10, "第一场",     "对上高远，第一次在天青宗众人面前展示实力",               "高远倒在擂台上，一脸不可置信。陈雪在观众席上站了起来，双眼盯着那个陌生散修的背影。"),
    (11, "连胜",       "连续赢下几场比赛，引起各方关注",                         "李青云放下茶盏，第一次认真打量你。他对身旁的人说了什么，对方随即起身，向裁判台走去。"),
    (12, "李青云出手", "李青云主动点将，要和你当众交手",                         "比赛前夜，有人在你房间门口塞了一张纸条：'别赢得太难看，我不想输给一个无名小卒。'"),
    (13, "擂台对决",   "与李青云正式交手，第一次真正测量彼此的距离",             "李青云站在擂台上，看着你的眼睛。那一刻，他似乎感觉到了一丝不对——但还没想到是谁。"),
    (14, "身份疑云",   "宋玄开始认真怀疑这个散修的真实身份",                     "宗门调查人员开始翻查报名档案。你的化名、身份证明——每一处都经得起查，但有一样东西例外：古戒。"),
    (15, "三长老追杀", "韩铁私下派人在深夜追杀你",                               "你在山道上回头看了一眼——那三个人都倒在原地，没有爬起来。身后传来韩铁压抑的怒骂声。"),
    (16, "借力打力",   "利用追杀者相互制衡，让宗门内部矛盾更加激化",             "凌峰二长老在黑暗中出现，他站在你两步外，声音平静：'你处理得不错。但你知道你最大的漏洞是什么吗？'"),
    (17, "陈雪认出",   "陈雪终于认出了你，二人第一次真正重逢",                   "陈雪的眼睛里同时盛着三年的愧疚、三年的想念、和三年都没有说出口的那句话。"),
    (18, "凌峰的棋",   "二长老凌峰表明他一直知道你的身份，并给了你一个选择",     "凌峰说：'宋玄下周要发动的，不只是对你。' 他停顿了一下：'你要的时机，比你预想的来得更快。'"),
    (19, "暗夜密谋",   "宋玄和韩铁的密谋被陈雪意外撞破",                         "陈雪颤着手把那张纸条递给你——纸条上的字，证明了宋玄当年构陷你父亲的全部经过。"),
    (20, "第一次清算", "在宗门众人面前当众揭开身份，第一卷高潮对决",              "天青宗第一次真正意义上的震动。宋玄的脸色苍白——他意识到，棋局已经不在他的掌控之内了。"),
]

# ─────────────────────────────────────────────
# 第4-20章生成函数
# ─────────────────────────────────────────────
def make_mid_chapter(num, title, goal, hook, arc_chars):
    cid = ch(num)
    char_a = arc_chars[0] if arc_chars else "char_song_xuan"
    char_b = arc_chars[1] if len(arc_chars) > 1 else "char_li_qingyun"
    char_c = arc_chars[2] if len(arc_chars) > 2 else "char_chen_xue"

    return {
        "id": cid, "book_id": BOOK_ID, "number": num, "title": title,
        "is_paid": False,
        "next_chapter_hook": hook,
        "nodes": [
            {"text": {"id": f"{cid}_01", "content": f"第{num}章开篇。{goal}——局势正在向着意想不到的方向演化。", "emphasis": "dramatic"}},
            {"text": {"id": f"{cid}_02", "content": "你感受着掌心古戒的温度，迅速判断当前形势。每一步都走在刀刃上，但你已经习惯了这种感觉。"}},
            {"text": {"id": f"{cid}_03", "content": "周围的气氛微妙地变化着。你清楚地感知到——现在是关键节点，接下来的决定将影响整个局势走向。"}},
            {"dialogue": {"id": f"{cid}_04", "character_id": char_a, "content": "你以为这就够了？这才只是开始。", "emotion": "深沉"}},
            {"choice": {
                "id": f"{cid}_choice_01",
                "prompt": f"面对「{goal}」的局面，你如何应对？",
                "choice_type": "keyDecision",
                "choices": [
                    {"id": f"{cid}_c01_a", "text": "正面强硬",
                     "description": "以绝对实力和气势压制，不给对方任何回旋余地。",
                     "satisfaction_type": "直接爽",
                     "visible_cost": "消耗较多资源\", \"visible_reward\": \"快速解决，建立威势",
                     "risk_hint": "暴露更多实力\", \"process_label\": \"正面压制",
                     "stat_effects": [{"stat": "战力", "delta": 5}, {"stat": "名望", "delta": 8}],
                     "relationship_effects": [{"character_id": char_a, "dimension": "敬畏", "delta": 15}],
                     "result_nodes": [{"text": {"id": f"{cid}_r01_a", "content": "你没有退缩，直接出手。局势在你的掌控之中快速收束。对方意识到——他们低估了你。"}}],
                     "is_premium": False},
                    {"id": f"{cid}_c01_b", "text": "隐忍布局，暗中落子",
                     "description": "按捺住冲动，在暗处布置后手，等待最佳时机。",
                     "satisfaction_type": "延迟爽",
                     "visible_cost": "短期示弱\", \"visible_reward\": \"后续爆发事半功倍",
                     "risk_hint": "布局若被识破则被动\", \"process_label\": \"暗中谋划",
                     "stat_effects": [{"stat": "谋略", "delta": 10}, {"stat": "天命值", "delta": 5}],
                     "relationship_effects": [{"character_id": char_b, "dimension": "敬畏", "delta": 8}],
                     "result_nodes": [{"text": {"id": f"{cid}_r01_b", "content": "你选择等待。眼神里有一种沉静的锋芒——那种知道自己在做什么的从容，比任何强硬都更令人忌惮。"}}],
                     "is_premium": False},
                    {"id": f"{cid}_c01_c", "text": "借助外力，以弱胜强",
                     "description": "利用现有资源和人际关系，最小代价化解当前困局。",
                     "satisfaction_type": "阴谋爽",
                     "visible_cost": "需要欠下人情\", \"visible_reward\": \"以弱胜强，出人意料",
                     "risk_hint": "所借之力未必可靠\", \"process_label\": \"借势化局",
                     "stat_effects": [{"stat": "魅力", "delta": 5}, {"stat": "谋略", "delta": 8}],
                     "relationship_effects": [{"character_id": char_c, "dimension": "信任", "delta": 10}],
                     "result_nodes": [{"text": {"id": f"{cid}_r01_c", "content": "你把局势主动权悄悄转移了出去。旁观者还没明白发生了什么，胜负已经悄悄定下。"}}],
                     "is_premium": True},
                ]
            }},
            {"dialogue": {"id": f"{cid}_05", "character_id": char_b, "content": "你比我预想的难对付得多。", "emotion": "复杂"}},
            {"text": {"id": f"{cid}_06", "content": "局势在悄然推进，每一步都在你的计算之中。但你知道——真正的考验，才刚刚开始。"}},
            {"choice": {
                "id": f"{cid}_choice_02",
                "prompt": "这个人想要与你进行更深入的交涉。你如何回应？",
                "choice_type": "characterPref",
                "choices": [
                    {"id": f"{cid}_c02_a", "text": "坦诚表态，建立信任",
                     "description": "适当暴露你的真实想法，换取对方的配合。",
                     "satisfaction_type": "情感爽",
                     "visible_cost": "暴露部分底牌\", \"visible_reward\": \"建立深层信任",
                     "risk_hint": "可能被利用\", \"process_label\": \"坦诚交流",
                     "stat_effects": [{"stat": "魅力", "delta": 5}],
                     "relationship_effects": [{"character_id": char_b, "dimension": "信任", "delta": 12}],
                     "result_nodes": [{"text": {"id": f"{cid}_r02_a", "content": "你说出了心里的一部分真话。对方的眼神发生了微妙的变化——戒备减少，更多了一层真实的尊重。"}}],
                     "is_premium": False},
                    {"id": f"{cid}_c02_b", "text": "沉默观察，以静制动",
                     "description": "用沉默让对方在等待中暴露更多信息。",
                     "satisfaction_type": "扮猪吃虎",
                     "visible_cost": "显得神秘难以接近\", \"visible_reward\": \"掌握信息优势",
                     "risk_hint": "关系可能冷却\", \"process_label\": \"以静制动",
                     "stat_effects": [{"stat": "谋略", "delta": 5}],
                     "relationship_effects": [{"character_id": char_b, "dimension": "敬畏", "delta": 8}],
                     "result_nodes": [{"text": {"id": f"{cid}_r02_b", "content": "你没有说话，只是看着对方。漫长的沉默里，他不由自主地把原本打算保留的信息也说了出来。"}}],
                     "is_premium": False},
                ]
            }},
            {"text": {"id": f"{cid}_07", "content": f"{title}——这一章的局势，向着你希望的方向偏转了一点。只是一点，但积少成多。", "emphasis": "dramatic"}},
        ]
    }

# ─────────────────────────────────────────────
# 21-1000章骨架生成
# ─────────────────────────────────────────────
def find_arc_and_beat(num):
    for arc in ARC_DEFS:
        if arc["start"] <= num <= arc["end"]:
            for beat in arc["beats"]:
                if beat[0] <= num <= beat[1]:
                    return arc, beat
            return arc, arc["beats"][0]
    return ARC_DEFS[-1], ARC_DEFS[-1]["beats"][-1]

def beat_chapter_title(arc, beat, num):
    pos = num - beat[0]
    total = beat[1] - beat[0] + 1
    phase = pos / max(total - 1, 1)
    realm = arc["realm"]
    if phase < 0.2:
        return f"{beat[2]}·起"
    elif phase < 0.5:
        return f"{beat[2]}·承"
    elif phase < 0.8:
        return f"{beat[2]}·转"
    else:
        return f"{beat[2]}·合"

def make_stub_chapter(num):
    cid = ch(num)
    arc, beat = find_arc_and_beat(num)
    chars = arc["chars"]
    char_a = chars[0]
    char_b = chars[1] if len(chars) > 1 else chars[0]
    title = beat_chapter_title(arc, beat, num)
    beat_theme = beat[2]
    beat_desc = beat[3]
    realm = arc["realm"]
    is_paid = num > 20

    return {
        "id": cid, "book_id": BOOK_ID, "number": num,
        "title": title, "is_paid": is_paid,
        "next_chapter_hook": f"第{num}章末——{beat_desc}，局势进入新的阶段，下一步的关键已经清晰。",
        "nodes": [
            {"text": {"id": f"{cid}_01", "content": f"【{arc['title']}·{beat_theme}】{realm}修炼期间，{beat_desc}的进程继续推进。", "emphasis": "dramatic"}},
            {"text": {"id": f"{cid}_02", "content": f"你清楚地感受到自己的变化——每一次历练，都在{realm}的道路上留下更深的印记。局势比昨天更加清晰，也比昨天更加危险。"}},
            {"text": {"id": f"{cid}_03", "content": "关键时刻来临。接下来的决定，将直接影响这条路能走多远。"}},
            {"choice": {
                "id": f"{cid}_c1",
                "prompt": f"面对{beat_theme}的局面，你如何抉择？",
                "choice_type": "keyDecision",
                "choices": [
                    {"id": f"{cid}_c1a", "text": "以力破局",
                     "description": "用实力说话，直接压制所有阻碍。",
                     "satisfaction_type": "直接爽",
                     "visible_cost": "消耗资源\", \"visible_reward\": \"快速建立优势",
                     "risk_hint": "暴露底牌\", \"process_label\": \"正面强攻",
                     "stat_effects": [{"stat": "战力", "delta": 5}, {"stat": "名望", "delta": 8}],
                     "relationship_effects": [{"character_id": char_a, "dimension": "敬畏", "delta": 10}],
                     "result_nodes": [{"text": {"id": f"{cid}_r1a", "content": "你选择了最直接的方式。结果立竿见影，局势向你倾斜。"}}],
                     "is_premium": False},
                    {"id": f"{cid}_c1b", "text": "隐忍蛰伏",
                     "description": "暂时退让，在暗处积蓄力量等待时机。",
                     "satisfaction_type": "延迟爽",
                     "visible_cost": "短期处于下风\", \"visible_reward\": \"积蓄更大的爆发",
                     "risk_hint": "时机可能错过\", \"process_label\": \"伺机而动",
                     "stat_effects": [{"stat": "谋略", "delta": 8}, {"stat": "天命值", "delta": 5}],
                     "relationship_effects": [{"character_id": char_b, "dimension": "信任", "delta": 8}],
                     "result_nodes": [{"text": {"id": f"{cid}_r1b", "content": "你选择了等待。这种沉静的克制，比冲动更令人敬畏。"}}],
                     "is_premium": False},
                    {"id": f"{cid}_c1c", "text": "借势化局",
                     "description": "利用外部矛盾，四两拨千斤。",
                     "satisfaction_type": "阴谋爽",
                     "visible_cost": "需要周旋\", \"visible_reward\": \"最小代价最大收益",
                     "risk_hint": "变数较多\", \"process_label\": \"借力打力",
                     "stat_effects": [{"stat": "谋略", "delta": 10}, {"stat": "魅力", "delta": 5}],
                     "relationship_effects": [],
                     "result_nodes": [{"text": {"id": f"{cid}_r1c", "content": "你把局势悄然转移，旁观者还没明白发生了什么，胜负已经落定。"}}],
                     "is_premium": True},
                ]
            }},
            {"dialogue": {"id": f"{cid}_04", "character_id": char_a, "content": "你的成长速度超出了所有人的预判。", "emotion": "复杂"}},
            {"choice": {
                "id": f"{cid}_c2",
                "prompt": "面对这个人的表态，你如何回应？",
                "choice_type": "characterPref",
                "choices": [
                    {"id": f"{cid}_c2a", "text": "直接表明立场",
                     "description": "让对方清楚地知道你站在哪里。",
                     "satisfaction_type": "直接爽",
                     "visible_cost": "暴露立场\", \"visible_reward\": \"建立清晰的同盟关系",
                     "risk_hint": "树立明确敌人\", \"process_label\": \"明确表态",
                     "stat_effects": [{"stat": "名望", "delta": 5}],
                     "relationship_effects": [{"character_id": char_a, "dimension": "信任", "delta": 10}],
                     "result_nodes": [{"text": {"id": f"{cid}_r2a", "content": "你说出了你的立场。清晰、坚定，没有任何余地。对方沉默了片刻，然后点头。"}}],
                     "is_premium": False},
                    {"id": f"{cid}_c2b", "text": "保持模糊，留有余地",
                     "description": "此刻还不是表明一切的时候。",
                     "satisfaction_type": "延迟爽",
                     "visible_cost": "显得不够坦诚\", \"visible_reward\": \"保留更多可能性",
                     "risk_hint": "对方可能产生误判\", \"process_label\": \"保留空间",
                     "stat_effects": [{"stat": "谋略", "delta": 5}],
                     "relationship_effects": [{"character_id": char_b, "dimension": "依赖", "delta": 8}],
                     "result_nodes": [{"text": {"id": f"{cid}_r2b", "content": "你没有正面回答，只是给了一个模糊的态度。这种模糊，让对方心里悬着的石头始终落不下来。"}}],
                     "is_premium": False},
                ]
            }},
            {"text": {"id": f"{cid}_05", "content": f"这一章的局势，已经悄然推进。在{realm}的修炼之路上，每一步都比上一步走得更深。", "emphasis": "dramatic"}},
        ]
    }

# ─────────────────────────────────────────────
# 攻略图生成
# ─────────────────────────────────────────────
def make_walkthrough():
    stages = []
    chapter_guides = []

    for arc in ARC_DEFS:
        stage = {
            "id": arc["id"],
            "title": arc["title"],
            "summary": arc["beats"][len(arc["beats"])//2][2],
            "chapter_ids": [ch(n) for n in range(arc["start"], arc["end"]+1)]
        }
        stages.append(stage)

        for num in range(arc["start"], arc["end"]+1):
            _, beat = find_arc_and_beat(num)
            title = beat_chapter_title(arc, beat, num) if num > 20 else ["逐出师门","漂泊三年","古洞传承","洪荒根脉","急速突破","出关前夕","化名入局","旧日故人","大比前夕","第一场","连胜","李青云出手","擂台对决","身份疑云","三长老追杀","借力打力","陈雪认出","凌峰的棋","暗夜密谋","第一次清算"][num-1]

            guide = {
                "chapter_id": ch(num),
                "stage_id": arc["id"],
                "public_summary": f"第{num}章：{beat[2]}阶段，局势在你的推动下继续演进。",
                "objective": beat[3],
                "estimated_minutes": 5,
                "interaction_count": 2,
                "visible_routes": [
                    {"id": f"route_{num}_a", "title": "正面强攻", "style": "直接爽", "unlock_hint": "偏战力", "payoff": "快速建立优势", "process_focus": "战力压制"},
                    {"id": f"route_{num}_b", "title": "隐忍布局", "style": "延迟爽", "unlock_hint": "偏谋略", "payoff": "积蓄更大爆发", "process_focus": "暗中谋划"},
                ],
                "hidden_route_hint": "某些选择会触发隐藏的剧情线索。"
            }
            chapter_guides.append(guide)

    return {
        "book_id": BOOK_ID,
        "title": "弃徒逆天·命运图谱",
        "stages": stages,
        "chapter_guides": chapter_guides,
    }

# ─────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────
def main():
    print("生成弃徒逆天 (xianxia_001) 1000章节...")

    chapters = []

    # 前3章：完整散文
    chapters.extend(FULL_CHAPTERS)
    print(f"  章节 1-3：完整散文 ✓")

    # 第4-20章：增强骨架
    arc_chars_early = ARC_DEFS[0]["chars"]
    for num, title, goal, hook in CHAPTER_SEEDS_4_20:
        chapters.append(make_mid_chapter(num, title, goal, hook, arc_chars_early))
    print(f"  章节 4-20：增强骨架 ✓")

    # 第21-1000章：标准骨架
    for num in range(21, 1001):
        chapters.append(make_stub_chapter(num))
        if num % 100 == 0:
            print(f"  章节 {num}/1000 ✓")

    print(f"  章节 21-1000：标准骨架 ✓")

    # 更新总章节数
    BOOK_META["total_chapters"] = len(chapters)

    # 生成攻略图
    print("生成攻略图...")
    walkthrough = make_walkthrough()

    story_package = {
        "book": BOOK_META,
        "reader_desire_map": READER_DESIRE_MAP,
        "story_bible": STORY_BIBLE,
        "route_graph": ROUTE_GRAPH,
        "walkthrough": walkthrough,
        "chapters": chapters,
    }

    out_dir = Path(__file__).parent.parent / "projects" / BOOK_ID
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "story_package.json"

    print(f"写入 {out_path} ...")
    out_path.write_text(
        json.dumps(story_package, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"\n✓ 完成！")
    print(f"  总章节数：{len(chapters)}")
    print(f"  文件大小：{size_mb:.1f} MB")
    print(f"  输出路径：{out_path}")
    print(f"\n下一步（编译进app）：")
    print(f"  python3 scripts/compile_story_package.py {out_path} \\")
    print(f"    --resources-dir LifeScript-iOS/Sources/LifeScript/Resources \\")
    print(f"    --output-dir projects/{BOOK_ID}/build")

if __name__ == "__main__":
    main()
