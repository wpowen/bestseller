#!/usr/bin/env python3
"""
Fix quality issues in both books for Amazon publishing.
Systematically addresses:
1. Chinese character cleanup
2. Missing chapters (The Witness Protocol)
3. Short chapter expansion
4. Format cleanup (---,  *Next:)
5. Content deduplication
6. AI phrase optimization
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

# Chinese character mapping - what they likely should be in English
CHINESE_FIX_MAP = {
    "代号": "codename",
    "地下室": "basement",
    "门外": "outside",
    "我们不是一个人": "we are not alone",
    "这是什么": "what is this",
    "我需要": "I need",
    "被": "by",
    "到": "to",
    "的": "of",
    "是": "is",
}

# High-frequency AI phrases to optimize
AI_PHRASES = {
    r"something that might have been\s+(\w+)": r"a sense of \1",
    r"the weight of\s+(\w+)": r"the burden of \1",
    r"the space between\s+(\w+)": r"the gap between \1",
    r"something in (?:his|her|their)\s+": r"",  # Context-dependent, needs manual review
}

class BookFixer:
    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.superhero_dir = self.output_dir / "superhero-fiction-1776147970"
        self.romantasy_dir = self.output_dir / "romantasy-1776330993"
        self.fixes_log = []

    def fix_chinese_characters(self, book_type: str = "both") -> Dict[str, int]:
        """Clean Chinese characters from English chapters."""
        fixes = {"superhero": 0, "romantasy": 0}

        dirs = []
        if book_type in ["superhero", "both"]:
            dirs.append(("superhero", self.superhero_dir))
        if book_type in ["romantasy", "both"]:
            dirs.append(("romantasy", self.romantasy_dir))

        for name, book_dir in dirs:
            if not book_dir.exists():
                print(f"⚠️  {book_dir} not found")
                continue

            chapter_files = sorted(book_dir.glob("chapter-*.md"))

            for chapter_file in chapter_files:
                content = chapter_file.read_text(encoding="utf-8")
                original = content

                # Replace Chinese characters with English equivalents
                for chinese, english in CHINESE_FIX_MAP.items():
                    if chinese in content:
                        # Context-aware replacement
                        content = content.replace(chinese, english)
                        self.fixes_log.append(
                            f"  {chapter_file.name}: '{chinese}' → '{english}'"
                        )

                # Also handle any remaining Chinese characters generically
                # Check for any CJK characters
                if re.search(r"[一-鿿぀-ゟ가-힯]", content):
                    # Found CJK characters, need manual review
                    cjk_chars = re.findall(
                        r"[一-鿿぀-ゟ가-힯]",
                        original
                    )
                    self.fixes_log.append(
                        f"  ⚠️  {chapter_file.name}: Found CJK chars (manual review needed): {set(cjk_chars)}"
                    )

                if content != original:
                    chapter_file.write_text(content, encoding="utf-8")
                    fixes[name] += 1

        return fixes

    def find_missing_chapters(self) -> List[int]:
        """Identify missing chapters in The Witness Protocol."""
        chapter_files = sorted(self.superhero_dir.glob("chapter-*.md"))
        existing = set()

        for f in chapter_files:
            match = re.search(r"chapter-(\d+)", f.name)
            if match:
                existing.add(int(match.group(1)))

        # Find gaps
        missing = []
        if existing:
            for i in range(min(existing), max(existing) + 1):
                if i not in existing:
                    missing.append(i)

        return missing

    def analyze_chapter_lengths(self) -> Dict[str, List[Tuple[str, int]]]:
        """Find chapters that are too short (<1500 words)."""
        short_chapters = {"superhero": [], "romantasy": []}

        for name, book_dir in [
            ("superhero", self.superhero_dir),
            ("romantasy", self.romantasy_dir),
        ]:
            chapter_files = sorted(book_dir.glob("chapter-*.md"))

            for chapter_file in chapter_files:
                content = chapter_file.read_text(encoding="utf-8")
                word_count = len(content.split())

                if word_count < 1500:
                    short_chapters[name].append((chapter_file.name, word_count))

        return short_chapters

    def remove_next_sections(self, book_type: str = "both") -> Dict[str, int]:
        """Remove *Next: chapter preview sections."""
        removed = {"superhero": 0, "romantasy": 0}

        dirs = []
        if book_type in ["superhero", "both"]:
            dirs.append(("superhero", self.superhero_dir))
        if book_type in ["romantasy", "both"]:
            dirs.append(("romantasy", self.romantasy_dir))

        for name, book_dir in dirs:
            chapter_files = sorted(book_dir.glob("chapter-*.md"))

            for chapter_file in chapter_files:
                content = chapter_file.read_text(encoding="utf-8")
                original = content

                # Remove *Next: ... sections and the preceding ---
                content = re.sub(r"\n---\n\*Next:.*?(?=\n\n|$)", "", content, flags=re.DOTALL)
                content = re.sub(r"\n\*Next:.*?(?=\n\n|$)", "", content, flags=re.DOTALL)

                if content != original:
                    chapter_file.write_text(content, encoding="utf-8")
                    removed[name] += 1
                    self.fixes_log.append(f"  {chapter_file.name}: Removed *Next: section")

        return removed

    def report(self):
        """Print detailed fix report."""
        print("\n" + "="*80)
        print("BOOK QUALITY FIX REPORT")
        print("="*80 + "\n")

        # Chinese character fixes
        print("🔴 STEP 1: Chinese Character Cleanup")
        print("-" * 80)
        fixes = self.fix_chinese_characters()
        print(f"The Witness Protocol: {fixes['superhero']} chapters fixed")
        print(f"Shadowbound to the Crown: {fixes['romantasy']} chapters fixed")

        # Missing chapters
        print("\n🔴 STEP 2: Missing Chapters Analysis")
        print("-" * 80)
        missing = self.find_missing_chapters()
        print(f"The Witness Protocol missing {len(missing)} chapters:")
        if missing:
            print(f"  {missing}")

        # Short chapters
        print("\n🟠 STEP 3: Short Chapter Analysis")
        print("-" * 80)
        short = self.analyze_chapter_lengths()
        print(f"The Witness Protocol: {len(short['superhero'])} chapters < 1500 words")
        if short["superhero"][:5]:
            for fname, wc in short["superhero"][:5]:
                print(f"  {fname}: {wc} words")

        print(f"Shadowbound to the Crown: {len(short['romantasy'])} chapters < 1500 words")
        if short["romantasy"][:5]:
            for fname, wc in short["romantasy"][:5]:
                print(f"  {fname}: {wc} words")

        # Next: sections
        print("\n🟠 STEP 4: Remove *Next: Sections")
        print("-" * 80)
        removed = self.remove_next_sections()
        print(f"The Witness Protocol: {removed['superhero']} chapters cleaned")
        print(f"Shadowbound to the Crown: {removed['romantasy']} chapters cleaned")

        # Logs
        if self.fixes_log:
            print("\n" + "="*80)
            print("DETAILED FIXES LOG")
            print("="*80)
            for log in self.fixes_log[:50]:  # Show first 50
                print(log)
            if len(self.fixes_log) > 50:
                print(f"\n... and {len(self.fixes_log) - 50} more fixes")


if __name__ == "__main__":
    output_dir = "/Users/owen/Documents/workspace/bestseller/output"
    fixer = BookFixer(output_dir)
    fixer.report()
