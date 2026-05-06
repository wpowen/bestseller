"""
Batch 54: anti_cliche_patterns for thin niche genres + plot_patterns for emerging spaces.
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
    # ═══════════ ANTI-CLICHE: thin niche genres ═══════════
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="洪荒",
        slug="anti-cliche-honghuang-saint-power-creep",
        name="反套路：洪荒『圣人无敌』『弟子全开挂』",
        narrative_summary="洪荒文最大问题：圣人之上无敌 / 主角是某圣人弟子 / 拿先天灵宝 = 无敌；"
                          "导致剧情失去张力。本条记录常见雷同模式 + 可替代的设计。",
        content_json={
            "banned_patterns": {
                "圣人血脉爽": "主角是盘古/三清后裔 / 直接获取至宝",
                "开门即至宝": "主角穿越就有先天灵宝在手 / 不需修炼",
                "弑圣狂魔": "主角竟能杀圣人 / 违背洪荒基本规则",
                "签到流入侵": "把现代签到/系统流硬塞进洪荒 / 破坏氛围",
                "所有人都是穿越": "三清/鸿钧都是穿越者 / 完全无原创",
                "打脸宿命": "和女娲/三清有不解之仇 / 挨打打回 / 套路",
                "封神就是装逼": "把封神大战当成爽文章节 / 不尊重原典",
            },
            "alternative_strategies": {
                "凡人逆袭": "从草根开始 / 靠功德/气运/感悟而非血统",
                "限制至宝": "灵宝有因果 / 用一次损一次 / 不能滥用",
                "圣人有规则": "圣人不出手 / 出手则失圣 / 守住设定",
                "改良签到流": "用『洪荒道争』『因果累积』替代签到",
                "原创人物": "主角不是任何门派弟子 / 自创新道",
                "尊重原典": "封神是悲剧 / 不是简单胜负 / 写出量劫无奈",
                "天道反扑": "主角逆天 / 必有代价",
            },
            "novelty_examples": [
                "主角是『天道意识残片』/ 学习人性",
                "主角拒绝拜师 / 自创第三十七道",
                "主角发现紫霄宫不是终点 / 鸿钧之上还有道",
                "主角用『放下』/ 而非『斩杀』化解封神",
            ],
            "activation_keywords": ["圣人", "至宝", "封神", "原创", "天道", "弑圣"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("洪荒流套路总结")],
        tags=["反套路", "洪荒", "无敌", "至宝"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns", genre="女尊",
        slug="anti-cliche-nuzun-male-faceless",
        name="反套路：女尊『男主只是花瓶』『反性别歧视』",
        narrative_summary="女尊文常见两大病：男主只是漂亮没人格/或反过来变成『男权批判文』；"
                          "好的女尊文应该塑造完整的性别反转世界 + 每个角色都有自己内心。",
        content_json={
            "banned_patterns": {
                "男主花瓶化": "只描写他的美/温柔/为女主奉献 / 没有自己的事业理想",
                "反向男权说教": "把现实男权问题硬塞进女尊 / 变成议论文",
                "女主太完美": "颜值/能力/家世/感情都顶配 / 没有缺点",
                "男配工具人": "几个备选男人都为女主服务 / 没有独立线",
                "性别仇恨煽动": "把所有男性写成反派 / 所有女性都是英雄",
                "强行三妻四夫": "为反转而反转 / 不顾感情逻辑",
                "现代价值观入侵": "用现代男女平等扣问古代世界",
            },
            "alternative_strategies": {
                "男主有事业心": "他可以是大儒 / 名医 / 商贾 / 有自己的人生目标",
                "男主有缺点": "嫉妒 / 软弱 / 任性 / 让他真实",
                "男配独立线": "几位备选男主各有自己的故事和困境",
                "女主有挣扎": "权力 vs 爱情 / 责任 vs 自由 / 她也痛苦",
                "性别复杂": "并非所有女性都是好人 / 有些女性是反派",
                "建构而非批判": "完整建构这个世界的运作 / 而非和现实对比",
                "情感真挚": "不是『嫁给妻主』而是『真的爱上她』",
            },
            "novelty_examples": [
                "男主是史官 / 记录女尊朝代史 / 文人风骨",
                "女主是改革派女皇 / 想推动男权解放 / 受阻",
                "副线是女权VS男权改革派的政治斗争",
                "男主前夫被害 / 复仇引发故事",
                "女主因爱上一位『立志成男』的女子而陷入伦理困境",
            ],
            "activation_keywords": ["女尊", "男主", "性别", "权力", "改革", "性别反转"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("女尊文常见问题分析")],
        tags=["反套路", "女尊", "性别", "男主"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns", genre="萌宠",
        slug="anti-cliche-mengchong-saccharine",
        name="反套路：萌宠『小奶团无敌』『甜到发腻』",
        narrative_summary="萌宠文常见三大问题：小奶团什么都能解决 / 全员不停撒糖 / 主角圣母心泛滥；"
                          "好的萌宠文需要冲突、真痛、严肃成长。",
        content_json={
            "banned_patterns": {
                "小奶团无敌": "幼崽随便就能解决高级威胁 / 失去合理性",
                "全员撒糖": "每章都在喂糖 / 没有冲突无聊",
                "主角圣母心": "见到伤害动物的人就崩溃 / 不切实际",
                "宠兽工具化": "宠兽只为情节服务 / 没自己个性",
                "幼态崇拜": "所有宠都是奶团 / 不允许成熟形态",
                "拒绝真伤害": "宠兽永远不会死 / 没有代价",
                "团宠玛丽苏": "所有人都爱主角和宠 / 没有反派",
            },
            "alternative_strategies": {
                "幼崽有局限": "强大但不万能 / 需要主人智慧配合",
                "节奏调控": "撒糖与冲突交替 / 每5糖配1痛",
                "主角有底线": "为救人不惜牺牲一只宠 / 真实痛苦",
                "宠兽有人格": "每只宠都有自己脾气、爱恶、目标",
                "成熟态展现": "幼崽长大 / 表现出威严 / 离开主人独立闯荡",
                "真伤害真死亡": "重要宠兽阵亡 / 主角必须面对",
                "反派魅力化": "反派也爱宠 / 立场对立但不是纯恶",
            },
            "novelty_examples": [
                "主角的小奶团其实是受伤的成兽 / 隐藏强大",
                "主角因不能阻止宠兽自然死亡而崩溃 / 学会接受",
                "主角拒绝战斗中使用宠兽 / 自己强大",
                "宠兽叛逃归野 / 主角理解放手",
                "反派是被遗弃的优秀驯兽师 / 立场对立但有道理",
            ],
            "activation_keywords": ["小奶团", "撒糖", "圣母", "宠兽", "成熟", "真伤害"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("萌宠文常见问题")],
        tags=["反套路", "萌宠", "小奶团", "圣母"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns", genre="快穿",
        slug="anti-cliche-kuaichuan-formula",
        name="反套路：快穿『公式化任务』『反派工具人』",
        narrative_summary="快穿文最大问题：每个世界一样的剧情公式（穿入→打脸原女主→收割气运）/ "
                          "反派智商下线 / 男主每世都迅速爱上 / 让快穿失去新鲜感。",
        content_json={
            "banned_patterns": {
                "公式化打脸": "每章都是穿入→揭破恶毒女配→打脸→升级",
                "反派智商下线": "所有反派都是傻子 / 不堪一击",
                "男主秒爱": "宿主一出现 / 男主三章爱上 / 真情不真",
                "原女主全恶": "原本剧情女主都是工具反派 / 没有合理性",
                "积分万能解": "什么问题都靠积分换道具搞定",
                "穿越等于胜利": "宿主光环让一切顺利 / 没有挫败",
                "全员穿越": "整个世界都是穿越者 / 失去原生感",
            },
            "alternative_strategies": {
                "异形任务": "并非每个世界都是『虐渣』 / 有救赎/守护/记录",
                "反派有动机": "原女主曾被伤害 / 行为有合理基础",
                "感情慢热": "男主可能十章后才动情 / 真感情",
                "原女主立体化": "她也是受害者 / 被剧情逼成那样",
                "积分有限": "关键时积分不足 / 必须靠智慧",
                "宿主受挫": "穿越光环失效 / 真正绝境",
                "原生人物觉醒": "NPC 觉醒为活生生的人 / 改变剧情",
            },
            "novelty_examples": [
                "宿主穿入受害者『反派』视角 / 替她活完一生",
                "宿主任务是『让原女主真正幸福』 / 不再夺气运",
                "反派觉醒后请求宿主帮助 / 反派联盟",
                "宿主在某世界爱上了 NPC / 不愿离开",
                "宿主发现系统是骗局 / 反系统觉醒",
            ],
            "activation_keywords": ["快穿", "公式化", "打脸", "反派", "宿主", "积分"],
        },
        source_type="llm_synth", confidence=0.82,
        source_citations=[llm_note("快穿文公式化问题")],
        tags=["反套路", "快穿", "公式", "反派"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns", genre="游戏",
        slug="anti-cliche-game-techbabble",
        name="反套路：游戏文『装备秒杀』『版本之子』",
        narrative_summary="网游文常见问题：装备碾压一切 / 主角永远是版本之子 / 公会战流于装备数据比拼；"
                          "好的游戏文应有玩家心理、社群文化、情怀和电竞精神。",
        content_json={
            "banned_patterns": {
                "装备碾压": "主角拿了顶级装备就能赢一切",
                "版本之子": "每个版本都是主角职业最强 / 永不调整",
                "纯数据胜负": "战斗只描写伤害数字 / 没有走位/反应",
                "PVP开挂感": "主角操作神到非人类",
                "工作室全是反派": "所有外挂代练都是邪恶",
                "GM秒响应": "主角投诉立刻处理 / 现实中不存在",
                "土豪一定输": "真实游戏里土豪不一定输",
            },
            "alternative_strategies": {
                "操作 + 装备 + 心理": "三轴并重 / 装备只是辅助",
                "版本周期": "主角职业可能某版本被砍 / 转职/适应",
                "细节描写": "走位/反应/预判/失误 / 让战斗有画面",
                "PVP合理": "主角也会输 / 也会失误",
                "工作室复杂": "也有靠脚本谋生的善良人",
                "GM真实": "封号有冤案 / 投诉无果",
                "土豪有故事": "土豪也曾是穷玩家 / 不要一棍打死",
            },
            "novelty_examples": [
                "主角是后期觉醒 / 不是版本之子 / 靠对战术理解",
                "主角职业被砍后转职 / 重新崛起",
                "公会战写细致心理 / 不只伤害数字",
                "工作室老板是主角曾经的恩人",
                "GM 有自己的故事 / 玩家与他建立关系",
                "主角输给土豪后向他学习",
            ],
            "activation_keywords": ["MMO", "版本之子", "装备", "公会", "PVP", "操作"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("网游文常见套路问题")],
        tags=["反套路", "游戏", "MMO", "PVP"],
    ),

    MaterialEntry(
        dimension="anti_cliche_patterns", genre="美食",
        slug="anti-cliche-food-tongue-god",
        name="反套路：美食『舌神金手指』『感动评审』",
        narrative_summary="美食文常见雷同：主角拥有『超级味觉』 / 每道菜都能感动评审落泪 / 师父都是隐世高人；"
                          "好的美食文应有文化底蕴、技艺细节、人物挣扎。",
        content_json={
            "banned_patterns": {
                "舌神金手指": "尝一口就能复刻一切",
                "评审必哭": "每道料理都让评审眼泪汪汪",
                "隐世师父": "都是深山隐居的料理之神",
                "食材神化": "动辄上古食材/灵食 / 偏离日常",
                "主角无瑕疵": "颜值/口才/技艺/家世样样顶配",
                "宿命对决": "和死敌的料理对决贯穿全文",
                "中国料理碾压": "用『中华文化博大精深』压一切外来料理",
            },
            "alternative_strategies": {
                "舌觉有限": "天才但不是上帝 / 仍需练习",
                "评审多元": "有的感动 / 有的不为所动 / 有人看不出门道",
                "师父平民化": "不是隐士 / 就是隔壁餐馆老厨",
                "食材日常": "用最普通的食材做出震撼料理",
                "主角有缺陷": "脾气暴躁 / 与人不善交际 / 因情绪做坏菜",
                "对决多元": "不只是宿仇 / 也可以是友善切磋",
                "文化对话": "中华、日法、韩泰料理彼此对话 / 不分高下",
            },
            "novelty_examples": [
                "主角因家庭压力放弃料理 / 后来重新开始",
                "评审恶意打分 / 主角靠下次表现翻盘",
                "主角的师父就是路边煎饼大爷",
                "主角用一道家常豆腐征服米其林",
                "宿敌最后变好友 / 一起开餐厅",
                "外来料理（如法餐）启发主角 / 中西融合",
            ],
            "activation_keywords": ["舌神", "评审", "师父", "食材", "宿仇", "中华料理"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("中華一番", "美食套路源头"), llm_note("美食文常见问题")],
        tags=["反套路", "美食", "金手指", "宿仇"],
    ),

    # ═══════════ PLOT PATTERNS ═══════════
    MaterialEntry(
        dimension="plot_patterns", genre="末世",
        slug="moshi-plot-faction-survival",
        name="末世剧情模式：派系博弈生存五幕",
        narrative_summary="末世题材标准五幕：1) 灾变爆发 2) 单兵幸存 3) 加入聚集点 4) 派系冲突 5) 重建文明；"
                          "本模式比单纯生存更具有政治和人性深度。",
        content_json={
            "act_1_outbreak": {
                "时长": "首3章",
                "事件": "灾变爆发 / 主角在普通日常中突遭巨变",
                "情绪": "震惊 / 恐惧 / 求生本能",
                "关键": "立刻确立『日常已不可逆破碎』",
                "示例场景": "上班路上突现丧尸 / 飞机坠毁后到达异世",
            },
            "act_2_lone_survival": {
                "时长": "约5-10章",
                "事件": "孤身一人 / 学习生存 / 觉醒异能",
                "情绪": "孤独 / 谨慎 / 自我成长",
                "关键": "证明主角能独立存活 / 觉醒 1-2 项异能",
                "示例场景": "搜寻物资 / 第一次正面战丧尸 / 异能觉醒",
            },
            "act_3_join_community": {
                "时长": "约10-15章",
                "事件": "遇到幸存者 / 加入聚集点 / 建立关系网",
                "情绪": "犹豫 / 接纳 / 信任",
                "关键": "证明聚集点的脆弱 / 引入派系矛盾",
                "示例场景": "遇到难民 / 解救被攻击者 / 接受领导邀请",
            },
            "act_4_faction_conflict": {
                "时长": "约15-25章",
                "事件": "派系斗争 / 内部叛变 / 主角被卷入决策",
                "情绪": "矛盾 / 愤怒 / 决断",
                "关键": "主角不能再做旁观者 / 必须立场",
                "示例场景": "派系会议 / 内部叛变 / 主角领导一方",
            },
            "act_5_rebuild_civilization": {
                "时长": "约25章后",
                "事件": "重建秩序 / 击退最大威胁 / 文明复苏",
                "情绪": "希望 / 责任 / 平和",
                "关键": "主角成为新秩序的核心或建立者",
                "示例场景": "击退终极敌人 / 重建城邦 / 与盟友共建",
            },
            "subplot_threads": [
                "感情线（与战友/盟友）",
                "异能进化线（觉醒到顶阶）",
                "人性挣扎线（守住底线 vs 灰色生存）",
                "真相线（灾变成因 + 终极敌人身份）",
            ],
            "activation_keywords": ["末世", "幸存", "派系", "聚集点", "异能", "重建"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("末日小说", "灾变文学典型"), llm_note("末世派系博弈结构")],
        tags=["末世", "剧情模式", "派系", "生存"],
    ),

    MaterialEntry(
        dimension="plot_patterns", genre="洪荒",
        slug="honghuang-plot-prequel-arc",
        name="洪荒剧情模式：封神前传六阶段",
        narrative_summary="洪荒文典型时间线：盘古开天→鸿钧讲道→龙凤大劫→巫妖大战→封神→西游；"
                          "本模式为单本写定一段大劫提供六阶段框架。",
        content_json={
            "stage_1_chaotic_birth": {
                "时长": "约1-3章",
                "事件": "主角降生 / 身份揭示（人/兽/灵物）",
                "关键": "确立主角的本源属性 / 拒绝单纯穿越者套路",
            },
            "stage_2_seek_dao": {
                "时长": "约5-10章",
                "事件": "求道之路 / 闻道紫霄宫 / 拜师/自学",
                "关键": "选择派系 / 树立修行哲学",
            },
            "stage_3_join_kalpa": {
                "时长": "约10-20章",
                "事件": "卷入大劫 / 选择阵营 / 与师兄弟产生分歧",
                "关键": "立场决定 / 失去某些至亲",
            },
            "stage_4_inner_conflict": {
                "时长": "约20-30章",
                "事件": "看透阐截两派的局限 / 自我怀疑",
                "关键": "重新定义『道』 / 偏离师门",
            },
            "stage_5_break_or_compromise": {
                "时长": "约30-40章",
                "事件": "化解大劫 / 用非常规手段",
                "关键": "不依赖至宝 / 不依赖圣人",
            },
            "stage_6_witness_or_lead": {
                "时长": "约40章后",
                "事件": "成为见证者或后续大劫的引导者",
                "关键": "传承 / 留偈 / 不强求成圣",
            },
            "core_innovation": {
                "拒绝弑圣": "不写打脸圣人 / 写理解圣人苦衷",
                "拒绝爽文": "封神是悲剧 / 不是简单胜负",
                "拒绝至宝堆": "用智慧/感悟/选择推动 / 不靠先天灵宝",
            },
            "activation_keywords": ["洪荒", "封神", "鸿钧", "三清", "量劫", "大劫"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("封神演义", "原典剧情"), wiki("山海经", "上古叙事")],
        tags=["洪荒", "剧情模式", "封神", "前传"],
    ),

    MaterialEntry(
        dimension="plot_patterns", genre="女尊",
        slug="nuzun-plot-court-romance",
        name="女尊剧情模式：女皇/皇夫宫廷争斗六幕",
        narrative_summary="女尊文标准六幕：女皇登基→选夫风波→朝堂派系→皇夫遇险→大变革→新秩序；"
                          "宫廷+情感+政治三线并行。",
        content_json={
            "act_1_succession": {
                "事件": "女皇登基 / 朝堂动荡 / 各方势力试探",
                "关键": "确立女皇的政治理念 / 树立第一个对手",
            },
            "act_2_consort_selection": {
                "事件": "选皇夫风波 / 各家男子参选 / 暗中较劲",
                "关键": "女皇看重的人 vs 政治需要的人 / 矛盾",
            },
            "act_3_court_factions": {
                "事件": "三大世家结盟反对女皇 / 朝堂博弈",
                "关键": "女皇必须出招 / 重要决策",
            },
            "act_4_consort_threat": {
                "事件": "皇夫被刺 / 揭露暗网",
                "关键": "女皇下令调查 / 真相牵涉旧情",
            },
            "act_5_reform": {
                "事件": "女皇推动重大改革（如允许男子从政）",
                "关键": "动摇千年规制 / 全朝堂震荡",
            },
            "act_6_new_order": {
                "事件": "新秩序建立 / 女皇与皇夫并肩",
                "关键": "完成情感与政治的双重统一",
            },
            "thread_layers": {
                "政治线": "派系→改革→新秩序",
                "情感线": "选夫→遇险→并肩",
                "成长线": "女皇从单纯掌权 → 真正治国",
            },
            "activation_keywords": ["女皇", "皇夫", "选夫", "朝堂", "改革", "宫廷"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[llm_note("女尊宫廷文标准结构")],
        tags=["女尊", "剧情模式", "宫廷", "改革"],
    ),
]


async def main() -> None:
    inserted = 0
    errors: list[tuple[str, str]] = []
    by_genre: dict[str, int] = {}
    by_dim: dict[str, int] = {}

    print(f"Seeding {len(ENTRIES)} entries to material_library (batch 54)...")
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
