from __future__ import annotations

import argparse
import csv
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
ADJUST_FOLDERS: dict[Any, str] = {
    "前复权": "前复权",
    "不复权": "不复权",
    "后复权": "后复权",
    "front": "前复权",
    "back": "后复权",
    "none": "不复权",
    "": "不复权",
    "forward": "前复权",
    "backward": "后复权",
    "qfq": "前复权",
    "hfq": "后复权",
    0: "不复权",
    1: "前复权",
    2: "后复权",
}
MINUTE_PERIODS = {"tick", "1m", "5m", "15m", "30m", "60m", "1h"}
DOWNLOAD_METHODS = {
    "download_history_data",
    "download_history_data2",
    "xtdata.download_history_data",
    "xtdata.download_history_data2",
    "xtdata.download_financial_data",
    "xtdata.download_financial_data2",
}


class MissingPickleObject:
    value: Any = None

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        obj = object.__new__(cls)
        obj.args = args
        obj.kwargs = kwargs
        obj.value = args[0] if args else cls.__name__
        return obj

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, dict):
            self.__dict__.update(state)
            if self.value is None:
                self.value = state.get("value") or state.get("name") or state.get("_name_")

    def __str__(self) -> str:
        return str(self.value or self.__class__.__name__)

    def __repr__(self) -> str:
        return str(self)


class LooseUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> type[Any]:
        if module.startswith("vnpy."):
            return type(str(name), (MissingPickleObject,), {"__module__": module})
        try:
            return super().find_class(module, name)
        except Exception:
            return type(str(name), (MissingPickleObject,), {"__module__": module})


@dataclass
class QmtInstance:
    instance_id: str
    python_dir: Path
    export_dir: Path
    accounts: list[dict[str, Any]]
    settings: dict[str, Any]

    @property
    def command_file(self) -> Path:
        return self.export_dir / "commands" / "inbox.jsonl"

    @property
    def snapshot_file(self) -> Path:
        return self.export_dir / "snapshots" / "latest.json"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def read_json(path: Path, default: Any, *, strict: bool = False) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
        return json.loads(strip_json_comments(text))
    except FileNotFoundError:
        if strict:
            raise
        return default
    except Exception as exc:
        if strict:
            raise ValueError(f"failed to parse JSON file {path}: {exc}") from exc
        print(f"QMT_SRV_JSON_IGNORED path={path} error={exc}", file=sys.stderr)
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True))
        fh.write("\n")


def strip_json_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    length = len(text)
    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""
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
            while index < length and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2 if index + 1 < length else 0
            continue
        if char == "#" and (not result or result[-1] in "\r\n"):
            index += 1
            while index < length and text[index] not in "\r\n":
                index += 1
            continue
        result.append(char)
        index += 1
    return "".join(result)


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


