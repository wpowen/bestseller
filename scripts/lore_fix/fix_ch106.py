# -*- coding: utf-8 -*-
import json

CHAPTERS_DIR = '/Users/owen/Documents/workspace/bestseller/output/天机录/if/chapters'

fpath = f'{CHAPTERS_DIR}/ch0106.json'

chapter = {
    "id": "天机录_ch0106",
    "book_id": "天机录",
    "number": 106,
    "title": "暗潮涌动",
    "is_paid": True,
    "next_chapter_hook": "就在众人惊疑未定之际，议事堂外突然传来一声苍老而威严的笑声——那是天青宗老掌门凌渊的声音。",
    "nodes": [
        {
            "text": {
                "id": "天机录_ch0106_01",
                "content": "子夜时分，万籁俱寂。\n\n天青宗的夜色如墨，只有主峰洞府中隐约闪烁的灵光提醒着世人——这座宗门的真正主人，从未入睡。\n\n陈机躺在杂役院破旧的木床上，浑身酸痛如被碾过。天道之眼的威压虽已消散，但那种被更高维度注视的恐惧仍残留在骨髓深处，如同一根看不见的刺，扎在他灵魂最柔软的地方。\n\n他闭上眼，试图入睡，但脑海中的画面不断翻涌——十七种死法、紫金色的锁链、天道之眼震怒的瞳孔……还有那个被刻意遮掩的模糊身影。\n\n「睡不着？」\n\n墨先生的声音在识海中响起，带着几分罕见的温和。",
                "emphasis": "dramatic"
            }
        },
        {
            "dialogue": {
                "id": "天机录_ch0106_02",
                "character_id": "char_moxiansheng",
                "content": "「天道之眼留下的印记，不是一两天能消的。」墨先生叹了口气，「你现在能做的只有一件事——变强。强到连天道之眼都不敢正眼看你的程度。」\n\n陈机苦笑：「说得轻巧。」\n\n「轻巧？」墨先生的声音骤然低沉，「小子，你可知道，上一位被天道之眼列为必杀目标的天机录持有者，活了多久？」\n\n陈机沉默。\n\n「三天。」墨先生的声音如同夜风，「从被标记到死亡，整整三天。而你……」\n\n「我已经超过三天了。」陈机睁开眼，目光在黑暗中格外明亮，「所以，我已经比前人强了。」\n\n墨先生没有说话，但识海中那缕残魂的波动，分明透着几分欣慰。",
                "emotion": "疲惫"
            }
        },
        {
            "text": {
                "id": "天机录_ch0106_03",
                "content": "翌日清晨。\n\n天青宗的钟声比平日早了半个时辰。陈机从床上翻身而起，多年的杂役生涯让他即便在极度疲惫中也能准时醒来——这种本能，不止一次救过他的命。\n\n杂役院中已是人来人往，但气氛与往日截然不同。弟子们三三两两聚在一起，压低声音交谈，目光中带着惊疑不定。\n\n「昨晚那道金光……你们看到了吗？」\n「何止看到，我差点以为天塌了！」\n「听说是天道异象，长老们连夜议事……」\n\n陈机低着头穿过人群，脚步不紧不慢，脸上挂着惯常的憨厚微笑。没有人注意到，他的袖中右手微微颤抖——那是天机录残页在轻颤，仿佛在感应着什么即将发生的事。",
                "emphasis": "system"
            }
        },
        {
            "text": {
                "id": "天机录_ch0106_04",
                "content": "议事堂的方向传来密集的脚步声。\n\n陈机抬头望去，只见各峰长老步履匆匆，面色凝重，朝着议事堂汇聚。这在天青宗是极罕见的景象——上一次所有长老同时被召集，还是三年前魔道大军压境之时。\n\n「所有内门弟子以上，即刻前往议事堂！」传令弟子的声音响彻山门，「掌门有令，不得缺席！」\n\n陈机的心微微一沉。\n\n昨晚天道之眼的降临，果然不可能这么轻易揭过。凌渊那个老狐狸，一定嗅到了什么——而一个闭关多年的老掌门突然出关，往往意味着一场更大的风暴即将来临。\n\n他跟在人群后方，不疾不徐地走向议事堂。沿途弟子们窃窃私语，有人注意到他的存在，投来或好奇或审视的目光——昨晚废墟中完好无损的废物，早已成了众人口中最大的疑点。\n\n陈机假装什么都没看见。\n\n但他的手指，已经悄然按在了眉心的天机录残页上。\n\n暴风雨前，总是最安静的。",
                "emphasis": "dramatic"
            }
        },
        {
            "choice": {
                "id": "天机录_ch0106_05",
                "prompt": "议事堂内暗潮涌动，各方势力蠢蠢欲动。陈机需要在进入议事堂前做好抉择——",
                "choice_type": "styleChoice",
                "choices": [
                    {
                        "id": "天机录_ch0106_05_A",
                        "text": "以退为进",
                        "description": "继续维持废物人设，在议事堂中尽量低调，避免引起凌渊注意",
                        "satisfaction_type": "扮猪吃虎",
                        "visible_cost": "可能错过关键情报",
                        "visible_reward": "安全脱身，不暴露实力",
                        "risk_hint": "凌渊未必会被表象蒙蔽",
                        "process_label": "决定继续伪装",
                        "stat_effects": [
                            {"stat": "谋略", "delta": 2}
                        ],
                        "relationship_effects": [],
                        "result_nodes": [
                            {
                                "text": {
                                    "id": "天机录_ch0106_result_05_A_001",
                                    "content": "陈机放缓脚步，有意识地让自己的步伐变得拖沓，肩膀微微佝偻——一个杂役弟子该有的姿态。他混入人群的最后方，找了个最不起眼的角落站定，目光低垂，一副惊惶不安的模样。\n\n但在无人注意的暗处，他的神识已如蛛丝般悄然扩散，捕捉着议事堂内每一丝气息的变化。"
                                }
                            }
                        ],
                        "is_premium": False,
                        "flags_set": [],
                        "requires_flag": None,
                        "forbids_flag": None,
                        "stat_gate": None,
                        "memory_label": None,
                        "branch_route_id": None
                    },
                    {
                        "id": "天机录_ch0106_05_B",
                        "text": "以进为退",
                        "description": "主动出现在显眼位置，观察凌渊反应，获取第一手情报",
                        "satisfaction_type": "智取",
                        "visible_cost": "暴露在凌渊视野中",
                        "visible_reward": "近距离观察老掌门，可能发现关键线索",
                        "risk_hint": "凌渊城府极深，主动暴露未必是好事",
                        "process_label": "决定主动暴露",
                        "stat_effects": [
                            {"stat": "名望", "delta": 3},
                            {"stat": "谋略", "delta": 1}
                        ],
                        "relationship_effects": [],
                        "result_nodes": [
                            {
                                "text": {
                                    "id": "天机录_ch0106_result_05_B_001",
                                    "content": "陈机整了整衣衫，目光坦然，径直走向议事堂中央的通道——不是内门弟子的位置，却也不是最偏僻的角落。他要让凌渊看见自己，但不要让那老狐狸觉得自己是在刻意表现。\n\n真正高明的伪装，从来不是躲起来，而是站在聚光灯下，演一出谁都看不穿的好戏。"
                                }
                            }
                        ],
                        "is_premium": False,
                        "flags_set": [],
                        "requires_flag": None,
                        "forbids_flag": None,
                        "stat_gate": None,
                        "memory_label": None,
                        "branch_route_id": None
                    }
                ]
            }
        }
    ]
}

with open(fpath, 'w', encoding='utf-8') as f:
    json.dump(chapter, f, ensure_ascii=False, indent=2)

print(f"Written ch0106: {len(json.dumps(chapter, ensure_ascii=False))} bytes")
