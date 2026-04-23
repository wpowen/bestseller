#!/usr/bin/env python3
"""Auto-fix lore issues in 天机录 source JSON chapters."""
import json, os, re, copy, shutil

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CHAPTERS_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'if', 'chapters')
BACKUP_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'if', 'chapters_pre_lore_fix')
AUDIT_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'amazon', 'quality_audit')

def vol_from_ch(ch_num):
    """Return volume number from chapter number."""
    return (ch_num - 1) // 120 + 1

# === FIX RULES ===
# Each rule: (pattern_func, replace_func)
# pattern_func(text, ch_num) -> list of (match_str, start, end)
# replace_func(match_str, ch_num) -> replacement_str

def fix_in_string(s, ch_num, fixes_applied):
    """Apply all fix rules to a single string, return (new_string, fix_count)."""
    if not isinstance(s, str) or not s.strip():
        return s, 0
    
    original = s
    count = 0
    
    # R001: 师父陈玄 -> 父亲陈玄
    for old in ['师父陈玄', '师尊陈玄']:
        if old in s:
            s = s.replace(old, '父亲陈玄')
            count += s.count('父亲陈玄') - original.count('父亲陈玄')
    
    # R003: 陈天命被错称为兄长 (biggest category: 85 hits)
    # Pattern: 兄长/哥哥/大哥/二哥 + 陈天命/天命
    s = re.sub(r'(?:兄长|哥哥|大哥)\s*(陈天命|天命)', r'父亲的宿敌\1', s)
    s = re.sub(r'(陈天命|天命)\s*(?:兄长|哥哥|弟弟)', r'\1，父亲的宿敌', s)
    # "他那位失踪多年的兄长——陈天命" type
    s = re.sub(r'他那位失踪多年的(?:兄长|哥哥)——(陈天命)', r'父亲当年的宿敌——\1', s)
    
    # R005: 二哥陈安/陈年 -> 三弟陈安
    s = re.sub(r'二哥(陈安|陈年)', r'三弟\1', s)
    s = re.sub(r'二弟(陈安|陈年)', r'三弟\1', s)
    
    # R202: 陈年 -> 陈安 (person name only)
    # Be careful: "陈年" can mean "aged/old" in other contexts
    # Only replace when it's clearly a person name
    s = re.sub(r'陈年(?=[，。、！？的了着过也又与和及从在给向被把让将比])', '陈安', s)
    s = re.sub(r'陈年(?=说|道|笑|看|走|站|坐|握|紧|抬|低|转)', '陈安', s)
    s = re.sub(r'(?:二哥|三弟|弟弟|兄长|妹妹|兄)陈年', lambda m: m.group().replace('陈年', '陈安'), s)
    
    # R201: 废材 -> 废柴 (but NOT in volume titles, keep 废柴 everywhere)
    # In text: 废材觉醒 -> 废柴觉醒 etc
    s = s.replace('废材', '废柴')
    
    # R101: 陈念分离时间统一为18年
    for old_time, new_time in [('十年', '十八年'), ('10年', '18年'), 
                                ('十五年', '十八年'), ('15年', '18年'),
                                ('十七年', '十八年'), ('17年', '18年')]:
        # Use lambda replacement to avoid backreference issues with Chinese numerals
        def _make_replacer(nt):
            def _repl(m):
                return m.group(1) + nt
            return _repl
        patterns = [
            rf'(陈念.*?(?:分离|失散|分别|被.*?带走|未见|未见面|未见着).*?){old_time}',
            rf'((?:分离|失散|分别|被.*?带走|未见|未见着).*?陈念.*?){old_time}',
            rf'((?:分离|失散|分别|被.*?带走|未见|未见着).*?妹妹.*?){old_time}',
            rf'(妹妹.*?(?:分离|失散|分别|被.*?带走|未见).*?){old_time}',
        ]
        for pat in patterns:
            try:
                new_s = re.sub(pat, _make_replacer(new_time), s)
                if new_s != s:
                    s = new_s
                    count += 1
            except re.error:
                pass
    
    # R102: 陈机入宗时间统一为10年 (in vol1-2 context)
    if vol_from_ch(ch_num) <= 2:
        for old_time in ['三年', '五年', '3年', '5年']:
            patterns = [
                (rf'((?:入宗|来到天青宗|进入天青宗|进了天青宗).*?){old_time}', '十年'),
                (rf'{old_time}(.*?(?:入宗|进宗|来到宗门|来到天青宗))', '十年'),
            ]
            for pat, new_val in patterns:
                try:
                    def _repl102(m, nv=new_val):
                        if nv == '十年':
                            return nv + m.group(1) if m.lastindex and m.group(1) else m.group(1) + nv
                        return m.group(0)
                    new_s = re.sub(pat, _repl102, s)
                    if new_s != s:
                        s = new_s
                        count += 1
                except re.error:
                    pass
    
    # R002: 父亲死亡时间 (vol3+ should be 18年)
    if vol_from_ch(ch_num) >= 3:
        for old_time in ['三年', '3年', '十年', '10年']:
            pat = rf'父亲{old_time}(前|之前)(死|去世|临终|咽气|陨落)'
            def _repl002(m):
                return '父亲十八年' + m.group(1) + m.group(2)
            try:
                new_s = re.sub(pat, _repl002, s)
                if new_s != s:
                    s = new_s
                    count += 1
            except re.error:
                pass
    
    # R104: choice/interactive residuals - remove from rendered text
    s = re.sub(r'请选择[：: ].*?[。\n]', '', s)
    s = re.sub(r'选项[ABC][：: ].*?[。\n]', '', s)
    s = re.sub(r'你选择了.*?[。\n]', '', s)
    s = re.sub(r'【选择[ABC]】', '', s)
    
    # R105: stat residuals
    s = re.sub(r'[，,]?天命值[+-]\d+', '', s)
    s = re.sub(r'[，,]?谋略[+-]\d+', '', s)
    s = re.sub(r'[，,]?名望[+-]\d+', '', s)
    
    if s != original:
        return s, max(count, 1)
    return s, 0

