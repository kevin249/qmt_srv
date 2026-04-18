from __future__ import annotations

from datetime import datetime
from pathlib import Path

from vnpy.trader.constant import Exchange, Interval, Product
from vnpy.trader.utility import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")
GATEWAY_NAME = "XTQUANT"

EXCHANGE_VT2XT: dict[Exchange, str] = {
    Exchange.SSE: "SH",
    Exchange.SZSE: "SZ",
    Exchange.BSE: "BJ",
    Exchange.CFFEX: "IF",
    Exchange.SHFE: "SF",
    Exchange.DCE: "DF",
    Exchange.CZCE: "ZF",
    Exchange.INE: "INE",
    Exchange.GFEX: "GF",
}

EXCHANGE_XT2VT: dict[str, Exchange] = {value: key for key, value in EXCHANGE_VT2XT.items()}
EXCHANGE_XT2VT["SHO"] = Exchange.SSE
EXCHANGE_XT2VT["SZO"] = Exchange.SZSE

INTERVAL_VT2XT: dict[Interval, str] = {
    Interval.MINUTE: "1m",
    Interval.HOUR: "1h",
    Interval.DAILY: "1d",
}


def map_vnpy_interval_to_xt(interval: Interval | None) -> str:
    if interval is None:
        return "1d"
    try:
        return INTERVAL_VT2XT[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported vnpy interval: {interval}") from exc


def normalize_qmt_root_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    parsed = Path(raw)
    if parsed.suffix.lower() == ".exe":
        return str(parsed.parent)
    if parsed.name.lower() in {"userdata", "userdata_mini"}:
        return str(parsed.parent)
    return raw


def resolve_userdata_path(qmt_root_path: str) -> str:
    root = Path(normalize_qmt_root_path(qmt_root_path))
    sibling_userdata_mini = root.parent / "userdata_mini" if root.name.lower() == "bin.x64" else None
    sibling_userdata = root.parent / "userdata" if root.name.lower() == "bin.x64" else None
    candidates = [
        sibling_userdata_mini,
        sibling_userdata,
        root / "userdata_mini",
        root / "userdata",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    if sibling_userdata_mini is not None:
        return str(sibling_userdata_mini)
    return str(root / "userdata")


def xt_symbol_to_vnpy(xt_symbol: str) -> tuple[str, Exchange]:
    symbol, xt_exchange = xt_symbol.split(".")
    return symbol, EXCHANGE_XT2VT[xt_exchange]


def vnpy_symbol_to_xt(symbol: str, exchange: Exchange) -> str:
    return f"{symbol}.{EXCHANGE_VT2XT[exchange]}"


def parse_xt_timestamp(value: int | float | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit() and len(raw) == 14:
            return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ)
        if raw.isdigit():
            value = int(raw)
        else:
            return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, CHINA_TZ)
    return None


def format_history_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(CHINA_TZ).strftime("%Y%m%d%H%M%S")


def infer_product(exchange: Exchange, detail: dict | None = None) -> Product:
    if exchange in {Exchange.SSE, Exchange.SZSE, Exchange.BSE}:
        product_type = str((detail or {}).get("ProductType", "")).lower()
        if "option" in product_type:
            return Product.OPTION
        if "index" in product_type:
            return Product.INDEX
        if "etf" in product_type:
            return Product.ETF
        if "bond" in product_type:
            return Product.BOND
        return Product.EQUITY
    return Product.FUTURES
