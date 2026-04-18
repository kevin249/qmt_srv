import unittest

from xtquant_bridge.xtdata_registry import build_xtdata_registry


class FakeXtData:
    def get_market_data(self, **kwargs):
        return kwargs

    def get_market_data_ex(self, **kwargs):
        return kwargs

    def get_local_data(self, **kwargs):
        return kwargs

    def get_full_tick(self, code_list):
        return code_list

    def get_full_kline(self, **kwargs):
        return kwargs

    def get_divid_factors(self, stock_code, start_time="", end_time=""):
        return stock_code, start_time, end_time

    def download_history_data(self, *args, **kwargs):
        return args, kwargs

    def download_history_data2(self, *args, **kwargs):
        return args, kwargs

    def get_period_list(self):
        return ["1m", "1d"]

    def get_trading_calendar(self, market, start_time="", end_time=""):
        return market, start_time, end_time

    def get_trading_dates(self, market, start_time="", end_time="", count=-1):
        return market, start_time, end_time, count

    def get_holidays(self):
        return []

    def get_instrument_detail(self, stock_code, iscomplete=False):
        return stock_code, iscomplete

    def get_instrument_type(self, stock_code, variety_list=None):
        return stock_code, variety_list

    def get_sector_list(self):
        return ["沪深A股"]

    def get_stock_list_in_sector(self, sector_name, real_timetag=-1):
        return sector_name, real_timetag

    def download_sector_data(self):
        return True

    def get_financial_data(self, stock_list, table_list=None, start_time="", end_time="", report_type="report_time"):
        return stock_list, table_list, start_time, end_time, report_type

    def subscribe_formula(self, **kwargs):
        return kwargs

    def unsubscribe_formula(self, request_id):
        return request_id

    def call_formula(self, **kwargs):
        return kwargs

    def generate_index_data(self, **kwargs):
        return kwargs

    def subscribe_quote(self, **kwargs):
        return kwargs

    def subscribe_whole_quote(self, **kwargs):
        return kwargs

    def unsubscribe_quote(self, seq):
        return seq

    def run(self):
        return None

    def get_l2_quote(self, stock_code):
        return stock_code

    def subscribe_l2thousand(self, **kwargs):
        return kwargs

    def reconnect(self, ip="", port=None, remember_if_success=True):
        return ip, port, remember_if_success


class XtdataRegistryTests(unittest.TestCase):
    def test_registry_contains_core_xtdata_methods(self) -> None:
        registry = build_xtdata_registry(FakeXtData())

        self.assertIn("xtdata.get_market_data_ex", registry)
        self.assertIn("xtdata.download_history_data", registry)
        self.assertIn("xtdata.subscribe_quote", registry)

    def test_registry_contains_market_and_calendar_methods(self) -> None:
        registry = build_xtdata_registry(FakeXtData())

        self.assertIn("xtdata.get_market_data", registry)
        self.assertIn("xtdata.get_local_data", registry)
        self.assertIn("xtdata.get_full_tick", registry)
        self.assertIn("xtdata.get_full_kline", registry)
        self.assertIn("xtdata.get_divid_factors", registry)
        self.assertIn("xtdata.download_history_data2", registry)
        self.assertIn("xtdata.get_period_list", registry)
        self.assertIn("xtdata.get_trading_calendar", registry)
        self.assertIn("xtdata.get_trading_dates", registry)
        self.assertIn("xtdata.get_holidays", registry)

    def test_registry_contains_reference_and_formula_methods(self) -> None:
        registry = build_xtdata_registry(FakeXtData())

        self.assertIn("xtdata.get_instrument_detail", registry)
        self.assertIn("xtdata.get_instrument_type", registry)
        self.assertIn("xtdata.get_sector_list", registry)
        self.assertIn("xtdata.get_stock_list_in_sector", registry)
        self.assertIn("xtdata.download_sector_data", registry)
        self.assertIn("xtdata.get_financial_data", registry)
        self.assertIn("xtdata.subscribe_formula", registry)
        self.assertIn("xtdata.unsubscribe_formula", registry)
        self.assertIn("xtdata.call_formula", registry)
        self.assertIn("xtdata.generate_index_data", registry)

    def test_registry_contains_l2_and_connection_methods(self) -> None:
        registry = build_xtdata_registry(FakeXtData())

        self.assertIn("xtdata.subscribe_whole_quote", registry)
        self.assertIn("xtdata.unsubscribe_quote", registry)
        self.assertIn("xtdata.run", registry)
        self.assertIn("xtdata.get_l2_quote", registry)
        self.assertIn("xtdata.subscribe_l2thousand", registry)
        self.assertIn("xtdata.reconnect", registry)


if __name__ == "__main__":
    unittest.main()
