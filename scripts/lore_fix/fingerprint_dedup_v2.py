#!/usr/bin/env python3
"""
AI Fingerprint Phrase Dedup v2 — works at chapter level, not string level.
Tracks phrase counts across all nodes in a chapter, then applies replacements.
"""
import json, os, re, random
from collections import defaultdict

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CHAPTERS_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'if', 'chapters')

FINGERPRINT_PHRASES = {
    "瞳孔骤缩": ["瞳孔一缩", "目光骤凝", "眼神一凛", "双眸骤紧", "眼中精光一闪"],
    "瞳孔微缩": ["眸光微凝", "眼底微变", "目光微沉", "眼中闪过一丝异色"],
    "瞳孔骤然收缩": ["瞳孔猛地一缩", "目光骤然凝固", "双眸陡然收紧"],
    "嘴角微微上扬": ["嘴角轻挑", "唇角微弯", "嘴角勾起一丝弧度", "唇边浮现一抹浅笑"],
    "嘴角勾起一抹": ["唇角绽开一丝", "嘴角泛起一缕", "唇边浮起一抹"],
    "冷笑一声": ["嗤笑一声", "冷哼一声", "发出一声低笑", "冷冷一笑"],
    "眼底深处": ["眸光深处", "双眸深处", "目光幽深处", "眼眸暗处"],
    "眼底闪过一丝": ["眸中掠过一抹", "目光划过一丝", "眼中浮起一缕"],
    "声音沙哑": ["嗓音嘶哑", "声音低沉嘶哑", "开口时声音已经变了调"],
    "声音冰冷": ["语气寒如冰", "嗓音冷冽", "声调降到了冰点"],
    "缓缓开口": ["沉声道", "低声说", "慢慢说道"],
    "淡淡开口": ["缓缓说道", "语气平淡地说", "随口道"],
    "心中一凛": ["心头骤紧", "心底一沉", "心中陡然一惊"],
    "面色骤变": ["脸色陡变", "神色倏忽一变", "面容骤然扭曲"],
    "一字一顿": ["逐字说道", "声音沉而缓", "语速极慢地开口"],
    "目光如炬": ["目光灼灼", "眼中精光四射", "眼神锐利如刀"],
    "神色复杂": ["表情晦暗难明", "面色阴晴不定", "神色变幻莫测"],
    "眼眶微红": ["眼角泛红", "双目微润", "眸中水光微闪"],
    "眼神变得深邃": ["眸光幽深起来", "目光沉了下去", "眼中多了几分深沉"],
    "嘴角抽搐了一下": ["嘴角微微一扯", "面皮抖了抖", "嘴唇动了动"],
}

def count_phrase_in_text(text, phrase):
    if not isinstance(text, str):
        return 0
    return text.count(phrase)

def collect_all_strings(obj, string_list):
    """Collect all string values and their paths for modification."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if isinstance(obj[k], str):
                string_list.append((obj, k))
            elif isinstance(obj[k], (dict, list)):
                collect_all_strings(obj[k], string_list)
    elif isinstance(obj, list):
        for item in obj:
            collect_all_strings(item, string_list)

def dedup_chapter(data, ch_num):
    """Dedup fingerprint phrases at chapter level."""
    # Collect all strings in this chapter
    string_refs = []
    collect_all_strings(data, string_refs)
    
    # Count total occurrences of each phrase across the whole chapter
    chapter_phrase_counts = defaultdict(int)
    for obj, key in string_refs:
        text = obj[key]
        for phrase in FINGERPRINT_PHRASES:
            chapter_phrase_counts[phrase] += count_phrase_in_text(text, phrase)
    
    # For phrases with >1 occurrences, we need to replace all but the first
    phrases_to_dedup = {p: count for p, count in chapter_phrase_counts.items() if count > 1}
    if not phrases_to_dedup:
        return 0
    
    total_replacements = 0
    
    for phrase, total_count in phrases_to_dedup.items():
        pool = FINGERPRINT_PHRASES[phrase]
        random.shuffle(pool)
        seen = 0
        pool_idx = 0
        
        for obj, key in string_refs:
            text = obj[key]
            if phrase not in text:
                continue
            
            # Build new text, keeping first occurrence, replacing rest
            new_text = []
            i = 0
            while i < len(text):
                if text[i:i+len(phrase)] == phrase:
                    seen += 1
                    if seen == 1:
                        new_text.append(phrase)
                    else:
                        replacement = pool[pool_idx % len(pool)]
                        new_text.append(replacement)
                        pool_idx += 1
                        total_replacements += 1
                    i += len(phrase)
                else:
                    new_text.append(text[i])
                    i += 1
            obj[key] = ''.join(new_text)
    
    return total_replacements

def main():
    files = sorted([f for f in os.listdir(CHAPTERS_DIR) if f.endswith('.json')])
    print(f"=== Fingerprint Dedup v2 — Processing {len(files)} chapters ===\n")
    
    total_fixes = 0
    chapters_modified = 0
    
    for fname in files:
        ch_num = int(fname.replace('ch', '').replace('.json', ''))
        fpath = os.path.join(CHAPTERS_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        n = dedup_chapter(data, ch_num)
        if n > 0:
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            chapters_modified += 1
            total_fixes += n
            if n >= 3:
                print(f"  ch{ch_num:04d}: {n} replacements")
    
    print(f"\n=== Dedup Complete ===")
    print(f"Chapters modified: {chapters_modified}")
    print(f"Total replacements: {total_fixes}")

if __name__ == '__main__':
    main()
