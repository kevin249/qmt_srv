import unittest

from xtquant_bridge.xtdata_rpc import XtdataMirrorExecutor


class FakeXtData:
    def __init__(self) -> None:
        self.last_kwargs = {}
        self.callback = None

    def get_market_data_ex(self, **kwargs):
        self.last_kwargs = kwargs
        return {"result": "ok"}

    def subscribe_quote(self, **kwargs):
        self.last_kwargs = kwargs
        self.callback = kwargs.get("callback")
        return 1


class FakePublisher:
    def __init__(self) -> None:
        self.events = []

    def enqueue(self, topic, event) -> None:
        self.events.append((topic, event))


class XtdataMirrorExecutorTests(unittest.TestCase):
    def test_executor_passes_period_and_dividend_type_through(self) -> None:
        fake_xtdata = FakeXtData()
        executor = XtdataMirrorExecutor(
            xtdata_module=fake_xtdata,
            registry={
                "xtdata.get_market_data_ex": {
                    "xtdata_name": "get_market_data_ex",
                    "available": True,
                }
            },
        )

        executor.call(
            "xtdata.get_market_data_ex",
            field_list=["time", "close"],
            stock_list=["000001.SZ"],
            period="1m",
            dividend_type="front_ratio",
        )

        self.assertEqual(fake_xtdata.last_kwargs["period"], "1m")
        self.assertEqual(fake_xtdata.last_kwargs["dividend_type"], "front_ratio")

    def test_executor_raises_for_unavailable_xtdata_method(self) -> None:
        executor = XtdataMirrorExecutor(
            xtdata_module=FakeXtData(),
            registry={
                "xtdata.call_formula_batch": {
                    "xtdata_name": "call_formula_batch",
                    "available": False,
                }
            },
        )

        with self.assertRaises(NotImplementedError):
            executor.call("xtdata.call_formula_batch")

    def test_subscription_methods_publish_serialized_callback_payload(self) -> None:
        fake_xtdata = FakeXtData()
        fake_publisher = FakePublisher()
        executor = XtdataMirrorExecutor(
            xtdata_module=fake_xtdata,
            publisher=fake_publisher,
            registry={
                "xtdata.subscribe_quote": {
                    "xtdata_name": "subscribe_quote",
                    "available": True,
                    "subscription": True,
                    "topic": "xtdata.subscribe_quote",
                }
            },
        )

        seq = executor.call("xtdata.subscribe_quote", stock_code="000001.SZ", period="1m")
        fake_xtdata.callback({"000001.SZ": [{"close": 10.1}]})

        self.assertEqual(seq, 1)
        self.assertEqual(fake_publisher.events[-1][0], "xtdata.subscribe_quote")
        self.assertEqual(fake_publisher.events[-1][1]["000001.SZ"][0]["close"], 10.1)


if __name__ == "__main__":
    unittest.main()
