import unittest
from types import SimpleNamespace

from xtquant_bridge.rpc_handler import RpcRequestHandler


class RpcLoggingTests(unittest.TestCase):
    def test_rpc_handler_logs_query_and_order_actions(self) -> None:
        logs = []

        bridge = SimpleNamespace(
            ticks={},
            orders={},
            trades={},
            positions={"P1": 1},
            accounts={"A1": 2},
            contracts={},
            register_client=lambda name, meta=None: True,
            subscribe=lambda req: logs.append(("subscribe", req.vt_symbol)),
            send_order=lambda req: "XTQUANT.XTQ0000000001",
            cancel_order=lambda req: logs.append(("cancel", req.orderid)),
            query_history=lambda req: ["bar1", "bar2"],
            publish_log=lambda message: logs.append(("log", message)),
        )

        handler = RpcRequestHandler(bridge)

        req = SimpleNamespace(vt_symbol="000001.SZSE")
        order_req = SimpleNamespace(symbol="000001", exchange="SZSE", direction="LONG", type="LIMIT", price=10.5, volume=100)
        cancel_req = SimpleNamespace(orderid="XTQ0000000001")

        handler.subscribe(req)
        handler.register_client("probe", {"pid": 1})
        handler.send_order(order_req)
        handler.cancel_order(cancel_req)
        accounts = handler.get_all_accounts()
        positions = handler.get_all_positions()
        history = handler.query_history(SimpleNamespace(symbol="000001"))

        self.assertEqual(accounts, [2])
        self.assertEqual(positions, [1])
        self.assertEqual(history, ["bar1", "bar2"])
        logged_messages = [entry[1] for entry in logs if entry[0] == "log"]
        self.assertTrue(any("rpc subscribe" in message for message in logged_messages))
        self.assertTrue(any("rpc register_client" in message for message in logged_messages))
        self.assertTrue(any("rpc send_order" in message for message in logged_messages))
        self.assertTrue(any("rpc get_all_accounts" in message for message in logged_messages))
        self.assertTrue(any("rpc query_history" in message for message in logged_messages))


if __name__ == "__main__":
    unittest.main()
