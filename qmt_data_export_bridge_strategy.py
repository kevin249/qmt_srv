# -*- coding: gbk -*-
from __future__ import print_function

import csv
import datetime as dt
import io
import json
import os
import pickle
import threading
import time
import traceback

SYMBOLS = []
OUTPUT_DIRNAME = "qmt_data_export"
OUTPUT_ROOT = ""
INSTANCE_ID = ""
CONFIG_FILENAME = "qmt_data_export_bridge_config.json"
COMMAND_FILENAME = "inbox.jsonl"
DIVIDEND_TYPE = "front"
ACCOUNT_ID = ""
ACCOUNT_TYPE = "STOCK"
ACCOUNT_IDS = []
ACCOUNT_CONFIGS = []
HISTORY_PERIODS = ["1m", "1d"]
HISTORY_COUNT = 240
STATIC_REFRESH_SECONDS = 600
SNAPSHOT_REFRESH_SECONDS = 3
TRADE_REFRESH_SECONDS = 5
WRITE_DUPLICATE_TICKS = False
SUBSCRIBE_TICK_IN_INIT = True
BRIDGE_VERSION = "20260706_fileio_bridge_v2"

try:
    print("QMT_DATA_EXPORT_MODULE_LOADED version=%s file=%s" % (BRIDGE_VERSION, globals().get("__file__", "")))
except Exception:
    pass

ENABLE_LEGACY_ZMQ = False
LEGACY_REP_ADDRESS = "tcp://*:20140"
LEGACY_PUB_ADDRESS = "tcp://*:20141"

BRIDGE_ENABLED = True
BRIDGE_REP_ADDRESS = "tcp://127.0.0.1:20140"
BRIDGE_PIPE_ADDRESS = r"\\.\pipe\qmt_srv_bridge"
BRIDGE_AUTHKEY = "qmt_srv_bridge"
BRIDGE_TIMEOUT_MS = 300
BRIDGE_PUSH_SECONDS = 1.0
FILE_OUTPUT_ENABLED = True
_FILE_OUTPUT_DISABLED = False
_FILE_OUTPUT_DISABLE_LOGGED = False
_BOOTSTRAP_INIT_CALLED = False
_LAST_BRIDGE_PUSH_AT = 0
_LAST_BRIDGE_ERROR_AT = 0

BAR_FIELDS = ["time", "open", "high", "low", "close", "volume", "amount"]
TICK_COLUMNS = [
    "local_dt", "source", "symbol", "tick_time", "tick_dt", "last_price", "open", "high", "low",
    "pre_close", "amount", "volume", "pvolume", "stock_status", "open_int",
    "bid_price1", "bid_price2", "bid_price3", "bid_price4", "bid_price5",
    "ask_price1", "ask_price2", "ask_price3", "ask_price4", "ask_price5",
    "bid_vol1", "bid_vol2", "bid_vol3", "bid_vol4", "bid_vol5",
    "ask_vol1", "ask_vol2", "ask_vol3", "ask_vol4", "ask_vol5", "raw_json",
]
BAR_COLUMNS = ["local_dt", "source", "symbol", "period", "stime"] + BAR_FIELDS + ["raw_json"]

_CONTEXT = None
_LOCK = threading.RLock()
_CACHE = {
    "ticks": {}, "histories": {}, "contracts": {}, "financial": {}, "sectors": {}, "calendar": {},
    "accounts": [], "positions": [], "orders": [], "trades": [], "clients": {}, "subscriptions": [],
    "network": {}, "meta": {}, "errors": [],
}
_LAST_TICK_SIG = {}
_LAST_BAR_SIG = {}
_RUNTIME_SYMBOLS = []
_RUNTIME_ACCOUNTS = []
_SUBSCRIBED_SYMBOLS = set()
_LAST_SNAPSHOT_AT = 0
_LAST_STATIC_AT = 0
_LAST_TRADE_AT = 0
_COMMAND_OFFSET = 0
_COMMAND_FILE_DISABLED = False
_COMMAND_FILE_DISABLE_LOGGED = False
_ROW_COUNTS = {"ticks": 0, "bars": 0, "events": 0, "rpc": 0, "commands": 0, "errors": 0}
_ZMQ_THREAD = None
_ZMQ_STOP = threading.Event()
_ZMQ_CONTEXT = None
_REP_SOCKET = None
_PUB_SOCKET = None
_ZMQ = None
_CONFIG_LOADED = False
_CONFIG = {}


def _now():
    return dt.datetime.now()


def _now_text():
    return _now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _today():
    return _now().strftime("%Y%m%d")


def _strategy_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return os.getcwd()


def _runtime_config_path():
    primary = os.path.join(_strategy_dir(), CONFIG_FILENAME)
    if os.path.exists(primary):
        return primary
    alternate = os.path.join(_qmt_root_dir(), "python", CONFIG_FILENAME)
    if os.path.exists(alternate):
        return alternate
    return primary


