from __future__ import annotations

from typing import Any

import httpx
import pytest

from bestseller.services.publishing.adapters import fanqie, qidian, qimao

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.request = httpx.Request("GET", "https://example.invalid/check")

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )


class _Client:
    def __init__(
        self,
        *,
        response: _Response | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, *args: object, **kwargs: object) -> _Response:
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    async def post(self, *args: object, **kwargs: object) -> _Response:
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@pytest.mark.parametrize(
    ("module", "adapter_cls"),
    [
        (fanqie, fanqie.FanqieAdapter),
        (qidian, qidian.QidianAdapter),
        (qimao, qimao.QimaoAdapter),
    ],
)
@pytest.mark.asyncio
async def test_authenticate_propagates_transient_network_errors(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    adapter_cls: type,
) -> None:
    request = httpx.Request("GET", "https://example.invalid/check")
    error = httpx.ConnectTimeout("temporary outage", request=request)
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(error=error),
    )
    adapter = adapter_cls(credentials={"cookie": "cookie", "book_id": "book"})

    with pytest.raises(httpx.ConnectTimeout):
        await adapter.authenticate()


@pytest.mark.parametrize(
    ("module", "adapter_cls"),
    [
        (fanqie, fanqie.FanqieAdapter),
        (qidian, qidian.QidianAdapter),
        (qimao, qimao.QimaoAdapter),
    ],
)
@pytest.mark.asyncio
async def test_authenticate_only_treats_credential_rejection_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    adapter_cls: type,
) -> None:
    adapter = adapter_cls(credentials={"cookie": "cookie", "book_id": "book"})
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(response=_Response(401)),
    )
    assert await adapter.authenticate() is False

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(response=_Response(503)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.authenticate()


@pytest.mark.parametrize(
    ("module", "adapter_cls", "invalid_payload", "valid_payload"),
    [
        (
            fanqie,
            fanqie.FanqieAdapter,
            {"code": 401, "message": "登录已失效"},
            {"code": 0, "data": {"book_id": "book"}},
        ),
        (
            qidian,
            qidian.QidianAdapter,
            {"code": 401, "msg": "please login again"},
            {"code": 0, "data": {"bookId": "book"}},
        ),
        (
            qimao,
            qimao.QimaoAdapter,
            {"success": False, "code": 401, "message": "session expired"},
            {"success": True, "data": {"bookId": "book"}},
        ),
    ],
)
@pytest.mark.asyncio
async def test_authenticate_http_200_requires_valid_business_payload(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    adapter_cls: type,
    invalid_payload: dict[str, Any],
    valid_payload: dict[str, Any],
) -> None:
    adapter = adapter_cls(credentials={"cookie": "cookie", "book_id": "book"})
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(response=_Response(200, invalid_payload)),
    )
    assert await adapter.authenticate() is False

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(response=_Response(200, valid_payload)),
    )
    assert await adapter.authenticate() is True


@pytest.mark.parametrize(
    ("module", "adapter", "payload"),
    [
        (
            qidian,
            qidian.QidianAdapter(credentials={"cookie": "c", "book_id": "b"}),
            {"data": {"reviewStatus": 1}},
        ),
        (
            qimao,
            qimao.QimaoAdapter(credentials={"cookie": "c", "book_id": "b"}),
            {"data": {"status": 1}},
        ),
    ],
)
@pytest.mark.asyncio
async def test_review_status_is_normalized_to_platform_protocol(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    adapter: object,
    payload: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(response=_Response(200, payload)),
    )

    status = await adapter.check_publish_status("remote-1")

    assert status.status == "published"


@pytest.mark.parametrize(
    ("module", "adapter", "payload"),
    [
        (
            fanqie,
            fanqie.FanqieAdapter(credentials={"cookie": "c", "book_id": "b"}),
            {"code": 401, "message": "登录已失效"},
        ),
        (
            qidian,
            qidian.QidianAdapter(credentials={"cookie": "c", "book_id": "b"}),
            {"code": 401, "msg": "please login again"},
        ),
        (
            qimao,
            qimao.QimaoAdapter(credentials={"cookie": "c", "book_id": "b"}),
            {"success": False, "code": 401, "message": "session expired"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_publish_http_200_business_auth_failure_is_classified_as_auth(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    adapter: object,
    payload: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(response=_Response(200, payload)),
    )

    result = await adapter.publish_chapter(
        "chapter body",
        module.ChapterPublishMeta(
            chapter_number=1,
            title="Chapter 1",
            word_count=100,
            project_title="Book",
            project_slug="book",
            idempotency_key="schedule:1",
        ),
    )

    assert result.success is False
    assert result.retryable is False
    assert result.error_kind == "auth"


@pytest.mark.parametrize(
    ("module", "adapter_cls"),
    [
        (fanqie, fanqie.FanqieAdapter),
        (qidian, qidian.QidianAdapter),
        (qimao, qimao.QimaoAdapter),
    ],
)
@pytest.mark.asyncio
async def test_publish_connection_timeout_is_uncertain_and_not_automatically_retryable(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    adapter_cls: type,
) -> None:
    request = httpx.Request("POST", "https://example.invalid/publish")
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(
            error=httpx.ConnectTimeout("connection outcome unknown", request=request)
        ),
    )
    adapter = adapter_cls(credentials={"cookie": "cookie", "book_id": "book"})

    result = await adapter.publish_chapter(
        "chapter body",
        module.ChapterPublishMeta(
            chapter_number=1,
            title="Chapter 1",
            word_count=100,
            project_title="Book",
            project_slug="book",
            idempotency_key="schedule:1",
        ),
    )

    assert result.success is False
    assert result.retryable is False
    assert result.error_kind == "delivery_unknown"


@pytest.mark.parametrize(
    ("module", "adapter_cls"),
    [
        (fanqie, fanqie.FanqieAdapter),
        (qidian, qidian.QidianAdapter),
        (qimao, qimao.QimaoAdapter),
    ],
)
@pytest.mark.asyncio
async def test_publish_http_503_is_known_safe_retry_response(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    adapter_cls: type,
) -> None:
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(response=_Response(503, {"message": "unavailable"})),
    )
    adapter = adapter_cls(credentials={"cookie": "cookie", "book_id": "book"})

    result = await adapter.publish_chapter(
        "chapter body",
        module.ChapterPublishMeta(
            chapter_number=1,
            title="Chapter 1",
            word_count=100,
            project_title="Book",
            project_slug="book",
            idempotency_key="schedule:1",
        ),
    )

    assert result.success is False
    assert result.retryable is True
    assert result.error_kind == "transient"
