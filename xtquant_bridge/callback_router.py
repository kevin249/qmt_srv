from __future__ import annotations

from xtquant.xttrader import XtQuantTraderCallback


class XtQuantCallbackRouter(XtQuantTraderCallback):
    def __init__(self, bridge, translator) -> None:
        super().__init__()
        self.bridge = bridge
        self.translator = translator

    def on_connected(self):
        self.bridge.publish_log("xttrader connected")

    def on_disconnected(self):
        self.bridge.publish_log("xttrader disconnected")

    def on_stock_asset(self, asset):
        self.bridge.handle_account(self.translator.translate_account(asset))

    def on_stock_order(self, order):
        translated = self.translator.translate_order(order)
        self.bridge.handle_order(translated, str(getattr(order, "order_sysid", "") or ""))

    def on_stock_trade(self, trade):
        self.bridge.handle_trade(self.translator.translate_trade(trade))

    def on_stock_position(self, position):
        self.bridge.handle_position(self.translator.translate_position(position))

    def on_order_error(self, order_error):
        self.bridge.publish_log(
            f"order error code={getattr(order_error, 'error_id', '')} msg={getattr(order_error, 'error_msg', '')}"
        )

    def on_cancel_error(self, cancel_error):
        self.bridge.publish_log(
            f"cancel error code={getattr(cancel_error, 'error_id', '')} msg={getattr(cancel_error, 'error_msg', '')}"
        )

    def on_tick_data(self, datas):
        for xt_symbol, records in datas.items():
            contract = self.bridge.ensure_contract(xt_symbol)
            for payload in records:
                tick = self.translator.translate_tick(xt_symbol, payload, contract)
                self.bridge.handle_tick(tick)
