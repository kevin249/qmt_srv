from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class XtdataMethodSpec:
    rpc_name: str
    xtdata_name: str
    available: bool = False
    subscription: bool = False
    topic: str | None = None


CORE_SPECS: tuple[XtdataMethodSpec, ...] = (
    XtdataMethodSpec("xtdata.subscribe_quote", "subscribe_quote", subscription=True, topic="xtdata.subscribe_quote"),
    XtdataMethodSpec(
        "xtdata.subscribe_whole_quote",
        "subscribe_whole_quote",
        subscription=True,
        topic="xtdata.subscribe_whole_quote",
    ),
    XtdataMethodSpec("xtdata.unsubscribe_quote", "unsubscribe_quote"),
    XtdataMethodSpec("xtdata.run", "run"),
    XtdataMethodSpec("xtdata.subscribe_formula", "subscribe_formula", subscription=True, topic="xtdata.subscribe_formula"),
    XtdataMethodSpec("xtdata.unsubscribe_formula", "unsubscribe_formula"),
    XtdataMethodSpec("xtdata.call_formula", "call_formula"),
    XtdataMethodSpec("xtdata.get_market_data_ex", "get_market_data_ex"),
    XtdataMethodSpec("xtdata.get_market_data", "get_market_data"),
    XtdataMethodSpec("xtdata.get_local_data", "get_local_data"),
    XtdataMethodSpec("xtdata.get_full_tick", "get_full_tick"),
    XtdataMethodSpec("xtdata.get_full_kline", "get_full_kline"),
    XtdataMethodSpec("xtdata.get_divid_factors", "get_divid_factors"),
    XtdataMethodSpec("xtdata.download_history_data", "download_history_data"),
    XtdataMethodSpec("xtdata.download_history_data2", "download_history_data2"),
    XtdataMethodSpec("xtdata.download_history_contracts", "download_history_contracts"),
    XtdataMethodSpec("xtdata.get_holidays", "get_holidays"),
    XtdataMethodSpec("xtdata.get_trading_calendar", "get_trading_calendar"),
    XtdataMethodSpec("xtdata.get_trading_dates", "get_trading_dates"),
    XtdataMethodSpec("xtdata.get_period_list", "get_period_list"),
    XtdataMethodSpec("xtdata.get_instrument_detail", "get_instrument_detail"),
    XtdataMethodSpec("xtdata.get_instrument_type", "get_instrument_type"),
    XtdataMethodSpec("xtdata.get_sector_list", "get_sector_list"),
    XtdataMethodSpec("xtdata.get_stock_list_in_sector", "get_stock_list_in_sector"),
    XtdataMethodSpec("xtdata.download_sector_data", "download_sector_data"),
    XtdataMethodSpec("xtdata.create_sector_folder", "create_sector_folder"),
    XtdataMethodSpec("xtdata.create_sector", "create_sector"),
    XtdataMethodSpec("xtdata.add_sector", "add_sector"),
    XtdataMethodSpec("xtdata.remove_stock_from_sector", "remove_stock_from_sector"),
    XtdataMethodSpec("xtdata.remove_sector", "remove_sector"),
    XtdataMethodSpec("xtdata.reset_sector", "reset_sector"),
    XtdataMethodSpec("xtdata.get_index_weight", "get_index_weight"),
    XtdataMethodSpec("xtdata.download_index_weight", "download_index_weight"),
    XtdataMethodSpec("xtdata.get_financial_data", "get_financial_data"),
    XtdataMethodSpec("xtdata.download_financial_data", "download_financial_data"),
    XtdataMethodSpec("xtdata.download_financial_data2", "download_financial_data2"),
    XtdataMethodSpec("xtdata.download_cb_data", "download_cb_data"),
    XtdataMethodSpec("xtdata.get_cb_info", "get_cb_info"),
    XtdataMethodSpec("xtdata.get_ipo_info", "get_ipo_info"),
    XtdataMethodSpec("xtdata.download_etf_info", "download_etf_info"),
    XtdataMethodSpec("xtdata.get_etf_info", "get_etf_info"),
    XtdataMethodSpec("xtdata.generate_index_data", "generate_index_data"),
    XtdataMethodSpec("xtdata.get_l2_quote", "get_l2_quote"),
    XtdataMethodSpec("xtdata.get_l2_order", "get_l2_order"),
    XtdataMethodSpec("xtdata.get_l2_transaction", "get_l2_transaction"),
    XtdataMethodSpec("xtdata.get_l2thousand_queue", "get_l2thousand_queue"),
    XtdataMethodSpec(
        "xtdata.subscribe_l2thousand",
        "subscribe_l2thousand",
        subscription=True,
        topic="xtdata.subscribe_l2thousand",
    ),
    XtdataMethodSpec(
        "xtdata.subscribe_l2thousand_queue",
        "subscribe_l2thousand_queue",
        subscription=True,
        topic="xtdata.subscribe_l2thousand_queue",
    ),
    XtdataMethodSpec("xtdata.reconnect", "reconnect"),
    XtdataMethodSpec("xtdata.get_option_detail_data", "get_option_detail_data"),
    XtdataMethodSpec("xtdata.call_formula_batch", "call_formula_batch"),
    XtdataMethodSpec("xtdata.get_trading_time", "get_trading_time"),
)


def build_xtdata_registry(xtdata_module: Any) -> dict[str, XtdataMethodSpec]:
    registry: dict[str, XtdataMethodSpec] = {}
    for spec in CORE_SPECS:
        registry[spec.rpc_name] = XtdataMethodSpec(
            rpc_name=spec.rpc_name,
            xtdata_name=spec.xtdata_name,
            available=hasattr(xtdata_module, spec.xtdata_name),
            subscription=spec.subscription,
            topic=spec.topic,
        )
    return registry
