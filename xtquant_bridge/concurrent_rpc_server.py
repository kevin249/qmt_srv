from __future__ import annotations

import pickle
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from time import time
from typing import Any, Callable

import zmq

try:
    from vnpy.rpc import HEARTBEAT_INTERVAL, HEARTBEAT_TOPIC
except ImportError:  # pragma: no cover - vn.py is an install-time dependency.
    HEARTBEAT_INTERVAL = 10
    HEARTBEAT_TOPIC = "_heartbeat"


class ConcurrentRpcServer:
    """A vn.py RpcServer-compatible server with fast/slow worker isolation."""

    DEFAULT_SLOW_METHODS = frozenset(
        {
            "query_history",
            "xtdata.get_market_data_ex",
            "xtdata.get_market_data",
            "xtdata.get_local_data",
            "xtdata.get_full_kline",
            "xtdata.download_history_data",
            "xtdata.download_history_data2",
            "xtdata.download_history_contracts",
            "xtdata.download_financial_data",
            "xtdata.download_financial_data2",
            "xtdata.download_index_weight",
            "xtdata.download_sector_data",
            "xtdata.download_cb_data",
            "xtdata.download_etf_info",
        }
    )
    DEFAULT_TRADE_METHODS = frozenset({"send_order", "cancel_order"})

    def __init__(
        self,
        *,
        trade_workers: int = 1,
        trade_queue_size: int = 16,
        trade_queue_timeout: float = 0.5,
        fast_workers: int = 8,
        fast_queue_size: int = 128,
        fast_queue_timeout: float = 3.0,
        slow_workers: int = 2,
        slow_queue_size: int = 4,
        slow_queue_timeout: float = 10.0,
        trade_methods: set[str] | None = None,
        slow_methods: set[str] | None = None,
    ) -> None:
        self._functions: dict[str, Callable] = {}
        self._context: zmq.Context = zmq.Context()
        self._socket_rep: zmq.Socket = self._context.socket(zmq.ROUTER)
        self._socket_pub: zmq.Socket = self._context.socket(zmq.PUB)
        self._socket_rep.setsockopt(zmq.LINGER, 0)
        self._socket_pub.setsockopt(zmq.LINGER, 0)

        self._active = False
        self._thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._publish_lock = threading.Lock()
        self._heartbeat_at: float | None = None

        self._trade_workers = max(1, int(trade_workers or 1))
        self._trade_queue_size = max(0, int(trade_queue_size or 0))
        self._trade_queue_timeout = max(0.0, float(trade_queue_timeout or 0.0))
        self._fast_workers = max(1, int(fast_workers or 1))
        self._fast_queue_size = max(0, int(fast_queue_size or 0))
        self._fast_queue_timeout = max(0.0, float(fast_queue_timeout or 0.0))
        self._slow_workers = max(1, int(slow_workers or 1))
        self._slow_queue_size = max(0, int(slow_queue_size or 0))
        self._slow_queue_timeout = max(0.0, float(slow_queue_timeout or 0.0))
        self._trade_executor = ThreadPoolExecutor(max_workers=self._trade_workers, thread_name_prefix="qmt-rpc-trade")
        self._fast_executor = ThreadPoolExecutor(max_workers=self._fast_workers, thread_name_prefix="qmt-rpc-fast")
        self._slow_executor = ThreadPoolExecutor(max_workers=self._slow_workers, thread_name_prefix="qmt-rpc-slow")
        self._trade_slots = threading.BoundedSemaphore(self._trade_workers + self._trade_queue_size)
        self._fast_slots = threading.BoundedSemaphore(self._fast_workers + self._fast_queue_size)
        self._slow_slots = threading.BoundedSemaphore(self._slow_workers + self._slow_queue_size)
        self._trade_methods = set(trade_methods or self.DEFAULT_TRADE_METHODS)
        self._slow_methods = set(slow_methods or self.DEFAULT_SLOW_METHODS)

    def is_active(self) -> bool:
        return self._active

    def start(self, rep_address: str, pub_address: str) -> None:
        if self._active:
            return

        self._socket_rep.bind(rep_address)
        self._socket_pub.bind(pub_address)
        self._active = True
        self._thread = threading.Thread(target=self.run, name="qmt-rpc-router", daemon=True)
        self._thread.start()
        self._heartbeat_at = time() + HEARTBEAT_INTERVAL

    def stop(self) -> None:
        self._active = False

    def join(self) -> None:
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        self._trade_executor.shutdown(wait=False, cancel_futures=True)
        self._fast_executor.shutdown(wait=False, cancel_futures=True)
        self._slow_executor.shutdown(wait=False, cancel_futures=True)
        try:
            self._context.term()
        except zmq.ZMQError:
            pass

    def run(self) -> None:
        while self._active:
            n = self._socket_rep.poll(1000)
            self.check_heartbeat()
            if not n:
                continue

            frames = self._socket_rep.recv_multipart()
            try:
                name, args, kwargs = pickle.loads(frames[-1])
            except Exception:  # noqa: BLE001
                self._send_response(frames[:-1], [False, traceback.format_exc()])
                continue

            name_str = str(name)
            kind = self._pool_kind(name_str)
            executor, slots = self._select_pool(name_str)
            if not slots.acquire(blocking=False):
                self._send_response(frames[:-1], [False, self._queue_full_error(name_str, kind)])
                continue

            try:
                executor.submit(
                    self._execute_request,
                    frames[:-1],
                    name_str,
                    args,
                    kwargs,
                    slots,
                    time(),
                    self._queue_timeout(kind),
                    kind,
                )
            except RuntimeError as exc:
                slots.release()
                message = f"RpcServer error: failed to enqueue {name!r}: {exc}"
                self._send_response(frames[:-1], [False, message])

        self._socket_pub.close(0)
        self._socket_rep.close(0)

    def _select_pool(self, name: str) -> tuple[ThreadPoolExecutor, threading.BoundedSemaphore]:
        if name in self._trade_methods:
            return self._trade_executor, self._trade_slots
        if name in self._slow_methods:
            return self._slow_executor, self._slow_slots
        return self._fast_executor, self._fast_slots

    def _pool_kind(self, name: str) -> str:
        if name in self._trade_methods:
            return "trade"
        return "slow" if name in self._slow_methods else "fast"

    def _queue_timeout(self, kind: str) -> float:
        if kind == "trade":
            return self._trade_queue_timeout
        return self._slow_queue_timeout if kind == "slow" else self._fast_queue_timeout

    @staticmethod
    def _queue_full_error(name: str, kind: str) -> str:
        return (
            f"RpcServer busy: {kind} queue is full for {name!r}. "
            "Please retry later or reduce concurrent requests."
        )

    @staticmethod
    def _queue_timeout_error(name: str, kind: str, waited: float, timeout: float) -> str:
        return (
            f"RpcServer busy: {kind} queue wait timeout for {name!r} "
            f"after {waited:.3f}s (limit {timeout:.3f}s). Please retry later."
        )

    def _execute_request(
        self,
        routing_frames: list[bytes],
        name: str,
        args: tuple,
        kwargs: dict,
        slots: threading.BoundedSemaphore,
        queued_at: float,
        queue_timeout: float,
        kind: str,
    ) -> None:
        try:
            waited = time() - queued_at
            if queue_timeout > 0 and waited > queue_timeout:
                reply = [False, self._queue_timeout_error(name, kind, waited, queue_timeout)]
                self._send_response(routing_frames, reply)
                return

            try:
                func = self._functions[name]
                result = func(*args, **kwargs)
                reply = [True, result]
            except Exception:  # noqa: BLE001
                reply = [False, traceback.format_exc()]
            self._send_response(routing_frames, reply)
        finally:
            slots.release()

    def _send_response(self, routing_frames: list[bytes], reply: list[Any]) -> None:
        if not self._active:
            return
        try:
            with self._send_lock:
                self._socket_rep.send_multipart([*routing_frames, pickle.dumps(reply)])
        except zmq.ZMQError:
            pass

    def publish(self, topic: str, data: object) -> None:
        if not self._active:
            return
        try:
            with self._publish_lock:
                self._socket_pub.send_pyobj([topic, data])
        except zmq.ZMQError:
            pass

    def register(self, func: Callable) -> None:
        self._functions[func.__name__] = func

    def check_heartbeat(self) -> None:
        now = time()
        if self._heartbeat_at and now >= self._heartbeat_at:
            self.publish(HEARTBEAT_TOPIC, now)
            self._heartbeat_at = now + HEARTBEAT_INTERVAL
