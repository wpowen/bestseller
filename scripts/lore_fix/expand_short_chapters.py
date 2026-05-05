#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Expand short chapters by inserting additional narrative nodes."""
import json, os, sys

CHAPTERS_DIR = '/Users/owen/Documents/workspace/bestseller/output/天机录/if/chapters'

def count_content(data):
    total = 0
    for node in data.get('nodes', []):
        if 'text' in node:
            total += len(node['text'].get('content', ''))
        if 'dialogue' in node:
            total += len(node['dialogue'].get('content', ''))
        if 'choice' in node:
            choice = node['choice']
            total += len(choice.get('prompt', ''))
            for c in choice.get('choices', []):
                total += len(c.get('text', ''))
                total += len(c.get('description', ''))
                for rn in c.get('result_nodes', []):
                    if 'text' in rn:
                        total += len(rn['text'].get('content', ''))
                    if 'dialogue' in rn:
                        total += len(rn['dialogue'].get('content', ''))
    return total

def expand_ch402():
    fpath = os.path.join(CHAPTERS_DIR, 'ch0402.json')
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Insert tactical analysis node before choice
    new_tactical = {
        "text": {
            "id": "天机录_ch0402_005b",
            "content": "陈机在识海中反复推演天机录给出的信息。九道锚点，九宫分布——这绝非偶然。圣宗的仪式设计者显然深谙阵法之道，以九宫之数构建封禁，意味着任何单点突破都会触发其余八道的连锁防御。\n\n但他注意到了一个关键细节：九宫阵法的运转依赖中央锚点作为核心，而中央锚点恰好在陈念身下的祭坛深处。如果先切断中央锚点，其余八道的防御至少会削弱三成。\n\n问题是，中央锚点被三层禁制层层包裹，要突破它，需要同时面对祭坛守护者和禁制反噬。而且一旦他暴露在祭坛上方，那十七道元婴气息会在三息之内赶到。\n\n三息。他的手指在袖中微微收紧——三息的时间，够他做什么？",
            "emphasis": "system"
        }
    }

    new_env = {
        "text": {
            "id": "天机录_ch0402_005c",
            "content": "远处传来沉闷的轰鸣，那是元婴修士破空的声音。时间不等人。\n\n陈机从暗处起身，目光掠过祭坛四周——九根锁链的交汇处各有不同的符文阵列，有些已经亮到极致，有些还在缓缓蓄力。这意味着仪式并非同时启动所有锚点，而是按照某种特定的顺序逐个激活。\n\n如果能在仪式完全激活之前动手，他要面对的禁制会少得多。但'如果'二字，从来都是赌徒的语言。\n\n他呼出一口气，将所有杂念排出脑海。此刻他不是废物杂役，不是天青宗的暗棋，他只是一个要去救妹妹的哥哥。\n\n而天机录第四页的金光，正在他掌心静静燃烧，等待着最后的指令。",
            "emphasis": "dramatic"
        }
    }

    # Insert before the last node (choice)
    data['nodes'].insert(-1, new_tactical)
    data['nodes'].insert(-1, new_env)

    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return count_content(data)

