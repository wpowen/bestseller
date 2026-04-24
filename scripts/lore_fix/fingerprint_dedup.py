#!/usr/bin/env python3
"""
AI Fingerprint Phrase Scanner & Dedup for 天机录
Detects overused AI cliché phrases and replaces with alternatives.
Rule: Any single phrase appears >1 time per chapter → flag for dedup.
"""
import json, os, re, random
from collections import defaultdict

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CHAPTERS_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'if', 'chapters')
AUDIT_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'amazon', 'quality_audit')

# AI fingerprint phrases and their replacement pools
FINGERPRINT_PHRASES = {
    # (phrase, replacement_pool) — pool is shuffled per use
    "瞳孔骤缩": ["瞳孔一缩", "目光骤凝", "眼神一凛", "双眸骤紧", "眼中精光一闪"],
    "瞳孔微缩": ["眸光微凝", "眼底微变", "目光微沉", "眼中闪过一丝异色"],
    "嘴角微微上扬": ["嘴角轻挑", "唇角微弯", "嘴角勾起一丝弧度", "唇边浮现一抹浅笑"],
    "嘴角勾起一抹": ["唇角绽开一丝", "嘴角泛起一缕", "唇边浮起一抹"],
    "冷笑一声": ["嗤笑一声", "冷哼一声", "发出一声低笑", "冷冷一笑"],
    "眼底深处": ["眸光深处", "双眸深处", "目光幽深处", "眼眸暗处"],
    "眼神变得深邃": ["眸光幽深起来", "目光沉了下去", "眼中多了几分深沉"],
    "眼底闪过一丝": ["眸中掠过一抹", "目光划过一丝", "眼中浮起一缕"],
    "嘴角抽搐了一下": ["嘴角微微一扯", "面皮抖了抖", "嘴唇动了动"],
    "心中一凛": ["心头骤紧", "心底一沉", "心中陡然一惊"],
    "面色骤变": ["脸色陡变", "神色倏忽一变", "面容骤然扭曲"],
    "神色复杂": ["表情晦暗难明", "面色阴晴不定", "神色变幻莫测"],
    "声音沙哑": ["嗓音嘶哑", "声音低沉嘶哑", "开口时声音已经变了调"],
    "眼眶微红": ["眼角泛红", "双目微润", "眸中水光微闪"],
    "淡淡开口": ["缓缓说道", "语气平淡地说", "随口道"],
    "缓缓开口": ["沉声道", "低声说", "慢慢说道"],
    "一字一顿": ["逐字说道", "声音沉而缓", "语速极慢地开口"],
    "瞳孔骤然收缩": ["瞳孔猛地一缩", "目光骤然凝固", "双眸陡然收紧"],
    "声音冰冷": ["语气寒如冰", "嗓音冷冽", "声调降到了冰点"],
    "目光如炬": ["目光灼灼", "眼中精光四射", "眼神锐利如刀"],
}

def _extract_from_node(node, texts):
    for node_type, content in node.items():
        if node_type == 'nodes' and isinstance(content, list):
            for sub in content:
                _extract_from_node(sub, texts)
            continue
        if isinstance(content, dict):
            for key in ('content', 'prompt', 'text', 'description'):
                if isinstance(content.get(key), str):
                    texts.append(content[key])
            if isinstance(content.get('nodes'), list):
                for sub in content['nodes']:
                    _extract_from_node(sub, texts)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    for key in ('content', 'text', 'description'):
                        v = item.get(key)
                        if v and isinstance(v, str):
                            texts.append(v)
                    for rn in item.get('result_nodes', []):
                        if isinstance(rn, dict):
                            for rn_type, rn_content in rn.items():
                                if isinstance(rn_content, dict):
                                    c = rn_content.get('content')
                                    if c and isinstance(c, str):
                                        texts.append(c)

def extract_text_from_chapter(data):
    texts = []
    if data.get('next_chapter_hook'):
        texts.append(data['next_chapter_hook'])
    for node in data.get('nodes', []):
        _extract_from_node(node, texts)
    return '\n'.join(texts)

