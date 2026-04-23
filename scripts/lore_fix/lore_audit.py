#!/usr/bin/env python3
"""Lore Audit Scanner for 天机录 - scans source JSON chapters against canon rules."""
import json, os, re, sys
from collections import defaultdict

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CHAPTERS_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'if', 'chapters')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output', '天机录', 'amazon', 'quality_audit')

RULES = [
    {"id": "R001", "severity": "critical", "category": "character_identity",
     "desc": "陈玄被错误描述为师父",
     "patterns": [r"师父陈玄", r"师尊陈玄", r"陈玄.*(?:收养|收徒).*(?:陈机|他)(?!.*父)"],
     "hint": "改为'父亲陈玄'"},
    {"id": "R002", "severity": "critical", "category": "timeline",
     "desc": "父亲死亡时间矛盾(非18年)",
     "patterns": [r"父亲.*(?:三年|3年).*(?:死|去世|临终|咽气|陨落)",
                  r"(?:三年|3年).*(?:前|之前).*(?:父亲.*死|死.*父亲)"],
     "hint": "vol1-vol2保留'失踪'；vol3后改为'十八年前'"},
    {"id": "R003", "severity": "critical", "category": "character_identity",
     "desc": "陈天命被错误描述为陈机兄长",
     "patterns": [r"(?:兄长|哥哥|大哥|二哥).*(?:陈天命|天命)",
                  r"(?:陈天命|天命).*(?:兄长|哥哥|弟弟)"],
     "hint": "改为'父亲的宿敌'或'魔道主帅'"},
    {"id": "R004", "severity": "critical", "category": "multi_ending",
     "desc": "多结局分支标签残留",
     "patterns": [r"【自由之路】", r"【执棋之路】", r"【融合之路】"],
     "hint": "删除非正典结局"},
    {"id": "R005", "severity": "high", "category": "character_identity",
     "desc": "陈安被错误称为二哥",
     "patterns": [r"(?:二哥|二弟).*(?:陈安|陈年|安儿|年儿)"],
     "hint": "改为'三弟'或'弟弟'"},
    {"id": "R006", "severity": "high", "category": "character_identity",
     "desc": "凌渊被错误描述为陈玄好友",
     "patterns": [r"凌渊.*(?:最好的朋友|此生唯一认可的好友)"],
     "hint": "改为'旧识'或'宿敌'"},
    {"id": "R007", "severity": "high", "category": "character_identity",
     "desc": "墨先生被错误描述为实体行动",
     "patterns": [r"墨先生(?:推门|走入|踏入|缓步走入|推开了?门)"],
     "hint": "改为'虚影显现'或'声音在识海中响起'"},
    {"id": "R101", "severity": "high", "category": "timeline",
     "desc": "陈念分离时间矛盾(非18年)",
     "patterns": [r"(?:分离|失散|分别|被带走|被.*带走).*(?:十年|10年|十五年|15年|十七年|17年)",
                  r"(?:十年|10年|十五年|15年|十七年|17年).*(?:分离|失散|分别|未见|未见面)"],
     "hint": "统一改为'十八年'"},
    {"id": "R102", "severity": "high", "category": "timeline",
     "desc": "陈机入宗时间矛盾(非10年)",
     "patterns": [r"(?:入宗|来到天青宗|进入天青宗).*(?:三年|五年|3年|5年)",
                  r"(?:三年|五年|3年|5年).*(?:入宗|进宗|来到宗门)"],
     "hint": "统一改为'十年前'"},
    {"id": "R103", "severity": "medium", "category": "geography",
     "desc": "韩烈可能错误出现在天青宗内部",
     "patterns": [r"天青宗[第一]?天才韩烈"],
     "hint": "韩烈是玄武宗弟子，检查上下文"},
    {"id": "R104", "severity": "high", "category": "if_residual",
     "desc": "交互式choice标签残留",
     "patterns": [r"请选择", r"选项[ABC]", r"你选择了?", r"【选择"],
     "hint": "删除交互残留"},
    {"id": "R105", "severity": "medium", "category": "if_residual",
     "desc": "属性变化提示残留",
     "patterns": [r"天命值[+-]\d+", r"谋略[+-]\d+", r"名望[+-]\d+"],
     "hint": "删除属性提示"},
    {"id": "R201", "severity": "medium", "category": "language",
     "desc": "废材/废柴混用",
     "patterns": [r"废材"],
     "hint": "统一为'废柴'"},
    {"id": "R202", "severity": "medium", "category": "language",
     "desc": "陈年应统一为陈安",
     "patterns": [r"陈年"],
     "hint": "改为'陈安'"},
]

