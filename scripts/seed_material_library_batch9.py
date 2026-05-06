"""
Batch 9: scene_templates large expansion — 15+ genres
Focus: standard scene types each major genre needs (opening hook / midpoint reversal /
final confrontation / emotional climax / quiet aftermath)
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
    # ─── 历史 ───
    MaterialEntry(
        dimension="scene_templates", genre="历史",
        slug="hist-st-court-debate",
        name="朝堂对决场景",
        narrative_summary="在皇帝面前两派大臣的政策辩论：表面是政见之争，实质是政治势力的角力。"
                          "关键不是谁有道理，而是谁能让皇帝倾向自己——皇帝的微表情比任何论据都更重要。",
        content_json={
            "structure": "议题提出→正方论述→反方反击→关键证据/奏疏→皇帝裁决",
            "subtle_signals": "皇帝的目光转向 / 太监的呼吸节奏 / 朝臣队列的微妙挪动",
            "stakes": "胜方获得政策主导权；败方可能丢官、流放、抄家",
            "writing_focus": "通过细节展现权力的微观运作，而非宏大叙事",
            "activation_keywords": ["朝议", "廷争", "御前对决", "政见之争"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("中国朝政", ""), llm_note("朝堂场景设计")],
        tags=["历史", "朝堂", "政治", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="历史",
        slug="hist-st-banquet-poison",
        name="宴席暗局场景",
        narrative_summary="表面是君臣同欢的庆功/接风宴，实则杀机四伏：投毒、布伏、试探。"
                          "宴会的每一道菜、每一句敬酒辞都是双关，懂的人才能在欢笑中看见血色。",
        content_json={
            "layered_dialogue": "敬酒辞同时是恐吓 / 称颂同时是警告",
            "physical_cues": "酒杯停顿的瞬间 / 菜肴上桌的顺序 / 座次的安排",
            "tipping_point": "某句话踩中禁忌或某人显露异常 → 局面急转",
            "common_endgames": ["突然下毒/血溅当场", "宾主尽欢但暗中已布定后续", "客人察觉提前撤离"],
            "activation_keywords": ["鸿门宴", "宫宴", "杀机", "宴无好宴"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("鸿门宴", "中国历史"), llm_note("历史宴席场景")],
        tags=["历史", "宴席", "阴谋", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="历史",
        slug="hist-st-battlefield-decisive",
        name="决战疆场场景",
        narrative_summary="军事决战的核心场景：不只是规模和血腥，而是统帅的临场决断如何在迷雾中找到敌人破绽，"
                          "以及士兵在生死线上的人性瞬间。古代战争的核心是士气而非武力。",
        content_json={
            "command_layer": "统帅的全局视野 / 信使奔走 / 阵型变化",
            "soldier_layer": "前线士兵的恐惧/勇气/同伴情谊 / 受伤者的内心独白",
            "decisive_moment": "某个偶然事件（风向/坠马/敌将露怯）触发胜负转折",
            "real_analogs": ["官渡之战乌巢", "赤壁火攻", "淝水风声鹤唳"],
            "activation_keywords": ["决战", "战场", "胜负", "阵型", "古代战争"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("中国古代战争", ""), llm_note("古战场叙事")],
        tags=["历史", "战争", "决战", "场景"],
    ),

    # ─── 言情 ───
    MaterialEntry(
        dimension="scene_templates", genre="言情",
        slug="rom-st-chance-encounter",
        name="命运邂逅场景",
        narrative_summary="男女主第一次相遇的场景：在错的时间、错的状态下，却有一个细节让对方记住，"
                          "为后续千百次重逢留下伏笔。一场好的初遇要『预告所有未来的纠葛』。",
        content_json={
            "context_options": ["雨中等出租", "电梯故障", "撞翻咖啡", "误入对方场合", "工作交接"],
            "must_have_element": "不喜欢对方第一面 / 但记住了某个细节",
            "subtext_layer": "对方的某个特质踩中自己内心未被满足的需求",
            "writing_traps": "不要把第一面写成『一见钟情』——读者要看到张力的种子",
            "activation_keywords": ["第一次见面", "邂逅", "初遇", "命运的相遇"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("言情邂逅场景设计")],
        tags=["言情", "邂逅", "初遇", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="言情",
        slug="rom-st-vulnerable-night",
        name="脆弱之夜场景",
        narrative_summary="男女主某一方在情绪极度脆弱时被对方意外看到/陪伴的场景。"
                          "这是关系深化的关键时刻——盔甲被卸下，真实的人格第一次完整呈现，比任何告白都强大。",
        content_json={
            "trigger": "酒醉 / 失业/丧亲后 / 噩梦惊醒 / 失败后",
            "chemistry": "脆弱方不愿被看见但需要被看见的矛盾 / 看见方的克制与心动",
            "physical_proximity": "靠近不一定是身体接触——一杯水、一条毯子、保持距离的陪伴更动人",
            "morning_after": "醒来后的尴尬重要——不要轻易跳过",
            "activation_keywords": ["脆弱", "深夜", "酒后", "陪伴", "看见真实"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[llm_note("言情情感深化场景")],
        tags=["言情", "脆弱", "夜晚", "深化"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="言情",
        slug="rom-st-confession-rejection",
        name="表白受挫场景",
        narrative_summary="表白不成功的场景：对方没有立刻接受，而是给出复杂的反应（沉默/逃避/诚实拒绝）。"
                          "这种不顺利的表白比『成功告白』更动人，因为它揭示了双方真实的情感地形。",
        content_json={
            "rejection_types": [
                "对方还没准备好（自己的问题）",
                "对方有现实顾虑（外部障碍）",
                "对方根本没理解（错位）",
                "对方表面拒绝实则慌乱",
            ],
            "writing_principle": "拒绝必须有真实理由，不是为虐而虐",
            "scene_pacing": "告白—对方反应—沉默—对方解释—主角的反应—离开",
            "long_term_impact": "这次受挫如何在后续重逢/和解中被回收",
            "activation_keywords": ["告白被拒", "心动受挫", "表白失败", "未完成的告白"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("言情拒绝场景设计")],
        tags=["言情", "告白", "拒绝", "场景"],
    ),

    # ─── 都市 ───
    MaterialEntry(
        dimension="scene_templates", genre="都市",
        slug="urban-st-power-display",
        name="实力暴露场景",
        narrative_summary="低调主角在公开场合被迫展露真实身份/实力的关键时刻。"
                          "周围人（特别是之前轻视过主角的）的表情转变是读者最享受的部分——这是都市爽文的灵魂场景。",
        content_json={
            "trigger": "霸道客户当众羞辱 / 老板炫耀人脉 / 学校势利眼老师为难",
            "reveal_layers": ["第一层：主角认识比对方更高的人物",
                             "第二层：被认出是更高位的存在",
                             "第三层：所有人意识到自己冒犯了真正的大佬"],
            "周围反应_choreography": "震惊→重新评估→恐惧→讨好的表情链",
            "trap_to_avoid": "过度延长打脸过程会显得拖沓——精确控制节奏",
            "activation_keywords": ["实力暴露", "身份揭穿", "打脸", "都市爽文", "扮猪吃虎"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[llm_note("都市爽文核心场景")],
        tags=["都市", "实力", "打脸", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="都市",
        slug="urban-st-business-meeting",
        name="商战谈判场景",
        narrative_summary="高端商务谈判桌上的暗战：表面是商业条款讨论，"
                          "实质是双方信息差/底牌/心理素质的较量，最关键的转折往往来自一个看似无关的小细节。",
        content_json={
            "preparation_phase": "情报收集 / 心理战准备 / 备选方案",
            "negotiation_choreography": "开局虚高→中场试探→关键让步→闭局收割",
            "psychological_tactics": "沉默施压 / 愤怒离场 / 突然示好 / 引入第三方",
            "small_detail_wins": "对方手机震动反应 / 喝水节奏变化 / 特定词汇的回避",
            "activation_keywords": ["商战", "谈判", "商业较量", "签约", "并购"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("商业谈判场景设计")],
        tags=["都市", "商战", "谈判", "场景"],
    ),

    # ─── 末日 ───
    MaterialEntry(
        dimension="scene_templates", genre="末日",
        slug="apoc-st-supply-raid",
        name="物资搜刮场景",
        narrative_summary="进入危险区域（超市/仓库/医院/废墟）寻找物资的核心场景。"
                          "节奏是收紧的：进入→搜寻→意外（敌人/丧尸/陷阱）→紧急撤退/战斗——是末日小说的『日常』戏。",
        content_json={
            "phase_structure": "侦查→进入→分头搜寻→意外→撤退",
            "tension_layers": "明面的丧尸/敌对幸存者 + 暗面的陷阱/资源短缺",
            "moral_micro_tests": "遇到濒死陌生人是否救？ / 找到物资如何分配？",
            "specific_locations": ["药店", "学校", "工厂", "医院", "派出所"],
            "writing_anchor": "用具体物品（一盒过期罐头/半瓶水）展示资源稀缺",
            "activation_keywords": ["搜刮物资", "废墟探索", "末日日常", "物资短缺"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("末日生存场景设计")],
        tags=["末日", "物资", "搜刮", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="末日",
        slug="apoc-st-base-attack",
        name="据点防守战场景",
        narrative_summary="据点遭受大规模进攻（丧尸潮/敌对势力围剿）的核心场景。"
                          "考验团队组织能力、个人技能极限、以及关键时刻的牺牲与抉择。",
        content_json={
            "structure": "预警→部署→第一波防御→局部告急→援军/撤退/最后一搏",
            "character_moments": "每个角色在不同战位的视角穿插",
            "critical_decisions": "牺牲外围保核心 / 全员防御失败 / 主动出击破局",
            "post_battle_focus": "伤亡名单/谁负责/创伤后",
            "activation_keywords": ["据点防守", "丧尸潮", "末日战役", "防御战"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("末日大规模场景")],
        tags=["末日", "防守战", "据点", "场景"],
    ),

    # ─── 悬疑 ───
    MaterialEntry(
        dimension="scene_templates", genre="悬疑",
        slug="susp-st-discovery-body",
        name="发现尸体场景",
        narrative_summary="案件起点的标志性场景：尸体的姿态、环境、第一目击者的反应都在叙事上做工作。"
                          "一具好的『尸体出场』要同时给读者：恐惧、信息、谜题、情感冲击。",
        content_json={
            "POV_options": ["晨跑者撞见", "清洁工发现", "亲属上门看到", "警察到达现场"],
            "scene_anchors": "环境异常细节 / 尸体姿态暗示 / 第一目击者的本能反应",
            "info_density": "尸体本身已经传递了案件要素：身份/可能凶器/时间线索",
            "writing_techniques": "感官描写 + 心理震撼 + 客观记录的层次切换",
            "activation_keywords": ["发现尸体", "命案现场", "第一目击者", "案件开端"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("悬疑开场场景设计"), wiki("犯罪现场调查", "")],
        tags=["悬疑", "尸体", "现场", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="悬疑",
        slug="susp-st-interrogation",
        name="嫌疑人审讯场景",
        narrative_summary="审讯室内侦探与嫌疑人的心理博弈：表面是问答，实质是双方都在试探对方的底牌。"
                          "好的审讯戏不是『谁聪明』，而是双方都在用同样的工具互相解读。",
        content_json={
            "physical_setup": "审讯室的灯光/桌椅角度/单向镜",
            "psychological_tactics": "沉默施压 / 抛出假证据 / 突然友好 / 暗示已知更多",
            "嫌疑人_response_types": "顽固否认 / 主动配合（可疑） / 转移话题 / 反向施压",
            "key_moment": "某个细节让侦探确认（或排除）对方——但不会立刻言明",
            "activation_keywords": ["审讯", "嫌疑人", "心理博弈", "口供", "审问"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("审讯", "刑事调查"), llm_note("悬疑审讯场景")],
        tags=["悬疑", "审讯", "心理战", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="悬疑",
        slug="susp-st-revelation",
        name="真相揭示场景",
        narrative_summary="侦探最终拼齐所有线索向所有人揭示真相的高潮场景。"
                          "经典的『阿加莎式』集会—揭示—指认结构，但现代悬疑更倾向于让真相在更小、更私密的场合传达。",
        content_json={
            "classic_format": "聚集所有嫌疑人→侦探重述案件→排除→指认凶手",
            "modern_variant": "侦探与凶手单独对峙 / 通过伪装陷阱触发自首",
            "emotional_layer": "真相不只是逻辑，还有受害者意义/凶手动机的人性维度",
            "twist_potential": "真相揭示后还有反转——指认错了，或真相比指认的更深",
            "activation_keywords": ["真相揭示", "凶手指认", "案情还原", "推理终幕"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("阿加莎·克里斯蒂", "推理小说"), llm_note("悬疑高潮场景")],
        tags=["悬疑", "真相", "揭示", "高潮"],
    ),

    # ─── 灵异 ───
    MaterialEntry(
        dimension="scene_templates", genre="灵异",
        slug="liyi-st-encounter",
        name="鬼魂初遇场景",
        narrative_summary="主角第一次（或最具冲击力的一次）目击鬼魂的标志性场景。"
                          "氛围营造比鬼本身的可怕更重要——读者的恐惧来自『不对劲』的渐进感。",
        content_json={
            "setup_techniques": [
                "环境异常（温度骤降/光线变暗/电器故障）",
                "听觉先于视觉（脚步声/呼吸声/低语）",
                "其他人不正常反应（宠物毛炸/小孩盯空气）",
            ],
            "rising_unease": "渐进式的不对劲 → 直到无法忽视的瞬间",
            "first_glimpse": "侧影 / 镜中倒影 / 错位的细节 → 比正面冲击更可怕",
            "aftermath_handling": "主角的否认 / 寻求第二证人 / 调查",
            "activation_keywords": ["撞鬼", "灵异事件", "鬼影", "异常感", "诡异"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("恐怖小说", "氛围营造"), llm_note("灵异场景设计")],
        tags=["灵异", "鬼魂", "氛围", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="灵异",
        slug="liyi-st-ritual",
        name="超度/驱邪仪式场景",
        narrative_summary="道士/法师执行驱邪或超度仪式的核心场景：仪式的过程不只是动作描写，"
                          "还要展示信仰体系的内在逻辑——『为什么这样做有效』本身就是世界观的展示。",
        content_json={
            "ritual_structure": "请神→陈情→施法→对抗（如有反扑）→送别",
            "visual_elements": "符箓焚烧 / 朱砂书写 / 法器响动 / 香火气味",
            "danger_potential": "仪式中途被打断 / 鬼魂反扑 / 主持者修为不够",
            "emotional_anchor": "如果是超度，往往涉及亡灵的执念被解开——这是核心情感时刻",
            "activation_keywords": ["超度", "驱邪", "法事", "符咒", "通灵仪式"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("道教仪式", ""), llm_note("灵异仪式场景")],
        tags=["灵异", "仪式", "驱邪", "场景"],
    ),

    # ─── 心理惊悚 ───
    MaterialEntry(
        dimension="scene_templates", genre="心理惊悚",
        slug="psy-st-paranoia-spiral",
        name="妄想螺旋场景",
        narrative_summary="主角对某事/某人的怀疑从合理质疑滑入偏执妄想的过程场景。"
                          "读者看见每一步的逻辑都成立，但累积起来已经偏离现实——这是心理惊悚的核心机制。",
        content_json={
            "step_progression": "正常质疑→寻找证据→选择性接收→构建解释→无法被反驳",
            "external_signs": "睡眠减少 / 翻找他人物品 / 频繁查证 / 对反驳的过度反应",
            "POV_handling": "用主角的内心独白同时让读者察觉偏差",
            "tipping_point": "某个客观事件本可证伪 → 但被主角解释为更深的阴谋",
            "activation_keywords": ["偏执", "妄想", "失控", "猜疑链", "心理失衡"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("偏执型人格", "心理学"), llm_note("心理惊悚机制")],
        tags=["心理惊悚", "妄想", "偏执", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="心理惊悚",
        slug="psy-st-mirror-confront",
        name="镜像对峙场景",
        narrative_summary="主角与折射自己内心的角色（往往是反派或导师）面对面交谈的场景。"
                          "对话中主角逐渐发现：对方说的某些话其实是自己的真实想法，自我边界在这场对话中崩塌。",
        content_json={
            "structure": "看似日常对话→关键话题→对方说出主角隐藏的想法→主角防御→真相承认",
            "对方_function": "镜像/阴影/恶魔代言人——说出主角不敢承认的部分",
            "physical_setting": "封闭空间（车内/小房间/地下室） + 单一光源",
            "ending_options": ["主角崩溃认知", "主角反抗但留下伤口", "对方让步但已埋下种子"],
            "activation_keywords": ["镜像对峙", "心魔", "自我审视", "阴影对话"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[wiki("阴影", "荣格心理学"), llm_note("心理对峙场景")],
        tags=["心理惊悚", "镜像", "对峙", "场景"],
    ),

    # ─── 重生 ───
    MaterialEntry(
        dimension="scene_templates", genre="重生",
        slug="rebirth-st-first-day",
        name="重生第一日场景",
        narrative_summary="重生者醒来发现回到关键时间点的核心场景：身体记忆/物品/天气/某个具体细节"
                          "确认时间线，然后是巨大的情感冲击——既是机会也是责任。",
        content_json={
            "confirmation_anchors": [
                "镜中年轻的自己",
                "某个已死的人还活着",
                "手机/报纸/日历的日期",
                "尚未发生的标志性事件",
            ],
            "first_emotion_arc": "怀疑→震惊→狂喜→恐惧（重蹈覆辙）→决心",
            "first_action": "重生者第一个行动选择揭示其性格——是急于改变？还是先观察？",
            "writing_principle": "用感官细节（妈妈的笑声/旧家具的味道/前世挚爱的眼神）让重生的真实感落地",
            "activation_keywords": ["重生第一天", "时间倒流", "回到过去", "记忆冲击", "重活一次"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[llm_note("重生开场场景设计")],
        tags=["重生", "开场", "确认", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="重生",
        slug="rebirth-st-warning-failed",
        name="警告未被采信场景",
        narrative_summary="重生者试图警告身边人某事将发生，但因证据不足或对方不信任而失败。"
                          "这是重生类作品的核心张力场景：知道未来不等于能改变，孤独的预知者最痛苦。",
        content_json={
            "warning_subjects": "家人/朋友/恋人——往往是最在乎也最不被信的人",
            "rejection_reasons": "听起来像精神失常 / 没有证据 / 对方更信任的人反对",
            "tragic_irony": "正因为重生者太迫切，反而显得更可疑",
            "emotional_aftermath": "孤独感深化 → 重生者决定独自承担",
            "narrative_function": "为后续『血淋淋的事实证明她对了』做铺垫",
            "activation_keywords": ["警告无效", "无人相信", "孤独的预知", "重生悲剧"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[llm_note("重生叙事核心张力")],
        tags=["重生", "警告", "孤独", "场景"],
    ),

    # ─── 机甲 ───
    MaterialEntry(
        dimension="scene_templates", genre="机甲",
        slug="mecha-st-first-launch",
        name="机甲首次出击场景",
        narrative_summary="主角第一次驾驶机甲投入实战的标志性场景：从启动序列到神经接入到战场进入，"
                          "每一步都既是仪式也是危险——读者跟随主角第一次理解机甲的力量与代价。",
        content_json={
            "stage_breakdown": "穿戴神经服→进入驾驶舱→系统启动→神经接入→出击",
            "sensory_immersion": "机甲与驾驶员的感官融合 / 重力反馈 / 系统提示音",
            "first_combat": "对手实力的客观评估 + 驾驶员的本能反应 + 机甲性能极限",
            "psychological_threshold": "第一次开火/第一次被击中/第一次面对死亡",
            "activation_keywords": ["机甲启动", "首次出击", "神经接入", "战场登陆"],
        },
        source_type="llm_synth", confidence=0.74,
        source_citations=[wiki("机甲", "科幻"), llm_note("机甲首战场景")],
        tags=["机甲", "首战", "启动", "场景"],
    ),

    # ─── 校园 ───
    MaterialEntry(
        dimension="scene_templates", genre="校园",
        slug="campus-st-classroom-exposure",
        name="教室公开冲突场景",
        narrative_summary="教室内的公开冲突（嘲讽/挑战/告白/打脸）：所有人在场让冲突无法私下解决，"
                          "事件的处理方式将决定主角在班级权力结构中的位置。",
        content_json={
            "trigger_types": "答题正确反被嘲 / 老师不公 / 同学挑衅 / 突发事件",
            "audience_dynamics": "支持者/敌对者/中立者的反应分布",
            "resolution_options": "用实力反击 / 用智慧化解 / 沉默承受（埋下伏笔）",
            "post_event_ripple": "课间讨论 / 微信群发酵 / 老师跟进 / 家长介入",
            "activation_keywords": ["教室冲突", "公开打脸", "课堂事件", "校园对峙"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("校园场景设计")],
        tags=["校园", "教室", "冲突", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="校园",
        slug="campus-st-graduation-farewell",
        name="毕业告别场景",
        narrative_summary="毕业典礼/最后一晚的告别场景：青春的终结与未来的开启同时压上，"
                          "未说出口的话和所有可能性在最后几小时里反复回响。",
        content_json={
            "physical_setting": "操场/教室/熟悉的小餐馆/校门口",
            "emotional_beats": "强装平静→某个细节触动→隐忍流泪→拥抱/挥手→分别",
            "未完成的事": "未说的告白 / 未道歉的过节 / 未感谢的恩情",
            "future_anchor": "约定再见的时间地点（往往不会兑现）",
            "writing_anchor": "通过物品（毕业册/校服/合影）承载记忆",
            "activation_keywords": ["毕业", "告别", "青春结束", "再见"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[llm_note("校园告别场景设计")],
        tags=["校园", "毕业", "告别", "场景"],
    ),

    # ─── 美食 ───
    MaterialEntry(
        dimension="scene_templates", genre="美食",
        slug="food-st-cooking-zen",
        name="料理顿悟场景",
        narrative_summary="厨师在烹饪过程中突然进入心流状态、领悟某个一直困扰自己的技艺真谛的场景。"
                          "周围声音消失，只有食材与手的对话，是美食类作品最具诗意的瞬间。",
        content_json={
            "trigger": "情绪积累到临界 / 某个偶然失误反成转机 / 客人的一句话点醒",
            "sensory_immersion": "手感/温度/气味/声音的极致描绘",
            "philosophical_layer": "做菜=做人/做菜=与食材对话/做菜=理解传承",
            "scene_outcome": "完成的菜与之前的同名菜在质上的根本差异",
            "activation_keywords": ["心流", "顿悟", "料理之道", "厨艺突破", "做菜如禅"],
        },
        source_type="llm_synth", confidence=0.75,
        source_citations=[wiki("心流", "心理学"), llm_note("美食叙事核心场景")],
        tags=["美食", "顿悟", "心流", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre="美食",
        slug="food-st-tasting-judgment",
        name="品鉴评判场景",
        narrative_summary="美食评论家或竞争对手品尝主角作品的关键场景。"
                          "评判者的每一个细微表情都被放大——这道菜的命运（和主角的命运）取决于这一刻。",
        content_json={
            "POV_strategy": "在主角和评判者之间切换，让读者既感受紧张也理解品鉴过程",
            "phases_of_tasting": "观色→闻香→入口→咀嚼→吞咽→回味→评语",
            "verdict_options": "盛赞 / 苛刻批评 / 沉默（最可怕） / 出人意料的评价",
            "stakes_layers": "比赛胜负 / 餐厅命运 / 主角自我认同 / 与师父/对手的关系",
            "activation_keywords": ["品鉴", "评判", "美食评价", "厨艺较量"],
        },
        source_type="llm_synth", confidence=0.73,
        source_citations=[llm_note("美食评判场景设计")],
        tags=["美食", "品鉴", "评判", "场景"],
    ),

    # ─── 快穿 ───
    MaterialEntry(
        dimension="scene_templates", genre="快穿",
        slug="kuaichuan-st-world-entry",
        name="新世界入场场景",
        narrative_summary="主角进入新副本世界的标志性时刻：宿主记忆涌入、身份适应、第一次与攻略对象相遇。"
                          "这个场景设定了本世界的基调和挑战难度——好的入场场景立刻吸引读者。",
        content_json={
            "memory_integration": "宿主记忆涌入的混乱→主角的整理与利用",
            "identity_adaptation": "外貌/身份/社会地位的快速适应",
            "first_meeting": "与攻略对象/反派/关键NPC的首次接触——埋下钩子",
            "world_rules": "通过宿主记忆和环境细节让读者快速理解世界规则",
            "activation_keywords": ["世界穿梭", "宿主记忆", "新副本", "快穿入场"],
        },
        source_type="llm_synth", confidence=0.71,
        source_citations=[llm_note("快穿叙事节奏设计")],
        tags=["快穿", "入场", "世界", "场景"],
    ),

    # ─── 萌宠 ───
    MaterialEntry(
        dimension="scene_templates", genre="萌宠",
        slug="meng-st-bond-formation",
        name="人宠羁绊建立场景",
        narrative_summary="主角与宠物建立深度羁绊的转折时刻：初遇时的不信任 → 某个事件后的相互守护。"
                          "宠物视角的内心独白往往比主角视角更打动读者。",
        content_json={
            "initial_state": "宠物的警惕/受伤/不信任 + 主角的不知所措",
            "bonding_event": "共同度过危险 / 主角无私付出 / 宠物的忠诚行为",
            "POV_alternation": "在人和宠物之间切换视角",
            "physical_anchors": "第一次主动靠近 / 第一次被允许触摸 / 第一次同睡",
            "activation_keywords": ["人宠羁绊", "宠物治愈", "信任建立", "萌宠"],
        },
        source_type="llm_synth", confidence=0.72,
        source_citations=[llm_note("萌宠叙事场景设计")],
        tags=["萌宠", "羁绊", "信任", "场景"],
    ),

    # ─── 通用 ───
    MaterialEntry(
        dimension="scene_templates", genre=None,
        slug="gen-st-betrayal-discovery",
        name="背叛被揭穿场景",
        narrative_summary="主角发现挚友/盟友/恋人背叛自己的决定性时刻。"
                          "好的背叛揭穿场景不只是震惊，还要让读者回想起此前的所有伏笔——『原来如此』和『我居然没看出来』同时涌上。",
        content_json={
            "discovery_methods": "无意中听到 / 收到证据 / 第三方告知 / 自己拼出真相",
            "first_reaction": "拒绝相信→寻找其他解释→不得不接受→选择应对",
            "confrontation_options": "立刻对峙 / 装作不知暗中布局 / 直接断绝关系",
            "emotional_arc": "信任崩塌 → 自我怀疑（我怎么会看错人？） → 重建认知",
            "writing_principle": "提前在小细节里埋伏笔，让揭穿时读者拍案",
            "activation_keywords": ["背叛", "揭穿", "信任崩塌", "真相暴露"],
        },
        source_type="llm_synth", confidence=0.76,
        source_citations=[llm_note("通用背叛场景设计")],
        tags=["通用", "背叛", "揭穿", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre=None,
        slug="gen-st-decision-point",
        name="重大抉择场景",
        narrative_summary="角色面临两难抉择的关键时刻：两个选择都有沉重代价，"
                          "无论选哪个都将永久改变角色的本质。这是任何故事最有力量的瞬间之一。",
        content_json={
            "structure": "情境逼迫→选项列出→挣扎→选择→承担",
            "dilemma_types": [
                "救A还是救B（两人都重要）",
                "坚持原则还是妥协救命",
                "牺牲自己还是牺牲他人",
                "复仇还是放下",
            ],
            "scene_pacing": "缓慢——让读者跟着挣扎；快速决定显得轻率",
            "post_decision": "选择本身定义角色——之后的所有行为都带着这次选择的痕迹",
            "activation_keywords": ["两难", "抉择", "代价", "关键时刻", "决定"],
        },
        source_type="llm_synth", confidence=0.78,
        source_citations=[wiki("道德困境", "伦理学"), llm_note("通用决策场景")],
        tags=["通用", "抉择", "两难", "场景"],
    ),
    MaterialEntry(
        dimension="scene_templates", genre=None,
        slug="gen-st-quiet-aftermath",
        name="风暴后宁静场景",
        narrative_summary="重大冲突/损失之后的安静时刻：尘埃落定，幸存者面对现实。"
                          "这种『余烬场景』比战斗本身更能打动读者——情感终于被允许沉淀。",
        content_json={
            "physical_state": "废墟/医院/空房间/熟悉但已改变的环境",
            "psychological_state": "麻木/缓慢消化/突然崩溃/沉默",
            "key_moment": "某个不经意的细节（一只袜子/一个咖啡杯/窗外的光线）触发情感",
            "dialogue_principle": "少说话，沉默和短句最有力量",
            "narrative_function": "为下一段故事做情感铺垫——主角带着这场风暴继续前进",
            "activation_keywords": ["事后", "余波", "宁静", "尘埃落定", "废墟"],
        },
        source_type="llm_synth", confidence=0.77,
        source_citations=[llm_note("通用余烬场景设计")],
        tags=["通用", "宁静", "余波", "情感"],
    ),
]


async def main(dry_run: bool = False) -> None:
    print(f"{'[DRY RUN] ' if dry_run else ''}Seeding {len(ENTRIES)} entries...\n")
    from collections import Counter
    g, d = Counter(), Counter()
    for e in ENTRIES:
        g[e.genre or "(通用)"] += 1
        d[e.dimension] += 1
    print(f"By genre:     {dict(g)}")
    print(f"By dimension: {dict(d)}\n")
    if dry_run:
        return
    errors = 0
    async with session_scope() as session:
        for entry in ENTRIES:
            try:
                await insert_entry(session, entry, compute_embedding=True)
            except Exception as exc:
                print(f"  ERROR {entry.slug}: {exc}")
                errors += 1
    print(f"\n✓ {len(ENTRIES) - errors} inserted/updated ({errors} errors)")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry))
