# xtquant ZeroMQ Bridge Design Specification

**Date:** 2026-04-12  
**Goal:** Replace vnpy_xt gateway with direct xtquant integration using event-driven architecture to minimize trading slippage while maintaining 100% vnpy RPC API compatibility.

---

## Background

The current implementation uses vnpy + vnpy_xt (wrapper around xtquant) + ZeroMQ RPC. However, vnpy_xt has compatibility issues with MiniQMT. The solution is to bypass vnpy entirely and build a direct xtquant bridge that:

1. Uses xtquant's native callback APIs for event-driven market data and trading
2. Implements vnpy's RPC protocol over ZeroMQ for client compatibility
3. Translates xtquant data structures to vnpy data models
4. Supports high-frequency and intraday trading strategies with minimal latency

**Why:** Eliminate vnpy_xt compatibility issues and reduce software-level slippage by using event-driven callbacks instead of polling.

**How to apply:** This design replaces the entire vnpy stack (MainEngine, EventEngine, BaseGateway, RpcServiceApp) with a custom bridge that directly wraps xtquant APIs.

---

## Architecture Overview

### Core Components

**1. XtQuantBridge**
- Main server class managing xtquant lifecycle and ZeroMQ sockets
- Coordinates initialization, shutdown, and error recovery
- Owns REP socket (command handler) and PUB socket (event broadcaster)

**2. DataTranslator**
- Converts xtquant data structures to vnpy data models
- Handles symbol normalization (SH↔SSE, SZ↔SZSE)
- Provides type-safe vnpy objects (TickData, OrderData, TradeData, etc.)

**3. RpcRequestHandler**
- Processes REQ/REP commands from clients
- Routes commands to appropriate xtquant APIs
- Returns responses in vnpy RPC format

**4. EventPublisher**
- Manages event queue and publisher thread
- Publishes real-time events via ZeroMQ PUB socket
- Handles backpressure and queue overflow

**5. XtQuantCallbackRouter**
- Receives callbacks from xtquant (market data, orders, trades)
- Translates to vnpy models via DataTranslator
- Enqueues events for EventPublisher

### Threading Model

- **Main thread:** ZeroMQ REQ/REP loop processing client commands
- **Publisher thread:** Dequeues events and publishes via PUB socket
- **xtquant callback threads:** Native xtquant threads that invoke our callbacks

### Data Flow

```
Client Request Flow:
Client → REQ socket → RpcRequestHandler → xtquant API → Response → Client

Event Flow:
xtquant callback → CallbackRouter → DataTranslator → Event Queue → Publisher Thread → PUB socket → Client
```

**Why:** Separating command handling (REQ/REP) from event publishing (PUB/SUB) prevents slow clients from blocking order execution. Using a queue between callbacks and publisher provides backpressure control.

**How to apply:** All xtquant callbacks must be non-blocking (translate + enqueue only). All ZeroMQ I/O happens in dedicated threads.

---

## Data Translation Layer

### Responsibilities

**Market Data Translation:**
- xtquant tick dict → vnpy TickData
- xtquant orderbook → vnpy depth fields (bid_price_1-5, ask_price_1-5, etc.)
- xtquant kline → vnpy BarData

**Trading Data Translation:**
- xtquant order dict → vnpy OrderData
- xtquant trade dict → vnpy TradeData
- xtquant position dict → vnpy PositionData
- xtquant account dict → vnpy AccountData

**Symbol Normalization:**
- xtquant format: "600000.SH", "000001.SZ"
- vnpy format: "600000.SSE", "000001.SZSE"
- Exchange mapping: SH→SSE, SZ→SZSE, CFE→CFFEX, etc.

### Field Mapping Strategy

- **Direct mapping:** Fields with same semantics (price, volume, timestamp)
- **Calculated fields:** vnpy's `datetime` from xtquant's timestamp
- **Default values:** vnpy fields not in xtquant (e.g., `gateway_name = "XTQUANT"`)
- **Type safety:** All translators return typed vnpy objects, not dicts
- **Validation:** Critical fields (symbol, exchange, direction) validated on translation
- **Graceful degradation:** Missing optional fields use sensible defaults

