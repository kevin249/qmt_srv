import unittest
from types import SimpleNamespace

from xtquant_bridge.rpc_handler import RpcRequestHandler


class RpcHandlerTests(unittest.TestCase):
    def test_rpc_handler_delegates(self) -> None:
        bridge = SimpleNamespace(
            ticks={"A": 1},
            orders={"B": 2},
            trades={"C": 3},
            positions={"D": 4},
            accounts={"E": 5},
            contracts={"F": 6},
            subscribe=lambda req: setattr(req, "called", "subscribe"),
            send_order=lambda req: "VT.ORDER",
            cancel_order=lambda req: setattr(req, "called", "cancel"),
            query_history=lambda req: ["bar"],
        )
        handler = RpcRequestHandler(bridge)
        req = SimpleNamespace()
        cancel_req = SimpleNamespace()

        handler.subscribe(req)
        result = handler.send_order(req)
        handler.cancel_order(cancel_req)

        self.assertEqual(req.called, "subscribe")
        self.assertEqual(cancel_req.called, "cancel")
        self.assertEqual(result, "VT.ORDER")
        self.assertEqual(handler.get_tick("A"), 1)
        self.assertEqual(handler.get_all_contracts(), [6])


if __name__ == "__main__":
    unittest.main()
