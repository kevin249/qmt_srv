from __future__ import annotations

"""L1 tick 诊断脚本。

用法（在装有 MiniQMT 且 qmt_srv 服务已运行的机器上）：

    python probe_l1tick.py 002594            # 默认连本机 RPC 20140
    python probe_l1tick.py 600000 --req tcp://127.0.0.1:20140

它会分别测试：
  A) 直接调用本地 xtdata.get_full_tick（订阅前 / 订阅后），判断是否需要订阅预热
  B) 通过 RPC 调用 bridge 的 get_l1_tick
便于区分“QMT 本身取不到” vs “RPC/转换链路问题”。
"""

import argparse
import time
from pprint import pprint

import zmq


DEFAULT_REQ_ADDRESS = "tcp://127.0.0.1:20140"


def rpc_call(req_address, function, *args, timeout=8000, **kwargs):
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    try:
        socket.connect(req_address)
        socket.send_pyobj([function, args, kwargs])
        if not socket.poll(timeout):
            raise TimeoutError(f"RPC timeout after {timeout}ms: {function}")
        success, payload = socket.recv_pyobj()
        if not success:
            raise RuntimeError(payload)
        return payload
    finally:
        socket.close()
        context.term()


def to_xt_symbol(code: str) -> str:
    code = code.strip().zfill(6)
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def probe_local(xt_symbol: str) -> None:
    print(f"\n=== A) 本地 xtdata.get_full_tick  {xt_symbol} ===")
    try:
        from xtquant import xtdata
    except Exception as exc:  # noqa: BLE001
        print(f"  [skip] 无法 import xtquant: {exc}")
        return
    try:
        xtdata.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"  connect() 异常（可能已连接）: {exc}")

    before = xtdata.get_full_tick([xt_symbol])
    print("  订阅前 get_full_tick:")
    pprint(before)

    print("  -> subscribe_quote(period='tick') 后等待 2s 重试...")
    try:
        xtdata.subscribe_quote(xt_symbol, period="tick", count=1)
    except Exception as exc:  # noqa: BLE001
        print(f"  subscribe_quote 异常: {exc}")
    time.sleep(2.0)
    after = xtdata.get_full_tick([xt_symbol])
    print("  订阅后 get_full_tick:")
    pprint(after)


def probe_rpc(req: str, code: str) -> None:
    print(f"\n=== B) RPC get_l1_tick  {code} ===")
    try:
        rpc_call(req, "register_client", "probe_l1tick", {"tool": "probe_l1tick"})
    except RuntimeError as exc:
        if "register_client" not in str(exc):
            print(f"  register_client 异常: {exc}")
    try:
        result = rpc_call(req, "get_l1_tick", code)
        print("  get_l1_tick ->")
        pprint(result)
    except Exception as exc:  # noqa: BLE001
        print(f"  get_l1_tick 调用失败: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="L1 tick 诊断")
    parser.add_argument("code", help="6 位股票代码，如 002594 / 600000")
    parser.add_argument("--req", default=DEFAULT_REQ_ADDRESS)
    parser.add_argument("--skip-local", action="store_true", help="跳过本地 xtdata 直连测试")
    args = parser.parse_args()

    xt_symbol = to_xt_symbol(args.code)
    if not args.skip_local:
        probe_local(xt_symbol)
    probe_rpc(args.req, args.code.strip().zfill(6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
