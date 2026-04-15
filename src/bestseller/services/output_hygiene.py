from __future__ import annotations

import re

from bestseller.services.writing_profile import is_english_language

_CJK_PATTERN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_LATIN_WORD_PATTERN = re.compile(r"[A-Za-z]{3,}")

# Placeholder detection: match ONLY in explicit placeholder contexts
# (brackets, "name:" labels, or role-word + capitalized short code).
#
# The patterns intentionally refuse to match bare words inside natural prose:
#   - "在身后补了一句" (natural ZH compound "behind" + "supplement") must NOT
#     trigger on the substring "后补".
#   - "used to be named Marcus Vance" (natural EN past-tense "was called")
#     must NOT trigger on "to be named".
#   - "Eleven prototypes. And one placeholder." (diegetic EN use) must NOT
#     trigger on standalone "placeholder".
#   - "ally he knew would never return" (natural EN phrase) must NOT trigger
#     on "ally he" — the old pattern's `re.IGNORECASE` on `[A-Z0-9]{1,3}`
#     matched any short lowercase word.
#
# Each pattern below requires one of:
#   a) a role word followed by an uppercase single-letter / short code
#      (e.g. "盟友甲", "Ally A", "Enemy B1");
#   b) the placeholder token enclosed in explicit delimiters
#      (e.g. "[placeholder]", "（角色名）", "<TBD>"); or
#   c) a "姓名：/Name:" label immediately preceding the token.
_ZH_PLACEHOLDER_PATTERNS = (
    # "盟友甲", "反派丙", "配角A", "敌人B1" — role word + short code.
    # The character class deliberately only covers 天干 (甲乙丙…) + ASCII
    # letters/digits, so real Chinese names ("盟友林惊雪") are already
    # excluded by construction.  The trailing negative-lookahead only guards
    # against Latin continuation — e.g. "盟友Alex" must not match "盟友A".
    re.compile(
        r"(?:盟友|队友|同伴|路人|反派|配角|角色|人物|敌人)"
        r"[甲乙丙丁戊己庚辛壬癸ABCDEFGHIJKLMNOPQRSTUVWXYZ0-9]{1,3}"
        r"(?![A-Za-z0-9])"
    ),
    # Bracketed placeholder: [待定] / （占位） / 【placeholder】 / <TBD>
    re.compile(
        r"[\[\(（【<](?:待定|未命名|占位|占坑|临时名|后补|角色名|名字待定|名称待定|placeholder|tbd)[\]\)）】>]",
        re.IGNORECASE,
    ),
    # Labeled placeholder: "姓名：待定", "名字: TBD", "名称：占位"
    re.compile(
        r"(?:姓名|名字|名称)\s*[：:]\s*(?:待定|未命名|占位|占坑|临时名|后补|placeholder|tbd)\b",
        re.IGNORECASE,
    ),
)

_EN_PLACEHOLDER_PATTERNS = (
    # "Ally A", "Enemy B1", "Villain C" — role word + uppercase short code.
    # No IGNORECASE so natural prose like "ally he" / "enemy or" is safe.
    re.compile(
        r"\b(?:Ally|Friend|Mentor|Villain|Enemy|Character|NPC|Teammate)\s+[A-Z][A-Z0-9]{0,2}\b"
    ),
    # Bracketed placeholder: [placeholder] / <TBD> / {unnamed}
    re.compile(
        r"[\[\(<{](?:placeholder|tbd|temp\s*name|to\s*be\s*named|unnamed)[\]\)>}]",
        re.IGNORECASE,
    ),
    # Labeled placeholder: "Name: Placeholder", "Character: TBD"
    re.compile(
        r"\b(?:name|character|role)\s*:\s*(?:placeholder|tbd|to\s+be\s+named|unnamed)\b",
        re.IGNORECASE,
    ),
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

# Curly-quote characters used in replacement strings.  Keeping them as named
# constants avoids leaning on raw-string ``\uXXXX`` escapes, which are NOT
# valid regex / replacement escapes (``re.error: bad escape \u``).
_CURLY_L = "\u201c"
_CURLY_R = "\u201d"

# Chinese quotes: normalize all variants to the project-standard style.
# Non-raw strings are used so Python interprets ``\u201c``/``\u201d`` as the
# curly-quote characters at compile time, before the regex engine sees them.
_ZH_STRAIGHT_QUOTES = re.compile(r'"([^"]*?)"')
_ZH_CURLY_QUOTES = re.compile("\u201c([^\u201d]*?)\u201d")
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
        # Pair up straight doubles: "hello" -> "hello" with typographic quotes.
        result = _EN_STRAIGHT_DOUBLE.sub(f"{_CURLY_L}\\1{_CURLY_R}", text)
        # Fix any corner brackets that leaked in.
        result = result.replace("「", _CURLY_L).replace("」", _CURLY_R)
        return result

    # Chinese: determine target style
    use_corner = target_style == "corner"

    if use_corner:
        # Normalize to 「」
        result = _ZH_CURLY_QUOTES.sub(r"「\1」", text)
        result = _ZH_STRAIGHT_QUOTES.sub(r"「\1」", result)
    else:
        # Normalize to ""
        result = _ZH_CORNER_QUOTES.sub(f"{_CURLY_L}\\1{_CURLY_R}", text)
        result = _ZH_STRAIGHT_QUOTES.sub(f"{_CURLY_L}\\1{_CURLY_R}", result)

    return result
