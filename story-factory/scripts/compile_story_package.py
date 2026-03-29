#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def chapter_text_length(chapter: dict[str, Any]) -> int:
    total = 0
    for node in chapter.get("nodes", []):
        if "text" in node:
            total += len(node["text"].get("content", ""))
        if "dialogue" in node:
            total += len(node["dialogue"].get("content", ""))
        if "choice" in node:
            for choice in node["choice"].get("choices", []):
                total += len(choice.get("text", ""))
                total += len(choice.get("description", ""))
                for result_node in choice.get("result_nodes", []):
                    if "text" in result_node:
                        total += len(result_node["text"].get("content", ""))
                    if "dialogue" in result_node:
                        total += len(result_node["dialogue"].get("content", ""))
    return total


def chapter_choice_nodes(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    return [node["choice"] for node in chapter.get("nodes", []) if "choice" in node]


def validate_package(payload: dict[str, Any]) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []

    for key in ["book", "walkthrough", "chapters"]:
        if key not in payload:
            errors.append(f"missing top-level key: {key}")

    if errors:
        return errors, warnings, {}

    book = payload["book"]
    walkthrough = payload["walkthrough"]
    chapters = payload["chapters"]

    book_id = book["id"]
    if walkthrough.get("book_id") != book_id:
        errors.append("walkthrough.book_id must match book.id")

    if book.get("total_chapters") != len(chapters):
        errors.append("book.total_chapters must equal chapter count")

    chapter_ids = [chapter["id"] for chapter in chapters]
    if len(chapter_ids) != len(set(chapter_ids)):
        errors.append("chapter ids must be unique")

    chapter_numbers = [chapter["number"] for chapter in chapters]
    if sorted(chapter_numbers) != list(range(1, len(chapters) + 1)):
        errors.append("chapter numbers must be contiguous starting at 1")

    guides = walkthrough.get("chapter_guides", [])
    guide_ids = [guide["chapter_id"] for guide in guides]
    if set(guide_ids) != set(chapter_ids):
        errors.append("walkthrough.chapter_guides must cover every chapter exactly once")

    chapter_reports: list[dict[str, Any]] = []
    total_choice_nodes = 0
    total_options = 0

    guide_by_chapter = {guide["chapter_id"]: guide for guide in guides}

    for chapter in chapters:
        if chapter.get("book_id") != book_id:
            errors.append(f"{chapter['id']}: chapter.book_id must match book.id")

        choice_nodes = chapter_choice_nodes(chapter)
        total_choice_nodes += len(choice_nodes)
        char_count = chapter_text_length(chapter)
        guide = guide_by_chapter.get(chapter["id"], {})

        if not 1200 <= char_count <= 3200:
            warnings.append(f"{chapter['id']}: text length {char_count} is outside recommended 1200-3200 range")

        recommended_interactions = guide.get("interaction_count")
        if recommended_interactions is not None and recommended_interactions != len(choice_nodes):
            warnings.append(
                f"{chapter['id']}: walkthrough interaction_count={recommended_interactions} but found {len(choice_nodes)} choice nodes"
            )

        for choice_node in choice_nodes:
            choices = choice_node.get("choices", [])
            total_options += len(choices)

            if not 2 <= len(choices) <= 4:
                errors.append(f"{chapter['id']}::{choice_node['id']}: each choice node must have 2-4 options")

            for choice in choices:
                if not choice.get("result_nodes") and not choice.get("result_node_ids"):
                    errors.append(f"{chapter['id']}::{choice['id']}: choice must contain result_nodes or result_node_ids")

                missing_preview_fields = [
                    field for field in ["visible_cost", "visible_reward", "risk_hint", "process_label"]
                    if not choice.get(field)
                ]
                if missing_preview_fields:
                    warnings.append(
                        f"{chapter['id']}::{choice['id']}: missing preview fields {', '.join(missing_preview_fields)}"
                    )

        chapter_reports.append(
            {
                "chapter_id": chapter["id"],
                "title": chapter["title"],
                "char_count": char_count,
                "choice_nodes": len(choice_nodes),
                "guide_interactions": recommended_interactions,
                "estimated_minutes": guide.get("estimated_minutes"),
                "visible_routes": len(guide.get("visible_routes", [])),
            }
        )

    qa_report = {
        "book_id": book_id,
        "chapter_count": len(chapters),
        "choice_nodes": total_choice_nodes,
        "average_choice_nodes_per_chapter": round(total_choice_nodes / max(len(chapters), 1), 2),
        "average_options_per_choice_node": round(total_options / max(total_choice_nodes, 1), 2),
        "chapters": chapter_reports,
        "warnings": warnings,
        "errors": errors,
    }

    return errors, warnings, qa_report


def compile_package(package_path: Path, resources_dir: Path, output_dir: Path) -> None:
    payload = load_json(package_path)
    errors, warnings, qa_report = validate_package(payload)

    if errors:
        raise SystemExit("validation failed:\n- " + "\n- ".join(errors))

    book = payload["book"]
    chapters = payload["chapters"]
    walkthrough = payload["walkthrough"]
    book_id = book["id"]

    dump_json(resources_dir / f"book_{book_id}.json", book)
    dump_json(resources_dir / f"chapters_{book_id}.json", chapters)
    dump_json(resources_dir / f"walkthrough_{book_id}.json", walkthrough)
    dump_json(output_dir / "qa_report.json", qa_report)

    print(f"compiled {book_id}")
    for warning in warnings:
        print(f"warning: {warning}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and compile a LifeScript story package.")
    parser.add_argument("package_path", type=Path)
    parser.add_argument("--resources-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    compile_package(args.package_path, args.resources_dir, args.output_dir)


if __name__ == "__main__":
    main()
