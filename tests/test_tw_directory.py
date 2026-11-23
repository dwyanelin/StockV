"""Unit tests for app.stock.tw_directory (fetching, caching, indexing, and lookup)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.stock import tw_directory


@pytest.fixture(autouse=True)
def _reset_cache():
    tw_directory.clear_cache()
    yield
    tw_directory.clear_cache()


def test_fetch_source_parses_valid_rows_and_skips_invalid(monkeypatch):
    resp = MagicMock()
    resp.json.return_value = [
        {"Code": " 2330 ", "Name": " 台積電 "},
        {"Code": "", "Name": "MissingCode"},
        {"Code": "9999", "Name": ""},
        "not-a-dict",
    ]
    monkeypatch.setattr(tw_directory.requests, "get", MagicMock(return_value=resp))

    stocks = tw_directory._fetch_source("TWSE", ".TW", "https://example.test", "Code", "Name")
    assert stocks == [tw_directory.TwStock("2330", "台積電", ".TW", "TWSE")]


def test_fetch_source_handles_non_list_and_request_error(monkeypatch):
    resp = MagicMock()
    resp.json.return_value = {"error": "unexpected"}
    monkeypatch.setattr(tw_directory.requests, "get", MagicMock(return_value=resp))
    assert tw_directory._fetch_source("TWSE", ".TW", "https://example.test", "Code", "Name") == []

    monkeypatch.setattr(tw_directory.requests, "get", MagicMock(side_effect=RuntimeError("boom")))
    assert tw_directory._fetch_source("TWSE", ".TW", "https://example.test", "Code", "Name") == []


def test_build_index_skips_ambiguous_or_colliding_aliases():
    stocks = [
        tw_directory.TwStock("1001", "甲乙", ".TW", "TWSE"),
        tw_directory.TwStock("1002", "甲乙-KY", ".TW", "TWSE"),  # collides with exact name 甲乙
        tw_directory.TwStock("2001", "丙丁-KY", ".TW", "TWSE"),
        tw_directory.TwStock("2002", "丙丁*", ".TW", "TWSE"),    # ambiguous stripped alias 丙丁
        tw_directory.TwStock("3661", "世芯-KY", ".TW", "TWSE"),  # unambiguous alias 世芯
    ]
    idx = tw_directory._build_index(stocks)
    assert "甲乙" not in idx.by_alias
    assert "丙丁" not in idx.by_alias
    assert idx.by_alias["世芯"].code == "3661"


def test_get_index_fetches_caches_and_falls_back_on_empty(monkeypatch):
    calls = []

    def fake_fetch(market, suffix, url, code_key, name_key):
        calls.append(market)
        if market == "TWSE":
            return [tw_directory.TwStock("2330", "台積電", ".TW", "TWSE")]
        return []

    monkeypatch.setattr(tw_directory, "_fetch_source", fake_fetch)
    idx1 = tw_directory._get_index()
    assert "2330" in idx1.by_code
    assert len(calls) == 3

    # Cache hit: should not refetch
    idx2 = tw_directory._get_index()
    assert idx2 is idx1
    assert len(calls) == 3

    # Expire cache & simulate upstream returning empty -> falls back to stale cache
    tw_directory._cache.loaded_at = -999999.0
    monkeypatch.setattr(tw_directory, "_fetch_source", lambda *a: [])
    idx3 = tw_directory._get_index()
    assert idx3 is idx1

    # No cache + empty upstream -> returns empty index
    tw_directory.clear_cache()
    idx4 = tw_directory._get_index()
    assert idx4.by_code == {}


def test_lookup_empty_queries_return_none():
    assert tw_directory.lookup_code("   ") is None
    assert tw_directory.lookup_name("   ") is None
    assert tw_directory.lookup("") is None
