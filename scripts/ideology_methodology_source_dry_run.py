"""Read an authorized local project/artifact capture; emit only a bound dry-run receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from bestseller.services.ideology_methodology import build_ideology_methodology_supersession


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--expected-kernel-sha256", required=True)
    parser.add_argument("--expected-creation-sha256", required=True)
    args = parser.parse_args()
    try:
        capture = json.loads(args.capture.read_text(encoding="utf-8"))
        project = capture["project"]
        artifact = capture["artifact"]
        receipt = build_ideology_methodology_supersession(
            SimpleNamespace(id=project["id"], metadata_json=project["metadata_json"]),
            SimpleNamespace(
                id=artifact["id"],
                project_id=artifact["project_id"],
                artifact_type=artifact["artifact_type"],
                status=artifact["status"],
                content=artifact["content"],
            ),
            expected_source_sha256=args.expected_kernel_sha256,
            expected_creation_sha256=args.expected_creation_sha256,
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