def _extract_from_node(node, texts):
    """Recursively extract text from a node (handles nested nodes)."""
    for node_type, content in node.items():
        if node_type == 'nodes' and isinstance(content, list):
            for sub in content:
                _extract_from_node(sub, texts)
            continue
        if isinstance(content, dict):
            if content.get('content') and isinstance(content['content'], str):
                texts.append(content['content'])
            if content.get('prompt') and isinstance(content['prompt'], str):
                texts.append(content['prompt'])
            if content.get('text') and isinstance(content['text'], str):
                texts.append(content['text'])
            if content.get('description') and isinstance(content['description'], str):
                texts.append(content['description'])
            if content.get('nodes') and isinstance(content['nodes'], list):
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
    """Extract all readable text from a chapter JSON."""
    texts = []
    if data.get('next_chapter_hook'):
        texts.append(data['next_chapter_hook'])
    if data.get('title'):
        texts.append(data['title'])
    for node in data.get('nodes', []):
        _extract_from_node(node, texts)
    return '\n'.join(texts)

def scan_chapters():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    files = sorted([f for f in os.listdir(CHAPTERS_DIR) if f.endswith('.json')])
    print(f"Scanning {len(files)} chapters...")
    
    report = []
    severity_counts = defaultdict(int)
    rule_counts = defaultdict(int)
    chapter_issues = defaultdict(list)
    
    for fname in files:
        ch_num = int(fname.replace('ch', '').replace('.json', ''))
        fpath = os.path.join(CHAPTERS_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        text = extract_text_from_chapter(data)
        
        for rule in RULES:
            for pattern in rule['patterns']:
                matches = re.findall(pattern, text)
                if matches:
                    sev = rule['severity']
                    # R002 special: vol1-vol2 "father missing" is correct POV
                    if rule['id'] == 'R002' and ch_num <= 240:
                        sev = 'low'
                    issue = {
                        'chapter': ch_num,
                        'rule_id': rule['id'],
                        'severity': sev,
                        'category': rule['category'],
                        'description': rule['desc'],
                        'matches': matches[:3],  # cap at 3
                        'match_count': len(matches),
                        'fix_hint': rule['hint']
                    }
                    report.append(issue)
                    severity_counts[sev] += 1
                    rule_counts[rule['id']] += len(matches)
                    chapter_issues[ch_num].append(issue)
    
    # Write full report
    report_path = os.path.join(OUTPUT_DIR, 'lore_audit_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    # Write summary
    summary_lines = [
        "# Lore Audit Summary",
        "",
        f"Chapters scanned: {len(files)}",
        f"Total issues found: {len(report)}",
        "",
        "## By Severity",
        f"| Severity | Count |",
        f"|----------|-------|",
    ]
    for sev in ['critical', 'high', 'medium', 'low']:
        summary_lines.append(f"| {sev} | {severity_counts.get(sev, 0)} |")
    
    summary_lines.extend(["", "## By Rule", "| Rule | Description | Count |", "|------|-------------|-------|"])
    for rule in RULES:
        summary_lines.append(f"| {rule['id']} | {rule['desc']} | {rule_counts.get(rule['id'], 0)} |")
    
    # Top 20 most problematic chapters
    ch_sorted = sorted(chapter_issues.items(), key=lambda x: -len(x[1]))[:20]
    summary_lines.extend(["", "## Top 20 Most Problematic Chapters", "| Chapter | Issues |", "|---------|--------|"])
    for ch_num, issues in ch_sorted:
        summary_lines.append(f"| {ch_num} | {len(issues)} |")
    
    summary_path = os.path.join(OUTPUT_DIR, 'lore_audit_summary.md')
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(summary_lines))
    
    # Critical fix queue
    critical_queue = [r for r in report if r['severity'] in ('critical', 'high')]
    queue_path = os.path.join(OUTPUT_DIR, 'critical_fix_queue.json')
    with open(queue_path, 'w', encoding='utf-8') as f:
        json.dump(critical_queue, f, ensure_ascii=False, indent=2)
    
    print(f"\nResults:")
    print(f"  Critical: {severity_counts.get('critical', 0)}")
    print(f"  High:     {severity_counts.get('high', 0)}")
    print(f"  Medium:   {severity_counts.get('medium', 0)}")
    print(f"  Low:      {severity_counts.get('low', 0)}")
    print(f"\nReport: {report_path}")
    print(f"Summary: {summary_path}")
    print(f"Fix Queue: {queue_path} ({len(critical_queue)} items)")
    
    return severity_counts

if __name__ == '__main__':
    scan_chapters()
