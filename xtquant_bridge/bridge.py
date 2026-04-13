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
from .event_publisher import EventPublisher
from .rpc_handler import RpcRequestHandler
from .translator import DataTranslator
from .utils import GATEWAY_NAME, format_history_time, normalize_qmt_root_path, resolve_userdata_path, vnpy_symbol_to_xt


class XtQuantBridge:
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
        self.callback_router = XtQuantCallbackRouter(self, self.translator)
        self.rpc_handler = RpcRequestHandler(self)

        self.qmt_root = normalize_qmt_root_path(self.xt_config["qmt_path"])
        self.userdata_path = resolve_userdata_path(self.qmt_root)
        self.account = self.stock_account_class(self.xt_config["account_id"], self.xt_config["account_type"])
        self.xt_trader = None
        self.running = False
        self.subscriptions: dict[str, int] = {}
        self._order_counter = count(1)
        self.local_order_sysid_map: dict[str, str] = {}

        self.ticks: dict[str, Any] = {}
        self.orders: dict[str, Any] = {}
        self.trades: dict[str, Any] = {}
        self.positions: dict[str, Any] = {}
        self.accounts: dict[str, Any] = {}
        self.contracts: dict[str, Any] = {}

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

    def initialize_market_data(self) -> None:
        self.xtdata.connect()
        self.publish_log("market data initialized")

    def initialize_trading(self) -> None:
        session_id = int(self.xt_config.get("session_id") or 0)
        self.xt_trader = self.xttrader_class(self.userdata_path, session_id)
        self.xt_trader.register_callback(self.callback_router)
        self.xt_trader.start()

        connect_result = self.xt_trader.connect()
        if connect_result != 0:
            raise RuntimeError(f"xttrader connect failed: {connect_result}")

        subscribe_result = self.xt_trader.subscribe(self.account)
        if subscribe_result != 0:
            raise RuntimeError(f"xttrader subscribe failed: {subscribe_result}")

        self.publish_log("trading initialized")

    def refresh_snapshots(self) -> None:
        asset = self.xt_trader.query_stock_asset(self.account)
        if asset:
            self.handle_account(self.translator.translate_account(asset))

        for position in self.xt_trader.query_stock_positions(self.account) or []:
            self.handle_position(self.translator.translate_position(position))
            self.ensure_contract(position.stock_code)

        for order in self.xt_trader.query_stock_orders(self.account) or []:
            self.handle_order(self.translator.translate_order(order), str(getattr(order, "order_sysid", "") or ""))
            self.ensure_contract(order.stock_code)

        for trade in self.xt_trader.query_stock_trades(self.account) or []:
            self.handle_trade(self.translator.translate_trade(trade))
            self.ensure_contract(trade.stock_code)

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

    def handle_trade(self, trade) -> None:
        self.trades[trade.vt_tradeid] = trade
        self.publish_data(EVENT_TRADE, EVENT_TRADE + trade.vt_symbol, trade)

    def handle_position(self, position) -> None:
        self.positions[position.vt_positionid] = position
        self.publish_data(EVENT_POSITION, EVENT_POSITION + position.vt_symbol, position)

    def handle_account(self, account) -> None:
        self.accounts[account.vt_accountid] = account
        self.publish_data(EVENT_ACCOUNT, EVENT_ACCOUNT + account.vt_accountid, account)

    def handle_contract(self, contract) -> None:
        self.contracts[contract.vt_symbol] = contract
        self.publish_event(EVENT_CONTRACT, Event(EVENT_CONTRACT, contract))

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

    def send_order(self, req: OrderRequest) -> str:
        payload = self.translator.order_request_to_xt(req)
        local_orderid = f"XTQ{next(self._order_counter):010d}"
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
            self.xt_trader.cancel_order_stock_sysid_async(self.account, payload["market"], sysid)
            return
        if str(req.orderid).isdigit():
            self.xt_trader.cancel_order_stock_async(self.account, int(req.orderid))
            return
        self.publish_log(f"cancel ignored, unknown local order id: {req.orderid}")

    def query_history(self, req):
        xt_symbol = vnpy_symbol_to_xt(req.symbol, req.exchange)
        self.ensure_contract(xt_symbol)
        xt_interval = "1d" if req.interval is None else {
            Interval.MINUTE: "1m",
            Interval.HOUR: "1h",
            Interval.DAILY: "1d",
        }.get(req.interval, "1d")
        self.xtdata.download_history_data(
            xt_symbol,
            xt_interval,
            format_history_time(req.start),
            format_history_time(req.end),
        )
        result = self.xtdata.get_market_data_ex(
            field_list=["time", "open", "high", "low", "close", "volume", "amount", "openInterest"],
            stock_list=[xt_symbol],
            period=xt_interval,
            start_time=format_history_time(req.start),
            end_time=format_history_time(req.end),
            count=-1,
        )
        rows = []
        symbol_data = result.get(xt_symbol, []) if isinstance(result, dict) else []
        if hasattr(symbol_data, "to_dict"):
            rows = symbol_data.to_dict("records")
        elif isinstance(symbol_data, list):
            rows = symbol_data
        return [self.translator.translate_bar(xt_symbol, row, req.interval) for row in rows]