def _cfg_bool(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _cfg_int(value, default):
    try:
        return int(value)
    except Exception:
        return default


def _cfg_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        if ("," in value) or (";" in value) or (" " in value):
            return [x.strip() for x in value.replace(";", ",").replace(" ", ",").split(",") if x.strip()]
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _read_config_json(path):
    if not os.path.exists(path):
        return {}
    last_error = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            last_error = exc
    print("QMT_DATA_EXPORT_CONFIG_IGNORED path=%s error=%s" % (path, last_error))
    return {}


def _load_runtime_config(force=False):
    global _CONFIG_LOADED, _CONFIG, SYMBOLS, OUTPUT_DIRNAME, OUTPUT_ROOT, INSTANCE_ID, COMMAND_FILENAME
    global DIVIDEND_TYPE, ACCOUNT_ID, ACCOUNT_TYPE, ACCOUNT_IDS, ACCOUNT_CONFIGS
    global HISTORY_PERIODS, HISTORY_COUNT, STATIC_REFRESH_SECONDS, SNAPSHOT_REFRESH_SECONDS, TRADE_REFRESH_SECONDS
    global WRITE_DUPLICATE_TICKS, SUBSCRIBE_TICK_IN_INIT, ENABLE_LEGACY_ZMQ, LEGACY_REP_ADDRESS, LEGACY_PUB_ADDRESS
    global BRIDGE_ENABLED, BRIDGE_REP_ADDRESS, BRIDGE_PIPE_ADDRESS, BRIDGE_AUTHKEY, BRIDGE_TIMEOUT_MS, BRIDGE_PUSH_SECONDS, FILE_OUTPUT_ENABLED
    if _CONFIG_LOADED and not force:
        return _CONFIG
    _CONFIG_LOADED = True
    config = _read_config_json(_runtime_config_path())
    _CONFIG = config
    if not config:
        return _CONFIG
    missing = object()
    def pick(*names):
        for name in names:
            if name in config:
                return config.get(name)
        return missing
    value = pick("symbols", "default_symbols")
    if value is not missing:
        SYMBOLS = _cfg_list(value)
    value = pick("output_dirname", "output_dir")
    if value is not missing:
        OUTPUT_DIRNAME = str(value or OUTPUT_DIRNAME)
    value = pick("output_root")
    if value is not missing:
        OUTPUT_ROOT = str(value or "")
    value = pick("instance_id", "instance")
    if value is not missing:
        INSTANCE_ID = str(value or "").strip()
    value = pick("command_filename")
    if value is not missing:
        COMMAND_FILENAME = str(value or COMMAND_FILENAME)
    value = pick("dividend_type")
    if value is not missing:
        DIVIDEND_TYPE = str(value or DIVIDEND_TYPE)
    value = pick("account_id", "account")
    if value is not missing:
        ACCOUNT_ID = str(value or "").strip()
    value = pick("account_type")
    if value is not missing:
        ACCOUNT_TYPE = str(value or ACCOUNT_TYPE).strip() or ACCOUNT_TYPE
    value = pick("account_ids")
    if value is not missing:
        ACCOUNT_IDS = _cfg_list(value)
    value = pick("accounts", "account_configs")
    if value is not missing:
        ACCOUNT_CONFIGS = _cfg_list(value)
    value = pick("history_periods")
    if value is not missing:
        HISTORY_PERIODS = [str(x) for x in _cfg_list(value) if str(x)]
    value = pick("history_count")
    if value is not missing:
        HISTORY_COUNT = _cfg_int(value, HISTORY_COUNT)
    value = pick("static_refresh_seconds")
    if value is not missing:
        STATIC_REFRESH_SECONDS = _cfg_int(value, STATIC_REFRESH_SECONDS)
    value = pick("snapshot_refresh_seconds")
    if value is not missing:
        SNAPSHOT_REFRESH_SECONDS = _cfg_int(value, SNAPSHOT_REFRESH_SECONDS)
    value = pick("trade_refresh_seconds")
    if value is not missing:
        TRADE_REFRESH_SECONDS = _cfg_int(value, TRADE_REFRESH_SECONDS)
    value = pick("write_duplicate_ticks")
    if value is not missing:
        WRITE_DUPLICATE_TICKS = _cfg_bool(value, WRITE_DUPLICATE_TICKS)
    value = pick("subscribe_tick_in_init")
    if value is not missing:
        SUBSCRIBE_TICK_IN_INIT = _cfg_bool(value, SUBSCRIBE_TICK_IN_INIT)
    legacy = config.get("legacy_zmq") or config.get("legacy") or {}
    if not isinstance(legacy, dict):
        legacy = {}
    value = pick("enable_legacy_zmq", "legacy_enabled")
    if value is missing:
        value = legacy.get("enabled", missing)
    if value is not missing:
        ENABLE_LEGACY_ZMQ = _cfg_bool(value, ENABLE_LEGACY_ZMQ)
    value = pick("legacy_rep_address", "rep_address")
    if value is missing:
        value = legacy.get("rep_address", missing)
    if value is not missing and value:
        LEGACY_REP_ADDRESS = str(value)
    value = pick("legacy_pub_address", "pub_address")
    if value is missing:
        value = legacy.get("pub_address", missing)
    if value is not missing and value:
        LEGACY_PUB_ADDRESS = str(value)
    bridge = config.get("bridge") or config.get("qmt_srv_bridge") or {}
    if not isinstance(bridge, dict):
        bridge = {}
    value = pick("bridge_enabled")
    if value is missing:
        value = bridge.get("enabled", missing)
    if value is not missing:
        BRIDGE_ENABLED = _cfg_bool(value, BRIDGE_ENABLED)
    value = pick("bridge_rep_address")
    if value is missing:
        value = bridge.get("rep_address", missing)
    if value is not missing and value:
        BRIDGE_REP_ADDRESS = str(value)
    value = pick("bridge_pipe_address")
    if value is missing:
        value = bridge.get("pipe_address", missing)
    if value is not missing and value:
        BRIDGE_PIPE_ADDRESS = str(value)
    value = pick("bridge_authkey")
    if value is missing:
        value = bridge.get("authkey", missing)
    if value is not missing and value:
        BRIDGE_AUTHKEY = str(value)
    value = pick("bridge_timeout_ms")
    if value is missing:
        value = bridge.get("timeout_ms", missing)
    if value is not missing:
        BRIDGE_TIMEOUT_MS = _cfg_int(value, BRIDGE_TIMEOUT_MS)
    value = pick("bridge_push_seconds")
    if value is missing:
        value = bridge.get("push_seconds", missing)
    if value is not missing:
        try:
            BRIDGE_PUSH_SECONDS = float(value)
        except Exception:
            pass
    value = pick("file_output_enabled")
    if value is not missing:
        FILE_OUTPUT_ENABLED = _cfg_bool(value, FILE_OUTPUT_ENABLED)
    return _CONFIG


def _qmt_root_dir():
    path = _strategy_dir()
    if os.path.basename(path).lower() in ("python", "bin.x64", "userdata", "userdata_mini"):
        return os.path.dirname(path)
    return path


def _safe_filename(text):
    text = str(text or "").strip()
    if not text:
        return "qmt"
    bad = '<>:"/\\|?*'
    return "".join(["_" if ch in bad else ch for ch in text])


def _instance_id():
    _load_runtime_config()
    if str(INSTANCE_ID or "").strip():
        return str(INSTANCE_ID).strip()
    root = _qmt_root_dir()
    return os.path.basename(root) or os.path.basename(_strategy_dir()) or "qmt"


def _output_root():
    _load_runtime_config()
    instance = _safe_filename(_instance_id())
    root = str(OUTPUT_ROOT or "").strip()
    if root:
        root = root.replace("{instance_id}", instance)
        return os.path.abspath(os.path.expandvars(root))
    dirname = str(OUTPUT_DIRNAME or "qmt_data_export").replace("{instance_id}", instance)
    if os.path.isabs(dirname):
        return os.path.abspath(os.path.expandvars(dirname))
    python_dir = os.path.join(_qmt_root_dir(), "python")
    base_dir = python_dir if os.path.isdir(python_dir) else _strategy_dir()
    return os.path.join(base_dir, dirname)


def _path(*parts):
    return os.path.join(_output_root(), *parts)


def _is_forbidden_fileio(exc):
    text = str(exc or "").lower()
    return "foribdden fileio" in text or "forbidden fileio" in text


def _disable_file_output(where, exc):
    global _FILE_OUTPUT_DISABLED, _FILE_OUTPUT_DISABLE_LOGGED
    _FILE_OUTPUT_DISABLED = True
    if not _FILE_OUTPUT_DISABLE_LOGGED:
        _FILE_OUTPUT_DISABLE_LOGGED = True
        print("QMT_DATA_EXPORT_FILE_OUTPUT_DISABLED where=%s error=%s" % (where, exc))


def _file_output_available():
    return bool(FILE_OUTPUT_ENABLED) and not _FILE_OUTPUT_DISABLED


def _ensure_dir(path):
    if not _file_output_available():
        return False
    try:
        if path and not os.path.isdir(path):
            os.makedirs(path)
        return True
    except Exception as exc:
        if _is_forbidden_fileio(exc):
            _disable_file_output("ensure_dir", exc)
            return False
        raise


def _ensure_parent(path):
    parent = os.path.dirname(path)
    if parent:
        return _ensure_dir(parent)
    return True


def _json_safe(value, depth=0):
    if depth > 6:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        if hasattr(value, "item"):
            return value.item()
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v, depth + 1) for v in list(value)]
    try:
        name = value.__class__.__name__
        if name == "DataFrame":
            return {
                "type": "DataFrame",
                "index": [str(x) for x in list(value.index)],
                "columns": [str(x) for x in list(value.columns)],
                "records": _json_safe(value.to_dict(orient="records"), depth + 1),
            }
        if name == "Series":
            return {str(k): _json_safe(v, depth + 1) for k, v in value.to_dict().items()}
        if hasattr(value, "to_dict"):
            return _json_safe(value.to_dict(), depth + 1)
    except Exception:
        pass
    try:
        return _json_safe(vars(value), depth + 1)
    except Exception:
        return str(value)


def _write_json(path, payload):
    if not _file_output_available():
        return False
    try:
        if not _ensure_parent(path):
            return False
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_json_safe(payload), fh, ensure_ascii=False, indent=2, sort_keys=True)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
        os.rename(tmp, path)
        return True
    except Exception as exc:
        if _is_forbidden_fileio(exc):
            _disable_file_output("write_json", exc)
            return False
        raise


def _append_jsonl(path, payload):
    if not _file_output_available():
        return False
    try:
        if not _ensure_parent(path):
            return False
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True))
            fh.write("\n")
        return True
    except Exception as exc:
        if _is_forbidden_fileio(exc):
            _disable_file_output("append_jsonl", exc)
            return False
        raise


