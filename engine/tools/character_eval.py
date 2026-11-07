"""Model-assisted, source-verified character baselines for saved books."""

import argparse
import json
from pathlib import Path

import yaml

from bookengine.character_diagnostics import character_reference, dialogue_samples, score_guesses, verify_fulfillment
from bookengine.client import Client
from bookengine.storage import save
from tools.book_checkup import _chapters, _read, write_checkup


JUDGE_SYSTEM = (
    "你是小说人物区分度评审。只能依据给定正文作答；引文必须逐字来自正文。"
    "不知道就留空，不得用常识补写。只输出要求的JSON。"
)


def _env_values(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("\"'")
    return values


def _family(base):
    lowered = base.lower()
    if "deepseek" in lowered:
        return "DeepSeek"
    if "minimax" in lowered:
        return "MiniMax"
    return "Unknown"


def judge_profile(env):
    """Choose a configured judge family different from the configured writer."""
    values = _env_values(env)
    writer_base = values.get("BESTSELLER__LLM__WRITER__API_BASE", "https://api.minimaxi.com/v1")
    writer_family = _family(writer_base)
    profiles = [
        ("DeepSeek", "deepseek-flash", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
        ("MiniMax", "MiniMax-M3.1-Flash", "https://api.minimaxi.com/v1", "MINIMAX_API_KEY"),
    ]
    selected = next((profile for profile in profiles if values.get(profile[3]) and profile[0] != writer_family), None)
    if selected is None:
        key_env = values.get("BESTSELLER__LLM__WRITER__API_KEY_ENV", "MINIMAX_API_KEY")
        model = values.get("BESTSELLER__LLM__WRITER__MODEL", "MiniMax-M3.1-Flash").split("/")[-1]
        selected = (writer_family, model, writer_base, key_env)
    family, model, api_base, key_env = selected
    if not values.get(key_env):
        raise ValueError("No configured model credential for character judge")
    return {
        "family": family,
        "model": model,
        "api_base": api_base,
        "key_env": key_env,
        "same_family": family == writer_family,
    }


def make_judge(book, env, timeout=180):
    profile = judge_profile(env)
    out = Path(book) / "character-eval"
    out.mkdir(parents=True, exist_ok=True)
    client = Client(
        out,
        env,
        "",
        timeout=timeout,
        model=profile["model"],
        key_env=profile["key_env"],
        api_base=profile["api_base"],
        provider=profile["family"],
        system=JUDGE_SYSTEM,
    )
    client.family = profile["family"]
    client.same_family = profile["same_family"]
    return client


def _book_inputs(book):
    book = Path(book).resolve()
    unknown = []
    bible = _read(book / "story_bible.yaml", unknown)
    chapters = _chapters(book, "final", unknown)
    return book, bible, chapters, unknown


def _evidence_corpus(bible, chapters, per_character=24):
    corpus = {}
    for card in bible.get("characters", []):
        name = card.get("name")
        if not name:
            continue
        aliases = card.get("aliases", [])
        aliases = [aliases] if isinstance(aliases, str) else aliases if isinstance(aliases, list) else []
        labels = [value for value in (name, card.get("address"), *aliases) if isinstance(value, str) and value]
        excerpts = []
        for chapter in chapters:
            for paragraph in chapter["text"].splitlines():
                if any(label in paragraph for label in labels):
                    excerpts.append({"chapter": chapter["number"], "text": paragraph[:800]})
                    if len(excerpts) == per_character:
                        break
            if len(excerpts) == per_character:
                break
        corpus[name] = excerpts
    return corpus


def _blind(client, bible, chapters):
    samples = dialogue_samples(bible, chapters)
    speakers = {sample["speaker"] for sample in samples}
    if len(samples) < 20 or len(speakers) < 4:
        return {
            "status": "Unknown",
            "reason": "fewer_than_20_explicit_samples_or_4_speakers",
            "total": len(samples),
            "speaker_count": len(speakers),
            "accuracy": None,
        }
    prompt = json.dumps(
        {
            "task": "名字和称呼已抹除。逐段猜说话人；只能从candidates选一个。",
            "candidates": [card.get("name") for card in bible.get("characters", []) if card.get("name")],
            "samples": [{"id": row["id"], "chapter": row["chapter"], "text": row["masked"]} for row in samples],
        },
        ensure_ascii=False,
    )
    raw = client.stage(
        "character-blind",
        prompt,
        schema='{"guesses":[{"id":1,"speaker":"候选人物名"}]}',
        validate=lambda value: isinstance(value, dict) and isinstance(value.get("guesses"), list),
        cap=3000,
    )
    return dict(status="Evidence", speaker_count=len(speakers), **score_guesses(samples, raw["guesses"]))


def _fulfillment(client, bible, chapters):
    cards = [
        {key: card.get(key) for key in ("name", "role", "flaw", "bottom_line", "voice", "catchphrase") if card.get(key)}
        for card in bible.get("characters", [])
    ]
    prompt = json.dumps(
        {
            "task": (
                "逐人物逐字段找能直接证明正文兑现了该人设的逐字引文。只返回确有引文的项；"
                "字段写在卡上但正文没有行为或台词兑现时不要返回。"
            ),
            "cards": cards,
            "corpus": _evidence_corpus(bible, chapters),
        },
        ensure_ascii=False,
    )
    raw = client.stage(
        "character-fulfillment",
        prompt,
        schema='{"findings":[{"character":"姓名","field":"flaw|bottom_line|voice|catchphrase","chapter":1,"quote":"逐字引文"}]}',
        validate=lambda value: isinstance(value, dict) and isinstance(value.get("findings"), list),
        cap=6000,
    )
    return verify_fulfillment(raw, bible, chapters)


def evaluate_book(book, client):
    book, bible, chapters, unknown = _book_inputs(book)
    result = {
        "schema_version": 1,
        "reference_only": True,
        "judge_family": getattr(client, "family", "Unknown"),
        "judge_model": getattr(client, "model", "Unknown"),
        "same_family_as_writer": bool(getattr(client, "same_family", True)),
        "blind_test": _blind(client, bible, chapters),
        "fulfillment": _fulfillment(client, bible, chapters),
        "antagonist_presence": character_reference(bible, chapters)["antagonist_presence"],
        "unknown_inputs": unknown,
        "limitations": [
            "Model judgments are reference values, not reader acceptance.",
            "Only verbatim quotes found in the claimed saved chapter are Evidence.",
        ],
    }
    save(book / "character_evaluation.json", result)
    write_checkup(book)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="人物区分度基线：只在指定书副本写评测与调用账本")
    parser.add_argument("books", nargs="+", type=Path)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    outputs = []
    for book in args.books:
        client = make_judge(book, args.env, args.timeout)
        result = evaluate_book(book, client)
        outputs.append({"book": str(book), "accuracy": result["blind_test"]["accuracy"]})
    print(json.dumps(outputs, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
