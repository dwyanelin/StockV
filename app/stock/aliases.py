"""Cross-asset alias table: Taiwan/global indices, crypto, forex, and commodities."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AssetAlias:
    ticker: str        # user-facing short label, e.g. "台股", "BTC", "USD/TWD"
    yahoo_symbol: str  # e.g. "^TWII", "BTC-USD", "TWD=X"
    name: str          # display name, e.g. "台灣加權指數", "比特幣"
    is_taiwan: bool
    currency: str
    asset_type: str    # "index", "crypto", "fx", "commodity"


_MARKET_TW = AssetAlias("台股", "^TWII", "台灣加權指數", True, "TWD", "index")

_ENTRIES: list[tuple[tuple[str, ...], AssetAlias]] = [
    # Taiwan Market Index
    (("台股", "臺股", "大盤", "加權", "加權指數", "TSE", "TWII", "TAIEX"), _MARKET_TW),
    # Global Indices
    (("SP500", "S&P500", "SPX", "標普", "標普500"), AssetAlias("SP500", "^GSPC", "S&P 500 指數", False, "USD", "index")),
    (("NASDAQ", "IXIC", "那斯達克", "那指", "納斯達克", "納指"), AssetAlias("NASDAQ", "^IXIC", "那斯達克指數", False, "USD", "index")),
    (("DJI", "道瓊", "道瓊工業"), AssetAlias("DJI", "^DJI", "道瓊工業指數", False, "USD", "index")),
    (("SOX", "費半", "費城半導體"), AssetAlias("SOX", "^SOX", "費城半導體指數", False, "USD", "index")),
    (("VIX", "恐慌指數"), AssetAlias("VIX", "^VIX", "VIX 恐慌指數", False, "USD", "index")),
    (("N225", "日經", "日經225"), AssetAlias("N225", "^N225", "日經 225 指數", False, "JPY", "index")),
    (("HSI", "恆生", "恒生"), AssetAlias("HSI", "^HSI", "香港恆生指數", False, "HKD", "index")),
    # Crypto
    (("BTC", "BTC-USD", "比特幣"), AssetAlias("BTC", "BTC-USD", "比特幣", False, "USD", "crypto")),
    (("ETH", "ETH-USD", "以太幣", "以太坊"), AssetAlias("ETH", "ETH-USD", "以太幣", False, "USD", "crypto")),
    (("SOL", "SOL-USD", "索拉納"), AssetAlias("SOL", "SOL-USD", "Solana", False, "USD", "crypto")),
    (("BNB", "BNB-USD", "幣安幣"), AssetAlias("BNB", "BNB-USD", "BNB", False, "USD", "crypto")),
    (("XRP", "XRP-USD", "瑞波幣"), AssetAlias("XRP", "XRP-USD", "瑞波幣", False, "USD", "crypto")),
    (("DOGE", "DOGE-USD", "狗狗幣"), AssetAlias("DOGE", "DOGE-USD", "狗狗幣", False, "USD", "crypto")),
    # Commodities
    (("GOLD", "XAU", "黃金"), AssetAlias("GOLD", "GC=F", "黃金期貨", False, "USD", "commodity")),
    (("OIL", "WTI", "原油"), AssetAlias("OIL", "CL=F", "WTI 原油期貨", False, "USD", "commodity")),
    # Common Forex
    (("USD/TWD", "USD/NTD", "USDTWD", "USDNTD", "美元", "美金"), AssetAlias("USD/TWD", "TWD=X", "美元 / 台幣", False, "TWD", "fx")),
    (("JPY/TWD", "JPY/NTD", "JPYTWD", "日圓", "日幣"), AssetAlias("JPY/TWD", "JPYTWD=X", "日圓 / 台幣", False, "TWD", "fx")),
    (("EUR/TWD", "EUR/NTD", "EURTWD", "歐元台幣"), AssetAlias("EUR/TWD", "EURTWD=X", "歐元 / 台幣", False, "TWD", "fx")),
    (("CNY/TWD", "CNY/NTD", "CNYTWD", "人民幣"), AssetAlias("CNY/TWD", "CNYTWD=X", "人民幣 / 台幣", False, "TWD", "fx")),
    (("GBP/TWD", "GBP/NTD", "GBPTWD", "英鎊台幣"), AssetAlias("GBP/TWD", "GBPTWD=X", "英鎊 / 台幣", False, "TWD", "fx")),
    (("USD/JPY", "USDJPY", "美元日圓"), AssetAlias("USD/JPY", "JPY=X", "美元 / 日圓", False, "JPY", "fx")),
    (("EUR/USD", "EURUSD", "歐元"), AssetAlias("EUR/USD", "EURUSD=X", "歐元 / 美元", False, "USD", "fx")),
    (("GBP/USD", "GBPUSD", "英鎊"), AssetAlias("GBP/USD", "GBPUSD=X", "英鎊 / 美元", False, "USD", "fx")),
]

_ALIAS_MAP: dict[str, AssetAlias] = {}
for keys, alias in _ENTRIES:
    for k in keys:
        _ALIAS_MAP[k.upper()] = alias

_FX_PAIR_RE = re.compile(r"^([A-Z]{3})/([A-Z]{3})$")
_FX_OVERVIEW_KEYS = ("外匯", "匯率", "FX")

FX_OVERVIEW_PAIRS: tuple[AssetAlias, ...] = (
    _ALIAS_MAP["USD/TWD"],
    _ALIAS_MAP["JPY/TWD"],
    _ALIAS_MAP["EUR/TWD"],
    _ALIAS_MAP["CNY/TWD"],
    _ALIAS_MAP["GBP/TWD"],
    _ALIAS_MAP["USD/JPY"],
)

MARKET_TW_KEYS = {"台股", "臺股", "大盤", "加權", "加權指數", "TSE", "TWII", "TAIEX"}


def is_fx_overview(query: str) -> bool:
    return query.strip().upper() in _FX_OVERVIEW_KEYS


def is_taiwan_market(query: str) -> bool:
    return query.strip().upper() in MARKET_TW_KEYS


def lookup(query: str) -> AssetAlias | None:
    """Resolve an alias (index, crypto, forex, commodity) or generic AAA/BBB FX pair."""
    key = "".join(query.strip().upper().split()).replace("臺", "台")
    if key in _ALIAS_MAP:
        return _ALIAS_MAP[key]

    m = _FX_PAIR_RE.match(key)
    if m:
        base, quote = m.group(1), m.group(2)
        quote_yf = "TWD" if quote == "NTD" else quote
        base_yf = "TWD" if base == "NTD" else base
        yf_sym = f"{quote_yf}=X" if base_yf == "USD" else f"{base_yf}{quote_yf}=X"
        return AssetAlias(f"{base}/{quote}", yf_sym, f"{base} / {quote}", False, quote_yf, "fx")
    return None
