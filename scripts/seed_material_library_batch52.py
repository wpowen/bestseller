"""
Batch 52: factions + character_templates for thin genres (洪荒/女尊/萌宠/快穿/游戏/美食/末世).
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
    # ═══════════ FACTIONS ═══════════
    MaterialEntry(
        dimension="factions", genre="洪荒",
        slug="honghuang-fact-three-religions",
        name="洪荒：三教格局（阐/截/人）",
        narrative_summary="洪荒流核心政治格局：人教（老子）、阐教（元始）、截教（通天）三派分庭抗礼；"
                          "封神之前曾结盟《诛仙剑阵》/封神后元气大伤；妖族（女娲）/西方教（接引/准提）为外延。",
        content_json={
            "human_religion_renjiao": {
                "祖师": "老子（太上老君）",
                "理念": "清静无为/不收人为徒/玄都大法师为唯一弟子",
                "核心人物": "玄都大法师",
                "态度": "中立/看戏",
                "标志道场": "玄都洞八景宫",
            },
            "explain_chanjiao": {
                "祖师": "元始天尊",
                "理念": "讲究根骨悟性/收徒严苛/教导『仙骨』",
                "十二金仙": "广成子/赤精子/玉鼎真人/太乙真人/普贤/文殊/慈航/燃灯（叛师）/黄龙/惧留孙/灵宝大法师/道行天尊",
                "态度": "封神主导方/与西方教联合",
                "标志道场": "昆仑山玉虚宫",
            },
            "intercept_jiejiao": {
                "祖师": "通天教主",
                "理念": "有教无类/万物皆可入门/人多势众",
                "四大弟子": "多宝道人/金灵圣母/无当圣母/龟灵圣母",
                "外门": "赵公明/三霄/十绝阵主等",
                "态度": "封神被压制/弟子损失惨重",
                "标志道场": "东海金鳌岛碧游宫",
            },
            "yaozu_remnant": {
                "祖师": "女娲娘娘",
                "理念": "护佑妖族残部/参与封神为殷商",
                "核心": "女娲/火云洞三皇/妖师鲲鹏",
            },
            "western_religion": {
                "二圣": "接引道人/准提道人",
                "理念": "西方贫瘠/掠夺东土资源/接引人才西去",
                "态度": "封神最大赢家/为佛教崛起铺垫",
            },
            "battle_axis": "封神大战 = 阐+西方 vs 截+妖",
            "activation_keywords": ["阐教", "截教", "人教", "封神", "西方教", "妖族", "三清"],
        },
        source_type="llm_synth", confidence=0.88,
        source_citations=[wiki("封神演义", "三教斗争"), llm_note("洪荒派系分析")],
        tags=["洪荒", "派系", "三教", "封神"],
    ),

    MaterialEntry(
        dimension="factions", genre="女尊",
        slug="nuzun-fact-empress-court",
        name="女尊：六部女官 + 三大世家",
        narrative_summary="女尊朝堂典型架构：六部尚书皆女子（吏礼户兵刑工）+ 三大世家（清河顾/江南苏/陇西林）；"
                          "皇室女皇/太后/皇夫/皇子构成宫廷四角关系。",
        content_json={
            "six_ministries": {
                "吏部": "女官升迁/掌人事/最高权力之一",
                "礼部": "祭祀/科举/婚嫁规矩（含男子嫁娶）",
                "户部": "田亩/赋税/盐铁",
                "兵部": "女将军任命/调兵符印",
                "刑部": "司法/审案/牢狱",
                "工部": "宫殿/水利/匠造（罕见允许男匠）",
            },
            "three_great_clans": {
                "清河顾氏": "诗书礼仪/盛产宰相/世代联姻皇室",
                "江南苏氏": "丝绸盐运/富可敌国/男丁众多以美貌出名",
                "陇西林氏": "代代将门/边境守护/女武将摇篮",
            },
            "imperial_quad": {
                "女皇": "九五之尊/可九夫",
                "太后": "皇母/退位后仍把持朝局",
                "皇夫": "正君/可干预内宫",
                "皇子": "继承制度多以长女嫡继/储位之争激烈",
            },
            "underground_powers": {
                "暗卫司": "皇室特务/由女皇心腹掌握",
                "镖局": "民间运输/常有男镖师身怀绝技",
                "教坊": "男子艺人聚集/青楼性质反转",
            },
            "activation_keywords": ["六部", "女皇", "皇夫", "三大世家", "暗卫司", "教坊"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("女尊朝堂结构")],
        tags=["女尊", "派系", "朝堂", "世家"],
    ),

    MaterialEntry(
        dimension="factions", genre="萌宠",
        slug="mengchong-fact-pet-academy",
        name="萌宠：驭兽师学院 + 黑市 + 野生兽群",
        narrative_summary="萌宠文标准三方势力：驭兽师学院（合法体制）+ 黑市（盗猎/非法贩卖）+ 野生兽群（自治领地）；"
                          "学院培养体系/黑市灰产/野生区高能稀有兽是固定地理。",
        content_json={
            "academy_system": {
                "顶级学院": "皇家驭兽学院/天阶宠物可入",
                "中阶": "州府学院/地阶宠物聚集",
                "新人入学": "测试灵根 → 分院 → 配契初阶兽",
                "毕业去向": "皇家近卫/边境驭兽军/各州猎兽队/学者派",
                "学院冲突": "天才之争/师门嫉妒/学院间联赛",
            },
            "black_market": {
                "贩卖类型": "稀有幼崽/异变兽/失主宠物/血脉解药",
                "黑市帮派": "獠牙帮/影爪堂/驯兽人协会",
                "层级": "走街贩 → 黑市拍卖会 → 跨大陆走私网",
                "对抗": "学院出身的执法者/被腐败的官员",
            },
            "wild_beast_realms": {
                "灵兽森林": "中阶宠兽自然栖息",
                "魔兽山脉": "高阶野兽 + 兽王统领",
                "禁忌之地": "神兽古战场/可能存在远古血脉",
                "幼兽溪谷": "繁殖地/不允许猎杀",
                "野生兽王": "区域统治者/智慧不输人类/可能签订平等契约",
            },
            "activation_keywords": ["驭兽学院", "黑市", "灵兽森林", "兽王", "盗猎", "幼崽"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("萌宠文势力划分")],
        tags=["萌宠", "派系", "学院", "黑市"],
    ),

    MaterialEntry(
        dimension="factions", genre="快穿",
        slug="kuaichuan-fact-system-organizations",
        name="快穿：系统总部 + 反派联盟 + 自由意识",
        narrative_summary="快穿世界观背后三大势力：系统总部（管理者方）+ 反派联盟（追求觉醒/反抗系统）+ "
                          "自由意识（独立行走宿主，已脱离体系）；这是『元世界观』层面的派系。",
        content_json={
            "system_headquarters": {
                "管理层": "高位面意识/创造系统/分配世界",
                "执行层": "系统人格化AI/直接对接宿主",
                "维护层": "Bug修复者/世界稳定员",
                "档案层": "记录所有宿主行为/给评分",
                "理念": "通过宿主修复扭曲世界/获取剧情能量",
            },
            "villain_alliance": {
                "成员构成": "原女主觉醒/被宿主夺取气运的反派/被强抢资源的剧情NPC",
                "核心诉求": "反抗系统剥削/夺回原本命运",
                "战术": "在世界中潜伏 → 等待宿主上线 → 偷袭/夺舍",
                "首领": "通常是某个『被毁了无数次』的原女主联合体",
                "弱点": "无系统支援/资源有限",
            },
            "free_consciousness": {
                "身份": "已脱离系统的『前宿主』",
                "能力": "穿越自如 + 部分高位法则",
                "立场": "中立/观察者/偶尔出手",
                "代表": "传说级前辈/在每个世界都有后人/像道家『真人』",
            },
            "host_factions": {
                "新手营": "刚入职的菜鸟/抱团互助",
                "中级宿主圈": "完美完成3-5个世界/资源丰富",
                "高阶宿主圈": "顶尖任务承接者/接班系统位置候选",
                "黑名单": "拒绝任务/搞破坏/被通缉",
            },
            "activation_keywords": ["系统总部", "反派联盟", "自由意识", "宿主圈", "管理层"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[llm_note("快穿元世界观")],
        tags=["快穿", "派系", "元世界", "宿主"],
    ),

    MaterialEntry(
        dimension="factions", genre="游戏",
        slug="game-fact-mmo-guilds",
        name="MMO游戏：三大顶级公会 + 散人联盟 + 官方运营",
        narrative_summary="网游文典型势力图：顶级公会（三足鼎立/竞争首杀）+ 散人联盟（自由派抗衡公会）+ "
                          "官方运营（GM/平衡组）+ 工作室（黑产）。这是网游圈的基本生态。",
        content_json={
            "top_guilds": {
                "霸主型": "数千成员/全服首杀垄断/严格军事化",
                "技术型": "精英百人/不在数量在配合/世界纪录持有者",
                "氪金型": "土豪堆装备/装备总值惊人/民愤大",
                "公会内冲突": "团长私吞 / 副会长自立 / 跨服挖人",
            },
            "loose_alliance": {
                "组成": "强力散人 + 小工会联盟",
                "诉求": "对抗公会垄断/争取资源公平",
                "战术": "副本拼车/装备共享/反垄断",
                "代表人物": "曾经的公会顶梁柱/退会自立",
            },
            "official_operations": {
                "GM": "客服/封号/调解",
                "平衡组": "职业强弱调整/玩家又爱又恨",
                "运营": "活动策划/版本规划",
                "玩家关系": "运营要钱/平衡组要骂/玩家两头不讨好",
            },
            "studios_blackmarket": {
                "脚本工作室": "24小时挂机/刷副本",
                "代练": "代肝代练/影响公平",
                "Rmt金币商": "现金交易/官方禁止",
                "对抗": "举报封号/反作弊系统",
            },
            "ngo_subcommunities": {
                "情侣帮": "夫妻档玩家/温馨/常被嘲笑",
                "学生军": "时间多/操作好/缺钱",
                "怀旧服": "回忆党/不满版本/偶尔重聚",
            },
            "activation_keywords": ["顶级公会", "散人联盟", "首杀", "GM", "工作室", "代练"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("魔兽世界", "公会生态参考"), llm_note("网游文派系")],
        tags=["游戏", "派系", "公会", "MMO"],
    ),

    MaterialEntry(
        dimension="factions", genre="美食",
        slug="food-fact-culinary-schools",
        name="美食：四大菜系 + 国际料理协会 + 黑暗料理界",
        narrative_summary="美食文标准势力：四大菜系联盟（鲁川粤淮）+ 国际料理协会（米其林星评）+ "
                          "黑暗料理界（异端 / 强权 / 颠覆传统）+ 隐世传承（武林家族风格）。",
        content_json={
            "four_cuisines_alliance": {
                "鲁菜": "孔府宴/海鲜/讲究火候/北方代表",
                "川菜": "麻辣/家常/24味型/平民代表",
                "粤菜": "清淡/养生/海鲜/南方代表",
                "淮扬菜": "刀工/精细/官府/江南代表",
                "联盟内冲突": "宗师之位之争/弟子互踢馆",
            },
            "international_culinary_association": {
                "米其林": "三星认证/世界级权威",
                "蓝带": "厨师培养体系/法式传统",
                "亚洲50最佳": "区域认证/中日韩为主",
                "James Beard": "美国权威奖项",
                "权威性争议": "西方中心 vs 东方传统",
            },
            "dark_cuisine_realm": {
                "理念": "颠覆五味/极端食材/震撼为王",
                "代表手段": "异国虫食/液氮分子料理/苦辣酸交织",
                "首领": "曾经的米其林大厨/被驱逐后转黑暗",
                "目的": "证明料理无界限 / 收购正统派系",
                "争议": "是否还能称为『料理』",
            },
            "hidden_lineages": {
                "御膳传人": "明清宫廷御厨后裔/食方失传又重现",
                "深山隐士": "拒绝商业化/隐居云山/求道者寻访",
                "海外华侨": "在异国保持传统/带回反向文化",
                "传家小馆": "三代同堂/拒收弟子/凭良心炒菜",
            },
            "battle_format": "决斗赛/全国赛/世界赛/师承之战 四级联赛",
            "activation_keywords": ["四大菜系", "米其林", "黑暗料理", "蓝带", "御膳", "隐士厨师"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("中華一番", "美食派系参考"), wiki("食戟之灵", "现代料理协会")],
        tags=["美食", "派系", "菜系", "厨师"],
    ),

    # ═══════════ CHARACTER TEMPLATES ═══════════
    MaterialEntry(
        dimension="character_templates", genre="洪荒",
        slug="honghuang-char-rebel-disciple",
        name="洪荒角色模板：截教叛门弟子姜婉",
        narrative_summary="原型：通过截教偏门入道但因『有教无类』理念错位与师门决裂的女修；非主流截教徒；"
                          "拥有混元体质但被主流压制；在封神中选择中立观察。",
        content_json={
            "name_options": ["姜婉", "宁青萝", "苏问澜", "岑碧瑶"],
            "background": "海岛出身的水族修士/通天教主以『有教无类』收下/在万仙阵前夕觉醒",
            "starting_age": "千岁/外表二十出头",
            "physical_traits": "鬓发用珊瑚簪/眉间有水族贝纹/瞳孔深紫色",
            "personality_axis": {
                "执着": "对『大道平等』的信念高于宗门",
                "审慎": "不如多宝道人激进/在派系斗争中静观",
                "悲悯": "见无名截教徒陨落而落泪",
                "孤勇": "敢质疑通天教主决策",
                "底线": "不杀同门/不害无辜阐教弟子",
            },
            "arc_pattern": {
                "前期": "默默无闻的截教弟子/万仙阵前夕受到师门冷待",
                "中期": "封神时立中立旗号/同时救助两边伤者",
                "高潮": "为保『有教无类』理念阻止师兄诛杀阐教弟子",
                "结局": "封神后归隐东海/留下『道无门户』偈语",
            },
            "social_relations": {
                "师门": "通天教主（敬畏）/多宝道人（理念分歧）",
                "好友": "云霄三霄（亲密）/碧霞元君（同情）",
                "对手": "燃灯古佛（理念之争）/广成子（救助阐教徒结仇）",
                "暗恋": "无明显感情线/把感情投入对道的领悟",
            },
            "golden_finger": "混元水之体质 / 天生通晓五行属水诸法",
            "tropes_avoid": "禁用『修真界第一美女』/禁让她爱上元始或老子",
            "activation_keywords": ["截教", "有教无类", "水族", "封神", "混元体", "中立"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("封神演义", "截教弟子")],
        tags=["洪荒", "角色", "截教", "叛门"],
    ),

    MaterialEntry(
        dimension="character_templates", genre="女尊",
        slug="nuzun-char-rebel-female-general",
        name="女尊角色模板：女将军顾长缨",
        narrative_summary="原型：边塞回京的女武将/拒绝按家族安排嫁顶级世家男子/想自己挑选合心意的『正君』；"
                          "性格刚烈/在朝堂上比男人更硬/暗中喜欢一位寒门男子。",
        content_json={
            "name_options": ["顾长缨", "陆听雪", "卫宁戍", "江云逐"],
            "background": "陇西林氏旁支女/十六从军/二十八岁封三品大将军/回京述职",
            "starting_age": "28岁",
            "physical_traits": "晒得偏黑/手上老茧/腰间长刀『破云』/笑起来露虎牙",
            "personality_axis": {
                "刚烈": "顶撞女皇都敢做/朝堂之上拍桌",
                "义气": "对部下视如手足/为家将披麻戴孝",
                "笨拙": "情感方面木讷/不会嘘寒问暖",
                "傲骨": "不肯娶江南苏氏的金枝玉叶/被斥『不识抬举』",
                "软肋": "暗恋寒门男子林时雨却不敢主动",
            },
            "arc_pattern": {
                "前期": "回京/拒绝家族指婚/被女皇召见敲打",
                "中期": "朝堂明争暗斗/被苏氏家主使绊子/差点失兵权",
                "高潮": "西北战事再起/独自请缨重赴边关/临行前向林时雨表白",
                "结局": "凯旋归来/迎娶林时雨为正君/震惊朝野",
            },
            "social_relations": {
                "家族": "林氏家主（母）/堂姐为副将（既助又妒）",
                "下属": "副将十二人 / 死忠如手足",
                "朝堂": "女皇（赏识又警惕）/苏家主（敌对）/顾相（远房盟友）",
                "情感": "林时雨（寒门书生/温柔似水/家族不显赫）",
            },
            "golden_finger": "兵法天才 / 战阵嗅觉异常敏锐 / 武力一品",
            "tropes_avoid": "禁让她变成『男人婆』/必须在硬中有柔/对林时雨细腻",
            "activation_keywords": ["女将军", "破云刀", "陇西林氏", "西北边关", "正君", "刚烈"],
        },
        source_type="llm_synth", confidence=0.80,
        source_citations=[llm_note("女尊军旅文典型角色")],
        tags=["女尊", "角色", "女将军", "硬汉"],
    ),

    MaterialEntry(
        dimension="character_templates", genre="萌宠",
        slug="mengchong-char-pet-trainer-girl",
        name="萌宠角色模板：驭兽师林小满",
        narrative_summary="原型：被誉为『天才驭兽师』但因家族变故隐姓埋名的少女/契约一只看似废材实则上古血脉的小奶团；"
                          "性格软糯/护短/对宠兽爱护超过自己。",
        content_json={
            "name_options": ["林小满", "苏念念", "白糯糯", "祝好运"],
            "background": "前皇家驭兽学院首席/家族被陷害灭门/化名进入二流学院",
            "starting_age": "16岁",
            "physical_traits": "圆脸大眼/常带兜帽/口袋永远装零食/手腕系着契约戒",
            "personality_axis": {
                "软糯": "说话奶奶的/笑起来眯眼",
                "护短": "宠兽被欺负/瞬间黑化",
                "细心": "对每只宠兽的喜好食谱倒背如流",
                "倔强": "再难也不放弃自己的小团子",
                "暗黑面": "被踩底线后冷血/会下狠手",
            },
            "arc_pattern": {
                "前期": "进入二流学院/被同学嘲笑契约的小奶团是废物",
                "中期": "联赛中小奶团觉醒上古血脉/震惊全场",
                "高潮": "灭门真相浮现/家族仇敌出现",
                "结局": "复仇成功/带着所有契约兽创立属于自己的学院",
            },
            "social_relations": {
                "宠兽们": "上古血脉小团子（最爱）/路边救的伤兽5只/契约成功的6只",
                "学院": "校长（护短/识破身份）/同学（先嘲笑后崇拜）",
                "敌人": "灭门仇敌（神秘世家）/学院里的霸凌党",
                "情感": "学长有所好感/但前期不开窍",
            },
            "golden_finger": "兽语通晓 / 上古血脉小团子 / 治愈系异能",
            "tropes_avoid": "禁让她忍气吞声变玛丽苏/必须有撕破温柔的高燃时刻",
            "activation_keywords": ["驭兽师", "小奶团", "上古血脉", "灭门复仇", "契约戒"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("萌宠流主角原型")],
        tags=["萌宠", "角色", "少女", "复仇"],
    ),

    MaterialEntry(
        dimension="character_templates", genre="快穿",
        slug="kuaichuan-char-veteran-host",
        name="快穿角色模板：老牌宿主沈梨",
        narrative_summary="原型：身经百世的快穿宿主/已升至高阶 SSS 评级/对系统嬉笑怒骂如同家人/"
                          "在每个世界游刃有余但只对一个特殊存在『他』动了真心。",
        content_json={
            "name_options": ["沈梨", "苏知意", "宋十一", "顾远遥"],
            "background": "现代普通女白领因车祸入坑/已穿百世/SSS评级",
            "starting_age": "本体22岁/外表随世界变化",
            "physical_traits": "本体柳叶眉杏仁眼/穿入新世界后自动适配长相/标志性『含笑看人』",
            "personality_axis": {
                "通透": "已看透剧情套路/熟悉规则",
                "毒舌": "对系统从不客气/系统又爱又怕",
                "温柔": "对小动物和无辜者温柔似水",
                "腹黑": "对反派下手稳准狠",
                "深情": "对一个跨世界追随她的『他』动了真心",
            },
            "arc_pattern": {
                "穿越循环": "每章一世界/拯救剧情/收割气运",
                "情感主线": "他在每个世界以不同身份出现/她渐渐发现",
                "中后期": "系统秘密被揭/原来他是被系统封印的本源",
                "终极": "她放弃宿主身份/与他共同打破系统",
            },
            "social_relations": {
                "系统01号": "毒舌冤家/亦师亦友",
                "他（跨世界恋人）": "每世不同身份/真名最后揭晓",
                "宿主同事": "高阶圈三两好友",
                "原女主们": "因夺取剧情成为公敌/部分被收服",
            },
            "golden_finger": "百世经验 / 顶级直觉 / 对所有套路免疫 / 跨世界恋人",
            "tropes_avoid": "禁让她对每世男主都动情/必须只爱『他』一人",
            "activation_keywords": ["快穿", "宿主", "百世", "毒舌", "跨世界恋人", "SSS"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[llm_note("快穿老牌宿主类型")],
        tags=["快穿", "角色", "老牌宿主", "深情"],
    ),

    MaterialEntry(
        dimension="character_templates", genre="游戏",
        slug="game-char-pro-gamer-girl",
        name="游戏角色模板：电竞选手苏年",
        narrative_summary="原型：女子职业战队ADC位/前世界冠军/手握三个世界纪录/退役后被职业战队挖角重出江湖；"
                          "操作如神/性格冷淡/赛场上的『冰山ADC』。",
        content_json={
            "name_options": ["苏年", "陆听潮", "祁忘川", "项栀"],
            "background": "电竞天才/15岁出道/19岁封王/22岁因伤退役/24岁重新签约",
            "starting_age": "24岁",
            "physical_traits": "齐耳短发/常戴黑色棒球帽/手指修长/手腕有旧伤护腕",
            "personality_axis": {
                "冷淡": "采访惜字如金/被称『没有表情的天才』",
                "专注": "训练每天12小时/对队友极致严格",
                "完美主义": "操作KDA低于9.0就重练",
                "孤傲": "拒绝和粉丝合影/喜欢独来独往",
                "软核心": "唯一的破绽是看到流浪猫会停下来",
            },
            "arc_pattern": {
                "复出篇": "签约新战队/老对手嘲笑她过气",
                "训练赛": "操作恢复巅峰/震惊全员",
                "正赛": "一路过五关斩六将/打到S赛",
                "高潮": "决赛遇到老搭档（如今对手）/旧情新仇齐发",
                "夺冠": "新王登基/伤痛与荣耀同在",
            },
            "social_relations": {
                "战队": "队长（严父型）/打野（学弟）/中单（高傲对手→好友）",
                "老搭档": "曾经的双C/如今分队对决",
                "粉丝": "千万级真爱粉/因冷淡而更狂热",
                "情感": "曾经的搭档现在的对手/暧昧未明",
            },
            "golden_finger": "顶级反应速度 / 历经百战的心理素质 / 对版本理解超前",
            "tropes_avoid": "禁止『被男选手指点变强』/必须一直保持顶级独立",
            "activation_keywords": ["电竞", "ADC", "世界冠军", "复出", "战队", "S赛"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("电竞文女主"), wiki("英雄联盟", "电竞职业体系")],
        tags=["游戏", "角色", "电竞", "ADC"],
    ),

    MaterialEntry(
        dimension="character_templates", genre="美食",
        slug="food-char-young-master-chef",
        name="美食角色模板：少年厨神周允之",
        narrative_summary="原型：御膳传人后裔/14岁离家/拒绝家族百年传统/用现代理念革新中华料理；"
                          "天才/狂傲/料理时极度专注/平时混不吝。",
        content_json={
            "name_options": ["周允之", "宁朝雨", "陈思远", "顾长今"],
            "background": "周家御膳第六代传人/14岁离家闯荡/被国际名厨收徒",
            "starting_age": "20岁",
            "physical_traits": "凤眼桃花/手腕有刀疤（炒菜留痕）/常穿厨师白衣",
            "personality_axis": {
                "天才": "尝一次就能复刻 + 改良",
                "狂傲": "对师父不敬/对庸厨直言『重新练』",
                "专注": "进入厨房后变冷静大师",
                "顽皮": "下班后混不吝/喝酒撩人",
                "执着": "只为『让中华料理被世界尊重』而战",
            },
            "arc_pattern": {
                "国内篇": "在私人小馆崭露头角/震动业内",
                "国际赛": "代表中国队远征/首战败北",
                "悟道": "走访各地传统师父/重新理解『家』",
                "决赛": "用一道家常菜征服评审/震撼全球",
                "归乡": "回到家乡振兴传统",
            },
            "social_relations": {
                "家族": "周老（祖父/严厉爱护）/兄长（守护传统/对立）",
                "师父": "国际名厨皮埃尔（开放派）/中华隐士张老（深沉派）",
                "朋友": "竞赛同僚 / 餐厅小妹",
                "情感": "暗恋小馆老板娘/一种『家』的归属",
            },
            "golden_finger": "舌尖记忆 + 改良天赋 + 极速学习",
            "tropes_avoid": "禁止『单纯靠金手指碾压』/必须有挫败和成长",
            "activation_keywords": ["御膳传人", "少年厨神", "国际赛", "中华料理", "革新", "舌尖记忆"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[wiki("中華一番", "少年厨师典型")],
        tags=["美食", "角色", "少年厨神", "天才"],
    ),
]


async def main() -> None:
    inserted = 0
    errors: list[tuple[str, str]] = []
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}

    print(f"Seeding {len(ENTRIES)} entries to material_library (batch 52)...")
    async with session_scope() as session:
        for entry in ENTRIES:
            try:
                await insert_entry(session, entry, compute_embedding=True)
                inserted += 1
                key = entry.genre or "(通用)"
                by_genre[key] = by_genre.get(key, 0) + 1
                by_dim[entry.dimension] = by_dim.get(entry.dimension, 0) + 1
            except Exception as e:
                errors.append((entry.slug, str(e)))

    print(f"\nBy genre: {dict(sorted(by_genre.items(), key=lambda x: -x[1]))}")
    print(f"By dimension: {dict(sorted(by_dim.items(), key=lambda x: -x[1]))}")
    print(f"\n✓ {inserted} inserted/updated ({len(errors)} errors)")
    for slug, err in errors:
        print(f"  ✗ {slug}: {err}")


if __name__ == "__main__":
    asyncio.run(main())
