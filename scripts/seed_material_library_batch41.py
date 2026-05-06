"""Batch 41: niche-genre device_templates + power_systems

Fills under-served dimensions and niche genres:
- 直播 / 娱乐圈 / 末日 / 赛博朋克 / 灵异 / 校园 specific gear & power tiers
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry


def llm_note(note: str) -> dict:
    return {"source": "llm_synth", "note": note}


def wiki(title: str, note: str = "") -> dict:
    return {"source": "wikipedia", "title": title, "note": note}


ENTRIES: list[MaterialEntry] = [
    # ---------- device_templates (8) ----------
    MaterialEntry(
        dimension="device_templates", genre="娱乐圈",
        slug="device-streaming-magic-link",
        name="直播金手指：流量超级链接 / 弹幕权杖",
        narrative_summary="给草根主播一个能短时间引爆流量的金手指：可能是任意人都看到的弹幕、能让观众入戏的麦克风、或者能预测下一个热点的笔记本。爽点是从素人到顶流的速度感。",
        content_json={
            "device_categories": "1) 弹幕权杖（说出去的弹幕带特效，观众必看）/ 2) 预言笔记本（能写下三天后的热搜）/ 3) 共情麦克风（开口必让观众落泪/大笑）/ 4) 系统面板（直播打赏=灵气=升级）",
            "growth_curve": "粉丝 0 → 10 万 → 100 万 → 千万顶流；每阶段金手指能力解锁",
            "limit_design": "金手指有冷却（每天只能用 N 次）、有副作用（用多了主角自己嗓子哑/变笨）、需要充能（看真心粉丝弹幕充电）",
            "anti_cliche": "不要直接抄'快本流量明星'式金手指；要让金手指本身有瑕疵，主角必须靠真才实艺补足",
            "activation_keywords": ["直播金手指", "流量", "弹幕权杖", "顶流", "升级"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("观察唐家三少《光之子》《天珠变》流量爆发型升级模板")],
        tags=["device", "娱乐圈", "直播", "金手指"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="末日",
        slug="device-doomsday-bunker-key",
        name="末日金手指：避难所主控钥匙 / 末日商城",
        narrative_summary="末世金手指典型物：能开启某个上古避难所、随身仓库、或绑定到某个'末日商城'系统，让主角拥有比其他幸存者更高的资源密度。要避免变成纯堆砌。",
        content_json={
            "device_categories": "1) 避难所主控钥匙（持有者能打开 N 个隐藏基地）/ 2) 末日商城面板（用功勋点换物资）/ 3) 上古地图（标注资源点）/ 4) 神级背包（容量+保鲜）/ 5) 自动农场芯片",
            "limit_rules": "钥匙每开一次需要消耗主角一段记忆；商城物资有 24h 冷却；地图随时间失效",
            "differentiation": "可以让金手指本身'有价格'：每用一次主角就少一年寿命/失去一段感情/变得更冷漠 — 角色弧线靠这个推",
            "anti_cliche": "不要让金手指=无脑白嫖；要让金手指=哲学选择：人性 vs 生存",
            "activation_keywords": ["末日", "避难所", "末日商城", "末日金手指", "幸存者"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("末日类作品共同模板：方想《卡徒》、迷路的龙《无限恐怖》派生")],
        tags=["device", "末日", "金手指", "稀缺资源"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="赛博朋克",
        slug="device-cyber-implant-deck",
        name="赛博植入物：神经接口卡组 / 黑客脑机",
        narrative_summary="赛博朋克世界的金手指通常是植入物：神经接口、视网膜 HUD、肌肉强化骨架、可换记忆芯片。每个植入物都有副作用（CPU 过热、人格碎片化、企业追踪）。要让科技感+代价感并存。",
        content_json={
            "implant_categories": "1) Cyberdeck 神经接口（黑客必备）/ 2) Synapse Booster 反应加速 / 3) Memory Vault 记忆备份芯片 / 4) Subdermal Armor 皮下装甲 / 5) Optical Suite 视网膜 HUD",
            "side_effects": "CPU 过热=痴呆 / 反应加速=帕金森 / 记忆备份=人格分裂 / 皮下装甲=慢性疼痛 / HUD=偏头痛+企业追踪",
            "social_layer": "二手植入物市场 / 黑医生 / 企业回收 / 街头改装铺 / 工会反植入派",
            "anti_cliche": "不要把植入物只写成'更强'；要写人格被慢慢替换的恐惧。参考 Cyberpunk 2077 V/Silverhand 关系",
            "activation_keywords": ["赛博朋克", "植入物", "Cyberdeck", "神经接口", "side-effect"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Cyberpunk_2077"), llm_note("William Gibson《Neuromancer》植入物范式")],
        tags=["device", "赛博朋克", "植入物", "代价"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="灵异",
        slug="device-occult-tools-maoshan",
        name="灵异法器：茅山八件套 / 出马仙堂口",
        narrative_summary="灵异/民俗题材的标配法器箱：桃木剑、朱砂、黄表纸、铜钱剑、师爷令牌、算盘、香炉、罗盘。每件都有具体用法和禁忌。",
        content_json={
            "tools": "1) 桃木剑（斩鬼+开光，每月一次）/ 2) 朱砂（画符必备，需童子血）/ 3) 黄表纸（写符咒，三日失效）/ 4) 铜钱剑（古钱铸造，对付邪祟）/ 5) 师爷令牌（家传，不能借人）/ 6) 罗盘（找龙脉/避煞）/ 7) 香炉（请神/送神）/ 8) 八卦镜（避邪反弹）",
            "taboos": "桃木剑不能见血 / 朱砂受潮失效 / 师爷令牌借人=师爷不再保佑 / 香炉断香=灾祸",
            "out_ma_xian_setup": "出马仙堂口：四大门+五大家（胡黄灰白柳）+师傅传承 + 弟子+客户 — 每个角色有固定职位",
            "anti_cliche": "不要纯写'桃木剑一砍鬼就死'；要写法器损坏/失效/反噬的真实代价。参考 Mr. Vampire 系列+《白蛇传说》",
            "activation_keywords": ["灵异", "茅山", "出马仙", "桃木剑", "法器"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Maoshan_Sect"), wiki("Chinese_folk_religion"), llm_note("天下霸唱《鬼吹灯》民俗法器体系")],
        tags=["device", "灵异", "法器", "茅山", "出马仙"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="穿书",
        slug="device-transmigration-system-panel",
        name="穿书系统：任务面板 / 男主好感度条",
        narrative_summary="穿书必备金手指：系统面板。任务、积分、好感度、剧情值、退场倒计时、原女主光环。每个数值都要有具体的'刺激'让读者上瘾。",
        content_json={
            "panel_modules": "1) 主线任务（攻略/反派/救男主）/ 2) 支线（捡碎片/收集物品）/ 3) 好感度条（男主/女主/反派）/ 4) 剧情完成度（影响奖励）/ 5) 倒计时（不完成=魂飞魄散）/ 6) 商城（买道具）/ 7) 礼包（每天签到）",
            "system_personality": "毒舌型/呆萌型/严肃型/腹黑型 — 系统人格要有反差萌",
            "constraint": "系统不能直接开挂；只能给情报+道具+任务奖励；最终要靠主角自己谈恋爱/打反派",
            "anti_cliche": "不要让系统全程嘴炮+主角全程幸运；要让主角和系统冲突（主角不愿做任务/系统强制）",
            "activation_keywords": ["穿书", "系统", "任务面板", "好感度", "剧情值"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("吉祥夜《国民男神宠上瘾》、墨香铜臭《魔道祖师》穿书衍生模板")],
        tags=["device", "穿书", "系统", "任务"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="种田",
        slug="device-farming-spatial-pouch",
        name="种田金手指：随身空间 / 古井泉水",
        narrative_summary="种田流的标配：随身空间、古井泉水、灵泉、神奇种子。让主角在贫瘠环境快速翻身，但要避免堆数据。",
        content_json={
            "spatial_categories": "1) 随身空间（独立时空，可种地+养灵兽）/ 2) 古井泉水（饮用治病/浇地增产）/ 3) 神奇种子（一夜成熟/异果异粮）/ 4) 灵泉（修仙/洗髓）/ 5) 商城（卖现代物品）",
            "limit_design": "空间初始 1 亩 → 灵泉解锁 → 时间流速变化（外面 1 天=空间 1 周）；不能直接收纳活物",
            "social_constraint": "金手指必须保密：被发现=被绑/被夺/被审；婆媳/邻居/族长压力都要逼真",
            "anti_cliche": "不要纯堆'空间产小麦每亩 2000 斤'数据；要写主角因为空间反而和原生家庭/村庄/古代社会产生冲突",
            "activation_keywords": ["种田", "随身空间", "灵泉", "古井泉水", "神奇种子"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("种田文典型模板：寻找失落的爱情《田园悍妇》、希行《君九龄》")],
        tags=["device", "种田", "空间", "金手指"],
    ),
    MaterialEntry(
        dimension="device_templates", genre="校园",
        slug="device-campus-genius-ledger",
        name="校园金手指：天才笔记本 / 学神记忆卡",
        narrative_summary="校园文金手指：能让差生秒变学神的笔记本、过目不忘药水、考试预知系统、全校排名 boost。要避免太逆天。",
        content_json={
            "device_categories": "1) 学神笔记本（写一遍=记一辈子）/ 2) 过目不忘药水（24h 强记）/ 3) 考题预知系统（提前 1 周）/ 4) 排名 boost 卡（按月/按学期触发）/ 5) 心声共鸣麦（听到老师内心讲解）",
            "limit_rules": "笔记本只能写不能复制；药水有副作用（智商透支）；预知会被反向调换试卷题目；排名 boost 触发=同学全部失利（道德困境）",
            "social_layer": "金手指会引来：班主任怀疑/竞赛组挖人/校长警惕/同学嫉妒；要写成长不靠金手指本身",
            "anti_cliche": "不要纯写'学神考试 100 分爽文'；要写主角因金手指失去正常的学习成就感+友情",
            "activation_keywords": ["校园", "学神", "天才笔记本", "考试", "金手指"],
        },
        source_type="llm_synth", confidence=0.8,
        source_citations=[llm_note("校园文学神模板：饶雪漫《沙漏》、辛夷坞青春题材的当代改造")],
        tags=["device", "校园", "学神", "金手指"],
    ),
    MaterialEntry(
        dimension="device_templates", genre=None,
        slug="device-fragmented-relic-quest",
        name="碎片化神器：集齐 N 块拼图",
        narrative_summary="跨题材通用模板：神器分散为 N 块碎片，主角必须收集、过程中触发各种支线、boss、阵营冲突。仙侠/玄幻/西方奇幻/末日都通用。",
        content_json={
            "structure": "1 块=入门，3 块=升级，5 块=破局，7 块=逆天，全集齐=终极对决",
            "carrier_examples": "仙侠=神器残卷/玄幻=神器零件/西方奇幻=圣物碎片/末日=疫苗 N 阶段/赛博=核心代码模块",
            "support_arcs": "每块碎片对应一段独立故事+一个反派+一段关系；可以用作章回式结构骨架",
            "anti_cliche": "不要让收集变成纯打怪流水账；每块碎片的获取要触发主角内心选择+关系变化",
            "activation_keywords": ["碎片", "神器", "集齐", "拼图", "章回结构"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("Dragonball, 哈利波特死亡圣器, 复仇者无限手套通用模板")],
        tags=["device", "通用", "结构", "碎片"],
    ),

    # ---------- power_systems (7) ----------
    MaterialEntry(
        dimension="power_systems", genre="赛博朋克",
        slug="power-cyberpunk-implant-tiers",
        name="赛博朋克：植入物等级 / 街头到公司",
        narrative_summary="赛博朋克升级=植入更多/更好的义体；从街头改装铺（次品）→ 黑医生（中端）→ 大公司军用级（顶端）。每级有副作用与社会代价。",
        content_json={
            "tiers": "1) Street（街头铺，质量差）/ 2) Black Clinic（黑医生，中端）/ 3) Corp Standard（公司标）/ 4) Corp Elite（公司精英特供）/ 5) Mil-Spec（军用，禁止流通）/ 6) Prototype（实验型，有人格替换风险）",
            "side_effects_by_tier": "Street: 感染+短命 / Black: 控制权被医生扣押 / Corp: 公司监控 / Mil-Spec: 思维洗脑 / Prototype: 人格逐渐被替换",
            "humanity_cost": "每升一级义体, '人性条' -10%；归零=Cyberpsycho（赛博精神病，全城追杀）",
            "anti_cliche": "不要把升级写成纯爽；要让主角每升一级失去一段感情/记忆/朋友。参考 Cyberpunk 2077 关系系统",
            "activation_keywords": ["赛博朋克", "植入物", "humanity", "Cyberpsycho", "Mil-Spec"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Cyberpunk_(role-playing_game)"), wiki("Cyberpunk_2077")],
        tags=["power_systems", "赛博朋克", "义体", "humanity"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="末日",
        slug="power-doomsday-evolution-tiers",
        name="末日：进化等级 / 感染层级",
        narrative_summary="末世进化体系：T 级 → 一阶 → 二阶 → ...→ 神级；分肉体派、异能派、混合派。每阶段有'晋级条件'+'感染风险'。",
        content_json={
            "evolution_branches": "肉体派（力量/敏捷/防御）/ 异能派（火/冰/雷/控制/治疗/精神）/ 混合派（半进化兽人）/ 智能派（解析丧尸的能力）",
            "tier_thresholds": "T0 普通人 / T1 觉醒（吃晶核）/ T2 一阶（吞噬 100 颗低阶）/ T3 二阶（吃 boss 晶核）/ T4 三阶（突破 / 雷劫式异能净化）/ T5 神级（不死之身）",
            "infection_risk": "异能觉醒可能携带 X 病毒；晶核吞噬过量=异化；杂食肉体派可能丧尸化",
            "social_tier": "T0 = 普通幸存者 / T1-T2 = 战士 / T3 = 基地高层 / T4 = 区域王 / T5 = 神话级（每个区域 ≤ 1 人）",
            "anti_cliche": "不要纯堆数据；要写每次进化的代价（人格变化/食欲改变/朋友疏远）",
            "activation_keywords": ["末日", "进化", "异能", "晶核", "丧尸"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("末日流标杆：方想《卡徒》、Z 世代《无限恐怖》末日卷模板综合")],
        tags=["power_systems", "末日", "进化", "异能"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="灵异",
        slug="power-occult-grading-mishu",
        name="灵异：法力等级 / 茅山+出马层级",
        narrative_summary="民俗灵异类的层级：童子（无法力）→ 黄册弟子 → 红册师 → 朱册祖师 → 神位（已成仙）。每段都有具体试炼。",
        content_json={
            "tiers": "1) 童子身（天生通灵但未学法）/ 2) 黄册弟子（受过开光/守护一位仙家）/ 3) 红册师（独立办堂）/ 4) 朱册祖师（带徒+收家族）/ 5) 升座（修成飞升半仙）/ 6) 神位（道教神祇）",
            "testing_rituals": "童子→黄册：开堂、过仙桥；黄册→红册：渡天劫小妖；红册→朱册：处理灾煞案件 N 起；朱册→升座：抗一次大灾；升座→神位：渡天劫",
            "school_branches": "茅山（驱邪）/ 出马（仙家）/ 武当（修真）/ 龙虎山（正一道）/ 全真（北宗）/ 闾山（赣地巫）",
            "anti_cliche": "不要纯写'桃木剑斩鬼' — 要写仙家附体的代价：性格扭曲/家族绝嗣/寿命减损",
            "activation_keywords": ["灵异", "茅山", "出马仙", "童子", "升座"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("Maoshan"), wiki("Quanzhen_School"), llm_note("民俗灵异常见层级综合")],
        tags=["power_systems", "灵异", "茅山", "出马"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="无限流",
        slug="power-infinite-flow-veteran-tiers",
        name="无限流：老兵等级 / 主神空间积分",
        narrative_summary="无限恐怖、深渊副本、塔防类的层级：新人 → 老兵 → 队长 → 大佬 → 半神 → 主神。每一阶都对应'活到的副本数+杀的怪+收的奖励'。",
        content_json={
            "tiers": "1) 新人（活下首个副本）/ 2) 老兵（活下 5 个副本，能带新人）/ 3) 队长（带队 3 局以上无团灭）/ 4) 大佬（活下噩梦级）/ 5) 半神（融合多个副本能力）/ 6) 主神（造副本/管空间）",
            "growth_system": "每副本=积分+任务奖励+恐怖点；积分换装备/血脉/技能/支线；恐怖点过低=主神惩罚",
            "death_rule": "死=魂飞魄散；除非有复活道具或主神承诺",
            "team_dynamic": "团队信任=资源共享 vs 独狼=高奖励但孤独；老兵会培养新人 or 把新人当肉盾",
            "anti_cliche": "不要每个副本都让主角无脑变强；要写主角变强后失去什么（道德/朋友/记忆）",
            "activation_keywords": ["无限流", "主神空间", "副本", "恐怖点", "老兵"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("zhttty《无限恐怖》、深海先生《诡秘之主》衍生无限流标杆")],
        tags=["power_systems", "无限流", "副本", "主神空间"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="机甲",
        slug="power-mecha-pilot-grades",
        name="机甲：驾驶员等级 / 战机型号",
        narrative_summary="机甲流的层级=驾驶员段位×机甲型号双轴。E 级机师 → SSS 级；机甲从工程型 → 量产型 → 试验型 → 神级。两轴交叉决定战力。",
        content_json={
            "pilot_grades": "E / D / C / B / A / S / SS / SSS — 每升一级需要击败上级 + 联邦认证",
            "mecha_grades": "工程型（民用）→ 量产型（军用标）→ 精英型（特种部队）→ 试验型（研究所）→ 神级（仅 7 台，传说）",
            "compatibility": "机师段位×机甲型号有匹配限制；E 机师强行驾驶神级=死；S 机师驾工程型=屈才但生还",
            "synchronicity_system": "驾驶员神经同步率 0-100%，超过 80%=灵魂注入机甲（强但易死）",
            "anti_cliche": "不要把驾驶员=纯爽文主角；要写同步率高=人格被机甲影响（暴躁/嗜战/记忆乱）",
            "activation_keywords": ["机甲", "驾驶员", "同步率", "SSS级", "试验型"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("Gundam UC、Evangelion、Code Geass、骷髅精灵《卡徒》机甲卷综合")],
        tags=["power_systems", "机甲", "驾驶员", "同步率"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="娱乐圈",
        slug="power-celebrity-fame-tiers",
        name="娱乐圈：流量等级 / 咖位体系",
        narrative_summary="娱乐圈题材的'修炼'其实是咖位升级：素人 → 路人 → 三线 → 二线 → 一线 → 顶流 → 影后 → 国宝。每一级都有具体的资源+受到的待遇差异。",
        content_json={
            "tiers": "1) 素人（试镜/选秀）/ 2) 路人（小角色配角）/ 3) 三线（主演网剧）/ 4) 二线（卫视主演）/ 5) 一线（影视双栖）/ 6) 顶流（流量+作品）/ 7) 影后/影帝（拿过 A 类奖）/ 8) 国宝级（殿堂级，少有作品但代表中国）",
            "resources_per_tier": "三线=三七分小代言；一线=代言主角；顶流=代言+综艺王座；影帝=艺术片自由+票房号召",
            "manipulation_levers": "粉丝、营销号、热搜、奖项、剧本、导演关系、资本资本、抄袭/塌房 — 每个都是杀招",
            "anti_cliche": "不要纯写'女主从素人三个月顶流'；要写每一级都有真实资源博弈+作品+实力支撑",
            "activation_keywords": ["娱乐圈", "咖位", "顶流", "影后", "塌房"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("Chinese_film_industry"), llm_note("墨宝非宝《一生一世》/酥油饼《娱乐圈梦想成真》咖位模板")],
        tags=["power_systems", "娱乐圈", "咖位", "顶流"],
    ),
    MaterialEntry(
        dimension="power_systems", genre="电竞",
        slug="power-esports-tier-progression",
        name="电竞：天梯段位 / 职业联赛体系",
        narrative_summary="电竞题材的硬核晋级：青铜→白银→黄金→白金→钻石→大师→宗师→王者；职业=低保→替补→主力→明星→世界冠军→传奇。两轴并行。",
        content_json={
            "ranked_ladder": "青铜/白银/黄金/白金/钻石/大师/宗师/王者 — 大众路径",
            "pro_ladder": "替补→首发→明星选手→冠军→FMVP→传奇/退役名宿",
            "esports_calendar": "春季赛→夏季赛→世界赛；每年有 2-3 个国际大奖",
            "team_economy": "工资+签字费+奖金+直播分成+广告代言；明星选手 vs 替补差 100 倍",
            "anti_cliche": "不要纯写'路人 0 → 王者 100 集'；要写训练量+team chemistry+伤病+心理压力",
            "activation_keywords": ["电竞", "天梯", "段位", "职业联赛", "FMVP"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("League_of_Legends"), wiki("Esports"), llm_note("蝴蝶蓝《全职高手》、《微微一笑很倾城》电竞标杆")],
        tags=["power_systems", "电竞", "段位", "职业"],
    ),
]


async def main() -> None:
    print(f"Seeding {len(ENTRIES)} entries...\n")
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}
    errors = 0
    async with session_scope() as session:
        for e in ENTRIES:
            try:
                await insert_entry(session, e, compute_embedding=True)
                by_genre[e.genre or "(通用)"] = by_genre.get(e.genre or "(通用)", 0) + 1
                by_dim[e.dimension] = by_dim.get(e.dimension, 0) + 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  ✗ {e.slug}: {exc}")
    print(f"\nBy genre:     {by_genre}")
    print(f"By dimension: {by_dim}")
    print(f"\n✓ {len(ENTRIES) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    asyncio.run(main())