def expand_ch106():
    fpath = os.path.join(CHAPTERS_DIR, 'ch0106.json')
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Insert morning preparation scene after dialogue node
    new_morning = {
        "text": {
            "id": "天机录_ch0106_001b",
            "content": "清晨的杂役院一如既往地忙碌。\n\n陈机蹲在井边洗脸，冰凉的井水让他彻底清醒过来。他看了一眼水中的倒影——那张憨厚平凡的脸，是他用了多年时间精心打磨的伪装。眼神中的锐利被他刻意藏起，嘴角的弧度被他调整到恰到好处的木讷。\n\n但今早，他注意到倒影中有什么不对。眉心处，一缕极淡的金色纹路若隐若现——那是天道之眼留下的印记。普通人看不到，但修为在他之上的修士……\n\n他迅速用灵力将那道纹路压下去，同时在心中盘算着应对之策。凌渊出关、长老议事、弟子紧急集合——这一切的时间点太巧了。天道之眼的降临只有短短一瞬，而老掌门就在今天出关？\n\n除非他早已知道今晚会有异象发生。\n\n陈机用袖子擦了擦脸，面上的憨厚笑容分毫不变。但他的心中，已经拉响了最高级别的警报。",
            "emphasis": "system"
        }
    }

    # Insert before the last node (choice)
    new_politics = {
        "text": {
            "id": "天机录_ch0106_003b",
            "content": "议事堂的大门就在前方。\n\n两扇朱红色的巨门上刻着天青宗的宗徽——一只振翅欲飞的青鸾，此刻在晨光中却显得格外肃穆。门前站着的不再是平日里慵懒的守卫，而是两名金丹期弟子，面色冷峻，手中的令牌闪烁着阵法的光芒——那是禁制级别的防护。\n\n陈机随人流进入议事堂，特意选了一个靠后的位置。堂内已经座无虚席，内门弟子分坐两侧，外门弟子和杂役站在后方。空气中弥漫着一股压抑的气息，像是暴风雨前的沉闷。\n\n他扫了一眼堂内布局。长老席上，五位长老已经落座，但正中央的掌门之位依然空着——凌渊还没有到。\n\n一个闭关多年的老怪物，在天道之眼降临的第二天突然出关，这意味着什么？\n\n陈机微微垂下眼帘，用余光观察着堂中每个人的表情。恐惧的、兴奋的、算计的、茫然的……这些表情在他眼中一一掠过，被天机录残页的本能自动归档——棋手的习惯，哪怕在风暴中心，也不会忘记观察棋盘上的每一枚棋子。",
            "emphasis": "dramatic"
        }
    }

    data['nodes'].insert(-1, new_morning)
    data['nodes'].insert(-1, new_politics)

    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return count_content(data)

def expand_ch708():
    fpath = os.path.join(CHAPTERS_DIR, 'ch0708.json')
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Insert emotional depth node before the choice node
    new_emotion = {
        "text": {
            "id": "天机录_ch0708_012b",
            "content": "天机阁总部。\n\n这四个字在陈机脑海中炸开。天机阁——那个传说中掌握着天下一切情报的神秘组织，三百年来无人知晓其总部的位置。无数修士穷尽一生寻找，却连它是否真实存在都无法确认。\n\n而母亲，竟然留下了指向天机阁总部的线索？\n\n陈机感到一阵眩晕。不是恐惧，而是一种近乎窒息的兴奋。如果天机阁总部真的存在，那里一定藏着关于天机录的最深层秘密——或许，还有关于父亲陈天命之死的真相。\n\n但他几乎是瞬间便冷静下来。兴奋是危险的。越诱人的线索，越可能是陷阱。天机阁若真的存在三百年而不被发觉，其底蕴远超任何宗门。贸然闯入，无异于自投罗网。\n\n他需要一个计划。一个万无一失的计划。",
            "emphasis": "dramatic"
        }
    }

    new_memory = {
        "text": {
            "id": "天机录_ch0708_012c",
            "content": "陈机看着妹妹消瘦的面容，记忆突然闪回到十二年前。\n\n那时念儿才五岁，总是跟在他身后，扯着他的衣角，奶声奶气地叫着'哥哥'。他做什么她都要跟着，他练字她就坐在旁边描红，他练剑她就在一旁挥舞小木棍。有一次他嫌她碍事，故意把她甩掉，结果她在后山迷了路，他找了整整一个时辰，最后在一棵老槐树下找到了哭成泪人的她。\n\n她看到他，哭得更凶了：'哥哥不要我了……哥哥不要念儿了……'\n\n他蹲下来抱住她，郑重地许下了一个承诺：'哥哥永远都不会丢下你。'\n\n此刻，他看着眼前这个用寿元为他铺路的妹妹，那个十二年前的承诺如烙铁般灼烧着他的胸口。\n\n他不会丢下她。这一次，换他来守护。",
            "emphasis": "system"
        }
    }

    # Find choice node and insert before it
    choice_idx = None
    for idx, node in enumerate(data['nodes']):
        if 'choice' in node:
            choice_idx = idx
            break

    if choice_idx is not None:
        data['nodes'].insert(choice_idx, new_emotion)
        data['nodes'].insert(choice_idx, new_memory)

    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return count_content(data)

if __name__ == '__main__':
    ch402_chars = expand_ch402()
    ch106_chars = expand_ch106()
    ch708_chars = expand_ch708()

    print(f"ch402 '献祭真相': {ch402_chars} content chars")
    print(f"ch106 '暗潮涌动': {ch106_chars} content chars")
    print(f"ch708 '妹妹的代价': {ch708_chars} content chars")
