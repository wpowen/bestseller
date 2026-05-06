"""Batch 46: scene_templates - genre-specific signature scenes (12 entries)

For under-served scene categories:
- 美食: 料理对决, 厨房新人day1
- 重生: 第一次睁眼+认现实
- 机甲: 第一次驾驶+同步率突破
- 快穿: 任务世界破局
- 历史: 科举榜眼+ 殿试
- 宫斗: 太后召见+ 凤袍加身
- 言情: 生离死别+ 葬礼+ 婚礼
- 武侠: 论剑试剑+ 江湖告别
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
        dimension="scene_templates", genre="美食",
        slug="scene-food-cooking-duel",
        name="美食标志：料理对决+ 评委盲品",
        narrative_summary="美食题材的核心场景：两位厨师在限时内做出主题菜+ 评委盲品+ 翻牌定胜负。要写出食物的色香味+ 厨师的紧张+ 评委的味觉细节。",
        content_json={
            "scene_structure": "1) 主题揭晓（突然来一个挑战食材）/ 2) 限时（30-60 分钟，倒计时压力）/ 3) 厨师并行做菜（剁切声+ 火苗声+ 油爆声）/ 4) 上桌（造型+ 香气）/ 5) 评委盲品（每口闭眼+ 描述层次）/ 6) 翻牌+ 评分+ 掌声/失望",
            "sensory_layers": "色（颜色搭配+摆盘）+ 香（开盖瞬间的香气波）+ 味（咸甜酸辣鲜的层次）+ 触（口感+爽脆/绵滑）+ 时（前调-中调-后调）",
            "psychological_layer": "厨师内心: 害怕评分低/ 害怕辜负师傅/ 害怕被对手羞辱；评委内心: 不能被名气影响/ 必须诚实",
            "model_examples": "《料理东西军》+ 《Master Chef》+ 《将太の寿司》+ 《食戟之灵》",
            "anti_cliche": "不要纯写'食物超神逆转'；让美食对决有真实瑕疵（一道菜咸了+一道菜嫩度不够），靠综合分胜出",
            "activation_keywords": ["美食", "料理对决", "盲品", "Master Chef", "食戟"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("MasterChef"), llm_note("食戟之灵+ 将太の寿司+ 中华小当家综合")],
        tags=["scene", "美食", "对决", "盲品"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="重生",
        slug="scene-rebirth-eyes-opening",
        name="重生标志：第一次睁眼+ 认清现实",
        narrative_summary="重生题材必有的开篇场景：主角从死前/失败前重生+ 睁眼看到熟悉但已经忘记的环境+ 慢慢确认重生事实+ 制定计划。是整本书的情感锚。",
        content_json={
            "scene_structure": "1) 死前/绝望瞬间（前一段记忆）/ 2) 黑暗中的转换（失重+ 心跳+ 时空错位感）/ 3) 第一缕光（睁开眼睛）/ 4) 不熟悉的天花板（确认不是死后）/ 5) 触摸自己的脸（确认是自己）/ 6) 看日历/手机/报纸（确认时间点）/ 7) 第一个出现的人（亲人/敌人/恋人，决定剧情走向）/ 8) 眼泪+ 决心",
            "sensory_anchors": "光线（早晨阳光+蚊帐+老式时钟）+ 气味（妈妈的菜+ 中学校服+ 童年家具的木味）+ 声音（旧手机铃声+ 90 年代电视广告+ 老歌）+ 触觉（年轻的皮肤+ 没有伤疤的身体）",
            "emotional_layer": "1) 不可置信 / 2) 试图理性化（梦/错觉）/ 3) 触摸+证据慢慢确认 / 4) 突然崩溃大哭（亲人还在世+ 错过的事都还没错过）/ 5) 决心：'这次我一定...'",
            "anti_cliche": "不要写'醒来 1 分钟就接受+ 立刻规划'；要写半小时-1 天的认知重整+ 反复怀疑+ 实际事件触发的崩溃",
            "activation_keywords": ["重生", "第一次睁眼", "认清现实", "时间点", "重来一次"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("辛夷坞《致青春》+ 八月长安《最好的我们》重生开篇模板综合")],
        tags=["scene", "重生", "开篇", "睁眼"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="机甲",
        slug="scene-mecha-first-pilot",
        name="机甲标志：第一次驾驶+ 同步率破百",
        narrative_summary="机甲题材必有的爽点：主角第一次进入驾驶舱+ 神经接驳+ 同步率突破+ 机甲苏醒+ 击败强敌。要让读者感受到金属共振+ 神经痛+ 灵魂注入。",
        content_json={
            "scene_structure": "1) 主角进入驾驶舱（暗+ 冷+ 金属味）/ 2) 神经接驳（冰冷探针刺入颈+ 失重感）/ 3) 视觉切换（接管机甲全身视角）/ 4) 第一次抬手（机甲缓慢响应）/ 5) 第一次跑（震动+ 风压）/ 6) 同步率从 30% → 50% → 80% → 95%（每跳一阶有撕裂痛）/ 7) 突破临界（灵魂入机+ 极致掌控）/ 8) 击败强敌的爽点",
            "sensory_layers": "听（机甲伺服马达声+ 关节摩擦+ 金属共振+ 警告音）+ 触（驾驶椅夹紧+ 神经探针冰凉+ 高 g 负载）+ 视（多屏幕信息+ 全息标记+ 光学透视）+ 痛（同步率高=神经过载，太阳穴+ 脊柱+ 关节钝痛）",
            "psychological_layer": "1) 害怕（这是第一次实战，可能死） / 2) 渴望（机甲带来力量感） / 3) 临界突破（短暂失神，看到机甲'灵魂'）/ 4) 完成后的虚脱（呕吐+ 流鼻血+ 眼部充血）",
            "model_examples": "Evangelion / Pacific Rim / Gundam Seed / Code Geass",
            "anti_cliche": "不要纯写'第一次就 100% 同步打败 boss'；让同步率有一次失败+ 退舱+ 主角被骂，再战才行",
            "activation_keywords": ["机甲", "驾驶舱", "同步率", "神经接驳", "Evangelion"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("Neon_Genesis_Evangelion"), wiki("Pacific_Rim"), wiki("Mobile_Suit_Gundam_SEED")],
        tags=["scene", "机甲", "驾驶", "同步率"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="快穿",
        slug="scene-rapid-transmigration-world-break",
        name="快穿标志：第 N 个世界的破局点",
        narrative_summary="快穿题材每个世界必有的'破局点'：主角进入新世界+ 接收任务+ 收集信息+ 找到关键人物+ 破解关键事件+ 任务完成出世界。每个世界 5-15 章为典型节奏。",
        content_json={
            "world_arc_structure": "1) 入世界（系统提示+ 身份信息+ 主线任务）/ 2) 适应期（前 1-2 章观察+ 假装是原身）/ 3) 信息搜集（探听+ 阅读原文+ 搞清剧情时间线）/ 4) 关键人物建立联系（男主/反派/挚友） / 5) 第一个转折（打破原剧情节点）/ 6) 高潮博弈（与原剧女主/反派直面）/ 7) 任务完成 / 8) 出世界（情感回收+ 系统结算）",
            "key_skills": "1) 演技（不能让人发现是穿越者）/ 2) 信息分析（快速理清原剧情）/ 3) 人际操纵（把男主从原女主那里带走/拆散）/ 4) 时间管理（在被淘汰前完成）",
            "world_type_pool": "1) 校园世界（学神/学渣/学霸 3 选 1）/ 2) 古言世界（庶女/嫡女/妾室）/ 3) 现代豪门（大小姐/继女/秘书）/ 4) 末日世界（幸存者/异能者）/ 5) 科幻世界（宇航员/AI/克隆人）/ 6) 修真世界（炼气/筑基/元婴）",
            "anti_cliche": "不要每个世界都'轻松破局+ 男主必爱我'；让每个世界至少 1 次失败+ 1 次被认出+ 1 次和系统冲突",
            "activation_keywords": ["快穿", "破局", "任务世界", "原女主", "系统"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[llm_note("酱子贝+ 莺莺草+ 木兰竹快穿头部模板综合")],
        tags=["scene", "快穿", "世界", "破局"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="历史",
        slug="scene-history-imperial-exam",
        name="历史标志：殿试+ 状元及第",
        narrative_summary="科举题材必有的高光：殿试+ 皇帝亲点状元+ 跨马游街+ 御赐宴。是寒窗十年的转折点+ 阶级跃迁的高光时刻。要写出紧张+ 自豪+ 命运感。",
        content_json={
            "scene_structure": "1) 殿试当日（凌晨 4 时起+ 太监引领+ 大殿外集合）/ 2) 入殿（金水桥过+ 太和殿前跪拜）/ 3) 题目下发（皇帝亲题，常是治国策论）/ 4) 限时 4 时辰作答（笔尖颤抖+ 一字一斟酌）/ 5) 收卷+ 等待（24h 阅卷）/ 6) 唱名（鸿胪寺司仪三声叫'状元 X X')/ 7) 跨马游街（红绸+ 百姓夹道+ 簪花+ 父老激动）/ 8) 御赐宴（皇帝亲赐+ 共饮）",
            "sensory_layers": "晨钟+ 香烛+ 金砖地+ 皇帝低头不可直视（只能眼角余光）+ 笔墨+ 宣纸触感+ 金鸡报晓+ 红绸打飘+ 民众欢呼",
            "psychological_layer": "1) 凌晨自我怀疑 / 2) 入殿时窒息感 / 3) 写策论时'天下兴亡'感 / 4) 等待时焦虑+ 想到死去的父母 / 5) 唱名时心脏停跳 / 6) 跨马时双腿打飘+ 想哭",
            "famous_examples": "范仲淹（北宋状元）+ 文天祥（南宋状元）+ 张謇（清末状元）+ 杨慎（明代状元）",
            "anti_cliche": "不要纯写'文采无双必中状元'；要写阅卷过程中的考官派系+ 皇帝的政治平衡+ 殿试前的紧张腹泻",
            "activation_keywords": ["历史", "殿试", "状元", "金榜题名", "跨马游街"],
        },
        source_type="research_agent", confidence=0.95,
        source_citations=[wiki("Imperial_examination"), llm_note("沈起《宋代科举制度研究》+ 《明代科举》综合")],
        tags=["scene", "历史", "科举", "状元"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="宫斗",
        slug="scene-palace-empress-audience",
        name="宫斗标志：太后召见+ 试探性对话",
        narrative_summary="宫斗题材的高频场景：年轻嫔妃被太后召见+ 表面慈祥实际试探+ 字字珠玑+ 一不小心就万劫不复。要写出沉默的力量+ 茶香的层次+ 每个动作的政治意义。",
        content_json={
            "scene_structure": "1) 太监引路（穿过 N 重宫门）/ 2) 阶下跪（不能抬头）/ 3) 太后让起（赐座次+ 等级象征）/ 4) 寒暄（'近来可好'）/ 5) 第一记试探（家世问题）/ 6) 第二记试探（与某人关系）/ 7) 第三记试探（皇帝最近龙体）/ 8) 突然送礼（玉镯/簪子/宫女）— 接还是不接？/ 9) 告退（太监示意+ 后退三步出门）",
            "subtext_layers": "每句话都有 3 层意思：表面+ 试探+ 警告。每个动作都有政治意义（茶杯端起=信号 / 宫女站位=立场）",
            "sensory_layers": "茶香（特定品种暗示恩宠等级）+ 殿内檀香（缓和气氛）+ 太后衣料窸窣声+ 宫女屏息声+ 年轻嫔妃心跳声+ 自己手心的汗",
            "psychological_layer": "1) 入殿前的腹泻 / 2) 跪下时膝盖麻 / 3) 听问话时反复推敲'她到底想知道什么' / 4) 答完一句后的眩晕（说错没？）/ 5) 离开时双腿打飘",
            "model_examples": "甄嬛传 / 宫廷计 / 步步惊心",
            "anti_cliche": "不要纯写'年轻嫔妃机智应答完美'；要写一次失误（多说半句）+ 太后微微皱眉+ 后续被穿小鞋",
            "activation_keywords": ["宫斗", "太后", "召见", "试探", "甄嬛传"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("流潋紫《甄嬛传》+ 桐华《步步惊心》宫廷对话模板综合")],
        tags=["scene", "宫斗", "太后", "试探"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="言情",
        slug="scene-romance-funeral-rain",
        name="言情标志：雨中葬礼+ 生离死别",
        narrative_summary="言情题材的最催泪场景：男女主一方死后的葬礼+ 雨中独立+ 黑白照片+ 老歌+ 旁人慰问。是整本书情感的最高点。要写出哀而不伤+ 时间感+ 留白。",
        content_json={
            "scene_structure": "1) 葬礼前夜（旁人安排+ 主角昏睡） / 2) 早晨醒来（不愿穿黑色） / 3) 灵堂（黑白照片+ 鲜花+ 老歌） / 4) 朋友亲人哭诉（主角呆滞） / 5) 主角第一次发声（说出意外的话）/ 6) 雨开始下（突如其来）/ 7) 走出灵堂站在雨里（不撑伞）/ 8) 闪回从前（黑白镜头切换）/ 9) 一个旧物件被发现（信/手机/戒指）/ 10) 主角终于哭出来",
            "sensory_layers": "黑白照片+ 鲜花（百合+ 白菊+ 黄玫瑰）+ 老歌（双方爱听的+ 一首特别的）+ 雨声（屋檐+ 落在伞上+ 落在地上）+ 烛火+ 香味（特定香水留下的气息）",
            "psychological_layer": "1) 否认（不可能他死了）/ 2) 麻木（按流程做事）/ 3) 突然崩溃（看到旧物件）/ 4) 雨中独立（接受现实）/ 5) 平静（决定怎么活下去）",
            "key_dialogue_moments": "1) '他生前最爱百合' / 2) 旧友回忆某段往事 / 3) 主角对照片说话 / 4) 离开前最后看一眼",
            "model_examples": "《情书》《Atonement》《Titanic》（Rose 80 years later）",
            "anti_cliche": "不要纯写哭哭啼啼；用沉默+ 留白+ 旧物+ 雨景营造哀而不伤",
            "activation_keywords": ["言情", "葬礼", "雨中", "生离死别", "情书"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("岩井俊二《情书》、Atonement、Titanic 言情高潮模板综合")],
        tags=["scene", "言情", "葬礼", "雨"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="武侠",
        slug="scene-wuxia-sword-test",
        name="武侠标志：山顶论剑+ 二人一剑",
        narrative_summary="武侠题材的经典场面：两位顶级剑客在山顶论剑+ 不为输赢只为印证武学+ 雪山+ 狂风+ 一招定胜负+ 留下传说。是武侠精神的极致表达。",
        content_json={
            "scene_structure": "1) 山顶相约（多年前的约定） / 2) 双方独自登山（一年磨剑）/ 3) 在风雪中相见（互相点头）/ 4) 探讨武学（不是打架，是学术交流）/ 5) 第一招试探+ 互赞 / 6) 高潮一招（双方真力）/ 7) 收剑（互相倒退）/ 8) 道别（'十年后再约'/'此别永诀'）",
            "philosophical_layer": "武侠精神=用剑而不爱杀；论剑=印证+ 切磋+ 互敬+ 不为名利。每个剑客都把对手视为'唯一懂自己'的人",
            "sensory_layers": "山顶风（猎猎打旗）+ 雪粒（打在脸上）+ 剑鸣（出鞘+ 真力共振）+ 远处雾海+ 阳光从云缝洒下+ 鸟鸣（古典氛围）",
            "famous_examples": "《射雕》华山论剑（东邪西毒南帝北丐）+ 《笑傲江湖》风清扬指点令狐冲+ 《天龙八部》萧峰vs慕容复",
            "anti_cliche": "不要纯写'打架'；论剑要有学术性+ 哲学性+ 互敬。胜者也常常落泪（败者是知己）",
            "activation_keywords": ["武侠", "论剑", "山顶", "华山论剑", "知己"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("金庸《射雕英雄传》+ 《笑傲江湖》+ 《天龙八部》论剑场面综合")],
        tags=["scene", "武侠", "论剑", "山顶"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="玄幻",
        slug="scene-xuanhuan-tribulation",
        name="玄幻标志：渡天劫+ 雷劫九重",
        narrative_summary="玄幻/仙侠的最高潮：主角突破到飞升/天人/帝境的渡劫场景。九重雷+ 心魔+ 死亡概率 90%+ 一旦渡过=飞升成仙。是整本书战斗力的最高点。",
        content_json={
            "scene_structure": "1) 渡劫前征兆（天空黑云+ 灵气紊乱+ 周身灵兽奔逃）/ 2) 第一道雷（试探，紫色+ 地脉级）/ 3) 第二道雷（升级，红色+ 火劫）/ 4) 第三-六道（依次水/风/土/光劫）/ 5) 第七道（心魔劫，主角内心被攻击）/ 6) 第八道（混元雷+ 体魄崩裂）/ 7) 第九道（无极雷+ 真身被毁）/ 8) 雷过云开+ 飞升或陨落",
            "sensory_layers": "雷声（破空声+ 共鸣空气）+ 雷光（紫红交替+ 视网膜灼伤）+ 体感（每道雷穿过身体+ 毛孔流血+ 骨骼断裂）+ 心理（每雷一次自我怀疑）",
            "psychological_layer": "1) 第一-三道：信心（'我有金手指')/ 2) 第四-五道：动摇（'怎么这么难') / 3) 第六道：濒死（'我可能死') / 4) 第七心魔：直面所有恐惧（亲人 / 失败 / 自我）/ 5) 第八-九道：超越自我+ 重塑+ 飞升",
            "famous_examples": "《诛仙》张小凡渡劫 / 《凡人修仙》韩立元婴劫 / 《斗破苍穹》萧炎斗帝劫 / 《圣墟》楚风渡劫",
            "anti_cliche": "不要纯写'每雷都靠金手指挡过'；要让心魔劫真实揭露主角内心阴暗+ 主角靠真心而不是金手指过关",
            "activation_keywords": ["玄幻", "渡劫", "九重雷", "心魔", "飞升"],
        },
        source_type="llm_synth", confidence=0.95,
        source_citations=[llm_note("萧鼎《诛仙》+ 忘语《凡人修仙》+ 天蚕土豆《斗破苍穹》渡劫场面综合")],
        tags=["scene", "玄幻", "渡劫", "雷劫"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="都市",
        slug="scene-urban-wedding-disrupted",
        name="都市标志：婚礼上的反转",
        narrative_summary="都市言情常用桥段：女主在婚礼上突然得知男主秘密/真相+ 当众取消婚礼+ 跑出教堂或酒店。要写出震惊+ 决断+ 旁人反应的层次。",
        content_json={
            "scene_structure": "1) 婚礼准备（紧张+ 化妆+ 父母叮嘱）/ 2) 入场（音乐+ 红毯+ 男主等待）/ 3) 牧师询问（'你愿意娶/嫁吗？'）/ 4) 突然有人闯入（朋友/前任/律师）/ 5) 真相揭露（出轨/隐瞒身份/财产黑幕） / 6) 女主转身看男主（眼神交锋）/ 7) 决断（脱下戒指+ 扔下花束 / 或者继续）/ 8) 跑出去（跟谁跑？或自己跑？）",
            "sensory_layers": "婚纱裙摆（太长，难走）+ 高跟鞋（10cm，跑不快）+ 香水（突然觉得呛）+ 头纱（视线遮挡）+ 闪光灯（亲友团拍照）+ 音乐（突然中断的婚礼进行曲）",
            "psychological_layer": "1) 不愿相信 / 2) 看一眼证据 / 3) 想到这些年的付出 / 4) 决定+ 行动 / 5) 跑出去时的解脱（'我自由了'）",
            "model_examples": "《Runaway Bride》/ 《My Best Friend's Wedding》/ 《杉杉来了》/ 《何以笙箫默》",
            "anti_cliche": "不要纯写'男主追出去挽回'；让女主一个人离开+ 自己面对接下来的人生",
            "activation_keywords": ["都市", "婚礼", "反转", "逃婚", "Runaway Bride"],
        },
        source_type="llm_synth", confidence=0.85,
        source_citations=[wiki("Runaway_Bride_(film)"), llm_note("顾漫《何以笙箫默》、墨宝非宝《一生一世》婚礼场面综合")],
        tags=["scene", "都市", "婚礼", "反转"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="末日",
        slug="scene-doomsday-first-stranger",
        name="末日标志：第一次和陌生幸存者对话",
        narrative_summary="末日初期的关键场景：主角孤身一人 N 天后+ 第一次遇到另一个幸存者+ 双方互不信任+ 缓慢建立沟通+ 决定结伴或分离。是末日社交从零开始的奠基。",
        content_json={
            "scene_structure": "1) 主角发现声音（远处的咳嗽/物品掉落/对话） / 2) 武器握紧+ 决定先观察+ 还是攻击 / 3) 远距离对喊（'你是人吗？'）/ 4) 双方互相确认不是丧尸（看眼睛+ 看皮肤+ 看动作）/ 5) 慢慢靠近（5m + 武器收起一半）/ 6) 第一次交谈（最近一次吃饭是什么时候？） / 7) 互相试探（你来自哪+ 见过谁活下来）/ 8) 决定（结伴+ 分手+ 战斗）",
            "sensory_layers": "远处声音（清晰度+ 来源）+ 武器握紧（手汗+ 心跳）+ 对方眼睛（清醒还是发红）+ 嘴唇（干裂程度）+ 衣服（破损程度+ 血迹+ 灰尘）+ 自己嗓子（很久没说话哑了）",
            "psychological_layer": "1) 警惕（最严重的怀疑） / 2) 渴望（人类+ 不是 alone）/ 3) 怕被骗（陷阱）/ 4) 决定相信的瞬间（看到对方腿伤+ 反向怀疑）/ 5) 突然眼眶湿（'已经一个月没说话了'）",
            "model_examples": "《Walking Dead》Rick + Morgan 第一次见面 / 《I Am Legend》Will Smith + 流浪狗 / 《The Last of Us》Joel + Ellie",
            "anti_cliche": "不要纯写'两人立刻信任结伴'；第一次见面应该有 70% 不信任+ 30% 渴望，再慢慢撑过 N 章建立",
            "activation_keywords": ["末日", "幸存者", "第一次见面", "Walking Dead", "信任"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[wiki("The_Walking_Dead_(TV_series)"), wiki("The_Last_of_Us"), llm_note("末日初遇模板综合")],
        tags=["scene", "末日", "幸存者", "信任"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="玄幻",
        slug="scene-xuanhuan-bloodline-awakening",
        name="玄幻标志：血脉觉醒+ 祖辈印记",
        narrative_summary="玄幻题材的爆点：主角因危机激发祖辈血脉+ 体内印记浮现+ 力量爆发+ 翻盘。是从弱到强转折点。要写出血脉的痛+ 祖辈的呼唤+ 力量的失控。",
        content_json={
            "scene_structure": "1) 主角被压制到濒死（被反派/家族/魔物逼到悬崖） / 2) 体内血液躁动（突然感到喉咙咸+ 血涌）/ 3) 印记浮现（额头/手心/胸口）/ 4) 祖辈意识介入（梦中或直接听见）/ 5) 力量爆发（远超等级）/ 6) 反派震惊（'这是 X 家族血脉？'）/ 7) 反杀+ 力量持续 1-3 分钟 / 8) 力量散去+ 主角昏迷+ 印记隐入皮肤",
            "sensory_layers": "血涌喉咙（咸+腥）+ 印记发热（皮肤被烙）+ 视觉变化（一切颜色变深）+ 听觉变化（外界声音变远+ 内心有古老低吟）+ 力量感（手指能轻易折断钢）",
            "psychological_layer": "1) 绝望（要死了） / 2) 突然不痛了（血脉接管） / 3) 见到祖辈意识（敬畏+ 自豪） / 4) 力量爆发的快感（爽点） / 5) 力量散去的虚脱（咳血+ 内伤）/ 6) 醒来后的反思（我到底是谁？）",
            "famous_examples": "《斗破苍穹》异火血脉 / 《吞噬星空》龙血战体觉醒 / 《圣墟》楚风血脉",
            "anti_cliche": "不要让血脉觉醒=纯爽+ 完美翻盘；要有真实代价（之后 1 个月修养+ 印记被人盯上）",
            "activation_keywords": ["玄幻", "血脉觉醒", "印记", "祖辈", "斗破苍穹"],
        },
        source_type="llm_synth", confidence=0.9,
        source_citations=[llm_note("天蚕土豆《斗破苍穹》+ 我吃西红柿《吞噬星空》血脉模板综合")],
        tags=["scene", "玄幻", "血脉", "觉醒"],
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
