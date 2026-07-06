from __future__ import annotations

import argparse
import io
import json
import os
import pickle
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_REP_ADDRESS = "tcp://*:20140"
DEFAULT_PUB_ADDRESS = "tcp://*:20141"
DEFAULT_CONFIG_PATH = BASE_DIR / "config.user.json"
TEMPLATE_CONFIG_PATH = BASE_DIR / "config.template.json"


class LooseUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> type[Any]:
        try:
            return super().find_class(module, name)
        except Exception:
            return type(str(name), (object,), {})


@dataclass
class QmtInstance:
    instance_id: str
    python_dir: Path
    export_dir: Path
    accounts: list[dict[str, Any]]

    @property
    def command_file(self) -> Path:
        return self.export_dir / "commands" / "inbox.jsonl"

    @property
    def snapshot_file(self) -> Path:
        return self.export_dir / "snapshots" / "latest.json"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception as exc:
        print(f"QMT_SRV_JSON_IGNORED path={path} error={exc}", file=sys.stderr)
        return default


def json_safe(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v, depth + 1) for v in value]
    try:
        return json_safe(vars(value), depth + 1)
    except Exception:
        return str(value)


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if "," in text or ";" in text or " " in text:
            return [x.strip() for x in text.replace(";", ",").replace(" ", ",").split(",") if x.strip()]
        return [text]
    return [value]


