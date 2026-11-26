"""Unit tests for Chinese stock name command parsing and Taiwan stock directory lookup."""
from __future__ import annotations

import datetime
import math
import sys
import types
import unittest
from unittest import mock

if "requests" not in sys.modules:
    try:
        import requests  # noqa: F401
    except ModuleNotFoundError:
        _req = types.ModuleType("requests")
        _req.get = mock.MagicMock()
        sys.modules["requests"] = _req

from app.stock import tw_directory


def _sample_stocks() -> list[tw_directory.TwStock]:
    return [
        tw_directory.TwStock("2330", "台積電", ".TW", "TWSE"),
        tw_directory.TwStock("3406", "玉晶光", ".TW", "TWSE"),
        tw_directory.TwStock("7822", "倍利科", ".TW", "TWSE"),
        tw_directory.TwStock("00631L", "元大台灣50正2", ".TW", "TWSE"),
        tw_directory.TwStock("3661", "世芯-KY", ".TW", "TWSE"),
        tw_directory.TwStock("6919", "康霈*", ".TW", "TWSE"),
        tw_directory.TwStock("6781", "AES-KY", ".TW", "TWSE"),
        tw_directory.TwStock("8069", "元太", ".TWO", "TPEX"),
        tw_directory.TwStock("1260", "富味鄉", ".TWO", "ESB"),
    ]


class TwDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        tw_directory.clear_cache()
        idx = tw_directory._build_index(_sample_stocks())
        tw_directory._cache = idx

    def tearDown(self) -> None:
        tw_directory.clear_cache()

    def test_lookup_by_chinese_name(self) -> None:
        self.assertEqual(tw_directory.lookup("玉晶光").code, "3406")
        self.assertEqual(tw_directory.lookup("玉晶光").yahoo_symbol, "3406.TW")
        self.assertEqual(tw_directory.lookup("倍利科").code, "7822")
        self.assertEqual(tw_directory.lookup("倍利科").yahoo_symbol, "7822.TW")
        self.assertEqual(tw_directory.lookup("元太").yahoo_symbol, "8069.TWO")
        self.assertEqual(tw_directory.lookup("富味鄉").yahoo_symbol, "1260.TWO")

    def test_lookup_traditional_variant_and_aliases(self) -> None:
        # 臺 -> 台 normalization
        self.assertEqual(tw_directory.lookup("臺積電").code, "2330")
        # -KY and * suffix stripped aliases
        self.assertEqual(tw_directory.lookup("世芯").code, "3661")
        self.assertEqual(tw_directory.lookup("世芯-KY").code, "3661")
        self.assertEqual(tw_directory.lookup("世芯－ky").code, "3661")
        self.assertEqual(tw_directory.lookup("康霈").code, "6919")
        self.assertEqual(tw_directory.lookup("康霈*").code, "6919")

    def test_lookup_alphanumeric_codes_and_ascii_tw_names(self) -> None:
        self.assertEqual(tw_directory.lookup("00631L").yahoo_symbol, "00631L.TW")
        self.assertEqual(tw_directory.lookup("00631l").code, "00631L")
        self.assertEqual(tw_directory.lookup("AES-KY").code, "6781")
        self.assertEqual(tw_directory.lookup("aes").code, "6781")

    def test_lookup_unknown_returns_none(self) -> None:
        self.assertIsNone(tw_directory.lookup("不存在的公司"))
        self.assertIsNone(tw_directory.lookup("AAPL"))


class CommandParsingAndHandlingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Allow importing app.main even in minimal environments without optional deps.
        cls._injected: list[str] = []
        try:
            import app.main as main_mod
        except ModuleNotFoundError:
            cls._stub_optional_deps()
            import app.main as main_mod
        cls.main = main_mod

    @classmethod
    def _stub_optional_deps(cls) -> None:
        def make_mod(name: str, attrs: dict | None = None):
            m = types.ModuleType(name)
            for k, v in (attrs or {}).items():
                setattr(m, k, v)
            sys.modules[name] = m
            cls._injected.append(name)
            return m

        class _DummyApp:
            def __init__(self, *a, **kw):
                pass

            def post(self, *a, **kw):
                return lambda fn: fn

            def get(self, *a, **kw):
                return lambda fn: fn

        class _DummyHandler:
            def __init__(self, *a, **kw):
                pass

            def add(self, *a, **kw):
                return lambda fn: fn

        class _DummyMsg:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        make_mod("fastapi", {"FastAPI": _DummyApp, "HTTPException": Exception, "Request": object})
        make_mod("fastapi.responses", {"FileResponse": object, "PlainTextResponse": object})
        make_mod("linebot", {})
        make_mod("linebot.v3", {"WebhookHandler": _DummyHandler})
        make_mod("linebot.v3.exceptions", {"InvalidSignatureError": Exception})
        make_mod(
            "linebot.v3.messaging",
            {
                "ApiClient": mock.MagicMock,
                "Configuration": _DummyMsg,
                "ImageMessage": _DummyMsg,
                "MessagingApi": mock.MagicMock,
                "ReplyMessageRequest": _DummyMsg,
                "TextMessage": _DummyMsg,
            },
        )
        make_mod("linebot.v3.webhooks", {"MessageEvent": object, "TextMessageContent": object})
        make_mod(
            "pandas",
            {
                "DataFrame": object,
                "isna": lambda v: v is None or (isinstance(v, float) and math.isnan(v)),
            },
        )
        make_mod("yfinance", {"Ticker": mock.MagicMock})
        make_mod("matplotlib", {"use": lambda *a, **kw: None})
        make_mod("matplotlib.dates", {})
        make_mod("matplotlib.font_manager", {"fontManager": types.SimpleNamespace(ttflist=[])})
        make_mod("matplotlib.pyplot", {"rcParams": {"font.sans-serif": []}})
        make_mod("mplfinance", {})

    @classmethod
    def tearDownClass(cls) -> None:
        for name in cls._injected:
            sys.modules.pop(name, None)

    def setUp(self) -> None:
        tw_directory.clear_cache()
        tw_directory._cache = tw_directory._build_index(_sample_stocks())
        self.main.rate_limiter.reset()
        self.main.response_cache.clear()

    def tearDown(self) -> None:
        tw_directory.clear_cache()

    def test_parse_command_chinese_and_ascii(self) -> None:
        parse = self.main._parse_command
        # Reported failing cases
        self.assertEqual(parse("P玉晶光"), ("P", "玉晶光"))
        self.assertEqual(parse("K玉晶光"), ("K", "玉晶光"))
        self.assertEqual(parse("P倍利科"), ("P", "倍利科"))
        # Case-insensitive & optional space for CJK / digits
        self.assertEqual(parse("p玉晶光"), ("P", "玉晶光"))
        self.assertEqual(parse("P 玉晶光"), ("P", "玉晶光"))
        self.assertEqual(parse("T台積電"), ("T", "台積電"))
        self.assertEqual(parse("P2330"), ("P", "2330"))
        self.assertEqual(parse("P 2330"), ("P", "2330"))
        self.assertEqual(parse("P00631L"), ("P", "00631L"))
        self.assertEqual(parse("PAAPL"), ("P", "AAPL"))
        self.assertEqual(parse("PAES-KY"), ("P", "AES-KY"))
        self.assertEqual(parse("PFT潔淨能源"), ("P", "FT潔淨能源"))
        # Non-commands should stay ignored
        self.assertIsNone(parse("T shirt"))
        self.assertIsNone(parse("Please help"))
        self.assertIsNone(parse("玉晶光"))
        self.assertIsNone(parse("P"))

    def test_group_chat_stays_quiet_on_unknown_chinese_text(self) -> None:
        event = types.SimpleNamespace(
            message=types.SimpleNamespace(text="P圖好好笑"),
            source=types.SimpleNamespace(type="group"),
            reply_token="tok",
        )
        with mock.patch.object(self.main, "ApiClient") as api_cls:
            self.main.on_message(event)
            api_cls.assert_not_called()

    def test_direct_chat_replies_not_found_on_unknown_chinese_text(self) -> None:
        event = types.SimpleNamespace(
            message=types.SimpleNamespace(text="P不存在的股"),
            source=types.SimpleNamespace(type="user"),
            reply_token="tok",
        )
        with mock.patch.object(self.main, "ApiClient") as api_cls, mock.patch.object(
            self.main, "MessagingApi"
        ) as msg_api:
            api_cls.return_value.__enter__.return_value = object()
            self.main.on_message(event)
            msg_api.return_value.reply_message.assert_called_once()
            req = msg_api.return_value.reply_message.call_args[0][0]
            self.assertIn("找不到「不存在的股」", req.messages[0].text)

    def test_t_command_resolves_chinese_name(self) -> None:
        fake_report = types.SimpleNamespace(
            ticker="3406",
            name="玉晶光",
            days=[types.SimpleNamespace(
                day=datetime.date(2026, 11, 20),
                foreign=100000, trust=20000, dealer=-5000, total=115000,
            )],
            foreign_holding_pct=12.34,
            unit="張",
            scale=1000.0,
            market="TWSE",
        )
        with mock.patch.object(self.main.institutional, "fetch", return_value=fake_report) as fetch_mock, \
             mock.patch.object(self.main.charts, "institutional_chart"), \
             mock.patch.object(self.main, "_base_url", return_value="https://example.com"):
            msgs = self.main._handle_command("T", "玉晶光")
            fetch_mock.assert_called_once_with("3406", market="TWSE")
            self.assertEqual(len(msgs), 1)
            self.assertIn("玉晶光 (3406)", msgs[0].alt_text)

    def test_v2_command_parsing(self) -> None:
        parse = self.main._parse_command
        self.assertEqual(parse("#2330"), ("#", "2330"))
        self.assertEqual(parse("#台積電"), ("#", "台積電"))
        self.assertEqual(parse("# BTC"), ("#", "BTC"))
        self.assertEqual(parse("KW2330"), ("KW", "2330"))
        self.assertEqual(parse("kw台積電"), ("KW", "台積電"))
        self.assertEqual(parse("KMAAPL"), ("KM", "AAPL"))
        self.assertEqual(parse("KM AAPL"), ("KM", "AAPL"))
        self.assertEqual(parse("D2330"), ("D", "2330"))
        self.assertEqual(parse("D台積電"), ("D", "台積電"))
        self.assertEqual(parse("F2330"), ("F", "2330"))
        self.assertEqual(parse("F玉晶光"), ("F", "玉晶光"))
        self.assertEqual(parse("T台股"), ("T", "台股"))
        self.assertEqual(parse("P外匯"), ("P", "外匯"))
        self.assertEqual(parse("PUSD/TWD"), ("P", "USD/TWD"))
        self.assertEqual(parse("PBTC"), ("P", "BTC"))

    def test_cross_asset_aliases(self) -> None:
        from app.stock import aliases
        self.assertEqual(aliases.lookup("台股").yahoo_symbol, "^TWII")
        self.assertEqual(aliases.lookup("比特幣").yahoo_symbol, "BTC-USD")
        self.assertEqual(aliases.lookup("USD/TWD").yahoo_symbol, "TWD=X")
        self.assertEqual(aliases.lookup("USD/NTD").yahoo_symbol, "TWD=X")
        self.assertEqual(aliases.lookup("AUD/TWD").yahoo_symbol, "AUDTWD=X")
        self.assertTrue(aliases.is_fx_overview("外匯"))
        self.assertTrue(aliases.is_taiwan_market("台股"))

    def test_parse_postback(self) -> None:
        self.assertEqual(self.main._parse_postback("cmd=K&q=7822"), ("K", "7822"))
        self.assertEqual(self.main._parse_postback("cmd=KW&q=7822"), ("KW", "7822"))
        self.assertEqual(self.main._parse_postback("cmd=D&q=2330"), ("D", "2330"))
        self.assertEqual(self.main._parse_postback("cmd=F&q=2330"), ("F", "2330"))
        self.assertEqual(self.main._parse_postback("cmd=%23&q=2330"), ("#", "2330"))
        self.assertEqual(self.main._parse_postback("cmd=t&q=2330"), ("T", "2330"))
        self.assertIsNone(self.main._parse_postback("cmd=X&q=2330"))
        self.assertIsNone(self.main._parse_postback(""))

    def test_postback_triggers_command_and_replies(self) -> None:
        event = types.SimpleNamespace(
            postback=types.SimpleNamespace(data="cmd=KW&q=7822"),
            source=types.SimpleNamespace(type="user", user_id="U123"),
            reply_token="tok",
        )
        with mock.patch.object(self.main, "_handle_command", return_value=["CARD"]) as handle_mock, \
             mock.patch.object(self.main, "_start_loading") as load_mock, \
             mock.patch.object(self.main, "_reply") as reply_mock:
            self.main.on_postback(event)
            load_mock.assert_called_once_with(event)
            handle_mock.assert_called_once_with("KW", "7822")
            reply_mock.assert_called_once_with("tok", ["CARD"])

    def test_direct_chat_bare_stock_name_triggers_quick_quote(self) -> None:
        event = types.SimpleNamespace(
            message=types.SimpleNamespace(text="玉晶光"),
            source=types.SimpleNamespace(type="user", user_id="U123"),
            reply_token="tok",
        )
        with mock.patch.object(self.main, "_execute_and_reply") as exec_mock:
            self.main.on_message(event)
            exec_mock.assert_called_once_with(event, "#", "玉晶光", False)

    def test_group_chat_bare_stock_name_stays_quiet(self) -> None:
        event = types.SimpleNamespace(
            message=types.SimpleNamespace(text="玉晶光"),
            source=types.SimpleNamespace(type="group"),
            reply_token="tok",
        )
        with mock.patch.object(self.main, "_execute_and_reply") as exec_mock:
            self.main.on_message(event)
            exec_mock.assert_not_called()


