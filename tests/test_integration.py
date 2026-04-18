import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from vnpy.event import Event
from vnpy.trader.constant import Exchange
from vnpy.trader.event import EVENT_ACCOUNT, EVENT_LOG, EVENT_ORDER

from xtquant_bridge.bridge import XtQuantBridge


class FakeRpcServer:
    def __init__(self) -> None:
        self.registered = []
        self.published = []
        self.started = None
        self.stopped = False
        self.joined = False

    def register(self, func) -> None:
        self.registered.append(func.__name__)

    def start(self, rep, pub) -> None:
        self.started = (rep, pub)

    def publish(self, topic, event) -> None:
        self.published.append((topic, event))

    def stop(self) -> None:
        self.stopped = True

    def join(self) -> None:
        self.joined = True


class FakeXtData:
    def __init__(self) -> None:
        self.subscriptions = []
        self.download_calls = []
        self.connect_calls = []

    def connect(self, ip="", port=None, remember_if_success=True):
        self.connect_calls.append((ip, port, remember_if_success))
        return self

    def subscribe_quote(self, stock_code, period="tick", callback=None):
        self.subscriptions.append((stock_code, period, callback))
        return 1

    def get_instrument_detail(self, xt_symbol, iscomplete=True):
        return {"InstrumentName": xt_symbol, "PriceTick": 0.01, "VolumeMultiple": 1}

    def download_history_data(self, *args):
        self.download_calls.append(args)

    def get_market_data_ex(self, **kwargs):
        return {
            kwargs["stock_list"][0]: [
                {"time": 1712888401500, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100}
            ]
        }


class FakeXtDc:
    def __init__(self) -> None:
        self.calls = []

    def shutdown(self):
        self.calls.append(("shutdown", None))


class FakeXtTrader:
    def __init__(self, path, session_id) -> None:
        self.path = path
        self.session_id = session_id
        self.callback = None
        self.started = False
        self.connected = False
        self.account = None
        self.order_calls = []

    def register_callback(self, callback):
        self.callback = callback

    def start(self):
        self.started = True

    def connect(self):
        self.connected = True
        return 0

    def subscribe(self, account):
        self.account = account
        return 0

    def query_stock_asset(self, account):
        return SimpleNamespace(account_id="123456", total_asset=100000, frozen_cash=1000, cash=99000)

    def query_stock_positions(self, account):
        return []

    def query_stock_orders(self, account):
        return []

    def query_stock_trades(self, account):
        return []

    def order_stock_async(self, *args, **kwargs):
        self.order_calls.append((args, kwargs))

    def stop(self):
        pass