def value_from(value: Any, *names: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        for name in names:
            if name in value and value.get(name) not in (None, ""):
                return value.get(name)
        return default
    for name in names:
        try:
            item = getattr(value, name)
            if item not in (None, ""):
                return item
        except Exception:
            pass
    return default


def enum_value(value: Any) -> Any:
    try:
        return value.value
    except Exception:
        return value


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
        result.extend(extract_symbols(value_from(value, key)))
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
    account_id = value_from(value, "account_id", "accountId", "accountID", "acc_id", "accID", "account", "fund_account")
    if account_id:
        return dedupe_accounts(
            [
                {
                    "account_id": str(account_id).strip(),
                    "account_type": str(value_from(value, "account_type", "accountType", "type", default="STOCK") or "STOCK"),
                    "name": str(value_from(value, "name", "label", default="") or ""),
                }
            ]
        )
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
    if path.name.lower() in {"bin.x64", "userdata", "userdata_mini"}:
        path = path.parent
    if path.suffix.lower() == ".exe":
        path = path.parent
    candidate = path / "python"
    return candidate if candidate.exists() else path


def resolve_qmt_python_dir(path_value: Any) -> Path:
    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if path.name.lower() == "python":
        return path
    if path.name.lower() in {"bin.x64", "userdata", "userdata_mini"}:
        path = path.parent
    if path.suffix.lower() == ".exe":
        path = path.parent
    return path / "python"


def normalized_path_text(path_value: Any) -> str:
    text = str(path_value or "").strip()
    if not text:
        return ""
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve(strict=False)
        return os.path.normcase(os.path.normpath(str(path)))
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(os.path.normpath(text))


def qmt_path_match_values(path_value: Any) -> set[str]:
    text = str(path_value or "").strip()
    if not text:
        return set()
    values = {text, normalized_path_text(text)}
    try:
        python_dir = resolve_qmt_python_dir(text)
        values.add(normalized_path_text(python_dir))
        values.add(normalized_path_text(python_dir.parent))
    except (OSError, RuntimeError, ValueError):
        pass
    return {value for value in values if value}


def normalize_instances(config: dict[str, Any]) -> list[QmtInstance]:
    raw_instances = config.get("qmt_instances") or config.get("instances") or config.get("qmt_dirs") or []
    instances: list[QmtInstance] = []
    for index, raw in enumerate(normalize_list(raw_instances), start=1):
        if isinstance(raw, str):
            raw = {"qmt_path": raw}
        if not isinstance(raw, dict):
            continue
        python_value = raw.get("python_dir") or raw.get("qmt_python_dir")
        qmt_path_value = raw.get("qmt_path") or raw.get("qmt_dir") or raw.get("path")
        if python_value:
            python_dir = resolve_python_dir(python_value)
        elif qmt_path_value:
            python_dir = resolve_qmt_python_dir(qmt_path_value)
        else:
            continue
        export_value = raw.get("export_dir")
        export_dir = Path(str(export_value)).expanduser() if export_value else python_dir / "qmt_data_export"
        if not export_dir.is_absolute():
            export_dir = (Path.cwd() / export_dir).resolve()
        instance_id = str(raw.get("instance_id") or raw.get("id") or python_dir.parent.name or f"qmt_{index}").strip()
        accounts = dedupe_accounts([*extract_accounts(raw.get("accounts") or raw.get("account_ids")), *extract_accounts(raw)])
        settings = {
            "qmt_path": str(qmt_path_value or python_value or ""),
            "stock_active": bool(raw.get("stock_active", True)),
            "futures_active": bool(raw.get("futures_active", False)),
            "option_active": bool(raw.get("option_active", False)),
            "simulation": bool(raw.get("simulation", False)),
            "account_type": str(raw.get("account_type") or "STOCK"),
            "account_id": str(raw.get("account_id") or ""),
            "session_id": raw.get("session_id", 1),
            "connect_retries": raw.get("connect_retries", 5),
            "connect_retry_interval": raw.get("connect_retry_interval", 1.0),
        }
        instances.append(QmtInstance(instance_id=instance_id, python_dir=python_dir, export_dir=export_dir, accounts=accounts, settings=settings))
    return instances


def load_config(config_path: Path | None) -> dict[str, Any]:
    path = config_path or (DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else TEMPLATE_CONFIG_PATH)
    config = read_json(path, {}, strict=True)
    if not isinstance(config, dict):
        raise ValueError(f"config root must be a JSON object: {path}")
    rpc_config = config.get("rpc") if isinstance(config.get("rpc"), dict) else {}
    if "rep_address" not in config and rpc_config.get("rep_address"):
        config["rep_address"] = rpc_config["rep_address"]
    if "pub_address" not in config and rpc_config.get("pub_address"):
        config["pub_address"] = rpc_config["pub_address"]
    config.setdefault("rep_address", DEFAULT_REP_ADDRESS)
    config.setdefault("pub_address", DEFAULT_PUB_ADDRESS)
    config.setdefault("snapshot_publish_seconds", 1.0)
    return config


class CsvDataSource:
    def __init__(self, base_path: str, default_adjust: str = "前复权") -> None:
        self.base_path = str(base_path or "").strip()
        self.default_adjust = ADJUST_FOLDERS.get(default_adjust, "前复权")

    @property
    def enabled(self) -> bool:
        return bool(self.base_path)

    def query(
        self,
        symbol: str,
        start_time: Any = "",
        end_time: Any = "",
        period: str = "1d",
        adjust_type: Any = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        period = normalize_period(period)
        if period in MINUTE_PERIODS:
            return self.query_minute(symbol, start_time, end_time)
        return self.query_daily(symbol, start_time, end_time, adjust_type)

    def query_daily(self, symbol: str, start_time: Any, end_time: Any, adjust_type: Any) -> list[dict[str, Any]]:
        adjust = ADJUST_FOLDERS.get(adjust_type, self.default_adjust) if adjust_type is not None else self.default_adjust
        path = Path(self.base_path) / "1day" / adjust / f"{symbol_code(symbol)}.csv"
        if not path.is_file():
            return []
        start_dt = parse_datetime(start_time)
        end_dt = parse_datetime(end_time)
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                dt = parse_datetime(row.get("日期"))
                if dt is None:
                    continue
                dt = dt.replace(hour=15, minute=0, second=0, microsecond=0)
                if start_dt is not None and dt < start_dt:
                    continue
                if end_dt is not None and dt > end_dt:
                    continue
                rows.append(
                    {
                        "time": dt,
                        "open": to_float(row.get("开盘价")),
                        "high": to_float(row.get("最高价")),
                        "low": to_float(row.get("最低价")),
                        "close": to_float(row.get("收盘价")),
                        "volume": to_float(row.get("成交量（股）") or row.get("成交量")),
                        "amount": to_float(row.get("成交额（元）") or row.get("成交额")),
                        "openInterest": 0.0,
                    }
                )
        return rows

    def query_minute(self, symbol: str, start_time: Any, end_time: Any) -> list[dict[str, Any]]:
        start_dt = parse_datetime(start_time)
        end_dt = parse_datetime(end_time)
        start_year = start_dt.year if start_dt else datetime.now().year
        end_year = end_dt.year if end_dt else start_year
        if end_year < start_year:
            end_year = start_year
        rows: list[dict[str, Any]] = []
        for year in range(start_year, end_year + 1):
            path = Path(self.base_path) / "1min" / "前复权" / str(year) / f"{symbol_code(symbol)}.csv"
            if not path.is_file():
                continue
            rows.extend(read_minute_csv(path, start_dt, end_dt))
        return rows


def read_minute_csv(path: Path, start_dt: datetime | None, end_dt: datetime | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            dt = parse_datetime(row.get("时间"))
            if dt is None:
                continue
            if start_dt is not None and dt < start_dt:
                continue
            if end_dt is not None and dt > end_dt:
                continue
            rows.append(
                {
                    "time": dt,
                    "open": to_float(row.get("开盘价")),
                    "high": to_float(row.get("最高价")),
                    "low": to_float(row.get("最低价")),
                    "close": to_float(row.get("收盘价")),
                    "volume": to_float(row.get("成交量")),
                    "amount": to_float(row.get("成交额")),
                    "openInterest": 0.0,
                }
            )
    return rows


def symbol_code(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    return normalized.split(".", 1)[0] if normalized else str(symbol).split(".", 1)[0]


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(enum_value(value)).strip()
    if not text:
        return None
    text = text.replace("/", "-")
    digits = "".join(ch for ch in text if ch.isdigit())
    for fmt_value, fmt in (
        (digits[:14], "%Y%m%d%H%M%S"),
        (digits[:8], "%Y%m%d"),
        (text[:19], "%Y-%m-%d %H:%M:%S"),
        (text[:10], "%Y-%m-%d"),
    ):
        if len(fmt_value) != len(datetime.now().strftime(fmt)):
            continue
        try:
            return datetime.strptime(fmt_value, fmt)
        except ValueError:
            continue
    return None


def normalize_period(value: Any) -> str:
    text = str(enum_value(value) or "").strip().lower()
    mapping = {
        "minute": "1m",
        "min": "1m",
        "1min": "1m",
        "m1": "1m",
        "daily": "1d",
        "day": "1d",
        "d": "1d",
    }
    return mapping.get(text, text or "1d")


def decode_rpc(raw: bytes) -> tuple[str, list[Any], dict[str, Any]]:
    try:
        obj = pickle.loads(raw)
    except Exception:
        try:
            obj = LooseUnpickler(io.BytesIO(raw)).load()
        except Exception as loose_exc:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception as json_exc:
                raise ValueError(f"failed to decode RPC request as pickle or JSON: {loose_exc}") from json_exc
    return decode_rpc_object(obj)


def decode_rpc_object(obj: Any) -> tuple[str, list[Any], dict[str, Any]]:
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
        csv_config = config.get("csv_data_source") if isinstance(config.get("csv_data_source"), dict) else {}
        self.csv_source = CsvDataSource(
            str(csv_config.get("path") or config.get("csv_data_path") or ""),
            str(csv_config.get("default_adjust") or "前复权"),
        )
        self.clients: dict[str, dict[str, Any]] = {}
        self.bridge_snapshots: dict[str, dict[str, Any]] = {}
        self.pending_bridge_commands: dict[str, list[dict[str, Any]]] = {}
        self.bridge_snapshot_ttl = float(config.get("bridge_snapshot_ttl_seconds") or 30.0)
        self.bridge_pipe_address = str(config.get("bridge_pipe_address") or r"\\.\pipe\qmt_srv_bridge")
        self.bridge_pipe_authkey = str(config.get("bridge_pipe_authkey") or "qmt_srv_bridge").encode("utf-8")
        self.bridge_pipe_thread: threading.Thread | None = None
        self.bridge_pipe_listener: Any = None
        self.stop_event = threading.Event()

    def load_snapshot(self, instance: QmtInstance) -> dict[str, Any]:
        bridge_snapshot = self.bridge_snapshots.get(instance.instance_id)
        if isinstance(bridge_snapshot, dict):
            received_at = float(bridge_snapshot.get("_bridge_received_at") or 0.0)
            if received_at <= 0.0 or time.time() - received_at <= self.bridge_snapshot_ttl:
                snapshot = dict(bridge_snapshot)
                snapshot.pop("_bridge_received_at", None)
                snapshot.setdefault("instance", {"id": instance.instance_id, "qmt_root": str(instance.python_dir.parent)})
                snapshot.setdefault("transport", "qmt_bridge")
                return snapshot
        snapshot = read_json(instance.snapshot_file, {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot.setdefault("instance", {"id": instance.instance_id, "qmt_root": str(instance.python_dir.parent)})
        return snapshot

    def update_bridge_snapshot(self, args: list[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        snapshot = args[0] if args and isinstance(args[0], dict) else kwargs.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("qmt_bridge.update requires a snapshot object")
        instance_meta = snapshot.get("instance") if isinstance(snapshot.get("instance"), dict) else {}
        incoming_instance_id = str(kwargs.get("instance_id") or instance_meta.get("id") or "").strip()
        instance_id = incoming_instance_id
        qmt_root = str(instance_meta.get("qmt_root") or "").strip()
        qmt_root_paths = qmt_path_match_values(qmt_root)
        for instance in self.instances:
            if instance.instance_id == incoming_instance_id or (qmt_root_paths and self._matches_instance(instance, qmt_root, qmt_root_paths)):
                instance_id = instance.instance_id
                break
        if instance_id == incoming_instance_id and incoming_instance_id not in {item.instance_id for item in self.instances} and len(self.instances) == 1:
            instance_id = self.instances[0].instance_id
        if not instance_id:
            raise ValueError("qmt_bridge.update requires instance_id")
        safe_snapshot = json_safe(snapshot)
        if not isinstance(safe_snapshot, dict):
            raise ValueError("qmt_bridge.update snapshot is not serializable")
        if incoming_instance_id and incoming_instance_id != instance_id:
            safe_snapshot["bridge_instance_id"] = incoming_instance_id
        safe_snapshot["_bridge_received_at"] = time.time()
        safe_snapshot["transport"] = "qmt_bridge"
        self.bridge_snapshots[instance_id] = safe_snapshot
        self.persist_bridge_snapshot(instance_id, safe_snapshot)
        fetch_commands = bool(kwargs.get("fetch_commands", True))
        commands = self.pending_bridge_commands.pop(instance_id, []) if fetch_commands else []
        if incoming_instance_id and incoming_instance_id != instance_id:
            routed_commands = []
            for command in commands:
                routed = dict(command)
                routed["server_instance_id"] = instance_id
                routed["instance_id"] = incoming_instance_id
                routed_commands.append(routed)
            commands = routed_commands
        return {"ok": True, "instance_id": instance_id, "commands": commands}

    def persist_bridge_snapshot(self, instance_id: str, snapshot: dict[str, Any]) -> None:
        instance = next((item for item in self.instances if item.instance_id == instance_id), None)
        if instance is None:
            return
        try:
            persisted = dict(snapshot)
            persisted.pop("_bridge_received_at", None)
            persisted["persisted_at"] = now_text()
            write_json(instance.snapshot_file, persisted)
            append_jsonl(instance.export_dir / "bridge" / f"snapshots_{datetime.now().strftime('%Y%m%d')}.jsonl", persisted)
        except Exception as exc:
            print(f"QMT_SRV_BRIDGE_PERSIST_ERROR instance_id={instance_id} error={exc}", file=sys.stderr)

    def start_bridge_pipe(self) -> None:
        if os.name != "nt" or not self.bridge_pipe_address:
            return
        if self.bridge_pipe_thread is not None and self.bridge_pipe_thread.is_alive():
            return

        def run() -> None:
            try:
                from multiprocessing.connection import Listener

                listener = Listener(self.bridge_pipe_address, family="AF_PIPE", authkey=self.bridge_pipe_authkey)
                self.bridge_pipe_listener = listener
                print(f"QMT_SRV_BRIDGE_PIPE_STARTED address={self.bridge_pipe_address}")
                while not self.stop_event.is_set():
                    conn = listener.accept()
                    try:
                        method, args, kwargs = decode_rpc_object(conn.recv())
                        payload = self.handle_rpc(method, args, kwargs)
                        conn.send([True, json_safe(payload)])
                    except Exception as exc:
                        try:
                            conn.send([False, str(exc)])
                        except Exception:
                            pass
                        print(f"QMT_SRV_BRIDGE_PIPE_ERROR error={exc}", file=sys.stderr)
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass
            except Exception as exc:
                if not self.stop_event.is_set():
                    print(f"QMT_SRV_BRIDGE_PIPE_DISABLED error={exc}", file=sys.stderr)

        self.bridge_pipe_thread = threading.Thread(target=run, name="qmt-srv-bridge-pipe", daemon=True)
        self.bridge_pipe_thread.start()

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
            "config": self.config_status(),
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

    def config_status(self) -> dict[str, Any]:
        rpc_config = self.config.get("rpc") if isinstance(self.config.get("rpc"), dict) else {}
        legacy_sections = {}
        for name in ("rpc", "csv_data_source", "data_download", "logging"):
            legacy_sections[name] = isinstance(self.config.get(name), dict)
        return {
            "rep_address": self.rep_address,
            "pub_address": self.pub_address,
            "legacy_sections": legacy_sections,
            "notes": {
                "rpc_worker_queues": "accepted for config compatibility; qmt_srv now uses one ZMQ frontend and QMT strategy export files",
                "csv_data_source": "used as the historical-data fallback for query_history and xtdata.get_market_data_ex",
                "data_download": "accepted for compatibility; qmt_srv no longer performs direct xtdata downloads",
                "logging": "accepted for compatibility; only basic service console output is currently implemented",
            },
            "csv_data_source": {"enabled": self.csv_source.enabled, "path": self.csv_source.base_path},
            "rpc": {
                "trade_workers": rpc_config.get("trade_workers"),
                "fast_workers": rpc_config.get("fast_workers"),
                "slow_workers": rpc_config.get("slow_workers"),
            },
        }

    def instance_meta(self, instance: QmtInstance) -> dict[str, Any]:
        return {
            "instance_id": instance.instance_id,
            "python_dir": str(instance.python_dir),
            "export_dir": str(instance.export_dir),
            "snapshot_file": str(instance.snapshot_file),
            "command_file": str(instance.command_file),
            "accounts": instance.accounts,
            "settings": instance.settings,
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

    @staticmethod
    def _extract_instance_selector(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        for key in ("instance_id", "qmt_instance", "qmt_path", "qmt_dir", "python_dir", "qmt_python_dir"):
            selected = str(value.get(key) or "").strip()
            if selected:
                return selected
        return ""

    @staticmethod
    def _matches_instance(instance: QmtInstance, wanted: str, wanted_paths: set[str]) -> bool:
        if instance.instance_id == wanted:
            return True
        raw_paths = {
            str(instance.python_dir),
            str(instance.python_dir.parent),
            str(instance.settings.get("qmt_path") or ""),
        }
        if wanted in raw_paths:
            return True
        instance_paths: set[str] = set()
        for raw_path in raw_paths:
            instance_paths.update(qmt_path_match_values(raw_path))
        return bool(wanted_paths & instance_paths)

    def target_instances(self, args: list[Any], kwargs: dict[str, Any]) -> list[QmtInstance]:
        wanted = self._extract_instance_selector(kwargs)
        if not wanted:
            for value in args:
                wanted = self._extract_instance_selector(value)
                if wanted:
                    break
        if not wanted or wanted in ("*", "all"):
            return self.instances
        wanted_paths = qmt_path_match_values(wanted)
        return [item for item in self.instances if self._matches_instance(item, wanted, wanted_paths)]

    def write_command(self, instance: QmtInstance, method: str, args: list[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        command = {
            "id": str(uuid.uuid4()),
            "time": now_text(),
            "instance_id": instance.instance_id,
            "method": method,
            "args": json_safe(args),
            "kwargs": json_safe(kwargs),
        }
        self.pending_bridge_commands.setdefault(instance.instance_id, []).append(command)
        self.pending_bridge_commands[instance.instance_id] = self.pending_bridge_commands[instance.instance_id][-200:]
        try:
            instance.command_file.parent.mkdir(parents=True, exist_ok=True)
            with instance.command_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(command, ensure_ascii=False, sort_keys=True))
                fh.write("\n")
        except Exception as exc:
            print(f"QMT_SRV_COMMAND_FILE_WRITE_ERROR instance_id={instance.instance_id} error={exc}", file=sys.stderr)
        return command

    def route_command(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> list[dict[str, Any]]:
        commands = []
        for instance in self.target_instances(args, kwargs):
            commands.append(self.write_command(instance, method, args, kwargs))
        return commands

    def handle_rpc(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if method in {"qmt_bridge.update", "qmt_bridge.snapshot"}:
            return self.update_bridge_snapshot(args, kwargs)
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
        if method in DOWNLOAD_METHODS:
            return True
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
        params = self.history_params(request)
        symbols = params["symbols"] or list(aggregate["ticks"].keys())
        period = params["period"]
        result = {}
        for symbol in symbols:
            result[symbol] = (
                aggregate["histories"].get(f"{symbol}|{period}")
                or aggregate["histories"].get(symbol)
                or self.csv_source.query(
                    symbol,
                    params["start_time"],
                    params["end_time"],
                    period,
                    params["adjust_type"],
                )
                or []
            )
        return result

    def query_market_data(self, args: list[Any], kwargs: dict[str, Any], aggregate: dict[str, Any]) -> dict[str, Any]:
        stock_list = args[1] if len(args) >= 2 else kwargs.get("stock_list") or kwargs.get("code_list") or list(aggregate["ticks"].keys())
        period = normalize_period(kwargs.get("period") or (args[2] if len(args) >= 3 else "1m"))
        start_time = kwargs.get("start_time") or kwargs.get("start") or (args[3] if len(args) >= 4 else "")
        end_time = kwargs.get("end_time") or kwargs.get("end") or (args[4] if len(args) >= 5 else "")
        adjust_type = kwargs.get("dividend_type") or kwargs.get("adjust_type") or kwargs.get("adjust")
        result = {}
        for raw in normalize_list(stock_list):
            symbol = normalize_symbol(raw)
            result[symbol] = (
                aggregate["histories"].get(f"{symbol}|{period}")
                or aggregate["histories"].get(symbol)
                or self.csv_source.query(symbol, start_time, end_time, period, adjust_type)
                or []
            )
        return result

    def history_params(self, request: Any) -> dict[str, Any]:
        start = value_from(request, "start", "start_time", "startTime", default="")
        end = value_from(request, "end", "end_time", "endTime", default="")
        period = value_from(request, "period", "interval", "frequency", default="1m")
        adjust = value_from(request, "dividend_type", "adjust_type", "adjust", default=None)
        symbols = extract_symbols(request)
        return {
            "symbols": symbols,
            "period": normalize_period(period),
            "start_time": start,
            "end_time": end,
            "adjust_type": adjust,
        }

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
        self.start_bridge_pipe()
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
            try:
                if self.bridge_pipe_listener is not None:
                    self.bridge_pipe_listener.close()
            except Exception:
                pass
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
    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"QMT_SRV_CONFIG_ERROR: {exc}", file=sys.stderr)
        return 2
    service = QmtSrv(config)
    if not service.instances:
        print("QMT_SRV_NO_INSTANCES: configure qmt_instances in config.user.json", file=sys.stderr)
    return service.serve()


if __name__ == "__main__":
    raise SystemExit(main())
