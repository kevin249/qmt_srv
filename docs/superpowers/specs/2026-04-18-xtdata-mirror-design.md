# xtdata 原生镜像与 vn.py 兼容双轨 RPC 设计

**日期：** 2026-04-18  
**目标：** 在保留现有 vn.py RPC 兼容能力的前提下，新增一套与 `xtquant.xtdata` 原生函数名、参数名、默认值尽量一致的镜像 RPC 接口，覆盖行情、下载、板块、财务、模型、连接等能力，并修正当前历史周期被错误降级为 `1d` 的问题。

---

## 背景

当前项目已经具备以下能力：

1. 通过 ZeroMQ 暴露 vn.py 风格的 RPC 接口。
2. 用 `xtquant` 直连 MiniQMT 获取账户、持仓、订单、成交、tick、bar。
3. 通过事件发布线程向客户端广播实时数据。

当前存在两个关键缺口：

1. `query_history` 仅支持少量 `Interval` 映射，且对未识别周期静默回退到 `1d`，导致 `1s`、`1min`、`1m` 等请求被错误转成日线。
2. 项目目前只暴露少量 vn.py 风格 RPC，无法按 `xtdata` 原生接口直接调用，缺少 K 线扩展周期、复权方式、板块/财务/模型/下载/连接等能力。

本次设计要求同时满足：

1. 保留现有 vn.py 风格 RPC，兼容已有客户端。
2. 新增一套 `xtdata` 原生镜像 RPC，接口名和参数尽量与本地安装的 `xtquant.xtdata` 一致。
3. 支持官方文档中的全部 K 线与特色 period。
4. 支持全部复权方式。
5. 覆盖行情相关接口，并一并镜像页面中涉及的下载、板块、财务、模型、连接等接口。

---

## 设计目标

### 必须实现

1. 保留现有 vn.py 兼容 RPC，不破坏已有调用方。
2. 新增 `xtdata` 原生镜像 RPC。
3. `period` 在镜像层按原生字符串透传，不再经过 `vnpy.Interval` 中转。
4. `dividend_type` 在镜像层完整支持：
   - `none`
   - `front`
   - `back`
   - `front_ratio`
   - `back_ratio`
5. 为订阅类接口提供与现有 PUB/SUB 机制兼容的实时推送方案。
6. 对文档存在但当前本地 `xtdata` 版本缺失的函数，返回明确的“当前 xtquant 版本不支持”错误，而不是静默失败。

### 明确不做

1. 不改变当前 ZeroMQ 传输协议的基础形态，仍使用现有 `vnpy.rpc` REQ/REP + PUB/SUB 模式。
2. 不要求在本次设计中彻底重写现有 bridge 主体结构。
3. 不在本次设计中引入 HTTP/REST 或 WebSocket。

---

## 官方能力范围与本地版本差异

根据 `xtdata` 官方页面与当前本地安装版本的实际探测结果，本地版本中可用的核心函数包括：

- 行情订阅/反订阅：`subscribe_quote`、`subscribe_whole_quote`、`unsubscribe_quote`、`run`
- 模型：`subscribe_formula`、`unsubscribe_formula`、`call_formula`、`generate_index_data`
- 行情获取：`get_market_data`、`get_market_data_ex`、`get_local_data`、`get_full_tick`、`get_full_kline`、`get_divid_factors`
- 历史下载：`download_history_data`、`download_history_data2`、`download_history_contracts`
- 日历与辅助：`get_holidays`、`get_trading_calendar`、`get_trading_dates`、`get_period_list`
- 可转债/ETF/IPO：`download_cb_data`、`get_cb_info`、`get_ipo_info`、`download_etf_info`、`get_etf_info`
- 财务：`get_financial_data`、`download_financial_data`、`download_financial_data2`
- 基础信息与板块：`get_instrument_detail`、`get_instrument_type`、`get_sector_list`、`get_stock_list_in_sector`、`download_sector_data`、`create_sector_folder`、`create_sector`、`add_sector`、`remove_stock_from_sector`、`remove_sector`、`reset_sector`、`get_index_weight`、`download_index_weight`
- 连接与其他：`reconnect`、`get_option_detail_data`
- Level2 与扩展：`get_l2_quote`、`get_l2_order`、`get_l2_transaction`、`get_l2thousand_queue`、`subscribe_l2thousand`、`subscribe_l2thousand_queue`

当前本地版本缺失但官方页面提到的函数至少包括：

- `call_formula_batch`
- `get_trading_time`

因此镜像层必须同时处理两类情况：