def scan_all_chapters():
    """Scan and report fingerprint phrase usage across all chapters."""
    os.makedirs(AUDIT_DIR, exist_ok=True)
    files = sorted([f for f in os.listdir(CHAPTERS_DIR) if f.endswith('.json')])
    
    # Global count
    global_counts = defaultdict(int)
    # Per-chapter: {ch_num: {phrase: count}}
    chapter_counts = defaultdict(lambda: defaultdict(int))
    # Per-chapter: {ch_num: [phrase1, phrase2...]} for phrases appearing >1
    chapter_duplicates = defaultdict(list)
    
    for fname in files:
        ch_num = int(fname.replace('ch', '').replace('.json', ''))
        fpath = os.path.join(CHAPTERS_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        text = extract_text_from_chapter(data)
        
        for phrase in FINGERPRINT_PHRASES:
            count = text.count(phrase)
            if count > 0:
                global_counts[phrase] += count
                chapter_counts[ch_num][phrase] = count
                if count > 1:
                    chapter_duplicates[ch_num].append((phrase, count))
    
    # Report
    print("=== AI Fingerprint Phrase Scan ===\n")
    print(f"Scanned {len(files)} chapters\n")
    
    print("## Global Usage (top 20)")
    for phrase, count in sorted(global_counts.items(), key=lambda x: -x[1])[:20]:
        pool = FINGERPRINT_PHRASES[phrase]
        print(f"  {phrase}: {count}x (pool: {len(pool)} alternatives)")
    
    print(f"\n## Chapters with Duplicate Usage (>1 per chapter)")
    dup_count = 0
    for ch_num in sorted(chapter_duplicates.keys()):
        dups = chapter_duplicates[ch_num]
        dup_count += len(dups)
        for phrase, count in dups:
            print(f"  ch{ch_num:04d}: '{phrase}' ×{count}")
    
    # Save report
    report = {
        'global_counts': dict(global_counts),
        'chapter_duplicates': {str(k): v for k, v in chapter_duplicates.items()},
        'total_duplicate_instances': dup_count,
        'total_unique_phrases_detected': len(global_counts),
    }
    report_path = os.path.join(AUDIT_DIR, 'fingerprint_scan_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nReport saved to: {report_path}")
    
    return global_counts, chapter_duplicates

def dedup_chapters(dry_run=True):
    """Replace duplicate phrase occurrences within each chapter with alternatives."""
    files = sorted([f for f in os.listdir(CHAPTERS_DIR) if f.endswith('.json')])
    total_replacements = 0
    chapters_modified = 0
    
    for fname in files:
        ch_num = int(fname.replace('ch', '').replace('.json', ''))
        fpath = os.path.join(CHAPTERS_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        ch_fixes = 0
        
        def fix_phrase_duplicates(text):
            nonlocal ch_fixes
            if not isinstance(text, str):
                return text
            for phrase, pool in FINGERPRINT_PHRASES.items():
                count = text.count(phrase)
                if count > 1:
                    # Keep first occurrence, replace subsequent ones
                    random.shuffle(pool)
                    idx = 0
                    first = True
                    result = []
                    i = 0
                    while i < len(text):
                        if text[i:i+len(phrase)] == phrase:
                            if first:
                                result.append(phrase)
                                first = False
                            else:
                                replacement = pool[idx % len(pool)]
                                result.append(replacement)
                                idx += 1
                                ch_fixes += 1
                            i += len(phrase)
                        else:
                            result.append(text[i])
                            i += 1
                    text = ''.join(result)
            return text
        
        def fix_recursive(obj):
            if isinstance(obj, dict):
                for k in list(obj.keys()):
                    if isinstance(obj[k], str):
                        obj[k] = fix_phrase_duplicates(obj[k])
                    elif isinstance(obj[k], (dict, list)):
                        fix_recursive(obj[k])
            elif isinstance(obj, list):
                for item in obj:
                    fix_recursive(item)
        
        fix_recursive(data)
        
        if ch_fixes > 0:
            chapters_modified += 1
            total_replacements += ch_fixes
            if not dry_run:
                with open(fpath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            if chapters_modified <= 20 or ch_fixes > 3:
                mode = "WOULD FIX" if dry_run else "FIXED"
                print(f"  {mode} ch{ch_num:04d}: {ch_fixes} replacements")
    
    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"\n=== Dedup {mode} ===")
    print(f"Chapters with duplicates: {chapters_modified}")
    print(f"Total replacements: {total_replacements}")
    return total_replacements

if __name__ == '__main__':
    import sys
    
    # Default: scan only
    # With --fix: apply fixes
    # With --dry-run: show what would be fixed
    
    if len(sys.argv) > 1 and sys.argv[1] == '--fix':
        global_counts, chapter_duplicates = scan_all_chapters()
        print("\n" + "="*50)
        print("Applying fixes...")
        dedup_chapters(dry_run=False)
    elif len(sys.argv) > 1 and sys.argv[1] == '--dry-run':
        global_counts, chapter_duplicates = scan_all_chapters()
        print("\n" + "="*50)
        dedup_chapters(dry_run=True)
    else:
        scan_all_chapters()
