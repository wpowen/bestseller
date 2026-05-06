"""
Batch 51: power_systems + device_templates for thin genres (洪荒/女尊/萌宠/快穿/游戏/美食/末世).
Targets: 洪荒(10→), 女尊(8→), 萌宠(10→), 快穿(13→), 游戏(12→), 美食(13→), 末世(6→).
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
    # ═══════════ POWER SYSTEMS ═══════════
    MaterialEntry(
        dimension="power_systems", genre="洪荒",
        slug="honghuang-ps-pangu-cosmology",
        name="洪荒：盘古开天 → 三清 → 圣人体系",
        narrative_summary="洪荒流核心权力梯度：盘古元神化三清 → 鸿钧道祖讲道紫霄宫 → "
                          "三清/接引准提/女娲六圣分掌天地 → 大罗金仙/准圣/混元层级；"
                          "本世界没有等级数字，只有『道果』『功德』『气运』三维。",
        content_json={
            "metaphysical_layers": {
                "鸿钧道祖": "天道意志体现/讲道时间已凝固/不入因果",
                "圣人": "混元大罗金仙/不死不灭/六位定额/受天道约束",
                "准圣": "斩二尸/距离圣人差一线/可斗法但不可弑圣",
                "大罗金仙": "斩一尸/可越过时空长河窥探/已脱离量劫规则部分",
                "太乙金仙": "渡过九次九九雷劫/可创小型天地",
                "玄仙": "渡过三次九九雷劫/出入虚空",
                "天仙": "渡过一次雷劫/驻颜永生",
                "地仙": "炼气化神圆满/坐镇一方福地",
                "人仙": "肉身不死/百年寿命起步",
            },
            "three_axes": {
                "道果": "对天道法则的领悟深度（决定上限）",
                "功德": "济世救劫积累（圣人门槛）",
                "气运": "天命赐予（决定能否破劫）",
            },
            "kalpa_system": "量劫每元会一次/封神/西游/巫妖/龙凤为四大量劫",
            "anti_godmode": "圣人不能直接出手/凡间因果反噬/弟子代行/留下转圜余地",
            "activation_keywords": ["盘古", "鸿钧", "三清", "圣人", "量劫", "道果", "功德", "气运"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("封神演义", "明许仲琳"), wiki("山海经", "上古地理志怪"), llm_note("洪荒流权力体系")],
        tags=["洪荒", "权力体系", "上古", "圣人"],
    ),

    MaterialEntry(
        dimension="power_systems", genre="女尊",
        slug="nuzun-ps-matriarchal-rank",
        name="女尊世界等级体系",
        narrative_summary="女尊文权力结构：女子科举/朝堂女官/男子操持家务/重要决策女性独占；"
                          "等级以『品阶 + 战功 + 嫁娶资源』综合决定；男子地位以正君/侧君/侍君排序。",
        content_json={
            "court_hierarchy": {
                "女皇": "天下共主/可纳九夫",
                "公主/郡主": "封地+食邑/可自由择夫",
                "一品女将/女相": "朝堂中枢/三妻四夫合规",
                "三品/五品": "州府/郡守级/二夫一侍",
                "九品/不入流": "县丞/吏员/一夫制",
            },
            "male_status_layers": {
                "正君": "正室身份/可参与家事决策/嫡子继承",
                "侧君": "次于正君/有自己院落",
                "侍君": "纳妾性质/无礼仪保障",
                "面首": "纯欲望关系/可遣可换",
            },
            "social_taboos": {
                "男子读书": "被视为不务正业（部分开明地区例外）",
                "男子从政": "极少例外/通常被斥为『阴阳颠倒』",
                "男子贞洁": "嫁前要『守身』/二嫁不被祝福",
            },
            "gender_inversion_logic": "权力/经济/法律全反转/温柔/需保护成为男性美德",
            "activation_keywords": ["女皇", "正君", "侍君", "三妻四夫", "嫁娶", "贞洁"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("女尊文等级架构")],
        tags=["女尊", "权力体系", "性别反转", "等级"],
    ),

    MaterialEntry(
        dimension="power_systems", genre="萌宠",
        slug="mengchong-ps-pet-evolution",
        name="萌宠流：宠兽进化树",
        narrative_summary="萌宠文中宠兽进化路径：契约 → 共鸣 → 觉醒血脉 → 异变 → 神兽；"
                          "宠兽等级常以『1-9阶』+『血脉纯度』双轴；主人成长与宠兽双线绑定。",
        content_json={
            "pet_rank_axis": {
                "1阶": "幼兽/常见宠物形态",
                "3阶": "突破第一次/出现初级技能",
                "5阶": "中阶觉醒/可幻化人形（部分）",
                "7阶": "高阶/可独立战斗",
                "9阶": "顶阶/接近神兽边界",
                "神兽": "已无等级/法则掌控",
            },
            "bloodline_purity": {
                "纯血": "原种血统/能力上限最高/极罕见",
                "混血": "杂交进化/能力多样/数量多",
                "返祖": "突变激活上古血脉/罕见但可遇",
                "异变": "辐射/魔气感染/能力不稳定",
            },
            "contract_layers": {
                "普通契约": "听话但无心灵相通",
                "血契": "主仆血脉相连/同生共死",
                "灵魂契约": "灵魂层面绑定/可分享视觉/最高阶",
            },
            "synergy_growth": "主人修炼境界 ↔ 宠兽进化阶级 双向锁定/任一卡死另一也卡",
            "activation_keywords": ["契约兽", "幻兽", "血脉", "进化", "神兽", "幼崽", "灵宠"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("萌宠流体系"), wiki("宝可梦", "进化机制参考")],
        tags=["萌宠", "权力体系", "进化", "契约"],
    ),

    MaterialEntry(
        dimension="power_systems", genre="快穿",
        slug="kuaichuan-ps-system-grade",
        name="快穿：系统等级与任务积分体系",
        narrative_summary="快穿文核心：系统给宿主分配世界 → 世界难度等级 → 完成度评分 → 积分兑换/等级提升；"
                          "宿主能力分『基础属性』『道具栏』『金手指』三块；积分既是货币也是命脉。",
        content_json={
            "world_difficulty_tier": {
                "F级（练手）": "无主线/低危/任务简单",
                "D级": "有主线但NPC弱智/容错率高",
                "C级": "正常世界/需注意因果",
                "B级": "高难/原女主有金手指/反派智商在线",
                "A级": "炼狱/世界本源会反扑/死亡可能",
                "S级": "禁忌世界/已有多个宿主死亡/积分巨额",
                "SSS级": "传说级/通关后可解锁本源",
            },
            "task_completion_grading": {
                "完美S": "100% + 隐藏目标/积分 ×3",
                "优秀A": "100%主线/积分 ×1.5",
                "合格B": "70%以上/正常发放",
                "勉强C": "50%-70%/积分 ×0.5",
                "失败F": "扣命寿/部分倒扣",
            },
            "host_resources": {
                "基础属性": "颜值/智力/体质/魅力/气运（系统初值+成长）",
                "道具栏": "限时面具/解药/记忆面包/位置追踪",
                "金手指": "学神模式/读心术/时光倒流（受积分限制）",
            },
            "system_personality": {
                "傲娇毒舌": "嘴上嫌弃实则操心",
                "冷酷高冷": "只汇报数据/不掺杂情感",
                "幼稚萌系": "需要宿主反向哄/带感情线",
                "上司型": "讲规则/不通融",
            },
            "activation_keywords": ["快穿", "积分", "任务", "宿主", "世界", "系统", "完成度"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[llm_note("快穿网文体系汇总")],
        tags=["快穿", "权力体系", "系统", "积分"],
    ),

    MaterialEntry(
        dimension="power_systems", genre="游戏",
        slug="game-ps-mmo-class-stat",
        name="MMO游戏：职业 + 属性 + 装备体系",
        narrative_summary="网游/虚拟现实文核心：职业三角（坦克/输出/治疗）+ 属性六维（力敏智耐魔运）+ "
                          "装备分级（白绿蓝紫橙红） + 副本难度（普通/英雄/史诗/噩梦）。",
        content_json={
            "class_triangle": {
                "坦克T": "守护骑士/狂战士/血盾",
                "输出DPS": "近战刺客/远程法师/弓手",
                "治疗HP": "牧师/德鲁伊/圣骑士辅疗",
                "辅助BUFF": "吟游诗人/咒术师/召唤师",
                "特殊职业": "驭兽师/工匠/暗影行者（隐藏职业）",
            },
            "stat_six_axes": {
                "力量STR": "物理伤害/负重",
                "敏捷AGI": "暴击/闪避/速度",
                "智力INT": "法术伤害/法力上限",
                "耐力CON": "生命/防御",
                "魔力WIL": "抗性/精神",
                "幸运LUK": "暴击概率/掉落/隐藏",
            },
            "equipment_tiers": {
                "白色普通": "杂兵掉落",
                "绿色精良": "副本绿装",
                "蓝色稀有": "副本BOSS",
                "紫色史诗": "团队副本/锻造",
                "橙色传说": "世界BOSS/任务链",
                "红色神话": "全服首杀/极罕见",
                "暗金/全套效果": "套装效果触发更强",
            },
            "dungeon_difficulty": {
                "普通": "5人小队/低门槛",
                "英雄": "属性翻倍/掉落更优",
                "史诗": "10-25人团/机制复杂",
                "噩梦": "禁复活/限时/最高奖励",
                "无尽": "无限层数/排行榜",
            },
            "leveling_curve": "1-50常规/50-99艰难/100+脱凡境界",
            "activation_keywords": ["MMO", "副本", "DPS", "T", "装备", "属性", "等级", "BOSS"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("魔兽世界", "MMORPG经典"), wiki("最终幻想XIV", "职业体系参考")],
        tags=["游戏", "权力体系", "MMO", "职业"],
    ),

    MaterialEntry(
        dimension="power_systems", genre="美食",
        slug="food-ps-culinary-master-rank",
        name="美食流：厨艺等级 + 心境 + 食材体系",
        narrative_summary="美食文核心三轴：厨艺等级（学徒→大厨→宗师→料理之神）+ 心境层次（手熟→意境→道）+ "
                          "食材稀有度（凡品→灵食→仙馐→神羞）；料理胜负不仅看味道，看『感动评审的瞬间』。",
        content_json={
            "chef_rank": {
                "学徒": "三年灶台/熟悉刀工",
                "大厨": "独立菜单/自创一道招牌",
                "特级厨师": "通过三星认证/可参加国赛",
                "宗师": "开宗立派/传承体系",
                "料理之神": "传说级/作品成为时代记忆",
            },
            "mind_state_layers": {
                "手熟": "动作不会出错",
                "用心": "为食客考量",
                "意境": "料理含主厨心情",
                "化境": "食物自带哲学命题",
                "道": "天人合一/食物即作者",
            },
            "ingredient_rarity": {
                "凡品": "市场普通食材",
                "上品": "限定季节/产地",
                "灵食": "灵山异谷/有微弱灵气",
                "仙馐": "天材地宝/百年生长",
                "神羞": "天地法则凝炼/吃下渡劫",
            },
            "judgement_axes": {
                "味": "基础/能否回味",
                "形": "色香摆盘",
                "意": "故事/情感",
                "心": "评审能否感动落泪",
                "魂": "作品脱离作者独立存在",
            },
            "battle_format": "决斗时双方限时/同食材/评审三人/盲品",
            "activation_keywords": ["厨艺", "心境", "食材", "灵食", "料理", "宗师", "化境"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("中華一番", "经典美食漫画"), wiki("食戟之灵", "现代美食漫画")],
        tags=["美食", "权力体系", "厨艺", "心境"],
    ),

    MaterialEntry(
        dimension="power_systems", genre="末世",
        slug="moshi-ps-evolution-tier",
        name="末世流：异能进化 + 丧尸阶段 + 安全区等级",
        narrative_summary="末世（含丧尸/天灾/外星）核心三轴：异能者进化阶（1-9阶）+ 丧尸/异种危险阶 + "
                          "安全区文明等级；与『末日』有重叠但更强调『进化』而非『生存』。",
        content_json={
            "esper_tier": {
                "1阶（觉醒期）": "微弱异能/控制不稳",
                "3阶（成熟期）": "技能成型/能用于实战",
                "5阶（变异期）": "肉体改造开始/超越人类",
                "7阶（蜕变期）": "异能高度专精/可影响小区域",
                "9阶（神化）": "改写法则/末世皇者",
            },
            "esper_categories": {
                "元素系": "火/水/雷/冰/风/土",
                "肉体系": "力量/速度/再生",
                "精神系": "心控/预知/读心",
                "空间系": "瞬移/空间切割",
                "召唤系": "唤兽/亡灵/机械",
                "辅助系": "治疗/隐身/障壁",
                "稀有特殊": "时间/概率/因果（仅个位数觉醒者）",
            },
            "zombie_tier": {
                "1阶（普通）": "数量多/反应慢",
                "3阶（异变）": "动作快/团队行动",
                "5阶（领主）": "智能恢复/能下命令",
                "7阶（君王）": "区域统治/释放群体技能",
                "9阶（终极）": "已突破生死/接近神性",
            },
            "safezone_tier": {
                "聚集点": "百人以下/无武装",
                "据点": "千人/有围墙",
                "基地": "万人/异能编队",
                "城市": "十万人/政府重建",
                "帝国": "百万级/重启文明",
            },
            "activation_keywords": ["末世", "异能", "丧尸", "进化", "安全区", "基地", "觉醒"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("末世流标准设定")],
        tags=["末世", "权力体系", "异能", "丧尸"],
    ),

    # ═══════════ DEVICE TEMPLATES ═══════════
    MaterialEntry(
        dimension="device_templates", genre="洪荒",
        slug="honghuang-dev-xiantian-lingbao",
        name="洪荒：先天灵宝层级",
        narrative_summary="洪荒流神器：先天至宝（盘古所留/七大件）→ 先天灵宝（鸿钧道祖讲道凝炼）→ "
                          "先天功德灵宝（圣人功德所化）→ 后天灵宝（修士炼制）；每件灵宝都有『主人因果』。",
        content_json={
            "tier_xiantian_zhibao": {
                "鸿蒙紫气": "证道之物/三朵分予三清",
                "盘古幡": "开天辟地之器/元始所执",
                "诛仙四剑": "通天教主主器/需四圣合围才能破",
                "造化玉碟": "记录天道/鸿钧持有",
                "混沌钟": "镇压气运/帝俊主器",
                "太极图": "至阴至阳/老子证道",
                "番天印": "执掌天地/广成子",
            },
            "tier_xiantian_lingbao": {
                "定海神珠": "二十四颗/赵公明",
                "翻天印": "广成子",
                "八卦炉": "太上老君",
                "瑞兽朱厌": "通天",
            },
            "tier_meritorious_lingbao": {
                "封神榜": "天道功德/姜子牙执",
                "打神鞭": "封神配套/可击神位",
                "山河社稷图": "女娲补天功德/护佑九州",
            },
            "lingbao_cause_effect": {
                "认主血誓": "灵宝主动选主/拒主则反噬",
                "因果纠缠": "每件灵宝有『前任主人』/承接因果",
                "本源伤害": "灵宝被毁/主人本源受损",
            },
            "activation_keywords": ["先天灵宝", "诛仙剑", "山河社稷图", "封神榜", "盘古幡", "至宝", "因果"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("封神演义", "灵宝来源"), llm_note("洪荒灵宝梯度")],
        tags=["洪荒", "道具", "灵宝", "至宝"],
    ),

    MaterialEntry(
        dimension="device_templates", genre="女尊",
        slug="nuzun-dev-pinjie-feili",
        name="女尊：品阶玉牒与凤翎簪",
        narrative_summary="女尊文核心信物：朝堂品阶玉牒（女子身份证明）+ 凤翎簪（高位女子标志）+ "
                          "玉佩（嫁娶信物，男子定情之物）；这些器物承载了世界观的性别反转逻辑。",
        content_json={
            "yudie_pinjie": {
                "形制": "和田玉刻品阶/系青绶",
                "功能": "出入宫禁/调兵符印/见官免礼",
                "等级标识": "一品紫玉/三品蓝玉/五品白玉/九品青石",
                "丢失后果": "三日内必上奏/否则视同叛乱",
            },
            "feng_ling_zan": {
                "形制": "金/银/铜/木分等/凤翎数表品级",
                "佩戴规则": "公主九翎/一品七翎/三品五翎",
                "传家性质": "母传女/不可随意赠与",
                "战时作用": "可代主下令/临时调度家兵",
            },
            "yu_pei_xinwu": {
                "嫁娶信物": "男方持玉佩相赠/接受即定亲",
                "回礼规矩": "女方需回赠『腰牌』表示重视",
                "破婚仪式": "玉佩当面摔碎/男子可改嫁",
                "玉的品类": "羊脂玉表诚意/碧玉表家世/翡翠表富贵",
            },
            "gender_inversion_layer": "男子不掌玉牒/不戴凤翎/玉佩定情=被动接受",
            "activation_keywords": ["玉牒", "凤翎簪", "玉佩", "定亲", "品阶", "凤翎"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("女尊文信物体系")],
        tags=["女尊", "道具", "信物", "性别"],
    ),

    MaterialEntry(
        dimension="device_templates", genre="萌宠",
        slug="mengchong-dev-soul-bind-ring",
        name="萌宠：契约戒 + 兽语铃 + 召唤阵",
        narrative_summary="萌宠文常见三件套：契约戒（人宠绑定）+ 兽语铃（双向沟通）+ 召唤阵（空间收纳/紧急召唤）；"
                          "三者构成『驯兽师/驭兽师/灵宠师』的标准装备。",
        content_json={
            "qiyue_jie": {
                "形制": "戒指或手链/嵌入血珠",
                "绑定方式": "滴血认主+宠兽舔舐",
                "功能": "双向心灵感应/分享生命力/共享视野",
                "破契代价": "强制解除→主人重伤/宠兽智商倒退",
            },
            "shouyu_ling": {
                "形制": "脚铃或项铃/兽魂铸成",
                "功能": "翻译动物语言/扩散50米",
                "升级路径": "1-9阶/铃数增加",
                "稀缺性": "需用契约兽魂魄打造/失去铃=失去交流",
            },
            "zhaohuan_zhen": {
                "形制": "手心刺青/心口印记/手镯",
                "功能": "随身收纳宠兽（独立空间） + 紧急召唤",
                "容量限制": "1-9阶/可纳兽数量+空间环境",
                "副作用": "频繁召唤/宠兽易倦怠/需修养",
            },
            "trio_combo_effect": "戒+铃+阵 三位一体/触发『心灵共鸣』状态/战力翻倍",
            "activation_keywords": ["契约戒", "兽语铃", "召唤阵", "灵宠", "驭兽", "心灵感应"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("萌宠流装备体系")],
        tags=["萌宠", "道具", "契约", "宠兽"],
    ),

    MaterialEntry(
        dimension="device_templates", genre="快穿",
        slug="kuaichuan-dev-system-tools",
        name="快穿：系统标配道具栏",
        narrative_summary="快穿宿主标配道具栏（按积分解锁）：易容面具/记忆面包/位置追踪/复活币/读心耳塞/锁灵符；"
                          "这些道具是宿主『身份切换』和『紧急避险』的核心工具。",
        content_json={
            "tier1_basic": {
                "易容面具": "改变样貌1小时/100积分/低级",
                "记忆面包": "瞬间记忆原主一生/200积分",
                "翻译耳塞": "听懂任何语言/50积分",
                "气味中和": "消除主角光环气场/30积分",
            },
            "tier2_advanced": {
                "位置追踪": "锁定关键NPC坐标/500积分",
                "读心耳塞": "听见目标内心OS/1000积分/限3次",
                "复活币": "免疫一次致命伤/2000积分/全任务一次",
                "锁灵符": "封印对方异能/500积分/30分钟",
            },
            "tier3_premium": {
                "时光倒流": "重置最近10秒/5000积分",
                "替身娃娃": "代受一次伤/3000积分",
                "幻境陷阱": "拖延对手30分钟/2000积分",
                "因果剪刀": "切断一段命运线/8000积分/危险",
            },
            "purchase_logic": {
                "强制采购": "新世界开始/系统送一件",
                "积分换取": "随时购买/价格随等级变化",
                "彩蛋赠送": "完美完成隐藏任务/限定道具",
            },
            "activation_keywords": ["易容面具", "记忆面包", "复活币", "读心耳塞", "锁灵符", "积分", "系统"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[llm_note("快穿系统通用道具")],
        tags=["快穿", "道具", "系统", "积分"],
    ),

    MaterialEntry(
        dimension="device_templates", genre="游戏",
        slug="game-dev-equipment-set",
        name="MMO游戏：套装效果 + 神器 + 隐藏装备",
        narrative_summary="网游文典型装备组合：套装效果（2件/4件/全套触发）+ 神器（任务链解锁）+ "
                          "隐藏装备（彩蛋/诡异条件触发）；橙色及以上多带『故事』。",
        content_json={
            "set_effects_tiers": {
                "白绿装": "无套装",
                "蓝装": "2件+5%属性/4件+10%",
                "紫装": "2件+小技能/4件+被动/6件+变身",
                "橙装": "全套激活专属觉醒",
                "红装": "2件就触发/全套时空属性",
            },
            "artifact_unlock_path": {
                "前置任务": "100级 + 阵营声望崇拜 + 5个稀有材料",
                "成长性神器": "随玩家等级解锁词条",
                "诅咒神器": "强力但每日扣血",
                "天命神器": "只能特定职业 + 特定姓名玩家",
            },
            "hidden_equipment": {
                "彩蛋触发": "在某地坐10小时不动",
                "极小概率掉落": "0.001%/全服首件公告",
                "条件触发": "同时穿戴指定3件 + 站在指定地点",
                "禁忌制造": "牺牲全部装备耐久换出/不可逆",
            },
            "equipment_appearance": {
                "光效": "蓝色微光/紫色流转/橙色火焰/红色雷霆/暗金黑雾",
                "特殊外观": "跨服首杀掉落/全服炫耀",
            },
            "activation_keywords": ["套装", "神器", "隐藏装备", "全服首杀", "光效", "诅咒装备"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("暗黑破坏神II", "传奇/套装装备体系参考")],
        tags=["游戏", "道具", "装备", "套装"],
    ),

    MaterialEntry(
        dimension="device_templates", genre="美食",
        slug="food-dev-divine-cookware",
        name="美食流：神级厨具 + 传家秘方 + 上古食器",
        narrative_summary="美食文核心器物：神级厨具（菜刀/铁锅/砂锅/烤炉）+ 传家秘方（书本/口诀/料理魂）+ "
                          "上古食器（青铜鼎/朱雀盏）；好厨师『刀比命重』。",
        content_json={
            "godly_cookware": {
                "传世菜刀": "祖传/越用越锋利/有自己性格",
                "玄铁锅": "受热均匀/不粘锅/可承大火",
                "砂锅": "锁味/慢炖之神/熬出本味",
                "竹蒸笼": "百年老竹/带山林之气",
                "黑松露刨": "巴黎名匠手作/刀工灵敏度+50%",
                "天目盏": "宋代古物/呈现料理意境",
            },
            "secret_recipe": {
                "传家手抄": "祖父辈秘方/字迹模糊/需领悟",
                "口耳相传": "无文字记录/只传嫡系",
                "料理魂": "已逝大厨残留意念/附着于刀具",
                "禁忌之书": "记载危险料理/可能反噬",
            },
            "ancient_food_vessels": {
                "青铜鼎": "煮国之大宴/出自夏商",
                "朱雀盏": "盛装稀世美酒",
                "玉箸": "可探毒气",
                "金樽": "增添酒香",
                "瓷碗": "薄如蝉翼/盛汤显灵",
            },
            "knife_grading": {
                "学徒刀": "普通钢刀",
                "二级刀": "大马士革花纹/有锋",
                "宗师刀": "陨铁锻造/可斩石",
                "神刀": "已通灵性/认主",
            },
            "activation_keywords": ["菜刀", "玄铁锅", "传家秘方", "青铜鼎", "竹蒸笼", "天目盏", "宗师"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("中華一番", "厨具神器化"), wiki("食戟之灵", "料理装备")],
        tags=["美食", "道具", "厨具", "秘方"],
    ),
]


async def main() -> None:
    inserted = 0
    errors: list[tuple[str, str]] = []
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}

    print(f"Seeding {len(ENTRIES)} entries to material_library (batch 51)...")
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
