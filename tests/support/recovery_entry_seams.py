"""Explicit offline collaborators for older control-plane/pipeline doubles.

Native ownership and review admission run unmocked in their dedicated unit/PG
tests. These fixtures preserve the scope of tests with fake sessions/providers;
they do not grant permission in application code or apply suite-wide.
"""
from contextlib import asynccontextmanager

import pytest


@pytest.fixture(autouse=True)
def offline_review_resume(monkeypatch):
    from bestseller.services import chapter_review_reconciliation

    async def unchanged(session, settings, *, project_id, chapter_id=None, requested_by="system"):
        return {"schema": "native-review-resume.8y24-r04.v1", "status": "unchanged",
                "project_id": str(project_id), "chapter_receipts": [], "provider_calls": 0,
                "new_attempts": 0, "credit_released": False, "dispatch_authorized": False,
                "adoption_authorized": False}

    monkeypatch.setattr(chapter_review_reconciliation, "prepare_native_review_resume", unchanged)


@pytest.fixture(autouse=True)
def offline_web_owner(monkeypatch, offline_review_resume):
    from bestseller.services import web_pipeline_owner

    @asynccontextmanager
    async def owned(settings, slug):
        assert isinstance(slug, str) and slug
        yield

    monkeypatch.setattr(web_pipeline_owner, "web_pipeline_owner", owned)


@pytest.fixture(autouse=True)
def offline_worker_owner(monkeypatch, offline_review_resume):
    from bestseller.worker import tasks

    async def acquire(*args):
        return "acquired"

    @asynccontextmanager
    async def owned(redis, slug, token, state):
        assert state == "acquired"
        yield

    monkeypatch.setattr(tasks, "_acquire_pipeline_live", acquire)
    monkeypatch.setattr(tasks, "_pipeline_live_guard", owned)
