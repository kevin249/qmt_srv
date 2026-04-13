from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from pprint import pprint
from typing import Any

import zmq


DEFAULT_REQ_ADDRESS = "tcp://127.0.0.1:20140"


def rpc_call(req_address: str, function: str, *args: Any, timeout: int = 5000, **kwargs: Any) -> Any:
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


def to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal RPC probe for XTQ Bridge")
    parser.add_argument("--req", default=DEFAULT_REQ_ADDRESS, help="REQ address")
    parser.add_argument("--timeout", type=int, default=5000, help="RPC timeout in ms")
    args = parser.parse_args()

    accounts = rpc_call(args.req, "get_all_accounts", timeout=args.timeout)
    positions = rpc_call(args.req, "get_all_positions", timeout=args.timeout)

    print(f"accounts={len(accounts)}")
    for account in accounts:
        pprint(to_plain(account))

    print(f"positions={len(positions)}")
    for position in positions:
        pprint(to_plain(position))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
