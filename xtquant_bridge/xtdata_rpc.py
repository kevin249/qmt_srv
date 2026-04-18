from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from .serialization import serialize_xtdata_result
from .xtdata_registry import XtdataMethodSpec


class XtdataMirrorExecutor:
    def __init__(self, xtdata_module: Any, registry: dict[str, Any], publisher: Any = None) -> None:
        self.xtdata = xtdata_module
        self.registry = registry
        self.publisher = publisher

    @staticmethod
    def _normalize_spec(spec: Any) -> dict[str, Any]:
        if isinstance(spec, dict):
            return spec
        if isinstance(spec, XtdataMethodSpec):
            return asdict(spec)
        if is_dataclass(spec):
            return asdict(spec)
        raise TypeError(f"unsupported xtdata registry entry: {type(spec)!r}")

    def call(self, rpc_name: str, *args: Any, **kwargs: Any) -> Any:
        if rpc_name not in self.registry:
            raise KeyError(f"unknown xtdata rpc: {rpc_name}")

        spec = self._normalize_spec(self.registry[rpc_name])
        if not spec.get("available", False):
            xtdata_name = spec.get("xtdata_name", rpc_name)
            raise NotImplementedError(f"{xtdata_name} is not available in current xtquant")

        if spec.get("subscription"):
            kwargs = {
                **kwargs,
                "callback": self._build_subscription_callback(spec),
            }

        func = getattr(self.xtdata, spec["xtdata_name"])
        result = func(*args, **kwargs)
        return serialize_xtdata_result(result)

    def _build_subscription_callback(self, spec: dict[str, Any]):
        topic = spec.get("topic") or spec.get("rpc_name") or spec.get("xtdata_name")

        def publish_callback(payload: Any) -> None:
            if self.publisher is None:
                return
            self.publisher.enqueue(topic, serialize_xtdata_result(payload))

        return publish_callback
