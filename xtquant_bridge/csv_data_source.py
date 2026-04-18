from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

from .utils import CHINA_TZ

# Maps user-facing adjust type names / codes to CSV folder names
_ADJUST_FOLDER: dict[Any, str] = {
    "前复权": "前复权",
    "不复权": "不复权",
    "后复权": "后复权",
    "forward": "前复权",
    "none": "不复权",
    "backward": "后复权",
    "qfq": "前复权",
    "hfq": "后复权",
    0: "不复权",
    1: "前复权",
    2: "后复权",
}


class CsvDataSource:
    """Read daily OHLCV data from local CSV files as a fallback data source.

    Directory layout under *base_path*::

        base_path/
          前复权/<code>.csv
          不复权/<code>.csv
          后复权/<code>.csv

    Each CSV has a header row followed by one row per trading day.
    Required columns: 日期, 开盘价, 最高价, 最低价, 收盘价, 成交量（股）, 成交额（元）
    """

    def __init__(self, base_path: str, default_adjust: str = "前复权") -> None:
        self.base_path = base_path
        self.default_adjust = _ADJUST_FOLDER.get(default_adjust, "前复权")

    def _csv_path(self, xt_symbol: str, adjust: str) -> str:
        folder = _ADJUST_FOLDER.get(adjust, self.default_adjust)
        code = xt_symbol.split(".")[0]
        return os.path.join(self.base_path, folder, f"{code}.csv")

    def query(
        self,
        xt_symbol: str,
        start_time: str,
        end_time: str,
        adjust_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return rows as dicts compatible with *translate_bar* payload format.

        *start_time* and *end_time* are QMT-style strings ``YYYYMMDDHHMMSS``
        (or ``YYYYMMDD``, or empty for unbounded).
        """
        adjust = adjust_type if adjust_type is not None else self.default_adjust
        csv_path = self._csv_path(xt_symbol, adjust)

        if not os.path.isfile(csv_path):
            return []

        start_dt = _parse_qmt_time(start_time)
        end_dt = _parse_qmt_time(end_time)

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
                    "open": _to_float(row.get("开盘价")),
                    "high": _to_float(row.get("最高价")),
                    "low": _to_float(row.get("最低价")),
                    "close": _to_float(row.get("收盘价")),
                    "volume": _to_float(row.get("成交量（股）")),
                    "amount": _to_float(row.get("成交额（元）")),
                    "openInterest": 0.0,
                })

        return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_qmt_time(time_str: str | None) -> datetime | None:
    """Parse a QMT time string (YYYYMMDDHHMMSS or YYYYMMDD) to a timezone-aware datetime."""
    if not time_str:
        return None
    s = time_str.strip()
    if len(s) >= 8:
        try:
            return datetime.strptime(s[:8], "%Y%m%d").replace(tzinfo=CHINA_TZ)
        except ValueError:
            pass
    return None
