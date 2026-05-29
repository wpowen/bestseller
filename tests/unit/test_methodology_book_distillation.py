from __future__ import annotations

import json
from pathlib import Path

import yaml

from bestseller.services.distillation_source_preparer import DuplicateSourceTitleError
from bestseller.services.methodology_book_distillation import (
    candidates_to_methodology_cards,
    load_methodology_candidates,
    prepare_methodology_book,
    validate_methodology_book_package,
    write_methodology_cards_yaml,
)


def _section_body(label: str) -> str:
    return (
        f"{label}: 写作者先把故事方法拆成目标、阻碍、行动、结果, 再检查每一步是否"
        "能被章节合约验证。这个段落只用于测试解析与脱敏, 不代表任何真实书籍内容。"
    ) * 4


def test_prepare_methodology_book_writes_private_text_and_safe_repo_manifest(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    private_root = tmp_path / "private"
    source = tmp_path / "理论写作书.epub.txt"
    source.write_text(
        "第一章 方法总论\n"
        f"{_section_body('第一节')}\n\n"
        "第二章 场景设计\n"
        f"{_section_body('第二节')}\n",
        encoding="utf-8",
    )

    result = prepare_methodology_book(
        source,
        "source-9001",
        repo_root,
        private_root,
        language_hint="zh-CN",
    )

    assert not result.skipped
    repo_dir = repo_root / "data" / "methodology_books" / "source-9001"
    private_dir = private_root / "source-9001"
    assert (private_dir / "raw" / "source.normalized.txt").is_file()
    assert (private_dir / "llm_payloads" / "sec-0001.prompt.json").is_file()
    assert not (repo_dir / "raw").exists()
    assert validate_methodology_book_package(repo_dir) == ()

    manifest_text = (repo_dir / "source_manifest.json").read_text(encoding="utf-8")
    assert "理论写作书" not in manifest_text
    assert str(tmp_path) not in manifest_text

    manifest = json.loads(manifest_text)
    assert manifest["redaction_policy"]["store_raw_text_in_repo"] is False
    assert manifest["parse_profile"]["section_count"] == 2
    assert manifest["outputs"]["candidate_schema"].endswith("methodology_candidate.schema.json")

    jobs = (repo_dir / "llm_jobs" / "section_jobs.index.jsonl").read_text(encoding="utf-8")
    assert ".methodology_private/source-9001/llm_payloads/sec-0001.prompt.json" in jobs
    assert "理论写作书" not in jobs


def test_prepare_methodology_book_duplicate_title_can_skip_or_error(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    private_root = tmp_path / "private"
    body = "第一章 方法\n" + _section_body("同名") + "\n"
    source_one = tmp_path / "同名理论书.txt"
    source_two = tmp_path / "同名理论书.md"
    source_one.write_text(body, encoding="utf-8")
    source_two.write_text(body.replace("方法", "方法二"), encoding="utf-8")

    first = prepare_methodology_book(source_one, "source-9001", repo_root, private_root)
    second = prepare_methodology_book(source_two, "source-9002", repo_root, private_root)

    assert not first.skipped
    assert second.skipped
    assert second.duplicate_of == "source-9001"

    try:
        prepare_methodology_book(
            source_two,
            "source-9003",
            repo_root,
            private_root,
            dedupe_policy="error",
        )
    except DuplicateSourceTitleError:
        pass
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("expected duplicate title error")


def test_reviewed_candidates_promote_to_methodology_cards_yaml(tmp_path: Path) -> None:
    candidates_path = tmp_path / "methodology_candidates.review.jsonl"
    candidate_row = {
        "candidate_id": "snowflake.expanding_layers",
        "source_id": "source-9001",
        "section_id": "sec-0001",
        "title": "雪花式逐层扩展",
        "category": "outline",
        "scope": ["book", "chapter"],
        "stage": ["planning", "review"],
        "core_claim": "故事设计应从核心承诺逐层展开, 并保持章节可追溯。",
        "operating_steps": ["写一句话承诺", "扩成段落", "绑定角色与场景"],
        "anti_patterns": ["先堆场景再补主线"],
        "required_contract_fields": ["one_sentence_premise", "scene_list"],
        "framework_bindings": ["story_design_kernel", "chapter_outline_readiness_gate"],
        "gate_bindings": [{"gate": "outline_executability", "default_mode": "warn"}],
        "alignment_terms": ["snowflake expansion", "outline executability"],
        "conflicts_with": [],
        "confidence": 0.91,
    }
    candidates_path.write_text(
        json.dumps({"candidates": [candidate_row]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    candidates = load_methodology_candidates(candidates_path)
    deck = candidates_to_methodology_cards(candidates, id_prefix="writing_books")
    assert len(deck.cards) == 1
    card = deck.cards[0]
    assert card.id == "writing_books.source-9001.sec-0001.snowflake.expanding_layers"
    assert card.source_ids == ("source-9001.sec-0001",)
    assert card.gate_bindings[0].gate == "outline_executability"

    cards_path = tmp_path / "cards.yaml"
    write_methodology_cards_yaml(cards_path, deck)
    payload = yaml.safe_load(cards_path.read_text(encoding="utf-8"))
    assert payload["cards"][0]["core_claim"].startswith("故事设计应")


def test_reviewed_candidates_normalize_model_enum_drift(tmp_path: Path) -> None:
    candidates_path = tmp_path / "methodology_candidates.review.jsonl"
    candidate_row = {
        "candidate_id": "model.enum_drift",
        "source_id": "source-9001",
        "section_id": "sec-0002",
        "title": "模型枚举漂移归一化",
        "category": "unknown_category",
        "scope": ["invalid_scope"],
        "stage": ["invalid_stage"],
        "core_claim": "模型输出的枚举漂移不应阻断 review 物料生成。",
        "framework_bindings": ["methodology_compiler"],
        "gate_bindings": [{"gate": "review_only", "default_mode": "check"}],
        "confidence": 0.9,
    }
    candidates_path.write_text(
        json.dumps(candidate_row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    cards = candidates_to_methodology_cards(load_methodology_candidates(candidates_path))

    card = cards.cards[0]
    assert card.category == "scene_design"
    assert card.scope == ("scene",)
    assert card.stage == ("planning",)
    assert card.gate_bindings[0].default_mode == "advisory"