**Why:** Clients expect vnpy data models. Full translation ensures 100% API compatibility.

**How to apply:** Every xtquant callback must pass through DataTranslator before publishing. No raw xtquant dicts exposed to clients.

---

## RPC Protocol Implementation

### REQ/REP Command Set

**Market Data Commands:**
- `subscribe(symbols: List[str])` → Subscribe to tick data
- `unsubscribe(symbols: List[str])` → Unsubscribe from symbols
- `query_history(symbol: str, interval: str, start: str, end: str)` → Get historical bars

**Trading Commands:**
- `send_order(req: OrderRequest)` → Place order (sync or async)
- `cancel_order(req: CancelRequest)` → Cancel order
- `send_orders(reqs: List[OrderRequest])` → Batch orders
- `cancel_orders(reqs: List[CancelRequest])` → Batch cancel

**Query Commands:**
- `query_account()` → Get account info
- `query_position()` → Get positions
- `query_orders()` → Get order list
- `query_trades()` → Get trade records

### PUB/SUB Event Topics

- `tick.{symbol}` → Real-time tick data
- `order` → Order status updates
- `trade` → Trade fills
- `position` → Position changes
- `account` → Account updates
- `log` → System logs
- `contract` → Contract info updates

### Message Format

**Request:**
```json
{
  "req_id": "unique_request_id",
  "function": "send_order",
  "params": {
    "symbol": "600000.SSE",
    "exchange": "SSE",
    "direction": "LONG",
    "type": "LIMIT",
    "price": 10.5,
    "volume": 100
  }
}
```

**Response:**
```json
{
  "req_id": "unique_request_id",
  "result": "order_id_12345",
  "error": null
}
```

**Event:**
```json
{
  "topic": "tick.600000.SSE",
  "data": {
    "symbol": "600000.SSE",
    "exchange": "SSE",
    "last_price": 10.52,
    "volume": 1000,
    "datetime": "2026-04-12 09:30:01.500"
  },
  "timestamp": 1712888401.5
}
```

### Error Handling

- xtquant errors wrapped in `response["error"]`
- Connection errors trigger reconnection logic
- Invalid requests return error without crashing server
- Callback exceptions logged but don't stop event processing

**Why:** Matching vnpy's RPC protocol exactly ensures existing clients work without modification.

**How to apply:** All command handlers must return responses in this format. All events must include topic and timestamp.

---

## Event-Driven Callback Architecture

### xtquant Callback Registration

**Market Data Callbacks:**
- `xtdata.subscribe_quote(stock_list, callback)` → Real-time tick callbacks
- `xtdata.subscribe_whole_quote(stock_list, callback)` → Full depth (optional)
- Callbacks fire immediately when exchange pushes new data (no polling)

**Trading Callbacks:**
- `xttrader.on_order_stock(callback)` → Order status changes
- `xttrader.on_trade(callback)` → Trade fills
- `xttrader.on_stock_order(callback)` → Order acknowledgments
- `xttrader.on_stock_asset(callback)` → Account/position updates
- `xttrader.on_stock_trade(callback)` → Trade confirmations

### Callback Processing Pipeline

```
xtquant callback (xtquant thread)
  ↓
DataTranslator.translate() (convert to vnpy model)
  ↓
Event Queue (thread-safe queue.Queue, fixed size)
  ↓
Publisher Thread (dequeue + ZeroMQ publish)
  ↓
Clients receive via SUB socket
```

### Performance Optimizations

- **Non-blocking callbacks:** Translate + enqueue only, no I/O
- **Async order APIs:** Use `order_async` to avoid blocking
- **Pre-allocated objects:** Reuse vnpy objects in hot path where possible
- **Batch publishing:** If queue has backlog, publish multiple events in one iteration
- **Backpressure:** Fixed-size queue drops oldest events if clients too slow

### Thread Safety

- All xtquant callbacks use thread-safe `queue.Queue`
- No blocking operations in callbacks
- Publisher thread handles all ZeroMQ I/O
- No shared mutable state between threads

**Why:** Event-driven callbacks eliminate polling delay (up to 1 second in polling architectures). Non-blocking callbacks ensure xtquant threads don't stall.

**How to apply:** Every callback must complete in <1ms. Heavy work (logging, persistence) must be async or offloaded.