class _FakeSeries:
    def __init__(self, values: list):
        self._values = values

    @property
    def iloc(self):
        return self._values


class _FakeLoc:
    def __init__(self, df: _FakeDF):
        self._df = df

    def __getitem__(self, key):
        idx, col = key
        pos = self._df.index.index(idx)
        return self._df._data[col][pos]

    def __setitem__(self, key, val):
        idx, col = key
        pos = self._df.index.index(idx)
        self._df._data[col][pos] = val


class _FakeDF:
    """Minimal pandas.DataFrame stand-in for testing daily_history without pandas."""

    def __init__(self, index: list[datetime.datetime], data: dict[str, list]):
        self.index = list(index)
        self._data = {k: list(v) for k, v in data.items()}
        self.columns = list(data.keys())

    @property
    def empty(self) -> bool:
        return len(self.index) == 0

    @property
    def loc(self) -> _FakeLoc:
        return _FakeLoc(self)

    def __getitem__(self, col: str) -> _FakeSeries:
        return _FakeSeries(self._data[col])

    def copy(self) -> _FakeDF:
        return _FakeDF(self.index, self._data)

    def dropna(self, subset: list[str]) -> _FakeDF:
        keep = [
            i for i in range(len(self.index))
            if all(
                self._data[c][i] is not None
                and not (isinstance(self._data[c][i], float) and math.isnan(self._data[c][i]))
                for c in subset
            )
        ]
        return _FakeDF(
            [self.index[i] for i in keep],
            {c: [vals[i] for i in keep] for c, vals in self._data.items()},
        )


class DailyHistoryBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Ensure app.stock.data can be imported even without pandas/yfinance installed.
        cls._injected: list[str] = []
        if "pandas" not in sys.modules:
            try:
                import pandas  # noqa: F401
            except ModuleNotFoundError:
                m = types.ModuleType("pandas")
                m.DataFrame = object
                m.isna = lambda v: v is None or (isinstance(v, float) and math.isnan(v))
                sys.modules["pandas"] = m
                cls._injected.append("pandas")
        if "yfinance" not in sys.modules:
            try:
                import yfinance  # noqa: F401
            except ModuleNotFoundError:
                m = types.ModuleType("yfinance")
                m.Ticker = mock.MagicMock
                sys.modules["yfinance"] = m
                cls._injected.append("yfinance")
        from app.stock import data as data_mod
        cls.data = data_mod

    @classmethod
    def tearDownClass(cls) -> None:
        for name in cls._injected:
            sys.modules.pop(name, None)

    def test_backfills_nan_close_from_1d_bar(self) -> None:
        quote = self.data.StockQuote("7822", "7822.TW", "倍利科", True, "TWD")
        d1 = datetime.datetime(2026, 11, 20, 9, 0)
        d2 = datetime.datetime(2026, 11, 23, 9, 0)
        df_6mo = _FakeDF(
            [d1, d2],
            {
                "Open": [480.0, 469.5],
                "High": [494.0, 495.0],
                "Low": [472.5, 447.0],
                "Close": [472.5, float("nan")],
                "Volume": [227040, 277786],
            },
        )
        df_1d = _FakeDF(
            [datetime.datetime(2026, 11, 23, 13, 30)],
            {
                "Open": [469.5],
                "High": [495.0],
                "Low": [447.0],
                "Close": [493.0],
                "Volume": [277786],
            },
        )
        fake_tk = mock.MagicMock()
        fake_tk.history.side_effect = [df_6mo, df_1d]

        with mock.patch.object(self.data.yf, "Ticker", return_value=fake_tk):
            out = self.data.daily_history(quote, months=6)

        self.assertEqual(len(out.index), 2)
        self.assertEqual(out["Close"].iloc[-1], 493.0)

    def test_backfills_nan_close_from_fast_info_when_1d_empty(self) -> None:
        quote = self.data.StockQuote("7822", "7822.TW", "倍利科", True, "TWD")
        d1 = datetime.datetime(2026, 11, 20, 9, 0)
        d2 = datetime.datetime(2026, 11, 23, 9, 0)
        df_6mo = _FakeDF(
            [d1, d2],
            {
                "Open": [480.0, 469.5],
                "High": [494.0, 490.0],
                "Low": [472.5, 447.0],
                "Close": [472.5, float("nan")],
                "Volume": [227040, 277786],
            },
        )
        fake_tk = mock.MagicMock()
        fake_tk.history.side_effect = [df_6mo, _FakeDF([], {})]
        fake_tk.fast_info = types.SimpleNamespace(last_price=493.0)

        with mock.patch.object(self.data.yf, "Ticker", return_value=fake_tk):
            out = self.data.daily_history(quote, months=6)

        self.assertEqual(len(out.index), 2)
        self.assertEqual(out["Close"].iloc[-1], 493.0)
        self.assertEqual(out["High"].iloc[-1], 493.0)

    def test_drops_unrecoverable_nan_row(self) -> None:
        quote = self.data.StockQuote("7822", "7822.TW", "倍利科", True, "TWD")
        d1 = datetime.datetime(2026, 11, 20, 9, 0)
        d2 = datetime.datetime(2026, 11, 23, 9, 0)
        df_6mo = _FakeDF(
            [d1, d2],
            {
                "Open": [480.0, 469.5],
                "High": [494.0, 495.0],
                "Low": [472.5, 447.0],
                "Close": [472.5, float("nan")],
                "Volume": [227040, 277786],
            },
        )
        fake_tk = mock.MagicMock()
        fake_tk.history.side_effect = [df_6mo, _FakeDF([], {})]
        fake_tk.fast_info = types.SimpleNamespace(last_price=None)

        with mock.patch.object(self.data.yf, "Ticker", return_value=fake_tk):
            out = self.data.daily_history(quote, months=6)

        self.assertEqual(len(out.index), 1)
        self.assertEqual(out["Close"].iloc[-1], 472.5)


if __name__ == "__main__":
    unittest.main()