class FakeStockAccount:
    def __init__(self, account_id, account_type="STOCK") -> None:
        self.account_id = account_id
        self.account_type = account_type


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tempdir = TemporaryDirectory()
        cls.qmt_path = Path(cls._tempdir.name) / "userdata_mini"
        cls.qmt_path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tempdir.cleanup()

    def test_bridge_start_and_publish(self) -> None:
        rpc_server = FakeRpcServer()
        xtdata_module = FakeXtData()
        xtdc_module = FakeXtDc()
        bridge = XtQuantBridge(
            {
                "xt": {
                    "token": "",
                    "stock_active": True,
                    "futures_active": False,
                    "option_active": False,
                    "simulation": True,
                    "account_type": "STOCK",
                    "qmt_path": str(self.qmt_path),
                    "account_id": "123456",
                    "session_id": 1,
                    "callback_thread_pool_size": 4,
                    "event_queue_size": 16,
                },
                "rpc": {
                    "rep_address": "tcp://*:20140",
                    "pub_address": "tcp://*:20141",
                },
            },
            rpc_server=rpc_server,
            xtdata_module=xtdata_module,
            xtdatacenter_module=xtdc_module,
            xttrader_class=FakeXtTrader,
            stock_account_class=FakeStockAccount,
        )

        bridge.start()
        bridge.publish_log("hello")
        bridge.handle_account(SimpleNamespace(vt_accountid="XTQUANT.123456", balance=0, frozen=0))
        bridge.publisher.stop()
        bridge.publisher.join()

        self.assertIn("subscribe", rpc_server.registered)
        self.assertIn("xtdata.get_market_data_ex", rpc_server.registered)
        self.assertIn("xtdata.call_formula_batch", rpc_server.registered)
        self.assertEqual(rpc_server.started, ("tcp://*:20140", "tcp://*:20141"))
        self.assertEqual(xtdata_module.connect_calls, [("", None, True)])
        self.assertEqual(xtdc_module.calls, [])
        self.assertTrue(any(topic == EVENT_LOG for topic, _ in rpc_server.published))
        self.assertTrue(any(topic == EVENT_ACCOUNT for topic, _ in rpc_server.published))
        bridge.close()

    def test_bridge_logs_connection_and_query_summary(self) -> None:
        rpc_server = FakeRpcServer()
        xtdata_module = FakeXtData()
        bridge = XtQuantBridge(
            {
                "xt": {
                    "token": "",
                    "stock_active": True,
                    "futures_active": False,
                    "option_active": False,
                    "simulation": True,
                    "account_type": "STOCK",
                    "qmt_path": str(self.qmt_path),
                    "account_id": "123456",
                    "session_id": 1,
                    "callback_thread_pool_size": 4,
                    "event_queue_size": 16,
                },
                "rpc": {
                    "rep_address": "tcp://*:20140",
                    "pub_address": "tcp://*:20141",
                },
            },
            rpc_server=rpc_server,
            xtdata_module=xtdata_module,
            xtdatacenter_module=FakeXtDc(),
            xttrader_class=FakeXtTrader,
            stock_account_class=FakeStockAccount,
        )

        bridge.start()
        bridge.publisher.stop()
        bridge.publisher.join()
        log_messages = [event.data.msg for topic, event in rpc_server.published if topic == EVENT_LOG]

        self.assertTrue(any("market data connected" in message for message in log_messages))
        self.assertTrue(any("trading connected" in message for message in log_messages))
        self.assertTrue(any("snapshot loaded" in message for message in log_messages))
        bridge.close()

    def test_bridge_filters_logs_by_level_and_category(self) -> None:
        rpc_server = FakeRpcServer()
        bridge = XtQuantBridge(
            {
                "xt": {
                    "token": "",
                    "stock_active": True,
                    "futures_active": False,
                    "option_active": False,
                    "simulation": True,
                    "account_type": "STOCK",
                    "qmt_path": str(self.qmt_path),
                    "account_id": "123456",
                    "session_id": 1,
                    "callback_thread_pool_size": 4,
                    "event_queue_size": 16,
                },
                "rpc": {
                    "rep_address": "tcp://*:20140",
                    "pub_address": "tcp://*:20141",
                },
                "logging": {
                    "enabled": True,
                    "level": "WARNING",
                    "console": False,
                    "publish_rpc_log_event": True,
                    "categories": {
                        "market_data": False,
                        "order": True,
                    },
                },
            },
            rpc_server=rpc_server,
            xtdata_module=FakeXtData(),
            xtdatacenter_module=FakeXtDc(),
            xttrader_class=FakeXtTrader,
            stock_account_class=FakeStockAccount,
        )

        bridge.publisher.start()
        bridge.log_info("market_data", "market data connected")
        bridge.log_warning("order", "send_order failed")
        bridge.publisher.stop()
        bridge.publisher.join()

        log_messages = [event.data.msg for topic, event in rpc_server.published if topic == EVENT_LOG]
        self.assertFalse(any("market data connected" in message for message in log_messages))
        self.assertTrue(any("send_order failed" in message for message in log_messages))

    def test_bridge_register_client_logs_details(self) -> None:
        rpc_server = FakeRpcServer()
        bridge = XtQuantBridge(
            {
                "xt": {
                    "token": "",
                    "stock_active": True,
                    "futures_active": False,
                    "option_active": False,
                    "simulation": True,
                    "account_type": "STOCK",
                    "qmt_path": str(self.qmt_path),
                    "account_id": "123456",
                    "session_id": 1,
                    "callback_thread_pool_size": 4,
                    "event_queue_size": 16,
                },
                "rpc": {
                    "rep_address": "tcp://*:20140",
                    "pub_address": "tcp://*:20141",
                },
            },
            rpc_server=rpc_server,
            xtdata_module=FakeXtData(),
            xtdatacenter_module=FakeXtDc(),
            xttrader_class=FakeXtTrader,
            stock_account_class=FakeStockAccount,
        )

        bridge.publisher.start()
        result = bridge.register_client("probe", {"pid": 123, "mode": "manual"})
        bridge.publisher.stop()
        bridge.publisher.join()
        log_messages = [event.data.msg for topic, event in rpc_server.published if topic == EVENT_LOG]

        self.assertTrue(result)
        self.assertIn("probe", bridge.registered_clients)
        self.assertTrue(any("client registered" in message for message in log_messages))

    def test_query_history_rejects_unknown_interval(self) -> None:
        bridge = XtQuantBridge(
            {
                "xt": {
                    "token": "",
                    "stock_active": True,
                    "futures_active": False,
                    "option_active": False,
                    "simulation": True,
                    "account_type": "STOCK",
                    "qmt_path": str(self.qmt_path),
                    "account_id": "123456",
                    "session_id": 1,
                    "callback_thread_pool_size": 4,
                    "event_queue_size": 16,
                },
                "rpc": {
                    "rep_address": "tcp://*:20140",
                    "pub_address": "tcp://*:20141",
                },
            },
            rpc_server=FakeRpcServer(),
            xtdata_module=FakeXtData(),
            xtdatacenter_module=FakeXtDc(),
            xttrader_class=FakeXtTrader,
            stock_account_class=FakeStockAccount,
        )
        req = SimpleNamespace(
            symbol="000001",
            exchange=Exchange.SSE,
            interval="1min",
            start=None,
            end=None,
            vt_symbol="000001.SSE",
        )

        with self.assertRaises(ValueError):
            bridge.query_history(req)


if __name__ == "__main__":
    unittest.main()
