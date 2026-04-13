from __future__ import annotations

from typing import Any

from xtquant import xtconstant
from vnpy.trader.constant import Direction, Exchange, Interval, Offset, OrderType, Product, Status
from vnpy.trader.object import (
    AccountData,
    BarData,
    CancelRequest,
    ContractData,
    OrderData,
    OrderRequest,
    PositionData,
    TickData,
    TradeData,
)

from .utils import GATEWAY_NAME, infer_product, parse_xt_timestamp, vnpy_symbol_to_xt, xt_symbol_to_vnpy


STATUS_XT2VT: dict[int, Status] = {
    xtconstant.ORDER_UNREPORTED: Status.SUBMITTING,
    xtconstant.ORDER_WAIT_REPORTING: Status.SUBMITTING,
    xtconstant.ORDER_REPORTED: Status.NOTTRADED,
    xtconstant.ORDER_REPORTED_CANCEL: Status.CANCELLED,
    xtconstant.ORDER_PARTSUCC_CANCEL: Status.CANCELLED,
    xtconstant.ORDER_PART_CANCEL: Status.CANCELLED,
    xtconstant.ORDER_CANCELED: Status.CANCELLED,
    xtconstant.ORDER_PART_SUCC: Status.PARTTRADED,
    xtconstant.ORDER_SUCCEEDED: Status.ALLTRADED,
    xtconstant.ORDER_JUNK: Status.REJECTED,
}

DIRECTION_VT2XT: dict[tuple[Direction, Offset], int] = {
    (Direction.LONG, Offset.NONE): xtconstant.STOCK_BUY,
    (Direction.SHORT, Offset.NONE): xtconstant.STOCK_SELL,
    (Direction.LONG, Offset.OPEN): xtconstant.STOCK_OPTION_BUY_OPEN,
    (Direction.LONG, Offset.CLOSE): xtconstant.STOCK_OPTION_BUY_CLOSE,
    (Direction.SHORT, Offset.OPEN): xtconstant.STOCK_OPTION_SELL_OPEN,
    (Direction.SHORT, Offset.CLOSE): xtconstant.STOCK_OPTION_SELL_CLOSE,
}

DIRECTION_XT2VT: dict[int, tuple[Direction, Offset]] = {value: key for key, value in DIRECTION_VT2XT.items()}

POSITION_DIRECTION_XT2VT: dict[int, Direction] = {
    xtconstant.DIRECTION_FLAG_BUY: Direction.LONG,
    xtconstant.DIRECTION_FLAG_SELL: Direction.SHORT,
}

