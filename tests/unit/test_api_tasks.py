from __future__ import annotations

import json

import pytest

from bestseller.api.routers.tasks import get_task_status

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_task_status_treats_completed_attention_payload_as_incomplete() -> None:
    class _FakeRedis:
        async def lrange(self, *_args: object) -> list[str]:
            return [
                json.dumps(
                    {
                        "event_type": "completed",
                        "message": "completed",
                        "data": {
                            "requires_human_review": True,
                            "final_verdict": "attention",
                        },
                    }
                )
            ]

    response = await get_task_status("task-a", _FakeRedis(), object())

    assert response.status == "incomplete"
