"""
Batch 38: Anti-cliche / reverse trope / subversion patterns.
Critical for novelty — these are explicit "do NOT do this" patterns
that reduce homogenization across same-genre books.
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
    # 反套路 - 仙侠开局
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="仙侠",
        slug="anti-cliche-xianxia-opening",
        name="反套路：仙侠开局烂大街 = 必须避免",
        narrative_summary="仙侠开局 80% 雷同：废灵根 + 外门弟子 + 宗门压迫 + 反派同名'方域'。"
                          "本条明确列禁用模式 + 替代写法。",
        content_json={
            "banned_patterns": "1) 废灵根开局 / 2) '外门弟子被欺' / 3) 反派叫'方域' / 4) 主角名字'陆渊 / 林尘 / 萧炎' / 5) 师姐冷艳但暗中关心 / 6) 三年期约 / 7) 第一章必有筑基心境 / 8) '蝼蚁'被反派一句嘲讽 / 9) '不入门派 / 当散修' / 10) 上古传承自动选中废柴",
            "why_avoid": "提示词池共享 → name-space collision / 读者疲劳 / 难有差异化",
            "alternative_openings": "1) 主角是宗门长老（不是新人）/ 2) 主角是反派门派 / 3) 主角是凡人记者卧底入修真 / 4) 主角是凡人猎人遇修士 / 5) 主角是修真界仆人偷学 / 6) 主角是飞升前夕的散修 / 7) 主角是已死之人魂归故里 / 8) 主角是一只灵宠开始 / 9) 主角是反派老 BOSS 转生少年 / 10) 主角是天道意志投生体",
            "name_alternatives": "避免'方'/'陆'/'萧'/'林' 开头 / 用稀有姓 = 谷 / 慕容 / 司马 / 上官 / 姒 / 纳兰 / 乔 / 薛 / 黎 / 闻人",
            "activation_keywords": ["反套路", "雷同", "禁忌套路", "废灵根", "方域", "陆渊", "林尘"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("当代网文同质化诊断")],
        tags=["反套路", "仙侠", "novelty"],
    ),
    # 反套路 - 玄幻三年废柴
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="玄幻",
        slug="anti-cliche-xuanhuan-three-years",
        name="反套路：'三年废柴 / 三年之约'重灾区",
        narrative_summary="玄幻爽文模仿《斗破苍穹》'萧炎三年废柴' + '与纳兰嫣然三年之约'设定。"
                          "几乎所有废柴流都套用 → 烂大街。",
        content_json={
            "banned_patterns": "1) '我萧 X / 林 X / 韩 X，X 年 X 月，必踏平你 X 家'/ 2) 三年废柴打脸 / 3) 退婚 + 退亲流 / 4) 第一章必有家人嘲讽 / 5) 月底必有家族测试 / 6) 觉醒玄阶以下血脉 / 7) 突然觉醒上古血脉 / 8) 收异火 / 9) 师傅是寄居戒指的老头",
            "why_avoid": "天蚕土豆四作《斗破》《武动》《大主宰》《元尊》本身就是同模式 / 模仿者数百本",
            "alternative_setups": "1) 主角是反派少爷 / 2) 主角是隐藏血脉子嗣（不要从废柴觉醒）/ 3) 主角是全族唯一存活 / 4) 主角是仇家潜入 / 5) 主角是穿越者 + 携带前世修为 / 6) 主角是机器人 + AI 觉醒 / 7) 主角是普通学院学生 / 不是天之骄子也不是废物",
            "first_chapter_alternatives": "不要打脸开场 / 不要嘲讽开场 / 用'迷案 / 任务 / 神秘出场'代替",
            "activation_keywords": ["反套路", "三年废柴", "退婚流", "斗破套路", "萧炎模板", "novelty"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("玄幻爽文同质化")],
        tags=["反套路", "玄幻", "novelty"],
    ),
    # 反套路 - 总裁文
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="言情",
        slug="anti-cliche-ceo-romance",
        name="反套路：总裁文'霸道总裁 + 灰姑娘'重灾区",
        narrative_summary="言情霸道总裁套路烂大街。"
                          "壁咚 / 公主抱 / 强吻 / 突然出现救场 = 几乎每本都有。",
        content_json={
            "banned_patterns": "1) 男主壁咚 / 2) 男主公主抱 / 3) 男主强吻女主 / 4) 男主送限量手表 / 5) 突然出现救场 / 6) 女主在咖啡店打工 / 7) 男主神秘前妻 / 8) 女主黑历史撞 / 9) 男配狂虐女主 / 10) 雨中追逐 / 11) 男主送钻戒",
            "why_avoid": "重复几千本言情 / 读者完全可猜后续 / 没有差异化",
            "alternative_dynamics": "1) 男女主对等关系 / 2) 女主是男主上司 / 3) 男女平凡都是普通工薪族 / 4) 没有'灰姑娘 vs 王子' / 5) 男主有缺陷 + 女主接受 / 6) 互相竞争职场 / 7) 异国相遇 / 8) 多年好友变恋人 / 9) 失婚再恋 / 10) 主角间无身份差距",
            "modern_trends_2025": "弱化霸道 + 强调成长 / 反偶像剧 / 现实主义甜文 / 慢节奏 / 双向奔赴",
            "activation_keywords": ["反套路", "霸道总裁", "灰姑娘", "壁咚", "公主抱", "强吻", "novelty"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("言情同质化")],
        tags=["反套路", "言情", "novelty"],
    ),
    # 反套路 - 都市赘婿
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="都市",
        slug="anti-cliche-urban-zhuangxu",
        name="反套路：赘婿流'扮猪吃虎'已疲劳",
        narrative_summary="赘婿流 2018-2020 红极一时。"
                          "现已重复滥用 / 没有新意。"
                          "反套路改造或不写。",
        content_json={
            "banned_patterns": "1) 入赘三年装废 / 2) 妻子家族下马威 / 3) 突然某次危机露脸 / 4) 各方势力跪舔 / 5) 妻子真心反追 / 6) 主角拒绝继续低调 / 7) '退婚书 / 入赘契约'桥段 / 8) 妻子家族败落主角接管",
            "why_avoid": "《赘婿》之后数百本仿作 / 网文 2020 年后已基本不写 / 读者对'扮猪吃虎'极度疲劳",
            "alternative_directions": "1) 主角真的废 + 一步一步成长 / 2) 主角故意装废但被妻子识破 + 共同布局 / 3) 主角妻子比主角更强 / 4) 不入赘 / 用其他身份隐藏 / 5) 主角身份从一开始就明牌（不藏拙）/ 6) 把'扮猪吃虎'改为'识时务'（被认出而非主动揭露）",
            "modern_trends_2025": "彻底放弃赘婿流 / 转写'职场进阶'/'创业青年'/'平凡进阶'",
            "activation_keywords": ["反套路", "赘婿", "扮猪吃虎", "上门女婿", "退婚流", "novelty"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("都市赘婿流疲劳")],
        tags=["反套路", "都市", "novelty"],
    ),
    # 反套路 - 重生流第一周
    MaterialEntry(
        dimension="anti_cliche_patterns", genre=None,
        slug="anti-cliche-rebirth-week-one",
        name="反套路：重生回到第一周'囤资源 + 杀仇人'套路",
        narrative_summary="重生流（仙侠 / 都市 / 末世通用）开局必囤资源 + 杀仇人。"
                          "重复百本以上 → 节奏完全可预测。",
        content_json={
            "banned_patterns": "1) 重生回到大学 / 入门 / 开店第一天 / 2) 第一件事冲股票 / 房地产 / 矿场 / 3) 第二件事见朋友 / 投资 / 抓住机会 / 4) 第三件事'你叫什么 / 我以前不知道你的名字' = 与未来仇人结识 / 4) 第一周完美布局 / 5) 第二周开始打脸 / 6) 第三周已经富甲一方",
            "why_avoid": "节奏完全可预测 / 读者已知主角下一步 / 缺乏意外",
            "alternative_setups": "1) 重生但失去原有记忆 / 必须重头来 / 2) 重生但前世记忆是假的（被植入）/ 3) 重生但回到一个不该回的时间（前世他根本不在那）/ 4) 重生但身体是别人 / 5) 重生但只能进 1 小时 / 6) 重生第一周做错事故意 = 改变命运",
            "narrative_techniques": "增加未知 / 让重生者也意外 / 让读者也猜不到下一步 / 用反向选择制造悬念",
            "famous_subversions": "《魔道祖师》魏无羡重生 = 失去前世修为 / 《无心法师》前世记忆碎片 / 《如懿传》改命选错路",
            "activation_keywords": ["反套路", "重生", "前世记忆", "囤资源", "打脸仇人", "novelty"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("重生流通用反思")],
        tags=["反套路", "重生", "通用", "novelty"],
    ),
    # 反套路 - 系统流
    MaterialEntry(
        dimension="anti_cliche_patterns", genre=None,
        slug="anti-cliche-system-stream",
        name="反套路：系统流'签到 / 任务面板'已饱和",
        narrative_summary="2014 年后系统流爆发 → 2024 年完全饱和。"
                          "'叮 / 签到成功 / 获得 X' = 老套到读者直接弃文。",
        content_json={
            "banned_patterns": "1) '叮 / 签到成功' / 2) '请宿主 X' / 3) '任务奖励 X' / 4) 系统冷漠工具人 / 5) 签到 7 天大礼包 / 6) 抽卡系统 / 7) '主角你这都行' / 8) 系统装中二",
            "why_avoid": "读者一看'叮'立即弃文 / 太多本套路完全相同 / 系统人设也雷同（傲娇 / 冷漠）",
            "alternative_systems": "1) 没有系统 / 主角靠真本事 / 2) 系统是反派（每完成一次任务都让主角更接近他设的陷阱）/ 3) 系统是主角前世意识 / 4) 系统是要主角偿还前世债务 / 5) 系统给的反向任务（要主角做坏事）/ 6) 系统已死亡 / 主角发现是失败前任 / 7) 系统是 AI 觉醒爱上主角",
            "modern_trends_2025": "去系统化 + 真实成长 + 实力派 / 系统流由饱和转向衰退",
            "activation_keywords": ["反套路", "系统流", "签到", "任务面板", "金手指", "饱和", "novelty"],
        },
        source_type="llm_synth", confidence=0.94,
        source_citations=[llm_note("系统流疲劳分析")],
        tags=["反套路", "系统流", "novelty"],
    ),
    # 反套路 - 武侠门派之争
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="武侠",
        slug="anti-cliche-wuxia-sect-war",
        name="反套路：武侠'正魔不两立 / 江湖联盟'已陈腐",
        narrative_summary="传统武侠'正派联盟围剿魔教'套路使用过度。"
                          "金庸已用尽 / 后来者难超越。",
        content_json={
            "banned_patterns": "1) 武林大会必有内奸 / 2) 正派联盟围攻日月神教 / 3) 主角拜入名门正派 / 4) 师姐看上主角 / 5) 主角必有六脉神剑般的绝学 / 6) 反派必是邪魔外道 / 7) 主角必赢武林盟主 / 8) 大反派死前必长篇独白",
            "why_avoid": "金庸已写尽 / 后人模仿无法超越 / 读者对'正魔大战'极度疲劳",
            "alternative_directions": "1) 灰色道德武侠（《雪中悍刀行》）/ 2) 武侠 + 朝廷 / 主角是朝廷锦衣卫不是江湖人 / 3) 武侠 + 商战 / 主角是镖师 / 票号 / 4) 武侠 + 探案 / 主角是仵作 / 神捕 / 5) 武侠 + 江湖之外 / 主角是和尚 / 道士 / 工匠不是侠客 / 6) 武侠 + 衰败 / 写江湖灭亡 / 不是江湖兴起",
            "modern_subversions": "《雪中悍刀行》 / 《英雄志》/ 《飞剑问道》/ 都拒绝传统正魔二分",
            "activation_keywords": ["反套路", "武侠", "正魔不两立", "江湖联盟", "武林大会", "novelty"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("武侠题材饱和")],
        tags=["反套路", "武侠", "novelty"],
    ),
    # 反套路 - 第一人称女主
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="言情",
        slug="anti-cliche-passive-female-lead",
        name="反套路：女主被动 + 等待救援",
        narrative_summary="传统言情女主多被动 = 现代女权意识下严重过时。"
                          "新女主必须能动 + 有目标 + 不依赖男主。",
        content_json={
            "banned_patterns": "1) 女主被欺负只会哭 / 2) 等男主来救 / 3) 女主无能力靠美貌 / 4) 女主谈恋爱无其他人生目标 / 5) 女主撞墙绝食式抗议 / 6) 女主'我什么都不要 / 只要你' / 7) 女主家境贫寒等男主接济",
            "why_avoid": "读者越来越拒绝被动女主 / 女权意识 / 反PUA / 现代女性独立 / 男主来救 = 玛丽苏",
            "alternative_designs": "1) 女主有事业 / 不靠男主 / 2) 女主主动追求 / 不被动等待 / 3) 女主救男主 / 反向 / 4) 双方对等 / 互相成长 / 5) 女主独行 / 不需男主 / 6) 女主有缺陷 / 不完美 / 7) 女主能力强 / 男主能力一般",
            "modern_trends_2025": "大女主 / 双女主 / 独立女性 / 拒绝玛丽苏 / 拒绝白莲花 / 拒绝圣母",
            "activation_keywords": ["反套路", "玛丽苏", "白莲花", "被动女主", "等待救援", "现代女性", "novelty"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("当代女权下的言情进化")],
        tags=["反套路", "言情", "女权", "novelty"],
    ),
    # 反套路 - 反派设计
    MaterialEntry(
        dimension="anti_cliche_patterns", genre=None,
        slug="anti-cliche-villain-design",
        name="反套路：反派'纯粹邪恶 / 蠢笨自大'已陈腐",
        narrative_summary="老套反派 = 蠢 + 自大 + 临死独白 + 没有动机。"
                          "现代叙事必须给反派完整动机 + 灰色道德。",
        content_json={
            "banned_patterns": "1) 反派一定是恶 / 没有理由 / 2) 反派死前必长篇独白 / 3) 反派蠢得连主角主动透露弱点都信 / 4) 反派只会喊'啊 / 你竟敢' / 5) 反派笑声永远'呵呵呵 / 哈哈哈' / 6) 反派身边一定有蠢手下 / 7) 反派和主角必有'必杀技对决'",
            "why_avoid": "读者已厌倦扁平反派 / 现代叙事崇尚灰色道德 / 蠢反派让主角胜利毫无成就感",
            "alternative_designs": "1) 反派的目标合理 / 反派'对'但方法错（《冰与火》异鬼应对人类破坏自然）/ 2) 反派比主角更聪明（主角靠运气 + 团队赢）/ 3) 反派与主角对等（双 BOSS 结构）/ 4) 反派曾是好人（堕落 / 阿尔萨斯）/ 5) 反派死前不独白（沉默死去）/ 6) 反派胜利（让主角失败 + 留悬念）",
            "famous_complex_villains": "Walter White（绝命毒师 / 主角变反派）/ Magneto（X-Men / 屠杀但理由强 = 集中营幸存者）/ Jamie Lannister（GoT / 复杂）/ Joker（混沌但有哲学）/ Kratos（神之战）",
            "modern_trends_2025": "灰色道德 / 反派当主角 / 没有纯反派 / 复杂动机",
            "activation_keywords": ["反套路", "反派", "灰色道德", "复杂反派", "Magneto", "Walter White", "novelty"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("当代复杂反派趋势")],
        tags=["反套路", "通用", "novelty"],
    ),
    # 反套路 - 美女如云后宫
    MaterialEntry(
        dimension="anti_cliche_patterns", genre=None,
        slug="anti-cliche-harem",
        name="反套路：'后宫流 / 美女如云'已饱和",
        narrative_summary="男性向网文'美女如云 / 后宫'套路使用过度。"
                          "2020 年后女性读者占大半 → 后宫文衰退。",
        content_json={
            "banned_patterns": "1) 主角身边必 X 美女 / 2) 美女各有特色（御姐 / 萝莉 / 高冷 / 傲娇 / 校花）/ 3) 美女主动倒贴 / 4) 主角对美女'冷淡'但暗中享受 / 5) '收宫'桥段 / 6) 一夫多妻和谐共处",
            "why_avoid": "女性读者占网文主力 / 不喜欢主角'多妻' / 男性读者也开始疲劳 / 政治正确变化",
            "alternative_designs": "1) 一对一深度感情 / 不收宫 / 2) 主角真心爱一人 / 其他女性是朋友 / 3) 女主多但与主角无情感 / 都是合作伙伴 / 4) 反向后宫 / 主角是被多个 NPC 追的 / 但主角拒绝 / 5) 主角是同性恋 / 跨性别（极少数）",
            "modern_trends_2025": "无后宫 / 一对一甜文 / 双向奔赴 / 平等关系",
            "activation_keywords": ["反套路", "后宫", "美女如云", "收宫", "一夫多妻", "novelty"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("当代男频后宫文衰退")],
        tags=["反套路", "通用", "novelty"],
    ),
    # 反套路 - 反派同名
    MaterialEntry(
        dimension="anti_cliche_patterns", genre=None,
        slug="anti-cliche-villain-name-collision",
        name="反套路：反派同名'方域 / 林墨 / 萧承'重灾",
        narrative_summary="提示词池共享导致同题材书反派同名。"
                          "用户反映：仙侠两本书第 1 章反派都叫'方域'。",
        content_json={
            "banned_villain_names": "1) 方域 / 方天 / 方启 / 2) 林墨 / 林尘 / 林炎 / 3) 萧承 / 萧炎 / 萧夜 / 4) 古青 / 古天 / 5) 陆渊 / 陆天 / 6) 苏沐 / 苏寒 / 7) 风寒 / 风晨 / 8) 慕容白 / 慕容凡 / 9) 龙傲天 / 龙天 / 10) 叶秋 / 叶辰",
            "why_avoid": "提示词池共享 → name-space collision / 用户能直接看出两本书是同模板",
            "alternative_naming_strategies": "1) 用稀有姓 = 谷 / 慕容 / 司马 / 上官 / 姒 / 纳兰 / 乔 / 薛 / 黎 / 闻人 / 纪 / 越 / 步 / 顾 / 蓝 / 严 / 简 / 苗 / 言 / 段 / 2) 古怪名字 = 二字 + 三字 + 四字混搭 / 3) 单字名（魏无羡）/ 4) 复古名（卫长青）/ 5) 半西式名（莫离 / 凯瑟琳）",
            "naming_resources": "百家姓罕见姓 / 《楚辞》《诗经》取名 / 古代官名 / 山川名 / 草药名（半夏 / 苍朮）",
            "activation_keywords": ["反套路", "同名雷同", "方域", "name-space", "稀有姓", "命名", "novelty"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("用户反馈直接证据")],
        tags=["反套路", "通用", "命名", "novelty"],
    ),
    # 反套路 - 末世第一周
    MaterialEntry(
        dimension="anti_cliche_patterns", genre="末世",
        slug="anti-cliche-doomsday-week-one",
        name="反套路：末世'重生第一周囤物资'套路",
        narrative_summary="末世重生流模板化严重。"
                          "几乎所有重生末世主角都'回到大灾前一周 + 抢超市物资'。",
        content_json={
            "banned_patterns": "1) 重生回末世前 7 天 / 2) 抢超市物资 / 3) 抢加油站汽油 / 4) 抢药店药品 / 5) 屯粮 1 年 / 6) 与渣男前夫 / 渣女前妻分手 / 7) 第一日丧尸爆发 / 主角早已准备 / 8) 救父母 + 弟妹 / 不救情人",
            "why_avoid": "套路完全相同 / 读者一看'重生末世'就知道前 5 章 / 缺乏新鲜感",
            "alternative_setups": "1) 重生但只回末世 1 个月（不是 1 周）/ 2) 重生回末世 5 年后（已是末世大佬）/ 3) 重生但身体是丧尸 / 主角必须不被发现 / 4) 重生但前世记忆是假的（被实验改造）/ 5) 重生回末世前 30 年 / 主角必须'阻止末世发生' / 6) 平行末世 / 不是重生而是穿越",
            "modern_trends_2025": "去重生化 / 现代末世主角直接面对 / 末世社群构建 + 心理深度",
            "activation_keywords": ["反套路", "末世重生", "囤物资", "丧尸爆发", "前世记忆", "novelty"],
        },
        source_type="llm_synth", confidence=0.93,
        source_citations=[llm_note("末世题材饱和")],
        tags=["反套路", "末世", "重生", "novelty"],
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
