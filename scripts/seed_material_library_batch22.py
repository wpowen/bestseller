"""
Batch 22: Relationship dynamics — 师徒 / 兄弟 / 父子 / 母女 / 夫妻 / 情敌 /
CP 化学 / 老友 / 竞争对手 / 同学. Activates relationship vocabulary
that decides how characters interact across genres.
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
    # 师徒关系
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-master-disciple",
        name="师徒关系动力学",
        narrative_summary="师徒关系核心：传承 + 期望 + 反叛 + 超越。"
                          "四阶段：投师拜入 → 苦修学艺 → 反叛或离散 → 青出于蓝。"
                          "适用武侠 / 仙侠 / 武术 / 学术 / 厨艺等师承传统。",
        content_json={
            "four_stages": "1) 投师（缘分 / 考验入门）/ 2) 修学（严苛苦练 + 偷学秘技）/ 3) 反叛或离散（理念冲突 / 生死分离）/ 4) 超越（青出于蓝 / 师之死 / 接班）",
            "common_dynamics": "严父 + 慈母（双师互补）/ 单师严苛 / 师爷 + 关门弟子（祖孙感）/ 师徒恋（争议）",
            "famous_archetypes": "唐三藏 + 孙悟空 / 张三丰 + 张无忌 / 李慕白 + 玉娇龙 / 老子 + 庄子 / 苏格拉底 + 柏拉图 / 灯笼裤 + 路飞",
            "tension_sources": "保留秘技 vs 全盘传授 / 期望过高压力 / 师不如徒尴尬 / 反叛或叛门",
            "narrative_use": "武侠仙侠 / 学院题材 / 厨艺音乐艺术 / 重生（师徒重逢）",
            "activation_keywords": ["师徒", "师父", "徒弟", "传承", "青出于蓝", "拜师", "叛门"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("师徒", ""), llm_note("师徒关系")],
        tags=["关系", "师徒", "通用"],
    ),
    # 兄弟情谊
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-brothers-bond",
        name="兄弟情谊（生死与共）",
        narrative_summary="兄弟情谊三型：血亲兄弟（家族传承）/ 拜把子兄弟（江湖结义）/ 战友兄弟（生死与共）。"
                          "核心：信任 / 守护 / 牺牲 / 永不抛下。"
                          "适用武侠 / 战争 / 都市黑帮 / 体育竞技。",
        content_json={
            "three_types": "1) 血亲兄弟（家族基因）/ 2) 拜把子兄弟（桃园三结义）/ 3) 战友兄弟（生死之交）",
            "core_values": "信任 / 守护 / 牺牲 / 不离不弃 / 共苦同甘",
            "famous_examples": "刘关张 / 杨家七郎八虎 / 《海贼王》草帽团 / 《七人侍》/ 《教父》家族 / 《老九门》",
            "tension_sources": "争夺继承权 / 女人介入 / 立场分裂（一忠一叛）/ 三角恋",
            "design_principles": "1) 至少一次共度生死 / 2) 互相补全的性格（粗 + 细 / 火 + 冷）/ 3) 关键时刻挡刀 / 4) 沉默时的眼神交流",
            "narrative_use": "武侠 / 战争 / 黑帮 / 体育竞技 / 仙侠（同门）",
            "activation_keywords": ["兄弟", "结义", "拜把子", "战友", "桃园", "生死", "挡刀"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("结义", ""), llm_note("兄弟情谊")],
        tags=["关系", "兄弟", "通用"],
    ),
    # 父子关系
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-father-son",
        name="父子关系（弑父与和解）",
        narrative_summary="父子关系是文学最古老的张力：俄狄浦斯弑父 → 卡夫卡《致父亲》→ 现代东亚高压父亲。"
                          "三阶段：仰望 → 反叛 → 和解（或永不和解）。"
                          "适用家族史 / 都市青年成长 / 武侠传承 / 历史朝堂。",
        content_json={
            "psychological_layers": "依恋 → 模仿 → 比较 → 反叛 → 超越或和解",
            "famous_archetypes": "俄狄浦斯弑父 / 卡夫卡《致父亲》/《教父》维多 + 麦克 / 《星球大战》达斯维达 + 卢克 / 《大宅门》白颖宇 + 白景琦",
            "common_arcs": "1) 父亲完美主角崇拜 → 看到父亲缺陷 → 反叛 → 父亲倒下后理解 / 2) 父亲压迫 → 主角反叛 → 永不和解 / 3) 父亲早逝 → 寻找替代父职",
            "themes": "权威 / 期望 / 模仿与超越 / 罪与赦 / 沉默的爱",
            "narrative_use": "家族史 / 成长题材 / 武侠（父亲是大反派 / 武学权威）/ 历史（皇帝太子）",
            "activation_keywords": ["父子", "弑父", "和解", "继承", "反叛", "父亲", "沉默的爱"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("父子关系", ""), llm_note("父子关系动力")],
        tags=["关系", "父子", "通用"],
    ),
    # 母女关系
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-mother-daughter",
        name="母女关系（紧密与窒息）",
        narrative_summary="母女关系是双向投射：母亲把未实现的梦投到女儿 / 女儿背负期待但渴望独立。"
                          "类型：紧密共生 / 控制窒息 / 朋友姐妹 / 冷漠疏离。"
                          "适用都市言情 / 家族剧 / 心理向 / 重生反抗母亲。",
        content_json={
            "four_types": "1) 紧密共生（不分边界）/ 2) 控制窒息（母亲用爱绑架）/ 3) 朋友姐妹（平等）/ 4) 冷漠疏离（情感淡薄）",
            "psychological_layers": "母亲投射未竟之梦 + 女儿背负期待与反叛 + 互为镜子（女儿成为或拒绝成为母亲）",
            "famous_archetypes": "《钢琴教师》母女 / 《伯德小姐》/ 《美丽人生》/ 《喜福会》/ 《丹麦女孩》",
            "common_conflicts": "婚姻选择 / 职业选择 / 育儿方式 / 价值观 / 与外婆三代连续",
            "narrative_arcs": "反抗 → 离家 → 经历后理解母亲 → 重新连接 / 反抗 → 永远疏远 / 模仿成为母亲（恐惧）",
            "narrative_use": "都市言情 / 家族剧 / 重生反母 / 心理向悬疑",
            "activation_keywords": ["母女", "母亲", "女儿", "代际", "控制", "投射", "和解"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("母女关系", ""), llm_note("母女关系动力")],
        tags=["关系", "母女", "通用"],
    ),
    # 夫妻关系
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-marriage-dynamics",
        name="夫妻关系演变曲线",
        narrative_summary="婚姻关系经过：浪漫期（1-2 年）→ 权力期（试探边界）→ 稳定期 / 危机期 → 深化期（30 年的友谊）。"
                          "Gottman 婚姻研究：四骑士（批评 / 蔑视 / 防御 / 筑墙）= 离婚预警。",
        content_json={
            "stages": "浪漫期（1-2 年蜜月）→ 磨合期 → 稳定期（孩子 / 工作）→ 危机期（七年之痒 / 中年）→ 深化期（晚年友谊）",
            "gottman_four_horsemen": "Criticism 批评（人身攻击）/ Contempt 蔑视（最致命）/ Defensiveness 防御 / Stonewalling 筑墙",
            "love_languages": "Five Love Languages：1) 肯定话语 2) 优质时间 3) 礼物 4) 服务 5) 身体接触",
            "common_crises": "外遇 / 不育 / 婆媳 / 失业 / 重大疾病 / 中年危机 / 子女青春期",
            "narrative_uses": "都市言情后续 / 家庭剧 / 中年题材 / 重生（重做夫妻）",
            "activation_keywords": ["夫妻", "婚姻", "蜜月", "七年之痒", "外遇", "Gottman", "深化"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("婚姻", ""), llm_note("夫妻关系演变")],
        tags=["关系", "夫妻", "通用"],
    ),
    # 情敌
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-romantic-rival",
        name="情敌关系动力学",
        narrative_summary="情敌是言情核心张力：男主有白月光 / 女主有竹马 / 三人关系制造情节冲突。"
                          "类型：青梅竹马（旧情）/ 完美对手（条件压制）/ 黑化前任（报复）/ 暗恋多年（默默支持型）。",
        content_json={
            "rival_types": "1) 青梅竹马（旧情）/ 2) 完美对手（条件 + 出身碾压）/ 3) 黑化前任（报复破坏）/ 4) 暗恋多年（深情守候）/ 5) 包办婚约（家族压力）",
            "narrative_functions": "制造冲突 / 试探主角真心 / 配角发光机会 / 逼男主告白",
            "design_principles": "1) 情敌不能太弱（否则没张力）/ 2) 必须有一个让主角动心的瞬间 / 3) 最后必须输得有尊严 / 4) 避免污名化（只让前任脸谱化）",
            "common_endings": "情敌祝福离场 / 情敌黑化失败 / 情敌找到自己的爱 / 情敌为爱牺牲",
            "famous_examples": "《泰坦尼克》卡尔 / 《新月》Jacob / 《来自星星的你》李辉京 / 《琅琊榜》谢玉",
            "narrative_use": "言情 / 古风 / 现代偶像 / 校园 / 重生改变结局",
            "activation_keywords": ["情敌", "青梅", "白月光", "三角恋", "前任", "竹马", "暗恋"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("三角恋", ""), llm_note("情敌动力学")],
        tags=["关系", "情敌", "言情"],
    ),
    # CP 化学反应
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-cp-chemistry",
        name="CP 化学反应配方",
        narrative_summary="CP（Couple）化学反应来自『反差 + 互补 + 张力 + 痛点共鸣』。"
                          "经典配方：高冷 × 阳光 / 病娇 × 单纯 / 总裁 × 灰姑娘 / 死对头 × 不打不相识 / 师徒 × 久别重逢。"
                          "高 CP 感 = 必须吵架 + 救场 + 误会 + 告白四件套循环。",
        content_json={
            "classic_formulas": "高冷 × 阳光 / 病娇 × 单纯 / 霸总 × 灰姑娘 / 死对头 → 恋人 / 师徒 / 重逢初恋 / 大叔 × 萝莉 / 校霸 × 学霸",
            "chemistry_components": "1) 反差萌 / 2) 互补缺口 / 3) 紧张感（追逐 / 拒绝）/ 4) 痛点共鸣（创伤治愈彼此）/ 5) 命运感（命中注定）",
            "must_have_scenes": "吵架 → 误会 → 救场 → 告白 → 撒糖（每章一糖一虐）",
            "famous_cp": "罗密欧朱丽叶 / 林黛玉贾宝玉 / 杨过小龙女 / 金庸笔下各种 CP / 韩剧 K-CP / 偶像剧定番",
            "narrative_use": "言情主线 / 双男主双女主 / 校园 / 古风 / 都市偶像",
            "activation_keywords": ["CP", "化学反应", "反差", "互补", "甜虐", "告白", "吵架", "救场"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("耽美", ""), llm_note("CP 化学反应")],
        tags=["关系", "CP", "言情"],
    ),
    # 死对头变恋人
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="rel-plot-enemies-to-lovers",
        name="死对头变恋人模式（Enemies to Lovers）",
        narrative_summary="言情金牌套路：两人第一面互相讨厌 → 不得不合作 → 发现对方一面 → 渐生情愫 → 真心爆发。"
                          "代表《傲慢与偏见》达西 + 伊丽莎白 / 《这个杀手不太冷》/ 《何以笙箫默》。",
        content_json={
            "five_stages": "1) 第一次冲突（误会 + 互相讨厌）/ 2) 不得不接触（合作 / 同居 / 任务）/ 3) 发现对方一面（隐藏的善 / 痛 / 才）/ 4) 渐生情愫（吃醋 / 想念 / 关心）/ 5) 真心爆发（救场 / 告白 / 拥吻）",
            "key_moments": "互相讽刺 / 不得不同处一室 / 第一次发现对方笑容 / 第一次想念 / 误以为对方有危险",
            "famous_examples": "《傲慢与偏见》/ 《何以笙箫默》/ 《杉杉来吃》/ 《暗恋橘生淮南》/ 韩剧《城市猎人》",
            "subgenres": "校园 / 都市 / 古风 / 仙侠 / 修真 / 民国",
            "design_principles": "讨厌的理由要『正当』而非脸谱化 / 中段要有足够的『被对方打动』瞬间 / 最后告白要有重大代价",
            "narrative_use": "言情主线 / 校园 / 都市 / 古风偶像 / 重生",
            "activation_keywords": ["死对头", "Enemies to Lovers", "讨厌", "傲慢与偏见", "渐生情愫", "误会"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("Enemies to lovers", ""), llm_note("死对头变恋人")],
        tags=["关系", "言情", "套路"],
    ),
    # 三体竞争
    MaterialEntry(
        dimension="plot_patterns", genre=None,
        slug="rel-plot-rivalry-progression",
        name="同辈竞争对手关系",
        narrative_summary="竞争对手不是敌人——同行同辈互相成就。三阶段：第一次相遇互不服 → 长期竞争中尊重 → 最后惺惺相惜或一方陨落。"
                          "适用体育竞技 / 武侠（武学之争）/ 商战 / 学术。",
        content_json={
            "three_stages": "1) 初识相轻（年轻气盛互不服）/ 2) 长期竞争（互相进步）/ 3) 终局（惺惺相惜 / 一方退出 / 一方陨落）",
            "key_moments": "第一次切磋后惊讶对方实力 / 中期被对方启发 / 看到对方私下苦练的样子 / 关键时刻并肩对抗第三方",
            "famous_examples": "《灌篮高手》流川枫 + 仙道 / 《棋魂》进藤光 + 塔矢亮 / 《全职高手》叶修 + 周泽楷 / 莫扎特 + 萨列里",
            "themes": "孤独的顶尖（只有对方理解）/ 互相成就 / 棋逢对手 / 你死则我无意义",
            "narrative_use": "体育竞技 / 武侠 / 商战 / 学术 / 围棋象棋",
            "activation_keywords": ["竞争对手", "对手", "棋逢对手", "切磋", "惺惺相惜", "宿敌"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("宿敌", ""), llm_note("竞争对手关系")],
        tags=["关系", "竞争对手", "通用"],
    ),
    # 老友
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-old-friends",
        name="老友关系（多年默契）",
        narrative_summary="老友三特征：长时间不见仍能秒懂 / 不需要解释的默契 / 关键时刻一句话救场。"
                          "适用都市言情 / 群像剧 / 重生（朋友圈重组）/ 校园（毕业多年重逢）。",
        content_json={
            "key_traits": "长期共同记忆 / 不需解释的默契 / 互相黑暗中的灯 / 见到就回到从前 / 关键时刻一句话",
            "famous_archetypes": "《六人行》Friends / 《老友记》/《独自在夜晚的海边》/ 校园群像（《那些年》）",
            "common_arcs": "校园同学多年重逢 / 童年伙伴成年再聚 / 同事变挚友 / 战友/狱友余生",
            "tension_sources": "时间冲淡 → 重新连接困难 / 价值观分化（一人发达一人潦倒）/ 变成情敌 / 永远的过去",
            "narrative_use": "都市言情 / 群像剧 / 重生 / 中年题材 / 校园 + 多年后",
            "activation_keywords": ["老友", "重逢", "默契", "多年不见", "童年伙伴", "毕业", "再聚"],
        },
        source_type="llm_synth", confidence=0.84,
        source_citations=[wiki("友谊", ""), llm_note("老友关系")],
        tags=["关系", "老友", "通用"],
    ),
    # 同事关系
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-coworkers-office",
        name="同事关系（职场动力学）",
        narrative_summary="职场关系核心：合作 + 竞争 + 站队 + 上下级。"
                          "类型：搭档（生死之交）/ 竞争对手（升职冤家）/ 上司（领导风格）/ 下属（培养接班）/ 老油条 / 新人。",
        content_json={
            "office_archetypes": "搭档 / 竞争冤家 / 严苛上司 / 慈父型 boss / 老油条 / 新人 / 部门花 / 闲职大佬 / 派系老大",
            "common_dynamics": "项目合作 / 业绩竞争 / 站队抉择 / 跳槽威胁 / 师徒带教 / 办公室恋情",
            "key_scenes": "电梯遭遇 / 食堂偶遇 / 会议室博弈 / 应酬酒桌 / 加班深夜 / 离职拥抱",
            "famous_works": "《半泽直树》/《北京遇上西雅图》/《杜拉拉升职记》/《我的前半生》/《Mad Men》",
            "narrative_use": "都市职场 / 商战 / 言情（办公室恋情）/ 悬疑（同事是凶手）",
            "activation_keywords": ["同事", "搭档", "上司", "下属", "升职", "竞争", "派系", "站队"],
        },
        source_type="llm_synth", confidence=0.83,
        source_citations=[wiki("职场", ""), llm_note("同事关系")],
        tags=["关系", "同事", "职场"],
    ),
    # 仇敌
    MaterialEntry(
        dimension="character_archetypes", genre=None,
        slug="rel-arch-blood-enemies",
        name="血海深仇关系",
        narrative_summary="血海深仇关系：杀亲灭门 / 抢夺挚爱 / 毁灭未来。"
                          "驱动主角全本主线复仇。代表《基督山伯爵》《赵氏孤儿》《琅琊榜》。"
                          "复仇心理学：怨恨 → 计划 → 执行 → 空虚 / 升华。",
        content_json={
            "trigger_types": "灭门屠戮 / 杀师杀友 / 抢夺爱人 / 毁人前途 / 害人重伤 / 灭族奴役",
            "revenge_psychology": "1) 复仇决心（誓言）/ 2) 长期蛰伏（积累实力）/ 3) 步步紧逼 / 4) 真相揭露 / 5) 终极一击 / 6) 复仇后空虚 / 7) 升华或同归于尽",
            "famous_examples": "《基督山伯爵》/ 《赵氏孤儿》/ 《琅琊榜》梅长苏 / 《杀死比尔》/ 《老男孩》",
            "moral_complications": "仇人是否还有家人？/ 仇人是否已悔改？/ 复仇是否值得？/ 复仇后我是谁？",
            "narrative_use": "复仇主线 / 武侠 / 古风 / 民国 / 末日（灭族）/ 重生（带恨重来）",
            "activation_keywords": ["血海深仇", "复仇", "灭门", "誓言", "蛰伏", "仇敌", "终极一击"],
        },
        source_type="llm_synth", confidence=0.86,
        source_citations=[wiki("复仇", ""), wiki("基督山伯爵", ""), llm_note("血仇关系")],
        tags=["关系", "仇敌", "通用"],
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
