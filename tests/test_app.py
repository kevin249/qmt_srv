import tempfile
import unittest
from pathlib import Path

import app


class FakeBridge:
    def __init__(self, config):
        self.config = config
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True


class AppTests(unittest.TestCase):
    def test_load_config_supports_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.user.json"
            config_path.write_text(
                """{
  // comment
  "xt": {
    # comment
    "qmt_path": "D:\\\\QMT\\\\userdata_mini",
    "account_id": "123456"
  },
  "rpc": {
    /* comment */
    "rep_address": "tcp://*:20140",
    "pub_address": "tcp://*:20141"
  }
}""",
                encoding="utf-8",
            )

            config = app.load_config(config_path)

        self.assertEqual(config["xt"]["qmt_path"], r"D:\QMT\userdata_mini")
        self.assertEqual(config["rpc"]["rep_address"], "tcp://*:20140")

    def test_load_config_preserves_comment_tokens_inside_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.user.json"
            config_path.write_text(
                """{
  "xt": {
    "qmt_path": "D:\\\\QMT\\\\//share",
    "account_id": "123456"
  },
  "rpc": {
    "rep_address": "http://127.0.0.1:20140",
    "pub_address": "tcp://*:20141"
  }
}""",
                encoding="utf-8",
            )

            config = app.load_config(config_path)

        self.assertEqual(config["xt"]["qmt_path"], r"D:\QMT\//share")
        self.assertEqual(config["rpc"]["rep_address"], "http://127.0.0.1:20140")

    def test_build_bridge_config_normalizes_qmt_root(self) -> None:
        built = app.build_bridge_config(
            {
                "xt": {
                    "qmt_path": r"D:\QMT\bin.x64\XtMiniQmt.exe",
                    "account_id": "123456",
                    "event_queue_size": 128,
                },
                "rpc": {},
            }
        )

        self.assertEqual(built["xt"]["qmt_path"], r"D:\QMT\bin.x64")
        self.assertEqual(built["xt"]["event_queue_size"], 128)
        self.assertEqual(built["rpc"]["rep_address"], app.DEFAULT_REP_ADDRESS)

    def test_build_bridge_config_normalizes_userdata_path(self) -> None:
        built = app.build_bridge_config(
            {
                "xt": {
                    "qmt_path": r"D:\QMT\userdata_mini",
                    "account_id": "123456",
                },
                "rpc": {},
            }
        )

        self.assertEqual(built["xt"]["qmt_path"], r"D:\QMT")

    def test_build_bridge_config_adds_logging_defaults(self) -> None:
        built = app.build_bridge_config(
            {
                "xt": {
                    "qmt_path": r"D:\QMT",
                    "account_id": "123456",
                },
                "rpc": {},
            }
        )

        self.assertEqual(built["logging"]["level"], "INFO")
        self.assertTrue(built["logging"]["enabled"])
        self.assertTrue(built["logging"]["categories"]["order"])
        self.assertFalse(built["logging"]["categories"]["market_data"])

    def test_build_bridge_config_allows_logging_override(self) -> None:
        built = app.build_bridge_config(
            {
                "xt": {
                    "qmt_path": r"D:\QMT",
                    "account_id": "123456",
                },
                "rpc": {},
                "logging": {
                    "enabled": True,
                    "level": "ERROR",
                    "categories": {
                        "order": True,
                        "market_data": True,
                    },
                    "console": False,
                    "publish_rpc_log_event": True,
                },
            }
        )

        self.assertEqual(built["logging"]["level"], "ERROR")
        self.assertTrue(built["logging"]["categories"]["market_data"])
        self.assertFalse(built["logging"]["console"])

    def test_start_server_uses_bridge_class(self) -> None:
        bridge = app.start_server(
            config={
                "xt": {
                    "qmt_path": r"D:\QMT",
                    "account_id": "123456",
                },
                "rpc": {},
            },
            bridge_class=FakeBridge,
        )

        self.assertIsInstance(bridge, FakeBridge)
        self.assertTrue(bridge.started)
        self.assertEqual(bridge.config["xt"]["qmt_path"], r"D:\QMT")


if __name__ == "__main__":
    unittest.main()
