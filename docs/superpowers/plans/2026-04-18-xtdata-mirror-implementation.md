# xtdata 原生镜像双轨 RPC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 `D:\gupiao\cc\qmt_srv\docs\superpowers\specs\2026-04-18-xtdata-mirror-design.md` 为当前 bridge 增加 `xtdata` 原生镜像 RPC，同时保留现有 vn.py 兼容 RPC，并修复历史周期静默降级为 `1d` 的问题。

**Architecture:** 保留现有 `XtQuantBridge` 生命周期、事件发布线程和 vn.py 兼容接口；新增“注册表驱动”的 `xtdata` 镜像执行层与统一序列化层，把原生 `xtdata` 函数按本地实际签名暴露为 RPC。订阅类接口通过 REQ/REP 返回订阅号，通过现有 PUB/SUB 通道推送序列化后的回调数据。

**Tech Stack:** Python 3.13, `xtquant.xtdata`, `vnpy.rpc`, `vnpy.trader.constant`, `vnpy.trader.object`, `zmq`, `unittest`, `pandas`, `numpy`

---

### Task 1: 锁定当前缺陷并补齐镜像层失败测试

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_integration.py`
- Create: `D:\gupiao\cc\qmt_srv\tests\test_xtdata_registry.py`
- Create: `D:\gupiao\cc\qmt_srv\tests\test_xtdata_rpc.py`
- Create: `D:\gupiao\cc\qmt_srv\tests\test_serialization.py`

- [ ] **Step 1: 为 `query_history` 写失败测试，证明未知周期不应再默认降级为 `1d`**

```python
def test_query_history_rejects_unknown_interval(self) -> None:
    req = SimpleNamespace(
        symbol="000001",
        exchange=Exchange.SSE,
        interval="1min",
        start=None,
        end=None,
        vt_symbol="000001.SSE",
    )
    with self.assertRaises(ValueError):
        bridge.query_history(req)
```

- [ ] **Step 2: 为镜像注册表写失败测试，确认会暴露 `get_market_data_ex`、`download_history_data`、`subscribe_quote` 等入口**

```python
def test_registry_contains_core_xtdata_methods(self) -> None:
    registry = build_xtdata_registry(fake_xtdata)
    self.assertIn("xtdata.get_market_data_ex", registry)
    self.assertIn("xtdata.download_history_data", registry)
    self.assertIn("xtdata.subscribe_quote", registry)
```

- [ ] **Step 3: 为序列化层写失败测试，覆盖 `DataFrame`、`ndarray`、嵌套结构**

```python
def test_serialize_dataframe(self) -> None:
    frame = pd.DataFrame({"close": [10.1, 10.2]})
    payload = serialize_xtdata_result(frame)
    self.assertEqual(payload["__type__"], "dataframe")
```

- [ ] **Step 4: 为镜像 RPC 执行器写失败测试，验证 `period` 与 `dividend_type` 原样透传**

```python
def test_executor_passes_period_and_dividend_type_through(self) -> None:
    result = executor.call(
        "xtdata.get_market_data_ex",
        field_list=["time", "close"],
        stock_list=["000001.SZ"],
        period="1m",
        dividend_type="front_ratio",
    )
    self.assertEqual(fake_xtdata.last_kwargs["period"], "1m")
    self.assertEqual(fake_xtdata.last_kwargs["dividend_type"], "front_ratio")
```

- [ ] **Step 5: 运行新增测试并确认它们先失败**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest tests.test_integration tests.test_xtdata_registry tests.test_xtdata_rpc tests.test_serialization -v`  
Expected: 失败点集中在“未知周期仍被降级”“缺少注册表/执行器/序列化实现”。

- [ ] **Step 6: 提交测试基线**

```bash
git add tests/test_integration.py tests/test_xtdata_registry.py tests/test_xtdata_rpc.py tests/test_serialization.py
git commit -m "test: cover xtdata mirror rpc requirements"
```

### Task 2: 实现 xtdata 注册表与统一序列化层

**Files:**
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\xtdata_registry.py`
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\serialization.py`
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\__init__.py`
- Test: `D:\gupiao\cc\qmt_srv\tests\test_xtdata_registry.py`
- Test: `D:\gupiao\cc\qmt_srv\tests\test_serialization.py`

- [ ] **Step 1: 写 `xtdata_registry.py` 的最小骨架，定义注册项结构与核心镜像函数清单**

```python
@dataclass(frozen=True)
class XtdataMethodSpec:
    rpc_name: str
    xtdata_name: str
    subscription: bool = False
    topic: str | None = None
```

- [ ] **Step 2: 写 `build_xtdata_registry()` 的最小实现，按本地 `xtdata` 实例探测函数是否存在**

```python
def build_xtdata_registry(xtdata_module: Any) -> dict[str, XtdataMethodSpec]:
    registry: dict[str, XtdataMethodSpec] = {}
    for spec in CORE_SPECS:
        available = hasattr(xtdata_module, spec.xtdata_name)
        registry[spec.rpc_name] = replace(spec, available=available)
    return registry