---

## Initialization and Lifecycle Management

### Startup Sequence

1. **Load Configuration** - Read config.json (reuse existing logic)
2. **Initialize xtdata** - `xtdata.connect()` or local mode with `set_data_home_dir()`
3. **Initialize xttrader** - `xttrader.connect(path, session_id, account_id)`
4. **Create ZeroMQ Sockets** - Bind REP and PUB sockets to configured addresses
5. **Start Publisher Thread** - Begin event queue processing loop
6. **Register Callbacks** - Attach all xtquant callback handlers
7. **Enter Main Loop** - Process REQ/REP commands until shutdown

### Shutdown Sequence

1. **Stop Accepting Requests** - Close REP socket
2. **Flush Event Queue** - Publish remaining events (with timeout)
3. **Stop Publisher Thread** - Signal stop, join thread
4. **Unsubscribe All** - Clean up xtquant subscriptions
5. **Disconnect xtquant** - `xttrader.disconnect()`, `xtdata.disconnect()`
6. **Close ZeroMQ Context** - Release all resources

### Error Recovery

- **xtquant disconnection:** Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s)
- **ZeroMQ socket errors:** Log error, attempt rebind once
- **Callback exceptions:** Log error with traceback, continue processing
- **Queue overflow:** Drop oldest events, log warning with drop count

### Health Monitoring

- Track last tick timestamp (detect market data stall)
- Monitor event queue depth (detect slow clients)
- Log connection status changes (connected, disconnected, reconnecting)
- Optional: Expose health check endpoint via simple HTTP server

**Why:** Robust lifecycle management prevents resource leaks and ensures clean shutdown. Auto-reconnect handles transient network issues.

**How to apply:** All initialization must be idempotent. Shutdown must be callable multiple times safely.

---

## Configuration

### config.json Structure

Extend existing config with xtquant-specific fields:

```json
{
  "xt": {
    "qmt_path": "D:\\迅投QMT交易终端财通证券版\\userdata_mini",
    "account_id": "35213367",
    "session_id": "session_001",
    "simulation": true,
    "callback_thread_pool_size": 4,
    "event_queue_size": 10000
  },
  "rpc": {
    "rep_address": "tcp://*:20140",
    "pub_address": "tcp://*:20141"
  }
}
```

**New Fields:**
- `session_id`: xtquant session identifier for xttrader.connect()
- `callback_thread_pool_size`: Thread pool for async order handling (optional)
- `event_queue_size`: Max events in queue before backpressure kicks in

**Why:** Configuration-driven behavior allows tuning without code changes.

**How to apply:** All configurable values must have sensible defaults. Validate critical fields on startup.

---

## Testing Strategy

### Unit Tests

**DataTranslator Tests:**
- Test each xtquant → vnpy conversion function
- Verify symbol normalization (SH↔SSE, SZ↔SZSE)
- Test edge cases (missing fields, invalid data, null values)
- Verify type safety (returns vnpy objects, not dicts)

**RpcRequestHandler Tests:**
- Mock xtquant APIs, verify command routing
- Test request/response serialization
- Test error handling for invalid requests
- Verify all command types (subscribe, send_order, query_*, etc.)

**EventPublisher Tests:**
- Test queue behavior (enqueue, dequeue, overflow)
- Verify event serialization format
- Test thread safety with concurrent producers
- Test backpressure (queue full scenarios)

### Integration Tests

**Mock xtquant Mode:**
- Simulate xtquant callbacks with test data
- Verify end-to-end: callback → translation → publish
- Test client subscription and data reception
- Test order round-trip (send_order → callback → event)

**ZeroMQ Protocol Tests:**
- Test REQ/REP round-trip with real sockets
- Test PUB/SUB with multiple subscribers
- Verify topic filtering works correctly
- Test reconnection scenarios

**Compatibility Tests:**
- Run existing vnpy RPC clients against new server
- Verify 100% API compatibility (all commands work)
- Test all command types and event topics
- Compare responses with original vnpy_rpcservice

### Performance Tests

- Measure callback-to-publish latency (target <5ms p99)
- Test throughput with high tick rate (1000+ ticks/sec)
- Verify no memory leaks under sustained load (24h run)
- Test queue behavior under backpressure

