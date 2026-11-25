"""Default keep-quotes punctuation normalization, preserving standalone timed sound effects."""

import re

_DASH = re.compile(r"——|—|--+")
_SENTENCE = "，,。.!！?？;；:：…"
_PUNCTUATION = _SENTENCE + "、\"“”'‘’」』）)"
_EXPLANATION = re.compile(r"^(因为|原来|这是|那是|也就是|换句话|说白了|所谓|答案|原因|结果|真相|问题在于)")
_NOUN = re.compile(r"(原因|答案|真相|结果|结论|问题|选择|意思)$")
_SOUND = r"(?:当|叮|咚|铛|砰|嘭|啪|轰|哐|吱|咔|咯吱|嘎吱|哐当|咔哒|咔嚓)"
_PACED_SOUND = re.compile(rf'[“「『"]{_SOUND}(?:(?:——|—){_SOUND})*(?:——|—)?[！!]?[”」』"]')
_PACED_CALL = re.compile(r'[“「『"][\u3400-\u9fff]{1,3}(?:——|—)[\u3400-\u9fff][！!][”」』"]')


def _replace_dash(match):
    previous = match.string[: match.start()].rstrip()
    rest = match.string[match.end() :].lstrip()
    left, right = previous[-1:], rest[:1]
    if not left:
        return ""
    if (re.fullmatch(r"[0-9]", left) and re.fullmatch(r"[0-9]", right)) or (right and right in '"”」』'):
        return match[0]
    if not right:
        return "" if left in _SENTENCE else "。"
    if left in _SENTENCE or right in _PUNCTUATION:
        return ""
    return "：" if _EXPLANATION.match(rest) or _NOUN.search(previous.strip()) else "，"


def normalize(text):
    lines = re.split(r"\r?\n", text)
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = lines.pop() == "" if text.endswith("\n") else False
    front = False
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), 0)
        front = any(re.match(r"^[A-Za-z0-9_-]+:\s*", line.strip()) for line in lines[1:end])
    fence = comment = False
    output = []
    changes = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = not fence
        elif front:
            front = not (index > 0 and stripped == "---")
        elif not fence:
            if comment or "<!--" in line:
                comment = "-->" not in (line if comment else line.split("<!--", 1)[1])
            elif stripped == "---":
                changes += 1
                continue
            else:
                original = line
                if not (_PACED_SOUND.fullmatch(stripped) or _PACED_CALL.fullmatch(stripped)):
                    line = _DASH.sub(_replace_dash, line)
                changes += line != original
        output.append(line)
    return newline.join(output) + (newline if trailing else ""), changes
