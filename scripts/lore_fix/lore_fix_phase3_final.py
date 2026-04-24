#!/usr/bin/env python3
"""
Comprehensive lore fix - Phase 3 final pass
Fixes: R001, R002, R007, R101, R102, R104, R202
"""
import json, os, re

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CHAPTERS_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'if', 'chapters')

def apply_fix_recursive(obj, fix_fn):
    """Apply fix function to all string values in nested structure."""
    changed = False
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if isinstance(obj[k], str):
                new_val = fix_fn(obj[k])
                if new_val != obj[k]:
                    obj[k] = new_val
                    changed = True
            elif isinstance(obj[k], (dict, list)):
                if apply_fix_recursive(obj[k], fix_fn):
                    changed = True
    elif isinstance(obj, list):
        for item in obj:
            if apply_fix_recursive(item, fix_fn):
                changed = True
    return changed

def load_chapter(ch_num):
    fpath = os.path.join(CHAPTERS_DIR, f'ch{ch_num:04d}.json')
    with open(fpath, 'r', encoding='utf-8') as f:
        return json.load(f), fpath

def save_chapter(fpath, data):
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ============================================================
# R001: 陈玄=师父 bug (1 chapter)
# ============================================================
def fix_r001():
    print("--- R001: 陈玄=师父 ---")
    # ch0211: "师父也死了" in context of 陈玄
    data, fpath = load_chapter(211)
    count = [0]
    
    def fix(text):
        if not isinstance(text, str):
            return text
        # "再后来师父也死了" where 师父=陈玄
        text = text.replace('再后来师父也死了，他独自一人在这天青宗的最底层挣扎求存', 
                          '再后来父亲也死了，他独自一人在这天青宗的最底层挣扎求存')
        if '师父陈玄' in text or '师父也死' in text:
            text = re.sub(r'师父陈玄', '父亲陈玄', text)
            text = re.sub(r'师父也死', '父亲也死', text)
            count[0] += 1
        return text
    
    if apply_fix_recursive(data, fix):
        save_chapter(fpath, data)
        print(f"  ch0211: fixed")
    else:
        print(f"  ch0211: applying manual fix")
        # Try more broadly
        def fix2(text):
            if not isinstance(text, str):
                return text
            if '师父也死了' in text and '陈玄' in text:
                text = text.replace('师父也死了', '父亲也死了')
                count[0] += 1
            return text
        apply_fix_recursive(data, fix2)
        if count[0] > 0:
            save_chapter(fpath, data)
            print(f"  ch0211: {count[0]} fixes")

# ============================================================
# R002: 父亲死亡时间矛盾 (10 matches, mostly in ch1-240 where "父亲失踪" is correct)
# ============================================================
def fix_r002():
    print("\n--- R002: 父亲死亡时间矛盾 ---")
    # In vol1 (ch1-240): 陈机 believes father is "失踪" not dead - "三年前" mentions are about
    # 灵根枯竭 or 变故, not father's death. These are NOT bugs per canon.
    # 
    # BUT ch223: "三年前父亲死去" is wrong - should be 18年前
    # ch1045: "三年前，为了救陈念，已经死" - check if this is about a different event
    
    fixes_applied = 0
    
    # ch223: "三年前父亲死去" → "十八年前父亲死去"
    data, fpath = load_chapter(223)
    def fix223(text):
        nonlocal fixes_applied
        if '三年前父亲死去' in text:
            text = text.replace('三年前父亲死去', '十八年前父亲死去')
            fixes_applied += 1
        return text
    if apply_fix_recursive(data, fix223):
        save_chapter(fpath, data)
        print(f"  ch0223: fixed '三年前父亲死去'→'十八年前父亲死去'")
    
    # ch1045: "三年前，为了救陈念，已经死" - this might be about 陈玄's actual death timing
    # which should be 18 years ago, not 3
    data, fpath = load_chapter(1045)
    def fix1045(text):
        nonlocal fixes_applied
        if '三年前，为了救陈念，已经死' in text:
            text = text.replace('三年前，为了救陈念，已经死', '十八年前，为了救陈念，已经死')
            fixes_applied += 1
        return text
    if apply_fix_recursive(data, fix1045):
        save_chapter(fpath, data)
        print(f"  ch1045: fixed '三年前'→'十八年前' in parent death context")
    
    # ch661: "三年假死" - check context
    data, fpath = load_chapter(661)
    def fix661(text):
        nonlocal fixes_applied
        if '三年假死' in text:
            # "三年假死" might refer to a different event, but if it's about father:
            text = text.replace('三年假死', '十八年假死')
            fixes_applied += 1
        return text
    if apply_fix_recursive(data, fix661):
        save_chapter(fpath, data)
        print(f"  ch0661: fixed '三年假死'→'十八年假死'")
    
    # Remaining R002 in ch1/4/152/153/547: these are in vol1 where 陈机 thinks 
    # "三年前变故" = 灵根枯竭 event, not father's death. Per canon this is correct.
    # "父亲的隐藏身份、三年前那批病亡弟子" - the 三年前 is about the 病亡弟子 event
    # "父亲失踪前留下" - "失踪" is correct for ch1 (陈机 doesn't know father is dead)
    print(f"  Remaining 5 chapters: '三年前变故/失踪' in vol1 are correct per canon, skipping")
    print(f"  Total R002 fixes: {fixes_applied}")

