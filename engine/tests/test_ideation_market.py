"""Market boundaries: public schema, bounded caching, fallback and prompt isolation."""

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from ideation import market


def today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()


def book(title="测试宗门：山门旧债", category="玄幻脑洞", channel="男频", **kw):
    return dict(
        title=title,
        hook="宗门招收新弟子，熟练度提升就返还资源。",
        category=category,
        channel=channel,
        tags=["穿越", "成长"],
        **kw,
    )


def cached(root, date, books, **kw):
    path = root / ".market" / (date + ".json")
    path.parent.mkdir(exist_ok=True)
    result = dict(date=date, status="fresh", source="fixture", books=books, features={}, **kw)
    path.write_text(json.dumps(result, ensure_ascii=False))
    return result


def test_success_is_cached_once_including_concurrent_callers(tmp_path, monkeypatch):
    calls = []

    def fetch():
        calls.append(1)
        time.sleep(0.02)
        return today(), [book()]

    monkeypatch.setattr(market, "_fetch", fetch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: market.snapshot(tmp_path), range(4)))
    assert len(calls) == 1
    assert all(r == results[0] for r in results)
    assert results[0]["status"] == "fresh"
    assert results[0]["source"] == market.URL
    assert (tmp_path / ".market" / (today() + ".json")).exists()


def test_failure_uses_latest_valid_cache_and_does_not_retry_today(tmp_path, monkeypatch):
    old = cached(tmp_path, "2000-01-01", [book()])
    cached(tmp_path, "2000-01-02", [])
    (tmp_path / ".market" / "2000-01-03.json").write_text("{bad")
    (tmp_path / ".market" / "2000-01-04.json").write_text("[]")
    cached(tmp_path, "2999-01-01", [book("未来数据不得回退")])
    with patch.object(market, "_fetch", side_effect=TimeoutError) as fetch:
        result = market.snapshot(tmp_path)
        assert result == dict(old, status="stale")
        assert market.snapshot(tmp_path) == result
        assert fetch.call_count == 1


def test_missing_is_nonblocking_and_daily_failure_is_cached(tmp_path):
    with patch.object(market, "_fetch", side_effect=OSError("offline")) as fetch:
        first = market.snapshot(tmp_path)
        assert first["status"] == "missing" and first["books"] == []
        assert market.snapshot(tmp_path) == first
        assert fetch.call_count == 1


def test_outdated_live_date_is_explicitly_stale(tmp_path):
    with patch.object(market, "_fetch", return_value=("2001-01-01", [book()])):
        result = market.snapshot(tmp_path)
    assert result["date"] == "2001-01-01" and result["status"] == "stale"
    assert result["source"] == market.URL


def test_unwritable_cache_is_nonblocking(tmp_path):
    (tmp_path / ".market").write_text("file blocks directory")
    with patch.object(market, "_fetch") as fetch:
        assert market.snapshot(tmp_path)["status"] == "missing"
        fetch.assert_not_called()


def test_readable_cache_survives_lock_write_failure(tmp_path):
    old = cached(tmp_path, "2000-01-01", [book()])
    (tmp_path / ".market" / ".lock").mkdir()
    with patch.object(market, "_fetch") as fetch:
        assert market.snapshot(tmp_path) == dict(old, status="stale")
        fetch.assert_not_called()


def test_malformed_current_cache_is_replaced_and_old_bad_rows_skipped(tmp_path):
    (tmp_path / ".market").mkdir()
    (tmp_path / ".market" / (today() + ".json")).write_text("null")
    cached(tmp_path, "2000-01-01", [{"title": "缺少字段"}])
    with patch.object(market, "_fetch", side_effect=ValueError):
        assert market.snapshot(tmp_path)["status"] == "missing"


def test_features_select_genre_channel_and_isolate_untrusted_text():
    books = [
        book(),
        book("女频玄幻测试", "玄幻脑洞", "女频"),
        book("测试婚恋", "职场婚恋", "女频"),
        book("不应入选", "历史古代"),
    ]
    books[0]["tags"].append("未经控制的实际书名")
    market_data = dict(books=books)
    result = market.features(market_data, "xuanhuan-power-fantasy", "male")
    assert result["anchors"] == books[:1]
    assert result["shell_counts"]["宗门"] == 1
    assert result["title_shapes"] == dict(colon=1, comma=0, question=0, short=0)
    assert "资源返还" in result["rules"]
    assert "未经控制的实际书名" not in result["tags"]
    assert "测试宗门" not in json.dumps({k: v for k, v in result.items() if k != "anchors"}, ensure_ascii=False)
    romance = market.features(market_data, "romance-tension-growth", "female")
    assert [b["title"] for b in romance["anchors"]] == ["测试婚恋"]
    assert len(result["shells"]) >= 12


