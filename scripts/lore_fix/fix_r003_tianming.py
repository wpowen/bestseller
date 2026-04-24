#!/usr/bin/env python3
"""
R003 comprehensive fix: 陈天命=同姓非亲
Strategy: 
- ch626: 叔父→同姓旧识
- ch482/569/702/669/685: 陈天命被明确称为兄长/弟弟 → 改写
- Other chapters where 兄长 refers to 陈机 (陈念/陈灵 calling 陈机 兄长) → NOT a bug, skip
"""
import json, os, re, copy

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CHAPTERS_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'if', 'chapters')

# Per-chapter fix specifications
# Each entry: (chapter_num, list of (old_text_snippet, new_text_snippet))
PER_CHAPTER_FIXES = {
    626: [
        # 叔父→同姓旧识
        (
            '我叫陈天命，是天机阁第七代阁主——也是你父亲的亲弟弟。换句话说，我是你的叔父。',
            '我叫陈天命，是天机阁第七代阁主——与你父亲同姓，曾是旧识。世人以为我与他有血缘，实则不过同姓之缘。'
        ),
    ],
    569: [
        # 陈机 calling 陈天命 弟弟
        (
            '我那好弟弟陈天命，不仅活着，还在皇城混得风生水起',
            '那个姓陈的天命，不仅活着，还在皇城混得风生水起'
        ),
        (
            '陈天命……」他喃喃道，「弟弟，该是你做出选择的时候了',
            '陈天命……」他喃喃道，「那个同姓之人，该是你做出选择的时候了'
        ),
        # Also fix '废物哥哥' context - this is 陈机 calling himself 弟弟's 哥哥
        (
            '还有个废物哥哥',
            '还有个所谓同姓的兄长'
        ),
        (
            '他若真认出我这个\'废物哥哥\'',
            '他若真认出我这个同姓之人'
        ),
        (
            '小心你弟弟',
            '小心那个姓陈的'
        ),
        (
            '看来我那弟弟比我想象的更敏锐',
            '看来陈天命比我想象的更敏锐'
        ),
        (
            '对自己的亲弟弟都能保持如此冷静的判断',
            '对陈天命都能保持如此冷静的判断'
        ),
    ],
    482: [
        # 兄长→that person (ambiguous context where 陈机 thinks of 陈天命 as 兄长)
        (
            '当年陈家灭门之夜，他亲眼看着兄长被神秘人带走',
            '当年陈家灭门之夜，他亲眼看着那个人被神秘人带走'
        ),
        (
            '兄长的下落——陈天命',
            '那个人的下落——陈天命'
        ),
        (
            '得知兄长真相后，陈机面临一个艰难的选择：是优先调查营救兄长，还是继续按原计划布局对抗皇朝？',
            '得知陈天命真相后，陈机面临一个艰难的选择：是优先调查营救那个被囚之人，还是继续按原计划布局对抗皇朝？'
        ),
        (
            '陈家灭门、兄长被囚、自己被选中',
            '陈家灭门、那人被囚、自己被选中'
        ),
    ],
    702: [
        # 陈天命 is兄长 in this chapter
        (
            '那是陈天命，他的兄长',
            '那是陈天命，那个与他同姓的人'
        ),
        (
            '十年未见，兄长竟还记得自己幼时的称呼',
            '十年未见，陈天命竟还记得自己幼时的称呼'
        ),
        (
            '面对兄长十年前以命相护的真相',
            '面对陈天命十年前以命相护的真相'
        ),
        (
            '他们三兄妹就已被那只看不见的眼睛盯上',
            '他们三人就已被那只看不见的眼睛盯上'
        ),
        (
            '陈天命、陈念、还有你……你们三兄妹',
            '陈天命、陈念、还有你……你们三人'
        ),
        # "幼时陈天命背着他" - this implies childhood together
        (
            '他想起幼时陈天命背着他穿越深山的情景，想起那道挡在妖兽面前、浑身浴血仍不退半步的背影',
            '他想起从前陈天命背着他穿越深山的情景，想起那道挡在妖兽面前、浑身浴血仍不退半步的背影'
        ),
        (
            '他已不再是当年那个只能躲在兄长身后的孩童',
            '他已不再是当年那个只能躲在别人身后的孩童'
        ),
        # "把我弟弟和妹妹排除在你的棋局之外" - 陈天命 calling 陈机 弟弟
        (
            '把我弟弟和妹妹排除在你的棋局之外',
            '把陈机和陈念排除在你的棋局之外'
        ),
        # "都是为了他这个废物弟弟" 
        (
            '都是为了他这个废物弟弟能多活几年',
            '都是为了他这个废柴能多活几年'
        ),
        (
            '兄长竟能精准预言到章节的更迭',
            '陈天命竟能精准预言到章节的更迭'
        ),
    ],
    669: [
        (
            '兄长！陈天命',
            '陈天命！'
        ),
    ],
    685: [
        (
            '兄长……有趣，天命',
            '那个人……有趣，天命'
        ),
    ],
    803: [
        # "兄长之爱" - need context check
        (
            '那是藏匿在废柴外表下十几年的兄长之爱',
            '那是藏匿在废柴外表下十几年的守护之念'
        ),
        (
            '陈劫看着兄长那张短暂失态的脸',
            '陈劫看着陈天命那张短暂失态的脸'
        ),
    ],
    269: [
        # "哥哥会死" - context: 陈念 thinks her 哥哥(陈机) will die if... about 天命锁钥
        # This is 陈念 calling 陈机 哥哥 → FP, but "哥哥" + "天命" triggers R003
        # NOT a genuine bug - skip
    ],
    444: [
        # "哥哥，那里是陷阱" - 陈念 calling 陈机 哥哥 → FP, skip
    ],
    554: [
        # "哥哥" - 陈念 calling 陈机 → FP, skip
    ],
    556: [
        # "哥哥的小丫头" - about 陈念 calling 陈机 → FP, skip  
    ],
    598: [
        # "哥哥的小丫头" - 陈念 → FP, skip
    ],
    837: [
        # "哥哥，我无处不在" - 陈念 → FP, skip
    ],
    1039: [
        # "哥哥" + "天命" in陈念 context → likely FP, skip
    ],
    1073: [
        # "哥哥，我感知到了" - 陈念 → FP, skip
    ],
    1079: [
        # "兄长身后" - need to check who this refers to
    ],
    1085: [
        # "急于与亲人相认的哥哥" + "天命之子" - check context
    ],
    1046: [
        # "兄长的意思" "兄长已经离开" - check if 兄长=陈机 or 陈天命
    ],
}

