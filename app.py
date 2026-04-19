from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from xtquant_bridge import XtQuantBridge


CONFIG_TEMPLATE_PATH = Path(__file__).with_name("config.template.json")
CONFIG_USER_PATH = Path(__file__).with_name("config.user.json")
DEFAULT_REP_ADDRESS = "tcp://*:20140"
DEFAULT_PUB_ADDRESS = "tcp://*:20141"
DEFAULT_EVENT_QUEUE_SIZE = 10000
DEFAULT_LOG_CATEGORIES = {
    "lifecycle": True,
    "rpc": True,
    "market_data": False,
    "snapshot": True,
    "account": True,
    "position": True,
    "order": True,
    "trade": True,
    "history": False,
    "contract": False,
    "heartbeat": False,
}


def strip_json_comments(text: str) -> str:
    """Remove //, /* */, and whole-line # comments while preserving string content."""
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue

        if char == "#":
            index += 1
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue

        result.append(char)
        index += 1

    return "".join(result)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load config.user.json by default, with JSONC-style comments support."""
    config_path = CONFIG_USER_PATH if path is None else Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Copy {CONFIG_TEMPLATE_PATH.name} to {CONFIG_USER_PATH.name} first."
        )

    raw_text = config_path.read_text(encoding="utf-8")
    config = json.loads(strip_json_comments(raw_text))
    if not isinstance(config, dict):
        raise ValueError("Config root must be a JSON object")
    return config


def normalize_qmt_root_path(value: Any) -> str:
    """Normalize qmt_path to the MiniQMT installation root directory."""
    raw = str(value or "").strip()
    if not raw:
        return ""

    path = Path(raw)
    if path.suffix.lower() == ".exe":
        return str(path.parent)
    if path.name.lower() in {"userdata", "userdata_mini"}:
        return str(path.parent)
    return raw


def build_bridge_config(config: dict[str, Any]) -> dict[str, Any]:
    """Build normalized bridge config from user JSON config."""
    xt = config.get("xt", {})
    rpc = config.get("rpc", {})
    logging_config = config.get("logging", {})
    csv_data_source = config.get("csv_data_source", {})
    legacy_csv_path = str(config.get("csv_data_path", "") or "").strip()

    if not isinstance(xt, dict):
        raise ValueError("xt section must be an object")
    if not isinstance(rpc, dict):
        raise ValueError("rpc section must be an object")
    if not isinstance(logging_config, dict):
        raise ValueError("logging section must be an object")
    if not isinstance(csv_data_source, dict):
        raise ValueError("csv_data_source section must be an object")

    qmt_path = normalize_qmt_root_path(xt.get("qmt_path", ""))
    account_id = str(xt.get("account_id", "") or "").strip()
    if not qmt_path:
        raise ValueError("xt.qmt_path is required")
    if not account_id:
        raise ValueError("xt.account_id is required")

    return {
        "xt": {
            "token": str(xt.get("token", "") or ""),
            "stock_active": bool(xt.get("stock_active", True)),
            "futures_active": bool(xt.get("futures_active", False)),
            "option_active": bool(xt.get("option_active", False)),
            "simulation": bool(xt.get("simulation", False)),
            "account_type": str(xt.get("account_type", "STOCK") or "STOCK"),
            "qmt_path": qmt_path,
            "account_id": account_id,
            "session_id": xt.get("session_id", 0),
            "callback_thread_pool_size": int(xt.get("callback_thread_pool_size", 4) or 4),
            "event_queue_size": int(xt.get("event_queue_size", DEFAULT_EVENT_QUEUE_SIZE) or DEFAULT_EVENT_QUEUE_SIZE),
        },
        "rpc": {
            "rep_address": str(rpc.get("rep_address", DEFAULT_REP_ADDRESS) or DEFAULT_REP_ADDRESS),
            "pub_address": str(rpc.get("pub_address", DEFAULT_PUB_ADDRESS) or DEFAULT_PUB_ADDRESS),
        },
        "logging": {
            "enabled": bool(logging_config.get("enabled", True)),
            "level": str(logging_config.get("level", "INFO") or "INFO").upper(),
            "console": bool(logging_config.get("console", True)),
            "publish_rpc_log_event": bool(logging_config.get("publish_rpc_log_event", True)),
            "categories": {
                **DEFAULT_LOG_CATEGORIES,
                **(logging_config.get("categories", {}) or {}),
            },
        },
        "csv_data_source": {
            "path": str(csv_data_source.get("path", "") or legacy_csv_path or "").strip(),
            "default_adjust": str(csv_data_source.get("default_adjust", "前复权") or "前复权"),
        },
    }


def start_server(
    *,
    config: dict[str, Any] | None = None,
    bridge_class: type[XtQuantBridge] = XtQuantBridge,
):
    """Instantiate and start the xtquant bridge server."""
    loaded = load_config() if config is None else config
    bridge_config = build_bridge_config(loaded)
    bridge = bridge_class(bridge_config)
    bridge.start()
    return bridge


def print_startup_summary(config: dict[str, Any]) -> None:
    xt = config["xt"]
    rpc = config["rpc"]
    print("[XTQ Bridge] server started")
    print(f"[XTQ Bridge] REP: {rpc['rep_address']}")
    print(f"[XTQ Bridge] PUB: {rpc['pub_address']}")
    print(f"[XTQ Bridge] QMT root: {xt['qmt_path']}")
    print(f"[XTQ Bridge] Account: {xt['account_id']}")
    print(f"[XTQ Bridge] Account type: {xt['account_type']}")
    print(f"[XTQ Bridge] Simulation: {xt['simulation']}")


def serve_forever() -> None:
    bridge = None
    config = build_bridge_config(load_config())
    try:
        bridge = start_server(config=config)
        print_startup_summary(config)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[XTQ Bridge] interrupted, shutting down")
    finally:
        if bridge is not None:
            bridge.close()


def main() -> int:
    try:
        serve_forever()
    except Exception as exc:  # noqa: BLE001
        print(f"[XTQ Bridge] startup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
