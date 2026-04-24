# -*- coding: utf-8 -*-
"""
棋子/棋局 overuse fixer.
Target: reduce from ~5483 to <1000 across 1021 chapters.
Strategy: cap at max 3 per chapter per word, replace excess with
context-appropriate synonyms based on grammatical role.
"""

import json, os, re, copy
from collections import Counter

CHAPTERS_DIR = '/Users/owen/Documents/workspace/bestseller/output/天机录/if/chapters'
MAX_PER_CHAPTER = 3  # max occurrences of 棋子 and 棋局 each per chapter

# Replacement pools by role
QIZI_REPLACEMENTS = {
    '被操控者': ['傀儡', '玩物', '工具', '附庸', '弃子'],
    '轻蔑': ['玩物', '工具', '弃子', '附庸'],
    '反抗_not': [],  # special handling: keep these, they're powerful moments
    '量词': ['一枚落子', '一颗钉子', '一枚暗子', '一粒微尘', '一步暗棋'],
    '成为': ['棋盘上的落子', '他人手中的利刃', '命运手中的刻刀', '天道布下的暗桩'],
    '其他': ['落子', '暗子', '弃子', '棋步', '暗棋'],
}

QIJU_REPLACEMENTS = {
    '所属/内部': ['迷局', '乱局', '困局', '危局', '暗流', '漩涡'],
    '开始/进行': ['局势', '风云', '角力', '博弈', '对决', '较量'],
    '修饰': ['迷局', '暗局', '残局', '变局', '险局'],
    '这盘那盘': ['这场博弈', '这盘对弈', '那场角力', '这场较量'],
    '其他': ['局势', '迷局', '乱局', '博弈', '暗流', '困局'],
}

def classify_qizi_context(text, pos):
    """Classify the role of 棋子 at position pos in text."""
    start = max(0, pos - 8)
    end = min(len(text), pos + 8)
    ctx = text[start:end]
    
    if re.search(r'(不是|绝非|从来不是|不再是|不甘做|不愿做).{0,3}棋子', ctx):
        return '反抗_not'
    if re.search(r'(当作|视为|成为|沦为|不过是|只是|乃是|便是).{0,3}棋子', ctx):
        return '被操控者'
    if re.search(r'棋子.{0,2}(罢了|而已)', ctx):
        return '轻蔑'
    if re.search(r'(一枚|一颗|一个|这颗|那颗|这枚|那枚|第.{1,2}枚).{0,2}棋子', ctx):
        return '量词'
    if re.search(r'(做|当|为|成|变).{0,2}棋子', ctx):
        return '成为'
    return '其他'

def classify_qiju_context(text, pos):
    """Classify the role of 棋局 at position pos in text."""
    start = max(0, pos - 8)
    end = min(len(text), pos + 8)
    ctx = text[start:end]
    
    if re.search(r'(棋局已|棋局才|棋局正|棋局开|棋局将|棋局渐)', ctx):
        return '开始/进行'
    if re.search(r'(这盘|那盘|这场|那场).{0,2}棋局', ctx):
        return '这盘那盘'
    if re.search(r'(更大|真正|最终|神秘|残酷|复杂|深远|无形).{0,2}棋局', ctx):
        return '修饰'
    if re.search(r'(的棋局|棋局的|棋局中|棋局里|棋局之|棋局内)', ctx):
        return '所属/内部'
    return '其他'

def get_replacement(role, used_counter, replacements_dict):
    """Get next replacement for a role, cycling through pool."""
    pool = replacements_dict.get(role, replacements_dict.get('其他', []))
    if not pool:
        return None
    idx = used_counter[role] % len(pool)
    used_counter[role] += 1
    return pool[idx]

def replace_excess_in_string(text, word, max_keep, classify_fn, replacements_dict, used_counter):
    """Replace excess occurrences of word in text, keeping first max_keep."""
    positions = [m.start() for m in re.finditer(word, text)]
    if len(positions) <= max_keep:
        return text, 0
    
    replaced = 0
    # Work backwards to preserve positions
    for pos in reversed(positions[max_keep:]):
        role = classify_fn(text, pos)
        if role == '反抗_not':
            # Keep these - they're powerful narrative moments
            max_keep_local = max_keep
            # But if we're way over, still replace some
            continue
        
        rep = get_replacement(role, used_counter, replacements_dict)
        if rep:
            text = text[:pos] + rep + text[pos + len(word):]
            replaced += 1
    
    return text, replaced

def count_word(text, word):
    return text.count(word)

