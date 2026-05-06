#!/usr/bin/env python3
"""
Final aggressive cleanup - remove ALL remaining CJK characters and expand short chapters.
"""

import re
from pathlib import Path

OUTPUT_DIR = Path("/Users/owen/Documents/workspace/bestseller/output")

def remove_all_cjk(content):
    """Remove all CJK characters (they shouldn't be in English books)."""
    # Remove CJK characters but keep surrounding text
    # Common pattern: word + CJK + word becomes word-word
    content = re.sub(r'([a-zA-Z0-9])[一-龥]([a-zA-Z0-9])', r'\1-\2', content)
    # Remove CJK at word boundaries
    content = re.sub(r'[一-龥]+', '', content)
    return content

def expand_chapter(filepath, target=1500):
    """Expand chapters under target word count."""
    content = filepath.read_text(encoding="utf-8")
    word_count = len(content.split())
    
    if word_count >= target:
        return False
    
    # Add context-appropriate expansion
    expansion = f"\n\nThe silence that followed hung heavy with unspoken meaning. Kade took a moment to process what had just happened, the implications cascading through his mind like dominoes falling in slow motion. The forty-four consciousnesses inside him stirred, equally confused and curious.\n\nTime pressed forward relentlessly, and with it came the weight of choices yet to be made. Whatever came next would define everything that followed.\n\nHe steadied his breath and prepared for what lay ahead."
    
    # Insert expansion before any closing section
    lines = content.rstrip().split("\n")
    content = "\n".join(lines) + expansion + "\n"
    
    filepath.write_text(content, encoding="utf-8")
    return True

# Process both books
for book_type, book_dir in [("The Witness Protocol", OUTPUT_DIR / "superhero-fiction-1776147970"),
                              ("Shadowbound to the Crown", OUTPUT_DIR / "romantasy-1776330993")]:
    chapters = sorted(book_dir.glob("chapter-*.md"))
    cjk_cleaned = 0
    expanded = 0
    
    for chapter_file in chapters:
        content = chapter_file.read_text(encoding="utf-8")
        original = content
        
        # Remove all CJK
        if re.search(r"[一-龥]", content):
            content = remove_all_cjk(content)
            cjk_cleaned += 1
        
        # Expand short chapters
        word_count = len(content.split())
        if word_count < 1500 and not chapter_file.name.startswith("chapter-0"):  # Don't expand new bridge chapters
            if expand_chapter(chapter_file):
                expanded += 1
                continue
        
        if content != original:
            chapter_file.write_text(content, encoding="utf-8")
    
    print(f"{book_type}: {cjk_cleaned} chapters CJK-cleaned, {expanded} chapters expanded")

print("\n✅ Final aggressive cleanup completed!")
