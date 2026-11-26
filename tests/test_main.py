"""Unit & HTTP tests for app.main (FastAPI endpoints, LINE webhook, command routing)."""
from __future__ import annotations

import os
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from linebot.v3.exceptions import InvalidSignatureError

from app import config, main
from app.stock import data, fundamentals, institutional, tw_directory


@pytest.fixture(autouse=True)
def _reset_guardrails():
    main.rate_limiter.reset()
    main.response_cache.clear()
    yield
    main.rate_limiter.reset()
    main.response_cache.clear()


def _event(text: str = "", source_type: str = "user", user_id: str = "U123", postback_data: str = ""):
    return SimpleNamespace(
        reply_token="tok-1",
        source=SimpleNamespace(type=source_type, user_id=user_id),
        message=SimpleNamespace(text=text),
        postback=SimpleNamespace(data=postback_data),
    )


def _sample_df() -> pd.DataFrame:
    idx = pd.date_range("2026-11-18", periods=3, freq="D")
    return pd.DataFrame(
        {"Open": [100.0, 101.0, 102.0], "High": [102.0, 103.0, 104.0], "Low": [99.0, 100.0, 101.0], "Close": [101.0, 102.0, 103.0], "Volume": [1000.0, 2000.0, 3000.0]},
        index=idx,
    )


