import unittest

from vnpy.trader.constant import Exchange

from xtquant_bridge.utils import normalize_qmt_root_path, resolve_userdata_path, vnpy_symbol_to_xt, xt_symbol_to_vnpy


class UtilsTests(unittest.TestCase):
    def test_normalize_qmt_root_path(self) -> None:
        self.assertEqual(normalize_qmt_root_path(r"D:\QMT\XtMiniQmt.exe"), r"D:\QMT")
        self.assertEqual(normalize_qmt_root_path(r"D:\QMT\userdata"), r"D:\QMT")
        self.assertEqual(normalize_qmt_root_path(r"D:\QMT\userdata_mini"), r"D:\QMT")

    def test_resolve_userdata_path_prefers_sibling_userdata_mini_for_bin_x64(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "bin.x64"
            root.mkdir()
            (root / "userdata").mkdir()
            sibling_userdata_mini = Path(tmpdir) / "userdata_mini"
            sibling_userdata_mini.mkdir()

            resolved = resolve_userdata_path(str(root))

        self.assertEqual(resolved, str(sibling_userdata_mini))

    def test_symbol_conversion_roundtrip(self) -> None:
        xt_symbol = vnpy_symbol_to_xt("000001", Exchange.SZSE)
        self.assertEqual(xt_symbol, "000001.SZ")
        symbol, exchange = xt_symbol_to_vnpy(xt_symbol)
        self.assertEqual(symbol, "000001")
        self.assertEqual(exchange, Exchange.SZSE)


if __name__ == "__main__":
    unittest.main()