def fix_node_recursive(node, ch_num, stats):
    """Recursively fix text in a node and its children."""
    total_fixes = 0
    for node_type, content in node.items():
        if node_type == 'nodes' and isinstance(content, list):
            for sub in content:
                total_fixes += fix_node_recursive(sub, ch_num, stats)
            continue
        
        if isinstance(content, dict):
            for key in ('content', 'prompt', 'text', 'description'):
                val = content.get(key)
                if isinstance(val, str):
                    new_val, n = fix_in_string(val, ch_num, stats)
                    if n > 0:
                        content[key] = new_val
                        total_fixes += n
            if isinstance(content.get('nodes'), list):
                for sub in content['nodes']:
                    total_fixes += fix_node_recursive(sub, ch_num, stats)
        
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    for key in ('content', 'text', 'description'):
                        val = item.get(key)
                        if isinstance(val, str):
                            new_val, n = fix_in_string(val, ch_num, stats)
                            if n > 0:
                                item[key] = new_val
                                total_fixes += n
                    for rn in item.get('result_nodes', []):
                        if isinstance(rn, dict):
                            for rn_type, rn_content in rn.items():
                                if isinstance(rn_content, dict):
                                    c = rn_content.get('content')
                                    if isinstance(c, str):
                                        new_c, n = fix_in_string(c, ch_num, stats)
                                        if n > 0:
                                            rn_content['content'] = new_c
                                            total_fixes += n

    return total_fixes

def main():
    # Create backup
    if not os.path.exists(BACKUP_DIR):
        print(f"Creating backup at {BACKUP_DIR}...")
        shutil.copytree(CHAPTERS_DIR, BACKUP_DIR)
        print("Backup created.")
    else:
        print(f"Backup already exists at {BACKUP_DIR}, skipping.")
    
    files = sorted([f for f in os.listdir(CHAPTERS_DIR) if f.endswith('.json')])
    print(f"Processing {len(files)} chapters...")
    
    total_fixes = 0
    fixed_chapters = []
    rule_stats = {}
    
    for fname in files:
        ch_num = int(fname.replace('ch', '').replace('.json', ''))
        fpath = os.path.join(CHAPTERS_DIR, fname)
        
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        stats = {}
        fixes = 0
        
        # Fix next_chapter_hook
        hook = data.get('next_chapter_hook')
        if hook and isinstance(hook, str):
            new_hook, n = fix_in_string(hook, ch_num, stats)
            if n > 0:
                data['next_chapter_hook'] = new_hook
                fixes += n
        
        # Fix title
        title = data.get('title')
        if title and isinstance(title, str):
            new_title, n = fix_in_string(title, ch_num, stats)
            if n > 0:
                data['title'] = new_title
                fixes += n
        
        # Fix nodes
        for node in data.get('nodes', []):
            fixes += fix_node_recursive(node, ch_num, stats)
        
        if fixes > 0:
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            fixed_chapters.append((ch_num, fixes))
            total_fixes += fixes
            if len(fixed_chapters) <= 50 or fixes > 3:
                print(f"  ch{ch_num:04d}: {fixes} fixes")
    
    print(f"\n=== Auto-fix Complete ===")
    print(f"Total chapters fixed: {len(fixed_chapters)}")
    print(f"Total fixes applied: {total_fixes}")
    
    # Save fix log
    log_path = os.path.join(AUDIT_DIR, 'lore_auto_fix_log.json')
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump({'total_fixes': total_fixes, 'fixed_chapters': fixed_chapters}, f, ensure_ascii=False, indent=2)
    print(f"Fix log: {log_path}")

if __name__ == '__main__':
    main()
