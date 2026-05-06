#!/usr/bin/env python3
"""
Clean up all format issues:
1. Remove excessive --- dividers (keep max 1 per chapter)
2. Clean remaining Chinese characters
3. Remove *Next: and --- combinations completely
"""

import re
from pathlib import Path

OUTPUT_DIR = Path("/Users/owen/Documents/workspace/bestseller/output")

# Remaining Chinese to English mappings
CHINESE_MAP = {
    "拥": "embrace",
    "抱": "hold",
    "僵": "stiff",
    "硬": "hard",
    "入": "enter",
    "潜": "stealth",
    "出": "burst",
    "吐": "spit",
    "手": "hand",
    "及": "and",
    "伸": "stretch",
    "可": "can",
    "脱": "strip",
    "解": "release",
    "赌": "gamble",
    "同": "same",
    "认": "acknowledge",
    "醒": "awake",
    "觉": "feel",
    "播": "broadcast",
    "送": "send",
    "裂": "crack",
    "撕": "tear",
    "集": "gather",
    "体": "body",
    "见": "see",
    "预": "foresee",
    "柔": "soft",
    "温": "warm",
    "跑": "run",
    "逃": "escape",
    "残": "broken",
    "渣": "debris",
    "共": "together",
    "生": "live",
    "步": "step",
    "纹": "pattern",
    "腰": "waist",
    "向": "toward",
    "间": "space",
    "子": "child",
    "正": "straight",
    "按": "press",
    "慢": "slow",
    "讯": "signal",
    "通": "through",
    "女": "woman",
    "器": "device",
    "死": "death",
    "灵": "spirit",
    "魂": "soul",
    "亡": "gone",
    "厂": "factory",
    "工": "work",
    "复": "repeat",
    "制": "make",
    "断": "break",
    "了": "le",
    "气": "air",
    "模": "mold",
    "样": "pattern",
    "告": "report",
    "报": "news",
    "月": "moon",
    "阵": "formation",
    "新": "new",
    "型": "type",
    "承": "bear",
    "构": "structure",
    "载": "carry",
    "结": "tie",
    "案": "case",
    "招": "beckon",
    "再": "again",
    "不": "not",
    "赞": "approve",
    "杀": "kill",
    "包": "wrap",
    "括": "include",
    "我": "I",
    "野": "wild",
    "有": "have",
    "笑": "laugh",
    "都": "all",
    "切": "all",
    "确": "sure",
    "种": "kind",
    "指": "point",
    "关": "close",
    "望": "watch",
    "但": "but",
    "与": "with",
    "无": "no",
    "在": "at",
    "奇": "strange",
    "瘀": "bruise",
    "下": "down",
    "微": "tiny",
    "他": "he",
    "情": "emotion",
    "定": "set",
    "上": "up",
    "挑": "challenge",
    "留": "keep",
    "衅": "provoke",
    "块": "piece",
    "青": "blue",
    "背": "back",
    "粗": "rough",
    "希": "hope",
    "怪": "strange",
    "深": "deep",
    "狙": "snipe",
    "击": "strike",
    "处": "place",
    "过": "past",
    "越": "exceed",
    "听": "hear",
    "化": "change",
    "话": "talk",
    "催": "urge",
    "剂": "agent",
    "接": "connect",
    "拼": "spell",
    "亲": "dear",
    "自": "self",
    "远": "far",
    "永": "forever",
}

stats = {"superhero": {"cleaned": 0, "dividers": 0}, "romantasy": {"cleaned": 0, "dividers": 0}}

for book_type, book_dir in [("superhero", OUTPUT_DIR / "superhero-fiction-1776147970"), 
                              ("romantasy", OUTPUT_DIR / "romantasy-1776330993")]:
    chapters = sorted(book_dir.glob("chapter-*.md"))
    
    for chapter_file in chapters:
        content = chapter_file.read_text(encoding="utf-8")
        original = content
        
        # Clean remaining Chinese characters
        for chinese, english in CHINESE_MAP.items():
            if chinese in content:
                # Replace in context
                content = re.sub(
                    rf'([^\s]){re.escape(chinese)}([^\s])',
                    rf'\1{english}\2',
                    content
                )
                stats[book_type]["cleaned"] += content.count(english) - original.count(english)
        
        # Remove excessive --- dividers (keep only first one if multiple exist)
        divider_count = content.count("\n---\n")
        if divider_count > 1:
            lines = content.split("\n")
            new_lines = []
            divider_seen = False
            for line in lines:
                if line == "---":
                    if not divider_seen:
                        new_lines.append(line)
                        divider_seen = True
                else:
                    new_lines.append(line)
            content = "\n".join(new_lines)
            stats[book_type]["dividers"] += divider_count - 1
        
        # Remove *Next: sections completely (with any preceding ---)
        content = re.sub(r'\n+---\n+\*Next:.*?(?=\n\n|\Z)', '', content, flags=re.DOTALL)
        content = re.sub(r'\n+\*Next:.*?(?=\n\n|\Z)', '', content, flags=re.DOTALL)
        
        if content != original:
            chapter_file.write_text(content, encoding="utf-8")

print("\n" + "="*80)
print("FORMAT CLEANUP REPORT")
print("="*80 + "\n")

print("Chinese Character Cleanup:")
print(f"  The Witness Protocol: {stats['superhero']['cleaned']} replacements")
print(f"  Shadowbound to the Crown: {stats['romantasy']['cleaned']} replacements")

print("\n--- Divider Reduction:")
print(f"  The Witness Protocol: {stats['superhero']['dividers']} excess dividers removed")
print(f"  Shadowbound to the Crown: {stats['romantasy']['dividers']} excess dividers removed")

print("\n✅ All format cleanup completed")
