from __future__ import annotations

from itertools import count
from typing import Any

from xtquant import xtdata, xtdatacenter
from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount
from vnpy.event import Event
from vnpy.rpc import RpcServer
from vnpy.trader.constant import Interval
from vnpy.trader.event import EVENT_ACCOUNT, EVENT_CONTRACT, EVENT_LOG, EVENT_ORDER, EVENT_POSITION, EVENT_TICK, EVENT_TRADE
from vnpy.trader.object import LogData, OrderRequest

from .callback_router import XtQuantCallbackRouter
from .csv_data_source import CsvDataSource
from .event_publisher import EventPublisher
from .rpc_handler import RpcRequestHandler
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

        self.rpc_server = rpc_server or RpcServer()
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

        try:
            self.initialize_market_data()
            self.initialize_trading()
            self.refresh_snapshots()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
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

    def register_rpc(self) -> None:
        for name in (
            "register_client",
            "subscribe",
            "send_order",
            "cancel_order",
            "query_history",
            "get_tick",
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

    def call_xtdata(self, rpc_name: str, *args, **kwargs):
        if rpc_name in self._DATA_FETCH_METHODS:
            self._log_data_fetch_request(rpc_name, args, kwargs)
        else:
            arg_summary = repr(args)[:200] if args else ""
            kwarg_summary = " ".join(f"{k}={repr(v)[:80]}" for k, v in kwargs.items()) if kwargs else ""
            self.log_info("rpc", "xtdata rpc start ...", method=rpc_name, args=arg_summary, kwargs=kwarg_summary)

        if rpc_name in ("xtdata.download_history_data2", "xtdata.download_history_data") and "callback" not in kwargs:
            kwargs = {**kwargs, "callback": self._make_download_progress_callback(rpc_name)}
        result = self.xtdata_executor.call(rpc_name, *args, **kwargs)

        if rpc_name in self._DATA_FETCH_METHODS:
            self._log_data_fetch_result(rpc_name, result, kwargs)
        else:
            self.log_info("rpc", "xtdata rpc done ...", method=rpc_name, result=self._summarize_xtdata_result(result))

        if self.csv_source is not None and rpc_name in self._DATA_FETCH_METHODS:
            result = self._csv_supplement_xtdata_result(result, kwargs)

        return result

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

        The CSV source only contains daily bars.  For any period (including 1m/1h),
        dates that QMT did not return at all are filled with the corresponding daily
        bar from CSV so the caller at least has OHLCV coverage for those days.
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
            if symbol_data is None:
                qmt_rows: list = []
            elif hasattr(symbol_data, "to_dict"):
                qmt_rows = symbol_data.to_dict("records")
            elif isinstance(symbol_data, dict) and symbol_data.get("__type__") == "dataframe":
                inner = symbol_data.get("data", {})
                columns = inner.get("columns", [])
                data_lists = inner.get("data", [])
                qmt_rows = [dict(zip(columns, row)) for row in data_lists] if columns and data_lists else []
            elif isinstance(symbol_data, list):
                qmt_rows = symbol_data
            else:
                continue

            supplemented = self._supplement_from_csv(
                xt_symbol, qmt_rows, start_time, end_time, "market",
                period=period, dividend_type=dividend_type,
            )
            if len(supplemented) != len(qmt_rows):
                result[xt_symbol] = supplemented
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

        connect_result = self.xt_trader.connect()
        if connect_result != 0:
            raise RuntimeError(
                f"xttrader connect failed (code={connect_result})\n"
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
        self.publish_data(EVENT_TICK, EVENT_TICK + tick.vt_symbol, tick)

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
        self.log_info(
            "history",
            "csv check",
            xt_symbol=xt_symbol,
            period=period,
            path=csv_path,
            qmt_rows=len(qmt_rows),
            start=start_time,
            end=end_time,
        )
        csv_rows = self.csv_source.query(
            xt_symbol, start_time, end_time, period=period, adjust_type=dividend_type
        )
        if stats is not None:
            stats["rows"] = list(csv_rows)
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
                f"period={period} csv_rows={len(csv_rows)} path={csv_path} ..."
            )
            self.log_info("history", "csv fallback used", xt_symbol=xt_symbol, csv_rows=len(csv_rows), path=csv_path)
            return csv_rows

        # Build a set of calendar dates already covered by QMT data.
        # QMT rows may carry 'time' as a datetime, a 14-digit int (YYYYMMDDHHMMSS),
        # or a unix-ms integer — extract just the YYYY-MM-DD portion.
        def _row_date(row) -> str:
            t = row.get("time")
            if t is None:
                return ""
            if hasattr(t, "strftime"):
                return t.strftime("%Y-%m-%d")
            # QMT 14-digit int timestamp: YYYYMMDDHHMMSS
            s = str(int(t))
            if len(s) == 14:
                return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
            # unix-ms or unix-s integer
            try:
                ts = float(t)
                if ts > 1_000_000_000_000:
                    ts /= 1000
                from datetime import datetime as _dt
                return _dt.fromtimestamp(ts, CHINA_TZ).strftime("%Y-%m-%d")
            except Exception:
                return ""

        qmt_dates = {_row_date(r) for r in qmt_rows}
        qmt_dates.discard("")

        missing = [r for r in csv_rows if r["time"].strftime("%Y-%m-%d") not in qmt_dates]
        if not missing:
            return qmt_rows

        print(
            f"[XTQ Bridge] [INFO][history] CSV 补齐缺失数据 xt_symbol={xt_symbol} "
            f"period={period} missing={len(missing)} path={csv_path} ..."
        )
        self.log_info(
            "history",
            "csv supplement",
            xt_symbol=xt_symbol,
            qmt_rows=len(qmt_rows),
            missing_from_csv=len(missing),
        )
        combined = qmt_rows + missing
        combined.sort(key=lambda r: _row_date(r))
        return combined

    def register_client(self, client_name: str, client_meta: dict[str, Any] | None = None) -> bool:
        meta = client_meta or {}
        self.registered_clients[client_name] = meta
        self.log_info("rpc", "client registered", client_name=client_name, **meta)
        return True
