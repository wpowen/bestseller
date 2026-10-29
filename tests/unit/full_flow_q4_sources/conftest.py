"""Local transport fixtures only; no native approval or paid result is asserted."""

from copy import deepcopy
import json
import socket
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.engine import Engine

from bestseller.services import chapter_writer_delivery as delivery
from bestseller.services import llm
from bestseller.services import source_bound_planning_content as protocol
from bestseller.services.prompt_source_policy import content_hash
from support.database_safety import load_test_database_settings

V3 = "approved-current-chapter-delivery.v3-reader-first"


def leaves(value, path=""):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            if key != "_meta":
                escaped = key.replace("~", "~0").replace("/", "~1")
                yield from leaves(child, path + "/" + escaped)
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from leaves(child, path + "/" + str(index))
    else:
        yield path


def rereview(case):
    """Rehash synthetic source to isolate census checks from stale-hash rejection."""
    envelope = case.receipt["reader_first_source"]
    raw = envelope["review"]
    raw["source_sha256"] = content_hash(envelope["source"])
    envelope["reader_receipt"] = protocol.parse_reader_first_review(
        raw,
        source=envelope["source"],
        binding=case.binding,
        checkpoint="outline",
    )
    assert envelope["reader_receipt"]["status"] == "passed"


def build_block(case):
    return delivery.delivery_block(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        chapter_number=case.chapter.chapter_number,
        artifact_id=case.artifact.id,
        approved=case.approved,
        receipt=case.receipt,
        reader_first=case.binding,
        language=case.project.language,
    )


def payload_of(block):
    return json.loads(block.text.split("\n", 2)[2])


def replace_payload(block, payload):
    # Keep framing, provenance and rendering intact except for the chosen fault.
    prefix = block.text.split("\n", 2)[:2]
    text = "\n".join(
        [
            *prefix,
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ),
        ]
    )
    return block.model_copy(update={"text": text})


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Q4 source consumer fixtures forbid DB/network/model execution")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "raw_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)


@pytest.fixture
def writer_settings():
    settings = load_test_database_settings().model_copy(deep=True)
    settings.llm.writer = settings.llm.writer.model_copy(
        update={
            "model": "openai/MiniMax-M3",
            "api_base": "https://api.minimaxi.com/v1",
            "max_tokens": 16384,
            "model_override": None,
            "stream": False,
            "n_candidates": 1,
            "thinking_type": "disabled",
            "reasoning_effort": None,
        }
    )
    return settings


@pytest.fixture
def full_case():
    def make(*, count=12, number=7, language="zh-CN"):
        binding = protocol.new_reader_first_binding(
            "Synthetic transport case: keep the promise, 09:17 and 1200.50 unchanged.",
        )
        project = SimpleNamespace(
            id=UUID(int=701),
            target_chapters=count,
            language=language,
            metadata_json={
                "reader_first_planning": deepcopy(binding),
                "llm_model_id": "fixture-configured-writer",
            },
        )
        chapter = SimpleNamespace(id=UUID(int=702), chapter_number=number)
        chapters = []
        for index in range(1, count + 1):
            row = {
                "chapter_number": index,
                "goal": f"Choice {index} changes the relationship.",
                "precise/fact~": "09:17; 1200.50; do not open before tomorrow.",
                "constraints": ["Do not sign again."],
                "scenes": [
                    {
                        "scene_number": 1,
                        "participants": ["Ming", "Lan"],
                        "entry_state": {"holder": "Lan", "amount": "1200.50"},
                        "action_sequence": ["Ming chooses to stay."],
                        "exit_state": {"choice": "The seal remains intact."},
                        "unknown_clause": "No extra witness or debt is authorized.",
                        "empty": [],
                        "nullable": None,
                    }
                ],
            }
            roles = dict.fromkeys(leaves(row), "continuity_reference")
            roles["/goal"] = roles["/scenes/0/action_sequence/0"] = "narrative_task"
            row["_meta"] = {
                "reader_first_planning": deepcopy(binding),
                "writer_delivery_schema": V3,
                "reader_first_field_roles": roles,
            }
            chapters.append(row)
        source = {
            "reader_first_planning": deepcopy(binding),
            "outline": {"chapters": chapters},
            "whole_book_payoff_index": "Reconciliation remains the ending promise.",
        }
        artifact = SimpleNamespace(id=UUID(int=703), content=source["outline"])
        raw = {
            "schema": "reader-first-review.v1",
            "checkpoint": "outline",
            "source_sha256": content_hash(source),
            "config_sha256": binding["config_sha256"],
            "dimensions": {
                name: {
                    "score": 0.8,
                    "reason": "Synthetic choice has an explicit consequence.",
                    "evidence": [
                        {"path": "/outline/chapters/0/goal", "quote": chapters[0]["goal"]}
                    ],
                }
                for name in protocol.READER_FIRST_DIMENSIONS
            },
            "issues": [],
            "options": ["Change the obstacle.", "Change the choice."],
        }
        receipt = {
            "chapter_numbers": list(range(1, count + 1)),
            "reader_first_source": {
                "schema": "reader-first-writer-source.v1",
                "binding": deepcopy(binding),
                "source_binding": {
                    "project_id": str(project.id),
                    "chapter_id": str(chapter.id),
                    "chapter_number": number,
                    "artifact_id": str(artifact.id),
                },
                "source": source,
                "chapter_pointer": f"/outline/chapters/{number - 1}",
                "review": raw,
                "reader_receipt": protocol.parse_reader_first_review(
                    raw,
                    source=source,
                    binding=binding,
                    checkpoint="outline",
                ),
            },
        }
        assert receipt["reader_first_source"]["reader_receipt"]["status"] == "passed"
        return SimpleNamespace(
            project=project,
            chapter=chapter,
            artifact=artifact,
            approved=chapters[number - 1],
            receipt=receipt,
            binding=binding,
        )

    return make