def _append_csv(path, row, columns):
    if not _file_output_available():
        return False
    try:
        if not _ensure_parent(path):
            return False
        has_header = os.path.exists(path) and os.path.getsize(path) > 0
        with open(path, "a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            if not has_header:
                writer.writeheader()
            writer.writerow(row)
            fh.flush()
        return True
    except Exception as exc:
        if _is_forbidden_fileio(exc):
            _disable_file_output("append_csv", exc)
            return False
        raise


def _log_error(where, exc):
    _ROW_COUNTS["errors"] += 1
    item = {"time": _now_text(), "where": where, "error": str(exc), "traceback": traceback.format_exc()}
    with _LOCK:
        _CACHE["errors"].append(item)
        _CACHE["errors"] = _CACHE["errors"][-200:]
    try:
        _append_jsonl(_path("events", "errors_%s.jsonl" % _today()), item)
    except Exception:
        pass
    if _ROW_COUNTS["errors"] <= 5 or _ROW_COUNTS["errors"] % 50 == 0:
        print("QMT_DATA_EXPORT_ERROR where=%s error=%s" % (where, exc))


def _market_alias(raw):
    text = str(raw or "").strip().upper()
    aliases = {
        "SH": "SH", "SHSE": "SH", "SSE": "SH", "SS": "SH",
        "SZ": "SZ", "SZSE": "SZ", "SZE": "SZ",
        "BJ": "BJ", "BSE": "BJ", "BJS": "BJ",
    }
    return aliases.get(text, text[:2] if text in ("SH", "SZ", "BJ") else "")


def _symbol(raw):
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        left, right = text.split(".", 1)
        left_digits = "".join([ch for ch in left if ch.isdigit()])
        right_digits = "".join([ch for ch in right if ch.isdigit()])
        if len(left_digits) >= 6:
            market = _market_alias(right) or _infer_market(left_digits[:6])
            return left_digits[:6] + "." + market
        if len(right_digits) >= 6:
            market = _market_alias(left) or _infer_market(right_digits[:6])
            return right_digits[:6] + "." + market
        return ""
    digits = "".join([ch for ch in text if ch.isdigit()])
    if len(digits) >= 6:
        code = digits[:6]
        return code + "." + _infer_market(code)
    return ""


def _infer_market(code):
    if code.startswith(("4", "8")):
        return "BJ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _symbols():
    result = []
    for raw in list(SYMBOLS) + list(_RUNTIME_SYMBOLS):
        s = _symbol(raw)
        if s and s not in result:
            result.append(s)
    return result


def _coerce_symbol_items(value):
    items = []
    if value is None:
        return items
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return items
        if ("," in text) or (";" in text) or (" " in text):
            for part in text.replace(";", ",").replace(" ", ",").split(","):
                if part.strip():
                    items.append(part.strip())
            return items
        return [text]
    if isinstance(value, dict):
        for key in (
            "vt_symbol", "vtSymbol", "symbol", "stock", "stock_code", "stockCode", "code",
            "instrument", "secid", "security", "security_id", "securityId", "wind_code", "WindCode",
            "name", "stock_name", "stockName",
        ):
            if key in value:
                items.extend(_coerce_symbol_items(value.get(key)))
        for key in (
            "vt_symbols", "vtSymbols", "symbols", "stocks", "stock_list", "stockList",
            "stock_code_list", "stockCodeList", "code_list", "codeList", "codes",
            "instruments", "securities", "names", "stock_names", "stockNames",
        ):
            if key in value:
                items.extend(_coerce_symbol_items(value.get(key)))
        return items
    if isinstance(value, (list, tuple, set)):
        for item in value:
            items.extend(_coerce_symbol_items(item))
        return items
    for key in (
        "vt_symbol", "vtSymbol", "symbol", "stock", "stock_code", "stockCode", "code",
        "instrument", "secid", "security", "security_id", "securityId", "wind_code", "WindCode",
        "name", "stock_name", "stockName",
    ):
        try:
            item = getattr(value, key)
            if item:
                items.extend(_coerce_symbol_items(item))
        except Exception:
            pass
    return items


def _normalize_symbol_list(*values):
    result = []
    for value in values:
        for raw in _coerce_symbol_items(value):
            symbol = _symbol(raw)
            if symbol and symbol not in result:
                result.append(symbol)
    return result


def _write_runtime_symbols(source="", added=None):
    try:
        _write_json(_path("metadata", "runtime_symbols.json"), {
            "updated_at": _now_text(),
            "source": source,
            "added": added or [],
            "symbols": _symbols(),
            "default_symbols": [_symbol(s) for s in SYMBOLS if _symbol(s)],
        })
    except Exception as exc:
        _log_error("write_runtime_symbols", exc)


def _set_universe_safe(context):
    if context is None:
        return
    stocks = _symbols()
    if not stocks:
        return
    try:
        setter = getattr(context, "set_universe", None)
        if callable(setter):
            setter(stocks)
    except Exception as exc:
        _log_error("set_universe", exc)


def _add_runtime_symbols(raw_values, source="runtime"):
    requested = _normalize_symbol_list(raw_values)
    added = []
    if not requested:
        return added
    with _LOCK:
        known = set(_symbols())
        for symbol in requested:
            if symbol not in known:
                _RUNTIME_SYMBOLS.append(symbol)
                known.add(symbol)
                added.append(symbol)
    if added:
        _write_runtime_symbols(source, added)
        _set_universe_safe(_CONTEXT)
        if _CONTEXT is not None:
            _subscribe(_CONTEXT, added)
        _publish("log", {"event": "runtime_symbols_added", "source": source, "symbols": added, "time": _now_text()})
        print("QMT_DATA_EXPORT_SYMBOLS_ADDED source=%s symbols=%s" % (source, ",".join(added)))
    return added

def _data_get(data, names, default=""):
    if not isinstance(data, dict):
        return default
    for name in names:
        if name in data:
            return data.get(name)
    return default


def _time_pair(value):
    if value in (None, ""):
        return "", ""
    text = str(value).strip()
    digits = "".join([ch for ch in text if ch.isdigit()])
    try:
        number = float(value)
        if number > 1000000000000:
            parsed = dt.datetime.fromtimestamp(number / 1000.0)
            return text, parsed.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        if number > 1000000000:
            parsed = dt.datetime.fromtimestamp(number)
            return text, parsed.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    for length, fmt in ((17, "%Y%m%d%H%M%S%f"), (14, "%Y%m%d%H%M%S"), (8, "%Y%m%d")):
        if len(digits) >= length:
            try:
                parsed = dt.datetime.strptime(digits[:length], fmt)
                return text, parsed.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            except Exception:
                pass
    return text, ""


def _level(data, array_names, key_prefixes, index):
    if not isinstance(data, dict):
        return ""
    for name in array_names:
        value = data.get(name)
        if isinstance(value, (list, tuple)) and len(value) > index:
            return value[index]
    n = index + 1
    for prefix in key_prefixes:
        for key in ("%s%d" % (prefix, n), "%s_%d" % (prefix, n)):
            if key in data:
                return data.get(key)
    return ""


def _normalize_tick_result(result, requested):
    if not isinstance(result, dict):
        return {}
    output = {}
    for stock in requested:
        if isinstance(result.get(stock), dict):
            output[stock] = result.get(stock)
    if output:
        return output
    if len(requested) == 1 and any(k in result for k in ("lastPrice", "last_price", "time", "bidPrice", "askPrice")):
        return {requested[0]: result}
    for key, value in result.items():
        if isinstance(value, dict):
            output[_symbol(key)] = value
    return output


def _fetch_full_ticks(context):
    stocks = _symbols()
    getter = getattr(context, "get_full_tick", None) or globals().get("get_full_tick")
    if not callable(getter):
        return {}
    try:
        ticks = _normalize_tick_result(getter(stocks), stocks)
        if ticks:
            return ticks
    except Exception:
        pass
    found = {}
    for stock in stocks:
        for arg in ([stock], stock):
            try:
                found.update(_normalize_tick_result(getter(arg), [stock]))
                break
            except Exception:
                pass
    return found


def _tick_row(symbol, tick, source):
    tick_time, tick_dt = _time_pair(_data_get(tick, ("time", "datetime", "date", "timetag", "stime")))
    row = {
        "local_dt": _now_text(), "source": source, "symbol": symbol, "tick_time": tick_time, "tick_dt": tick_dt,
        "last_price": _data_get(tick, ("lastPrice", "last_price", "price")),
        "open": _data_get(tick, ("open", "openPrice")), "high": _data_get(tick, ("high", "highPrice")),
        "low": _data_get(tick, ("low", "lowPrice")), "pre_close": _data_get(tick, ("lastClose", "preClose", "pre_close")),
        "amount": _data_get(tick, ("amount", "turnoverValue")), "volume": _data_get(tick, ("volume", "vol")),
        "pvolume": _data_get(tick, ("pvolume", "pVolume")), "stock_status": _data_get(tick, ("stockStatus", "stock_status")),
        "open_int": _data_get(tick, ("openInt", "open_int")),
        "raw_json": json.dumps(_json_safe(tick), ensure_ascii=False, sort_keys=True),
    }
    for i in range(5):
        n = i + 1
        row["bid_price%d" % n] = _level(tick, ("bidPrice", "bid_price"), ("bidPrice", "bid_price", "bid"), i)
        row["ask_price%d" % n] = _level(tick, ("askPrice", "ask_price"), ("askPrice", "ask_price", "ask"), i)
        row["bid_vol%d" % n] = _level(tick, ("bidVol", "bidVolume", "bid_vol"), ("bidVol", "bidVolume", "bid_vol"), i)
        row["ask_vol%d" % n] = _level(tick, ("askVol", "askVolume", "ask_vol"), ("askVol", "askVolume", "ask_vol"), i)
    return row


def _write_ticks(context, ticks, source):
    written = 0
    safe_ticks = {}
    for raw_symbol, tick in (ticks or {}).items():
        symbol = _symbol(raw_symbol)
        if not symbol or not isinstance(tick, dict):
            continue
        sig = "%s|%s|%s|%s|%s" % (
            symbol, _data_get(tick, ("time", "datetime", "date", "timetag", "stime")),
            _data_get(tick, ("lastPrice", "last_price", "price")), _data_get(tick, ("volume", "vol")),
            _data_get(tick, ("amount", "turnoverValue")),
        )
        if not WRITE_DUPLICATE_TICKS and _LAST_TICK_SIG.get(symbol) == sig:
            continue
        _LAST_TICK_SIG[symbol] = sig
        row = _tick_row(symbol, tick, source)
        safe_ticks[symbol] = _json_safe(tick)
        day = (row.get("tick_dt") or row.get("local_dt") or _now_text())[:10].replace("-", "")
        _append_csv(_path("ticks", "qmt_ticks_%s.csv" % day), row, TICK_COLUMNS)
        _append_jsonl(_path("ticks", "qmt_ticks_%s.jsonl" % day), {"symbol": symbol, "source": source, "tick": tick})
        written += 1
    if written:
        with _LOCK:
            _CACHE["ticks"].update(safe_ticks)
        _ROW_COUNTS["ticks"] += written
        _publish("tick", list(safe_ticks.values()))
    return written


def _rows_from_frame(frame):
    rows = []
    if frame is None:
        return rows
    try:
        if hasattr(frame, "iterrows"):
            for index, item in frame.iterrows():
                data = item.to_dict() if hasattr(item, "to_dict") else dict(item)
                data["stime"] = str(index)
                rows.append(_json_safe(data))
            return rows
    except Exception:
        pass
    if isinstance(frame, list):
        return [_json_safe(item) for item in frame]
    if isinstance(frame, dict):
        if any(isinstance(v, (list, tuple)) for v in frame.values()):
            keys = list(frame.keys())
            size = max([len(v) for v in frame.values() if isinstance(v, (list, tuple))] or [0])
            for i in range(size):
                row = {}
                for k in keys:
                    v = frame.get(k)
                    row[k] = v[i] if isinstance(v, (list, tuple)) and i < len(v) else v
                rows.append(_json_safe(row))
        else:
            rows.append(_json_safe(frame))
    return rows


def _extract_history_rows(data, symbol):
    if isinstance(data, dict):
        if symbol in data:
            return _rows_from_frame(data.get(symbol))
        if data:
            return _rows_from_frame(data.get(list(data.keys())[0]))
    return _rows_from_frame(data)


def _fetch_history(context, symbol, period):
    getter = getattr(context, "get_market_data_ex", None)
    if callable(getter):
        try:
            data = getter(BAR_FIELDS, [symbol], period=period, count=HISTORY_COUNT, dividend_type=DIVIDEND_TYPE, fill_data=True, subscribe=False)
            return _extract_history_rows(data, symbol)
        except Exception as exc:
            _log_error("get_market_data_ex %s %s" % (symbol, period), exc)
    getter_old = getattr(context, "get_market_data", None)
    if callable(getter_old):
        try:
            data = getter_old(BAR_FIELDS, stock_code=[symbol], period=period, count=HISTORY_COUNT, dividend_type=DIVIDEND_TYPE)
            return _extract_history_rows(data, symbol)
        except Exception as exc:
            _log_error("get_market_data %s %s" % (symbol, period), exc)
    return []


def _write_history(context, source):
    histories = {}
    written = 0
    for symbol in _symbols():
        for period in HISTORY_PERIODS:
            rows = _fetch_history(context, symbol, period)
            key = "%s|%s" % (symbol, period)
            histories[key] = rows
            _write_json(_path("history", symbol.replace(".", "_"), "%s_latest.json" % period), {
                "symbol": symbol, "period": period, "source": source, "rows": rows, "updated_at": _now_text(),
            })
            if rows:
                latest = rows[-1]
                sig = json.dumps(_json_safe(latest), ensure_ascii=False, sort_keys=True)
                if _LAST_BAR_SIG.get(key) != sig:
                    _LAST_BAR_SIG[key] = sig
                    row = {"local_dt": _now_text(), "source": source, "symbol": symbol, "period": period, "raw_json": sig}
                    if isinstance(latest, dict):
                        row.update(latest)
                    _append_csv(_path("bars", "qmt_bars_%s.csv" % _today()), row, BAR_COLUMNS)
                    written += 1
    with _LOCK:
        _CACHE["histories"].update(histories)
    if written:
        _ROW_COUNTS["bars"] += written
    return written


def _import_xtdata():
    try:
        from xtquant import xtdata
        return xtdata
    except Exception:
        return None


def _safe_call(func, *args, **kwargs):
    quiet = bool(kwargs.pop("_quiet", False))
    if not callable(func):
        return None
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        if not quiet:
            _log_error(getattr(func, "__name__", "call"), exc)
        return None


def _collect_static(context):
    xtdata = _import_xtdata()
    contracts = {}
    financial = {}
    for symbol in _symbols():
        detail = None
        getter = getattr(context, "get_instrumentdetail", None)
        if callable(getter):
            detail = _safe_call(getter, symbol)
        if detail is None and xtdata is not None:
            detail = _safe_call(getattr(xtdata, "get_instrument_detail", None), symbol, True)
        contracts[symbol] = _json_safe(detail or {})
        if xtdata is not None:
            start = (_now() - dt.timedelta(days=370)).strftime("%Y%m%d")
            end = _now().strftime("%Y%m%d")
            value = _safe_call(getattr(xtdata, "get_financial_data", None), [symbol], ["Balance", "Income", "CashFlow", "Capital", "PershareIndex"], start, end)
            financial[symbol] = _json_safe(value or {})
    sectors = {}
    sector_names = ["沪深A股", "上证A股", "深证A股", "创业板", "科创板"]
    for name in sector_names:
        getter = getattr(context, "get_stock_list_in_sector", None)
        data = _safe_call(getter, name) if callable(getter) else None
        if data is None and xtdata is not None:
            data = _safe_call(getattr(xtdata, "get_stock_list_in_sector", None), name)
        if data is not None:
            sectors[name] = _json_safe(data)
    calendar = {}
    if xtdata is not None:
        start = (_now() - dt.timedelta(days=30)).strftime("%Y%m%d")
        end = (_now() + dt.timedelta(days=30)).strftime("%Y%m%d")
        calendar["SH"] = _json_safe(_safe_call(getattr(xtdata, "get_trading_calendar", None), "SH", start, end, _quiet=True) or [])
        calendar["SZ"] = _json_safe(_safe_call(getattr(xtdata, "get_trading_calendar", None), "SZ", start, end, _quiet=True) or [])
    with _LOCK:
        _CACHE["contracts"] = contracts
        _CACHE["financial"] = financial
        _CACHE["sectors"] = sectors
        _CACHE["calendar"] = calendar
    _write_json(_path("metadata", "contracts_latest.json"), contracts)
    _write_json(_path("metadata", "financial_latest.json"), financial)
    _write_json(_path("metadata", "sectors_latest.json"), sectors)
    _write_json(_path("metadata", "calendar_latest.json"), calendar)



def _context_account_id(context):
    for name in ("accID", "accountid", "accountID"):
        try:
            value = getattr(context, name, "")
            if value:
                return str(value)
        except Exception:
            pass
    return ""


def _coerce_account_items(value):
    items = []
    if value is None:
        return items
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return items
        if ("," in text) or (";" in text) or (" " in text):
            for part in text.replace(";", ",").replace(" ", ",").split(","):
                if part.strip():
                    items.append(part.strip())
            return items
        return [text]
    if isinstance(value, dict):
        account_id = ""
        for key in ("account_id", "accountId", "accountID", "acc_id", "accID", "account", "fund_account", "fundAccount"):
            if key in value and value.get(key):
                account_id = str(value.get(key)).strip()
                break
        if account_id:
            items.append({
                "account_id": account_id,
                "account_type": str(value.get("account_type") or value.get("accountType") or value.get("type") or ACCOUNT_TYPE),
                "name": str(value.get("name") or value.get("label") or ""),
            })
        for key in ("accounts", "account_ids", "accountIds", "account_list", "accountList", "fund_accounts", "fundAccounts"):
            if key in value:
                items.extend(_coerce_account_items(value.get(key)))
        return items
    if isinstance(value, (list, tuple, set)):
        for item in value:
            items.extend(_coerce_account_items(item))
        return items
    for key in ("account_id", "accountId", "accountID", "acc_id", "accID", "account", "fund_account", "fundAccount"):
        try:
            item = getattr(value, key)
            if item:
                items.extend(_coerce_account_items(item))
        except Exception:
            pass
    return items


def _normalize_accounts(*values):
    result = []
    seen = set()
    for value in values:
        for item in _coerce_account_items(value):
            if isinstance(item, dict):
                account_id = str(item.get("account_id") or "").strip()
                account_type = str(item.get("account_type") or ACCOUNT_TYPE).strip() or ACCOUNT_TYPE
                name = str(item.get("name") or "")
            else:
                account_id = str(item or "").strip()
                account_type = ACCOUNT_TYPE
                name = ""
            if not account_id:
                continue
            key = (account_id, account_type)
            if key in seen:
                continue
            seen.add(key)
            result.append({"account_id": account_id, "account_type": account_type, "name": name})
    return result


def _trade_accounts(context):
    _load_runtime_config()
    values = [ACCOUNT_CONFIGS, ACCOUNT_IDS, ACCOUNT_ID, _RUNTIME_ACCOUNTS]
    context_id = _context_account_id(context)
    if context_id:
        values.append({"account_id": context_id, "account_type": ACCOUNT_TYPE})
    return _normalize_accounts(values)


def _write_runtime_accounts(source="", added=None):
    try:
        _write_json(_path("metadata", "runtime_accounts.json"), {
            "updated_at": _now_text(), "source": source, "added": added or [], "accounts": _trade_accounts(_CONTEXT),
        })
    except Exception as exc:
        _log_error("write_runtime_accounts", exc)


def _add_runtime_accounts(raw_values, source="runtime"):
    requested = _normalize_accounts(raw_values)
    added = []
    if not requested:
        return added
    with _LOCK:
        known = set((item.get("account_id"), item.get("account_type")) for item in _trade_accounts(None))
        for item in requested:
            key = (item.get("account_id"), item.get("account_type"))
            if key not in known:
                _RUNTIME_ACCOUNTS.append(item)
                known.add(key)
                added.append(item)
    if added:
        _write_runtime_accounts(source, added)
        _publish("log", {"event": "runtime_accounts_added", "source": source, "accounts": added, "time": _now_text()})
        print("QMT_DATA_EXPORT_ACCOUNTS_ADDED source=%s accounts=%s" % (source, ",".join([x.get("account_id", "") for x in added])))
    return added


def _tag_account_rows(data, account):
    safe = _json_safe(data)
    rows = safe if isinstance(safe, list) else [safe]
    tagged = []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            item = {"value": row}
        item.setdefault("account_id", account.get("account_id"))
        item.setdefault("account_type", account.get("account_type"))
        if account.get("name"):
            item.setdefault("account_name", account.get("name"))
        tagged.append(item)
    return tagged


def _collect_trade_details(context):
    accounts = _trade_accounts(context)
    fn = globals().get("get_trade_detail_data")
    if not accounts or not callable(fn):
        return
    combined = {"accounts": [], "positions": [], "orders": [], "trades": []}
    by_account = {}
    for account in accounts:
        account_id = account.get("account_id")
        account_type = account.get("account_type") or ACCOUNT_TYPE
        if not account_id:
            continue
        collected = {}
        for cache_key, query_type in (("accounts", "account"), ("positions", "position"), ("orders", "order"), ("trades", "deal")):
            data = _safe_call(fn, account_id, account_type, query_type)
            if data is not None:
                safe = _json_safe(data)
                collected[cache_key] = safe
                combined[cache_key].extend(_tag_account_rows(safe, account))
        if collected:
            by_account["%s|%s" % (account_id, account_type)] = {"account": account, "data": collected}
    if by_account:
        with _LOCK:
            _CACHE["account_details"] = by_account
            for key, value in combined.items():
                _CACHE[key] = value
        _write_json(_path("metadata", "trade_detail_latest.json"), {
            "updated_at": _now_text(), "accounts": accounts, "by_account": by_account, "data": combined,
        })

def _module_status(name):
    try:
        __import__(name)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _legacy_method_names():
    return [
        "register_client", "set_account", "subscribe", "send_order", "cancel_order", "query_history", "get_tick", "get_l1_tick",
        "get_order", "get_trade", "get_position", "get_account", "get_contract", "get_all_ticks",
        "get_all_orders", "get_all_trades", "get_all_positions", "get_all_accounts", "get_all_contracts",
        "get_all_active_orders", "xtdata.get_full_tick", "xtdata.get_market_data_ex", "xtdata.get_local_data",
        "xtdata.get_instrument_detail", "xtdata.get_financial_data", "xtdata.get_trading_calendar",
        "xtdata.get_stock_list_in_sector", "xtdata.subscribe_quote", "xtdata.subscribe_whole_quote", "xtdata.unsubscribe_quote",
    ]


def _write_support_inventory(context):
    xtdata = _import_xtdata()
    ctx_methods = []
    for name in [
        "get_market_data_ex", "get_market_data", "get_local_data", "get_full_tick", "get_financial_data",
        "get_stock_list_in_sector", "get_trading_dates", "get_instrumentdetail", "set_account", "subscribe_quote",
        "subscribe_whole_quote", "unsubscribe_quote",
    ]:
        if callable(getattr(context, name, None)):
            ctx_methods.append(name)
    xt_methods = []
    if xtdata is not None:
        for name in [
            "get_market_data", "get_market_data_ex", "get_local_data", "get_full_tick", "download_history_data",
            "download_history_data2", "subscribe_quote", "subscribe_whole_quote", "unsubscribe_quote",
            "get_trading_calendar", "get_instrument_detail", "get_sector_list", "get_stock_list_in_sector",
            "get_financial_data", "download_financial_data", "download_financial_data2",
        ]:
            if callable(getattr(xtdata, name, None)):
                xt_methods.append(name)
    inventory = {
        "created_at": _now_text(), "strategy_file": globals().get("__file__", ""), "output_root": _output_root(),
        "instance": {"id": _instance_id(), "qmt_root": _qmt_root_dir(), "config_path": _runtime_config_path(), "config_loaded": bool(_CONFIG)},
        "symbols": _symbols(), "runtime_symbols": list(_RUNTIME_SYMBOLS), "accounts": _trade_accounts(context), "runtime_accounts": list(_RUNTIME_ACCOUNTS),
        "context_methods": ctx_methods, "xtdata_methods": xt_methods,
        "modules": {"zmq": _module_status("zmq"), "pandas": _module_status("pandas"), "numpy": _module_status("numpy"), "xtquant": _module_status("xtquant")},
        "trade_detail_data_available": callable(globals().get("get_trade_detail_data")),
        "legacy_rpc": {"enabled": ENABLE_LEGACY_ZMQ, "rep_address": LEGACY_REP_ADDRESS, "pub_address": LEGACY_PUB_ADDRESS, "methods": _legacy_method_names()},
        "command_file": _path("commands", COMMAND_FILENAME),
    }
    with _LOCK:
        _CACHE["meta"] = inventory
    _write_json(_path("metadata", "support_inventory.json"), inventory)


def _snapshot_payload():
    with _LOCK:
        return _json_safe({
            "updated_at": _now_text(), "instance": {"id": _instance_id(), "qmt_root": _qmt_root_dir()}, "symbols": _symbols(), "row_counts": dict(_ROW_COUNTS),
            "ticks": _CACHE.get("ticks", {}), "histories": _CACHE.get("histories", {}),
            "contracts": _CACHE.get("contracts", {}), "financial": _CACHE.get("financial", {}),
            "sectors": _CACHE.get("sectors", {}), "calendar": _CACHE.get("calendar", {}),
            "accounts": _CACHE.get("accounts", []), "positions": _CACHE.get("positions", []),
            "orders": _CACHE.get("orders", []), "trades": _CACHE.get("trades", []), "account_details": _CACHE.get("account_details", {}),
            "network": _CACHE.get("network", {}), "errors": _CACHE.get("errors", [])[-20:],
        })


def _write_snapshots(force=False):
    global _LAST_SNAPSHOT_AT
    now = time.time()
    if not force and now - _LAST_SNAPSHOT_AT < SNAPSHOT_REFRESH_SECONDS:
        return
    _LAST_SNAPSHOT_AT = now
    payload = _snapshot_payload()
    _write_json(_path("snapshots", "latest.json"), payload)
    legacy = {
        "register_client": True, "get_all_accounts": payload.get("accounts", []),
        "get_all_positions": payload.get("positions", []), "get_all_orders": payload.get("orders", []),
        "get_all_trades": payload.get("trades", []), "get_all_contracts": list(payload.get("contracts", {}).values()),
        "get_all_ticks": list(payload.get("ticks", {}).values()), "query_history": payload.get("histories", {}),
        "xtdata.get_full_tick": payload.get("ticks", {}), "xtdata.get_market_data_ex": payload.get("histories", {}),
        "xtdata.get_instrument_detail": payload.get("contracts", {}), "xtdata.get_financial_data": payload.get("financial", {}),
        "xtdata.get_trading_calendar": payload.get("calendar", {}), "xtdata.get_stock_list_in_sector": payload.get("sectors", {}),
    }
    _write_json(_path("legacy", "legacy_rpc_snapshot.json"), legacy)


def _req_attr(req, name, default=""):
    if isinstance(req, dict):
        return req.get(name, default)
    return getattr(req, name, default)


def _interval_to_period(value):
    text = str(value or "").lower()
    if "1m" in text or "minute" in text or "min" in text:
        return "1m"
    if "5m" in text:
        return "5m"
    if "day" in text or "daily" in text or "1d" in text or text == "d":
        return "1d"
    return text or "1m"


def _legacy_query_history(req):
    symbol = _symbol(_req_attr(req, "vt_symbol", "") or _req_attr(req, "symbol", ""))
    if not symbol:
        return []
    period = _interval_to_period(_req_attr(req, "interval", _req_attr(req, "period", "1m")))
    with _LOCK:
        return _json_safe(_CACHE.get("histories", {}).get("%s|%s" % (symbol, period), []))


def _legacy_xtdata_get_market_data_ex(args, kwargs):
    stock_list = kwargs.get("stock_list") or kwargs.get("stock_code") or kwargs.get("code_list")
    if not stock_list and len(args) >= 2:
        stock_list = args[1]
    if isinstance(stock_list, str):
        stock_list = [stock_list]
    stock_list = [_symbol(s) for s in (stock_list or _symbols())]
    period = kwargs.get("period") or (args[2] if len(args) >= 3 else "1m")
    result = {}
    with _LOCK:
        histories = _CACHE.get("histories", {})
        for symbol in stock_list:
            result[symbol] = histories.get("%s|%s" % (symbol, period), [])
    return _json_safe(result)


def _accounts_from_legacy(method, args, kwargs):
    args = args or []
    kwargs = kwargs or {}
    candidates = []
    if method == "register_client":
        if len(args) > 1:
            candidates.extend(args[1:])
        elif len(args) == 1 and not isinstance(args[0], str):
            candidates.append(args[0])
        candidates.append(kwargs)
    elif method in ("set_account", "get_account", "get_position", "get_order", "get_trade", "get_all_accounts", "get_all_positions", "get_all_orders", "get_all_trades"):
        candidates.extend(args)
        candidates.append(kwargs)
    else:
        candidates.append(kwargs)
    return _normalize_accounts(candidates)


def _refresh_after_account_request(method):
    if _CONTEXT is None:
        return
    try:
        if method in ("set_account", "register_client", "get_account", "get_position", "get_order", "get_trade", "get_all_accounts", "get_all_positions", "get_all_orders", "get_all_trades"):
            _collect_trade_details(_CONTEXT)
            _write_snapshots(force=True)
    except Exception as exc:
        _log_error("refresh_after_account_request %s" % method, exc)


def _filter_rows_by_accounts(rows, accounts):
    if not accounts:
        return rows
    wanted = set([item.get("account_id") for item in accounts if item.get("account_id")])
    if not wanted:
        return rows
    result = []
    for row in rows:
        if isinstance(row, dict) and str(row.get("account_id") or "") in wanted:
            result.append(row)
    return result

def _symbols_from_legacy(method, args, kwargs):
    args = args or []
    kwargs = kwargs or {}
    candidates = []
    if method == "register_client":
        if len(args) > 1:
            candidates.extend(args[1:])
        elif len(args) == 1 and not isinstance(args[0], str):
            candidates.append(args[0])
        candidates.append(kwargs)
    elif method in ("subscribe", "query_history"):
        if args:
            candidates.append(args[0])
        candidates.append(kwargs)
    elif method in ("get_tick", "get_l1_tick", "get_contract"):
        if args:
            candidates.append(args[0])
        candidates.append(kwargs)
    elif method == "xtdata.get_full_tick":
        if args:
            candidates.append(args[0])
        for key in ("code_list", "stock_list", "stock_code", "stockCode"):
            candidates.append(kwargs.get(key))
    elif method in ("xtdata.get_market_data", "xtdata.get_market_data_ex", "xtdata.get_local_data"):
        if len(args) >= 2:
            candidates.append(args[1])
        for key in ("stock_list", "stock_code", "stockCode", "code_list"):
            candidates.append(kwargs.get(key))
    elif method == "xtdata.get_instrument_detail":
        if args:
            candidates.append(args[0])
        candidates.append(kwargs)
    elif method in ("xtdata.subscribe_quote", "xtdata.subscribe_whole_quote"):
        if args:
            candidates.append(args[0])
        for key in ("stock_code", "stockCode", "code_list", "stock_list"):
            candidates.append(kwargs.get(key))
    return _normalize_symbol_list(candidates)


def _refresh_after_symbol_request(method):
    if _CONTEXT is None:
        return
    try:
        if method in ("subscribe", "get_tick", "get_l1_tick", "xtdata.get_full_tick", "xtdata.subscribe_quote", "xtdata.subscribe_whole_quote"):
            _write_ticks(_CONTEXT, _fetch_full_ticks(_CONTEXT), "legacy_rpc")
        if method in ("subscribe", "query_history", "xtdata.get_market_data", "xtdata.get_market_data_ex", "xtdata.get_local_data"):
            _write_history(_CONTEXT, "legacy_rpc")
        if method in ("get_contract", "get_all_contracts", "xtdata.get_instrument_detail", "xtdata.get_financial_data", "xtdata.get_stock_list_in_sector", "xtdata.get_trading_calendar"):
            _collect_static(_CONTEXT)
        _write_snapshots(force=True)
    except Exception as exc:
        _log_error("refresh_after_symbol_request %s" % method, exc)
def _legacy_rpc_call(method, args=None, kwargs=None):
    args = args or []
    kwargs = kwargs or {}
    request_accounts = _accounts_from_legacy(method, args, kwargs)
    if request_accounts:
        _add_runtime_accounts(request_accounts, "legacy_rpc:%s" % method)
        _refresh_after_account_request(method)
    request_symbols = _symbols_from_legacy(method, args, kwargs)
    if request_symbols:
        _add_runtime_symbols(request_symbols, "legacy_rpc:%s" % method)
        _refresh_after_symbol_request(method)
    with _LOCK:
        ticks = _CACHE.get("ticks", {})
        contracts = _CACHE.get("contracts", {})
    if method == "register_client":
        name = args[0] if args else kwargs.get("client_name", "unknown")
        meta = args[1] if len(args) > 1 else kwargs.get("client_meta", {})
        with _LOCK:
            _CACHE["clients"][str(name)] = {"meta": _json_safe(meta), "time": _now_text()}
        return True
    if method == "set_account":
        return bool(request_accounts)
    if method == "subscribe":
        return True
    if method in ("send_order", "cancel_order"):
        raise RuntimeError("qmt_data_export_bridge_strategy is read-only; order APIs are disabled")
    if method == "query_history":
        return _legacy_query_history(args[0] if args else kwargs)
    if method in ("get_tick", "get_l1_tick"):
        symbol = _symbol(args[0] if args else kwargs.get("vt_symbol") or kwargs.get("symbol"))
        return _json_safe(ticks.get(symbol))
    if method == "get_all_ticks":
        return _json_safe(list(ticks.values()))
    if method == "get_contract":
        symbol = _symbol(args[0] if args else kwargs.get("vt_symbol") or kwargs.get("symbol"))
        return _json_safe(contracts.get(symbol))
    if method == "get_all_contracts":
        return _json_safe(list(contracts.values()))
    if method in ("get_all_accounts", "get_all_positions", "get_all_orders", "get_all_trades", "get_all_active_orders"):
        key = method.replace("get_all_", "")
        if key == "active_orders":
            key = "orders"
        with _LOCK:
            return _json_safe(_CACHE.get(key, []))
    if method in ("get_account", "get_position", "get_order", "get_trade"):
        key = {"get_account": "accounts", "get_position": "positions", "get_order": "orders", "get_trade": "trades"}.get(method)
        with _LOCK:
            rows = list(_CACHE.get(key, []))
        rows = _filter_rows_by_accounts(rows, request_accounts)
        return _json_safe(rows[0] if len(rows) == 1 else rows)
    if method == "xtdata.get_full_tick":
        stock_list = args[0] if args else kwargs.get("code_list") or kwargs.get("stock_list") or _symbols()
        if isinstance(stock_list, str):
            stock_list = [stock_list]
        return _json_safe({s: ticks.get(_symbol(s)) for s in stock_list})
    if method in ("xtdata.get_market_data_ex", "xtdata.get_local_data"):
        return _legacy_xtdata_get_market_data_ex(args, kwargs)
    if method == "xtdata.get_instrument_detail":
        symbol = _symbol(args[0] if args else kwargs.get("stock_code") or kwargs.get("symbol"))
        return _json_safe(contracts.get(symbol))
    if method == "xtdata.get_financial_data":
        with _LOCK:
            return _json_safe(_CACHE.get("financial", {}))
    if method == "xtdata.get_trading_calendar":
        with _LOCK:
            return _json_safe(_CACHE.get("calendar", {}))
    if method == "xtdata.get_stock_list_in_sector":
        name = args[0] if args else kwargs.get("sector_name", "")
        with _LOCK:
            sectors = _CACHE.get("sectors", {})
            return _json_safe(sectors.get(name, sectors))
    if method in ("xtdata.subscribe_quote", "xtdata.subscribe_whole_quote"):
        return 1
    if method == "xtdata.unsubscribe_quote":
        return None
    raise KeyError(method)


class _LooseUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        try:
            return pickle.Unpickler.find_class(self, module, name)
        except Exception:
            return type(str(name), (object,), {})


def _loose_pickle_loads(raw):
    return _LooseUnpickler(io.BytesIO(raw)).load()

def _decode_rpc(raw):
    try:
        obj = pickle.loads(raw)
    except Exception:
        try:
            obj = _loose_pickle_loads(raw)
        except Exception:
            obj = json.loads(raw.decode("utf-8"))
    if isinstance(obj, (list, tuple)) and len(obj) >= 3:
        return obj[0], list(obj[1] or []), dict(obj[2] or {})
    if isinstance(obj, dict):
        return obj.get("method") or obj.get("function"), obj.get("args") or [], obj.get("kwargs") or {}
    raise ValueError("unsupported rpc request: %r" % (obj,))


def _publish(topic, payload):
    if _PUB_SOCKET is None or _ZMQ is None:
        return
    try:
        _PUB_SOCKET.send_pyobj([topic, _json_safe(payload)], flags=_ZMQ.NOBLOCK)
    except Exception:
        pass


def _bridge_should_report_error():
    global _LAST_BRIDGE_ERROR_AT
    now = time.time()
    if now - _LAST_BRIDGE_ERROR_AT >= 30:
        _LAST_BRIDGE_ERROR_AT = now
        return True
    return False


def _bridge_call_zmq(method, args=None, kwargs=None):
    import zmq
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.linger = 0
    timeout = max(50, int(BRIDGE_TIMEOUT_MS or 300))
    sock.rcvtimeo = timeout
    sock.sndtimeo = timeout
    try:
        sock.connect(BRIDGE_REP_ADDRESS)
        sock.send_pyobj([method, args or [], kwargs or {}])
        return sock.recv_pyobj()
    finally:
        try:
            sock.close(0)
        except Exception:
            pass


def _bridge_call_pipe(method, args=None, kwargs=None):
    from multiprocessing.connection import Client
    authkey = str(BRIDGE_AUTHKEY or "qmt_srv_bridge").encode("utf-8")
    conn = Client(BRIDGE_PIPE_ADDRESS, family="AF_PIPE", authkey=authkey)
    try:
        conn.send([method, args or [], kwargs or {}])
        return conn.recv()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _bridge_call(method, args=None, kwargs=None):
    if not BRIDGE_ENABLED:
        return None
    last_error = None
    if BRIDGE_REP_ADDRESS:
        try:
            return _bridge_call_zmq(method, args, kwargs)
        except Exception as exc:
            last_error = exc
    if BRIDGE_PIPE_ADDRESS:
        try:
            return _bridge_call_pipe(method, args, kwargs)
        except Exception as exc:
            last_error = exc
    if last_error is not None and _bridge_should_report_error():
        print("QMT_DATA_EXPORT_BRIDGE_UNAVAILABLE error=%s" % last_error)
    return None


def _push_bridge_snapshot(source="", force=False):
    global _LAST_BRIDGE_PUSH_AT
    if not BRIDGE_ENABLED:
        return False
    now = time.time()
    if not force and now - _LAST_BRIDGE_PUSH_AT < float(BRIDGE_PUSH_SECONDS or 1.0):
        return False
    _LAST_BRIDGE_PUSH_AT = now
    payload = _snapshot_payload()
    payload["bridge_source"] = source
    response = _bridge_call("qmt_bridge.update", [payload], {"instance_id": _instance_id()})
    ok = isinstance(response, (list, tuple)) and len(response) >= 1 and bool(response[0])
    if ok:
        with _LOCK:
            _CACHE["network"] = {"enabled": True, "mode": "client", "rep": BRIDGE_REP_ADDRESS, "pipe": BRIDGE_PIPE_ADDRESS, "last_push": _now_text()}
    return ok


def _zmq_loop():
    global _ZMQ_CONTEXT, _REP_SOCKET, _PUB_SOCKET, _ZMQ
    try:
        import zmq
        _ZMQ = zmq
        _ZMQ_CONTEXT = zmq.Context()
        _REP_SOCKET = _ZMQ_CONTEXT.socket(zmq.REP)
        _REP_SOCKET.linger = 0
        _REP_SOCKET.bind(LEGACY_REP_ADDRESS)
        _PUB_SOCKET = _ZMQ_CONTEXT.socket(zmq.PUB)
        _PUB_SOCKET.linger = 0
        _PUB_SOCKET.bind(LEGACY_PUB_ADDRESS)
        poller = zmq.Poller()
        poller.register(_REP_SOCKET, zmq.POLLIN)
        with _LOCK:
            _CACHE["network"] = {"enabled": True, "rep": LEGACY_REP_ADDRESS, "pub": LEGACY_PUB_ADDRESS, "started_at": _now_text()}
        print("QMT_DATA_EXPORT_ZMQ_STARTED rep=%s pub=%s" % (LEGACY_REP_ADDRESS, LEGACY_PUB_ADDRESS))
        while not _ZMQ_STOP.is_set():
            events = dict(poller.poll(200))
            if _REP_SOCKET in events:
                raw = _REP_SOCKET.recv()
                method = ""
                try:
                    method, args, kwargs = _decode_rpc(raw)
                    _ROW_COUNTS["rpc"] += 1
                    payload = _legacy_rpc_call(method, args, kwargs)
                    _REP_SOCKET.send_pyobj([True, payload])
                    _append_jsonl(_path("network", "rpc_%s.jsonl" % _today()), {"time": _now_text(), "method": method, "ok": True})
                except Exception as exc:
                    _REP_SOCKET.send_pyobj([False, str(exc)])
                    _append_jsonl(_path("network", "rpc_%s.jsonl" % _today()), {"time": _now_text(), "method": method, "ok": False, "error": str(exc)})
    except Exception as exc:
        with _LOCK:
            _CACHE["network"] = {"enabled": False, "error": str(exc), "time": _now_text()}
        print("QMT_DATA_EXPORT_ZMQ_DISABLED error=%s" % exc)
    finally:
        try:
            if _REP_SOCKET is not None:
                _REP_SOCKET.close(0)
        except Exception:
            pass
        try:
            if _PUB_SOCKET is not None:
                _PUB_SOCKET.close(0)
        except Exception:
            pass
        try:
            if _ZMQ_CONTEXT is not None:
                _ZMQ_CONTEXT.term()
        except Exception:
            pass


def _start_zmq():
    global _ZMQ_THREAD
    if not ENABLE_LEGACY_ZMQ:
        return
    if _ZMQ_THREAD is not None and _ZMQ_THREAD.is_alive():
        return
    _ZMQ_STOP.clear()
    _ZMQ_THREAD = threading.Thread(target=_zmq_loop, name="qmt-data-export-rpc")
    _ZMQ_THREAD.daemon = True
    _ZMQ_THREAD.start()


def _stop_zmq():
    _ZMQ_STOP.set()
    try:
        if _ZMQ_THREAD is not None and _ZMQ_THREAD.is_alive():
            _ZMQ_THREAD.join(1.0)
    except Exception:
        pass
def _command_file_path():
    return _path("commands", COMMAND_FILENAME)


def _append_command_ack(command, ok, payload=None, error=""):
    try:
        _append_jsonl(_path("commands", "acks_%s.jsonl" % _today()), {
            "time": _now_text(), "id": command.get("id"), "method": command.get("method"),
            "ok": bool(ok), "payload": payload if ok else None, "error": error,
            "instance_id": _instance_id(),
        })
    except Exception as exc:
        _log_error("append_command_ack", exc)


def _process_command_file(context):
    global _COMMAND_OFFSET, _COMMAND_FILE_DISABLED, _COMMAND_FILE_DISABLE_LOGGED
    if _COMMAND_FILE_DISABLED:
        return 0
    path = _command_file_path()
    try:
        exists = os.path.exists(path)
    except Exception as exc:
        if _is_forbidden_fileio(exc):
            _COMMAND_FILE_DISABLED = True
            if not _COMMAND_FILE_DISABLE_LOGGED:
                _COMMAND_FILE_DISABLE_LOGGED = True
                print("QMT_DATA_EXPORT_COMMAND_FILE_DISABLED error=%s" % exc)
            return 0
        raise
    if not exists:
        return 0
    count = 0
    try:
        size = os.path.getsize(path)
        if size < _COMMAND_OFFSET:
            _COMMAND_OFFSET = 0
        with open(path, "rb") as fh:
            fh.seek(_COMMAND_OFFSET)
            data = fh.read()
            _COMMAND_OFFSET = fh.tell()
        if not data:
            return 0
        text = data.decode("utf-8", "ignore")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
                target = str(command.get("instance_id") or command.get("target_instance_id") or "").strip()
                if target and target not in ("*", "all", _instance_id()):
                    continue
                method = command.get("method") or command.get("function")
                args = command.get("args") or []
                kwargs = command.get("kwargs") or {}
                payload = _legacy_rpc_call(method, args, kwargs)
                _append_command_ack(command, True, payload, "")
                _ROW_COUNTS["commands"] += 1
                count += 1
            except Exception as exc:
                try:
                    _append_command_ack(command if isinstance(command, dict) else {}, False, None, str(exc))
                except Exception:
                    pass
                _log_error("process_command", exc)
        return count
    except Exception as exc:
        if _is_forbidden_fileio(exc):
            _COMMAND_FILE_DISABLED = True
            if not _COMMAND_FILE_DISABLE_LOGGED:
                _COMMAND_FILE_DISABLE_LOGGED = True
                print("QMT_DATA_EXPORT_COMMAND_FILE_DISABLED error=%s" % exc)
        else:
            _log_error("process_command_file", exc)
        return count


def _on_quote_push(*args, **kwargs):
    try:
        candidates = list(args)
        for key in ("data", "tick", "ticks", "quote", "quotes"):
            if key in kwargs:
                candidates.append(kwargs.get(key))
        for item in candidates:
            ticks = _normalize_tick_result(item, _symbols())
            if ticks:
                _write_ticks(_CONTEXT, ticks, "subscribe_quote")
                _write_snapshots(force=False)
                _push_bridge_snapshot("subscribe_quote")
                return
    except Exception as exc:
        _log_error("on_quote_push", exc)


def _subscribe(context, symbols=None):
    if not SUBSCRIBE_TICK_IN_INIT:
        return
    fn = getattr(context, "subscribe_quote", None) or globals().get("subscribe_quote")
    if not callable(fn):
        print("QMT_DATA_EXPORT subscribe_quote unavailable; fallback to handlebar get_full_tick")
        return
    ok = 0
    total = 0
    stocks = list(symbols or _symbols())
    for stock in stocks:
        if stock in _SUBSCRIBED_SYMBOLS:
            continue
        total += 1
        attempts = (
            lambda s=stock: fn(s, period="tick", dividend_type=DIVIDEND_TYPE, result_type="dict", callback=_on_quote_push),
            lambda s=stock: fn(s, period="tick", callback=_on_quote_push),
            lambda s=stock: fn(s, "tick", DIVIDEND_TYPE, _on_quote_push),
            lambda s=stock: fn(s, "tick", _on_quote_push),
        )
        for attempt in attempts:
            try:
                sub_id = attempt()
                with _LOCK:
                    _CACHE["subscriptions"].append({"symbol": stock, "sub_id": _json_safe(sub_id), "time": _now_text()})
                    _SUBSCRIBED_SYMBOLS.add(stock)
                ok += 1
                break
            except TypeError:
                continue
            except Exception as exc:
                _log_error("subscribe %s" % stock, exc)
                break
    if total:
        print("QMT_DATA_EXPORT_SUBSCRIBE ok=%s total=%s" % (ok, total))

def _collect(context, source):
    global _LAST_STATIC_AT, _LAST_TRADE_AT
    _process_command_file(context)
    ticks_written = _write_ticks(context, _fetch_full_ticks(context), source)
    bars_written = _write_history(context, source)
    now = time.time()
    if now - _LAST_TRADE_AT >= TRADE_REFRESH_SECONDS:
        _LAST_TRADE_AT = now
        _collect_trade_details(context)
    if now - _LAST_STATIC_AT >= STATIC_REFRESH_SECONDS:
        _LAST_STATIC_AT = now
        _collect_static(context)
        _write_support_inventory(context)
    _write_snapshots(force=False)
    _push_bridge_snapshot(source)
    if ticks_written or bars_written:
        print("QMT_DATA_EXPORT rows ticks=%s bars=%s source=%s" % (_ROW_COUNTS["ticks"], _ROW_COUNTS["bars"], source))
    return ticks_written + bars_written


def init(ContextInfo):
    global _CONTEXT, _LAST_STATIC_AT, _LAST_TRADE_AT, _BOOTSTRAP_INIT_CALLED
    print("QMT_DATA_EXPORT_INIT_ENTER version=%s file=%s" % (BRIDGE_VERSION, globals().get("__file__", "")))
    _BOOTSTRAP_INIT_CALLED = True
    _CONTEXT = ContextInfo
    _load_runtime_config(force=True)
    _LAST_TRADE_AT = 0
    _ensure_dir(_output_root())
    _set_universe_safe(ContextInfo)
    _collect_static(ContextInfo)
    _write_support_inventory(ContextInfo)
    _LAST_STATIC_AT = time.time()
    _subscribe(ContextInfo)
    _start_zmq()
    _collect(ContextInfo, "init")
    _write_snapshots(force=True)
    _push_bridge_snapshot("init", force=True)
    print("QMT_DATA_EXPORT_INIT instance=%s symbols=%s accounts=%s output=%s" % (_instance_id(), ",".join(_symbols()), ",".join([a.get("account_id", "") for a in _trade_accounts(ContextInfo)]), _output_root()))


def handlebar(ContextInfo):
    global _CONTEXT
    _CONTEXT = ContextInfo
    try:
        return _collect(ContextInfo, "handlebar")
    except Exception as exc:
        _log_error("handlebar", exc)
        return 0


def stop(ContextInfo):
    try:
        _write_snapshots(force=True)
    except Exception:
        pass
    _stop_zmq()
    print("QMT_DATA_EXPORT_STOP ticks=%s bars=%s rpc=%s errors=%s" % (
        _ROW_COUNTS["ticks"], _ROW_COUNTS["bars"], _ROW_COUNTS["rpc"], _ROW_COUNTS["errors"]
    ))


def _bootstrap_from_global_context():
    if _BOOTSTRAP_INIT_CALLED:
        return
    context = globals().get("ContextInfo")
    if context is None:
        print("QMT_DATA_EXPORT_BOOTSTRAP_NO_CONTEXT version=%s file=%s" % (BRIDGE_VERSION, globals().get("__file__", "")))
        return
    try:
        print("QMT_DATA_EXPORT_BOOTSTRAP_CONTEXT version=%s file=%s" % (BRIDGE_VERSION, globals().get("__file__", "")))
        init(context)
    except Exception as exc:
        _log_error("bootstrap_context", exc)


_bootstrap_from_global_context()
