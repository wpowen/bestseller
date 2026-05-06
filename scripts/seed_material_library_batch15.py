"""
Batch 15: World cultural traditions for activation across diverse genres —
Korean drama tropes, Japanese anime/light novel conventions, Russian
literary tradition specifics, French literature, Latin American magical
realism deepening, Indian epic traditions, Middle Eastern Arabian Nights,
African oral tradition. All universal real_world_references.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import insert_entry, MaterialEntry


def wiki(title: str, note: str = "") -> dict:
    return {"type": "wikipedia", "title": title, "note": note}


def llm_note(note: str) -> dict:
    return {"type": "llm_synth", "note": note}


ENTRIES = [
    # ═══════════════════════════════════════════════════════════════
    # 韩剧/日漫/东亚流行文化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-kdrama-tropes",
        name="韩剧叙事范式",
        narrative_summary="韩剧情感节奏：偶遇 → 误会 → 心动 → 双向暗恋 → 第三者搅局 → 误会爆发 → 危机救赎 → 终极告白。"
                          "以『虐恋甜结』『一见倾心』『身份反差』为核心，强调情感颗粒度与镜头慢节奏。",
        content_json={
            "narrative_pillars": "宿命论 / 身份反差 / 误会三连 / 失忆 / 绝症 / 复仇 / 财阀豪门",
            "trope_examples": "灰姑娘 + 财阀公子 / 失忆失明绝症三连 / 多年后重逢 / 雨中告白 / 公主抱",
            "famous_works": "《冬日恋歌》《大长今》《来自星星的你》《鬼怪》《太阳的后裔》《W》《孤单又灿烂的神》",
            "shooting_style": "光影柔焦 / 慢镜头特写 / 抒情 OST / 雨景雪景",
            "narrative_use": "言情甜虐 / 现代偶像 / 身份反差恋爱 / 重生再爱一次",
            "activation_keywords": ["财阀", "灰姑娘", "失忆", "绝症", "雨中", "公主抱", "误会", "重逢"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("韩国电视剧", ""), llm_note("韩剧叙事范式")],
        tags=["韩剧", "言情", "流行文化"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-anime-isekai",
        name="日漫异世界（Isekai）范式",
        narrative_summary="异世界穿越是日漫核心子类：主角穿到剑魔法/游戏/历史世界，自带 buff（系统/作弊/前世记忆/金手指）。"
                          "套路三件套：开局拯救奴隶女→结识公会→打怪升级→建立后宫/集团。"
                          "影响国产穿书/快穿/系统流。",
        content_json={
            "core_archetypes": "勇者重生 / 异世界冒险者 / 召唤师 / 商人重生 / 魔王降临",
            "trope_set": "公会注册 / 等级显示 / 技能树 / 装备栏 / 状态魔法 / 后宫扩张 / NPC 互动",
            "famous_works": "《为美好的世界献上祝福》《Re:0》《关于我转生变成史莱姆这档事》《盾之勇者》《无职转生》《骨王》《刀剑神域》",
            "narrative_engine": "现实失意 → 转生异界 → 系统/前世 buff → 弱→强崛起 → 拯救世界 / 收服女角",
            "narrative_use": "穿书 / 快穿 / 系统流 / 网游小说",
            "activation_keywords": ["异世界", "公会", "等级", "技能", "勇者", "魔王", "穿书", "Isekai"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("异世界 (创作)", ""), llm_note("日漫 Isekai 范式")],
        tags=["日漫", "异世界", "穿书"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-anime-shonen-formula",
        name="少年漫画黄金公式",
        narrative_summary="少年漫画三大要素：友情 / 努力 / 胜利。主角『弱→训练→强敌→更强』循环。"
                          "影响热血竞技/系统流/玄幻升级文。代表作《海贼王》《火影》《龙珠》。",
        content_json={
            "core_values": "友情 / 努力 / 胜利（少年 Jump 三大原则）",
            "structure": "弱小起点 → 师徒训练 → 友军组建 → 中等敌人 → 升级技能 → 大反派 → 终极对决 → 新征途",
            "tropes": "热血必胜 / 觉醒变身 / 招式命名 / 友情救场 / 反派洗白 / 师傅之死",
            "famous_works": "《龙珠》《海贼王》《火影忍者》《死神》《我的英雄学院》《鬼灭之刃》",
            "narrative_use": "少年向爽文 / 系统流 / 玄幻 / 武侠 / 体育竞技",
            "activation_keywords": ["友情", "努力", "胜利", "热血", "觉醒", "招式", "升级", "对决"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("少年漫画", ""), llm_note("少年漫黄金公式")],
        tags=["日漫", "少年漫", "热血"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-anime-mecha-tradition",
        name="日本机甲动画传统",
        narrative_summary="日漫机甲三大流派：超级系（高达/勇者）/ 真实系（高达 0079/EVA）/ 异端解构（EVA/翠星）。"
                          "核心是『少年驾驶员 + 巨大兵器 + 战争反思』。机甲流必备文化坐标。",
        content_json={
            "schools": "超级系（无敌英雄）/ 真实系（战争残酷）/ 解构系（精神分析+宗教隐喻）",
            "famous_works": "《机动战士高达》《新世纪福音战士》《超时空要塞 Macross》《勇者王》《翠星之加尔冈缇亚》《银河战国群雄传》",
            "key_themes": "少年驾驶员心理 / 战争伦理 / 父子情结 / 母性与机体 / 人类升华 / 战争创伤",
            "narrative_use": "机甲流 / 末日机甲 / 星际战争 / 心理悬疑混合",
            "activation_keywords": ["机甲", "驾驶员", "EVA", "高达", "战争", "少年", "巨大机器人"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("机甲动画", ""), llm_note("日本机甲传统")],
        tags=["日漫", "机甲", "科幻"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 法国文学
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-french-novel-tradition",
        name="法国小说传统",
        narrative_summary="法国小说从巴尔扎克现实主义 → 福楼拜文体革命 → 普鲁斯特意识流 → 加缪存在主义 → 新小说派解构。"
                          "对都市、心理、哲学性叙事影响深远。",
        content_json={
            "key_movements": "现实主义（巴尔扎克）→ 自然主义（左拉）→ 象征主义（波德莱尔）→ 意识流（普鲁斯特）→ 存在主义（萨特/加缪）→ 新小说（罗伯-格里耶）",
            "famous_works": "《人间喜剧》《包法利夫人》《追忆似水年华》《局外人》《情人》（杜拉斯）《嫉妒》（罗伯-格里耶）",
            "thematic_focus": "社会观察 / 阶级流动 / 心理深度 / 时间记忆 / 存在荒诞 / 物的描写",
            "literary_devices": "自由间接引语 / 多视角叙事 / 时间错位 / 物化描写 / 内心独白",
            "narrative_use": "都市心理 / 言情深度 / 文学风格借鉴",
            "activation_keywords": ["巴尔扎克", "包法利", "普鲁斯特", "意识流", "存在主义", "新小说"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[wiki("法国文学", ""), llm_note("法国小说传统")],
        tags=["法国", "文学", "通用"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 印度
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-indian-epic-mahabharata",
        name="印度史诗《摩诃婆罗多》",
        narrative_summary="《摩诃婆罗多》是世界最长史诗之一，约 10 万颂。"
                          "讲述般度族五兄弟与堂兄弟俱卢族争夺王位的库鲁之野大战。"
                          "包含哲学经典《薄伽梵歌》（克里希纳对阿周那的教诲）。"
                          "提供宿命/达摩/正义/家族冲突的原型。",
        content_json={
            "core_conflict": "般度五子（坚战/怖军/阿周那/无种/偕天）vs 难敌为首的俱卢百子",
            "key_figures": "克里希纳（化身神）/ 毗湿摩（不死战神）/ 德罗那（武器之师）/ 迦尔纳（悲剧英雄）",
            "philosophical_core": "《薄伽梵歌》18 章 — 谈达摩、行动、解脱",
            "themes": "达摩 dharma 责任 / 业力 karma / 战争义不义 / 家族悲剧 / 宿命与自由",
            "narrative_use": "玄幻神话 / 史诗规模战争 / 兄弟反目 / 宿命对决",
            "activation_keywords": ["摩诃婆罗多", "克里希纳", "薄伽梵歌", "达摩", "业力", "般度", "俱卢"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("摩诃婆罗多", ""), wiki("薄伽梵歌", ""), llm_note("印度史诗通识")],
        tags=["印度", "史诗", "神话"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-indian-epic-ramayana",
        name="印度史诗《罗摩衍那》",
        narrative_summary="《罗摩衍那》讲述毗湿奴化身罗摩王子营救被罗刹王罗波那掳走的妻子悉多的征程。"
                          "为东南亚文化（泰国/印尼/柬埔寨）共同神话原型。"
                          "提供英雄救美/十首恶魔王/猴神助战的经典叙事。",
        content_json={
            "plot_arc": "罗摩流放 → 悉多被劫 → 联合猴王哈奴曼 → 跨海远征 → 大战罗波那 → 凯旋归国",
            "key_figures": "罗摩（毗湿奴第七化身）/ 悉多（地母女儿）/ 哈奴曼（神猴）/ 罗波那（十首魔王）/ 拉克什曼那（弟弟）",
            "themes": "理想君王 / 忠贞夫妻 / 兄弟情义 / 善恶大战 / 神性与人性",
            "cultural_spread": "影响泰国《拉玛坚》、印尼《哇扬》、柬埔寨壁画、东南亚舞剧",
            "narrative_use": "玄幻救美 / 跨界远征 / 猴形伙伴（参考西游）/ 善恶对决",
            "activation_keywords": ["罗摩", "悉多", "哈奴曼", "罗波那", "毗湿奴", "罗刹", "猴神"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("罗摩衍那", ""), llm_note("印度史诗通识")],
        tags=["印度", "史诗", "神话"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 阿拉伯/中东
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-arabian-nights",
        name="《一千零一夜》叙事框架",
        narrative_summary="《一千零一夜》是阿拉伯故事集大成，框架故事内嵌套大量奇幻短篇。"
                          "山鲁佐德每夜讲故事拖延砍头。"
                          "影响后世奇幻、嵌套叙事、东方主义想象。提供精灵/飞毯/宝藏/智者/魔法灯等元素。",
        content_json={
            "frame_story": "国王每夜娶女次日杀掉 → 山鲁佐德通过悬念故事拖到第 1001 夜终被原谅",
            "famous_inner_tales": "《阿拉丁》《阿里巴巴四十大盗》《辛巴达航海》《渔夫与魔鬼》",
            "magical_elements": "精灵 Jinn / 飞毯 / 神灯 / 隐身斗篷 / 宝藏地图 / 魔法变形",
            "narrative_devices": "嵌套叙事 / 悬念延续 / 框架故事 / 万一夜结构",
            "narrative_use": "玄幻奇幻借用 / 嵌套故事 / 古风冒险 / 神灯三愿模式",
            "activation_keywords": ["一千零一夜", "山鲁佐德", "阿拉丁", "神灯", "精灵 Jinn", "飞毯", "嵌套故事"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("一千零一夜", ""), llm_note("阿拉伯故事集通识")],
        tags=["阿拉伯", "中东", "奇幻"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-persian-shahnameh",
        name="波斯史诗《列王纪》",
        narrative_summary="《列王纪》（菲尔多西作）是波斯民族史诗，讲述自创世到阿拉伯征服波斯的 50 位君王故事。"
                          "包含『七关历险』『罗斯坦传奇』等经典章节。"
                          "提供波斯古典英雄主义、父子相残（罗斯坦杀子）的悲剧原型。",
        content_json={
            "structure": "神话期 → 半神英雄期（罗斯坦时代）→ 历史期（萨珊王朝）",
            "famous_heroes": "罗斯坦（最强英雄）/ 索赫拉布（被父亲误杀的儿子）/ 萨亚乌什（被诬陷王子）/ 凯库斯洛 /菲利顿",
            "themes": "光暗大战 / 王权天命 / 父子悲剧 / 民族抗争 / 英雄宿命",
            "cultural_role": "波斯文化民族认同核心 + 伊朗/阿富汗/塔吉克共同遗产",
            "narrative_use": "玄幻史诗规模 / 父子悲剧（罗斯坦杀索赫拉布）/ 七关考验原型",
            "activation_keywords": ["列王纪", "罗斯坦", "索赫拉布", "波斯", "菲尔多西", "七关"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("列王纪", ""), llm_note("波斯史诗通识")],
        tags=["波斯", "中东", "史诗"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 拉美
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-latam-magic-realism-deep",
        name="拉美魔幻现实主义深化",
        narrative_summary="拉美魔幻现实主义将奇幻视为日常一部分，融合印第安神话、殖民历史、独裁政治。"
                          "代表 García Márquez《百年孤独》、Borges《虚构集》、Cortázar《跳房子》。"
                          "提供『荒诞作为现实底色』『时间循环』『家族百年史』的原型。",
        content_json={
            "core_principle": "奇幻不是异常而是日常一部分；叙述者从不解释",
            "famous_authors": "García Márquez 马尔克斯 / Borges 博尔赫斯 / Cortázar 科塔萨尔 / Vargas Llosa 略萨 / Allende 阿连德",
            "key_works": "《百年孤独》《佩德罗·巴拉莫》《虚构集》《阿莱夫》《跳房子》《幽灵之家》",
            "common_motifs": "黄蝴蝶 / 失眠瘟疫 / 死人讲话 / 雨季 / 无尽乡村 / 镜子迷宫 / 图书馆",
            "narrative_use": "灵异 / 重生回溯 / 家族史 / 时间循环 / 鬼魂在场",
            "activation_keywords": ["魔幻现实", "百年孤独", "马尔克斯", "马孔多", "博尔赫斯", "时间迷宫", "黄蝴蝶"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("魔幻现实主义", ""), wiki("百年孤独", ""), llm_note("拉美文学通识")],
        tags=["拉美", "魔幻现实", "文学"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 非洲
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-african-oral-tradition",
        name="非洲口述传统",
        narrative_summary="非洲口述传统由 griot（吟游诗人）传承部族史诗。"
                          "代表《松迪亚塔史诗》（马里帝国创立）、约鲁巴神话、阿肯传说。"
                          "强调集体记忆、动物寓言（蜘蛛阿南西）、祖灵观。",
        content_json={
            "key_traditions": "griot 西非吟游诗人 / 约鲁巴 Orisha 神话 / 阿肯民俗 / 班图班图说唱 / 班图克利亚祈祷",
            "famous_epics": "《松迪亚塔》（马里帝国 13 世纪）/《姆温多》（刚果）",
            "common_archetypes": "蜘蛛阿南西（智慧诈术）/ 兔子（狡猾智者）/ 鬣狗（贪婪）/ 狮子（王权）",
            "themes": "祖灵在场 / 集体责任 / 智慧 vs 蛮力 / 部族认同 / 抗殖民",
            "narrative_use": "玄幻神话拓展 / 智慧动物伙伴 / 殖民抗争 / 祖灵附体",
            "activation_keywords": ["griot", "松迪亚塔", "阿南西", "约鲁巴", "祖灵", "口述史诗"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("非洲文学", ""), wiki("松迪亚塔史诗", ""), llm_note("非洲口述通识")],
        tags=["非洲", "口述", "神话"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 凯尔特/北欧/斯拉夫深化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-celtic-mythology-deep",
        name="凯尔特神话深化",
        narrative_summary="凯尔特神话四大循环：神族（图哈达南）/ 厄尔斯特（库胡林）/ 芬尼亚（芬恩马克尔）/ 国王（亚瑟）。"
                          "核心元素：仙境 / 魔法武器 / 三相神 / 半神英雄。"
                          "影响《指环王》《纳尼亚》及现代奇幻。",
        content_json={
            "four_cycles": "神话循环（图哈达南神族）/ 厄尔斯特（库胡林）/ 芬尼亚（芬恩）/ 国王（亚瑟王）",
            "key_figures": "卢格（光神）/ 摩瑞甘（战争三相女神）/ 库胡林（厄尔斯特无敌战士）/ 梅林（巫师）/ 亚瑟王",
            "magical_objects": "斩铁剑 Caladbolg / 复活釜 / 命运石 / 王者之剑 Excalibur / 圣杯",
            "common_motifs": "三相女神 / 仙境 Tír na nÓg / 鹿与雄鹿 / 德鲁伊 / 时间错位",
            "narrative_use": "西式奇幻 / 亚瑟王传说重构 / 仙境穿越 / 半神英雄",
            "activation_keywords": ["库胡林", "亚瑟王", "梅林", "圣杯", "Excalibur", "德鲁伊", "仙境"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("凯尔特神话", ""), wiki("亚瑟王传说", ""), llm_note("凯尔特神话深化")],
        tags=["凯尔特", "神话", "奇幻"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-slavic-folklore",
        name="斯拉夫民间传说",
        narrative_summary="斯拉夫民间传说包括俄罗斯/波兰/巴尔干传统：芭芭雅嘎（鸡腿屋老巫婆）、卡谢伊（不死之徒）、罗萨卡（水仙）、伊万王子。"
                          "以严寒森林、女巫、变形为特征。提供与北欧/凯尔特不同的奇幻原型。",
        content_json={
            "key_figures": "芭芭雅嘎（鸡腿屋女巫）/ 卡谢伊（不死巫师）/ 龙蛇 Zmey / 罗萨卡（水仙）/ 伊万王子（傻子英雄）/ 火鸟",
            "common_settings": "深林 / 木屋 / 严冬 / 沼泽 / 三十王国",
            "trope_set": "三兄弟历险 / 寻找火鸟 / 智斗女巫 / 亡魂复仇 / 变形（青蛙公主/天鹅）",
            "famous_works": "《青蛙公主》《伊万王子与灰狼》《火鸟》（影响斯特拉文斯基芭蕾舞）",
            "narrative_use": "异国奇幻 / 暗黑童话 / 严寒求生 / 变形咒语",
            "activation_keywords": ["芭芭雅嘎", "卡谢伊", "伊万王子", "火鸟", "罗萨卡", "鸡腿屋"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("斯拉夫神话", ""), llm_note("斯拉夫民间传说")],
        tags=["斯拉夫", "民间", "奇幻"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 日本古典
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-japan-genji-tradition",
        name="《源氏物语》与平安美学",
        narrative_summary="《源氏物语》（紫式部）是世界最早长篇小说，描绘平安朝光源氏的恋情与政治沉浮。"
                          "确立『物哀』『幽玄』『风雅』三大日本古典美学。"
                          "影响后世言情/宫廷题材，提供季节意象/和歌赠答/物哀结构。",
        content_json={
            "structure": "第一部光源氏崛起 → 第二部权力顶峰 → 第三部宇治十帖（光源氏死后）",
            "core_aesthetics": "物哀（瞬息之美悲叹）/ 幽玄（深远之美）/ 风雅（雅致情趣）",
            "key_motifs": "季节移转 / 和歌赠答 / 香道 / 衣纹色彩 / 月与樱花",
            "narrative_use": "古风言情 / 宫廷情史 / 时光流转感 / 季节美学",
            "activation_keywords": ["源氏物语", "紫式部", "平安", "物哀", "幽玄", "和歌", "光源氏"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("源氏物语", ""), llm_note("平安美学")],
        tags=["日本", "古典", "美学"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-japan-yokai-tradition",
        name="日本妖怪传统",
        narrative_summary="日本妖怪文化历经平安《今昔物语》→ 江户《画图百鬼夜行》→ 现代《鬼太郎》《阴阳师》。"
                          "妖怪分『付丧神』（器物百年成精）/『鬼』（怨灵恶鬼）/『神兽』（白虎玄武）/『野怪』（河童狐狸）。"
                          "灵异 / 玄幻 / 都市怪谈题材必备。",
        content_json={
            "categories": "付丧神（器物精灵）/ 鬼（恶灵）/ 神兽 / 野怪（狐狸 河童 天狗）/ 怨灵 / 物之怪",
            "famous_yokai": "天狗 / 河童 / 雪女 / 狐妖 / 茨木童子 / 玉藻前 / 酒吞童子 / 化猫",
            "lore_sources": "《今昔物语》《画图百鬼夜行》（鸟山石燕）《雨月物语》《耳袋》",
            "modern_inheritors": "水木茂《鬼太郎》/ 梦枕貘《阴阳师》/《妖怪手表》/《夏目友人帐》",
            "narrative_use": "灵异都市怪谈 / 阴阳师题材 / 古风志怪 / 萌宠妖怪",
            "activation_keywords": ["天狗", "河童", "狐妖", "付丧神", "百鬼夜行", "怨灵", "阴阳师"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("妖怪", ""), wiki("百鬼夜行", ""), llm_note("日本妖怪通识")],
        tags=["日本", "妖怪", "灵异"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 美国流行文化
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-american-superhero",
        name="美式超级英雄范式",
        narrative_summary="美式超英三大传统：DC（神性英雄）/ Marvel（人性英雄）/ Image（暴力解构）。"
                          "起源故事 → 失去导师 → 选择身份 → 反派对决 → 自我和解，神性 vs 人性张力是核心。",
        content_json={
            "DC_vs_Marvel": "DC：神下凡（超人/神奇女侠）；Marvel：凡人变神（蜘蛛侠/钢铁侠）",
            "origin_template": "平凡 → 意外赋能 → 失去导师/亲人 → 责任觉醒 → 试错 → 担当英雄",
            "core_archetypes": "侦探（蝙蝠侠）/ 神祇（超人）/ 怪物（绿巨人）/ 工程师（钢铁侠）/ 边缘人（金刚狼）",
            "themes": "能力即责任 / 双重身份 / 神性人性张力 / 复仇 vs 正义 / 团队 vs 单干",
            "narrative_use": "重生超能 / 都市异能 / 系统流救世 / 反派洗白",
            "activation_keywords": ["超能力", "起源故事", "双重身份", "复仇者", "正义联盟", "异变"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("超级英雄", ""), llm_note("美式超英范式")],
        tags=["美国", "超英", "流行文化"],
    ),
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-american-western",
        name="美国西部片传统",
        narrative_summary="西部片以 19 世纪美国西部边疆为背景，塑造孤胆牛仔、亡命徒、印第安人、骑警、黄金小镇。"
                          "经典三幕：陌生人入镇 → 矛盾激化 → 镇外决斗。"
                          "影响《荒野大镖客》游戏、太空歌剧、星战。",
        content_json={
            "settings": "1860-1890 美国西部 / 沙漠 / 草原 / 荒废小镇 / 火车站 / 牧场",
            "common_archetypes": "孤胆牛仔 / 亡命徒 / 镇上警长 / 牧场主 / 印第安酋长 / 寡妇老板娘 / 老投手",
            "key_motifs": "马 / 左轮手枪 / 沙尘 / 决斗（高午时分）/ 火车 / 印第安战 / 淘金热",
            "narrative_arc": "陌生人入镇 → 与镇上势力冲突 → 帮助弱者 → 大决斗 → 离开",
            "famous_works": "塞吉欧·莱昂内《镖客三部曲》/ 约翰·福特《关山飞渡》/《不可饶恕》/《荒野大镖客救赎》（游戏）",
            "narrative_use": "末日西部融合 / 异世界荒野 / 太空西部（星际牛仔）",
            "activation_keywords": ["牛仔", "西部", "决斗", "亡命徒", "警长", "高午", "拓荒"],
        },
        source_type="llm_synth", confidence=0.81,
        source_citations=[wiki("西部片", ""), llm_note("美国西部传统")],
        tags=["美国", "西部", "电影"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 韩国
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-korean-han-emotion",
        name="韩国『恨』情感美学",
        narrative_summary="『한 / Han』是韩国独有情感概念：长期压抑的怨叹、悲愤、不甘交织成深沉哀伤。"
                          "源自殖民/战争/分裂的集体创伤，在韩剧/韩影/韩文学中常见。"
                          "提供与中文『悲愤』不完全相同的情感颗粒度。",
        content_json={
            "core_meaning": "积压长久的悲怨 + 不甘 + 期盼 + 哀伤的复合情感",
            "historical_roots": "日本殖民期 / 朝鲜战争 / 南北分裂 / 军事独裁 / 阶级压迫",
            "expressions": "盘索里（哭腔说唱）/ 巫俗仪式 / 韩影长镜头静默",
            "famous_works": "李沧东《诗》《密阳》/ 朴赞郁复仇三部曲 / 韩江《素食者》",
            "narrative_use": "韩剧化言情 / 重生雪耻 / 历史悲情 / 复仇主线",
            "activation_keywords": ["恨", "Han", "怨叹", "悲愤", "不甘", "压抑", "哀伤"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("恨 (韩国)", ""), llm_note("韩国情感美学")],
        tags=["韩国", "情感", "美学"],
    ),

    # ═══════════════════════════════════════════════════════════════
    # 东南亚
    # ═══════════════════════════════════════════════════════════════
    MaterialEntry(
        dimension="real_world_references", genre=None,
        slug="rw-sea-spirits-pontianak",
        name="东南亚妖怪传统",
        narrative_summary="东南亚（泰国/马来/印尼/越南）妖怪传统融合佛印 + 本土万物有灵。"
                          "代表：泰国蛇神娜迦 / 马来吸血女鬼 Pontianak / 印尼尸鬼 / 越南幽灵。"
                          "提供与中日传统不同的灵异底色。",
        content_json={
            "key_creatures": "Naga 娜迦（蛇神）/ Pontianak 蓬莎娜（产难女鬼）/ Kuntilanak / Krasue（飘头鬼）/ Nang Tani（香蕉树鬼）",
            "common_motifs": "树灵 / 产难女鬼 / 蛇神河神 / 黑魔法 / 巫师 dukun",
            "regional_variations": "泰：佛教 + 蛇神河神 / 马印：伊斯兰 + 万物有灵 / 越：道教 + 祖灵",
            "narrative_use": "异域灵异 / 东南亚旅行恐怖 / 海岛怪谈 / 跨文化灵修",
            "activation_keywords": ["娜迦", "Pontianak", "Krasue", "树灵", "巫师", "黑魔法", "蛇神"],
        },
        source_type="llm_synth", confidence=0.79,
        source_citations=[wiki("东南亚神话", ""), llm_note("东南亚灵异传统")],
        tags=["东南亚", "妖怪", "灵异"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...\n")
    by_genre = {}
    by_dim = {}
    inserted = 0
    errors = 0
    async with session_scope() as session:
        for e in ENTRIES:
            try:
                await insert_entry(session, e, compute_embedding=True)
                inserted += 1
                by_genre[e.genre or "(通用)"] = by_genre.get(e.genre or "(通用)", 0) + 1
                by_dim[e.dimension] = by_dim.get(e.dimension, 0) + 1
            except Exception as exc:
                errors += 1
                print(f"ERROR {e.slug}: {exc}")
        await session.commit()
    print(f"By genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n\u2713 {inserted} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