def fix_chapter(ch_num):
    fpath = os.path.join(CHAPTERS_DIR, f'ch{ch_num:04d}.json')
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Count current
    full_text = json.dumps(data, ensure_ascii=False)
    qizi_count = full_text.count('棋子')
    qiju_count = full_text.count('棋局')
    
    if qizi_count <= MAX_PER_CHAPTER and qiju_count <= MAX_PER_CHAPTER:
        return 0, 0
    
    used_qizi = Counter()
    used_qiju = Counter()
    total_qizi_replaced = 0
    total_qiju_replaced = 0
    
    def walk_and_fix(obj):
        nonlocal total_qizi_replaced, total_qiju_replaced
        
        if isinstance(obj, str):
            # Count current in this string
            cur_qizi = obj.count('棋子')
            cur_qiju = obj.count('棋局')
            
            if cur_qizi > 0:
                # How many 棋子 have we already kept in this chapter?
                # We need chapter-level tracking, so use a different approach:
                # Fix the entire string but only replace beyond our budget
                obj, n = replace_excess_in_string(obj, '棋子', 0, classify_qizi_context, QIZI_REPLACEMENTS, used_qizi)
                total_qizi_replaced += n
            
            if cur_qiju > 0:
                obj, n = replace_excess_in_string(obj, '棋局', 0, classify_qiju_context, QIJU_REPLACEMENTS, used_qiju)
                total_qiju_replaced += n
            
            return obj
        
        elif isinstance(obj, dict):
            return {k: walk_and_fix(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [walk_and_fix(item) for item in obj]
        return obj
    
    # We need a smarter approach: walk content strings in order,
    # keep first MAX_PER_CHAPTER occurrences of each word,
    # replace the rest
    
    # Collect all content strings with their paths
    content_strings = []
    
    def collect_contents(obj, path=''):
        if isinstance(obj, str) and len(obj) > 20:
            # Only substantial content strings
            if '棋子' in obj or '棋局' in obj:
                content_strings.append((path, obj))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                collect_contents(v, f'{path}.{k}')
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                collect_contents(v, f'{path}[{i}]')
    
    collect_contents(data)
    
    qizi_budget = MAX_PER_CHAPTER
    qiju_budget = MAX_PER_CHAPTER
    
    # Process each content string in order
    new_contents = {}
    for path, text in content_strings:
        # For this string, count occurrences
        qizi_positions = [m.start() for m in re.finditer('棋子', text)]
        qiju_positions = [m.start() for m in re.finditer('棋局', text)]
        
        # Keep first N, replace rest (N = remaining budget)
        qizi_keep = min(qizi_budget, len(qizi_positions))
        qiju_keep = min(qiju_budget, len(qiju_positions))
        qizi_budget -= qizi_keep
        qiju_budget -= qiju_keep
        
        # Replace excess 棋子
        if len(qizi_positions) > qizi_keep:
            excess_positions = qizi_positions[qizi_keep:]  # these get replaced
            # Work backwards
            for pos in reversed(excess_positions):
                role = classify_qizi_context(text, pos)
                if role == '反抗_not':
                    # Keep "不是棋子" - these are powerful, don't replace
                    qizi_budget += 1  # refund since we're keeping one
                    continue
                rep = get_replacement(role, used_qizi, QIZI_REPLACEMENTS)
                if rep:
                    text = text[:pos] + rep + text[pos + len('棋子'):]
                    total_qizi_replaced += 1
        
        # Replace excess 棋局
        if len(qiju_positions) > qiju_keep:
            excess_positions = qiju_positions[qiju_keep:]
            for pos in reversed(excess_positions):
                role = classify_qiju_context(text, pos)
                rep = get_replacement(role, used_qiju, QIJU_REPLACEMENTS)
                if rep:
                    text = text[:pos] + rep + text[pos + len('棋局'):]
                    total_qiju_replaced += 1
        
        new_contents[path] = text
    
    # Now apply the replacements back to the data structure
    content_idx = [0]
    
    def apply_fixes(obj, path=''):
        if isinstance(obj, str) and len(obj) > 20 and path in new_contents:
            return new_contents[path]
        elif isinstance(obj, dict):
            return {k: apply_fixes(v, f'{path}.{k}') for k, v in obj.items()}
        elif isinstance(obj, list):
            return [apply_fixes(item, f'{path}[{i}]') for i, item in enumerate(obj)]
        return obj
    
    data = apply_fixes(data)
    
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return total_qizi_replaced, total_qiju_replaced

# Run
total_qizi_fixed = 0
total_qiju_fixed = 0
chapters_fixed = 0

for i in range(1, 1201):
    qz, qj = fix_chapter(i)
    if qz > 0 or qj > 0:
        chapters_fixed += 1
        total_qizi_fixed += qz
        total_qiju_fixed += qj

print(f"Fixed {chapters_fixed} chapters")
print(f"棋子 replaced: {total_qizi_fixed}")
print(f"棋局 replaced: {total_qiju_fixed}")
print(f"Total replacements: {total_qizi_fixed + total_qiju_fixed}")

# Verify
remaining_qizi = 0
remaining_qiju = 0
for i in range(1, 1201):
    with open(os.path.join(CHAPTERS_DIR, f'ch{i:04d}.json'), 'r', encoding='utf-8') as f:
        text = f.read()
    remaining_qizi += text.count('棋子')
    remaining_qiju += text.count('棋局')

print(f"\nRemaining: 棋子={remaining_qizi}, 棋局={remaining_qiju}, total={remaining_qizi+remaining_qiju}")
