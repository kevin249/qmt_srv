from __future__ import annotations


class RpcRequestHandler:
    def __init__(self, bridge) -> None:
        self.bridge = bridge

    def _log(self, message: str) -> None:
        logger = getattr(self.bridge, "log_info", None)
        if callable(logger):
            logger("rpc", message)
            return
        publisher = getattr(self.bridge, "publish_log", None)
        if callable(publisher):
            publisher(message)

    def register_client(self, client_name: str, client_meta: dict | None = None) -> bool:
        self._log(f"rpc register_client client_name={client_name}")
        return self.bridge.register_client(client_name, client_meta)

    def subscribe(self, req, gateway_name: str = "") -> None:
        self._log(f"rpc subscribe vt_symbol={getattr(req, 'vt_symbol', '')}")
        self.bridge.subscribe(req)

    def send_order(self, req, gateway_name: str = "") -> str:
        self._log(
            f"rpc send_order symbol={getattr(req, 'symbol', '')} "
            f"exchange={getattr(getattr(req, 'exchange', None), 'value', getattr(req, 'exchange', ''))} "
            f"price={getattr(req, 'price', '')} volume={getattr(req, 'volume', '')}"
        )
        return self.bridge.send_order(req)

    def cancel_order(self, req, gateway_name: str = "") -> None:
        self._log(f"rpc cancel_order orderid={getattr(req, 'orderid', '')}")
        self.bridge.cancel_order(req)

    def query_history(self, req, gateway_name: str = ""):
        self._log(f"rpc query_history symbol={getattr(req, 'symbol', '')}")
        return self.bridge.query_history(req)

    def get_tick(self, vt_symbol: str):
        return self.bridge.ticks.get(vt_symbol)

    def get_order(self, vt_orderid: str):
        return self.bridge.orders.get(vt_orderid)

    def get_trade(self, vt_tradeid: str):
        return self.bridge.trades.get(vt_tradeid)

    def get_position(self, vt_positionid: str):
        return self.bridge.positions.get(vt_positionid)

    def get_account(self, vt_accountid: str):
        return self.bridge.accounts.get(vt_accountid)

    def get_contract(self, vt_symbol: str):
        return self.bridge.contracts.get(vt_symbol)

    def get_all_ticks(self):
        return list(self.bridge.ticks.values())

    def get_all_orders(self):
        return list(self.bridge.orders.values())

    def get_all_trades(self):
        return list(self.bridge.trades.values())

    def get_all_positions(self):
        self._log(f"rpc get_all_positions count={len(self.bridge.positions)}")
        return list(self.bridge.positions.values())

    def get_all_accounts(self):
        self._log(f"rpc get_all_accounts count={len(self.bridge.accounts)}")
        return list(self.bridge.accounts.values())

    def get_all_contracts(self):
        return list(self.bridge.contracts.values())

    def get_all_active_orders(self):
        return [order for order in self.bridge.orders.values() if order.is_active()]
