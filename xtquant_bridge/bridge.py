from __future__ import annotations

import threading
from datetime import date, datetime
from itertools import count
from typing import Any

from xtquant import xtdata, xtdatacenter
from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount
from vnpy.event import Event
from vnpy.rpc import RpcServer
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.event import EVENT_ACCOUNT, EVENT_CONTRACT, EVENT_LOG, EVENT_ORDER, EVENT_POSITION, EVENT_TICK, EVENT_TRADE
from vnpy.trader.object import LogData, OrderRequest

from .callback_router import XtQuantCallbackRouter
from .concurrent_rpc_server import ConcurrentRpcServer
from .csv_data_source import CsvDataSource
from .event_publisher import EventPublisher
from .rpc_handler import RpcRequestHandler
from .serialization import serialize_xtdata_result
from .translator import DataTranslator
from .utils import (
    CHINA_TZ,
    GATEWAY_NAME,
    format_history_time,
    map_vnpy_interval_to_xt,
    normalize_qmt_root_path,
    parse_xt_timestamp,
    resolve_userdata_path,
    vnpy_symbol_to_xt,
)
from .xtdata_registry import build_xtdata_registry
from .xtdata_rpc import XtdataMirrorExecutor


class XtQuantBridge:
    LOG_LEVELS = {
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
    }
    DEFAULT_LOGGING_CONFIG = {
        "enabled": True,
        "level": "INFO",
        "console": True,
        "publish_rpc_log_event": True,
        "categories": {
            "lifecycle": True,
            "rpc": True,
            "market_data": False,
            "snapshot": True,
            "account": True,
            "position": True,
            "order": True,
            "trade": True,
            "history": False,
            "contract": False,
            "heartbeat": False,
            "data_download": True,
        },
    }

    def __init__(
        self,
        config: dict[str, Any],
        *,
        rpc_server: RpcServer | None = None,
        xtdata_module: Any = xtdata,
        xtdatacenter_module: Any = xtdatacenter,
        xttrader_class: type[Any] = XtQuantTrader,
        stock_account_class: type[Any] = StockAccount,
        translator: DataTranslator | None = None,
    ) -> None:
        self.config = config
        self.xt_config = config["xt"]
        self.rpc_config = config["rpc"]
        self.data_download_config = config.get("data_download", {}) or {}

        self.rpc_server = rpc_server or self._create_rpc_server(self.rpc_config)
        self.xtdata = xtdata_module
        self.xtdatacenter = xtdatacenter_module
        self.xttrader_class = xttrader_class
        self.stock_account_class = stock_account_class
        self.translator = translator or DataTranslator()
        self.publisher = EventPublisher(self.rpc_server, maxsize=self.xt_config["event_queue_size"])
        self.xtdata_registry = build_xtdata_registry(self.xtdata)
        self.xtdata_executor = XtdataMirrorExecutor(self.xtdata, self.xtdata_registry, publisher=self.publisher)
        self.callback_router = XtQuantCallbackRouter(self, self.translator)
        self.rpc_handler = RpcRequestHandler(self)
        raw_logging_config = config.get("logging", {}) or {}
        self.logging_config = {
            **self.DEFAULT_LOGGING_CONFIG,
            **raw_logging_config,
            "categories": {
                **self.DEFAULT_LOGGING_CONFIG["categories"],
                **(raw_logging_config.get("categories", {}) or {}),
            },
        }

        self.qmt_root = normalize_qmt_root_path(self.xt_config["qmt_path"])
        self.userdata_path = resolve_userdata_path(self.qmt_root)
        self.account = self.stock_account_class(self.xt_config["account_id"], self.xt_config["account_type"])
        self.xt_trader = None
        self.running = False
        self.registered_clients: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, int] = {}
        self._order_counter = count(1)
        self.local_order_sysid_map: dict[str, str] = {}

        self.ticks: dict[str, Any] = {}
        self.l1_ticks: dict[str, dict[str, Any]] = {}
        self.orders: dict[str, Any] = {}
        self.trades: dict[str, Any] = {}
        self.positions: dict[str, Any] = {}
        self.accounts: dict[str, Any] = {}
        self.contracts: dict[str, Any] = {}

        csv_cfg = config.get("csv_data_source") or {}
        csv_path = str(csv_cfg.get("path") or "").strip()
        csv_adjust = str(csv_cfg.get("default_adjust") or "前复权")
        self.csv_source: CsvDataSource | None = CsvDataSource(csv_path, csv_adjust) if csv_path else None
        if self.csv_source is not None and self.csv_source.default_adjust not in {"前复权", "后复权", "不复权"}:
            self.csv_source.default_adjust = "前复权"

        # Track which symbols have had their financial data ensured this session
        # to avoid redundant download_financial_data calls per symbol.
        self._financial_data_ensured: set[str] = set()
        self._daily_1min_downloaded_dates: set[str] = set()
        self._daily_1min_checked_non_trading_dates: set[str] = set()
        self._data_download_stop = threading.Event()
        self._data_download_thread: threading.Thread | None = None
        self._data_download_lock = threading.Lock()

    @staticmethod
    def _create_rpc_server(rpc_config: dict[str, Any]) -> ConcurrentRpcServer:
        return ConcurrentRpcServer(
            fast_workers=max(1, int(rpc_config.get("fast_workers", rpc_config.get("worker_threads", 8)) or 8)),
            fast_queue_size=max(0, int(rpc_config.get("fast_queue_size", rpc_config.get("queue_size", 128)) or 0)),
            slow_workers=max(1, int(rpc_config.get("slow_workers", 2) or 2)),
            slow_queue_size=max(0, int(rpc_config.get("slow_queue_size", 4) or 0)),
        )

    @staticmethod
    def _safe_attr(data: Any, name: str, default: Any = "") -> Any:
        return getattr(data, name, default)

    @staticmethod
    def _extract_history_rows(result: Any, xt_symbol: str) -> list:
        if not result:
            return []
        symbol_data = result.get(xt_symbol, []) if isinstance(result, dict) else []
        if hasattr(symbol_data, "to_dict"):
            return symbol_data.to_dict("records")
        if isinstance(symbol_data, dict) and symbol_data.get("__type__") == "dataframe":
            data = symbol_data.get("data", {})
            columns = data.get("columns") or []
            rows = data.get("data") or []
            return [dict(zip(columns, row)) for row in rows] if columns else []
        if isinstance(symbol_data, list):
            return symbol_data
        return []

    @staticmethod
    def _format_history_range(rows: list) -> str:
        datetimes = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            dt = parse_xt_timestamp(row.get("time") or row.get("timestamp"))
            if dt is not None:
                datetimes.append(dt.astimezone(CHINA_TZ))
        if not datetimes:
            return "-"
        datetimes.sort()
        return f"{datetimes[0].strftime('%Y-%m-%d %H:%M:%S')}~{datetimes[-1].strftime('%Y-%m-%d %H:%M:%S')}"

    def _print_history_summary(
        self,
        vt_symbol: str,
        interval: str,
        source: str,
        local_rows: list,
        market_rows: list,
        csv_rows: list,
        final_rows: list,
    ) -> None:
        lines = [
            f"[XTQ Bridge] [INFO][history] 最终历史数据汇总 vt_symbol={vt_symbol} interval={interval} source={source} ...",
            f"[XTQ Bridge] [INFO][history] local_count={len(local_rows)} local_range={self._format_history_range(local_rows)} ...",
            f"[XTQ Bridge] [INFO][history] market_count={len(market_rows)} market_range={self._format_history_range(market_rows)} ...",
            f"[XTQ Bridge] [INFO][history] csv_count={len(csv_rows)} csv_range={self._format_history_range(csv_rows)} ...",
            f"[XTQ Bridge] [INFO][history] final_count={len(final_rows)} final_range={self._format_history_range(final_rows)} ...",
        ]
        print("\n".join(lines))

    @staticmethod
    def _rows_to_serialized_dataframe(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any]:
        import pandas as pd

        normalized_rows = []
        for row in rows:
            normalized = {}
            for column in columns:
                value = row.get(column)
                if column == "time" and value is not None:
                    dt = parse_xt_timestamp(value)
                    if dt is not None:
                        value = int(dt.astimezone(CHINA_TZ).timestamp() * 1000)
                normalized[column] = value
            normalized_rows.append(normalized)
        return serialize_xtdata_result(pd.DataFrame(normalized_rows, columns=columns))

    def should_log(self, level: str, category: str) -> bool:
        if not self.logging_config.get("enabled", True):
            return False
        categories = self.logging_config.get("categories", {})
        if not categories.get(category, False):
            return False
        current = self.LOG_LEVELS.get(str(self.logging_config.get("level", "INFO")).upper(), 20)
        target = self.LOG_LEVELS.get(level.upper(), 20)
        return target >= current

    def format_log_message(self, level: str, category: str, message: str, **fields: Any) -> str:
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value != "")
        base = f"[{level.upper()}][{category}] {message}"
        return f"{base} {extras}".rstrip()

    def emit_log(self, level: str, category: str, message: str, **fields: Any) -> None:
        if not self.should_log(level, category):
            return

        formatted = self.format_log_message(level, category, message, **fields)
        if self.logging_config.get("console", True):
            print(f"[XTQ Bridge] {formatted}")
        if self.logging_config.get("publish_rpc_log_event", True):
            self.publish_log(formatted)

    def log_debug(self, category: str, message: str, **fields: Any) -> None:
        self.emit_log("DEBUG", category, message, **fields)

    def log_info(self, category: str, message: str, **fields: Any) -> None:
        self.emit_log("INFO", category, message, **fields)

    def log_warning(self, category: str, message: str, **fields: Any) -> None:
        self.emit_log("WARNING", category, message, **fields)

    def log_error(self, category: str, message: str, **fields: Any) -> None:
        self.emit_log("ERROR", category, message, **fields)

    def start(self) -> None:
        self.register_rpc()
        self.rpc_server.start(self.rpc_config["rep_address"], self.rpc_config["pub_address"])
        self.publisher.start()
        self.running = True
        self._data_download_stop.clear()

        try:
            self.initialize_market_data()
            self.initialize_trading()
            self.refresh_snapshots()
            self.run_boot_data_download_if_enabled()
            self.start_daily_1min_download_scheduler()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        self.stop_daily_1min_download_scheduler()

        if self.xt_trader is not None:
            try:
                self.xt_trader.stop()
            except Exception:  # noqa: BLE001
                pass
            self.xt_trader = None

        try:
            self.xtdatacenter.shutdown()
        except Exception:  # noqa: BLE001
            pass

        self.publisher.stop()
        self.publisher.join()
        self.rpc_server.stop()
        self.rpc_server.join()
        self.running = False

    @staticmethod
    def _config_flag_enabled(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        try:
            return int(value or 0) != 0
        except (TypeError, ValueError):
            return str(value).strip().lower() in {"true", "yes", "on"}

    @staticmethod
    def _normalize_download_day(value: Any, *, end_of_day: bool) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        digits = "".join(char for char in raw if char.isdigit())
        if len(digits) == 8:
            return f"{digits}{'235959' if end_of_day else '000000'}"
        if len(digits) == 14:
            return digits
        raise ValueError(f"invalid download day: {value!r}; expected YYYYMMDD or YYYY-MM-DD")

    def _data_download_dividend_type(self) -> str:
        raw = str(
            self.data_download_config.get(
                "dividend_type",
                self.data_download_config.get(
                    "download_mode",
                    self.data_download_config.get("mode", "front"),
                ),
            )
            or "front"
        ).strip()
        aliases = {
            "前复权": "front",
            "qfq": "front",
            "forward": "front",
            "后复权": "back",
            "hfq": "back",
            "backward": "back",
            "不复权": "none",
            "none": "none",
            "raw": "none",
            "front": "front",
            "back": "back",
            "front_ratio": "front_ratio",
            "back_ratio": "back_ratio",
        }
        normalized = aliases.get(raw.lower(), aliases.get(raw, raw))
        if normalized not in {"none", "front", "back", "front_ratio", "back_ratio"}:
            raise ValueError(f"unsupported data_download.dividend_type: {raw!r}")
        return normalized

    def _parse_daily_trigger_time(self) -> tuple[int, int, int]:
        raw = str(self.data_download_config.get("daily_trigger_time") or "15:30").strip()
        parts = raw.split(":")
        if len(parts) not in {2, 3}:
            raise ValueError(f"invalid data_download.daily_trigger_time: {raw!r}")
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            raise ValueError(f"invalid data_download.daily_trigger_time: {raw!r}")
        return hour, minute, second

    def run_boot_data_download_if_enabled(self) -> bool:
        if not self._config_flag_enabled(self.data_download_config.get("boot_data_download", 0)):
            return False

        try:
            start_time = self._normalize_download_day(
                self.data_download_config.get("boot_check_startday", ""),
                end_of_day=False,
            )
            end_time = self._normalize_download_day(
                self.data_download_config.get("boot_check_endday", ""),
                end_of_day=True,
            )
            if not start_time or not end_time:
                self.log_warning(
                    "data_download",
                    "boot 1min download skipped",
                    reason="boot_check_startday or boot_check_endday is empty",
                )
                return False
            if start_time > end_time:
                raise ValueError("boot_check_startday must be earlier than or equal to boot_check_endday")

            self.download_all_a_share_1min(start_time, end_time, reason="boot")
            return True
        except Exception as exc:  # noqa: BLE001
            self.log_error("data_download", "boot 1min download failed", error=exc)
            return False

    def start_daily_1min_download_scheduler(self) -> None:
        if not self._config_flag_enabled(self.data_download_config.get("daily_1min_download", 1)):
            return
        if self._data_download_thread is not None and self._data_download_thread.is_alive():
            return

        self._parse_daily_trigger_time()
        self._data_download_stop.clear()
        self._data_download_thread = threading.Thread(
            target=self._daily_1min_download_loop,
            name="XTQ-1min-download",
            daemon=True,
        )
        self._data_download_thread.start()
        self.log_info(
            "data_download",
            "daily 1min download scheduler started",
            trigger_time=self.data_download_config.get("daily_trigger_time", "15:30"),
        )

    def stop_daily_1min_download_scheduler(self) -> None:
        self._data_download_stop.set()
        thread = self._data_download_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._data_download_thread = None

    def _daily_1min_download_loop(self) -> None:
        interval = max(10, int(self.data_download_config.get("check_interval_seconds", 60) or 60))
        while not self._data_download_stop.wait(interval):
            try:
                self.run_daily_1min_download_check()
            except Exception as exc:  # noqa: BLE001
                self.log_error("data_download", "daily 1min download check failed", error=exc)

    def run_daily_1min_download_check(self, now: datetime | None = None) -> bool:
        if not self._config_flag_enabled(self.data_download_config.get("daily_1min_download", 1)):
            return False

        current = now or datetime.now(CHINA_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=CHINA_TZ)
        else:
            current = current.astimezone(CHINA_TZ)

        hour, minute, second = self._parse_daily_trigger_time()
        trigger_time = current.replace(hour=hour, minute=minute, second=second, microsecond=0)
        day_token = current.strftime("%Y%m%d")
        if current < trigger_time:
            return False
        if day_token in self._daily_1min_downloaded_dates:
            return False
        if day_token in self._daily_1min_checked_non_trading_dates:
            return False

        if not self.is_trading_day(current.date()):
            self._daily_1min_checked_non_trading_dates.add(day_token)
            self.log_info("data_download", "daily 1min download skipped", trade_date=day_token, reason="not-trading-day")
            return False

        start_time = f"{day_token}000000"
        end_time = f"{day_token}235959"
        try:
            count = self.download_all_a_share_1min(start_time, end_time, reason="daily")
            self._daily_1min_downloaded_dates.add(day_token)
            return count > 0
        except Exception as exc:  # noqa: BLE001
            self.log_error("data_download", "daily 1min download failed", trade_date=day_token, error=exc)
            return False

    def is_trading_day(self, target_day: date) -> bool:
        day_token = target_day.strftime("%Y%m%d")
        market = str(self.data_download_config.get("calendar_market") or "SH")
        try:
            if hasattr(self.xtdata, "get_trading_dates"):
                dates = self.xtdata.get_trading_dates(market, day_token, day_token, -1)
            elif hasattr(self.xtdata, "get_trading_calendar"):
                dates = self.xtdata.get_trading_calendar(market, day_token, day_token)
            else:
                return target_day.weekday() < 5
        except Exception as exc:  # noqa: BLE001
            self.log_warning("data_download", "trading calendar unavailable; fallback to weekday", error=exc)
            return target_day.weekday() < 5

        return self._trading_calendar_contains_day(dates, day_token)

    def _trading_calendar_contains_day(self, dates: Any, day_token: str) -> bool:
        if dates is None:
            return False
        if isinstance(dates, dict):
            iterable = dates.values()
        else:
            iterable = dates
        try:
            for item in iterable:
                if self._calendar_item_to_day(item) == day_token:
                    return True
        except TypeError:
            return self._calendar_item_to_day(dates) == day_token
        return False

    def _calendar_item_to_day(self, item: Any) -> str:
        if isinstance(item, datetime):
            return item.astimezone(CHINA_TZ).strftime("%Y%m%d")
        if isinstance(item, date):
            return item.strftime("%Y%m%d")
        if isinstance(item, dict):
            for key in ("date", "trade_date", "trading_day", "time", "timestamp"):
                if key in item:
                    day = self._calendar_item_to_day(item[key])
                    if day:
                        return day
            return ""
        if isinstance(item, (int, float)):
            parsed = parse_xt_timestamp(item)
            return parsed.astimezone(CHINA_TZ).strftime("%Y%m%d") if parsed else ""

        raw = str(item or "").strip()
        digits = "".join(char for char in raw if char.isdigit())
        if len(digits) >= 8 and digits[:2] in {"19", "20"}:
            return digits[:8]
        if digits:
            parsed = parse_xt_timestamp(digits)
            return parsed.astimezone(CHINA_TZ).strftime("%Y%m%d") if parsed else ""
        return ""

    def download_all_a_share_1min(self, start_time: str, end_time: str, *, reason: str) -> int:
        if not self._data_download_lock.acquire(blocking=False):
            self.log_warning("data_download", "1min download skipped", reason=reason, status="already-running")
            return 0

        try:
            stock_list = self._get_a_share_stock_list()
            if not stock_list:
                self.log_warning("data_download", "1min download skipped", reason=reason, status="empty-stock-list")
                return 0

            batch_size = max(1, int(self.data_download_config.get("batch_size", 200) or 200))
            total = len(stock_list)
            self.log_info(
                "data_download",
                "1min download started",
                reason=reason,
                symbols=total,
                start_time=start_time,
                end_time=end_time,
                batch_size=batch_size,
                dividend_type=self._data_download_dividend_type(),
            )

            downloaded = 0
            for index in range(0, total, batch_size):
                if self._data_download_stop.is_set():
                    self.log_warning("data_download", "1min download stopped", reason=reason, downloaded=downloaded, total=total)
                    break
                batch = stock_list[index:index + batch_size]
                self._download_history_1min_batch(batch, start_time, end_time)
                downloaded += len(batch)
                self.log_info(
                    "data_download",
                    "1min download batch done",
                    reason=reason,
                    downloaded=downloaded,
                    total=total,
                )

            self.log_info("data_download", "1min download completed", reason=reason, downloaded=downloaded, total=total)
            return downloaded
        finally:
            self._data_download_lock.release()

    def _get_a_share_stock_list(self) -> list[str]:
        if hasattr(self.xtdata, "download_sector_data"):
            try:
                self.xtdata.download_sector_data()
            except Exception as exc:  # noqa: BLE001
                self.log_warning("data_download", "download sector data failed", error=exc)

        if not hasattr(self.xtdata, "get_stock_list_in_sector"):
            raise RuntimeError("current xtquant does not support get_stock_list_in_sector")

        sectors = self.data_download_config.get("stock_sectors") or ["沪深A股"]
        if isinstance(sectors, str):
            sectors = [sectors]

        seen: set[str] = set()
        symbols: list[str] = []
        for sector in sectors:
            sector_name = str(sector).strip()
            if not sector_name:
                continue
            sector_symbols = self.xtdata.get_stock_list_in_sector(sector_name) or []
            for symbol in sector_symbols:
                xt_symbol = str(symbol).strip()
                if not xt_symbol:
                    continue
                if "." in xt_symbol and xt_symbol.rsplit(".", 1)[-1] not in {"SH", "SZ", "BJ"}:
                    continue
                if xt_symbol not in seen:
                    seen.add(xt_symbol)
                    symbols.append(xt_symbol)
        return symbols

    def _download_history_1min_batch(self, stock_list: list[str], start_time: str, end_time: str) -> Any:
        dividend_type = self._data_download_dividend_type()
        if dividend_type in {"front", "back", "front_ratio", "back_ratio"}:
            self._ensure_financial_data(stock_list)

        if hasattr(self.xtdata, "download_history_data2"):
            callback = self._make_download_progress_callback("xtdata.download_history_data2")
            try:
                return self.xtdata.download_history_data2(stock_list, "1m", start_time, end_time, callback=callback)
            except TypeError:
                return self.xtdata.download_history_data2(stock_list, "1m", start_time, end_time)

        if hasattr(self.xtdata, "download_history_data"):
            callback = self._make_download_progress_callback("xtdata.download_history_data")
            try:
                return self.xtdata.download_history_data(stock_list, "1m", start_time, end_time, callback=callback)
            except TypeError:
                return self.xtdata.download_history_data(stock_list, "1m", start_time, end_time)

        raise RuntimeError("current xtquant does not support download_history_data")

    def register_rpc(self) -> None:
        for name in (
            "register_client",
            "subscribe",
            "send_order",
            "cancel_order",
            "query_history",
            "get_tick",
            "get_l1_tick",
            "get_order",
            "get_trade",
            "get_position",
            "get_account",
            "get_contract",
            "get_all_ticks",
            "get_all_orders",
            "get_all_trades",
            "get_all_positions",
            "get_all_accounts",
            "get_all_contracts",
            "get_all_active_orders",
        ):
            self.rpc_server.register(getattr(self.rpc_handler, name))
        for rpc_name in self.xtdata_registry:
            self.rpc_server.register(self._make_xtdata_rpc(rpc_name))

    def _make_xtdata_rpc(self, rpc_name: str):
        def xtdata_rpc_method(*args, **kwargs):
            return self.call_xtdata(rpc_name, *args, **kwargs)

        xtdata_rpc_method.__name__ = rpc_name
        return xtdata_rpc_method

    @staticmethod
    def _summarize_xtdata_result(result: Any) -> str:
        if result is None:
            return "None"
        if isinstance(result, dict):
            if result.get("__type__") == "dataframe":
                rows = result.get("data", {}).get("data", [])
                return f"dataframe:rows={len(rows)}"
            parts = []
            for k, v in result.items():
                if isinstance(v, dict) and v.get("__type__") == "dataframe":
                    rows = v.get("data", {}).get("data", [])
                    parts.append(f"{k}:rows={len(rows)}")
                elif hasattr(v, "__len__"):
                    parts.append(f"{k}:len={len(v)}")
                else:
                    parts.append(f"{k}:{repr(v)[:40]}")
            return "{" + ", ".join(parts) + "}" if parts else "{}"
        if hasattr(result, "__len__"):
            return f"len={len(result)}"
        return repr(result)[:80]

    def _make_download_progress_callback(self, rpc_name: str):
        def callback(data):
            if isinstance(data, dict):
                self.log_info("rpc", "download progress", method=rpc_name, **{k: v for k, v in data.items()})
            else:
                self.log_info("rpc", "download progress", method=rpc_name, data=repr(data)[:200])
        return callback

    _DATA_FETCH_METHODS = frozenset({"xtdata.get_market_data_ex", "xtdata.get_local_data"})
    _HISTORY_DOWNLOAD_METHODS = frozenset({"xtdata.download_history_data2", "xtdata.download_history_data"})

    @staticmethod
    def _arg_period(rpc_name: str, args: tuple, kwargs: dict) -> str:
        if "period" in kwargs:
            return str(kwargs.get("period") or "").strip().lower()
        if rpc_name in XtQuantBridge._HISTORY_DOWNLOAD_METHODS and len(args) >= 2:
            return str(args[1] or "").strip().lower()
        if rpc_name in XtQuantBridge._DATA_FETCH_METHODS and len(args) >= 3:
            return str(args[2] or "").strip().lower()
        return ""

    @staticmethod
    def _arg_stock_list(rpc_name: str, args: tuple, kwargs: dict) -> list[str]:
        stock_list = kwargs.get("stock_list")
        if stock_list is None and args:
            stock_list = args[0]
        if isinstance(stock_list, str):
            return [stock_list]
        try:
            return [str(item) for item in stock_list or [] if str(item or "").strip()]
        except TypeError:
            return []

    def _tick_market_snapshot_result(self, stock_list: list[str]) -> dict[str, Any]:
        rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
        if not stock_list:
            return rows_by_symbol

        raw = self.xtdata.get_full_tick(stock_list) if hasattr(self.xtdata, "get_full_tick") else {}
        if not isinstance(raw, dict):
            raw = {}
        for xt_symbol in stock_list:
            payload = raw.get(xt_symbol) or {}
            if not payload:
                continue
            row = dict(payload)
            if "time" not in row:
                # get_full_tick reports the timestamp as "timetag"
                # ("YYYYMMDD HH:MM:SS"); subscribed ticks use "time"/"timestamp".
                row["time"] = row.get("timestamp") or row.get("timetag")
            rows_by_symbol[xt_symbol] = [row]
        return serialize_xtdata_result(rows_by_symbol)

    def _tick_history_enabled(self) -> bool:
        # When enabled (default), tick-period download/fetch passes through to
        # the real xtdata so callers can read a full intraday tick series
        # (needed by dbfp 历史复盘). Set data_download.tick_history_enabled=0
        # to fall back to the L1-snapshot-only behaviour.
        return self._config_flag_enabled(self.data_download_config.get("tick_history_enabled", 1))

    def call_xtdata(self, rpc_name: str, *args, **kwargs):
        period = self._arg_period(rpc_name, args, kwargs)
        if period == "tick" and not self._tick_history_enabled():
            if rpc_name in self._HISTORY_DOWNLOAD_METHODS:
                self.log_warning("rpc", "tick history download skipped; use get_l1_tick/get_full_tick for L1 snapshot", method=rpc_name)
                return {"skipped": True, "reason": "tick-history-download-disabled"}
            if rpc_name in self._DATA_FETCH_METHODS:
                stock_list = self._arg_stock_list(rpc_name, args, kwargs)
                self.log_warning("rpc", "tick history fetch served as L1 snapshot", method=rpc_name, stock_list=stock_list)
                return self._tick_market_snapshot_result(stock_list)

        if rpc_name in self._DATA_FETCH_METHODS:
            self._log_data_fetch_request(rpc_name, args, kwargs)
        else:
            arg_summary = repr(args)[:200] if args else ""
            kwarg_summary = " ".join(f"{k}={repr(v)[:80]}" for k, v in kwargs.items()) if kwargs else ""
            self.log_info("rpc", "xtdata rpc start ...", method=rpc_name, args=arg_summary, kwargs=kwarg_summary)

        if rpc_name in ("xtdata.download_history_data2", "xtdata.download_history_data") and "callback" not in kwargs:
            kwargs = {**kwargs, "callback": self._make_download_progress_callback(rpc_name)}
        if rpc_name in self._DATA_FETCH_METHODS:
            result = self.xtdata_executor.call(rpc_name, *args, **kwargs)
            result = self._refresh_stale_xtdata_result_if_needed(rpc_name, result, args, kwargs)
            self._log_data_fetch_result(rpc_name, result, kwargs)
        else:
            result = self.xtdata_executor.call(rpc_name, *args, **kwargs)
            self.log_info("rpc", "xtdata rpc done ...", method=rpc_name, result=self._summarize_xtdata_result(result))

        if self.csv_source is not None and rpc_name in self._DATA_FETCH_METHODS:
            result = self._csv_supplement_xtdata_result(result, kwargs)

        return result

    def _refresh_stale_xtdata_result_if_needed(self, rpc_name: str, result: Any, args: tuple, kwargs: dict) -> Any:
        period = self._arg_period(rpc_name, args, kwargs)
        if not period or period == "tick":
            return result

        end_dt = parse_xt_timestamp(kwargs.get("end_time"))
        if end_dt is None:
            return result
        requested_end = min(end_dt.astimezone(CHINA_TZ).date(), datetime.now(CHINA_TZ).date())

        stale: list[tuple[str, date | None]] = []
        for xt_symbol in self._arg_stock_list(rpc_name, args, kwargs):
            rows = self._extract_history_rows(result, xt_symbol)
            last_dt = self._last_row_datetime(rows)
            if last_dt is None or last_dt.astimezone(CHINA_TZ).date() < requested_end:
                stale.append((xt_symbol, last_dt.astimezone(CHINA_TZ).date() if last_dt else None))

        if not stale:
            return result

        symbols = [item[0] for item in stale]
        last_ranges = ", ".join(f"{symbol}:{last_day or 'none'}" for symbol, last_day in stale)
        self.log_warning(
            "history",
            "QMT data shorter than requested; refreshing latest cache",
            method=rpc_name,
            period=period,
            symbols=symbols,
            qmt_last=last_ranges,
            requested_end=requested_end,
        )

        refresh_start = self._refresh_start_time(stale, str(kwargs.get("start_time") or ""))
        refresh_end = str(kwargs.get("end_time") or "")
        try:
            self._download_history(symbols, period, refresh_start, refresh_end, incrementally=True)
        except Exception as exc:  # noqa: BLE001
            self.log_warning(
                "history",
                "QMT latest cache refresh failed",
                method=rpc_name,
                period=period,
                symbols=symbols,
                error=exc,
            )
            return result

        refreshed = self.xtdata_executor.call(rpc_name, *args, **kwargs)
        refreshed_stale = []
        for xt_symbol in symbols:
            rows = self._extract_history_rows(refreshed, xt_symbol)
            last_dt = self._last_row_datetime(rows)
            if last_dt is None or last_dt.astimezone(CHINA_TZ).date() < requested_end:
                refreshed_stale.append(f"{xt_symbol}:{last_dt.astimezone(CHINA_TZ).date() if last_dt else 'none'}")

        if refreshed_stale:
            self.log_warning(
                "history",
                "QMT data still shorter after refresh",
                method=rpc_name,
                period=period,
                qmt_last=", ".join(refreshed_stale),
                requested_end=requested_end,
            )
        else:
            self.log_info("history", "QMT latest cache refresh applied", method=rpc_name, period=period, symbols=symbols)
        return refreshed

    @staticmethod
    def _last_row_datetime(rows: list) -> datetime | None:
        latest: datetime | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            dt = parse_xt_timestamp(row.get("time") or row.get("timestamp"))
            if dt is None:
                continue
            dt = dt.astimezone(CHINA_TZ)
            if latest is None or dt > latest:
                latest = dt
        return latest

    @staticmethod
    def _refresh_start_time(stale: list[tuple[str, date | None]], fallback_start: str) -> str:
        known_dates = [last_day for _, last_day in stale if last_day is not None]
        if not known_dates:
            return fallback_start
        return min(known_dates).strftime("%Y%m%d000000")

    def _download_history(
        self,
        stock_list: list[str],
        period: str,
        start_time: str,
        end_time: str,
        *,
        incrementally: bool | None = None,
    ) -> Any:
        callback = self._make_download_progress_callback("xtdata.download_history_data2")
        if hasattr(self.xtdata, "download_history_data2"):
            kwargs: dict[str, Any] = {"callback": callback}
            if incrementally is not None:
                kwargs["incrementally"] = incrementally
            return self.xtdata.download_history_data2(stock_list, period, start_time, end_time, **kwargs)
        if hasattr(self.xtdata, "download_history_data"):
            result = None
            for xt_symbol in stock_list:
                kwargs = {}
                if incrementally is not None:
                    kwargs["incrementally"] = incrementally
                result = self.xtdata.download_history_data(xt_symbol, period, start_time, end_time, **kwargs)
            return result
        raise RuntimeError("xtdata history download API is unavailable")

    def _log_data_fetch_request(self, rpc_name: str, args: tuple, kwargs: dict) -> None:
        period = kwargs.get("period", "")
        dividend_type = kwargs.get("dividend_type", "")
        stock_list = kwargs.get("stock_list") or (list(args[0]) if args else [])
        start_time = kwargs.get("start_time", "")
        end_time = kwargs.get("end_time", "")
        count = kwargs.get("count", "")
        fill_data = kwargs.get("fill_data", "")
        field_list = kwargs.get("field_list") or []

        period_label = {
            "tick": "Tick", "1m": "分钟线(1m)", "5m": "分钟线(5m)", "15m": "分钟线(15m)",
            "30m": "分钟线(30m)", "1h": "小时线(1h)", "1d": "日线(1d)", "1w": "周线(1w)",
            "1mon": "月线(1mon)",
        }.get(str(period), str(period))

        adjust_label = {
            "front": "前复权", "back": "后复权", "none": "不复权", "": "不复权(默认)",
        }.get(str(dividend_type), str(dividend_type))

        lines = [
            f">>> MiniQMT 数据请求 [{rpc_name}] ...",
            f"    股票列表  : {stock_list}",
            f"    周期      : {period_label}",
            f"    复权模式  : {adjust_label}",
            f"    开始时间  : {start_time}",
            f"    结束时间  : {end_time}",
            f"    条数限制  : {count}",
            f"    填充空值  : {fill_data}",
            f"    字段列表  : {field_list}",
        ]
        print("[XTQ Bridge] " + "\n[XTQ Bridge] ".join(lines))

        # ── 坑1：伪复权防护 ───────────────────────────────────────────────
        # 若请求复权数据但本地缺少除权表，QMT 不报错，静默返回不复权原始数据。
        # 在每次请求前确保财务数据已下载，避免回测/实盘中吃到除权跳空。
        if str(dividend_type) in ("front", "back") and stock_list:
            self._ensure_financial_data(stock_list)

        # ── 坑2：量价不匹配警告 ──────────────────────────────────────────
        # QMT 前/后复权只调整价格，部分版本不同步调整成交量，导致 amount 对不上。
        # 若策略强依赖精确量价关系（如主力资金净流入），建议用 none + 手动复权。
        if str(dividend_type) in ("front", "back") and "volume" in field_list:
            print(
                "[XTQ Bridge] [WARN][market_data] 量价不匹配风险："
                " QMT 复权只调整价格(open/high/low/close)，"
                "成交量(volume)在部分版本不按比例还原，"
                "amount 可能与 price×volume 不一致。"
                " 若策略强依赖量价关系，建议改用 dividend_type='none' + 手动复权。"
            )

    def _ensure_financial_data(self, stock_list: list[str]) -> None:
        """下载缺失的财务除权数据，防止 QMT 静默返回伪复权数据。

        QMT 行为：若本地无除权表，即使 dividend_type='front'/'back' 也不报错，
        直接返回不复权原始数据，导致回测出现除权跳空（未来函数）。
        """
        missing = [s for s in stock_list if s not in self._financial_data_ensured]
        if not missing:
            return

        if hasattr(self.xtdata, "download_financial_data2"):
            print(
                f"[XTQ Bridge] [INFO][market_data] 复权请求前自动补充财务除权数据"
                f" (共 {len(missing)} 只): {missing} ..."
            )
            try:
                self.xtdata.download_financial_data2(
                    missing,
                    callback=self._make_download_progress_callback("xtdata.download_financial_data2"),
                )
                self._financial_data_ensured.update(missing)
                print(
                    f"[XTQ Bridge] [INFO][market_data] 财务除权数据下载已发起"
                    f" symbols={missing} mode=async ..."
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[XTQ Bridge] [WARN][market_data] 财务除权数据下载失败"
                    f" symbols={missing} error={exc} ..."
                )
            return

        if not hasattr(self.xtdata, "download_financial_data"):
            print(
                f"[XTQ Bridge] [WARN][market_data] 当前 xtquant 版本无 download_financial_data，"
                f"无法自动补充除权表，复权数据可能不准确。股票: {missing} ..."
            )
            self._financial_data_ensured.update(missing)
            return

        print(
            f"[XTQ Bridge] [WARN][market_data] 当前仅支持阻塞版财务除权下载，"
            f"可能导致 MiniQMT 卡住。股票: {missing} ..."
        )
        try:
            self.xtdata.download_financial_data(missing)
            self._financial_data_ensured.update(missing)
            print(f"[XTQ Bridge] [INFO][market_data] 财务除权数据下载完成: {missing} ...")
        except Exception as exc:  # noqa: BLE001
            print(
                f"[XTQ Bridge] [WARN][market_data] 财务除权数据下载失败: {exc}。"
                f" 复权数据可能不准确，请在 QMT 客户端「数据管理」中手动补充除权表。..."
            )

    def _log_data_fetch_result(self, rpc_name: str, result: Any, kwargs: dict) -> None:
        period = kwargs.get("period", "")
        stock_list = kwargs.get("stock_list") or []

        if not isinstance(result, dict) or result.get("__type__") == "dataframe":
            print(f"[XTQ Bridge] <<< MiniQMT 返回 [{rpc_name}]: {self._summarize_xtdata_result(result)} ...")
            return

        lines = [f"<<< MiniQMT 返回 [{rpc_name}]  周期={period} ..."]
        for sym in stock_list:
            val = result.get(sym)
            if val is None:
                lines.append(f"    {sym}: 无数据")
                continue

            if hasattr(val, "shape"):
                # pandas DataFrame
                rows, cols = val.shape
                columns = list(val.columns)
                first_time = last_time = ""
                if "time" in val.columns and len(val) > 0:
                    first_time = val["time"].iloc[0]
                    last_time = val["time"].iloc[-1]
                lines.append(
                    f"    {sym}: DataFrame {rows}行 x {cols}列  "
                    f"列={columns}  "
                    f"首条time={first_time}  末条time={last_time}"
                )
            elif isinstance(val, dict) and val.get("__type__") == "dataframe":
                inner = val.get("data", {})
                columns = inner.get("columns", [])
                data = inner.get("data", [])
                rows = len(data)
                first_time = data[0][columns.index("time")] if data and "time" in columns else ""
                last_time = data[-1][columns.index("time")] if data and "time" in columns else ""
                lines.append(
                    f"    {sym}: DataFrame(序列化) {rows}行  "
                    f"列={columns}  "
                    f"首条time={first_time}  末条time={last_time}"
                )
            elif isinstance(val, list):
                rows = len(val)
                first_time = val[0].get("time", "") if rows > 0 and isinstance(val[0], dict) else ""
                last_time = val[-1].get("time", "") if rows > 0 and isinstance(val[-1], dict) else ""
                lines.append(f"    {sym}: list {rows}条  首条time={first_time}  末条time={last_time}")
            else:
                lines.append(f"    {sym}: {type(val).__name__} {repr(val)[:120]}")

        print("[XTQ Bridge] " + "\n[XTQ Bridge] ".join(lines))

    def _csv_supplement_xtdata_result(self, result: Any, kwargs: dict) -> Any:
        """Post-process a get_market_data_ex / get_local_data result and fill gaps from CSV.

        Dates that QMT did not return at all are filled from CSV so the caller
        has OHLCV coverage for those bars.  The supplemented data is re-serialised
        back to the same ``{"__type__": "dataframe", ...}`` envelope the client
        expects, so it is handled identically to pure-QMT results.
        """
        stock_list = kwargs.get("stock_list") or []
        start_time = str(kwargs.get("start_time") or "")
        end_time = str(kwargs.get("end_time") or "")
        period = str(kwargs.get("period") or "")
        dividend_type = kwargs.get("dividend_type", None)

        # Single serialized dataframe envelope — not a per-symbol dict; skip.
        if isinstance(result, dict) and result.get("__type__") == "dataframe":
            return result

        if not isinstance(result, dict):
            return result

        changed = False
        for xt_symbol in stock_list:
            symbol_data = result.get(xt_symbol)
            orig_columns: list[str] | None = None

            if symbol_data is None:
                qmt_rows: list = []
            elif hasattr(symbol_data, "to_dict"):
                qmt_rows = symbol_data.to_dict("records")
            elif isinstance(symbol_data, dict) and symbol_data.get("__type__") == "dataframe":
                inner = symbol_data.get("data", {})
                orig_columns = inner.get("columns") or []
                data_lists = inner.get("data", [])
                qmt_rows = [dict(zip(orig_columns, row)) for row in data_lists] if orig_columns and data_lists else []
            elif isinstance(symbol_data, list):
                qmt_rows = symbol_data
            else:
                continue

            supplemented = self._supplement_from_csv(
                xt_symbol, qmt_rows, start_time, end_time, "market",
                period=period, dividend_type=dividend_type,
            )
            if len(supplemented) != len(qmt_rows):
                # Re-serialise back to the DataFrame envelope the client expects.
                # CSV rows carry datetime objects for 'time'; normalise to unix-ms
                # integers to match the QMT format.
                columns = orig_columns or (list(qmt_rows[0].keys()) if qmt_rows else
                                           ["time", "open", "high", "low", "close", "volume", "amount"])

                def _norm_time(t: Any) -> Any:
                    if hasattr(t, "timestamp"):
                        return int(t.timestamp() * 1000)
                    return t

                rows_data = [
                    [_norm_time(row["time"]) if col == "time" else row.get(col)
                     for col in columns]
                    for row in supplemented
                ]
                result[xt_symbol] = {
                    "__type__": "dataframe",
                    "orient": "split",
                    "data": {
                        "index": list(range(len(rows_data))),
                        "columns": columns,
                        "data": rows_data,
                    },
                }
                changed = True

        if changed:
            self.log_info("rpc", "csv supplement applied", period=period, symbols=len(stock_list))

        return result

    def initialize_market_data(self) -> None:
        self.xtdata.connect()
        self.log_info("lifecycle", "market data connected")

    def initialize_trading(self) -> None:
        import os as _os
        session_id = int(self.xt_config.get("session_id") or 0)

        # ── 预检：userdata 路径必须存在 ──────────────────────────────────
        if not _os.path.isdir(self.userdata_path):
            raise RuntimeError(
                f"userdata 目录不存在: {self.userdata_path}\n"
                f"  请检查 config.user.json 中 xt.qmt_path 是否正确指向 MiniQMT 安装根目录。\n"
                f"  当前配置的 qmt_path: {self.qmt_root}"
            )

        self.log_info("lifecycle", "trading init", userdata_path=self.userdata_path, session_id=session_id)
        self.xt_trader = self.xttrader_class(self.userdata_path, session_id)
        self.xt_trader.register_callback(self.callback_router)
        self.xt_trader.start()

        # connect() 在 start() 之后立刻调用常返回 -1（交易线程尚未就绪），
        # 第一次失败重试几次即可成功。可用 xt.connect_retries / connect_retry_interval 调整。
        import time as _time
        retries = max(1, int(self.xt_config.get("connect_retries", 5) or 5))
        retry_interval = float(self.xt_config.get("connect_retry_interval", 1.0) or 1.0)
        connect_result = -1
        for attempt in range(1, retries + 1):
            connect_result = self.xt_trader.connect()
            if connect_result == 0:
                if attempt > 1:
                    self.log_info("lifecycle", "xttrader connect succeeded after retry", attempt=attempt)
                break
            self.log_warning(
                "lifecycle",
                "xttrader connect failed; retrying",
                code=connect_result,
                attempt=attempt,
                retries=retries,
            )
            if attempt < retries:
                _time.sleep(retry_interval)
        if connect_result != 0:
            raise RuntimeError(
                f"xttrader connect failed (code={connect_result}) after {retries} attempts\n"
                f"  userdata_path: {self.userdata_path}\n"
                f"  session_id: {session_id}\n"
                f"  常见原因:\n"
                f"    1. MiniQMT 客户端未运行或未登录 — 请先启动并登录 MiniQMT\n"
                f"    2. userdata 路径与 MiniQMT 实际路径不符 — 检查 xt.qmt_path\n"
                f"    3. session_id 冲突 — 修改 config.user.json 中 xt.session_id 为其他数值（如 1）"
            )

        subscribe_result = self.xt_trader.subscribe(self.account)
        if subscribe_result != 0:
            raise RuntimeError(f"xttrader subscribe failed: {subscribe_result}")

        self.log_info(
            "lifecycle",
            "trading connected",
            account_id=self.xt_config["account_id"],
            account_type=self.xt_config["account_type"],
            userdata_path=self.userdata_path,
        )

    def refresh_snapshots(self) -> None:
        account_count = 0
        position_count = 0
        order_count = 0
        trade_count = 0

        asset = self.xt_trader.query_stock_asset(self.account)
        if asset:
            self.handle_account(self.translator.translate_account(asset))
            account_count += 1

        for position in self.xt_trader.query_stock_positions(self.account) or []:
            self.handle_position(self.translator.translate_position(position))
            self.ensure_contract(position.stock_code)
            position_count += 1

        for order in self.xt_trader.query_stock_orders(self.account) or []:
            self.handle_order(self.translator.translate_order(order), str(getattr(order, "order_sysid", "") or ""))
            self.ensure_contract(order.stock_code)
            order_count += 1

        for trade in self.xt_trader.query_stock_trades(self.account) or []:
            self.handle_trade(self.translator.translate_trade(trade))
            self.ensure_contract(trade.stock_code)
            trade_count += 1

        self.log_info(
            "snapshot",
            "snapshot loaded",
            accounts=account_count,
            positions=position_count,
            orders=order_count,
            trades=trade_count,
        )

    def publish_event(self, topic: str, event: Event) -> None:
        self.publisher.enqueue(topic, event)

    def publish_data(self, base_topic: str, specific_topic: str | None, data: Any) -> None:
        self.publish_event(base_topic, Event(base_topic, data))
        if specific_topic:
            self.publish_event(specific_topic, Event(specific_topic, data))

    def publish_log(self, message: str) -> None:
        log = LogData(gateway_name=GATEWAY_NAME, msg=message)
        self.publish_event(EVENT_LOG, Event(EVENT_LOG, log))

    def handle_tick(self, tick) -> None:
        self.ticks[tick.vt_symbol] = tick
        self.l1_ticks[tick.vt_symbol] = self._tick_to_l1_payload(tick)
        self.publish_data(EVENT_TICK, EVENT_TICK + tick.vt_symbol, tick)

    @staticmethod
    def _tick_to_l1_payload(tick: Any) -> dict[str, Any]:
        payload = {
            "symbol": getattr(tick, "symbol", ""),
            "exchange": getattr(getattr(tick, "exchange", None), "value", getattr(tick, "exchange", "")),
            "vt_symbol": getattr(tick, "vt_symbol", ""),
            "datetime": getattr(tick, "datetime", None),
            "last_price": getattr(tick, "last_price", 0.0),
            "volume": getattr(tick, "volume", 0.0),
            "turnover": getattr(tick, "turnover", 0.0),
            "open_price": getattr(tick, "open_price", 0.0),
            "high_price": getattr(tick, "high_price", 0.0),
            "low_price": getattr(tick, "low_price", 0.0),
            "pre_close": getattr(tick, "pre_close", 0.0),
        }
        for index in range(1, 6):
            payload[f"bid_price_{index}"] = getattr(tick, f"bid_price_{index}", 0.0)
            payload[f"ask_price_{index}"] = getattr(tick, f"ask_price_{index}", 0.0)
            payload[f"bid_volume_{index}"] = getattr(tick, f"bid_volume_{index}", 0.0)
            payload[f"ask_volume_{index}"] = getattr(tick, f"ask_volume_{index}", 0.0)
        return serialize_xtdata_result(payload)

    def handle_order(self, order, system_orderid: str = "") -> None:
        self.orders[order.vt_orderid] = order
        if system_orderid:
            self.local_order_sysid_map[order.orderid] = system_orderid
        self.publish_data(EVENT_ORDER, EVENT_ORDER + order.vt_orderid, order)
        self.log_info(
            "order",
            "order update",
            vt_orderid=order.vt_orderid,
            status=order.status.name,
            symbol=order.vt_symbol,
            volume=order.volume,
            traded=order.traded,
        )

    def handle_trade(self, trade) -> None:
        self.trades[trade.vt_tradeid] = trade
        self.publish_data(EVENT_TRADE, EVENT_TRADE + trade.vt_symbol, trade)
        self.log_info(
            "trade",
            "trade update",
            vt_tradeid=trade.vt_tradeid,
            orderid=trade.vt_orderid,
            symbol=trade.vt_symbol,
            price=trade.price,
            volume=trade.volume,
        )

    def handle_position(self, position) -> None:
        self.positions[position.vt_positionid] = position
        self.publish_data(EVENT_POSITION, EVENT_POSITION + position.vt_symbol, position)
        self.log_info(
            "position",
            "position update",
            vt_positionid=position.vt_positionid,
            volume=self._safe_attr(position, "volume", ""),
            frozen=self._safe_attr(position, "frozen", ""),
        )

    def handle_account(self, account) -> None:
        self.accounts[account.vt_accountid] = account
        self.publish_data(EVENT_ACCOUNT, EVENT_ACCOUNT + account.vt_accountid, account)
        self.log_info(
            "account",
            "account update",
            vt_accountid=account.vt_accountid,
            balance=self._safe_attr(account, "balance", ""),
            frozen=self._safe_attr(account, "frozen", ""),
        )

    def handle_contract(self, contract) -> None:
        self.contracts[contract.vt_symbol] = contract
        self.publish_event(EVENT_CONTRACT, Event(EVENT_CONTRACT, contract))
        self.log_debug("contract", "contract cached", vt_symbol=contract.vt_symbol, name=contract.name)

    def ensure_contract(self, xt_symbol: str):
        symbol, exchange = xt_symbol.split(".")
        if exchange == "SH":
            vt_symbol = f"{symbol}.SSE"
        elif exchange == "SZ":
            vt_symbol = f"{symbol}.SZSE"
        elif exchange == "BJ":
            vt_symbol = f"{symbol}.BSE"
        elif exchange == "IF":
            vt_symbol = f"{symbol}.CFFEX"
        elif exchange == "SF":
            vt_symbol = f"{symbol}.SHFE"
        elif exchange == "DF":
            vt_symbol = f"{symbol}.DCE"
        elif exchange == "ZF":
            vt_symbol = f"{symbol}.CZCE"
        elif exchange == "INE":
            vt_symbol = f"{symbol}.INE"
        elif exchange == "GF":
            vt_symbol = f"{symbol}.GFEX"
        elif exchange == "SHO":
            vt_symbol = f"{symbol}.SSE"
        elif exchange == "SZO":
            vt_symbol = f"{symbol}.SZSE"
        else:
            vt_symbol = xt_symbol
        existing = self.contracts.get(vt_symbol)
        if existing:
            return existing

        detail = self.xtdata.get_instrument_detail(xt_symbol, True) or {}
        contract = self.translator.translate_contract(xt_symbol, detail)
        self.handle_contract(contract)
        return contract

    def subscribe(self, req) -> None:
        xt_symbol = vnpy_symbol_to_xt(req.symbol, req.exchange)
        self.ensure_contract(xt_symbol)
        if req.vt_symbol not in self.subscriptions:
            seq = self.xtdata.subscribe_quote(xt_symbol, period="tick", callback=self.callback_router.on_tick_data)
            self.subscriptions[req.vt_symbol] = seq
            self.log_info("market_data", "subscribe success", vt_symbol=req.vt_symbol, xt_symbol=xt_symbol, seq=seq)
        else:
            self.log_debug("market_data", "subscribe skipped", vt_symbol=req.vt_symbol, reason="already-subscribed")

    @staticmethod
    def _infer_exchange_from_symbol(symbol: str) -> Exchange:
        code = str(symbol or "").strip().zfill(6)
        if code.startswith("6"):
            return Exchange.SSE
        if code.startswith(("4", "8")):
            return Exchange.BSE
        return Exchange.SZSE

    @staticmethod
    def _normalize_l1_xt_symbol(symbol: str, exchange: Any = None) -> tuple[str, str]:
        raw = str(symbol or "").strip()
        if "." in raw:
            code, suffix = raw.split(".", 1)
            suffix_upper = suffix.upper()
            if suffix_upper in {"SH", "SZ", "BJ"}:
                xt_suffix = suffix_upper
                vt_suffix = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[xt_suffix]
            else:
                vt_suffix = suffix_upper
                xt_suffix = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}.get(vt_suffix, suffix_upper)
            code = code.zfill(6) if code.isdigit() else code
            return f"{code}.{xt_suffix}", f"{code}.{vt_suffix}"

        code = raw.zfill(6) if raw.isdigit() else raw
        ex = exchange or XtQuantBridge._infer_exchange_from_symbol(code)
        xt_symbol = vnpy_symbol_to_xt(code, ex) if isinstance(ex, Exchange) else f"{code}.{str(ex).upper()}"
        vt_suffix = getattr(ex, "value", str(ex))
        return xt_symbol, f"{code}.{vt_suffix}"

    @staticmethod
    def _full_tick_has_data(payload: Any) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False
        try:
            if float(payload.get("lastPrice", 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
        return bool(payload.get("timetag") or payload.get("time") or payload.get("timestamp"))

    def _prime_l1_subscription(self, xt_symbol: str, vt_symbol: str) -> None:
        """Subscribe a code once so get_full_tick has live data to return.

        QMT's get_full_tick returns {} or all-zero stubs for codes that were
        never subscribed; subscribing wakes the quote feed and also warms
        self.l1_ticks via on_tick_data for subsequent polls.
        """
        if vt_symbol in self.subscriptions:
            return
        if not hasattr(self.xtdata, "subscribe_quote"):
            return
        try:
            seq = self.xtdata.subscribe_quote(xt_symbol, period="tick", callback=self.callback_router.on_tick_data)
            self.subscriptions[vt_symbol] = seq
            self.log_info("market_data", "l1 subscribe primed", vt_symbol=vt_symbol, xt_symbol=xt_symbol, seq=seq)
        except Exception as exc:  # noqa: BLE001
            self.log_warning("market_data", "l1 subscribe prime failed", xt_symbol=xt_symbol, error=exc)

    def _fetch_full_tick_payload(self, xt_symbol: str, vt_symbol: str) -> dict[str, Any] | None:
        def _extract() -> dict[str, Any] | None:
            result = self.xtdata.get_full_tick([xt_symbol]) or {}
            payload = result.get(xt_symbol) if isinstance(result, dict) else None
            return payload if self._full_tick_has_data(payload) else None

        payload = _extract()
        if payload is not None:
            return payload

        # Prime via subscription and retry briefly: live tick data arrives
        # asynchronously after the first subscribe.
        self._prime_l1_subscription(xt_symbol, vt_symbol)
        import time as _time
        for _ in range(5):
            _time.sleep(0.2)
            cached = self.l1_ticks.get(vt_symbol)
            if cached:
                return None  # cache already warmed by on_tick_data; caller returns it
            payload = _extract()
            if payload is not None:
                return payload
        return None

    def get_l1_tick(self, symbol: str, exchange: Any = None) -> dict[str, Any] | None:
        xt_symbol, vt_symbol = self._normalize_l1_xt_symbol(symbol, exchange)
        cached = self.l1_ticks.get(vt_symbol)
        if cached:
            return cached

        if not hasattr(self.xtdata, "get_full_tick"):
            return None
        payload = self._fetch_full_tick_payload(xt_symbol, vt_symbol)
        if payload is None:
            # _fetch_full_tick_payload may have warmed the cache via subscription.
            return self.l1_ticks.get(vt_symbol)
        contract = self.ensure_contract(xt_symbol)
        tick = self.translator.translate_tick(xt_symbol, payload, contract)
        built = self._tick_to_l1_payload(tick)
        self.l1_ticks[vt_symbol] = built
        self.ticks[vt_symbol] = tick
        return built

    def send_order(self, req: OrderRequest) -> str:
        payload = self.translator.order_request_to_xt(req)
        local_orderid = f"XTQ{next(self._order_counter):010d}"
        self.log_info(
            "order",
            "send_order",
            symbol=req.symbol,
            exchange=req.exchange.value,
            direction=req.direction.value,
            type=req.type.value,
            price=req.price,
            volume=req.volume,
            local_orderid=local_orderid,
        )
        self.xt_trader.order_stock_async(
            self.account,
            payload["stock_code"],
            payload["order_type"],
            payload["volume"],
            payload["price_type"],
            payload["price"],
            strategy_name=payload["reference"],
            order_remark=local_orderid,
        )
        order = req.create_order_data(local_orderid, GATEWAY_NAME)
        self.handle_order(order)
        return order.vt_orderid

    def cancel_order(self, req) -> None:
        sysid = self.local_order_sysid_map.get(req.orderid)
        if sysid:
            payload = self.translator.cancel_request_to_xt(req)
            self.log_info("order", "cancel_order", orderid=req.orderid, via_="sysid", sysid=sysid)
            self.xt_trader.cancel_order_stock_sysid_async(self.account, payload["market"], sysid)
            return
        if str(req.orderid).isdigit():
            self.log_info("order", "cancel_order", orderid=req.orderid, via_="orderid")
            self.xt_trader.cancel_order_stock_async(self.account, int(req.orderid))
            return
        self.log_warning("order", "cancel ignored", orderid=req.orderid, reason="unknown-local-orderid")

    def query_history(self, req):
        xt_symbol = vnpy_symbol_to_xt(req.symbol, req.exchange)
        self.ensure_contract(xt_symbol)
        if getattr(req, "interval", None) == Interval.TICK:
            self.log_warning(
                "history",
                "tick history query skipped; use get_l1_tick/get_full_tick for L1 snapshot",
                vt_symbol=req.vt_symbol,
            )
            return []
        xt_interval = map_vnpy_interval_to_xt(req.interval)
        start_time = format_history_time(req.start)
        end_time = format_history_time(req.end)

        query_kwargs = {
            "field_list": ["time", "open", "high", "low", "close", "volume", "amount", "openInterest"],
            "stock_list": [xt_symbol],
            "period": xt_interval,
            "start_time": start_time,
            "end_time": end_time,
            "count": -1,
        }
        source = "market"
        rows: list = []
        local_rows: list = []
        market_rows: list = []
        csv_stats: dict[str, list] = {"rows": []}

        download_args = ([xt_symbol], xt_interval, start_time, end_time)
        if hasattr(self.xtdata, "download_history_data2"):
            print(
                f"[XTQ Bridge] [INFO][history] MiniQMT 历史数据下载开始 "
                f"vt_symbol={req.vt_symbol} interval={xt_interval} start={start_time} end={end_time} ..."
            )
            self.xtdata.download_history_data2(*download_args)
            print(
                f"[XTQ Bridge] [INFO][history] MiniQMT 历史数据下载完成 "
                f"vt_symbol={req.vt_symbol} interval={xt_interval} start={start_time} end={end_time} ..."
            )
        elif hasattr(self.xtdata, "download_history_data"):
            print(
                f"[XTQ Bridge] [INFO][history] MiniQMT 历史数据下载开始 "
                f"vt_symbol={req.vt_symbol} interval={xt_interval} start={start_time} end={end_time} ..."
            )
            self.xtdata.download_history_data(*download_args)
            print(
                f"[XTQ Bridge] [INFO][history] MiniQMT 历史数据下载完成 "
                f"vt_symbol={req.vt_symbol} interval={xt_interval} start={start_time} end={end_time} ..."
            )

        # 1. Try local QMT cache first
        if hasattr(self.xtdata, "get_local_data"):
            print(
                f"[XTQ Bridge] [INFO][history] MiniQMT 本地缓存读取开始 "
                f"vt_symbol={req.vt_symbol} interval={xt_interval} start={start_time} end={end_time} ..."
            )
            result = self.xtdata.get_local_data(**query_kwargs)
            local_rows = self._extract_history_rows(result, xt_symbol)
            if local_rows:
                rows = list(local_rows)
                source = "local"
                print(
                    f"[XTQ Bridge] [INFO][history] MiniQMT 本地缓存读取完成 "
                    f"vt_symbol={req.vt_symbol} interval={xt_interval} count={len(rows)} ..."
                )
            else:
                print(
                    f"[XTQ Bridge] [WARN][history] MiniQMT 本地缓存无数据 "
                    f"vt_symbol={req.vt_symbol} interval={xt_interval} start={start_time} end={end_time} count=0 ..."
                )

        # 2. If local cache is empty, try QMT market data
        if not rows:
            print(
                f"[XTQ Bridge] [INFO][history] MiniQMT 行情接口读取开始 "
                f"vt_symbol={req.vt_symbol} interval={xt_interval} start={start_time} end={end_time} ..."
            )
            result = self.xtdata.get_market_data_ex(**query_kwargs)
            market_rows = self._extract_history_rows(result, xt_symbol)
            if market_rows:
                rows = list(market_rows)
                source = "market"
                print(
                    f"[XTQ Bridge] [INFO][history] MiniQMT 行情接口读取完成 "
                    f"vt_symbol={req.vt_symbol} interval={xt_interval} count={len(rows)} ..."
                )
            else:
                print(
                    f"[XTQ Bridge] [WARN][history] MiniQMT 行情接口失败 "
                    f"vt_symbol={req.vt_symbol} interval={xt_interval} start={start_time} end={end_time} count=0 ..."
                )

        # 3. Supplement any missing bars from the CSV data source
        if self.csv_source is not None:
            before = len(rows)
            print(
                f"[XTQ Bridge] [INFO][history] CSV 回退检查开始 "
                f"vt_symbol={req.vt_symbol} interval={xt_interval} source={source} count={before} ..."
            )
            rows = self._supplement_from_csv(
                xt_symbol, rows, start_time, end_time, source,
                period=xt_interval, dividend_type=None, stats=csv_stats,
            )
            if not rows:
                source = "csv"
            elif len(rows) > before:
                source = f"{source}+csv"
        elif not rows:
            print(
                f"[XTQ Bridge] [WARN][history] MiniQMT 无数据且未配置 CSV 数据源 "
                f"vt_symbol={req.vt_symbol} interval={xt_interval} start={start_time} end={end_time} ..."
            )

        self._print_history_summary(
            req.vt_symbol,
            xt_interval,
            source,
            local_rows,
            market_rows,
            csv_stats.get("rows", []),
            rows,
        )
        bars = [self.translator.translate_bar(xt_symbol, row, req.interval) for row in rows]
        self.log_info(
            "history",
            "query_history",
            vt_symbol=req.vt_symbol,
            interval=xt_interval,
            start=start_time,
            end=end_time,
            source=source,
            count=len(bars),
        )
        return bars

    def _supplement_from_csv(
        self,
        xt_symbol: str,
        qmt_rows: list,
        start_time: str,
        end_time: str,
        current_source: str,
        period: str = "1d",
        dividend_type: str | None = None,
        stats: dict[str, Any] | None = None,
    ) -> list:
        """Merge *qmt_rows* with CSV rows, filling gaps that QMT did not return."""
        csv_path = self.csv_source.csv_path_for(
            xt_symbol,
            period=period,
            adjust_type=dividend_type,
            start_time=start_time,
            end_time=end_time,
        )
        # ── helper: convert any row 'time' field to unix seconds ────────────
        def _row_unix_s(row) -> float:
            t = row.get("time")
            if t is None:
                return 0.0
            if hasattr(t, "timestamp"):
                return t.timestamp()
            s = str(int(t))
            if len(s) == 14:
                from datetime import datetime as _dt
                try:
                    return _dt.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ).timestamp()
                except Exception:
                    return 0.0
            try:
                ts = float(t)
                return ts / 1000 if ts > 1_000_000_000_000 else ts
            except Exception:
                return 0.0

        def _row_date_str(row) -> str:
            t = row.get("time")
            if t is None:
                return ""
            if hasattr(t, "strftime"):
                return t.strftime("%Y-%m-%d")
            s = str(int(t))
            if len(s) == 14:
                return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
            try:
                ts = float(t)
                if ts > 1_000_000_000_000:
                    ts /= 1000
                from datetime import datetime as _dt
                return _dt.fromtimestamp(ts, CHINA_TZ).strftime("%Y-%m-%d")
            except Exception:
                return ""

        # ── log QMT date range before reading CSV ─────────────────────────
        qmt_first = _row_date_str(qmt_rows[0]) if qmt_rows else ""
        qmt_last  = _row_date_str(qmt_rows[-1]) if qmt_rows else ""
        self.log_info(
            "history",
            "csv check",
            xt_symbol=xt_symbol,
            period=period,
            path=csv_path,
            qmt_rows=len(qmt_rows),
            qmt_range=f"{qmt_first} ~ {qmt_last}" if qmt_first else "none",
            requested=f"{start_time} ~ {end_time}",
        )
        print(
            f"[XTQ Bridge] [INFO][history] csv check  xt_symbol={xt_symbol} period={period}"
            f"  QMT={len(qmt_rows)}行 [{qmt_first} ~ {qmt_last}]"
            f"  请求范围=[{start_time} ~ {end_time}]"
            f"  path={csv_path}"
        )

        csv_rows = self.csv_source.query(
            xt_symbol, start_time, end_time, period=period, adjust_type=dividend_type
        )
        if stats is not None:
            stats["rows"] = list(csv_rows)

        csv_first = csv_rows[0]["time"].strftime("%Y-%m-%d") if csv_rows else ""
        csv_last  = csv_rows[-1]["time"].strftime("%Y-%m-%d") if csv_rows else ""

        if not csv_rows:
            print(
                f"[XTQ Bridge] [WARN][history] CSV 回退失败 xt_symbol={xt_symbol} "
                f"period={period} path={csv_path} count=0 ..."
            )
            self.log_info("history", "csv no data", xt_symbol=xt_symbol, path=csv_path)
            return qmt_rows

        if not qmt_rows:
            print(
                f"[XTQ Bridge] [WARN][history] MiniQMT 失败后改用 CSV 数据 xt_symbol={xt_symbol} "
                f"period={period} csv_rows={len(csv_rows)} [{csv_first} ~ {csv_last}] path={csv_path} ..."
            )
            self.log_info("history", "csv fallback used", xt_symbol=xt_symbol,
                          csv_rows=len(csv_rows), csv_range=f"{csv_first} ~ {csv_last}", path=csv_path)
            return csv_rows

        # Fill whole missing trading days from CSV.  Date-level matching avoids
        # mixing two providers inside the same intraday session.
        qmt_dates = {_row_date_str(r) for r in qmt_rows if _row_date_str(r)}
        qmt_min_date = min(qmt_dates, default="")
        qmt_max_date = max(qmt_dates, default="")
        missing = [r for r in csv_rows if r["time"].strftime("%Y-%m-%d") not in qmt_dates]

        csv_used_first = missing[0]["time"].strftime("%Y-%m-%d") if missing else ""
        csv_used_last  = missing[-1]["time"].strftime("%Y-%m-%d") if missing else ""

        if not missing:
            print(
                f"[XTQ Bridge] [INFO][history] CSV 无需补齐 xt_symbol={xt_symbol} period={period}"
                f"  QMT=[{qmt_min_date} ~ {qmt_max_date}] CSV=[{csv_first} ~ {csv_last}] 已完全覆盖"
            )
            return qmt_rows

        # ── log per-source ranges before merging ─────────────────────────
        print(
            f"[XTQ Bridge] [INFO][history] CSV 补齐缺失数据 xt_symbol={xt_symbol} period={period}\n"
            f"[XTQ Bridge]   [1] QMT   : {len(qmt_rows):>7}行  [{qmt_min_date} ~ {qmt_max_date}]\n"
            f"[XTQ Bridge]   [2] CSV补充: {len(missing):>7}行  [{csv_used_first} ~ {csv_used_last}]\n"
            f"[XTQ Bridge]   [3] 合并后 : {len(qmt_rows) + len(missing):>7}行  "
            f"[{min(csv_used_first, qmt_min_date)} ~ {max(csv_used_last, qmt_max_date)}]"
        )
        self.log_info(
            "history",
            "csv supplement",
            xt_symbol=xt_symbol,
            qmt_rows=len(qmt_rows),
            qmt_range=f"{qmt_min_date} ~ {qmt_max_date}",
            missing_from_csv=len(missing),
            csv_range=f"{csv_used_first} ~ {csv_used_last}",
            merged_total=len(qmt_rows) + len(missing),
            merged_range=f"{min(csv_used_first, qmt_min_date)} ~ {max(csv_used_last, qmt_max_date)}",
        )
        # CSV rows come first (older), QMT rows follow (newer); sort by full
        # timestamp so minute bars within each day are correctly ordered.
        combined = missing + qmt_rows
        combined.sort(key=_row_unix_s)
        return combined

    def register_client(self, client_name: str, client_meta: dict[str, Any] | None = None) -> bool:
        meta = client_meta or {}
        self.registered_clients[client_name] = meta
        self.log_info("rpc", "client registered", client_name=client_name, **meta)
        return True
