"""
Batch 36: Specific named character templates.
Cross-genre signature characters with full bios:
仙侠魔尊 / 武侠浪子 / 都市赘婿 / 总裁文女主 / 网游大神 / 古言郡主 etc.
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
    # 仙侠 - 凡人韩立模板
    MaterialEntry(
        dimension="character_templates", genre="仙侠",
        slug="char-tmpl-pingfan-cultivator",
        name="平凡修真者模板（韩立式）",
        narrative_summary="凡人修真主角原型。"
                          "出身贫寒农村 / 极致谨慎 / 平凡资质 + 上古遗产。"
                          "忘语《凡人修仙传》韩立 = 网文修真新流派开山。",
        content_json={
            "background": "出身偏远山村 / 多兄弟姐妹 / 父母农民 / 童年砍柴务农 / 12-15 岁被路过散修选中入门派",
            "appearance": "中等身材 / 普通五官 / 黝黑皮肤 / 不算英俊但不丑 / 比同龄人显成熟 / 眉宇间凝重",
            "personality": "极度谨慎（老阴比代名词）/ 不冲动不张扬 / 心思缜密 / 从不轻信他人 / 平时温和但杀人决绝 / 自保第一",
            "core_traits": "韩跑跑（保命第一）/ 老阴比（埋伏算计）/ 千年王八（活得久）/ 韩老魔（后期狠辣）",
            "talent_arc": "灵根普通 / 没有金手指（只有一瓶神秘绿液 = 真正的低调金手指）/ 全靠苦修 + 算计 + 资源积累",
            "growth_curve": "练气期混 7 年 / 筑基期险象环生 / 金丹期开始有底气 / 元婴期方为强者 / 化神期游离顶级 / 大乘期飞升",
            "narrative_pattern": "大量篇幅在'谨慎计划' + '资源积累' + '炼丹炼器' + '埋伏战' / 没有龙傲天的'气运' + 没有'仇人主动找茬' / 极致写实派",
            "famous_works": "《凡人修仙传》《仙逆》部分元素 / 网文'凡人流'代表",
            "activation_keywords": ["凡人", "韩立", "韩跑跑", "老阴比", "凡人修真流", "韩老魔", "千年王八"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("忘语《凡人修仙传》韩立")],
        tags=["仙侠", "凡人流", "主角模板"],
    ),
    # 玄幻 - 萧炎模板
    MaterialEntry(
        dimension="character_templates", genre="玄幻",
        slug="char-tmpl-feichaifanshen",
        name="废柴翻身少年（萧炎式）",
        narrative_summary="玄幻爽文主角原型。"
                          "天才陨落 → 三年废柴 → 觉醒翻身。"
                          "天蚕土豆《斗破苍穹》萧炎 = 网文废柴流标杆。",
        content_json={
            "background": "豪门嫡子 / 4 岁觉醒为天才 / 9 岁陨落（暗中被害 / 神秘吞噬异火 = 后期觉醒之源）/ 12-17 岁'三年废柴'被族人歧视",
            "appearance": "少年书生气 / 身高中等偏高 / 浓眉黑眸 / 棱角分明 / 后期成熟稳重",
            "personality": "外柔内刚 / 平静下藏锋芒 / 重亲情（家人朋友 = 死也守）/ 仇人三年记仇 / 有恩必报有仇必雪",
            "key_relationships": "药老（爷爷辈导师）/ 萧薰儿（青梅竹马）/ 美杜莎（敌后伴侣）/ 云韵 / 海波东 / 香尘老人 / 萧战（父）",
            "talent_arc": "三年废柴 → 觉醒戒指（药老）→ 一年苦修 + 月考 → 入加贝兰学院 → 收异火 → 入魔兽山脉 → 入丹塔 → 古界 → 天上界",
            "growth_curve": "斗者 → 斗师 → 斗灵 → 斗王 → 斗皇 → 斗宗 → 斗尊 → 斗圣 → 斗帝 / 5-7 年完成（玄幻爽文节奏）",
            "narrative_pattern": "废柴翻身（前期）/ 收异火（中期）/ 救父母（推动）/ 灭仇敌（高潮）/ 登顶斗帝",
            "famous_works": "《斗破苍穹》原模板 / 后续大量翻版（《武动乾坤》林动 / 《大主宰》牧尘 / 《元尊》周元）",
            "activation_keywords": ["萧炎", "废柴翻身", "斗气大陆", "异火", "三年之约", "药老", "美杜莎"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("天蚕土豆《斗破苍穹》萧炎")],
        tags=["玄幻", "废柴翻身", "主角模板"],
    ),
    # 武侠 - 浪子大侠模板
    MaterialEntry(
        dimension="character_templates", genre="武侠",
        slug="char-tmpl-langzi-daxia",
        name="浪子大侠（楚留香 / 李寻欢式）",
        narrative_summary="古龙武侠主角原型。"
                          "潇洒不羁 + 风流倜傥 + 武功盖世。"
                          "古龙楚留香 + 李寻欢 = 浪子英雄经典。",
        content_json={
            "background": "出身贵族 / 早年逐出家门 / 浪迹江湖 20 年 / 名声籍甚 / 朋友遍天下",
            "appearance": "潇洒 / 美男（眉如墨画）/ 身姿如松 / 衣着考究但不张扬 / 永远微笑 / 眼神深邃",
            "personality": "外表潇洒不羁 / 内心孤独 / 重情重义 / 救人无数但不留名 / 风流但不下流 / 有底线",
            "core_quirks": "楚留香 = 偷遍天下不为财 / 李寻欢 = 飞刀绝技 + 喝酒咳血 / 陆小凤 = 4 道眉毛 + 灵犀指",
            "key_relationships": "红颜知己（很多但平等）/ 死党（楚留香 + 胡铁花 + 姬冰雁）/ 仇人（极少 / 但每个都是顶级）",
            "famous_works": "古龙《楚留香传奇》《多情剑客无情剑》《陆小凤传奇》《绝代双骄》",
            "narrative_pattern": "案件牵动主角 + 一案一江湖 + 揭秘 + 决战 / 浪子探案 + 武侠悬疑 / 不在乎结局只在乎过程",
            "activation_keywords": ["楚留香", "李寻欢", "陆小凤", "古龙", "浪子", "侠客", "飞刀", "盗帅"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("古龙浪子原型")],
        tags=["武侠", "浪子", "主角模板"],
    ),
    # 都市 - 赘婿模板
    MaterialEntry(
        dimension="character_templates", genre="都市",
        slug="char-tmpl-zhuangxu",
        name="赘婿翻身（叶修 / 林云式）",
        narrative_summary="2018 年后网文核心主角原型。"
                          "明面是被歧视的赘婿 / 暗里是大佬。"
                          "《赘婿》《我岳父是李世民》《极品上门女婿》流派。",
        content_json={
            "surface_identity": "被妻子家族瞧不起的入赘女婿 / 被欺辱被捉弄被分配最差屋 / 妻子 / 妻家小姨子姑姑都欺负 / 看似无能",
            "hidden_identity": "国家级特工 / 千亿富豪 / 修真高手 / 退役兵王 / 仙界遗孤 / 七大家族真传 / 各种隐藏身份",
            "personality": "表面隐忍 + 沉默 + 老实 / 内心冷静 + 决绝 + 关键时刻反击 / 重情重义 + 妻子真心 = 主角真心",
            "wife_attitude_arc": "前期妻子也歧视 → 中期偶发事件让妻子开始动摇 → 主角不为所动 → 妻子真心反追 → 主角原谅",
            "narrative_engine": "主线 = 一次次危机让主角'露一手' → 周围人傻眼打脸 / 节奏天然密集",
            "famous_works": "《赘婿》《极品上门女婿》《最强赘婿》《重生之极品赘婿》",
            "common_arcs": "1) 入赘三年隐忍 / 2) 第一次露脸（妻子家族出大事 / 主角解决）/ 3) 各方势力上门 / 4) 主角身份揭开 / 5) 妻子家族跪地 / 6) 主角拒绝并继续低调",
            "activation_keywords": ["赘婿", "上门女婿", "扮猪吃虎", "隐藏身份", "退役兵王", "千亿富豪"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("赘婿流网文标杆")],
        tags=["都市", "赘婿", "主角模板"],
    ),
    # 总裁文 - 霸道总裁模板
    MaterialEntry(
        dimension="character_templates", genre="言情",
        slug="char-tmpl-bossman-ceo",
        name="霸道总裁（顾里 / 何以琛 / 慕容沉式）",
        narrative_summary="总裁文男主原型。"
                          "傲娇 + 富有 + 帅气 + 痴情 = 4 大特征。"
                          "中国 2010 年后言情核心。",
        content_json={
            "background": "豪门继承人 / 30 岁亿万富翁 / 海外名校毕业（哈佛 / 沃顿）/ 公司行业第一 / 帅气未婚 / 神秘过去（前任死了 / 失忆 / 童年阴影）",
            "appearance": "身高 188+ / 黑色西装 / 手握限量手表 / 五官精致硬朗 / 身材完美 / 永远高冷脸 / 对女主例外",
            "personality": "外人冷酷霸道 + 唯独对女主温柔 / 占有欲极强 / 嫉妒心强 / 表面强硬内心痴情 / 不善表达爱 / 通过送礼物 + 行动表达",
            "key_quirks": "壁咚（经典动作）/ 公主抱 / 强吻（前期女主拒绝 / 后期接受）/ 突然出现救场 / 默默帮女主家解困",
            "love_story_arc": "1) 偶然相遇（电梯 / 雨夜 / 公司）/ 2) 误会冲突 / 3) 慢慢被女主性格吸引 / 4) 偶发事件中救女主 / 5) 表白 / 6) 误会再生 / 7) 大结局在一起",
            "famous_works": "《何以笙箫默》/ 《杉杉来了》/ 《千山暮雪》/ 《微微一笑很倾城》/ 《亲爱的翻译官》",
            "activation_keywords": ["霸道总裁", "总裁", "壁咚", "公主抱", "亿万富翁", "豪门继承人", "高冷"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("总裁文男主标杆")],
        tags=["言情", "总裁文", "男主模板"],
    ),
    # 古言 - 强势郡主模板
    MaterialEntry(
        dimension="character_templates", genre="古言",
        slug="char-tmpl-strong-princess",
        name="强势郡主 / 大女主（武则天 / 大明宫词式）",
        narrative_summary="古言大女主原型。"
                          "聪慧 + 强势 + 不靠男人。"
                          "《大宋宫词》《长歌行》《大明宫词》《延禧攻略》。",
        content_json={
            "background": "皇室贵胄（公主 / 郡主 / 太女）/ 自幼读书 / 文武双全 / 政治嗅觉敏锐 / 童年丧亲（推动改变命运）",
            "appearance": "美貌但不柔弱 / 凤眸 / 身材修长 / 红色 / 紫色衣服多 / 凤冠霞帔时威严无比 / 私下穿男装",
            "personality": "聪慧 / 决断 / 从不退让 / 智斗朝堂 / 看穿人心 / 不依赖男人 / 重感情但不妥协 / 内心也有柔软",
            "narrative_arc": "1) 童年灾难（家人被害）/ 2) 立志 / 3) 隐忍 + 学习 / 4) 突围 + 第一次反击 / 5) 进入朝堂权斗 / 6) 一一解决敌手 / 7) 登顶（女皇 / 摄政 / 颠覆王朝）",
            "love_dynamic": "可以有爱情但不依赖 / 男主 = 衬托 / 女主决策 + 男主辅助 / 对等关系 / 不为爱情让步",
            "famous_works": "《武则天》《延禧攻略》/ 《知否》/ 《长歌行》/ 《如懿传》/ 《大明宫词》/ 《甄嬛传》",
            "modern_subversion_2020": "更强调女主独立 / 男主成为陪衬 / 政治作业第一 / 爱情第二 / 女性力量觉醒",
            "activation_keywords": ["大女主", "公主", "郡主", "女皇", "宫斗", "权谋", "独立女性", "凤眸"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("古言大女主标杆")],
        tags=["古言", "大女主", "宫斗"],
    ),
    # 网游 - 高玩大神模板
    MaterialEntry(
        dimension="character_templates", genre="网游",
        slug="char-tmpl-pro-gamer",
        name="网游大神（叶修 / 蝶恋花式）",
        narrative_summary="网游小说男主原型。"
                          "现实平凡 / 游戏内大神。"
                          "《全职高手》叶修 = 网游小说现代标杆。",
        content_json={
            "real_life_appearance": "瘦长身材 / 邋遢 / 烟不离手 / 总穿黑色 / 眼神冷淡 / 不在乎外表",
            "personality_real_world": "外冷内热 / 看似懒散 / 实则极度专业 / 朋友以男性为主 / 不擅情感表达 / 教练 + 队长气质",
            "personality_in_game": "活力四射 / 玩家偶像 / 战术大师 / 引领队伍 / 创造神操作 / 业内传奇",
            "background": "前职业选手 / 因伤 / 因背叛 / 因家庭原因退役 / 进入网吧 / 找回初心 / 重组队伍",
            "narrative_arc": "1) 退役低谷 / 2) 进网吧打工 / 3) 重新组队 / 4) 比赛起步 / 5) 全国联赛 / 6) 复仇前队友 / 7) 重夺冠军",
            "key_relationships": "队友（兄弟情）/ 死敌（前队友 / 商业对手）/ 红颜知己（陶轩 / 苏沐橙）/ 老板 / 教练",
            "famous_works": "《全职高手》《网游之纵横天下》《奥术神座》《英雄联盟之传奇大魔王》",
            "activation_keywords": ["叶修", "全职高手", "网游大神", "电竞", "退役选手", "重夺冠军"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("蝴蝶蓝《全职高手》叶修")],
        tags=["网游", "电竞", "主角模板"],
    ),
    # 仙侠 - 魔尊 / 反派模板
    MaterialEntry(
        dimension="character_templates", genre="仙侠",
        slug="char-tmpl-demon-king",
        name="魔尊 / 大反派（魔道祖师 / 原尊式）",
        narrative_summary="仙侠魔尊反派原型。"
                          "压迫感 + 黑暗魅力 + 复杂动机。"
                          "魔道祖师 / 完美世界 / 仙逆系列。",
        content_json={
            "appearance": "高瘦修长 / 黑发披肩 / 红眼或紫眼 / 修罗黑衣 / 苍白皮肤 / 笑里藏刀 / 散发气压让普通修士跪下",
            "personality": "矛盾复杂 / 表面冷酷无情 + 实则曾受重伤（被恋人背叛 / 兄弟出卖 / 父母惨死）/ 黑化原因合理化",
            "ideology": "强者为尊 / 不信因果 / 我命由我不由天 / 报复一切伪正派",
            "key_traits": "邪魅一笑 = 经典 / 杀人不眨眼 / 但对珍视者极尽柔情 / 双重人格般",
            "famous_examples": "《魔道祖师》魏无羡（半反派 / 半主角）/ 《完美世界》/ 《诛仙》鬼厉（张小凡黑化）/ 《圣墟》",
            "narrative_uses": "终极反派 / 突然变盟友 / 主角的影子 / 主角的未来可能 / 黑化主角对照",
            "modern_design": "当代魔尊不再是纯反派 / 灰色道德 / 复杂动机 / 让读者既怕又爱",
            "activation_keywords": ["魔尊", "魔王", "黑化", "邪魅", "魔道祖师", "鬼厉", "原尊"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("仙侠反派模板")],
        tags=["仙侠", "反派", "魔尊"],
    ),
    # 末世 - 异能女王模板
    MaterialEntry(
        dimension="character_templates", genre="末世",
        slug="char-tmpl-doomsday-female-leader",
        name="末世女王（异能女主）",
        narrative_summary="末世女主流原型。"
                          "重生回末世前 + 异能觉醒 + 团队领袖。"
                          "末世女主 + 团队 + 异能 + 反击。",
        content_json={
            "background": "重生（末世第 5 年死掉的女主回到末世前 1 周）/ 有完整末世记忆 / 异能（火 / 冰 / 治愈 / 空间）/ 商人妻子 / 老总 / 普通白领",
            "appearance": "美貌但不柔弱 / 黑色战斗服 / 身材健美 / 持手枪 / 持长刀 / 红眸 / 冷脸",
            "personality": "前世被害 = 这世先下手为强 / 冷静 + 冷酷 + 重情 / 团队第一 / 不容许背叛 / 重生第一周搜集物资",
            "narrative_arc": "1) 重生前 1 周 / 2) 提前囤积物资 / 3) 末世爆发 / 4) 救亲人 / 5) 找异能伙伴 / 6) 建基地 / 7) 反杀前世仇人 / 8) 团队壮大 / 9) 终极反派（前世大 BOSS）",
            "team_setup": "主角 + 异能特长伙伴（治愈 / 战斗 / 工程师 / 科学家）+ 男主（默默支持）+ 反派（前世仇人）",
            "famous_works": "《末世重生》《丧尸末日》《重生之末世女王》《我的末世女友》",
            "activation_keywords": ["末世重生", "异能女主", "丧尸", "重生女王", "团队领袖", "前世记忆"],
        },
        source_type="llm_synth", confidence=0.92,
        source_citations=[llm_note("末世女主流标杆")],
        tags=["末世", "重生", "女主模板"],
    ),
    # 科幻 - 黑客主角模板
    MaterialEntry(
        dimension="character_templates", genre="科幻",
        slug="char-tmpl-cyberpunk-hacker",
        name="赛博朋克黑客（Mr.Robot / Neo 式）",
        narrative_summary="赛博朋克小说男主原型。"
                          "现实底层 + 网络精英 + 反抗系统。"
                          "Neuromancer / Matrix / Mr.Robot 经典。",
        content_json={
            "real_life_appearance": "瘦削 / 苍白 / 黑帽 / 黑色卫衣 / 牛仔裤 / 黑色眼袋 / 总在咖啡馆或地下室",
            "personality": "社交焦虑 / 内向 / 偏执 / 智商极高（IQ 160+）/ 现实社交无能 / 网络上呼风唤雨",
            "skills": "C++ / Python / Assembly / 0day / 渗透 / 社工 / 加密货币 / 暗网 / 神经接口（赛博朋克）",
            "ideology": "信息自由 / 反极权 / 反公司 / 反监控 / 隐私至上 / 政府是敌人",
            "narrative_arc": "1) 平凡程序员 / 黑客高手 / 2) 偶然发现大阴谋 / 3) 加入地下组织 / 4) 与公司 / 政府对抗 / 5) 队友牺牲 / 6) 大决战 / 7) 揭示真相",
            "famous_works": "Mr.Robot（Elliot）/ Matrix（Neo）/ Neuromancer（Case）/ Watch Dogs / 攻壳机动队",
            "psychological_depth": "童年阴影 / 父母去世 / 社交障碍 / 抑郁症 + 焦虑症 / 用代码逃避现实",
            "activation_keywords": ["黑客", "赛博朋克", "Mr.Robot", "Neo", "Matrix", "0day", "暗网"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("赛博朋克男主标杆")],
        tags=["科幻", "赛博朋克", "黑客"],
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
