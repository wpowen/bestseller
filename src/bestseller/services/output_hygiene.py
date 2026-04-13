from __future__ import annotations

import re

from bestseller.services.writing_profile import is_english_language

_CJK_PATTERN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_LATIN_WORD_PATTERN = re.compile(r"[A-Za-z]{3,}")

_ZH_PLACEHOLDER_PATTERNS = (
    re.compile(r"(?:盟友|队友|同伴|路人|反派|配角|角色|人物|敌人)[甲乙丙丁戊己庚辛壬癸A-Z0-9]+"),
    re.compile(r"(?:待定|未命名|占位|占坑|临时名|后补|placeholder|tbd)", re.IGNORECASE),
    re.compile(r"[\[\(（【](?:角色名|名字待定|名称待定|placeholder|tbd)[\]\)）】]", re.IGNORECASE),
)

_EN_PLACEHOLDER_PATTERNS = (
    re.compile(r"\b(?:ally|friend|mentor|villain|enemy|character|npc|teammate)\s+[A-Z0-9]{1,3}\b", re.IGNORECASE),
    re.compile(r"\b(?:placeholder|tbd|temp name|to be named)\b", re.IGNORECASE),
    # Match "unnamed" only in bracket-style placeholders, not natural prose like
    # "an unnamed stranger".  Natural English uses "unnamed" as an adjective.
    re.compile(r"[\[\(<{]unnamed[\]\)>}]", re.IGNORECASE),
)

_NON_ENGLISH_CONTAMINATION_MARKERS = (
    "chapter ",
    "scene ",
    "reader choice",
    "choice_text",
    "walkthrough",
    "\"choices\"",
    "\"chapter_id\"",
    "\"text\"",
    "option a",
    "prompt:",
)


def _summarize_hits(hits: set[str]) -> str:
    ordered = sorted(hit.strip() for hit in hits if hit and hit.strip())
    return ", ".join(ordered[:5])


def collect_unfinished_artifact_issues(
    content: str,
    *,
    language: str | None = None,
) -> list[str]:
    text = content or ""
    if not text.strip():
        return []

    is_en = is_english_language(language)
    issues: list[str] = []

    patterns = _EN_PLACEHOLDER_PATTERNS if is_en else _ZH_PLACEHOLDER_PATTERNS
    placeholder_hits: set[str] = set()
    for pattern in patterns:
        for match in pattern.findall(text):
            if isinstance(match, tuple):
                placeholder_hits.add("".join(str(part) for part in match if part))
            else:
                placeholder_hits.add(str(match))
    if placeholder_hits:
        issues.append(
            (
                f"Detected unresolved placeholder naming or unfinished tokens: {_summarize_hits(placeholder_hits)}."
                if is_en
                else f"检测到未完成占位符或临时命名：{_summarize_hits(placeholder_hits)}。"
            )
        )

    latin_word_count = len(_LATIN_WORD_PATTERN.findall(text))
    cjk_count = len(_CJK_PATTERN.findall(text))
    lower_text = text.lower()
    contamination_hits = [
        marker for marker in _NON_ENGLISH_CONTAMINATION_MARKERS
        if marker in lower_text
    ]
    if not is_en and cjk_count >= 120 and latin_word_count >= 35 and len(contamination_hits) >= 2:
        issues.append(
            f"检测到明显的英文结构化污染或翻译残留：{', '.join(contamination_hits[:4])}。"
        )

    if is_en and cjk_count >= 60:
        issues.append("Detected substantial non-English text leakage inside an English draft.")

    return issues


# ---------------------------------------------------------------------------
# Quote format sanitization
# ---------------------------------------------------------------------------

# Chinese quotes: normalize all variants to the project-standard style.
_ZH_STRAIGHT_QUOTES = re.compile(r'"([^"]*?)"')
_ZH_CURLY_QUOTES = re.compile(r'\u201c([^\u201d]*?)\u201d')
_ZH_CORNER_QUOTES = re.compile(r'「([^」]*?)」')

# English quotes: normalize all to curly quotes.
_EN_STRAIGHT_DOUBLE = re.compile(r'"([^"]*?)"')


def normalize_quote_format(
    text: str,
    *,
    language: str | None = None,
    target_style: str = "auto",
) -> str:
    """Normalize all quotation marks to a consistent format.

    For Chinese: default to \u201c\u201d (curly quotes).
    For English: default to \u201c\u201d (typographic curly quotes).
    If target_style="corner", use \u300c\u300d for Chinese.
    """
    if not text:
        return text

    is_en = is_english_language(language)

    if is_en:
        # Normalize straight quotes to curly
        result = text.replace('"', '\u201c').replace('"', '\u201d')
        # Fix any corner brackets that leaked in
        result = result.replace('「', '\u201c').replace('」', '\u201d')
        return result

    # Chinese: determine target style
    use_corner = target_style == "corner"

    if use_corner:
        # Normalize to 「」
        result = _ZH_CURLY_QUOTES.sub(r'「\1」', text)
        result = _ZH_STRAIGHT_QUOTES.sub(r'「\1」', result)
    else:
        # Normalize to ""
        result = _ZH_CORNER_QUOTES.sub(r'\u201c\1\u201d', text)
        result = _ZH_STRAIGHT_QUOTES.sub(r'\u201c\1\u201d', result)

    return result
