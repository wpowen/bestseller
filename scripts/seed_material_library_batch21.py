"""
Batch 21: Nature + geography + season motifs as locale_templates and
thematic_motifs. Mountain / river / sea / desert / snow / forest / weather
provide visual texture for any genre needing atmospheric scene anchoring.
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
    # 山岳
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-mountain-imposing",
        name="巍峨高山场景",
        narrative_summary="高山场景标准元素：拔地而起的崖壁 / 终年积雪山顶 / 云海缭绕 / 嶙峋怪石 / 古松倒挂。"
                          "天气剧变（晴天暴雪 / 雷雨）。视觉：垂直冲击 + 渺小感。"
                          "适用于仙侠（飞升地）/ 武侠（绝顶比剑）/ 修真（洞府）/ 玄幻（神山）。",
        content_json={
            "physical_elements": "崖壁 / 雪顶 / 云海 / 古松 / 怪石 / 飞瀑 / 云梯 / 鹰击长空",
            "weather_patterns": "瞬息万变（一山有四季）/ 高海拔缺氧 / 雷电频发 / 雪崩",
            "famous_real_mountains": "黄山 / 华山 / 泰山 / 衡山 / 嵩山 / 武当 / 峨眉 / 喜马拉雅 / 阿尔卑斯 / 富士山",
            "fictional_mountain_archetypes": "蜀山（仙侠）/ 不周山（神话）/ 昆仑山（西游）/ 五行山 / 须弥山",
            "atmospheric_words": "巍峨 / 嶙峋 / 险峻 / 苍翠 / 云雾缭绕 / 终年积雪 / 飞鸟难渡",
            "narrative_use": "仙侠飞升 / 武侠绝顶 / 修真洞府 / 户外冒险 / 雪山求生",
            "activation_keywords": ["高山", "雪顶", "崖壁", "云海", "蜀山", "华山", "巍峨", "险峻"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("山岳", ""), llm_note("高山场景")],
        tags=["地理", "山", "场景"],
    ),
    # 江河
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-river-flowing",
        name="奔流江河场景",
        narrative_summary="江河场景：浩荡水流 / 两岸景观 / 渡口 / 渔船 / 风浪 / 桥梁。"
                          "中国四大江河（长江 / 黄河 / 珠江 / 黑龙江）+ 世界名河（尼罗 / 亚马逊 / 恒河 / 莱茵）。"
                          "情感载体：流逝感 / 离别 / 母性。",
        content_json={
            "river_archetypes": "湍急峡谷河 / 平缓蜿蜒大江 / 沙漠绿洲河 / 雪山融水冷河 / 神圣母亲河",
            "famous_rivers_china": "长江（万里）/ 黄河（母亲河）/ 珠江（岭南）/ 黑龙江 / 漓江（桂林）",
            "famous_rivers_world": "尼罗河（埃及）/ 亚马逊（雨林）/ 恒河（印度教）/ 莱茵河（欧洲）/ 多瑙河 / 密西西比",
            "scene_elements": "渡口 / 渔船 / 撑船人 / 桥（廊桥 / 石桥 / 浮桥）/ 龙王庙 / 浣纱女",
            "emotional_associations": "流逝（逝者如斯夫）/ 离别（折柳渡江）/ 母性（哺育文明）/ 阻隔（天险）",
            "narrative_use": "古风（渡口送别）/ 历史（兵家必争）/ 都市（江畔散步）/ 玄幻（龙王传说）",
            "activation_keywords": ["江河", "长江", "黄河", "渡口", "渔船", "桥", "撑船", "浣纱"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("河流", ""), llm_note("江河场景")],
        tags=["地理", "江河", "场景"],
    ),
    # 海洋
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-sea-vast",
        name="浩瀚大海场景",
        narrative_summary="大海场景：无边的水平线 / 不同时刻的色彩（晨蓝 / 午绿 / 暮金 / 夜墨）/ 浪涛 / 海风 / 海鸟 / 远帆。"
                          "情感：自由 / 孤独 / 危险 / 神秘 / 永恒。"
                          "适用于航海冒险 / 末日海岛 / 仙侠东海 / 现代海边度假。",
        content_json={
            "sea_states": "镜面 / 微浪 / 浪涌 / 大浪 / 怒涛 / 风暴 / 龙卷风（海龙卷）",
            "color_by_time": "黎明：蓝灰 / 上午：碧蓝 / 正午：透绿 / 下午：金 / 黄昏：橙红 / 夜：墨黑反月光",
            "scene_creatures": "海鸥 / 信天翁 / 海豚 / 鲸鱼 / 鲨鱼 / 章鱼 / 飞鱼 / 巨型水母",
            "famous_archetypes": "白鲸 Moby Dick（执念）/ 老人与海大马林鱼 / 加勒比海盗黑珍珠号 / 仙侠东海蓬莱",
            "emotional_associations": "自由（远航）/ 孤独（无垠）/ 危险（吞噬）/ 永恒（不变）/ 母性（孕育生命）",
            "narrative_use": "航海冒险 / 末日海岛 / 仙侠东海 / 都市言情海边",
            "activation_keywords": ["大海", "浪涛", "海风", "船帆", "海鸟", "潮汐", "蓝色", "永恒"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("海洋", ""), llm_note("大海场景")],
        tags=["地理", "海", "场景"],
    ),
    # 沙漠
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-desert-vast",
        name="大漠苍凉场景",
        narrative_summary="沙漠场景：起伏沙丘 / 烈日 / 海市蜃楼 / 骆驼商队 / 绿洲 / 风沙暴。"
                          "三大沙漠类型：撒哈拉（大）/ 戈壁（北方）/ 阿拉伯沙漠（油）。"
                          "意象：渺小 / 求生 / 神秘 / 古老。",
        content_json={
            "desert_types": "撒哈拉（非洲）/ 戈壁（蒙古中亚）/ 阿拉伯（中东）/ 塔克拉玛干（新疆）/ 莫哈维（美国）",
            "scene_elements": "沙丘 / 绿洲 / 海市蜃楼 / 骆驼商队 / 风沙暴 / 月夜星空 / 古城遗址 / 干涸河床",
            "weather_extremes": "白天 50°C → 夜晚 -10°C / 沙尘暴遮天蔽日 / 突发暴雨成洪水",
            "famous_legends": "丝路古城（楼兰）/ 阿凡提（智者）/ 阿拉丁神灯 / 木乃伊诅咒",
            "scene_creatures": "骆驼 / 蝎子 / 蛇 / 沙狐 / 秃鹰 / 跳鼠",
            "narrative_use": "丝路冒险 / 末日求生 / 西域玄幻 / 阿拉伯奇幻 / 考古题材",
            "activation_keywords": ["沙漠", "沙丘", "绿洲", "骆驼", "海市蜃楼", "丝路", "戈壁", "楼兰"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("沙漠", ""), llm_note("沙漠场景")],
        tags=["地理", "沙漠", "场景"],
    ),
    # 森林
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-forest-deep",
        name="幽深森林场景",
        narrative_summary="森林场景：参天古树 / 苔藓覆盖 / 阳光斑驳 / 鸟鸣兽吼 / 神秘小径 / 雾霭弥漫。"
                          "类型：温带阔叶 / 寒带针叶 / 热带雨林 / 北方寒林。"
                          "意象：迷失 / 神秘 / 庇护 / 童话 / 妖怪栖息地。",
        content_json={
            "forest_types": "温带阔叶（落叶）/ 寒带针叶（雪松）/ 热带雨林（亚马逊 / 刚果）/ 红木林（北美）/ 竹林",
            "scene_elements": "参天古树 / 苔藓 / 林间小径 / 倒木 / 蘑菇圈 / 溪流 / 营火 / 鹿迹 / 苔藓覆盖石碑",
            "lighting_atmosphere": "阳光斑驳 / 晨雾弥漫 / 月光透叶 / 雷雨突至 / 火把摇曳",
            "scene_creatures": "野鹿 / 狐狸 / 狼 / 熊 / 山猫 / 猫头鹰 / 渡鸦 / 萤火虫 / 蘑菇小妖",
            "famous_archetypes": "格林童话黑森林 / 莎士比亚《仲夏夜之梦》/ 凯尔特圣林 / 西游花果山",
            "emotional_associations": "迷失（迷宫感）/ 庇护（藏匿）/ 童话（精灵）/ 神秘（古老）/ 妖怪（栖息地）",
            "narrative_use": "童话奇幻 / 仙侠灵山 / 历史隐居 / 末日求生",
            "activation_keywords": ["森林", "古树", "苔藓", "迷失", "斑驳", "雾", "妖怪", "幽深"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("森林", ""), llm_note("森林场景")],
        tags=["地理", "森林", "场景"],
    ),
    # 雪地
    MaterialEntry(
        dimension="locale_templates", genre=None,
        slug="locale-snow-frozen",
        name="冰雪极地场景",
        narrative_summary="雪地场景：白茫茫一片 / 极光 / 风雪交加 / 冰封河流 / 麋鹿驯鹿 / 极夜或极昼。"
                          "极寒求生 + 异域冒险背景。"
                          "情感：孤绝 / 纯净 / 死亡。",
        content_json={
            "scene_elements": "雪原 / 冰湖 / 冰川 / 雪崩 / 极光 / 雪屋 / 雪橇 / 风暴 / 冰晶 / 冰洞",
            "scene_creatures": "驯鹿 / 北极熊 / 极光狐 / 海豹 / 海象 / 帝企鹅 / 雪鸮 / 哈士奇雪橇犬",
            "weather_extremes": "-40°C / 暴风雪 / 极夜（冬季）/ 极昼（夏季）/ 极光 / 白虹（雪盲）",
            "famous_archetypes": "西伯利亚冻原 / 阿拉斯加 / 北极圈 / 喜马拉雅 / 南极洲 / 长白山",
            "emotional_associations": "孤绝（白色无垠）/ 纯净（罪洗）/ 死亡（冻僵）/ 救赎（雪掩历史）",
            "narrative_use": "极寒求生 / 异域冒险 / 末日冰封 / 仙侠雪山宗 / 言情（极地浪漫）",
            "activation_keywords": ["雪地", "极地", "冰川", "极光", "暴风雪", "驯鹿", "雪盲", "冻原"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("极地", ""), llm_note("雪地场景")],
        tags=["地理", "雪", "场景"],
    ),
    # 季节意象
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-four-seasons",
        name="四季意象与情感对应",
        narrative_summary="春夏秋冬四季承载稳定情感谱：春（生发希望）/ 夏（炽烈青春）/ 秋（收获感伤）/ 冬（沉静死亡）。"
                          "古典文学和现代影视常用季节切换标记心境演化。",
        content_json={
            "spring": "生机 / 希望 / 初恋 / 万物复苏 / 桃花 / 燕归 / 春雨 / 嫩芽",
            "summer": "炽热 / 青春 / 热情 / 蝉鸣 / 西瓜 / 烟火 / 雷雨 / 树荫",
            "autumn": "丰收 / 感伤 / 离别 / 黄叶 / 桂花 / 月圆 / 秋风 / 长亭",
            "winter": "凋零 / 沉静 / 死亡 / 重生 / 雪 / 梅 / 炉火 / 围炉",
            "common_pairings": "春（青年）→ 夏（壮年）→ 秋（中年）→ 冬（老年/死亡）/ 春恋夏燃秋别冬归",
            "famous_uses": "《红楼梦》四季对应人物命运 / 川端康成《雪国》/ 沈从文《边城》",
            "narrative_use": "古风言情 / 都市情感 / 重生回忆 / 仙侠四时变",
            "activation_keywords": ["春", "夏", "秋", "冬", "四季", "桃花", "蝉鸣", "黄叶", "雪"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("四季", ""), llm_note("四季意象")],
        tags=["意象", "季节", "通用"],
    ),
    # 天气
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-weather-emotion",
        name="天气情感符号系统",
        narrative_summary="天气是文学最古老的情感符号库：晴（明朗）/ 阴（压抑）/ 雨（哀伤 / 净化）/ 雪（纯净 / 死亡）/ 雾（迷茫）/ 雷（震怒 / 启示）。"
                          "用得好提供情感衬托，用得糟变成俗套（『心情如雨』）。",
        content_json={
            "weather_emotions": "晴：明朗自由 / 阴：压抑不安 / 雨：哀伤洗涤 / 雪：纯净死亡 / 雾：迷茫隐藏 / 雷：震怒启示 / 风：变化漂泊 / 暴风雨：剧烈冲突",
            "rain_subtypes": "细雨（哀愁）/ 暴雨（情绪宣泄）/ 春雨（生机）/ 秋雨（凄凉）/ 雷雨（冲突高潮）",
            "design_principles": "1) 与情感同步（关键时刻天气配合）/ 2) 反差使用（葬礼晴天反衬荒诞）/ 3) 避免俗套（不是每次哭都要下雨）",
            "famous_examples": "《呼啸山庄》荒原暴风 / 《雷雨》（直接以天气命题）/ 《情人》湄公河雨季 / 《廊桥遗梦》雨中告别",
            "narrative_use": "情感场景渲染 / 章节开头氛围 / 高潮天气配合 / 反差使用",
            "activation_keywords": ["天气", "晴", "阴", "雨", "雪", "雾", "雷", "暴风雨"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("天气", ""), llm_note("天气情感符号")],
        tags=["意象", "天气", "通用"],
    ),
    # 月亮
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-moon-symbolism",
        name="月亮的多重象征",
        narrative_summary="月亮是文学最广泛的象征之一：圆缺（人事变迁）/ 阴性（女性 / 母性）/ 故乡（思乡）/ 孤独（独酌）/ 神秘（狼人变身）。"
                          "中西文化对月亮意象高度重叠又略有差异。",
        content_json={
            "phases_meaning": "新月（开始）/ 上弦（成长）/ 满月（圆满 / 疯狂）/ 下弦（衰退）/ 残月（哀伤）",
            "chinese_associations": "团圆（中秋）/ 思乡（举头望月）/ 月老（姻缘）/ 嫦娥（孤独）/ 月饼 / 玉兔",
            "western_associations": "lunatic（月光下疯狂）/ 狼人变形 / Diana 月神 / 哥特浪漫",
            "famous_uses": "李白举头望明月 / 苏轼但愿人长久 / 《狼人》传说 / 《月光奏鸣曲》",
            "design_principles": "月相 + 情感同步 / 月亮镜像（水中倒影）/ 跨界月（古今同月）",
            "narrative_use": "古风言情 / 灵异恐怖 / 仙侠（月华炼宝）/ 都市夜场景",
            "activation_keywords": ["月亮", "满月", "新月", "嫦娥", "月老", "举头望月", "月光"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("月亮", ""), llm_note("月亮象征")],
        tags=["意象", "月亮", "通用"],
    ),
    # 火
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-fire-symbolism",
        name="火的象征系统",
        narrative_summary="火是文学最强力的双面意象：温暖（家庭炉火）/ 毁灭（火灾火葬）/ 激情（炽热爱）/ 净化（凤凰涅槃）/ 知识（盗火普罗米修斯）。"
                          "几乎每个文化都有火神和火崇拜。",
        content_json={
            "dual_nature": "温暖 vs 毁灭 / 创造 vs 死亡 / 激情 vs 暴怒 / 净化 vs 焚毁",
            "fire_subtypes": "炉火（家）/ 篝火（聚集）/ 烛光（亲密）/ 火灾（灾难）/ 火葬（终结）/ 凤凰火（重生）",
            "famous_uses": "普罗米修斯盗火 / 凤凰涅槃 / 《简爱》桑菲尔德庄园大火 / 《华氏 451》焚书 / 中国祝融",
            "philosophical_layer": "火 = 改变（一切坚固的都会被火融化）/ 文明象征 / 人神分界",
            "color_associations": "红橙（激情）/ 蓝（高温神圣）/ 紫（神秘）/ 黑烟（死亡）/ 白灰（终结）",
            "narrative_use": "言情（炽热爱）/ 末日（大火灾）/ 仙侠（火系功法）/ 重生（涅槃）",
            "activation_keywords": ["火", "炉火", "篝火", "火灾", "凤凰", "涅槃", "普罗米修斯", "燎原"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("火", ""), llm_note("火的象征")],
        tags=["意象", "火", "通用"],
    ),
    # 镜子
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-mirror-symbolism",
        name="镜子的象征系统",
        narrative_summary="镜子是文学最哲学化的意象：自我认知（看见真实自我）/ 双重身份（镜中另一个我）/ 入口（穿越异界）/ 真相（揭露隐藏）。"
                          "代表《爱丽丝镜中奇遇》/ 《白雪公主》毒后 / 《黑镜》。",
        content_json={
            "core_meanings": "自我认知 / 真相揭示 / 双重身份 / 异界入口 / 虚实分界 / 时间反转",
            "famous_uses": "《白雪公主》魔镜 / 《爱丽丝镜中奇遇》/ 《哈利波特》厄里斯魔镜 / 《盗梦空间》镜面无限 / 《黑镜》",
            "subtypes": "明镜（普通映像）/ 魔镜（揭真相）/ 古镜（穿越）/ 破镜（隔离）/ 哈哈镜（扭曲）",
            "philosophical_layer": "拉康镜像阶段 / 自我 vs 他者 / 主体 vs 客体 / 真 vs 假",
            "narrative_use": "灵异（鬼魂在镜中）/ 心理悬疑 / 仙侠（穿越镜界）/ 言情（双胞胎或镜像）",
            "activation_keywords": ["镜子", "魔镜", "镜中", "破镜", "倒影", "镜像", "对照"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("镜子", ""), llm_note("镜子象征")],
        tags=["意象", "镜子", "通用"],
    ),
    # 道路
    MaterialEntry(
        dimension="thematic_motifs", genre=None,
        slug="motif-road-journey",
        name="道路与旅程象征",
        narrative_summary="道路是叙事最古老的意象：旅程 = 人生 / 选择叉路 = 命运抉择 / 漫长道路 = 成长 / 终点 = 死亡或顿悟。"
                          "公路片 / 西游记 / 朝圣 / 罗马大道 都是变体。",
        content_json={
            "subtypes": "笔直大道（明朗）/ 蜿蜒小路（曲折）/ 叉路口（抉择）/ 死胡同（绝境）/ 山道（艰难）/ 桥（过渡）/ 隧道（黑暗中的希望）",
            "famous_archetypes": "西游取经路 / 罗马大道（条条通罗马）/ 美国 66 公路 / 唐玄宗西巡蜀道 / Yellow Brick Road",
            "philosophical_layer": "旅程 = 寻找自我 / 道路 = 命运的隐喻 / 走与停 = 行动与停滞",
            "narrative_uses": "公路片 / 西游变体 / 朝圣题材 / 末日逃亡 / 历史远征",
            "famous_works": "《西游记》/《在路上》Kerouac /《魔戒》/《指环王》/《阿甘正传》",
            "activation_keywords": ["道路", "旅程", "叉路", "公路", "西游", "朝圣", "归途"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("公路片", ""), llm_note("道路象征")],
        tags=["意象", "旅程", "通用"],
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