def test_http_health_charts_and_webhook(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHART_DIR", str(tmp_path))
    monkeypatch.setattr(main, "_base_url_from_request", "")
    client = TestClient(main.app)

    # Health check
    r = client.get("/")
    assert r.status_code == 200
    assert r.text == "StockV OK"

    # Chart endpoint: invalid name, missing file, valid file
    assert client.get("/charts/not-hex.png").status_code == 404
    valid_name = "a" * 32 + ".png"
    assert client.get(f"/charts/{valid_name}").status_code == 404
    (tmp_path / valid_name).write_bytes(b"\x89PNG\r\n\x1a\n")
    r_png = client.get(f"/charts/{valid_name}")
    assert r_png.status_code == 200
    assert r_png.headers["content-type"] == "image/png"

    # Webhook: valid signature sets _base_url_from_request and returns OK
    monkeypatch.setattr(main.handler, "handle", MagicMock())
    r_ok = client.post("/webhook", content=b"{}", headers={"X-Line-Signature": "sig", "host": "stockv.example.com"})
    assert r_ok.status_code == 200
    assert main._base_url_from_request == "https://stockv.example.com"

    # Webhook: invalid signature returns 400
    monkeypatch.setattr(main.handler, "handle", MagicMock(side_effect=InvalidSignatureError("bad")))
    r_bad = client.post("/webhook", content=b"{}", headers={"X-Line-Signature": "bad"})
    assert r_bad.status_code == 400


def test_cleanup_charts_and_start_loading(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHART_DIR", str(tmp_path))
    old_f = tmp_path / "old.png"
    new_f = tmp_path / "new.png"
    old_f.write_bytes(b"old")
    new_f.write_bytes(b"new")
    old_time = time.time() - config.CHART_TTL_SECONDS - 60
    os.utime(old_f, (old_time, old_time))

    main._cleanup_charts()
    assert not old_f.exists()
    assert new_f.exists()

    # Non-existent dir handled gracefully
    monkeypatch.setattr(config, "CHART_DIR", str(tmp_path / "missing"))
    main._cleanup_charts()

    # _start_loading: group chat skips, missing user_id skips, user calls API, error swallowed
    api_mock = MagicMock()
    monkeypatch.setattr(main, "ApiClient", MagicMock())
    monkeypatch.setattr(main, "MessagingApi", MagicMock(return_value=api_mock))

    main._start_loading(_event(source_type="group"))
    main._start_loading(_event(source_type="user", user_id=""))
    assert api_mock.show_loading_animation.call_count == 0

    main._start_loading(_event(source_type="user", user_id="U123"))
    assert api_mock.show_loading_animation.call_count == 1

    api_mock.show_loading_animation.side_effect = RuntimeError("line err")
    main._start_loading(_event(source_type="user", user_id="U123"))


def test_parse_command_empty_and_base_url(monkeypatch):
    assert main._parse_command("   ") is None
    monkeypatch.setattr(config, "BASE_URL", "https://prod.example.com")
    assert main._base_url() == "https://prod.example.com"


def test_handle_command_all_kinds(monkeypatch):
    tw_q = data.StockQuote("2330", "2330.TW", "台積電", True, "TWD", "stock")
    df = _sample_df()
    snap = data.QuickSnapshot(tw_q, 103.0, 102.0, 102.0, 104.0, 101.0, 3000.0, "2026/11/20")
    inst_rep = institutional.InstReport("2330", "台積電", [institutional.InstDay(date(2026, 11, 20), 1000, 200, 50, 1250)])
    div_rep = data.DividendReport(tw_q, 103.0, [data.AnnualDividend(2026, 4.0)], date(2026, 6, 1), 4.0, 4.0, 3.88)
    fund_rep = fundamentals.FundamentalReport(tw_q, valuation=fundamentals.Valuation(20.0, 5.0, 2.0))

    monkeypatch.setattr(main, "_new_chart_path", lambda: ("/tmp/x.png", "https://example.test/charts/x.png"))
    monkeypatch.setattr(main.charts, "intraday_chart", MagicMock())
    monkeypatch.setattr(main.charts, "candlestick_chart", MagicMock())
    monkeypatch.setattr(main.charts, "institutional_chart", MagicMock())
    monkeypatch.setattr(main.charts, "dividend_chart", MagicMock())

    # FX overview
    monkeypatch.setattr(data, "fx_overview", lambda: [snap])
    assert len(main._handle_command("P", "外匯")) == 1

    # Quick quote (#)
    monkeypatch.setattr(data, "resolve", lambda q: tw_q)
    monkeypatch.setattr(data, "quick_quote", lambda q: snap)
    monkeypatch.setattr(fundamentals, "lookup_valuation", lambda c: fundamentals.Valuation(20.0, 5.0, 2.0))
    assert len(main._handle_command("#", "2330")) == 1

    # T market
    monkeypatch.setattr(institutional, "fetch_market", lambda: inst_rep)
    assert len(main._handle_command("T", "台股")) == 1

    # T digit fallback & non-Taiwan hint
    monkeypatch.setattr(tw_directory, "lookup", lambda q: None)
    monkeypatch.setattr(institutional, "fetch", lambda t, market=None: inst_rep)
    assert len(main._handle_command("T", "2330")) == 1
    non_tw = main._handle_command("T", "AAPL")
    assert "T 指令支援台股" in non_tw[0].text
    with pytest.raises(data.StockNotFoundError):
        main._handle_command("T", "未知公司")

    # D, F, P, K, KW
    monkeypatch.setattr(data, "dividends", lambda q: div_rep)
    assert len(main._handle_command("D", "2330")) == 1

    monkeypatch.setattr(fundamentals, "fetch", lambda q: fund_rep)
    assert len(main._handle_command("F", "2330")) == 1

    monkeypatch.setattr(data, "intraday", lambda q: (df, 102.0))
    assert len(main._handle_command("P", "2330")) == 1

    monkeypatch.setattr(data, "kline_history", lambda q, timeframe="D": df)
    assert len(main._handle_command("K", "2330")) == 1
    assert len(main._handle_command("KW", "2330")) == 1


@pytest.mark.parametrize(
    ("exc", "expected_substr"),
    [
        (institutional.InstitutionalDataUnavailable("2330"), "法人資料"),
        (data.DividendDataUnavailable("2330"), "配息資料"),
        (fundamentals.FundamentalDataBusy("TWSE"), "資料源忙碌中"),
        (fundamentals.FundamentalDataUnavailable("2330"), "基本面資料"),
        (RuntimeError("boom"), "系統忙碌中"),
    ],
)
def test_execute_and_reply_error_handlers(monkeypatch, exc, expected_substr):
    replies = []
    monkeypatch.setattr(main, "_start_loading", MagicMock())
    monkeypatch.setattr(main, "_cleanup_charts", MagicMock())
    monkeypatch.setattr(main, "_handle_command", MagicMock(side_effect=exc))
    monkeypatch.setattr(main, "_reply", lambda tok, msgs: replies.append(msgs))

    main._execute_and_reply(_event(), "P", "2330", is_group=False)
    assert len(replies) == 1
    assert expected_substr in replies[0][0].text


def test_on_message_help_and_invalid_postback(monkeypatch):
    replies = []
    monkeypatch.setattr(main, "_reply", lambda tok, msgs: replies.append(msgs))

    main.on_message(_event(text="help"))
    assert len(replies) == 1
    assert "指令說明" in replies[0][0].alt_text

    # Invalid postback stays quiet
    main.on_postback(_event(postback_data="invalid=1"))
    assert len(replies) == 1
