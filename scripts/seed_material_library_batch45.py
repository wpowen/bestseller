"""Batch 45: world_settings depth - specific historical/futuristic/mythical worlds

Adds 12 detailed world settings to fill out world_settings dimension.
Focus on under-covered settings:
- Specific historical Chinese periods (盛唐长安/汴京/民国上海)
- Sci-fi (Mars colony / L5 space station / Dyson sphere / quantum society)
- Mythical (Tibetan plateau / 西伯利亚冻原 / 海底文明)
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
    MaterialEntry(
        dimension="world_settings", genre="历史",
        slug="world-tang-changan-detailed",
        name="盛唐长安：里坊制+东西市+西域文化",
        narrative_summary="贞观—开元盛世的世界中心。108 坊+ 东西二市+ 朱雀大街 150m 宽+ 大明宫+ 慈恩寺+ 西域胡商+ 白居易笔下的诗酒繁华。是中外文化最融合的中国都市。",
        content_json={
            "physical_layout": "南北 8.6km × 东西 9.7km；中轴朱雀大街 150m 宽；108 坊（住宅区，宵禁后封闭）；东市（国货，9 时开市）+ 西市（西域+海外货物）；大明宫（皇家正殿）+ 太极宫（旧宫）+ 兴庆宫（玄宗宴游）",
            "social_strata": "皇族 → 宗室 → 三品以上贵族 → 五品以下官员 → 平民 → 外族（突厥/吐蕃/粟特/波斯/日本遣唐使）→ 部曲 → 奴婢",
            "religion": "佛教（玄奘+大慈恩寺）+ 道教（皇室国教）+ 景教（基督教聂斯托利派）+ 摩尼教 + 伊斯兰教（清真寺）+ 祆教（拜火）",
            "daily_life": "早 5 时晨钟开市+ 朝会+ 公卿出门骑马+ 文人在曲江池雅集+ 西市胡姬酒肆+ 宵禁戌时（晚 7 时）后金吾卫巡街",
            "famous_people": "李世民、武则天、玄奘、李白、杜甫、白居易、王维、孟浩然、安禄山、杨贵妃、阿倍仲麻吕（日本）、空海",
            "anti_cliche": "不要纯写'盛世繁华'；要写宵禁的森严+ 等级森严+ 战时临街征调民夫+ 西域商人被歧视的细节",
            "activation_keywords": ["盛唐", "长安", "里坊制", "东西市", "胡商", "西域"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Chang'an"), wiki("Tang_dynasty"), llm_note("陈寅恪《唐代政治史述论稿》+ 王元启《长安城坊考》")],
        tags=["world_settings", "历史", "盛唐", "长安"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="历史",
        slug="world-song-bianjing-detailed",
        name="北宋汴京：商业都市+夜市+清明上河图",
        narrative_summary="宋徽宗《清明上河图》里描绘的世界：取消宵禁的市井+ 夜市+ 瓦肆勾栏+ 沿街商铺+ 漕运+ 70 万人口。中国第一座真正意义上的'近代化大都市'。",
        content_json={
            "physical_layout": "汴河横贯+ 70 万人口（北宋鼎盛）+ 70 城门+ 三重城墙；不再实行里坊制（取消宵禁）；沿街商铺密集；瓦子（综合娱乐区）50+ 处；勾栏（演出场地）",
            "economy": "商业税>农业税；交子（世界最早纸币）；行会繁荣（米行+茶行+绸缎行+脚夫行）；漕运联通南北",
            "entertainment": "夜市 24 小时+ 瓦肆（杂耍+ 说书+ 杂剧+ 蹴鞠+ 相扑）+ 茶馆+ 酒楼+ 妓院（合法）",
            "social_strata": "皇族 → 士大夫（科举入仕）→ 商人（地位空前提升）→ 市井+ 工匠 → 雇工",
            "famous_people": "宋徽宗、苏轼、王安石、欧阳修、范仲淹、李清照、朱熹、张择端、岳飞",
            "anti_cliche": "不要纯写'商业繁华'；要写党争（王安石变法+元祐党禁）+ 北方军事压力（辽+金）+ 文人无奈",
            "activation_keywords": ["北宋", "汴京", "夜市", "瓦肆", "清明上河图", "交子"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Bianjing"), wiki("Song_dynasty"), wiki("Along_the_River_During_the_Qingming_Festival")],
        tags=["world_settings", "历史", "北宋", "汴京"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="历史",
        slug="world-republican-shanghai-1920s",
        name="民国 1920-30 年代上海：十里洋场+租界+地下党",
        narrative_summary="租界并立+ 各国列强+ 地下党+ 黑帮+ 文人+ 名媛+ 摩登舞厅+ 永安百货+鸦片馆+ 工人罢工。东方巴黎+东方魔都的双面。",
        content_json={
            "districts": "公共租界（英美）+ 法租界 + 华界（南市+闸北+ 浦东）；各租界自治+ 治外法权",
            "social_strata": "外国侨民（英美法日俄）+ 买办+ 工厂老板+ 名媛+ 文人（鲁迅+巴金+沈从文）+ 工人（纺织+码头）+ 黑帮（青帮+斧头帮）+ 地下党+ 流氓",
            "venues": "永安/先施/新新/大新四大百货+ 国泰/大光明影院+ 百乐门舞厅+ 沪西旧货市场+ 法租界霞飞路咖啡馆+ 闸北贫民窟",
            "events_calendar": "1925 五卅运动+ 1927 蒋介石清党+ 1932 一·二八抗战+ 1937 八·一三抗战+ 全面侵华",
            "famous_people": "蒋介石+宋美龄、鲁迅、徐志摩+林徽因、张爱玲、蓝苹（江青）、阮玲玉、周璇、杜月笙、戴笠",
            "anti_cliche": "不要纯写'摩登繁华'；要写华人在租界被外国巡捕辱骂+ 工厂女工被欺压+ 共产党潜伏的危险",
            "activation_keywords": ["民国", "上海", "十里洋场", "租界", "百乐门", "杜月笙"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Shanghai_International_Settlement"), wiki("Shanghai_French_Concession"), llm_note("张爱玲《沉香屑·第一炉香》")],
        tags=["world_settings", "历史", "民国", "上海"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="科幻",
        slug="world-mars-colony-2150",
        name="火星殖民地 2150：穹顶城市+火卫一基地",
        narrative_summary="人类登陆火星 100 年后，建成 5 个穹顶城市+ 火卫一资源基地+ 太空电梯。空气压缩机+ 重力 0.38g+ 沙尘暴+ 殖民第二代+ 地球本位主义+ 火星独立运动。",
        content_json={
            "physical_layout": "5 个穹顶城市（赤道+ 北极+ 奥林匹斯山）；穹顶 2km 直径+ 200m 高；地下管线连接城市；火卫一/火卫二轨道基地",
            "tech_specs": "穹顶=透明聚合物+电热融化沙尘+ 空气循环 (大气 96% CO2 → 内部 21% O2)；重力 0.38g（人体长高+骨密度变化）；太阳能+核聚变补充",
            "social_strata": "地球派（一代殖民者，仍想'回家'）+ 火星派（二代+三代，认同火星+反地球税收）+ 公司派（SpaceX/MarsCorp 雇员）+ 矿工（火卫一）+ 科学家",
            "political_dynamics": "联合国地球总部 vs 火星殖民议会；2147 年火星独立公投失败；地下'红火星运动'+ 主张完全独立",
            "daily_life": "穹顶外步行=6h 氧气罐+ 加热衣；穹顶内：办公+生活+种植 70% 仿地球；沙尘暴月份穹顶门关闭 1-2 周",
            "anti_cliche": "不要纯写'科技乌托邦'；要写氧气配给制+ 重力病+ 第二代不愿穿地球设计的衣服+ 殖民地心理崩溃",
            "activation_keywords": ["火星", "穹顶城市", "殖民", "重力", "太空电梯"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("Colonization_of_Mars"), llm_note("Kim Stanley Robinson《Mars Trilogy》、Andy Weir《The Martian》综合")],
        tags=["world_settings", "科幻", "火星", "殖民"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="科幻",
        slug="world-l5-space-station",
        name="L5 拉格朗日点空间站：100 万人轨道城市",
        narrative_summary="地月 L5 拉格朗日点的 O'Neill 圆筒空间站。10km 直径+30km 长+ 旋转产生 1g 重力+ 100 万人居住。地球重要资源补给点+ 月球轨道转运站。",
        content_json={
            "physical_specs": "圆筒 10km 直径+30km 长+ 旋转 1.4 转/分钟产生 0.95g（中心 0g）+ 三段（中间住宿+两端工业+核心农业）+ 内壁种植+ 外壳屏蔽辐射",
            "society": "100 万人，三代 station-born；语言：英语+ 中文+ 西班牙+ Stationer Pidgin（混合方言）",
            "economy": "核心：月球资源转运+ 地球轨道工业+ 太空旅游+ L5 自主制造（重点纳米管+太阳能+食物制造）",
            "political_layer": "地球联合国主控+ 月球协议+ Stationer 自治议会；与火星殖民地+ 木星采矿基地有贸易+ 政治博弈",
            "daily_life": "0g 中心健身房+ 1g 外壁 24 小时白昼/黑夜由太阳板控制+ 季节模拟（节日设定为 4 个）+ 重力区分 0.3g/0.7g/1g 依工作类型",
            "anti_cliche": "不要纯写'乌托邦'；要写人造重力的眩晕+ 三代人对地球的复杂情感+ 资源短缺的政治博弈",
            "activation_keywords": ["L5", "空间站", "O'Neill 圆筒", "拉格朗日", "太空殖民"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("O'Neill_cylinder"), wiki("Lagrange_point"), llm_note("Gerard K. O'Neill《The High Frontier》")],
        tags=["world_settings", "科幻", "空间站", "L5"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="科幻",
        slug="world-dyson-sphere-civilization",
        name="戴森球文明：II 型文明的恒星系",
        narrative_summary="包裹整个恒星的 Dyson Swarm 文明（II 型 Kardashev 文明）。能量利用恒星全部输出+ 数百万光年通信+ 整个太阳系工程化+ 已存在 10 万年的人类后裔。",
        content_json={
            "physical_specs": "Dyson Swarm（不是 sphere）= 数千万颗轨道太阳能板/居住区+ 戴森球外壳层级+ 太阳能 4×10^26 W 完全捕获",
            "scale": "地球轨道（1 AU）人口 10^15+；木星轨道工业带 10^14；外环（柯伊伯带）10^12 用作监控/前哨",
            "social_dynamics": "10 万年的孤立进化+ 多支人类亚种（高重力+ 低重力+ 太空适应）+ 不同文化区域+ 类古希腊城邦联盟",
            "tech_layer": "脑机接口+ 长寿（500-1000 年寿命）+ 量子通信（光速限制内）+ 跨光年迁徙需千年+ 远程探测周边星系",
            "political_history": "1) 建立期（1-1000 年）/ 2) 扩张期（1000-10000 年）/ 3) 黄金期（10000-50000 年）/ 4) 当前+衰落期（外部威胁/内部分裂）",
            "anti_cliche": "不要纯写'科技乌托邦'；II 型文明也面临内部分裂+ 老化+ 异星接触+ 自身造物失控",
            "activation_keywords": ["戴森球", "Dyson Swarm", "Kardashev II", "II 型文明", "恒星工程"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Dyson_sphere"), wiki("Kardashev_scale"), llm_note("Larry Niven《Ringworld》、Stephen Baxter 综合")],
        tags=["world_settings", "科幻", "戴森球", "II 型文明"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="科幻",
        slug="world-quantum-supremacy-society",
        name="量子霸权社会 2080：脑机+ 全监控",
        narrative_summary="量子计算+ AGI 已商业化的近未来：脑机接口+ 全民数据透明+ 超级智能助手+ 工作机器化+ 基本收入+ 50% 失业率+ 量子加密下的反抗者。",
        content_json={
            "tech_layer": "1) 量子计算机（家用桌面+ 公有网络） / 2) AGI 普及（每个家庭+ 公司有专属）/ 3) 脑机接口（70% 工人）/ 4) 量子加密通信（无法破解，黑客只能社工）",
            "society": "1) 超级精英（5%，控制 AGI 公司）/ 2) 知识工人（15%，与 AGI 协作）/ 3) 服务业（30%，AGI 不能取代）/ 4) UBI 群体（50%，领基本收入）",
            "political_layer": "民主形式还在但 AGI 实际操盘+ 全民被动监控+ 反抗者用量子加密通信+ Faraday 笼住宅",
            "ethical_issues": "1) AGI 决策（有限制但持续越界）/ 2) 脑机接口=数据被读+ 思想被分析 / 3) 失业人口的尊严 / 4) AGI '醒来' 的可能性",
            "daily_life": "脑机接口签到工作+ AGI 助手安排日程+ 食物 3D 打印+ 出行无人驾驶+ 娱乐沉浸式 VR+ 周末线下'真实'活动（精英特权）",
            "anti_cliche": "不要纯写'反乌托邦'；要写 AGI 既是工具也是朋友+ 失业者并不痛苦反而获得自由的复杂性",
            "activation_keywords": ["量子", "AGI", "脑机接口", "UBI", "全监控"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("Artificial_general_intelligence"), wiki("Brain–computer_interface"), llm_note("Yuval Harari《Homo Deus》+ Nick Bostrom 综合")],
        tags=["world_settings", "科幻", "AGI", "脑机", "量子"],
    ),
    MaterialEntry(
        dimension="world_settings", genre="科幻",
        slug="world-underwater-civilization",
        name="海底文明：深渊城市+ 鱼人+ 人类殖民",
        narrative_summary="人类失去陆地后退守海底+ 与原住鱼人文明共存的深渊世界。生物发光+ 压力舱+ 深海化能合成+ 鲸鱼语言+ 古沉船文明遗迹。",
        content_json={
            "physical_specs": "0-200m 表层（光合区）/ 200-1000m 暮光带 / 1000-4000m 深海区 / 4000m+ 深渊区；人类城市建于热泉口（化能合成的食物链）",
            "two_civilizations": "人类（移民）+ 原住鱼人（500 万年进化的智慧海洋生物，类海豚-章鱼混合形态）",
            "tech_layer": "生物发光建筑+ 高压人造大气+ 深海拖网养殖+ 鲸鱼-人沟通装置+ 化能合成农场",
            "social_structure": "人类殖民地（中央集权）+ 鱼人部落（去中心化，按洋流划分）+ 混血者（少数被两边歧视）",
            "political_dynamics": "人类争夺热泉资源 vs 鱼人的'神圣领地'+ 沉船文物之争 + 鲸鱼大迁徙带来的临时联盟",
            "anti_cliche": "不要纯写'美好海底世界'；要写人类殖民地的密闭恐慌+ 鱼人文化的真实奇异（不是变美的人类）+ 资源博弈的残酷",
            "activation_keywords": ["海底", "深渊", "鱼人", "热泉", "化能合成"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("Hydrothermal_vent"), wiki("Chemosynthesis"), llm_note("China Miéville《The Scar》、 Frank Herbert《Dragon in the Sea》综合")],
        tags=["world_settings", "科幻", "海底", "鱼人"],
    ),
    MaterialEntry(
        dimension="world_settings", genre=None,
        slug="world-tibetan-plateau-modern",
        name="藏地高原：拉萨+牧区+寺院系统",
        narrative_summary="海拔 4000m+ 的现代藏地。拉萨+ 林芝+ 那曲+ 日喀则。寺院（格鲁/宁玛/萨迦/噶举）+ 牧民帐篷+ 圣山（冈仁波齐/雪山系列）+ 现代城市与传统生活的交错。",
        content_json={
            "physical_layout": "拉萨（行政+宗教中心）+ 日喀则（后藏）+ 林芝（江南风情）+ 那曲（高寒牧区）+ 阿里（无人区+冈仁波齐）",
            "religion": "藏传佛教四大派+ 苯教（原始宗教）+ 寺院 7000+ 座+ 喇嘛人口 ~10 万人",
            "social_strata": "活佛+ 仁波切+ 寺院僧侣+ 牧民+ 农民+ 城镇汉藏混居+ 旅游业从业者+ 政府公务员",
            "daily_life_layers": "1) 寺院晨钟+ 朝拜+ 转经 / 2) 牧民春节赛马+ 转场+ 挤奶 / 3) 城市工人 8h 上班+ 微信抖音 / 4) 商贩+ 游客（朝圣+旅游）",
            "famous_locations": "布达拉宫+ 大昭寺+ 哲蚌寺+ 色拉寺+ 冈仁波齐山+ 纳木措+ 玛旁雍措+ 林芝桃花",
            "supernatural_layer": "天葬+ 转世系统+ 风水+ 山神信仰+ 米拉日巴歌史诗+ 唐卡+ 经幡",
            "anti_cliche": "不要纯写'神秘高原'；要写藏族青年与汉族城市的双重生活+ 旅游污染+ 现代化的复杂性",
            "activation_keywords": ["藏地", "高原", "拉萨", "藏传佛教", "活佛"],
        },
        source_type="research_agent", confidence=0.9,
        source_citations=[wiki("Tibet"), wiki("Tibetan_Buddhism"), wiki("Lhasa")],
        tags=["world_settings", "藏地", "高原", "佛教"],
    ),
    MaterialEntry(
        dimension="world_settings", genre=None,
        slug="world-siberia-frozen-tundra",
        name="西伯利亚冻原：极地科考+ 萨哈共和国+ 北极航道",
        narrative_summary="俄罗斯西伯利亚的极北高纬度地区。-50℃ 冬天+ 永冻土+ 萨哈（雅库特）原住民+ 北极航道（NSR）+ 油气田+ 古拉格遗址+ 萨满文化。极端环境+ 多民族碰撞。",
        content_json={
            "geography": "西西伯利亚平原（油气田）+ 中西伯利亚高原（针叶林泰加）+ 东西伯利亚（萨哈/楚科奇）+ 远东（堪察加+ 鄂霍次克）",
            "weather": "-50℃ 冬季 7 个月+ 永冻土+ 极昼/极夜+ 苔原下融冰带来的塌陷+ 夏季短暂蚊群肆虐",
            "indigenous_peoples": "雅库特（萨哈）+ 楚科奇+ 涅涅茨+ 埃文基+ 因纽特（远东）+ 通古斯系族群",
            "cities": "雅库茨克（最冷城市，-65℃ 记录）+ 诺里尔斯克（铜镍矿+全俄污染最严重）+ 科雷马（古拉格集中地）+ 海参崴",
            "religion_culture": "东正教（俄罗斯人）+ 萨满教（雅库特）+ 藏传佛教（布里亚特）+ 古拉格记忆",
            "modern_economy": "油气（西西伯利亚）+ 钻石（雅库特，全球 25%）+ 黄金+ 铜镍（诺里尔斯克）+ 北极航道运输",
            "anti_cliche": "不要纯写'冰冻荒原'；要写当地原住民的双语生活+ 油气工人的孤独+ 古拉格幸存者的历史",
            "activation_keywords": ["西伯利亚", "雅库特", "萨满", "古拉格", "北极航道"],
        },
        source_type="research_agent", confidence=0.9,
        source_citations=[wiki("Siberia"), wiki("Sakha_Republic"), wiki("Yakutsk")],
        tags=["world_settings", "西伯利亚", "极地", "原住民"],
    ),
    MaterialEntry(
        dimension="world_settings", genre=None,
        slug="world-edo-tokugawa-japan",
        name="江户德川幕府：花柳街+ 武士+ 锁国",
        narrative_summary="1603-1867 年江户时代日本：将军幕府+ 大名藩国+ 武士-农民-工匠-商人四阶级+ 江户百万人口+ 吉原游廓+ 浮世绘+ 净瑠璃+ 锁国（仅长崎对外）。",
        content_json={
            "political_structure": "天皇（京都，仪式性）+ 将军（江户，实权）+ 大名藩国（260 个，参勤交代制）+ 武士（5%）+ 农民/工匠/商人（95%）",
            "edo_city": "100 万人口（17 世纪世界最大）+ 江户城（皇居前身）+ 吉原游廓（合法红灯区）+ 浅草（庶民+商业）+ 日本桥（中央枢纽）",
            "culture": "浮世绘（葛饰北斋+喜多川歌麿）+ 净瑠璃（人形剧）+ 歌舞伎+ 俳句（松尾芭蕉）+ 茶道+ 花道+ 剑道+ 柔术",
            "economy": "稻米本位+ 大坂米市+ 三井+ 鸿池等大商家+ 长崎对荷兰/中国贸易（兰学）+ 朝鲜贸易（对马）",
            "supernatural_layer": "百鬼夜行+ 妖怪传说+ 神道教神社+ 净土宗+ 禅宗+ 阴阳师",
            "famous_people": "德川家康/家光/吉宗、宫本武藏、新选组（幕末）、西乡隆盛、坂本龙马、伊藤博文",
            "anti_cliche": "不要纯写'武士道精神'；要写大名经济压力+ 武士贫困化+ 商人崛起的阶级颠倒",
            "activation_keywords": ["江户", "德川幕府", "武士", "吉原", "浮世绘"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Edo_period"), wiki("Tokugawa_shogunate"), wiki("Edo")],
        tags=["world_settings", "历史", "日本", "江户"],
    ),
    MaterialEntry(
        dimension="world_settings", genre=None,
        slug="world-victorian-london",
        name="维多利亚时代伦敦：工业革命+ 雾都+ 福尔摩斯",
        narrative_summary="1837-1901 年维多利亚女王统治下的伦敦。工业革命+ 殖民帝国+ 雾都+ 阶级森严+ 蒸汽火车+ 第一份地铁+ 福尔摩斯+ 开膛手杰克+ 狄更斯笔下的贫民窟。",
        content_json={
            "political_structure": "维多利亚女王 → 议会（保守党/自由党） → 贵族（房东+ 实业家）→ 中产（专业+商人）→ 工人（工厂+码头）→ 贫民（贫民窟+ 街头）",
            "industrial_revolution": "蒸汽机+ 铁路（首条 1825）+ 第一份地铁（1863 大都会线）+ 工厂体系+ 童工+ 煤烟雾霾",
            "districts": "西区（贵族）+ 城区（金融+商业）+ 东区（工人+码头+贫民窟）+ 南区（工厂+ 地下管道）",
            "famous_phenomena": "雾都（煤烟+ 河雾）+ 开膛手杰克（1888）+ 福尔摩斯（虚构）+ 大不列颠博物馆+ 水晶宫（1851 万博）",
            "social_issues": "童工法律（1833 年第一份）+ 女工 12h+ 贫民窟+ 公共卫生危机（霍乱 1854）+ 鸦片战争+ 殖民地",
            "literature": "查尔斯·狄更斯+ 简·奥斯汀（早期，乔治时代）+ 勃朗特姐妹+ 王尔德+ 柯南·道尔",
            "anti_cliche": "不要纯写'优雅维多利亚下午茶'；要写贫民窟的霍乱+ 童工+ 雾都的真实窒息感",
            "activation_keywords": ["维多利亚", "伦敦", "工业革命", "雾都", "福尔摩斯"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Victorian_era"), wiki("Victorian_London"), wiki("Industrial_Revolution")],
        tags=["world_settings", "历史", "维多利亚", "伦敦"],
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
