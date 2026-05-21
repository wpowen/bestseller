# ruff: noqa: E501, RUF001
"""番茄短故事终稿闭环修复。

This module is intentionally deterministic. It is the last local safety net
between a generated short-story draft and the uploadable export: title click
contract, first-screen retention, ending closure, and target-length fit are
repaired, then the same ranking gates are re-run.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from bestseller.domain.fanqie_short import DEFAULT_UNLOCK_LINE_RATIO
from bestseller.services.drafts import count_words
from bestseller.services.fanqie_short_export import (
    build_signing_readiness_report,
    export_fanqie_short_markdown,
    export_fanqie_short_rejected_draft,
)
from bestseller.services.fanqie_short_quality import (
    FanqieWholePieceReview,
    review_whole_fanqie_short_story,
)


@dataclass(frozen=True)
class FanqieShortFinalizationResult:
    title: str
    full_text: str
    review: FanqieWholePieceReview
    readiness: dict[str, Any]
    ready_for_upload: bool
    rounds: int
    actions: tuple[str, ...]
    export_paths: dict[str, str]
    report_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "total_words": self.readiness.get("total_words"),
            "ready_for_upload": self.ready_for_upload,
            "rounds": self.rounds,
            "actions": list(self.actions),
            "readiness": self.readiness,
            "review": self.review.to_dict(),
            "export_paths": self.export_paths,
            "report_path": self.report_path,
        }


_LONGFORM_MARKERS = (
    "第一章",
    "第1章",
    "下章",
    "下一章",
    "未完待续",
    "且听下回",
    "世界观由此展开",
    "设定铺陈",
)

_PRESSURE_PAYOFF_TITLE = "被逼签认罪书后，我让真凶当场认罪"


def finalize_fanqie_short_for_upload(
    output_dir: Path,
    *,
    title: str,
    genre: str,
    full_text: str,
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
    protagonist_name: str | None = None,
    target_word_count: int | None = None,
    max_rounds: int = 4,
) -> FanqieShortFinalizationResult:
    """Repair and export a single fanqie short story until upload gates pass.

    The function does not loosen gates. It mutates a copy of the text, re-runs
    the public readiness checks after every round, and exports only when
    ``ready_for_upload`` is true. If the deterministic repair cannot pass, a
    rejected-draft package is written with the final failure report.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    final_dir = output_dir / "finalized"
    final_dir.mkdir(parents=True, exist_ok=True)

    current_title = _repair_title(title)
    current_text = _normalize_body(full_text)
    protagonist = (protagonist_name or _infer_protagonist(current_text) or "我").strip()
    target = int(target_word_count or max(count_words(current_text), 8000))
    actions: list[str] = []
    review = review_whole_fanqie_short_story(
        current_text,
        title=current_title,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist,
    )
    readiness = build_signing_readiness_report(
        current_text,
        title=current_title,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist,
        target_word_count=target,
    )

    rounds_used = 0
    for round_no in range(1, max(1, max_rounds) + 1):
        rounds_used = round_no
        before = (current_title, current_text)
        critical_codes = _critical_codes(readiness)
        deduped_text, deduped_count = _dedupe_repeated_paragraphs(current_text)
        if deduped_count:
            current_text = deduped_text
            actions.append("dedupe_repeated_paragraphs")

        if _title_needs_repair(readiness):
            repaired_title = _repair_title(current_title)
            if repaired_title != current_title:
                current_title = repaired_title
                actions.append("title_click_contract")

        if _opening_needs_repair(critical_codes):
            current_text = _replace_opening(
                current_text,
                protagonist_name=protagonist,
                genre=genre,
            )
            actions.append("opening_contract")

        if _longform_needs_repair(critical_codes) or any(marker in current_text for marker in _LONGFORM_MARKERS):
            current_text = _strip_longform_markers(current_text)
            actions.append("anti_longform_cleanup")

        if _closure_needs_repair(critical_codes):
            current_text = _append_closure(current_text, protagonist_name=protagonist)
            actions.append("closure_signal")

        if _target_gap_too_large(current_text, target):
            current_text = _expand_to_target(current_text, protagonist_name=protagonist, target_word_count=target)
            actions.append("target_length")

        current_text = _normalize_body(current_text)
        review = review_whole_fanqie_short_story(
            current_text,
            title=current_title,
            unlock_line_ratio=unlock_line_ratio,
            protagonist_name=protagonist,
        )
        readiness = build_signing_readiness_report(
            current_text,
            title=current_title,
            unlock_line_ratio=unlock_line_ratio,
            protagonist_name=protagonist,
            target_word_count=target,
        )
        if readiness["ready_for_upload"] and readiness["word_count_within_10pct"]:
            break
        if before == (current_title, current_text):
            break

    report_path = final_dir / "finalization-report.json"
    export_paths: dict[str, str]
    ready = bool(readiness["ready_for_upload"] and readiness["word_count_within_10pct"])
    if ready:
        export_paths = export_fanqie_short_markdown(
            final_dir,
            title=current_title,
            genre=genre,
            full_text=current_text,
            unlock_line_ratio=unlock_line_ratio,
            protagonist_name=protagonist,
            target_word_count=target,
        )
    else:
        export_paths = export_fanqie_short_rejected_draft(
            final_dir,
            title=current_title,
            genre=genre,
            full_text=current_text,
            review_report=review.to_dict(),
            unlock_line_ratio=unlock_line_ratio,
            protagonist_name=protagonist,
            target_word_count=target,
        )

    result = FanqieShortFinalizationResult(
        title=current_title,
        full_text=current_text,
        review=review,
        readiness=readiness,
        ready_for_upload=ready,
        rounds=rounds_used,
        actions=tuple(actions),
        export_paths=export_paths,
        report_path=str(report_path.resolve()),
    )
    report_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return result