def test_empty_genre_selection_does_not_borrow_unrelated_anchors():
    result = market.features({"books": [book()]}, "romance-tension-growth")
    assert result["anchors"] == [] and sum(result["title_shapes"].values()) == 0
    assert len(result["shells"]) >= 12 and result["tags"] == []


def test_all_snapshot_features_cover_all_books():
    result = market.features({"books": [book(), book("婚恋测试", "职场婚恋", "女频")]}, "")
    assert len(result["anchors"]) == 2
    assert sum(result["title_shapes"].values()) == 1


def api_payload():
    return dict(
        success=True,
        total=2,
        data=[
            dict(
                书名="测试二",
                简介="种田",
                分类="职场婚恋",
                平台="女频",
                标签="甜宠，成长",
                排名=2,
                榜单类型="新书榜",
                数据日期="2026-11-04",
            ),
            dict(
                书名="测试一",
                简介="宗门",
                分类="玄幻脑洞",
                平台="男频",
                标签=["穿越", "成长"],
                排名=1,
                榜单类型="新书榜",
                数据日期="2026-11-03",
            ),
        ],
    )


def mocked_fetch(monkeypatch, payload):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == 12_000_001
            return json.dumps(payload, ensure_ascii=False).encode()

    def open_url(url, timeout):
        assert url.startswith("https://www.fanqiehub.com/api/data?") and timeout == 12
        assert "board_type=" in url and "per_page=3000" in url
        return Response()

    monkeypatch.setattr(market.http, "build_opener", lambda *args: SimpleNamespace(open=open_url))
    return market._fetch()


def test_public_api_schema_normalization_and_order(monkeypatch):
    date, books = mocked_fetch(monkeypatch, api_payload())
    assert date == "2026-11-03"  # Never invent a newer source date.
    assert [b["title"] for b in books] == ["测试一", "测试二"]
    assert books[1]["tags"] == ["成长", "甜宠"]
    assert all(set(b) == {"title", "hook", "category", "channel", "tags"} for b in books)


@pytest.mark.parametrize(
    "payload",
    [
        dict(success=False, data=[]),
        dict(success=True, data=[]),
        dict(success=True, total=200, data=[]),
        dict(success=True, data={}),
    ],
)
def test_public_api_empty_and_partial_payload_fail_closed(monkeypatch, payload):
    with pytest.raises(ValueError):
        mocked_fetch(monkeypatch, payload)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/api/data",
        "http://www.fanqiehub.com/api/data",
        "https://www.fanqiehub.com.evil.example/",
        "https://me@fanqiehub.com/",
        "https://fanqiehub.com:8443/",
    ],
)
def test_redirect_host_boundary(url):
    with pytest.raises(ValueError, match="Unexpected public API redirect"):
        market._PublicRedirect().redirect_request(None, None, 301, "", {}, url)


def test_public_canonical_redirect_is_allowed():
    req = market.http.Request("https://fanqiehub.com/api/data")
    redirected = market._PublicRedirect().redirect_request(req, None, 301, "", {}, market.URL)
    assert redirected.full_url == market.URL
    assert not redirected.has_header("Authorization") and not redirected.has_header("Cookie")


def test_data_contracts_cover_every_web_genre():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/webui"))
    from library import GENRES

    mapping, pools = market._data("genre_map"), market._data("pools")
    assert set(GENRES) <= mapping.keys()
    for genre in GENRES:
        assert 1 <= len(mapping[genre]) <= 3
        assert len(set(pools["shells"][genre])) >= 12, genre
    variables = market._data("variables")
    assert set(variables) == {"职业", "地域民俗", "时代", "物件", "规则", "关系动态"}
    assert all(
        len(set(items)) >= 30 and all(isinstance(v, str) and len(v) > 1 for v in items) for items in variables.values()
    )
    assert len(set(pools["payoffs"])) >= 15 and len(set(pools["desires"])) >= 15
    names = market._data("names")
    assert "苏小满" in names["blacklist"] and isinstance(names["traditional"], str)
    assert "體" in names["traditional"] and "体" not in names["traditional"]
    assert all(
        len(pair) >= 2 and len(set(pair)) == len(pair) for combos in market._data("cliche").values() for pair in combos
    )


def test_simplified_magic_escape_story_is_not_flagged_as_traditional():
    from ideation.filters import validate

    candidate = dict(
        title="山门试炼",
        hook="手艺人寻找失踪伙伴",
        tags=["成长", "冒险", "守护"],
        premise="陈禾躲避隔壁的魔法师，发誓要寻回伙伴。",
        golden_finger_rule="无",
        opening="陈禾走进村口的旧药铺。",
        engine="依次追查村庄、城镇与王城的三条线索。",
    )
    assert validate(candidate, 30) == []
