import unittest
from types import SimpleNamespace

from vnpy.trader.constant import Direction, Exchange, Interval, OrderType
from vnpy.trader.object import OrderRequest

from xtquant_bridge.translator import DataTranslator


class TranslatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.translator = DataTranslator()

    def test_translate_tick(self) -> None:
        tick = self.translator.translate_tick(
            "000001.SZ",
            {
                "time": 1712888401500,
                "lastPrice": 10.5,
                "volume": 1000,
                "amount": 10500,
                "openInt": 0,
                "bidPrice": [10.4],
                "askPrice": [10.6],
                "bidVol": [100],
                "askVol": [200],
            },
        )
        self.assertEqual(tick.symbol, "000001")
        self.assertEqual(tick.exchange, Exchange.SZSE)
        self.assertEqual(tick.last_price, 10.5)
        self.assertEqual(tick.bid_price_1, 10.4)

    def test_translate_order_request_to_xt(self) -> None:
        req = OrderRequest(
            symbol="000001",
            exchange=Exchange.SZSE,
            direction=Direction.LONG,
            type=OrderType.LIMIT,
            volume=100,
            price=10.5,
        )
        payload = self.translator.order_request_to_xt(req)
        self.assertEqual(payload["stock_code"], "000001.SZ")
        self.assertEqual(payload["volume"], 100)
        self.assertEqual(payload["price"], 10.5)

    def test_translate_order_trade_position_account(self) -> None:
        order = self.translator.translate_order(
            SimpleNamespace(
                stock_code="000001.SZ",
                order_remark="LOCAL1",
                order_id=1,
                price_type=11,
                order_type=23,
                price=10.5,
                order_volume=100,
                traded_volume=20,
                order_status=50,
                order_time=1712888401,
                strategy_name="",
            )
        )
        trade = self.translator.translate_trade(
            SimpleNamespace(
                stock_code="000001.SZ",
                traded_id="T1",
                order_remark="LOCAL1",
                order_id=1,
                order_type=23,
                traded_price=10.6,
                traded_volume=20,
                traded_time=1712888401,
            )
        )
        position = self.translator.translate_position(
            SimpleNamespace(
                stock_code="000001.SZ",
                volume=100,
                frozen_volume=10,
                open_price=10.0,
                market_value=1000,
                yesterday_volume=50,
                direction=48,
            )
        )
        account = self.translator.translate_account(
            SimpleNamespace(account_id="123456", total_asset=100000, frozen_cash=1000, cash=99000)
        )

        self.assertEqual(order.orderid, "LOCAL1")
        self.assertEqual(trade.tradeid, "T1")
        self.assertEqual(position.symbol, "000001")
        self.assertEqual(account.accountid, "123456")

    def test_translate_bar(self) -> None:
        bar = self.translator.translate_bar(
            "000001.SZ",
            {"time": 1712888401500, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
            Interval.MINUTE,
        )
        self.assertEqual(bar.symbol, "000001")
        self.assertEqual(bar.close_price, 10.5)


if __name__ == "__main__":
    unittest.main()