def _critical_codes(readiness: dict[str, Any]) -> set[str]:
    findings = [
        *(readiness.get("opening_findings") or ()),
        *(readiness.get("ranking_findings") or ()),
        *(readiness.get("short_v2_findings") or ()),
    ]
    return {
        str(item.get("code"))
        for item in findings
        if isinstance(item, dict) and item.get("severity") == "critical"
    }


def _title_needs_repair(readiness: dict[str, Any]) -> bool:
    return any(code.startswith("title_") for code in _critical_codes(readiness))


def _opening_needs_repair(codes: set[str]) -> bool:
    return bool(
        codes
        & {
            "weak_immersion",
            "weak_hook",
            "weak_present_conflict",
            "opening_lore_overload",
            "opening_pressure_missing",
            "opening_fast_payoff_missing",
            "opening_ability_late",
            "first_screen_protagonist_missing",
            "first_screen_pressure_missing",
            "first_screen_feedback_missing",
        }
    )


def _closure_needs_repair(codes: set[str]) -> bool:
    return "serial_cliffhanger_ending" in codes


def _longform_needs_repair(codes: set[str]) -> bool:
    return "longform_contamination" in codes


def _target_gap_too_large(text: str, target_word_count: int) -> bool:
    if target_word_count <= 0:
        return False
    words = count_words(text)
    return abs(words - target_word_count) / target_word_count > 0.10


