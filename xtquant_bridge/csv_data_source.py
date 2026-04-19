from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

from .utils import CHINA_TZ


# Maps QMT/user-facing dividend_type values to folder names.
# QMT values: "front" = 前复权, "back" = 后复权, "none"/"" = 不复权.
_ADJUST_FOLDER: dict[Any, str] = {
    "前复权": "前复权",
    "不复权": "不复权",
    "后复权": "后复权",
    "front": "前复权",
    "back": "后复权",
    "none": "不复权",
    "": "不复权",
    "forward": "前复权",
    "backward": "后复权",
    "qfq": "前复权",
    "hfq": "后复权",
    0: "不复权",
    1: "前复权",
    2: "后复权",
}

# Minute-bar periods supported by the 1min CSV source.
_MINUTE_PERIODS = frozenset({"tick", "1m", "5m", "15m", "30m", "60m", "1h"})

# 1min CSV only has 前复权 data.
_MIN_ONLY_ADJUST = "前复权"


class CsvDataSource:
    """Fallback OHLCV data source backed by local CSV files."""

    def __init__(self, base_path: str, default_adjust: str = "前复权") -> None:
        self.base_path = base_path
        self.default_adjust = _ADJUST_FOLDER.get(default_adjust, "前复权")

    def csv_path_for(
        self,
        xt_symbol: str,
        period: str = "1d",
        adjust_type: str | None = None,
    ) -> str:
        """Return the primary CSV path for *xt_symbol* (may not exist)."""
        code = _code_from_xt(xt_symbol)
        if period in _MINUTE_PERIODS:
            return os.path.join(self.base_path, "1min", _MIN_ONLY_ADJUST, "<year>", f"{code}.csv")

        adjust = _ADJUST_FOLDER.get(adjust_type, self.default_adjust) if adjust_type is not None else self.default_adjust
        return os.path.join(self.base_path, "1day", adjust, f"{code}.csv")

    def query(
        self,
        xt_symbol: str,
        start_time: str,
        end_time: str,
        period: str = "1d",
        adjust_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return rows compatible with `translate_bar` payloads."""
        if period in _MINUTE_PERIODS:
            return self._query_minute(xt_symbol, start_time, end_time)
        return self._query_daily(xt_symbol, start_time, end_time, adjust_type)

    def _query_daily(
        self,
        xt_symbol: str,
        start_time: str,
        end_time: str,
        adjust_type: str | None,
    ) -> list[dict[str, Any]]:
        adjust = _ADJUST_FOLDER.get(adjust_type, self.default_adjust) if adjust_type is not None else self.default_adjust
        code = _code_from_xt(xt_symbol)
        csv_path = os.path.join(self.base_path, "1day", adjust, f"{code}.csv")

        if not os.path.isfile(csv_path):
            return []

        start_dt = _parse_qmt_date(start_time)
        end_dt = _parse_qmt_date(end_time)

        rows: list[dict[str, Any]] = []
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                date_str = (row.get("日期") or "").strip()
                if not date_str:
                    continue
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                        hour=15, minute=0, second=0, tzinfo=CHINA_TZ
                    )
                except ValueError:
                    continue

                if start_dt is not None and dt < start_dt:
                    continue
                if end_dt is not None and dt > end_dt:
                    continue

                rows.append({
                    "time": dt,
                    "open": _f(row.get("开盘价")),
                    "high": _f(row.get("最高价")),
                    "low": _f(row.get("最低价")),
                    "close": _f(row.get("收盘价")),
                    "volume": _f(row.get("成交量（股）")),
                    "amount": _f(row.get("成交额（元）")),
                    "openInterest": 0.0,
                })

        return rows

    def _query_minute(
        self,
        xt_symbol: str,
        start_time: str,
        end_time: str,
    ) -> list[dict[str, Any]]:
        code = _code_from_xt(xt_symbol)
        start_dt = _parse_qmt_datetime(start_time)
        end_dt = _parse_qmt_datetime(end_time)

        start_year = start_dt.year if start_dt else 2000
        end_year = end_dt.year if end_dt else datetime.now(CHINA_TZ).year

        rows: list[dict[str, Any]] = []
        base_min = os.path.join(self.base_path, "1min", _MIN_ONLY_ADJUST)

        for year in range(start_year, end_year + 1):
            csv_path = os.path.join(base_min, str(year), f"{code}.csv")
            if not os.path.isfile(csv_path):
                continue
            rows.extend(_read_minute_csv(csv_path, start_dt, end_dt))

        return rows


def _read_minute_csv(
    csv_path: str,
    start_dt: datetime | None,
    end_dt: datetime | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            time_str = (row.get("时间") or "").strip()
            if not time_str:
                continue
            try:
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA_TZ)
            except ValueError:
                continue

            if start_dt is not None and dt < start_dt:
                continue
            if end_dt is not None and dt > end_dt:
                continue

            rows.append({
                "time": dt,
                "open": _f(row.get("开盘价")),
                "high": _f(row.get("最高价")),
                "low": _f(row.get("最低价")),
                "close": _f(row.get("收盘价")),
                "volume": _f(row.get("成交量")),
                "amount": _f(row.get("成交额")),
                "openInterest": 0.0,
            })

    return rows


def _code_from_xt(xt_symbol: str) -> str:
    return xt_symbol.split(".")[0]


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_qmt_date(time_str: str | None) -> datetime | None:
    if not time_str:
        return None
    s = time_str.strip()
    if len(s) >= 8:
        try:
            return datetime.strptime(s[:8], "%Y%m%d").replace(tzinfo=CHINA_TZ)
        except ValueError:
            pass
    return None


def _parse_qmt_datetime(time_str: str | None) -> datetime | None:
    if not time_str:
        return None
    s = time_str.strip()
    if len(s) >= 14:
        try:
            return datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ)
        except ValueError:
            pass
    if len(s) >= 8:
        try:
            return datetime.strptime(s[:8], "%Y%m%d").replace(tzinfo=CHINA_TZ)
        except ValueError:
            pass
    return None