PRICE_TYPE_VT2XT: dict[tuple[Exchange, OrderType], int] = {
    (Exchange.SSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
    (Exchange.SZSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
    (Exchange.BSE, OrderType.LIMIT): xtconstant.FIX_PRICE,
}

PRICE_TYPE_XT2VT: dict[int, OrderType] = {
    xtconstant.FIX_PRICE: OrderType.LIMIT,
    50: OrderType.LIMIT,
}


class DataTranslator:
    def __init__(self, gateway_name: str = GATEWAY_NAME) -> None:
        self.gateway_name = gateway_name

    def translate_tick(self, xt_symbol: str, payload: dict[str, Any], contract: ContractData | None = None) -> TickData:
        symbol, exchange = xt_symbol_to_vnpy(xt_symbol)
        tick = TickData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            datetime=parse_xt_timestamp(payload.get("time")) or parse_xt_timestamp(payload.get("timestamp")),
            name=(contract.name if contract else payload.get("instrument_name", "")),
            volume=float(payload.get("volume", 0) or 0),
            turnover=float(payload.get("amount", 0) or 0),
            open_interest=float(payload.get("openInt", 0) or 0),
            last_price=float(payload.get("lastPrice", 0) or 0),
            open_price=float(payload.get("open", 0) or 0),
            high_price=float(payload.get("high", 0) or 0),
            low_price=float(payload.get("low", 0) or 0),
            pre_close=float(payload.get("lastClose", 0) or 0),
        )

        bid_price = payload.get("bidPrice", []) or []
        ask_price = payload.get("askPrice", []) or []
        bid_volume = payload.get("bidVol", []) or []
        ask_volume = payload.get("askVol", []) or []

        for index in range(min(5, len(bid_price))):
            setattr(tick, f"bid_price_{index + 1}", float(bid_price[index] or 0))
        for index in range(min(5, len(ask_price))):
            setattr(tick, f"ask_price_{index + 1}", float(ask_price[index] or 0))
        for index in range(min(5, len(bid_volume))):
            setattr(tick, f"bid_volume_{index + 1}", float(bid_volume[index] or 0))
        for index in range(min(5, len(ask_volume))):
            setattr(tick, f"ask_volume_{index + 1}", float(ask_volume[index] or 0))

        return tick

    def translate_order(self, xt_order: Any) -> OrderData:
        symbol, exchange = xt_symbol_to_vnpy(xt_order.stock_code)
        direction, offset = DIRECTION_XT2VT.get(getattr(xt_order, "order_type", None), (Direction.NET, Offset.NONE))
        return OrderData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            orderid=str(getattr(xt_order, "order_remark", "") or getattr(xt_order, "order_id")),
            type=PRICE_TYPE_XT2VT.get(getattr(xt_order, "price_type", xtconstant.FIX_PRICE), OrderType.LIMIT),
            direction=direction,
            offset=offset,
            price=float(getattr(xt_order, "price", 0) or 0),
            volume=float(getattr(xt_order, "order_volume", 0) or 0),
            traded=float(getattr(xt_order, "traded_volume", 0) or 0),
            status=STATUS_XT2VT.get(getattr(xt_order, "order_status", xtconstant.ORDER_UNREPORTED), Status.SUBMITTING),
            datetime=parse_xt_timestamp(getattr(xt_order, "order_time", None)),
            reference=str(getattr(xt_order, "strategy_name", "") or ""),
        )

    def translate_trade(self, xt_trade: Any) -> TradeData:
        symbol, exchange = xt_symbol_to_vnpy(xt_trade.stock_code)
        direction, offset = DIRECTION_XT2VT.get(getattr(xt_trade, "order_type", None), (Direction.NET, Offset.NONE))
        return TradeData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            orderid=str(getattr(xt_trade, "order_remark", "") or getattr(xt_trade, "order_id")),
            tradeid=str(getattr(xt_trade, "traded_id")),
            direction=direction,
            offset=offset,
            price=float(getattr(xt_trade, "traded_price", 0) or 0),
            volume=float(getattr(xt_trade, "traded_volume", 0) or 0),
            datetime=parse_xt_timestamp(getattr(xt_trade, "traded_time", None)),
        )

    def translate_position(self, xt_position: Any) -> PositionData:
        symbol, exchange = xt_symbol_to_vnpy(xt_position.stock_code)
        direction = POSITION_DIRECTION_XT2VT.get(getattr(xt_position, "direction", xtconstant.DIRECTION_FLAG_BUY), Direction.NET)
        if exchange in {Exchange.SSE, Exchange.SZSE, Exchange.BSE}:
            direction = Direction.NET
        return PositionData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            volume=float(getattr(xt_position, "volume", 0) or 0),
            frozen=float(getattr(xt_position, "frozen_volume", 0) or 0),
            price=float(getattr(xt_position, "open_price", 0) or 0),
            pnl=float(getattr(xt_position, "market_value", 0) or 0),
            yd_volume=float(getattr(xt_position, "yesterday_volume", 0) or 0),
        )

    def translate_account(self, xt_asset: Any) -> AccountData:
        account = AccountData(
            gateway_name=self.gateway_name,
            accountid=str(getattr(xt_asset, "account_id")),
            balance=float(getattr(xt_asset, "total_asset", 0) or 0),
            frozen=float(getattr(xt_asset, "frozen_cash", 0) or 0),
        )
        account.available = float(getattr(xt_asset, "cash", 0) or 0)
        return account

    def translate_contract(self, xt_symbol: str, detail: dict[str, Any]) -> ContractData:
        symbol, exchange = xt_symbol_to_vnpy(xt_symbol)
        return ContractData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            name=str(detail.get("InstrumentName") or detail.get("instrument_name") or symbol),
            product=infer_product(exchange, detail),
            size=float(detail.get("VolumeMultiple", 1) or 1),
            pricetick=float(detail.get("PriceTick", 0.01) or 0.01),
            min_volume=float(detail.get("MinVolume", 1) or 1),
            history_data=True,
        )

    def translate_bar(self, xt_symbol: str, payload: dict[str, Any], interval: Interval | None) -> BarData:
        symbol, exchange = xt_symbol_to_vnpy(xt_symbol)
        return BarData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            datetime=parse_xt_timestamp(payload.get("time") or payload.get("timestamp")),
            interval=interval,
            volume=float(payload.get("volume", 0) or 0),
            turnover=float(payload.get("amount", 0) or 0),
            open_interest=float(payload.get("openInterest", payload.get("openInt", 0)) or 0),
            open_price=float(payload.get("open", 0) or 0),
            high_price=float(payload.get("high", 0) or 0),
            low_price=float(payload.get("low", 0) or 0),
            close_price=float(payload.get("close", 0) or 0),
        )

    def order_request_to_xt(self, req: OrderRequest) -> dict[str, Any]:
        product_offset = req.offset
        if req.exchange in {Exchange.SSE, Exchange.SZSE, Exchange.BSE}:
            product_offset = Offset.NONE
        return {
            "stock_code": vnpy_symbol_to_xt(req.symbol, req.exchange),
            "order_type": DIRECTION_VT2XT[(req.direction, product_offset)],
            "price_type": PRICE_TYPE_VT2XT[(req.exchange, req.type)],
            "price": float(req.price),
            "volume": int(req.volume),
            "reference": req.reference,
        }

    def cancel_request_to_xt(self, req: CancelRequest) -> dict[str, Any]:
        market = 0 if req.exchange == Exchange.SSE else 1
        return {"market": market, "orderid": req.orderid}
