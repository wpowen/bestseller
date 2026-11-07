"""Run segmented editor and picky-reader reviews against an anonymous reading pack."""

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import tempfile

from bookengine.client import Client, MODEL
from bookengine.storage import load, save

try:
    from tools.review_pack import available_chapters, create_reading_pack, is_system_temp, parse_chapter_spec
except ModuleNotFoundError:
    from review_pack import available_chapters, create_reading_pack, is_system_temp, parse_chapter_spec


EDITOR_QUESTIONS = (
    "开篇能否留住人",
    "金手指何时出现",
    "节奏是否拖沓或重复",
    "人物是否鲜明且有区分",
    "有哪些硬伤",
    "能不能签约",
)
READER_QUESTIONS = (
    "在第几章想弃书",
    "为什么想弃书或继续读",
    "记住了哪些人物",
    "各用一句话描述记住的人物",
)
RESPONSE_SCHEMA = """{
  "claims": [{
    "question": "对应问题", "conclusion": "单一结论", "severity": "critical|high|medium|low",
    "chapter": 1, "quote": "逐字原文，不超过60字", "subject": "结论明确指向的人物或空串",
    "action": "用于核对人物归属的动作或空串"
  }],
  "impressions": [{"question": "对应问题", "conclusion": "没有可核引文的总体印象"}],
  "abandon_chapter": null,
  "remembered_characters": [{"name": "人物名", "description": "一句话"}],
  "signable": null
}"""


@dataclass
class Reviewer:
    role: str
    model: str
    configured_model: str
    compatibility: str
    base: str
    key_env: str
    family: str
    writer_family: str
    same_family: bool
    key: str = field(repr=False)

    def public(self):
        value = asdict(self)
        value.pop("key")
        value.pop("base")
        value.pop("key_env")
        return value


def _env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#") and "=" in text:
            name, value = text.split("=", 1)
            values[name.strip()] = value.strip().strip("\"'")
    return values


def model_family(model, base=""):
    value = (model + " " + base).lower()
    for family, needles in (
        ("minimax", ("minimax", "minimaxi")),
        ("deepseek", ("deepseek",)),
        ("anthropic", ("claude", "anthropic")),
        ("qwen", ("qwen", "dashscope")),
        ("doubao", ("doubao", "volces", "ark.cn")),
        ("kimi", ("kimi", "moonshot")),
        ("openai", ("gpt-", "openai.com")),
        ("xiaomi", ("mimo", "xiaomi")),
    ):
        if any(needle in value for needle in needles):
            return family
    return "unknown"


def _role(values, role):
    prefix = f"BESTSELLER__LLM__{role}__"
    model = values.get(prefix + "MODEL")
    base = values.get(prefix + "API_BASE")
    key_env = values.get(prefix + "API_KEY_ENV")
    key = values.get(key_env or "")
    if not model or not base or not key_env or not key or not base.startswith("https://"):
        return None
    return model, base.rstrip("/"), key_env, key


def _native_model(model, base):
    if model_family(model, base) == "minimax" and model.lower().startswith("openai/") and "minimaxi.com" in base:
        return MODEL, "legacy_litellm_name_replaced_with_engine_model"
    return model, ""


def select_reviewer(env):
    values = _env(env)
    writer = _role(values, "WRITER")
    writer_model, writer_base = (writer[0], writer[1]) if writer else (MODEL, "https://api.minimaxi.com/v1")
    writer_family = model_family(writer_model, writer_base)
    candidates = []
    for role in ("CRITIC", "EDITOR", "PLANNER", "SUMMARIZER", "WRITER"):
        configured = _role(values, role)
        if configured:
            configured_model, base, key_env, key = configured
            model, compatibility = _native_model(configured_model, base)
            family = model_family(configured_model, base)
            candidates.append(
                Reviewer(
                    role.lower(),
                    model,
                    configured_model,
                    compatibility,
                    base,
                    key_env,
                    family,
                    writer_family,
                    family == writer_family,
                    key,
                )
            )
    if not candidates:
        key = values.get("MINIMAX_API_KEY")
        if not key:
            raise ValueError(".env 没有可用的评审模型配置")
        candidates.append(
            Reviewer(
                "writer",
                MODEL,
                MODEL,
                "",
                "https://api.minimaxi.com/v1",
                "MINIMAX_API_KEY",
                "minimax",
                writer_family,
                writer_family == "minimax",
                key,
            )
        )
    return next((item for item in candidates if not item.same_family), candidates[0])


class ReviewClient(Client):
    def __init__(self, output, reviewer, prefix):
        self.out, self.prefix, self.timeout = Path(output), prefix, 180
        self.model, self.base, self._key = reviewer.model, reviewer.base, reviewer.key
        self.context_limit, self.clip_context = None, False
        self.history_path = self.out / "calls.json"
        self.calls = load(self.history_path) if self.history_path.exists() else []
        for entry in self.calls:
            if entry["status"] == "pending":
                entry["status"] = "interrupted"
        save(self.history_path, self.calls)