1. **本地版本存在：** 正常注册与调用。
2. **文档存在但本地版本缺失：** 暴露一致的错误语义，明确提示当前安装版本不可用。

---

## 总体架构

新增能力采用“双轨 RPC + 注册表驱动”的结构。

### 轨道一：vn.py 兼容层

继续保留当前 RPC：

- `subscribe`
- `send_order`
- `cancel_order`
- `query_history`
- `get_tick`
- `get_order`
- `get_trade`
- `get_position`
- `get_account`
- `get_contract`
- `get_all_ticks`
- `get_all_orders`
- `get_all_trades`
- `get_all_positions`
- `get_all_accounts`
- `get_all_contracts`
- `get_all_active_orders`

该层面向现有客户端，继续返回 vn.py 风格对象和缓存数据。

### 轨道二：xtdata 原生镜像层

新增一组原生镜像 RPC，用于尽量按本地 `xtdata` 的调用方式远程执行对应函数。

建议使用统一前缀以避免与现有 RPC 冲突，例如：

- `xtdata.subscribe_quote`
- `xtdata.get_market_data_ex`
- `xtdata.download_history_data`

如果底层 `RpcServer.register()` 不支持带点号的方法名，则内部注册名使用安全格式，例如：

- `xtdata__subscribe_quote`
- `xtdata__get_market_data_ex`

客户端侧再通过适配器映射到 `xtdata.xxx` 名称。

### 注册表驱动

镜像层不为每个函数手写一份完全重复的包装，而是新增一份接口注册表，描述：

- RPC 名称
- 对应的 `xtdata` 函数名
- 是否为订阅类接口
- 是否需要特殊回调桥接
- 返回值是否需要特殊序列化
- 本地版本是否可用
- 备注或约束

这样可以避免大量重复代码，并且让未来升级 `xtquant` 时只需扩展注册表和少量特例逻辑。

---

## 模块边界

### `xtquant_bridge/bridge.py`

职责保留为：

1. 生命周期管理
2. ZeroMQ RPC 注册
3. PUB/SUB 事件发布
4. 交易接口与账户快照维护

新增职责仅限：

1. 持有镜像层执行器实例
2. 在启动时收集本地 `xtdata` 可用函数信息
3. 注册镜像 RPC

不应继续把每个 `xtdata` 接口的参数适配、回调处理、序列化逻辑都堆在该文件中。

### `xtquant_bridge/rpc_handler.py`

保留现有 vn.py 接口入口。

新增职责：

1. 暴露统一的 `xtdata` 调用入口，或者动态注册一组镜像方法。
2. 为镜像调用写统一日志，记录函数名和关键参数。
3. 不直接实现具体业务逻辑，只转发给镜像执行器。

### `xtquant_bridge/xtdata_registry.py`

新增文件，职责：

1. 定义镜像 RPC 注册表。
2. 描述可用函数、缺失函数、订阅类函数、特殊序列化函数。
3. 为启动阶段提供“应注册接口列表”。

### `xtquant_bridge/xtdata_rpc.py`

新增文件，职责：

1. 解析镜像 RPC 请求。
2. 调用实际的 `xtdata` 函数。
3. 为订阅类函数挂接回调桥。
4. 统一处理缺失函数、参数错误、运行异常。
5. 对返回值执行标准序列化。

### `xtquant_bridge/serialization.py`

新增文件，职责：

1. 递归序列化 `dict/list/tuple`
2. 序列化 `pandas.DataFrame`
3. 序列化 `numpy.ndarray`
4. 处理时间类型、NaN、标量类型
5. 生成可逆的类型标记结构

### `xtquant_bridge/utils.py`

补充与周期、复权、时间格式相关的常量或校验函数，但不再把镜像层的 period 规则和 vn.py 兼容层混在一起。

---

## 周期与复权设计

### 镜像层 `period`

镜像层对 `period` 使用 `xtdata` 原生字符串，不再尝试映射为 `vnpy.Interval`。

应支持至少以下 level1 周期：

- `tick`
- `1m`
- `3m`
- `5m`
- `15m`
- `30m`
- `1h`
- `1d`
- `1w`
- `1mon`
- `1q`
- `1hy`
- `1y`

应支持官方页面列出的特色 period，例如：

- `transactioncount1m`
- `transactioncount1d`
- `northfinancechange1m`
- `northfinancechange1d`
- `historycontract`
- `optionhistorycontract`
- `historymaincontract`
- `stoppricedata`
- `snapshotindex`
- 以及页面列出的其他投研版特色数据类型

镜像层不对 period 做“修正”或“降级”。调用方传什么，就传给 `xtdata` 什么。

### 镜像层 `dividend_type`

