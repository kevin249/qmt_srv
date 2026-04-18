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
    GATEWAY_NAME,
    format_history_time,
    map_vnpy_interval_to_xt,
    normalize_qmt_root_path,
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

    @staticmethod
    def _safe_attr(data: Any, name: str, default: Any = "") -> Any:
        return getattr(data, name, default)

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

    def call_xtdata(self, rpc_name: str, *args, **kwargs):
        arg_summary = repr(args)[:200] if args else ""
        kwarg_summary = " ".join(f"{k}={repr(v)[:80]}" for k, v in kwargs.items()) if kwargs else ""
        self.log_info("rpc", "xtdata rpc start", method=rpc_name, args=arg_summary, kwargs=kwarg_summary)
        if rpc_name in ("xtdata.download_history_data2", "xtdata.download_history_data") and "callback" not in kwargs:
            kwargs = {**kwargs, "callback": self._make_download_progress_callback(rpc_name)}
        result = self.xtdata_executor.call(rpc_name, *args, **kwargs)
        self.log_info("rpc", "xtdata rpc done", method=rpc_name, result=self._summarize_xtdata_result(result))
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
        result = {}

        if hasattr(self.xtdata, "get_local_data"):
            result = self.xtdata.get_local_data(**query_kwargs)
            source = "local"

        if not result:
            result = self.xtdata.get_market_data_ex(**query_kwargs)
            source = "market"

        rows = []
        symbol_data = result.get(xt_symbol, []) if isinstance(result, dict) else []
        if hasattr(symbol_data, "to_dict"):
            rows = symbol_data.to_dict("records")
        elif isinstance(symbol_data, list):
            rows = symbol_data
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

    def register_client(self, client_name: str, client_meta: dict[str, Any] | None = None) -> bool:
        meta = client_meta or {}
        self.registered_clients[client_name] = meta
        self.log_info("rpc", "client registered", client_name=client_name, **meta)
        return True
