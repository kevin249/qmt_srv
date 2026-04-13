from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Event as ThreadEvent, Thread
from typing import Any


class EventPublisher:
    def __init__(self, rpc_server: Any, maxsize: int = 10000) -> None:
        self.rpc_server = rpc_server
        self.queue: Queue[tuple[str, Any] | None] = Queue(maxsize=maxsize)
        self.stop_event = ThreadEvent()
        self.thread: Thread | None = None
        self.dropped_count = 0

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = Thread(target=self.run, name="xtq-event-publisher", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.queue.put_nowait(None)
        except Full:
            pass

    def join(self) -> None:
        if self.thread and self.thread.is_alive():
            self.thread.join()

    def enqueue(self, topic: str, event: Any) -> None:
        item = (topic, event)
        try:
            self.queue.put_nowait(item)
        except Full:
            try:
                self.queue.get_nowait()
            except Empty:
                pass
            self.dropped_count += 1
            self.queue.put_nowait(item)

    def run(self) -> None:
        while not self.stop_event.is_set() or not self.queue.empty():
            try:
                item = self.queue.get(timeout=0.2)
            except Empty:
                continue

            if item is None:
                continue

            topic, event = item
            self.rpc_server.publish(topic, event)
