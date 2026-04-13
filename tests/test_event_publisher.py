import unittest
from types import SimpleNamespace

from xtquant_bridge.event_publisher import EventPublisher


class FakeRpcServer:
    def __init__(self) -> None:
        self.published = []

    def publish(self, topic, event) -> None:
        self.published.append((topic, event))


class EventPublisherTests(unittest.TestCase):
    def test_enqueue_and_publish(self) -> None:
        rpc_server = FakeRpcServer()
        publisher = EventPublisher(rpc_server, maxsize=4)
        publisher.start()
        publisher.enqueue("topic", SimpleNamespace(data="value"))
        publisher.stop()
        publisher.join()
        self.assertEqual(len(rpc_server.published), 1)
        self.assertEqual(rpc_server.published[0][0], "topic")


if __name__ == "__main__":
    unittest.main()
