"""Unit tests for Chinese stock name command parsing and Taiwan stock directory lookup."""
from __future__ import annotations

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
        make_mod("pandas", {"DataFrame": object})
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
                day=mock.MagicMock(__format__=lambda *a: "2026-11-20"),
                foreign=100000, trust=20000, dealer=-5000, total=115000,
            )],
            foreign_holding_pct=12.34,
        )
        with mock.patch.object(self.main.institutional, "fetch", return_value=fake_report) as fetch_mock, \
             mock.patch.object(self.main.charts, "institutional_chart"):
            msgs = self.main._handle_command("T", "玉晶光")
            fetch_mock.assert_called_once_with("3406")
            self.assertEqual(len(msgs), 2)
            self.assertIn("玉晶光 (3406)", msgs[1].text)


if __name__ == "__main__":
    unittest.main()