def fix_text_in_chapter(ch_num):
    """Apply per-chapter text fixes."""
    if ch_num not in PER_CHAPTER_FIXES or not PER_CHAPTER_FIXES[ch_num]:
        return 0
    
    fpath = os.path.join(CHAPTERS_DIR, f'ch{ch_num:04d}.json')
    if not os.path.exists(fpath):
        print(f'  ch{ch_num:04d}: file not found')
        return 0
    
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    fixes = PER_CHAPTER_FIXES[ch_num]
    count = 0
    
    def apply_fixes(text):
        nonlocal count
        if not isinstance(text, str):
            return text
        for old, new in fixes:
            if old in text:
                text = text.replace(old, new)
                count += 1
        return text
    
    def fix_recursive(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str):
                    obj[k] = apply_fixes(v)
                elif isinstance(v, dict):
                    fix_recursive(v)
                elif isinstance(v, list):
                    for item in v:
                        fix_recursive(item)
        elif isinstance(obj, list):
            for item in obj:
                fix_recursive(item)
    
    fix_recursive(data)
    
    if count > 0:
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    return count

# Chapters that are FP (陈念叫陈机哥哥 or 天命术语) - no fix needed
FP_CHAPTERS = {124, 145, 148, 152, 212, 223, 224, 225, 269, 293, 342, 353, 355, 357, 
               383, 444, 447, 462, 472, 492, 554, 556, 567, 571, 582, 621, 659, 662,
               695, 745, 750, 762, 763, 791, 833, 907, 935, 944, 1018, 1039, 1059,
               1063, 1073, 1079, 1085, 1107, 1127, 1191, 1197, 1198}

# Remove FP chapters from fix list
for ch in FP_CHAPTERS:
    if ch in PER_CHAPTER_FIXES:
        del PER_CHAPTER_FIXES[ch]

def main():
    print("=== R003 Comprehensive Fix ===")
    total = 0
    for ch_num in sorted(PER_CHAPTER_FIXES.keys()):
        n = fix_text_in_chapter(ch_num)
        if n > 0:
            print(f"  ch{ch_num:04d}: {n} fixes applied")
            total += n
        else:
            print(f"  ch{ch_num:04d}: no matches found (may need manual check)")
    print(f"\nTotal R003 fixes: {total}")

if __name__ == '__main__':
    main()