**Why:** Comprehensive testing ensures production readiness and prevents regressions. Performance tests validate low-latency requirements.

**How to apply:** All tests must be automated and runnable via `python -m unittest`. Performance tests should be separate (long-running).

---

## Implementation Modules

### File Structure

```
qmt_srv/
├── app.py                    # Entry point (modified)
├── config.json               # Configuration (extended)
├── xtquant_bridge/
│   ├── __init__.py
│   ├── bridge.py             # XtQuantBridge main class
│   ├── translator.py         # DataTranslator
│   ├── rpc_handler.py        # RpcRequestHandler
│   ├── event_publisher.py    # EventPublisher
│   ├── callback_router.py    # XtQuantCallbackRouter
│   └── utils.py              # Symbol normalization, helpers
├── tests/
│   ├── test_translator.py
│   ├── test_rpc_handler.py
│   ├── test_event_publisher.py
│   ├── test_integration.py
│   └── test_performance.py
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-04-12-xtquant-zeromq-bridge-design.md
```

### Module Responsibilities

**bridge.py:**
- XtQuantBridge class (main server)
- Initialization and shutdown logic
- Error recovery and health monitoring

**translator.py:**
- DataTranslator class with methods for each data type
- Symbol normalization functions
- Field mapping and validation

**rpc_handler.py:**
- RpcRequestHandler class
- Command routing to xtquant APIs
- Request/response serialization

**event_publisher.py:**
- EventPublisher class
- Event queue management
- Publisher thread loop

**callback_router.py:**
- XtQuantCallbackRouter class
- Callback registration with xtquant
- Routing to DataTranslator and EventPublisher

**utils.py:**
- Symbol format conversion
- Exchange code mapping
- Common helper functions

**Why:** Modular design allows independent testing and clear separation of concerns.

**How to apply:** Each module should have a single responsibility. No circular dependencies between modules.

---

## Migration Path

### Phase 1: Build Core Bridge
- Implement XtQuantBridge, DataTranslator, EventPublisher
- Add basic market data support (subscribe, tick callbacks)
- Test with mock xtquant

### Phase 2: Add Trading Support
- Implement RpcRequestHandler for trading commands
- Add order/trade callbacks
- Test order round-trip

### Phase 3: Full API Coverage
- Implement all query commands
- Add all event types
- Achieve 100% vnpy RPC compatibility

### Phase 4: Production Hardening
- Add error recovery and reconnection
- Performance optimization
- Load testing and monitoring

### Phase 5: Deployment
- Run in parallel with existing vnpy_xt setup
- Gradual client migration
- Deprecate vnpy_xt once stable

**Why:** Incremental migration reduces risk and allows validation at each step.

**How to apply:** Each phase should be fully tested before moving to next. Keep old system running until new system proven stable.

---

## Success Criteria

- ✓ 100% vnpy RPC API compatibility (all existing clients work unchanged)
- ✓ Event-driven architecture (no polling, callbacks only)
- ✓ Callback-to-publish latency <5ms (p99)
- ✓ Supports 1000+ ticks/sec throughput
- ✓ No memory leaks under 24h load
- ✓ Auto-reconnect on xtquant disconnection
- ✓ Full test coverage (unit + integration + performance)
- ✓ Supports all xtquant features (stocks, futures, options, all order types)

---

## Risks and Mitigations

**Risk:** vnpy RPC protocol has undocumented behavior  
**Mitigation:** Run compatibility tests against real vnpy_rpcservice, capture edge cases

**Risk:** xtquant callback threading model causes race conditions  
**Mitigation:** Use thread-safe queue, no shared mutable state, thorough concurrency testing

**Risk:** Performance doesn't meet <5ms latency target  
**Mitigation:** Profile hot paths, optimize translation layer, consider Cython for critical code

**Risk:** xtquant API changes break compatibility  
**Mitigation:** Pin xtquant version, add integration tests that detect API changes

**Risk:** Clients overwhelm event queue causing drops  
**Mitigation:** Configurable queue size, backpressure monitoring, client-side buffering

---

## Open Questions

None - all requirements clarified during brainstorming.