def _prompt(perspective):
    questions = EDITOR_QUESTIONS if perspective == "editor" else READER_QUESTIONS
    name = "责编" if perspective == "editor" else "挑剔读者"
    return (
        f"你以{name}视角阅读。逐项回答：\n- "
        + "\n- ".join(questions)
        + "\n每条可核结论必须附准确章号和不超过60字的逐字原文；明确说到某个人做了某事时，"
        "subject 写人物，action 写动作。没有逐字引文的内容不得放 claims，只放 impressions。"
        "不要猜测未提供的章节。"
    )


def _normalize(value, perspective, start, end):
    if not isinstance(value, dict):
        raise ValueError("评审返回必须是对象")
    claims, impressions = [], []
    for item in value.get("claims", []):
        if not isinstance(item, dict) or not str(item.get("conclusion", "")).strip():
            continue
        quote = str(item.get("quote", "")).strip()
        chapter = item.get("chapter")
        if isinstance(chapter, str) and chapter.isdigit():
            chapter = int(chapter)
        if not quote:
            impressions.append(
                {
                    "perspective": perspective,
                    "question": str(item.get("question", "")),
                    "conclusion": str(item["conclusion"]).strip(),
                    "reason": "未附引文",
                    "segment": [start, end],
                }
            )
            continue
        if len(quote) > 60 or type(chapter) is not int or not start <= chapter <= end:
            raise ValueError("带引文结论必须有本段章号，且引文不超过60字")
        severity = str(item.get("severity", "medium")).lower()
        if severity not in ("critical", "high", "medium", "low"):
            severity = "medium"
        claims.append(
            {
                "perspective": perspective,
                "question": str(item.get("question", "")).strip(),
                "conclusion": str(item["conclusion"]).strip(),
                "severity": severity,
                "chapter": chapter,
                "quote": quote,
                "subject": str(item.get("subject", "")).strip(),
                "action": str(item.get("action", "")).strip(),
                "segment": [start, end],
            }
        )
    for item in value.get("impressions", []):
        if isinstance(item, dict) and str(item.get("conclusion", "")).strip():
            impressions.append(
                {
                    "perspective": perspective,
                    "question": str(item.get("question", "")).strip(),
                    "conclusion": str(item["conclusion"]).strip(),
                    "reason": "模型标为印象",
                    "segment": [start, end],
                }
            )
    return {
        "claims": claims,
        "impressions": impressions,
        "abandon_chapter": value.get("abandon_chapter"),
        "remembered_characters": value.get("remembered_characters", []),
        "signable": value.get("signable"),
    }


def review_book(book, env, *, output=None, chapter_spec=None, segment_chapters=10, client_factory=ReviewClient):
    book = Path(book).resolve()
    chapters = parse_chapter_spec(chapter_spec) if isinstance(chapter_spec, str) else sorted(chapter_spec or [])
    chapters = chapters or available_chapters(book)
    if not chapters:
        raise ValueError("没有可评审章节")
    if segment_chapters < 1:
        raise ValueError("segment_chapters 必须大于 0")
    output = Path(output or tempfile.mkdtemp(prefix="bestseller-review-run-")).resolve()
    if not is_system_temp(output):
        raise ValueError("评审运行目录必须位于系统临时目录")
    output.mkdir(parents=True, exist_ok=True)
    reviewer = select_reviewer(env)
    client = client_factory(output, reviewer, "你是独立小说评审，只依据匿名阅读包给出可核意见。")
    segments, reviews, claims, impressions = [], [], [], []
    for offset in range(0, len(chapters), segment_chapters):
        selected = chapters[offset : offset + segment_chapters]
        start, end = selected[0], selected[-1]
        pack = output / "packs" / f"chapters-{start:03d}-{end:03d}.md"
        create_reading_pack(book, output=pack, chapter_spec=selected)
        text = pack.read_text(encoding="utf-8")
        segments.append({"chapters": selected})
        for perspective in ("editor", "reader"):
            name = f"review-{perspective}-{start:03d}-{end:03d}"
            instruction = _prompt(perspective) + "\n\n匿名阅读包：\n" + text
            value = client.stage(
                name,
                instruction,
                RESPONSE_SCHEMA,
                cap=6000,
                effort="low",
                normalize=lambda item, p=perspective, a=start, b=end: _normalize(item, p, a, b),
            )
            value = _normalize(value, perspective, start, end)
            review = {"perspective": perspective, "segment": [start, end], **value}
            reviews.append(review)
            claims.extend(value["claims"])
            impressions.extend(value["impressions"])
    result = {
        "book_title": create_reading_pack(book, output=output / "reading-pack.md", chapter_spec=chapters)["title"],
        "chapters": chapters,
        "reviewer": reviewer.public(),
        "prompts": {"editor": _prompt("editor"), "reader": _prompt("reader")},
        "segments": segments,
        "reviews": reviews,
        "claims": claims,
        "impressions": impressions,
    }
    (output / "raw-reviews.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--chapters")
    parser.add_argument("--segment-chapters", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = review_book(
        args.book,
        args.env,
        output=args.output,
        chapter_spec=args.chapters,
        segment_chapters=args.segment_chapters,
    )
    print(json.dumps({"title": result["book_title"], "claims": len(result["claims"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
