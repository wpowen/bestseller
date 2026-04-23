#!/usr/bin/env python3
"""Targeted fixes for remaining lore issues after auto-fix pass."""
import json, os, re, shutil

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CHAPTERS_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'if', 'chapters')
AUDIT_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'amazon', 'quality_audit')

def fix_tianming_brother(text):
    """Fix genuine 陈天命=兄长 patterns (NOT 陈念 calling 陈机 哥哥)."""
    if not isinstance(text, str):
        return text, False
    orig = text
    text = re.sub(r'(?:他|其|那)(?:位|个)?(?:失踪|离开|离去|分别)?(?:多年|很久)?(?:的)?(?:兄长|哥哥|大哥)(?:\s*[——]+\s*|\s*[,，]\s*|\s*就是\s*)(陈天命)', r'父亲的宿敌——\1', text)
    text = re.sub(r'(陈天命)(?:是|乃|即是|便是)(?:他|陈机)(?:的)?(?:兄长|哥哥|大哥)', r'\1是陈机父亲的宿敌', text)
    text = re.sub(r'他那位失踪多年的兄长——陈天命', '父亲当年的宿敌——陈天命', text)
    text = re.sub(r'兄长的下落——陈天命', '宿敌的下落——陈天命', text)
    return text, text != orig

def fix_choice_residual(text):
    """Fix '你选择' residual (shorter pattern than '你选择了')."""
    if not isinstance(text, str):
        return text, False
    orig = text
    text = re.sub(r'你选择[了]?[^。，！？\n]{0,20}[。\n]', '', text)
    text = re.sub(r'【选择[ABC]】', '', text)
    return text, text != orig

def fix_chennian(text):
    """Fix remaining 陈年->陈安 in person-name contexts."""
    if not isinstance(text, str):
        return text, False
    orig = text
    # In contexts where 陈年 is clearly a person name
    text = re.sub(r'(?:三弟|弟弟|兄长|妹妹|小)陈年', lambda m: m.group().replace('陈年', '陈安'), text)
    text = re.sub(r'陈年(?=(?:的|说|道|笑|看|走|站|坐|握|紧|抬|低|转|被|将|与|和|也|却|已|还|在))', '陈安', text)
    return text, text != orig

def fix_node_recursive(node, fix_fns):
    """Apply fix functions recursively to all text in a node."""
    changed = False
    for k, v in node.items():
        if k == 'nodes' and isinstance(v, list):
            for sub in v:
                if fix_node_recursive(sub, fix_fns):
                    changed = True
            continue
        if isinstance(v, dict):
            for key in ('content', 'prompt', 'text', 'description'):
                val = v.get(key)
                if isinstance(val, str):
                    for fn in fix_fns:
                        new_val, did_change = fn(val)
                        if did_change:
                            v[key] = new_val
                            val = new_val
                            changed = True
            if isinstance(v.get('nodes'), list):
                for sub in v['nodes']:
                    if fix_node_recursive(sub, fix_fns):
                        changed = True
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    for key in ('content', 'text', 'description'):
                        val = item.get(key)
                        if isinstance(val, str):
                            for fn in fix_fns:
                                new_val, did_change = fn(val)
                                if did_change:
                                    item[key] = new_val
                                    val = new_val
                                    changed = True
                    for rn in item.get('result_nodes', []):
                        if isinstance(rn, dict):
                            for rk, rv in rn.items():
                                if isinstance(rv, dict) and isinstance(rv.get('content'), str):
                                    for fn in fix_fns:
                                        new_c, did_change = fn(rv['content'])
                                        if did_change:
                                            rv['content'] = new_c
                                            changed = True
    return changed

def main():
    fix_fns = [fix_tianming_brother, fix_choice_residual, fix_chennian]
    
    files = sorted([f for f in os.listdir(CHAPTERS_DIR) if f.endswith('.json')])
    print(f"Applying targeted fixes to {len(files)} chapters...")
    
    total_fixed = 0
    for fname in files:
        ch_num = int(fname.replace('ch', '').replace('.json', ''))
        fpath = os.path.join(CHAPTERS_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        changed = False
        
        # Fix hook
        hook = data.get('next_chapter_hook')
        if isinstance(hook, str):
            for fn in fix_fns:
                new_hook, did = fn(hook)
                if did:
                    data['next_chapter_hook'] = new_hook
                    hook = new_hook
                    changed = True
        
        # Fix nodes
        for node in data.get('nodes', []):
            if fix_node_recursive(node, fix_fns):
                changed = True
        
        if changed:
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            total_fixed += 1
            print(f"  ch{ch_num:04d}: fixed")
    
    print(f"\nTargeted fix complete: {total_fixed} chapters modified")

if __name__ == '__main__':
    main()