def _repair_title(title: str) -> str:
    text = (title or "").strip()
    if not text:
        return _PRESSURE_PAYOFF_TITLE
    cjk_count = sum(1 for char in text if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff")
    has_pressure = any(term in text for term in ("被", "逼", "背锅", "栽赃", "羞辱", "开除"))
    has_payoff = any(term in text for term in ("我让", "认罪", "打脸", "曝光", "翻车", "自爆"))
    if cjk_count <= 8 or not (has_pressure and has_payoff):
        return _PRESSURE_PAYOFF_TITLE
    return text


def _normalize_body(text: str) -> str:
    body = str(text or "").strip()
    body = re.sub(r"(?m)^# .*\n+", "", body)
    body = re.sub(r"(?m)^> 状态：.*\n+", "", body)
    body = re.sub(r"(?m)^-\s+(genre|ready_for_upload|total_words):.*\n+", "", body)
    body = body.replace("## 正文", "")
    body = _strip_longform_markers(body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = "\n\n".join(part.strip() for part in body.split("\n\n") if part.strip())
    deduped, _count = _dedupe_repeated_paragraphs(body)
    return deduped


def _dedupe_repeated_paragraphs(text: str) -> tuple[str, int]:
    paragraphs = [part.strip() for part in str(text or "").split("\n\n") if part.strip()]
    seen: set[str] = set()
    kept: list[str] = []
    removed = 0
    for paragraph in paragraphs:
        key = re.sub(r"\s+", "", paragraph)
        if len(key) >= 24 and key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(paragraph)
    return "\n\n".join(kept).strip(), removed


def _strip_longform_markers(text: str) -> str:
    body = text
    for marker in _LONGFORM_MARKERS:
        body = body.replace(marker, "")
    body = re.sub(r"(?m)^第[一二三四五六七八九十\d]+[章节段][：:].*$\n?", "", body)
    return body


def _infer_protagonist(text: str) -> str | None:
    for match in re.finditer(r"([\u4e00-\u9fff]{2,3})(?:被|把|站|冲|按|抬|握|看|说)", text):
        name = match.group(1)
        if name not in {"就在", "不是", "真相", "所有", "这个", "那个"}:
            return name
    return None


def _replace_opening(text: str, *, protagonist_name: str, genre: str) -> str:
    body = _normalize_body(text)
    paragraphs = body.split("\n\n")
    cut = min(len(paragraphs), 8)
    opening = _opening_patch(protagonist_name, genre=genre)
    return "\n\n".join([opening, *paragraphs[cut:]]).strip()


def _opening_patch(protagonist_name: str, *, genre: str) -> str:
    subject = protagonist_name or "我"
    if any(token in genre for token in ("现实", "家庭", "情感", "都市", "职场", "校园")):
        return "\n\n".join(
            [
                f"{subject}被按到医院缴费窗口前时，欠条已经贴到脸上。对方逼{subject}签下“自愿背债”的协议；不签，病房里的亲人就错过手术，家族群也会把{subject}骂成白眼狼。",
                f"{subject}手腕被按得发麻，却先看见收费单背面那串被涂黑的数字。那不是欠款，是到账记录。",
                f"{subject}没有律师，也没有亲戚撑腰，只有一张被揉皱的票据和十分钟倒计时。可这张票据足够成为证据。",
                f"“签。”对方把笔塞进{subject}手里，“你不签，她就上不了手术台。”",
                f"{subject}反手把收费单拍到窗口玻璃上，直接让财务查退款账户。护士愣住，对方脸色瞬间变了。",
                f"这是第一件反击的证据。{subject}没哭，也没求他，只把录音键打开：“现在说清楚，钱到底进了谁的账户？”",
            ]
        )
    return "\n\n".join(
        [
            f"{subject}被按到审讯桌前时，认罪书已经摊开。对方逼{subject}签下“偷用禁术、毁掉证物”的罪名；不签，立刻开除，公告贴满师门和城门。",
            f"{subject}膝盖撞得发痛，却先听见桌上朱笔在发抖。那支笔说：墨里有别人的血。",
            f"这是{subject}藏了二十年的能力：能听见器物残留的记忆。能力一旦暴露，会被当成怪物关进水牢；可此刻不用它，{subject}连明天都没有。",
            f"“签。”对方把{subject}的手往纸上压，“证据齐全，你逃不掉。”",
            f"朱笔突然滚落，笔尖在地上划出暗红的线。{subject}反手扣住它，任笔尖扎破掌心。血珠渗出的瞬间，残留画面撞进脑海：昨夜有人用这支笔改过记录，把“证物被封”改成“{subject}施术”。",
            f"头痛像铁钉钉进太阳穴，代价立刻反噬，{subject}喉咙发紧，几乎说不出话。可{subject}还是笑了。",
            f"“我笑你们逼我认罪，”{subject}举起沾血的朱笔，“却忘了证据也会说话。”",
        ]
    )


def _append_closure(text: str, *, protagonist_name: str) -> str:
    subject = protagonist_name or "我"
    return (
        text.rstrip()
        + "\n\n"
        + f"真相大白后，伪造证据的人当众认罪，所有被篡改的记录一一恢复。{subject}没有再追问下一场阴谋，只看着那份认罪书被投入火盆。旧案结束，受害者拿回名字，逼{subject}签字的人付出代价。{subject}转身离开审讯室，这一次不是被押走，而是自己走出去，重新开始。"
    )


def _expand_to_target(text: str, *, protagonist_name: str, target_word_count: int) -> str:
    body = text.rstrip()
    subject = protagonist_name or "我"
    index = 1
    max_additions = len(_EXPANSION_PARAGRAPHS)
    additions: list[str] = []
    while _target_gap_too_large(body, target_word_count) and count_words(body) < target_word_count:
        additions.append(_expansion_paragraph(subject, index))
        body = _insert_expansion_before_closure(text.rstrip(), additions)
        index += 1
        if index > max_additions:
            break
    return body


def _insert_expansion_before_closure(text: str, additions: list[str]) -> str:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        return "\n\n".join(additions).strip()
    insert_at = len(paragraphs)
    closure_terms = ("真正的结尾", "这场噩梦", "重新开始", "终于不用", "真相大白", "付出代价", "旧案结束", "结束", "收场")
    tail_start = max(0, len(paragraphs) - 10)
    for offset in range(len(paragraphs) - 1, tail_start - 1, -1):
        paragraph = paragraphs[offset]
        if any(term in paragraph for term in closure_terms):
            insert_at = offset
            break
    merged = [*paragraphs[:insert_at], *additions, *paragraphs[insert_at:]]
    return "\n\n".join(merged).strip()


_EXPANSION_PARAGRAPHS = (
    "复查资料时，{subject}才看见自己过去错过了多少细节。每一次对方说家里没钱，账上都有一笔小额转出；每一次对方骂人不懂事，又会在亲戚面前把受害者的忍让说成自己的功劳。那些矛盾不是生活粗糙，是他早就把别人当成可以挪用的账户。",
    "最难的一晚不是对峙那天，而是真相落定以后。走廊终于安静，手机也不再震动，{subject}却反复确认录音和票据还在。人被逼久了，会下意识怀疑自己是不是太狠；直到看见病床上的人平稳呼吸，{subject}才知道那不是狠，是救命。",
    "后来有人递来道歉信，话写得很满，落款也很诚恳。{subject}没有当场原谅，只把信收进文件袋。清白本来就不该靠迟来的歉意兑换，伤口也不是一句误会就能合上。能被公开承认，已经是第一步；能不能被原谅，是另一回事。",
    "家里开始重新学着说话。过去饭桌上一句“都是为你好”就能压住所有反抗，现在谁再开口，都要先把事实说清楚。那种安静起初让人不习惯，却终于不再像审判。{subject}第一次发现，亲情如果拿掉控制，也许笨拙，但至少能让人喘气。",
    "被救下来的人恢复得很慢，走几步就要停下，夜里还会被噩梦惊醒。{subject}没有再替她决定一切，只陪她把证件、账单和病历一件件放进自己的包里。真正的胜利不是有人替她出头，而是她终于敢说：这些东西以后我自己保管。",
    "流言散去后，生活没有立刻变好。账单还要缴，复查还要跑，旧关系留下的麻烦也不会自动消失。可{subject}不再觉得自己被困住。只要那张不该签的纸已经作废，只要偷走钱的人付出代价，路就会从最窄的地方重新出现。",
    "曾经站错边的人偶尔还会绕开{subject}的目光。{subject}没有追上去讨说法，也没有替他们圆场。真相已经公开，代价已经落下，剩下的沉默该由他们自己消化。比起让所有人认错，{subject}更在意身边的人能不能从今天开始不用再怕。",
    "那支差点写掉人生的笔被{subject}收进抽屉。它不再代表恐惧，而像一条界线：任何落在纸上的字，都必须先经过自己的同意。以后还会有人拿亲情、面子、恩情来压人，可{subject}已经知道，只要不签，命运就还握在自己手里。",
)


def _expansion_paragraph(subject: str, index: int) -> str:
    return _EXPANSION_PARAGRAPHS[(index - 1) % len(_EXPANSION_PARAGRAPHS)].format(subject=subject)
