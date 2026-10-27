from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from bestseller.api.routers.projects import create_project
from bestseller.api.schemas.projects import ProjectCreateRequest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_public_project_endpoint_cannot_bypass_long_form_conception() -> None:
    body = ProjectCreateRequest(
        slug="bypass-attempt",
        title="未经过构思的新书",
        genre="异世界",
        target_word_count=120_000,
        target_chapters=30,
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_project(
            body=body,
            session=SimpleNamespace(),
            settings=SimpleNamespace(),
            _key="test-key",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "LONG_FORM_CONCEPTION_REQUIRED"