应原样支持：

- `none`
- `front`
- `back`
- `front_ratio`
- `back_ratio`

镜像层不修改默认值，不自动补默认除权逻辑，直接遵循本地 `xtdata` 签名。

### vn.py 兼容层 `query_history`

现有逻辑中最严重的问题是：

```python
xt_interval = "1d" if req.interval is None else {...}.get(req.interval, "1d")
```

这个逻辑会把未知值静默降级到 `1d`。

修正原则：

1. 兼容层只接受明确定义过的 vn.py 周期。
2. 不支持的周期直接报错。
3. 绝不再把未知周期默认转成 `1d`。

---

## 回调与实时推送设计

### 基本原则

REQ/REP 返回值只负责一次性响应，持续回调通过现有 PUB/SUB 通道发送。

### 订阅类函数

以下接口属于订阅类：

- `subscribe_quote`
- `subscribe_whole_quote`
- `subscribe_formula`
- `subscribe_l2thousand`
- `subscribe_l2thousand_queue`

这些接口在 REQ/REP 中只返回：

- 订阅号
- 或明确错误

其后续推送数据通过 PUB/SUB 发出。

### 推送 topic

建议新增 `xtdata` 风格 topic，例如：

- `xtdata.subscribe_quote`
- `xtdata.subscribe_whole_quote`
- `xtdata.subscribe_formula`
- `xtdata.subscribe_l2thousand`
- `xtdata.subscribe_l2thousand_queue`

推送 payload 应尽量保持与本地回调结构一致，再经过统一序列化。

### 回调包装

镜像层不允许客户端通过 RPC 传可执行函数。服务端内部需要为订阅接口自动生成包装回调：

1. 接收 `xtdata` 本地回调数据
2. 序列化
3. 发布到对应 topic

---

## 返回值序列化设计

### 目标

镜像层返回值必须：

1. 能被稳定跨进程序列化
2. 尽量保留原始结构
3. 可由客户端恢复为 pandas/numpy 结构

### 规则

#### 普通类型

- `None`、`bool`、`int`、`float`、`str` 原样返回
- `dict/list/tuple` 递归处理

#### `pandas.DataFrame`

统一序列化为带类型标记的结构，例如：

```python
{
    "__type__": "dataframe",
    "orient": "split",
    "data": {...}
}
```

#### `numpy.ndarray`

统一序列化为：

```python
{
    "__type__": "ndarray",
    "dtype": "float64",
    "data": [...]
}
```

#### `datetime` / 时间戳

统一使用稳定格式返回。建议：

1. 优先保留原始数值时间戳
2. 仅在本地对象为 `datetime` 时转成 ISO 字符串或毫秒时间戳

#### NaN / 无穷值

应在协议中明确处理方式，避免 JSON 场景下出现不兼容值。建议保持 `pyobj` 兼容前提下，尽量保留数值语义；如需跨语言客户端支持，可再补一个严格 JSON 模式。

### 镜像层与兼容层的区别

- vn.py 兼容层继续返回项目当前习惯的数据对象
- xtdata 镜像层始终返回可逆序列化后的结构

---

## 版本兼容与错误处理

### 启动探测

启动时通过 `dir(xtdata)` 或 `getattr` 探测本地可用函数。

### 缺失函数处理

对于文档中存在、但当前本地版本缺失的函数，例如：

- `call_formula_batch`
- `get_trading_time`

镜像层应提供一致错误：

- 错误类型：`NotImplementedError` 或项目自定义异常
- 错误信息包含：
  - 函数名
  - 当前 `xtquant` 版本不支持
  - 建议升级客户端库

### 参数错误

若调用参数与本地签名不匹配，应原样暴露参数错误语义，避免误导调用方。

### 运行时错误

若 `xtdata` 因权限、连接、行情终端状态、数据缺失等原因失败，应记录日志并把错误返回给 RPC 调用方。

---

## 接口覆盖范围

### 第一批必须覆盖

#### 行情订阅与获取

- `subscribe_quote`
- `subscribe_whole_quote`
- `unsubscribe_quote`
- `run`
- `get_market_data`
- `get_market_data_ex`
- `get_local_data`
- `get_full_tick`
- `get_full_kline`
- `get_divid_factors`

#### 历史下载

- `download_history_data`
- `download_history_data2`
- `download_history_contracts`

#### 日历与基础辅助

- `get_holidays`
- `get_trading_calendar`
- `get_trading_dates`
- `get_period_list`
- `download_holiday_data`
- `reconnect`

#### 板块与基础信息

