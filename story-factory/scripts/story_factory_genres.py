from __future__ import annotations


GENRE_RULES = {
    "都市逆袭": "公开打脸、身份反转、资源跃迁",
    "修仙升级": "等级门槛、资源争夺、阶段破境",
    "悬疑生存": "规则压力、信息差、倒计时",
    "职场商战": "证据链、话语权、策略拆招",
    "末日爽文": "资源争夺、安全区权力、尸潮倒计时、公开立威、阵营吞并",
}

VALID_GENRES = set(GENRE_RULES)
DEFAULT_GENRE = "修仙升级"


def format_allowed_genres() -> str:
    return "|".join(GENRE_RULES)


def genre_rule_summary(genre: str) -> str:
    return GENRE_RULES.get(genre, GENRE_RULES[DEFAULT_GENRE])
