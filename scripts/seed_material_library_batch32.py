"""
Batch 32: Iconic devices / legendary objects / mythic artifacts.
Expands device_templates with cross-genre signature objects:
swords, rings, books, crystals, machines, golden-finger items.
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
    # 仙侠 - 储物戒指
    MaterialEntry(
        dimension="device_templates", genre="仙侠",
        slug="device-storage-ring",
        name="储物戒指（须弥戒 / 乾坤袋）",
        narrative_summary="仙侠 / 玄幻标配。空间法宝。"
                          "外见小戒指，内含数百立方米空间。"
                          "存丹药 / 灵石 / 法宝 / 食物 / 活物（高阶）。",
        content_json={
            "rank_design": "下品 / 中品 / 上品 / 极品 / 仙器级 / 不同空间大小（10立 / 100立 / 1000立 / 万立 / 无限）",
            "binding_method": "滴血认主（最常见）/ 灵识烙印 / 神识封印 / 魂血同源（双修组合）",
            "limitations": "不能放活物（低阶）/ 时间静止（高阶才有）/ 失主三天解封 / 储能耗灵力",
            "narrative_uses": "金手指：开局捡到祖传戒指 + 内含修炼资源 / 反派抢戒指 / 死后掉戒指 / 戒指里藏前世记忆 / 戒指里有上古传承",
            "famous_examples": "《凡人修仙传》韩立的青竹蜂云剑储物袋 / 《仙逆》绿萝的传家戒指 / 《诛仙》乾坤袋 / 《斗破苍穹》纳戒",
            "twist_design": "戒指里有一具未死的封印强者 / 戒指本身是上古凶兽 / 戒指里时间流速是外界 100 倍（突破修炼瓶颈）",
            "activation_keywords": ["储物戒指", "须弥戒", "乾坤袋", "纳戒", "空间法宝", "滴血认主"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("仙侠玄幻标配设定")],
        tags=["仙侠", "玄幻", "金手指", "标配"],
    ),
    # 仙侠 - 渡劫秘宝
    MaterialEntry(
        dimension="device_templates", genre="仙侠",
        slug="device-tribulation-shield",
        name="渡劫秘宝（避雷珠 / 渡劫塔）",
        narrative_summary="渡天劫专用法宝。仙侠后期高阶设定。"
                          "雷劫每升一阶难度倍增，凡修没渡劫秘宝几乎必死。"
                          "渡劫秘宝来源稀有 / 是大派传承。",
        content_json={
            "tribulation_levels": "九重雷劫 / 心魔劫 / 风火劫 / 三花聚顶 / 飞升劫",
            "shield_types": "避雷珠（吸 70% 雷劫）/ 渡劫塔（暂避 1 次完整劫）/ 仙阶护体灵衣 / 道门符箓阵",
            "scarcity": "千年一现 / 大派镇派之宝 / 上古仙人遗物 / 拍卖会底价亿灵石",
            "narrative_uses": "主角资源凑不齐渡劫秘宝 → 必须冒险下古墓 / 副本 / 闯禁地 / 反派故意夺取秘宝逼主角硬渡劫",
            "tragic_uses": "导师 / 兄长把唯一渡劫秘宝让给主角 / 自己渡劫身亡 = 强情感推动",
            "twist_design": "秘宝是诅咒物 / 渡劫之时反噬 / 渡劫成功但魂飞魄散",
            "activation_keywords": ["渡劫", "雷劫", "避雷珠", "渡劫塔", "天劫", "飞升", "心魔劫"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[llm_note("仙侠后期标配")],
        tags=["仙侠", "玄幻", "高阶", "金手指"],
    ),
    # 仙侠 - 上古图谱
    MaterialEntry(
        dimension="device_templates", genre="仙侠",
        slug="device-ancient-cultivation-manual",
        name="上古功法残卷",
        narrative_summary="主角金手指经典之一。上古失传功法。"
                          "比当代功法强百倍。一般是残卷（缺顶级 3-4 重）。"
                          "强迫主角去找剩余残卷 = 长篇主线。",
        content_json={
            "rank_above_modern": "上古 > 远古 > 太古 > 鸿蒙 / 修炼速度比现代功法快 5-10 倍 / 突破瓶颈无障碍 / 自带反噬功法",
            "scarcity_design": "整部残卷 9-12 卷，开局只有 1-3 卷 / 每卷在不同势力手里 / 集齐解锁鸿蒙级",
            "side_effects": "上古功法 = 走火入魔率高 / 反噬主角 / 修练魔功须吃噬人灵 / 寿元缩短",
            "narrative_arc": "贯穿全书。每卷代表一个长线副本 / 拿到一卷 = 升一阶 / 集齐 = 飞升或开光",
            "famous_pattern": "《诛仙》青云十三诛剑卷 + 《斗破苍穹》三千焱炎火功法 + 《遮天》九秘 + 《圣墟》九帝功",
            "twist_design": "残卷是诅咒物 / 上古功法是反派遗留 / 修炼到顶反成反派延续",
            "activation_keywords": ["上古功法", "残卷", "鸿蒙", "失传", "禁忌", "反噬"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("仙侠金手指核心设定")],
        tags=["仙侠", "玄幻", "金手指", "主线"],
    ),
    # 玄幻 - 系统面板
    MaterialEntry(
        dimension="device_templates", genre="玄幻",
        slug="device-game-system-panel",
        name="系统面板（金手指系统）",
        narrative_summary="2014 年后网文最大金手指。模仿网游 RPG 系统。"
                          "主角脑内自带任务面板 / 经验值 / 等级 / 装备 / 商城。"
                          "细分流派众多。",
        content_json={
            "system_subtypes": "签到系统 / 任务系统 / 商城系统 / 抽卡系统 / 模拟器系统 / 进化系统 / 收藏家系统 / 反派系统 / 副本系统 / 学神 / 美食 / 直播 / 打脸系统",
            "core_loop": "做事（签到/打脸/帮助NPC）→ 任务奖励 → 升级/法宝/秘籍 → 强者更强 → 更复杂任务",
            "system_personality_options": "傲娇系统 / 毒舌系统 / 中二系统 / 忠犬系统 / 天降妹妹系统 / 沉默工具系统",
            "punishment_mechanism": "不完成任务 = 扣寿元 / 失神通 / 等级倒退 / 灵魂湮灭",
            "narrative_uses": "完美的明线主线 = 系统给的任务列表 / 推进节奏天然 / 解决主角'下一步该做什么'问题",
            "novelty_pressure": "2024 年系统流已疲劳。需要变体：反系统 / 系统假死 / 系统是反派 / 系统是主角前世意识",
            "activation_keywords": ["系统", "金手指", "签到", "任务面板", "商城", "经验值", "进化"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("网文 2014 年后核心设定")],
        tags=["玄幻", "系统流", "金手指", "网文"],
    ),
    # 玄幻 - 血脉觉醒
    MaterialEntry(
        dimension="device_templates", genre="玄幻",
        slug="device-bloodline-awakening",
        name="血脉觉醒物（神血 / 上古血珠 / 龙之血）",
        narrative_summary="主角通过吞食 / 滴血某物觉醒强大血脉。"
                          "废柴翻身经典套路。前期废柴 → 血脉觉醒 → 一夜变强。",
        content_json={
            "bloodline_tiers": "凡人血 / 灵人血 / 半神血 / 神血 / 创世神血 / 鸿蒙血 / 跨阶式越级",
            "awakening_methods": "出生即觉醒（隐藏废柴体质）/ 死亡边缘觉醒 / 吞食血珠 / 上古凶兽血淬体 / 神器认主 / 转世归来",
            "side_effects": "血脉强 = 心性变（暴躁/冷酷/嗜血）/ 与凡人渐行渐远 / 寿元被压榨 / 招天劫嫉妒",
            "narrative_uses": "废柴翻身典型起点 / 第三章前必觉醒 / 之后是 '怎么用好血脉' = 修炼 + 战斗主线",
            "twist_design": "血脉是反派血脉 / 血脉觉醒后主角不再是原来的人 / 血脉之力反噬 / 真正觉醒是恶魔血",
            "famous_examples": "《斗破苍穹》萧炎陨落异火吞噬 / 《遮天》荒天帝转世 / 《圣墟》不凡传承 / 《雪鹰领主》龙血",
            "activation_keywords": ["血脉觉醒", "神血", "龙血", "上古血", "废柴翻身", "传承"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("玄幻金手指经典")],
        tags=["玄幻", "血脉", "金手指", "废柴翻身"],
    ),
    # 都市修仙 - 灵气复苏感应器
    MaterialEntry(
        dimension="device_templates", genre="都市修仙",
        slug="device-urban-spirit-detector",
        name="灵气感应器 / 古玩鉴宝镜",
        narrative_summary="都市修仙 / 鉴宝流主角金手指。"
                          "可视化探测灵气 / 灵物 / 古玩真伪。"
                          "现代科技外壳 + 古老内核。",
        content_json={
            "appearance_disguise": "智能手表 / 眼镜 / 老花镜 / 怀表 / 戒指 / 翡翠吊坠 / 古币",
            "detection_abilities": "灵气浓度（PPM/m³）/ 灵物等级 / 古玩真伪 / 风水气场 / 命格 / 因果业力",
            "rarity_warnings": "市场假货 99% / 真品万分之一 / 国宝级千万分之一 / 主角靠它'捡漏'起家",
            "narrative_uses": "都市低武起点 / 鉴宝流 + 都市修仙交叉 / 主角古玩市场捡漏 → 第一桶金 → 修炼资源",
            "famous_examples": "《捡宝生涯》丰宁鉴宝镜 / 《天才相师》金手指 / 《重生之鉴宝大师》",
            "twist_design": "感应器消耗主角寿元 / 感应器有意识 / 感应器是上古真人之灵",
            "activation_keywords": ["鉴宝", "捡漏", "灵气感应", "古玩", "金手指", "天眼", "透视"],
        },
        source_type="llm_synth", confidence=0.91,
        source_citations=[llm_note("都市修仙 + 鉴宝流标配")],
        tags=["都市修仙", "鉴宝流", "都市低武", "金手指"],
    ),
    # 西方奇幻 - 魔法戒指
    MaterialEntry(
        dimension="device_templates", genre="西方奇幻",
        slug="device-magic-ring",
        name="魔法戒指（One Ring / Vanyar Ring）",
        narrative_summary="西方奇幻 + 北欧神话经典物。"
                          "戒指承载魔法 / 诅咒 / 力量。"
                          "托尔金 LOTR 至尊魔戒 = 经典原型。",
        content_json={
            "ring_archetypes": "权力之戒（诱惑性 / 腐化性）/ 隐身戒（gyges 神话）/ 元素戒（火/水/风/土）/ 智慧戒（增加魔力上限）/ 召唤戒（召唤魔仆）",
            "lotr_inspiration": "至尊魔戒 + 三精灵戒 + 七矮人戒 + 九人类戒 + 索隆造的至尊戒可控制其他",
            "side_effects": "腐化心智（魔戒 → 史矛革性格）/ 寿命延长但灵魂衰朽 / 上瘾 / 召唤索隆之眼",
            "narrative_uses": "毁灭线（必须毁掉戒指 = 主线）/ 收集线（集齐 N 个戒指）/ 守护线（戒指压制魔王封印）",
            "famous_examples": "至尊魔戒（LOTR）/ 安杜里尔（Aragorn 之剑）/ 北欧诸神安德瓦利金戒（Sigurd 神话）",
            "modern_subversions": "《七人会议》/ 《魔戒》后现代解构 / 戒指变成手机戒指（轻奇幻）",
            "activation_keywords": ["魔戒", "至尊魔戒", "权力之戒", "腐化", "索隆", "毁灭"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[wiki("魔戒", ""), llm_note("Tolkien")],
        tags=["西方奇幻", "魔法物", "经典"],
    ),
    # 西方奇幻 - 魔杖 / 法杖
    MaterialEntry(
        dimension="device_templates", genre="西方奇幻",
        slug="device-magic-wand-staff",
        name="魔杖 / 法杖（Wand / Staff）",
        narrative_summary="西方奇幻施法核心媒介。"
                          "Harry Potter 把魔杖文化推到极致。"
                          "魔杖与施法者绑定（魔杖选择主人）。",
        content_json={
            "wand_components": "杖芯 = 凤凰羽 / 独角兽尾 / 龙心 / 蛇怪牙 / 木材 = 接骨木 / 冬青 / 紫杉",
            "wand_personality": "魔杖有'意识' / 选择主人 / 跟随胜利者 / 第一任主人死后忠诚转移",
            "staff_design": "巫师法杖 = 增幅 + 储能 + 共振 / 顶端水晶 / 木质雕刻符文 / 高阶法杖能召唤本源",
            "schools_of_magic": "8 类：变形 / 召唤 / 附魔 / 占卜 / 幻术 / 牧术 / 防护 / 黑魔法（DnD/HP传统）",
            "narrative_uses": "主角换杖 = 成长节点 / 毕业杖 / 死亡圣器接骨木魔杖 = 终极目标",
            "famous_examples": "Harry 的凤凰尾魔杖 / Voldemort 老魔杖 / Gandalf 的法杖 / Merlin 法杖 / Doctor Strange 的圣母玛利亚之手",
            "activation_keywords": ["魔杖", "法杖", "杖芯", "霍格沃茨", "接骨木", "凤凰羽"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("Harry Potter + DnD")],
        tags=["西方奇幻", "魔法物"],
    ),
    # 科幻 - 超光速引擎
    MaterialEntry(
        dimension="device_templates", genre="科幻",
        slug="device-warp-drive",
        name="曲速引擎 / 超光速跃迁器",
        narrative_summary="科幻文明跨星系级别物。"
                          "把不可能的星际旅行变可能。"
                          "Star Trek warp / Star Wars hyperspace / 三体曲率驱动 = 三大流派。",
        content_json={
            "physics_basis": "曲率驱动（Alcubierre / 三体）/ 跃迁门 / 虫洞 / 超空间 / 亚空间（Warhammer）/ 折跃航行（玛克罗斯）",
            "warp_types": "Star Trek 9 速级（Warp 1-10）/ SW 0.4级（点跳）/ 三体光速 / Battlestar 跃迁瞬移 / Mass Effect 质量效应中继器",
            "energy_costs": "需要消耗整颗恒星 / 反物质 / 第零物质 element zero / 灵能 / 黑洞蒸发能",
            "side_effects": "跃迁后船员失忆 / 时间膨胀（百年航行）/ 跃迁错乱遗失到未知象限 / 召唤亚空间恶魔（Warhammer）",
            "narrative_uses": "推动星际旅行 / 文明交流 / 探索未知空间 / 战争超光速火力",
            "famous_works": "Star Trek 曲速引擎 / Star Wars hyperdrive / Mass Effect 中继器 / 三体曲率驱动 / Dune 折越 / Warhammer 40k 亚空间",
            "activation_keywords": ["曲速", "超光速", "跃迁", "虫洞", "warp", "hyperspace", "曲率驱动"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("科幻硬核设定")],
        tags=["科幻", "硬科幻", "星际"],
    ),
    # 科幻 - 神经接口
    MaterialEntry(
        dimension="device_templates", genre="科幻",
        slug="device-neural-interface",
        name="神经接口 / 脑机连接（Neural Link）",
        narrative_summary="赛博朋克核心物件。"
                          "人脑直连数字世界 / 电子设备 / 网络。"
                          "Cyberpunk 2077 / Matrix / Neuromancer 经典设定。",
        content_json={
            "interface_types": "Cyberpunk 'Wetware' 神经端口 / Matrix 后脑插孔 / Neuralink 微创手术植入 / Mass Effect biotic implant",
            "capabilities": "VR 完全沉浸 / 即时学习（下载知识包）/ 情感同步 / 死亡 = 数据上传永生 / 思维直接控物 / 大脑黑客攻击",
            "subtype_genres": "VR 沉浸流 / 黑客赛博朋克 / 全息进化流 / AI 觉醒流 / 主仆控制流",
            "side_effects": "脑机分裂 / 数字依赖症 / 黑客入侵 = 脑死 / 灵肉分离 / 电子上瘾",
            "narrative_uses": "主角失忆 + 神经接口被反派改写 / VR 网游成现实 / 数字孪生背叛主角 / 死后意识上传",
            "famous_works": "Matrix / Cyberpunk 2077 / Neuromancer / SAO / 头号玩家 / 攻壳机动队",
            "activation_keywords": ["神经接口", "脑机", "Neural Link", "赛博朋克", "wetware", "VR沉浸"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("赛博朋克 + 脑机接口")],
        tags=["科幻", "赛博朋克", "近未来"],
    ),
    # 末世 - 末世指南书
    MaterialEntry(
        dimension="device_templates", genre="末世",
        slug="device-doomsday-survival-handbook",
        name="末世指南书 / 异变图鉴",
        narrative_summary="末世流主角金手指。"
                          "提前知道末世走向 / 异变规律 / 安全区位置 / 资源点。"
                          "重生回末世前 + 自带记忆 = 经典套路。",
        content_json={
            "info_categories": "丧尸进化时间表 / 异兽刷新地点 / 安全区分布 / 资源点（药店/超市/军火库）/ 末世大事件时间表 / 关键人物名单",
            "rarity_levels": "1星（民间小道）/ 5星（国家级）/ 10星（神级 = 含未来 100 年走向）",
            "narrative_uses": "重生类必备 / 主角靠图鉴在末世第一周抢占资源 / 第一个月拉队伍 / 第一年建基地 / 后续是图鉴预言之外",
            "twist_pattern": "图鉴写错关键事件 / 图鉴是反派精心铺设的诱饵 / 图鉴预言到主角的死",
            "famous_examples": "《丧尸末日》/ 《末世图鉴》/ 《重生在末世》/ 《全球进化》",
            "activation_keywords": ["末世", "图鉴", "重生", "丧尸", "异变", "安全区"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("末世重生流标配")],
        tags=["末世", "重生", "金手指"],
    ),
    # 都市 - 古董相机 (悬疑)
    MaterialEntry(
        dimension="device_templates", genre="都市",
        slug="device-cursed-camera",
        name="古董相机 / 诅咒拍立得",
        narrative_summary="都市悬疑灵异道具。"
                          "拍出的照片显现真相 / 死亡预兆 / 灵体。"
                          "类似经典都市怪谈。",
        content_json={
            "powers": "拍出真相（伪装人显原形）/ 拍出未来 1-3 天 / 拍出鬼影 / 拍出过去某瞬间 / 拍 = 锁定灵魂",
            "side_effects": "每拍一次缩短主角 1 天寿元 / 每拍一次相机变冷 / 拍 7 张后毁灭",
            "narrative_uses": "悬疑探案主角 / 民俗灵异 / 都市怪谈 / 主角靠相机一一识破都市迷案",
            "twist_design": "相机本身是凶器 / 相机有自己的目的（要主角拍指定对象）/ 相机最后一张拍主角自己的死",
            "famous_examples": "《不思议游戏》《奇怪 Q》《盗墓笔记》系列 / 都市恐怖题材",
            "activation_keywords": ["古董相机", "拍立得", "诅咒物", "怪谈", "灵异", "悬疑"],
        },
        source_type="llm_synth", confidence=0.89,
        source_citations=[llm_note("都市灵异道具")],
        tags=["都市", "悬疑", "灵异", "怪谈"],
    ),
    # 历史 - 兵符 / 虎符
    MaterialEntry(
        dimension="device_templates", genre="历史",
        slug="device-tiger-tally",
        name="虎符（兵符）",
        narrative_summary="中国战国 - 唐代调兵权信物。"
                          "铜铸虎形，左右两半。"
                          "君王留左半，将军持右半，合则可调兵。",
        content_json={
            "physical_design": "铜铸 / 长 7-12cm / 虎形 / 左右两半 / 接合处错齿设计 / 内刻铭文（部队番号、调动权限）",
            "real_examples": "杜虎符（陕西博物馆）/ 阳陵虎符 / 战国楚秦兵符 / 唐代鱼符（演变形态）",
            "key_use": "信陵君窃符救赵（《史记》战国四公子之一） = 经典夺符故事 / 凭虎符调动十万军是底层制度",
            "narrative_uses": "宫斗 / 权谋必备 / 失虎符 = 失兵权 = 政治死亡 / 偷虎符 / 伪造虎符 / 虎符争夺 = 政变核心",
            "twist_design": "假虎符（看上去对但有暗记）/ 三合虎符（皇帝 + 太子 + 老臣）/ 虎符附带咒文",
            "famous_works": "《大秦赋》《赵氏孤儿》《信陵君》题材 / 历史正剧 / 历史爽文",
            "activation_keywords": ["虎符", "兵符", "调兵", "信陵君", "窃符救赵", "权谋"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[wiki("虎符", "调兵信物"), llm_note("历史正剧标配")],
        tags=["历史", "权谋", "古代"],
    ),
    # 通用 - 时光怀表
    MaterialEntry(
        dimension="device_templates", genre=None,
        slug="device-time-pocket-watch",
        name="时光怀表 / 时间逆流物",
        narrative_summary="时间题材核心物。"
                          "倒流时间 / 暂停时间 / 看到过去/未来。"
                          "适合穿越 / 重生 / 时间循环 / 命定恋人题材。",
        content_json={
            "powers": "倒流 1 分钟 / 倒流 24 小时 / 暂停 5 秒 / 看见 1 周后 / 替换两人灵魂 / 跳到平行时空",
            "limitations": "次数有限（每天 1 次 / 每周 1 次）/ 反噬寿元 / 倒流后失忆 1 小时 / 表毁则主角同毁",
            "narrative_uses": "时间循环类（《明日边缘》）/ 重生类（《步步惊心》）/ 救赎类（《寻梦环游记》）",
            "twist_design": "怀表是反派给的 / 怀表是死神物 / 怀表越用使用者越接近死亡 / 怀表是上一任失败者遗留",
            "famous_examples": "《时间机器》/ 《Time Bandits》/ 《明日边缘》/ 《关于时间的一切》/ 《彗星来的那一夜》",
            "activation_keywords": ["时光", "怀表", "倒流", "时间循环", "穿越", "重生"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("时间题材道具")],
        tags=["通用", "时间", "穿越", "重生"],
    ),
    # 武侠 - 神兵谱
    MaterialEntry(
        dimension="device_templates", genre="武侠",
        slug="device-divine-weapon-ranking",
        name="兵器谱 / 神兵榜",
        narrative_summary="武侠传统设定。"
                          "江湖排名前 100 的兵器。"
                          "古龙《兵器谱》→ 金庸《天龙八部》→ 网络武侠。",
        content_json={
            "gulong_top_ten": "1) 天机棒 / 2) 子母龙凤环 / 3) 离别钩 / 4) 多情环 / 5) 七巧玲珑 / 6) 龙凤双环 / 7) 沉香木镖 / 8) 嵩阳铁剑 / 9) 玉箫 / 10) 飞凤剑",
            "wuxia_classics": "倚天剑 / 屠龙刀 / 玄铁重剑 / 桃花剑 / 越女剑 / 紫金剑 / 干将莫邪 / 鱼肠剑（古代）",
            "ranking_rules": "兵器谱不是看锋利 / 是看主人功夫 + 兵器特性 + 用法巧妙度 / 主人换 = 排名变",
            "narrative_uses": "武侠新人挑战榜 = 经典桥段 / 主角入榜 = 成名 / 兵器换主 = 江湖大事 / 兵器谱排第二的杀手 = 永远的目标",
            "famous_works": "古龙《多情剑客无情剑》兵器谱 / 金庸 / 黄易 / 温瑞安 / 大唐双龙",
            "activation_keywords": ["兵器谱", "神兵榜", "屠龙刀", "倚天剑", "玄铁剑", "古龙"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("古龙兵器谱"), wiki("兵器谱", "")],
        tags=["武侠", "古风", "神兵"],
    ),
    # 通用 - 信物 (爱情)
    MaterialEntry(
        dimension="device_templates", genre=None,
        slug="device-love-token",
        name="爱情信物（玉佩 / 红绳 / 项链）",
        narrative_summary="言情类核心物。"
                          "信物 = 情感载体 + 身份认证 + 误会道具。"
                          "古言常用玉佩 / 现言常用项链 + 戒指。",
        content_json={
            "ancient_types": "玉佩（成对 / 鸳鸯佩）/ 香囊 / 红绳 / 玉镯 / 步摇 / 玉簪 / 头钗 / 同心结",
            "modern_types": "项链 / 戒指 / 手表 / 信物钥匙 / 定情物纹身 / 共同书 / 老照片",
            "narrative_uses": "认亲（失散母女凭玉佩相认）/ 婚约信物 / 告别留念 / 定情物 / 错认（玉佩落别人手 → 误会）/ 灾难前留念",
            "tragic_uses": "信物伴尸首归来 / 信物碎了 = 缘分尽 / 信物给了别人 = 心意转",
            "trope_subversion": "信物是假的（替身爱情）/ 信物是诅咒（持有者必死）/ 信物原本是另一个人的（重生类）",
            "famous_examples": "《步步惊心》玉镯 / 《知否》玉佩 / 《延禧攻略》荷包 / 《何以笙箫默》项链",
            "activation_keywords": ["信物", "玉佩", "项链", "鸳鸯佩", "定情物", "认亲"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("言情核心道具")],
        tags=["通用", "言情", "古言", "现言"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...")
    inserted = 0
    errors = 0
    by_genre: dict = {}
    by_dim: dict = {}
    async with session_scope() as session:
        for entry in ENTRIES:
            try:
                await insert_entry(session, entry, compute_embedding=True)
                inserted += 1
                by_genre[entry.genre or "(通用)"] = by_genre.get(entry.genre or "(通用)", 0) + 1
                by_dim[entry.dimension] = by_dim.get(entry.dimension, 0) + 1
            except Exception as e:
                print(f"  ✗ {entry.slug}: {e}")
                errors += 1
        await session.commit()
    print()
    print(f"\nBy genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n✓ {inserted} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