def normalize_symbol(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    aliases = {
        "SH": "SH",
        "SHSE": "SH",
        "SSE": "SH",
        "SS": "SH",
        "SZ": "SZ",
        "SZSE": "SZ",
        "SZE": "SZ",
        "BJ": "BJ",
        "BSE": "BJ",
        "BJS": "BJ",
    }
    if "." in text:
        left, right = text.split(".", 1)
        left_digits = "".join(ch for ch in left if ch.isdigit())
        right_digits = "".join(ch for ch in right if ch.isdigit())
        if len(left_digits) >= 6:
            code = left_digits[:6]
            return f"{code}.{aliases.get(right, infer_market(code))}"
        if len(right_digits) >= 6:
            code = right_digits[:6]
            return f"{code}.{aliases.get(left, infer_market(code))}"
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        code = digits[:6]
        return f"{code}.{infer_market(code)}"
    return ""


def infer_market(code: str) -> str:
    if code.startswith(("4", "8")):
        return "BJ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def extract_symbols(value: Any) -> list[str]:
    result: list[str] = []
    if value is None:
        return result
    if isinstance(value, str):
        for item in normalize_list(value):
            symbol = normalize_symbol(item)
            if symbol and symbol not in result:
                result.append(symbol)
        return result
    if isinstance(value, dict):
        for key in (
            "vt_symbol",
            "vtSymbol",
            "symbol",
            "stock",
            "stock_code",
            "stockCode",
            "code",
            "instrument",
            "secid",
            "security",
            "wind_code",
            "name",
            "stock_name",
        ):
            result.extend(extract_symbols(value.get(key)))
        for key in (
            "vt_symbols",
            "vtSymbols",
            "symbols",
            "stocks",
            "stock_list",
            "stockList",
            "stock_code_list",
            "code_list",
            "codes",
            "names",
            "stock_names",
        ):
            result.extend(extract_symbols(value.get(key)))
        return list(dict.fromkeys(result))
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(extract_symbols(item))
        return list(dict.fromkeys(result))
    return result


def extract_accounts(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if value is None:
        return result
    if isinstance(value, str):
        return [{"account_id": item, "account_type": "STOCK"} for item in normalize_list(value)]
    if isinstance(value, dict):
        account_id = ""
        for key in ("account_id", "accountId", "accountID", "acc_id", "accID", "account", "fund_account"):
            if value.get(key):
                account_id = str(value[key]).strip()
                break
        if account_id:
            result.append(
                {
                    "account_id": account_id,
                    "account_type": str(value.get("account_type") or value.get("accountType") or value.get("type") or "STOCK"),
                    "name": str(value.get("name") or value.get("label") or ""),
                }
            )
        for key in ("accounts", "account_ids", "accountIds", "account_list", "fund_accounts"):
            result.extend(extract_accounts(value.get(key)))
        return dedupe_accounts(result)
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(extract_accounts(item))
        return dedupe_accounts(result)
    return result


def dedupe_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for account in accounts:
        account_id = str(account.get("account_id") or "").strip()
        account_type = str(account.get("account_type") or "STOCK").strip() or "STOCK"
        if not account_id:
            continue
        key = (account_id, account_type)
        if key in seen:
            continue
        seen.add(key)
        item = dict(account)
        item["account_id"] = account_id
        item["account_type"] = account_type
        result.append(item)
    return result


def resolve_python_dir(path_value: Any) -> Path:
    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if path.name.lower() == "python":
        return path
    candidate = path / "python"
    return candidate if candidate.exists() else path


def normalize_instances(config: dict[str, Any]) -> list[QmtInstance]:
    raw_instances = config.get("qmt_instances") or config.get("instances") or config.get("qmt_dirs") or []
    instances: list[QmtInstance] = []
    for index, raw in enumerate(normalize_list(raw_instances), start=1):
        if isinstance(raw, str):
            raw = {"python_dir": raw}
        if not isinstance(raw, dict):
            continue
        python_value = raw.get("python_dir") or raw.get("qmt_python_dir") or raw.get("qmt_dir") or raw.get("path")
        if not python_value:
            continue
        python_dir = resolve_python_dir(python_value)
        export_value = raw.get("export_dir")
        export_dir = Path(str(export_value)).expanduser() if export_value else python_dir / "qmt_data_export"
        if not export_dir.is_absolute():
            export_dir = (Path.cwd() / export_dir).resolve()
        instance_id = str(raw.get("instance_id") or raw.get("id") or python_dir.parent.name or f"qmt_{index}").strip()
        accounts = dedupe_accounts(extract_accounts(raw.get("accounts") or raw.get("account_ids") or raw.get("account_id")))
        instances.append(QmtInstance(instance_id=instance_id, python_dir=python_dir, export_dir=export_dir, accounts=accounts))
    return instances


def load_config(config_path: Path | None) -> dict[str, Any]:
    path = config_path or (DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else TEMPLATE_CONFIG_PATH)
    config = read_json(path, {})
    if not isinstance(config, dict):
        config = {}
    config.setdefault("rep_address", DEFAULT_REP_ADDRESS)
    config.setdefault("pub_address", DEFAULT_PUB_ADDRESS)
    config.setdefault("snapshot_publish_seconds", 1.0)
    return config


def decode_rpc(raw: bytes) -> tuple[str, list[Any], dict[str, Any]]:
    try:
        obj = pickle.loads(raw)
    except Exception:
        try:
            obj = LooseUnpickler(io.BytesIO(raw)).load()
        except Exception:
            obj = json.loads(raw.decode("utf-8"))
    if isinstance(obj, (list, tuple)) and len(obj) >= 3:
        return str(obj[0]), list(obj[1] or []), dict(obj[2] or {})
    if isinstance(obj, dict):
        return str(obj.get("method") or obj.get("function")), list(obj.get("args") or []), dict(obj.get("kwargs") or {})
    raise ValueError(f"unsupported rpc request: {obj!r}")


class QmtSrv:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.rep_address = str(config.get("rep_address") or DEFAULT_REP_ADDRESS)
        self.pub_address = str(config.get("pub_address") or DEFAULT_PUB_ADDRESS)
        self.publish_seconds = float(config.get("snapshot_publish_seconds") or 1.0)
        self.instances = normalize_instances(config)
        self.clients: dict[str, dict[str, Any]] = {}
        self.stop_event = threading.Event()

    def load_snapshot(self, instance: QmtInstance) -> dict[str, Any]:
        snapshot = read_json(instance.snapshot_file, {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot.setdefault("instance", {"id": instance.instance_id, "qmt_root": str(instance.python_dir.parent)})
        return snapshot

    def aggregate(self) -> dict[str, Any]:
        snapshots: dict[str, Any] = {}
        ticks: dict[str, Any] = {}
        histories: dict[str, Any] = {}
        contracts: dict[str, Any] = {}
        financial: dict[str, Any] = {}
        sectors: dict[str, Any] = {}
        calendar: dict[str, Any] = {}
        accounts: list[Any] = []
        positions: list[Any] = []
        orders: list[Any] = []
        trades: list[Any] = []
        for instance in self.instances:
            snapshot = self.load_snapshot(instance)
            snapshots[instance.instance_id] = snapshot
            self._merge_dict(ticks, snapshot.get("ticks"), instance.instance_id)
            self._merge_dict(histories, snapshot.get("histories"), instance.instance_id, keep_key=True)
            self._merge_dict(contracts, snapshot.get("contracts"), instance.instance_id)
            self._merge_dict(financial, snapshot.get("financial"), instance.instance_id, keep_key=True)
            self._merge_dict(sectors, snapshot.get("sectors"), instance.instance_id, keep_key=True)
            self._merge_dict(calendar, snapshot.get("calendar"), instance.instance_id, keep_key=True)
            accounts.extend(self._tag_rows(snapshot.get("accounts") or instance.accounts, instance.instance_id))
            positions.extend(self._tag_rows(snapshot.get("positions"), instance.instance_id))
            orders.extend(self._tag_rows(snapshot.get("orders"), instance.instance_id))
            trades.extend(self._tag_rows(snapshot.get("trades"), instance.instance_id))
        return {
            "updated_at": now_text(),
            "instances": [self.instance_meta(item) for item in self.instances],
            "snapshots": snapshots,
            "ticks": ticks,
            "histories": histories,
            "contracts": contracts,
            "financial": financial,
            "sectors": sectors,
            "calendar": calendar,
            "accounts": accounts,
            "positions": positions,
            "orders": orders,
            "trades": trades,
        }

    def instance_meta(self, instance: QmtInstance) -> dict[str, Any]:
        return {
            "instance_id": instance.instance_id,
            "python_dir": str(instance.python_dir),
            "export_dir": str(instance.export_dir),
            "snapshot_file": str(instance.snapshot_file),
            "command_file": str(instance.command_file),
            "accounts": instance.accounts,
            "snapshot_exists": instance.snapshot_file.exists(),
        }

    def _merge_dict(self, target: dict[str, Any], value: Any, instance_id: str, keep_key: bool = False) -> None:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            out = item
            if isinstance(out, dict):
                out = dict(out)
                out.setdefault("instance_id", instance_id)
            target[str(key)] = out
            if keep_key:
                target[f"{instance_id}|{key}"] = out

    def _tag_rows(self, rows: Any, instance_id: str) -> list[Any]:
        result = []
        for row in normalize_list(rows):
            if isinstance(row, dict):
                item = dict(row)
                item.setdefault("instance_id", instance_id)
                result.append(item)
            elif row is not None:
                result.append({"value": row, "instance_id": instance_id})
        return result

    def target_instances(self, args: list[Any], kwargs: dict[str, Any]) -> list[QmtInstance]:
        wanted = str(kwargs.get("instance_id") or kwargs.get("qmt_instance") or kwargs.get("qmt_dir") or "").strip()
        for value in args:
            if isinstance(value, dict):
                wanted = str(value.get("instance_id") or value.get("qmt_instance") or wanted).strip()
        if not wanted or wanted in ("*", "all"):
            return self.instances
        return [item for item in self.instances if item.instance_id == wanted or str(item.python_dir.parent) == wanted]

    def write_command(self, instance: QmtInstance, method: str, args: list[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        instance.command_file.parent.mkdir(parents=True, exist_ok=True)
        command = {
            "id": str(uuid.uuid4()),
            "time": now_text(),
            "instance_id": instance.instance_id,
            "method": method,
            "args": json_safe(args),
            "kwargs": json_safe(kwargs),
        }
        with instance.command_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(command, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
        return command

    def route_command(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> list[dict[str, Any]]:
        commands = []
        for instance in self.target_instances(args, kwargs):
            commands.append(self.write_command(instance, method, args, kwargs))
        return commands

    def handle_rpc(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        aggregate = self.aggregate()
        if method == "register_client":
            name = str(args[0] if args else kwargs.get("client_name", "unknown"))
            self.clients[name] = {"meta": json_safe(args[1] if len(args) > 1 else kwargs), "time": now_text()}
            if extract_symbols(args) or extract_symbols(kwargs) or extract_accounts(args) or extract_accounts(kwargs):
                self.route_command(method, args, kwargs)
            return True
        if method in {"subscribe", "set_account", "xtdata.subscribe_quote", "xtdata.subscribe_whole_quote"}:
            self.route_command(method, args, kwargs)
            return True if method != "xtdata.subscribe_quote" else 1
        if method in {"send_order", "cancel_order"}:
            raise RuntimeError("qmt_srv data bridge is read-only; order APIs are disabled")
        if method == "query_history":
            self.route_command(method, args, kwargs)
            return self.query_history(args[0] if args else kwargs, aggregate)
        if method in {"get_tick", "get_l1_tick"}:
            symbol = normalize_symbol(args[0] if args else kwargs.get("vt_symbol") or kwargs.get("symbol"))
            return aggregate["ticks"].get(symbol)
        if method == "get_all_ticks":
            return list(aggregate["ticks"].values())
        if method == "get_contract":
            symbol = normalize_symbol(args[0] if args else kwargs.get("vt_symbol") or kwargs.get("symbol"))
            return aggregate["contracts"].get(symbol)
        if method == "get_all_contracts":
            return list(aggregate["contracts"].values())
        if method in {"get_all_accounts", "get_all_positions", "get_all_orders", "get_all_trades", "get_all_active_orders"}:
            key = method.replace("get_all_", "")
            if key == "active_orders":
                key = "orders"
            return aggregate.get(key, [])
        if method in {"get_account", "get_position", "get_order", "get_trade"}:
            key = {"get_account": "accounts", "get_position": "positions", "get_order": "orders", "get_trade": "trades"}[method]
            rows = self.filter_rows(aggregate.get(key, []), extract_accounts(args) + extract_accounts(kwargs))
            return rows[0] if len(rows) == 1 else rows
        if method == "xtdata.get_full_tick":
            stock_list = args[0] if args else kwargs.get("code_list") or kwargs.get("stock_list") or list(aggregate["ticks"].keys())
            return {normalize_symbol(s): aggregate["ticks"].get(normalize_symbol(s)) for s in normalize_list(stock_list)}
        if method in {"xtdata.get_market_data", "xtdata.get_market_data_ex", "xtdata.get_local_data"}:
            return self.query_market_data(args, kwargs, aggregate)
        if method == "xtdata.get_instrument_detail":
            symbol = normalize_symbol(args[0] if args else kwargs.get("stock_code") or kwargs.get("symbol"))
            return aggregate["contracts"].get(symbol)
        if method == "xtdata.get_financial_data":
            return aggregate["financial"]
        if method == "xtdata.get_trading_calendar":
            return aggregate["calendar"]
        if method == "xtdata.get_stock_list_in_sector":
            name = args[0] if args else kwargs.get("sector_name", "")
            return aggregate["sectors"].get(name, aggregate["sectors"])
        if method == "xtdata.unsubscribe_quote":
            return None
        if method in {"get_instances", "get_qmt_instances"}:
            return aggregate["instances"]
        if method in {"get_snapshot", "get_all"}:
            return aggregate
        raise KeyError(method)

    def query_history(self, request: Any, aggregate: dict[str, Any]) -> Any:
        symbols = extract_symbols(request) or list(aggregate["ticks"].keys())
        period = "1m"
        if isinstance(request, dict):
            period = str(request.get("period") or request.get("interval") or period)
        result = {}
        for symbol in symbols:
            result[symbol] = aggregate["histories"].get(f"{symbol}|{period}") or aggregate["histories"].get(symbol) or []
        return result

    def query_market_data(self, args: list[Any], kwargs: dict[str, Any], aggregate: dict[str, Any]) -> dict[str, Any]:
        stock_list = args[1] if len(args) >= 2 else kwargs.get("stock_list") or kwargs.get("code_list") or list(aggregate["ticks"].keys())
        period = kwargs.get("period") or (args[2] if len(args) >= 3 else "1m")
        result = {}
        for raw in normalize_list(stock_list):
            symbol = normalize_symbol(raw)
            result[symbol] = aggregate["histories"].get(f"{symbol}|{period}") or aggregate["histories"].get(symbol) or []
        return result

    def filter_rows(self, rows: list[Any], accounts: list[dict[str, Any]]) -> list[Any]:
        accounts = dedupe_accounts(accounts)
        if not accounts:
            return rows
        wanted = {item["account_id"] for item in accounts}
        return [row for row in rows if isinstance(row, dict) and str(row.get("account_id") or "") in wanted]

    def serve(self) -> int:
        try:
            import zmq
        except Exception as exc:
            print(f"pyzmq is required: {exc}", file=sys.stderr)
            return 2
        ctx = zmq.Context()
        rep = ctx.socket(zmq.REP)
        pub = ctx.socket(zmq.PUB)
        rep.linger = 0
        pub.linger = 0
        rep.bind(self.rep_address)
        pub.bind(self.pub_address)
        poller = zmq.Poller()
        poller.register(rep, zmq.POLLIN)
        print(f"QMT_SRV_STARTED rep={self.rep_address} pub={self.pub_address} instances={len(self.instances)}")
        last_pub = 0.0
        try:
            while not self.stop_event.is_set():
                events = dict(poller.poll(200))
                if rep in events:
                    method = ""
                    try:
                        method, args, kwargs = decode_rpc(rep.recv())
                        payload = self.handle_rpc(method, args, kwargs)
                        rep.send_pyobj([True, json_safe(payload)])
                    except Exception as exc:
                        rep.send_pyobj([False, str(exc)])
                        print(f"QMT_SRV_RPC_ERROR method={method} error={exc}", file=sys.stderr)
                        traceback.print_exc()
                now = time.time()
                if now - last_pub >= self.publish_seconds:
                    last_pub = now
                    snapshot = json_safe(self.aggregate())
                    pub.send_pyobj(["snapshot", snapshot])
                    for topic in ("ticks", "accounts", "positions", "orders", "trades"):
                        pub.send_pyobj([topic, snapshot.get(topic)])
        except KeyboardInterrupt:
            pass
        finally:
            rep.close(0)
            pub.close(0)
            ctx.term()
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate multiple QMT client strategy exports behind one old REP/PUB interface.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.user.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    service = QmtSrv(config)
    if not service.instances:
        print("QMT_SRV_NO_INSTANCES: configure qmt_instances in config.user.json", file=sys.stderr)
    return service.serve()


if __name__ == "__main__":
    raise SystemExit(main())