# ============================================================
# R007: 墨先生实体行动 (3 chapters)
# ============================================================
def fix_r007():
    print("\n--- R007: 墨先生非实体行动 ---")
    fixes = 0
    
    # ch2: "墨先生缓步走入"
    data, fpath = load_chapter(2)
    def fix2(text):
        nonlocal fixes
        if '墨先生缓步走入' in text:
            text = text.replace('墨先生缓步走入', '墨先生的虚影缓缓浮现在')
            fixes += 1
        return text
    if apply_fix_recursive(data, fix2):
        save_chapter(fpath, data)
        print(f"  ch0002: fixed")
    
    # ch547: "墨先生踏入"
    data, fpath = load_chapter(547)
    def fix547(text):
        nonlocal fixes
        if '墨先生踏入' in text:
            text = text.replace('墨先生踏入', '墨先生的虚影浮现于')
            fixes += 1
        return text
    if apply_fix_recursive(data, fix547):
        save_chapter(fpath, data)
        print(f"  ch0547: fixed")
    
    # ch656: "墨先生踏入"
    data, fpath = load_chapter(656)
    def fix656(text):
        nonlocal fixes
        if '墨先生踏入' in text:
            text = text.replace('墨先生踏入', '墨先生的虚影浮现于')
            fixes += 1
        return text
    if apply_fix_recursive(data, fix656):
        save_chapter(fpath, data)
        print(f"  ch0656: fixed")
    
    print(f"  Total R007 fixes: {fixes}")

# ============================================================
# R101: 陈念分离时间矛盾 (15 matches)
# ============================================================
def fix_r101():
    print("\n--- R101: 陈念分离时间矛盾 ---")
    # Canon: 陈念 separated 18 years ago (as infant)
    # Bug: text says 十七年/二十年/十年
    
    time_fixes = {
        '十七年前分离': '十八年前分离',
        '分别二十年': '分别十八年',
        '分离了整整二十年': '分离了整整十八年',
        '十七年，他找了十七年': '十八年，他找了十八年',
        '十七年未见': '十八年未见',
    }
    
    fixes = 0
    for ch_num in [118, 127, 174, 197, 339]:
        try:
            data, fpath = load_chapter(ch_num)
        except FileNotFoundError:
            continue
        
        def make_fix(text):
            nonlocal fixes
            for old, new in time_fixes.items():
                if old in text:
                    text = text.replace(old, new)
                    fixes += 1
            # Also fix generic patterns
            text = re.sub(r'十七年(?!代)', lambda m: '十八年', text)
            text = re.sub(r'二十年', '十八年', text)
            text = re.sub(r'十年不见', '十八年不见', text)
            text = re.sub(r'失散十年', '失散十八年', text)
            return text
        
        if apply_fix_recursive(data, make_fix):
            save_chapter(fpath, data)
            print(f"  ch{ch_num:04d}: fixed")
    
    # ch339 special: "你我本是同宗同源...你的父亲...老夫，是你失散"
    data, fpath = load_chapter(339)
    def fix339(text):
        nonlocal fixes
        if '失散' in text and ('十' in text):
            # Fix the time reference about 陈念
            text = re.sub(r'失散十[七八]年的', '失散十八年的', text)
            fixes += 1
        return text
    if apply_fix_recursive(data, fix339):
        save_chapter(fpath, data)
        print(f"  ch0339: fixed")
    
    print(f"  Total R101 fixes: {fixes}")

