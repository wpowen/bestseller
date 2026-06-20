"""
Batch 57: 题材覆盖补缺(配合 config/genre_taxonomy.yaml canonical 收敛)。

补 4 个此前场景库近乎空白的题材的具体素材:
  军事(military) / 纯爱·百合(pure-love) / 幻想言情(fantasy-romance) / 大女主(female-growth)。

genre 串均可被 services/genre_taxonomy.canonicalize() 收敛到正确 canonical 题材,
确保 material_library.query_library 检索命中(见 docs/题材体系-全量调研与实施计划 §1.4 / Phase B4)。

素材一律具体可落地(场景/桥段/人设/道具),避免抽象旁白——抽象素材会让正文质量显著下降。
运行:uv run python scripts/seed_material_library_batch57.py  (需连接 DB;会计算 embedding)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bestseller.infra.db.session import session_scope
from bestseller.services.material_library import MaterialEntry, insert_entry


def wiki(title: str, note: str = "") -> dict:
    return {"type": "wikipedia", "title": title, "note": note}


def llm_note(note: str) -> dict:
    return {"type": "llm_synth", "note": note}


ENTRIES: list[MaterialEntry] = [
    # ═══════════════════════ 军事 military ═══════════════════════
    MaterialEntry(
        dimension="world_settings", genre="军事", sub_genre="现代战争",
        slug="mil-ws-special-ops-chain",
        name="现代特种作战指挥链",
        narrative_summary="一支跨军种特战小队的真实运作骨架:情报—投送—渗透—撤离四环相扣,任何一环的延迟都会让队员暴露在火力下。",
        content_json={
            "command_chain": "前指(后方)—小队长(现场)—单兵,通讯延迟以秒计,卫星过顶有8分钟盲窗",
            "logistics_constraint": "弹药、续航、撤离窗口三者互斥,带得多走得慢,走得快火力薄",
            "real_cost_rule": "每次开火都暴露位置,静默渗透比火力压制更值钱",
            "unique_conflict_source": "撤离窗口与伤员转运冲突:背一个伤员=全队慢40%,救还是不救是命题",
        },
        source_type="llm_synth",
        source_citations=[wiki("特种部队"), llm_note("以现代特战条令为原型的军事世界观骨架")],
        confidence=0.6, tags=["军事", "现代战争", "特战", "渗透"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="军事", sub_genre="军旅生涯",
        slug="mil-ca-returning-veteran",
        name="退役归来的老兵",
        narrative_summary="退役特种兵重回平凡生活,身体记得每一次心跳过速的战场,却要学会在菜市场讨价还价——战场后遗症与平凡渴望的撕扯。",
        content_json={
            "core_wound": "一次任务里他按规程放弃了一个来不及救的战友,军功章成了刑具",
            "external_goal": "想做个普通人,开个小店,夜里不被警觉惊醒",
            "flaw": "对威胁的反应快过理智,容易把平民冲突误判成战场",
            "strength": "极致的观察力与执行力,危机中是唯一靠得住的人",
            "signature_action": "进任何房间先扫一眼出口与遮蔽物,这是刻进骨子的本能",
        },
        source_type="llm_synth",
        source_citations=[llm_note("军旅题材高代入主角原型:战场后遗症×平凡渴望")],
        confidence=0.62, tags=["军事", "军旅", "退役老兵", "PTSD"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="谍战", sub_genre="谍战特工",
        slug="mil-st-cafe-handoff-standoff",
        name="咖啡馆情报交接对峙",
        narrative_summary="两名情报员在闹市咖啡馆完成一次看似平常的交接,桌上每个动作都是暗语,而邻桌的第三个人正在用糖罐反光观察他们。",
        content_json={
            "scene_trigger": "约定的交接:一份报纸夹着微缩胶卷,以'借个火'为接头暗号",
            "sensory_anchors": ["磨豆机的噪音掩盖耳语", "糖罐不锈钢面映出邻桌的影", "对方搅咖啡的勺敲了三下杯壁=有尾巴"],
            "turn": "接头人发现暗号节奏错了一拍——对面坐的不是同志,是被策反的人",
            "payoff": "他不动声色把胶卷换到糖包下,起身时'不慎'碰倒咖啡,趁混乱完成真正的交接",
        },
        source_type="llm_synth",
        source_citations=[llm_note("谍战标志性桥段:日常场景下的暗语博弈与临场应变")],
        confidence=0.6, tags=["谍战", "情报交接", "暗语", "对峙"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="军事", sub_genre="现代战争",
        slug="mil-pp-deep-rescue",
        name="孤胆深入敌后救援",
        narrative_summary="情报失误导致一名队员被困敌方腹地,小队违令组织非授权营救,在没有空中支援的48小时里用智慧弥补火力。",
        content_json={
            "setup": "官方判定队员已阵亡,放弃搜救;小队长拿到一段证明他还活着的电台杂音",
            "escalation": "每深入一层敌区,补给与撤离希望递减,队内对'值不值'产生分裂",
            "reversal": "被困者其实掌握了能改变战局的情报,救他不只是道义,是战略必要",
            "payoff": "靠地形、夜色与一次精确的声东击西换回人,但全队背上违令的代价",
        },
        source_type="llm_synth",
        source_citations=[llm_note("军事经典三幕:不抛弃不放弃×火力劣势下的智取")],
        confidence=0.6, tags=["军事", "敌后救援", "孤胆", "战友"],
    ),

    # ═══════════════════════ 纯爱·百合 pure-love ═══════════════════════
    MaterialEntry(
        dimension="character_archetypes", genre="纯爱", sub_genre="现代纯爱",
        slug="bl-ca-possessive-cold-gong",
        name="占有欲反差攻",
        narrative_summary="对外人冷面零度、对认定的人却占有欲极强的攻,克制是他的盔甲,失控是他唯一的破绽。",
        content_json={
            "surface": "商界或学界的绝对强者,对所有人保持礼貌的距离",
            "crack": "只有在受面前会露出不讲道理的那一面,越克制越说明在意",
            "core_wound": "曾被最亲近的人当成工具,从此不再轻易交付真心",
            "signature_action": "用行动而非语言占有:替对方挡掉所有麻烦,却嘴硬说'顺手'",
            "anti_cliche": "占有欲不等于控制与伤害,他的底线是绝不让对方为难",
        },
        source_type="llm_synth",
        source_citations=[llm_note("纯爱高人气攻原型:冷×占有的反差,守住不PUA底线")],
        confidence=0.6, tags=["纯爱", "双男主", "占有欲", "反差"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="纯爱", sub_genre="现代纯爱",
        slug="bl-st-rainy-umbrella-approach",
        name="雨夜共伞的克制靠近",
        narrative_summary="一把不够大的伞,两个都把伞往对方那边推的人,半边湿透的肩膀比任何告白都响。",
        content_json={
            "scene_trigger": "突如其来的暴雨,只有一把伞,两人不得不靠近",
            "sensory_anchors": ["伞沿滴水连成帘", "湿透的半边肩膀贴着衬衫", "呼吸在冷雨里凝成白雾,距离近到能数睫毛"],
            "turn": "其中一人发现对方一直把伞往自己这边倾,自己那侧早已湿透",
            "payoff": "没有告白,只是默默把伞推回去,又被推回来——克制的拉扯比拥抱更动人",
        },
        source_type="llm_synth",
        source_citations=[llm_note("纯爱标志性糖点:留白与克制,身体语言代替告白")],
        confidence=0.6, tags=["纯爱", "双男主", "共伞", "克制"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="纯爱", sub_genre="现代纯爱",
        slug="bl-pp-misunderstand-reunion",
        name="误会—错过—重逢三幕",
        narrative_summary="一个没说出口的真相把两人推开多年,重逢时身份、立场都变了,旧情却在每个克制的细节里复燃。",
        content_json={
            "setup": "分开的真正原因是一个善意的谎言/一次被截留的信",
            "escalation": "重逢后两人都用冷淡掩饰,误会层层叠加,旁人推波助澜",
            "reversal": "当年那封信/那句话的真相被揭开,所有冷淡都成了自我保护",
            "payoff": "不是狗血和好,而是先把误会一寸寸拆开,再重新认识彼此",
        },
        source_type="llm_synth",
        source_citations=[llm_note("纯爱/言情通用三幕:误会要有信息差逻辑,重逢要先拆误会再复合")],
        confidence=0.58, tags=["纯爱", "误会", "重逢", "破镜重圆"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="百合", sub_genre="百合GL",
        slug="gl-ca-sunny-rescuer",
        name="阳光救赎型GL",
        narrative_summary="像夏天一样的女孩,固执地照进另一个把自己关在阴影里的女孩的世界,救赎不是同情,是平视的并肩。",
        content_json={
            "surface": "外向、温暖、看似没心没肺,实则极度敏锐",
            "core_drive": "见不得另一个人独自扛,会用笨拙又坚定的方式靠近",
            "anti_cliche": "救赎是陪伴与平视,不是把对方变成需要被拯救的弱者",
            "signature_action": "在对方最想消失的时刻,不说大道理,只是递一份热的食物坐下来",
        },
        source_type="llm_synth",
        source_citations=[llm_note("百合高人气原型:阳光×阴郁的双向救赎,守平视不俯视")],
        confidence=0.58, tags=["百合", "双女主", "救赎", "治愈"],
    ),

    # ═══════════════════════ 幻想言情 fantasy-romance ═══════════════════════
    MaterialEntry(
        dimension="world_settings", genre="幻想言情", sub_genre="仙侠情缘",
        slug="fr-ws-heavenly-law-forbidden-love",
        name="天规禁恋的仙凡世界",
        narrative_summary="一个仙凡有别、动情即违天规的世界:上神动心要受天罚,凡人攀情要折寿,爱本身就是最大的逆天。",
        content_json={
            "world_rule": "三界有序,仙不可恋凡,违者渡情劫;情劫不渡则魂飞,渡过则失忆重来",
            "power_cost": "每动一次真情,仙力损一分,这是世界对越界者的标价",
            "geography_model": "九重天—人间—幽冥三界,以忘川与南天门为界,跨界即留痕",
            "unique_conflict_source": "天规是真为维系三界,还是旧神为私欲立的牢笼——主角的爱在质问规则本身",
        },
        source_type="llm_synth",
        source_citations=[wiki("仙侠"), llm_note("幻想言情世界观:把'爱'设成需付代价的逆天行为,制造结构性阻力")],
        confidence=0.6, tags=["幻想言情", "仙侠", "天规", "虐恋"],
    ),
    MaterialEntry(
        dimension="character_archetypes", genre="幻想言情", sub_genre="玄幻言情",
        slug="fr-ca-fated-god",
        name="背负宿命的上神",
        narrative_summary="生来就被预言绑定的上神,要为三界牺牲掉自己的情,他的克制不是无情,是把情藏进每一次替对方挡下的天劫里。",
        content_json={
            "core_wound": "预言说他动情之日即三界倾覆之时,他从出生就被教导不可爱",
            "external_goal": "维系三界平衡,完成宿命",
            "internal_need": "学会爱不是灾难的源头,而是值得为之改写预言的理由",
            "flaw": "习惯用'为你好'替对方做所有决定,剥夺了对方选择的权利",
            "signature_action": "永远站在对方身前替她挡劫,却从不说一个字",
        },
        source_type="llm_synth",
        source_citations=[llm_note("幻想言情男主原型:宿命×克制,情藏于行动,弧光是反抗预言")],
        confidence=0.6, tags=["幻想言情", "上神", "宿命", "克制"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="幻想言情", sub_genre="仙侠情缘",
        slug="fr-st-farewell-before-tribulation",
        name="渡劫前的诀别",
        narrative_summary="渡情劫前夜,渡过去就会忘了彼此,他把所有想说的话酿成一句平静的'别等我',她却把他每根睫毛都刻进眼里。",
        content_json={
            "scene_trigger": "情劫将至,渡劫成功的代价是忘记这段情",
            "sensory_anchors": ["渡劫台上的风把两人的衣袂吹向相反方向", "他指尖的温度一点点变凉", "天边的劫雷压下来,照亮她没落下的泪"],
            "turn": "她忽然明白他选择渡劫不是不爱,是怕自己一旦爱就毁了三界",
            "payoff": "她不阻拦,只把一缕自己的魂偷偷渡给他——'你忘了不要紧,我替我们记着'",
        },
        source_type="llm_synth",
        source_citations=[llm_note("幻想言情高泪点桥段:BE美学,牺牲式诀别+一方默默记住")],
        confidence=0.6, tags=["幻想言情", "渡劫", "诀别", "BE美学"],
    ),

    # ═══════════════════════ 大女主 female-growth ═══════════════════════
    MaterialEntry(
        dimension="character_archetypes", genre="大女主", sub_genre="复仇逆袭",
        slug="fg-ca-returning-deposed-queen",
        name="复仇归来的废后",
        narrative_summary="重生归来的废后,记得前世如何被构陷至死,这一世她不再等谁来救,自己执棋,把当年逼死她的人一个个请进局里。",
        content_json={
            "core_wound": "前世满门被害、自己被废至死,临终才看清谁是真凶",
            "external_goal": "不是复宠,是把权力握进自己手里,让构陷者付出代价",
            "internal_need": "明白翻盘不是为了报复而活,而是为自己活一次",
            "flaw": "初期太信'证据与公道',容易被规则反噬",
            "signature_action": "永远比对手多想三步,用对方的规则反杀对方",
            "anti_cliche": "大女主不靠男人翻盘,感情线是锦上添花而非救命稻草",
        },
        source_type="llm_synth",
        source_citations=[llm_note("大女主核心原型:复仇归来,执棋者而非被救者,感情线不喧宾夺主")],
        confidence=0.62, tags=["大女主", "复仇", "重生", "女强"],
    ),
    MaterialEntry(
        dimension="plot_patterns", genre="大女主", sub_genre="女强权谋",
        slug="fg-pp-stepwise-power-climb",
        name="步步为营的权力攀登",
        narrative_summary="女主从最底层一步步搭建自己的势力,每一阶都用敌人的轻视当垫脚石,把'不被当回事'变成最锋利的武器。",
        content_json={
            "setup": "身处权力最末端,所有人都把她当无害的棋子",
            "escalation": "她借力打力,每一次被低估都被她转化成一次布局",
            "reversal": "当对手终于把她当对手时,她已经掌握了对方最致命的把柄",
            "payoff": "登顶不是靠某个男人加冕,是她自己把对手一一请下牌桌",
            "engine": "信息差+人心算计+对方的傲慢,三者构成她的'金手指'",
        },
        source_type="llm_synth",
        source_citations=[llm_note("大女主权谋主线:被低估→借力→反杀,女性能动性贯穿")],
        confidence=0.6, tags=["大女主", "权谋", "女强", "扮猪吃虎"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="大女主", sub_genre="女强权谋",
        slug="fg-st-court-public-turnaround",
        name="朝堂当众翻盘",
        narrative_summary="满朝文武等着看她身败名裂,她却当众抖出对方伪造的证据链,把一场针对自己的公审,变成对方的断头台。",
        content_json={
            "scene_trigger": "政敌设局,在朝堂/公开场合发难,意图一举定罪",
            "sensory_anchors": ["满殿目光像针扎在背上", "对方笑意里的笃定", "她袖中那卷证据被攥得发烫"],
            "turn": "她不辩解,反而顺着对方的指控往下问,一步步把对方逼进自己挖好的逻辑陷阱",
            "payoff": "证据链当众闭合,定罪的剑调头指向构陷者,她全程声音不高,却字字诛心",
        },
        source_type="llm_synth",
        source_citations=[llm_note("大女主高光桥段:当众翻盘靠逻辑与证据,而非情绪与外援")],
        confidence=0.6, tags=["大女主", "朝堂", "翻盘", "打脸"],
    ),
    MaterialEntry(
        dimension="thematic_motifs", genre="大女主", sub_genre="无CP大女主",
        slug="fg-tm-self-fulfillment",
        name="不靠他人的自我成全",
        narrative_summary="大女主故事的母题内核:女性的价值不由谁来认定,她攀登的终点不是嫁给谁,而是成为自己想成为的人。",
        content_json={
            "motif_core": "把'被拯救/被选择'的传统女性叙事,改写为'自我定义/自我成全'",
            "belief_arc": "从'我要证明给他们看'到'我无需向任何人证明'",
            "expression_in_scene": "拒绝以联姻换权位、在巅峰处放下复仇执念、把机会让给同样被压制的女性",
            "anti_cliche": "不靠男人不等于敌视感情,而是感情不再是女主价值的来源",
        },
        source_type="llm_synth",
        source_citations=[llm_note("大女主母题:自我成全>被救赎,信念弧贯穿全书")],
        confidence=0.6, tags=["大女主", "无CP", "女性成长", "母题"],
    ),
]


async def main() -> None:
    inserted = 0
    errors: list[tuple[str, str]] = []
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}

    print(f"Seeding {len(ENTRIES)} entries to material_library (batch 57)...")
    async with session_scope() as session:
        for entry in ENTRIES:
            try:
                await insert_entry(session, entry, compute_embedding=True)
                inserted += 1
                by_genre[entry.genre or "(通用)"] = by_genre.get(entry.genre or "(通用)", 0) + 1
                by_dim[entry.dimension] = by_dim.get(entry.dimension, 0) + 1
            except Exception as e:  # noqa: BLE001
                errors.append((entry.slug, str(e)))

    print(f"\nBy genre: {dict(sorted(by_genre.items(), key=lambda x: -x[1]))}")
    print(f"By dimension: {dict(sorted(by_dim.items(), key=lambda x: -x[1]))}")
    print(f"\n✓ {inserted} inserted/updated ({len(errors)} errors)")
    for slug, err in errors:
        print(f"  ✗ {slug}: {err}")


if __name__ == "__main__":
    asyncio.run(main())