```

- [ ] **Step 3: 写 `serialization.py` 的最小实现，先让 `DataFrame` 与 `ndarray` 测试转绿**

```python
def serialize_xtdata_result(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return {"__type__": "dataframe", "orient": "split", "data": value.to_dict(orient="split")}
    if isinstance(value, np.ndarray):
        return {"__type__": "ndarray", "dtype": str(value.dtype), "data": value.tolist()}
    ...
```

- [ ] **Step 4: 跑注册表与序列化测试，确认转绿**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest tests.test_xtdata_registry tests.test_serialization -v`  
Expected: PASS

- [ ] **Step 5: 在 `xtquant_bridge/__init__.py` 暴露新模块需要导出的入口**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m py_compile D:\gupiao\cc\qmt_srv\xtquant_bridge\xtdata_registry.py D:\gupiao\cc\qmt_srv\xtquant_bridge\serialization.py`

- [ ] **Step 6: 提交注册表与序列化层**

```bash
git add xtquant_bridge/xtdata_registry.py xtquant_bridge/serialization.py xtquant_bridge/__init__.py tests/test_xtdata_registry.py tests/test_serialization.py
git commit -m "feat: add xtdata registry and serialization layer"
```

### Task 3: 实现 xtdata 镜像执行器与缺失函数错误语义

**Files:**
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\xtdata_rpc.py`
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\bridge.py`
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\rpc_handler.py`
- Test: `D:\gupiao\cc\qmt_srv\tests\test_xtdata_rpc.py`
- Test: `D:\gupiao\cc\qmt_srv\tests\test_rpc_handler.py`

- [ ] **Step 1: 为执行器写最小实现，只支持一次性镜像调用**

```python
class XtdataMirrorExecutor:
    def __init__(self, xtdata_module: Any, registry: dict[str, XtdataMethodSpec], publisher: EventPublisher | None = None) -> None:
        ...

    def call(self, rpc_name: str, **kwargs: Any) -> Any:
        spec = self.registry[rpc_name]
        if not spec.available:
            raise NotImplementedError(f"{spec.xtdata_name} is not available in current xtquant")
        func = getattr(self.xtdata, spec.xtdata_name)
        return serialize_xtdata_result(func(**kwargs))
```

- [ ] **Step 2: 让 `RpcRequestHandler` 能接收镜像 RPC，并统一记录日志**

```python
def call_xtdata(self, method: str, **kwargs):
    self._log(f"rpc xtdata method={method}")
    return self.bridge.call_xtdata(method, **kwargs)
```

- [ ] **Step 3: 在 `XtQuantBridge` 中挂载注册表与执行器，并注册核心镜像 RPC**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest tests.test_xtdata_rpc tests.test_rpc_handler -v`  
Expected: PASS

- [ ] **Step 4: 扩展执行器错误处理，明确区分“函数缺失”“参数错误”“运行时错误”**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest tests.test_xtdata_rpc -v`

- [ ] **Step 5: 确认本地版本缺失函数会返回明确错误，而不是未注册或静默失败**

```python
with self.assertRaises(NotImplementedError):
    executor.call("xtdata.call_formula_batch", ...)
```

- [ ] **Step 6: 提交执行器与 RPC 接线**

```bash
git add xtquant_bridge/xtdata_rpc.py xtquant_bridge/bridge.py xtquant_bridge/rpc_handler.py tests/test_xtdata_rpc.py tests/test_rpc_handler.py
git commit -m "feat: add xtdata mirror rpc executor"
```

### Task 4: 打通订阅类接口与 PUB/SUB 推送

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\xtdata_registry.py`
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\xtdata_rpc.py`
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\bridge.py`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_xtdata_rpc.py`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_integration.py`

- [ ] **Step 1: 为 `subscribe_quote`、`subscribe_whole_quote`、`subscribe_formula` 写失败测试，验证返回订阅号、推送走指定 topic**

```python
def test_subscribe_quote_publishes_serialized_payload(self) -> None:
    seq = executor.call("xtdata.subscribe_quote", stock_code="000001.SZ", period="1m")
    self.assertEqual(seq, 1)
    fake_xtdata.fire_callback({"000001.SZ": [{"close": 10.1}]})
    self.assertEqual(fake_publisher.events[-1][0], "xtdata.subscribe_quote")
```

- [ ] **Step 2: 在注册表中标记订阅类接口与 topic 名**

```python
XtdataMethodSpec(
    rpc_name="xtdata.subscribe_quote",
    xtdata_name="subscribe_quote",
    subscription=True,
    topic="xtdata.subscribe_quote",
)
```

- [ ] **Step 3: 在执行器中为订阅类自动包裹 callback，把回调结果序列化后发布到事件队列**

- [ ] **Step 4: 跑订阅与集成测试，确保 REQ/REP 与 PUB/SUB 都正确**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest tests.test_xtdata_rpc tests.test_integration -v`  
Expected: PASS

- [ ] **Step 5: 补齐 `unsubscribe_quote`、`unsubscribe_formula` 等取消类接口的回归测试**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest tests.test_xtdata_rpc -v`

- [ ] **Step 6: 提交订阅桥能力**

```bash
git add xtquant_bridge/xtdata_registry.py xtquant_bridge/xtdata_rpc.py xtquant_bridge/bridge.py tests/test_xtdata_rpc.py tests/test_integration.py
git commit -m "feat: bridge xtdata subscriptions over pubsub"
```

### Task 5: 修复 vn.py 兼容层周期处理并补齐 README

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\bridge.py`
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\utils.py`
- Modify: `D:\gupiao\cc\qmt_srv\README.md`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_integration.py`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_rpc_logging.py`

- [ ] **Step 1: 为 vn.py 兼容层补失败测试，验证未知周期直接报错**

```python
def test_query_history_raises_for_invalid_vnpy_interval(self) -> None:
    req = SimpleNamespace(..., interval="1s")
    with self.assertRaises(ValueError):
        bridge.query_history(req)
```

- [ ] **Step 2: 在 `bridge.py` 中抽出显式的 vn.py interval 映射函数**

```python
def map_vnpy_interval_to_xt(interval: Interval | None) -> str:
    if interval is None:
        return "1d"
    if interval not in INTERVAL_VT2XT:
        raise ValueError(f"unsupported vnpy interval: {interval}")
    return INTERVAL_VT2XT[interval]
```

- [ ] **Step 3: 跑 `query_history` 相关测试，确认未知周期不再变成 `1d`**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest tests.test_integration tests.test_rpc_logging -v`  
Expected: PASS

- [ ] **Step 4: 更新 README，说明双轨 RPC 的用法**

README 至少补充：
- 现有 vn.py 兼容接口仍然保留
- 新增 `xtdata.*` 镜像 RPC
- 镜像层 `period` 与 `dividend_type` 直接按 xtdata 原生参数传递
- 订阅类接口通过 PUB/SUB 推送

- [ ] **Step 5: 对 README 做最小 smoke check，确认示例命令与函数名一致**

Run: `rg -n "xtdata\\.|query_history|dividend_type|period" D:\gupiao\cc\qmt_srv\README.md`

- [ ] **Step 6: 提交兼容层修复与文档更新**

```bash
git add xtquant_bridge/bridge.py xtquant_bridge/utils.py README.md tests/test_integration.py tests/test_rpc_logging.py
git commit -m "fix: reject unsupported vnpy history intervals"
```

### Task 6: 扩展镜像覆盖面并完成总体验证

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\xtdata_registry.py`
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\xtdata_rpc.py`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_xtdata_registry.py`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_xtdata_rpc.py`

- [ ] **Step 1: 按 spec 补齐第一批镜像函数到注册表**

至少补齐以下分组：
- 行情获取与下载
- 板块与基础信息
- 财务
- 模型
- Level2
- 连接与节假日

- [ ] **Step 2: 为每个分组各补一个透传测试，避免只注册不调用**

```python
def test_get_financial_data_is_registered(self) -> None: ...
def test_download_sector_data_is_registered(self) -> None: ...
def test_get_l2_quote_is_registered(self) -> None: ...
```

- [ ] **Step 3: 运行镜像层完整测试**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest tests.test_xtdata_registry tests.test_xtdata_rpc -v`  
Expected: PASS

- [ ] **Step 4: 运行全量测试**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest discover -s D:\gupiao\cc\qmt_srv\tests -v`  
Expected: PASS

- [ ] **Step 5: 运行语法校验**

Run: `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -c "import py_compile, pathlib; [py_compile.compile(str(p), doraise=True) for p in [pathlib.Path('app.py'), pathlib.Path('probe_rpc.py'), *pathlib.Path('xtquant_bridge').glob('*.py'), *pathlib.Path('tests').glob('*.py')]]; print('ok')"`  
Expected: `ok`

- [ ] **Step 6: 提交扩展覆盖与最终验证**

```bash
git add xtquant_bridge/xtdata_registry.py xtquant_bridge/xtdata_rpc.py tests/test_xtdata_registry.py tests/test_xtdata_rpc.py
git commit -m "feat: expand xtdata mirror rpc coverage"
```

---

## 完成定义

满足以下条件后，才可以宣告本计划完成：

- `query_history` 不再把未知周期静默变成 `1d`
- `xtdata` 镜像 RPC 可调用 `get_market_data_ex`、`download_history_data`、`subscribe_quote` 等核心接口
- 镜像层支持原生 `period` 与 `dividend_type`
- 订阅类接口能通过 PUB/SUB 推送序列化后的回调数据
- 对本地版本缺失函数返回明确错误
- README 清晰说明双轨 RPC 用法
- 全量测试与语法检查全部通过