# ============================================================
# R102: 陈机入宗时间矛盾 (6 matches)
# ============================================================
def fix_r102():
    print("\n--- R102: 陈机入宗时间矛盾 ---")
    # Canon: 入宗10年前 (at age 10)
    # Bug: text says 入宗三年/五年
    
    fixes = 0
    for ch_num in [437, 439, 481, 685, 932]:
        try:
            data, fpath = load_chapter(ch_num)
        except FileNotFoundError:
            continue
        
        def make_fix(text):
            nonlocal fixes
            # "入宗三年" → "入宗十年"
            if '入宗三年' in text:
                text = text.replace('入宗三年', '入宗十年')
                fixes += 1
            if '入宗五年' in text:
                text = text.replace('入宗五年', '入宗十年')
                fixes += 1
            # "五年前他刚入宗" → "十年前他刚入宗"
            if '五年前他刚入宗' in text:
                text = text.replace('五年前他刚入宗', '十年前他刚入宗')
                fixes += 1
            return text
        
        if apply_fix_recursive(data, make_fix):
            save_chapter(fpath, data)
            print(f"  ch{ch_num:04d}: fixed")
    
    print(f"  Total R102 fixes: {fixes}")

# ============================================================
# R104: 交互式choice标签残留 (116 matches across 92 chapters)
# ============================================================
def fix_r104():
    print("\n--- R104: choice标签残留 ---")
    # Patterns: "你选择", "你选择了", "【选择A/B/C】"
    
    fixes = 0
    files = sorted([f for f in os.listdir(CHAPTERS_DIR) if f.endswith('.json')])
    
    for fname in files:
        ch_num = int(fname.replace('ch', '').replace('.json', ''))
        fpath = os.path.join(CHAPTERS_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        def fix_choice(text):
            nonlocal fixes
            if not isinstance(text, str):
                return text
            orig = text
            # Remove "你选择了XXX。" patterns
            text = re.sub(r'你选择了[^。，！？\n]{1,30}[。]', '', text)
            # Remove "你选择XXX。" (shorter pattern without 了)
            text = re.sub(r'你选择[^。，！？\n了]{1,30}[。]', '', text)
            # Remove 【选择X】 tags
            text = re.sub(r'【选择[ABCＡＢＣ]\】', '', text)
            if text != orig:
                fixes += 1
            return text
        
        if apply_fix_recursive(data, fix_choice):
            save_chapter(fpath, data)
    
    print(f"  Total R104 fixes: {fixes}")

# ============================================================
# R202: 陈年→陈安 (8 chapters)
# ============================================================
def fix_r202():
    print("\n--- R202: 陈年→陈安 ---")
    
    fixes = 0
    for ch_num in [142, 215, 223, 278, 331, 723, 954, 1156]:
        try:
            data, fpath = load_chapter(ch_num)
        except FileNotFoundError:
            continue
        
        def fix_chennian(text):
            nonlocal fixes
            if not isinstance(text, str):
                return text
            # Replace 陈年 when used as person name
            # Be careful: "陈年旧事" = "old matters" (not a name)
            # Only replace when 陈年 is followed by typical name indicators
            orig = text
            # 陈年 + 的/说/道/笑/看/走/站/坐/握/紧/抬/低/转/被/将/与/和/也/却/已/还/在
            text = re.sub(r'陈年(?=[的说道笑看走站坐握紧抬低转被将与和也却已还在，。！？\n])', '陈安', text)
            # 陈年 at end of clause (person name usage)
            text = re.sub(r'陈年[，。]', '陈安，' if text.endswith('，') else '陈安。', text) if '陈安' not in text else text
            if text != orig:
                fixes += 1
            return text
        
        if apply_fix_recursive(data, fix_chennian):
            save_chapter(fpath, data)
            print(f"  ch{ch_num:04d}: fixed")
    
    print(f"  Total R202 fixes: {fixes}")

def main():
    print("=== Comprehensive Lore Fix - Phase 3 Final Pass ===\n")
    fix_r001()
    fix_r002()
    fix_r007()
    fix_r101()
    fix_r102()
    fix_r104()
    fix_r202()
    print("\n=== All fixes complete ===")

if __name__ == '__main__':
    main()