- `get_instrument_detail`
- `get_instrument_type`
- `get_sector_list`
- `get_stock_list_in_sector`
- `download_sector_data`
- `create_sector_folder`
- `create_sector`
- `add_sector`
- `remove_stock_from_sector`
- `remove_sector`
- `reset_sector`
- `get_index_weight`
- `download_index_weight`

#### 可转债 / ETF / IPO

- `download_cb_data`
- `get_cb_info`
- `get_ipo_info`
- `download_etf_info`
- `get_etf_info`

#### 财务

- `get_financial_data`
- `download_financial_data`
- `download_financial_data2`

#### 模型

- `subscribe_formula`
- `unsubscribe_formula`
- `call_formula`
- `generate_index_data`

#### Level2 / 扩展

- `get_l2_quote`
- `get_l2_order`
- `get_l2_transaction`
- `get_l2thousand_queue`
- `subscribe_l2thousand`
- `subscribe_l2thousand_queue`
- `get_option_detail_data`

### 第二批按本地版本探测挂载

包括文档有描述但本地版本不一定存在的能力，统一走注册表探测机制。

---

## 数据流

### 一次性请求

```text
Client
  -> REQ/REP
  -> RpcRequestHandler
  -> XtdataMirrorExecutor
  -> xtdata.func(...)
  -> Serialization
  -> Client
```

### 订阅请求

```text
Client
  -> REQ/REP
  -> xtdata.subscribe_*
  -> return seq

xtdata callback
  -> internal wrapper
  -> Serialization
  -> EventPublisher
  -> PUB/SUB
  -> Client
```

---

## 测试策略

本次改动必须采用测试先行的方式推进，至少包含以下测试层次。

### 1. 注册表测试

验证：

1. 镜像注册表中的函数名、RPC 名和真实 `xtdata` 函数绑定正确。
2. 缺失函数被正确标记为不可用。

### 2. 参数透传测试

重点验证：

1. `period` 原样透传
2. `dividend_type` 原样透传
3. `download_* / get_* / subscribe_*` 参数顺序和默认值符合本地签名

### 3. 序列化测试

验证：

1. `DataFrame` 能稳定序列化
2. `ndarray` 能稳定序列化
3. 嵌套 `dict[list[DataFrame]]` 之类复杂结构也能处理

### 4. 订阅桥测试

验证：

1. 订阅接口返回订阅号
2. 回调数据被正确转发到指定 topic
3. 推送 payload 结构符合约定

### 5. vn.py 回归测试

必须新增针对 `query_history` 的回归用例：

1. 未知周期不再默认变 `1d`
2. `Interval.MINUTE` 正常映射
3. 非法周期明确报错

### 6. 集成测试

使用 fake `xtdata` 模块验证：

1. 镜像 RPC 被正确注册
2. 镜像调用能到达 fake `xtdata`
3. 返回值能正确回到 RPC 调用方

---

## 风险与约束

### 1. 本地 `xtquant` 版本差异

官方文档并不保证与当前环境完全一致，镜像层必须以“本地实际可调用签名”为准。

### 2. pandas / numpy 返回值体积

`get_market_data_ex`、财务接口等返回值可能很大，应避免无边界请求导致 ZeroMQ 响应体过大。

### 3. 订阅推送压力

高频订阅、全推行情、L2 千档等场景会对事件队列和序列化开销提出更高要求，需要沿用现有异步发布模型。

### 4. 客户端恢复逻辑

镜像层如果采用可逆序列化，客户端需要提供反序列化适配器才能恢复为 pandas/numpy 对象。

---

## 实施顺序建议

1. 先修复 `query_history` 的未知周期降级问题，并加回归测试。
2. 新增镜像注册表与执行器骨架。
3. 打通第一批最核心镜像接口：
   - `get_market_data_ex`
   - `download_history_data`
   - `subscribe_quote`
   - `unsubscribe_quote`
   - `get_period_list`
4. 补充序列化层。
5. 扩展到板块、财务、模型、Level2。
6. 更新 README 与示例脚本。

---

## 验收标准

满足以下条件即可视为本设计落地成功：

1. 现有 vn.py 客户端无需修改即可继续调用现有 RPC。
2. 新客户端可以按 `xtdata` 原生函数名和参数远程调用镜像 RPC。
3. `period` 与 `dividend_type` 不再被兼容层错误篡改。
4. `query_history` 不再把未知周期静默降级成 `1d`。
5. 订阅类镜像接口可返回订阅号，并通过 PUB/SUB 推送回调数据。
6. 对本地版本缺失函数给出明确错误。
7. 自动化测试覆盖注册、透传、序列化、订阅桥和兼容层回归。
