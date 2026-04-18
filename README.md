# qmt_srv

基于 `xtquant` 的 MiniQMT ZeroMQ Bridge。

这个项目直接连接 MiniQMT / xtquant，本地读取账户、持仓、订单、成交、行情数据，并通过 `vnpy.rpc` 兼容的 REQ/REP + PUB/SUB 协议对外提供服务，方便已有的 vn.py / ZeroMQ 客户端接入，而不再依赖 `vnpy_xt` 网关。

## 项目目标

这个项目主要解决两类问题：

1. 直接绕过 `vnpy_xt` 的兼容性问题，改为原生使用 `xtquant`
2. 保持 `vnpy.rpc.RpcServer` 风格的 ZeroMQ 协议，方便已有客户端无缝接入
3. 为迅投miniqmt提供rpc服务，可以使用veighna station进行连接。

当前实现已经实测打通：

- MiniQMT 本地行情连接
- xtquant 交易连接
- 账户查询
- 持仓查询
- 订单、成交、合约缓存
- ZeroMQ RPC 查询
- 最小 RPC 探针验证

## 适用场景

适合以下场景：

- 已经在本机安装 MiniQMT，希望通过 Python 自动化读取账户和持仓
- 需要一个稳定的本地 ZeroMQ 桥接服务，供策略进程或其他客户端连接
- 需要继续复用 `vnpy.rpc` 的 REQ/REP + PUB/SUB 协议，而不想继续依赖 `vnpy_xt`

不适合以下场景：

- 没有安装 MiniQMT / xtquant 的纯离线环境
- 需要 HTTP REST API，而不是 ZeroMQ
- 需要跨机器远程穿透、权限管理、多租户隔离等完整服务端能力

## 当前能力

当前 bridge 提供的核心 RPC 方法包括：

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

当前同时支持两套 RPC 风格：

### 1. vn.py 兼容接口

保留现有的 `subscribe`、`send_order`、`cancel_order`、`query_history`、`get_*`、`get_all_*` 调用方式，方便原有客户端继续接入。

说明：

- `query_history` 只接受 bridge 明确支持的 vn.py 周期映射
- 不再把未知周期静默降级成 `1d`
- 如果调用方传入兼容层不支持的周期，会直接返回错误

### 2. `xtdata` 原生镜像接口

新增一套按 `xtquant.xtdata` 原生函数名暴露的 RPC 方法，函数名形如：

- `xtdata.get_market_data`
- `xtdata.get_market_data_ex`
- `xtdata.get_local_data`
- `xtdata.get_full_tick`
- `xtdata.get_full_kline`
- `xtdata.download_history_data`
- `xtdata.download_history_data2`
- `xtdata.subscribe_quote`
- `xtdata.subscribe_whole_quote`
- `xtdata.unsubscribe_quote`
- `xtdata.get_period_list`
- `xtdata.get_trading_calendar`
- `xtdata.get_instrument_detail`
- `xtdata.get_sector_list`
- `xtdata.get_financial_data`
- `xtdata.subscribe_formula`
- `xtdata.call_formula`
- `xtdata.generate_index_data`
- `xtdata.get_l2_quote`
- `xtdata.subscribe_l2thousand`
- `xtdata.reconnect`

镜像层规则：

- `period` 按 `xtdata` 原生字符串直接透传，例如 `tick`、`1m`、`5m`、`1d`、`1w`、`1mon`
- `dividend_type` 按 `xtdata` 原生参数直接透传，支持 `none`、`front`、`back`、`front_ratio`、`back_ratio`
- 文档中存在但当前本地 `xtquant` 版本缺失的函数会返回明确错误，而不是静默失败

订阅类镜像接口说明：

- REQ/REP 返回订阅号
- 实时回调数据通过 PUB/SUB 推送
- topic 使用 `xtdata.subscribe_quote`、`xtdata.subscribe_whole_quote`、`xtdata.subscribe_formula` 等镜像名称

当前已验证的真实链路：

- 本地 `xtdata.connect()` 行情连接成功
- `XtQuantTrader` 连接成功
- 账户 `12231234` 查询成功
- 持仓查询成功
- `probe_rpc.py` 可通过 RPC 拿到账户和持仓

## 环境要求

- Windows
- Python 3.13
- 已安装并可正常登录的 MiniQMT
- 本机可正常使用 `xtquant`

建议使用项目自己的虚拟环境，不要混用别的仓库 `.venv`。

## 安装

### 1. 创建虚拟环境

```powershell
uv venv
```

### 2. 安装依赖

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果你的环境里 `xtquant` 不在 `requirements.txt` 覆盖范围内，需要按券商 / MiniQMT 提供的方式额外安装。

## 配置

项目使用两份配置文件：

- `config.template.json`：模板文件，提交到 Git
- `config.user.json`：本地实际配置，不提交到 Git

程序默认读取 `config.user.json`。

### 初始化配置

从模板复制一份用户配置：

```powershell
Copy-Item config.template.json config.user.json
```

### 配置示例

```jsonc
{
  "xt": {
    "token": "",
    "stock_active": true,
    "futures_active": false,
    "option_active": false,
    "simulation": true,
    "account_type": "STOCK",
    "qmt_path": "D:\\迅投QMT交易终端财通证券版\\bin.x64",
    "account_id": "35213367",
    "session_id": 0,
    "callback_thread_pool_size": 4,
    "event_queue_size": 10000
  },
  "rpc": {
    "rep_address": "tcp://*:20140",
    "pub_address": "tcp://*:20141"
  }
}
```

### 配置字段说明

#### `xt.token`

可选。

当前本地 MiniQMT 模式下通常留空。bridge 当前使用本地 `xtdata.connect()`，不是远端 `xt user mode` 行情模式。

#### `xt.stock_active`

是否启用股票行情订阅。

#### `xt.futures_active`

是否启用期货相关合约能力。

#### `xt.option_active`

是否启用期权相关合约能力。

#### `xt.simulation`

是否处于模拟模式，仅用于配置展示和上层区分；具体能否交易取决于 MiniQMT 本地会话。

#### `xt.account_type`

账户类型，默认 `STOCK`。

#### `xt.qmt_path`

MiniQMT 安装路径或相关路径。

程序会自动归一化以下几种写法：

- `...\XtMiniQmt.exe`
- `...\bin.x64`
- `...\userdata`
- `...\userdata_mini`

当前这套实现会自动解析到真正可用的交易路径。例如某些安装中，配置填的是 `...\bin.x64`，程序最终会连接到同级的 `...\userdata_mini`。

#### `xt.account_id`

交易账户资金账号。

#### `xt.session_id`

可选。

传给 `XtQuantTrader` 的 session id；为 `0` 时由 xtquant / 运行时自行处理。

#### `xt.callback_thread_pool_size`

预留字段，当前实现中未重点使用。

#### `xt.event_queue_size`

本地事件发布队列大小。用于 ZeroMQ PUB 侧缓冲。

#### `rpc.rep_address`

REQ/REP 地址。默认：

```text
tcp://*:20140
```

#### `rpc.pub_address`

PUB/SUB 地址。默认：

```text
tcp://*:20141
```

### 配置支持注释

`config.user.json` 和 `config.template.json` 支持以下注释风格：

- `// 注释`
- `/* 注释块 */`
- `# 注释`

但它本质上仍然是 JSON with comments，不支持尾逗号之类 YAML/TOML 语法。

## 启动服务

建议使用项目自己的 `.venv`：

```powershell
.\.venv\Scripts\python.exe app.py
```

或者在已经激活虚拟环境后：

```powershell
uv run app.py
```

正常启动后，你会看到类似输出：

```text
[XTQ Bridge] server started
[XTQ Bridge] REP: tcp://*:20140
[XTQ Bridge] PUB: tcp://*:20141
[XTQ Bridge] QMT root: D:\迅投QMT交易终端财通证券版\bin.x64
[XTQ Bridge] Account: 12231234
[XTQ Bridge] Account type: STOCK
[XTQ Bridge] Simulation: True
```

## 最小 RPC 验证

项目自带一个最小探针 [probe_rpc.py](D:/gupiao/cc/qmt_srv/probe_rpc.py)，用于验证 RPC 侧是否真的拿到账户和持仓。

在 bridge 已启动后执行：

```powershell
.\.venv\Scripts\python.exe probe_rpc.py
```

如果链路正常，你会看到类似输出：

```text
accounts=1
positions=5
```

并打印出账户信息和持仓明细。

## 项目结构

```text
qmt_srv/
├─ app.py
├─ config.template.json
├─ config.user.json
├─ probe_rpc.py
├─ requirements.txt
├─ xtquant_bridge/
│  ├─ __init__.py
│  ├─ bridge.py
│  ├─ callback_router.py
│  ├─ event_publisher.py
│  ├─ rpc_handler.py
│  ├─ translator.py
│  └─ utils.py
├─ tests/
│  ├─ test_app.py
│  ├─ test_event_publisher.py
│  ├─ test_integration.py
│  ├─ test_rpc_handler.py
│  ├─ test_translator.py
│  └─ test_utils.py
└─ docs/
   └─ superpowers/
      ├─ specs/
      └─ plans/
```

### 关键模块说明

#### `app.py`

入口文件，负责：

- 读取配置
- 支持注释配置
- 规范化 `qmt_path`
- 构建 bridge 配置
- 启动和关闭 `XtQuantBridge`

#### `xtquant_bridge/bridge.py`

bridge 主类，负责：

- `xtdata` 行情连接
- `XtQuantTrader` 交易连接
- ZeroMQ `RpcServer` 注册
- 内存缓存账户、持仓、订单、成交、合约、tick
- 发布 `Event` 给 RPC 客户端

#### `xtquant_bridge/translator.py`

负责：

- xtquant 数据结构转 vnpy 数据模型
- 订单、成交、持仓、账户、tick、bar 翻译
- `OrderRequest` / `CancelRequest` 到 xtquant 参数映射

#### `xtquant_bridge/callback_router.py`

负责处理 xtquant 回调，并转发到 bridge：

- 订单回报
- 成交回报
- 持仓回报
- 账户回报
- 行情回调

#### `xtquant_bridge/event_publisher.py`

负责：

- ZeroMQ PUB 事件队列
- 发布线程
- 基本背压处理

#### `xtquant_bridge/rpc_handler.py`

负责将 RPC 方法映射到 bridge：

- 查询缓存
- 订阅
- 下单
- 撤单
- 历史数据

#### `xtquant_bridge/utils.py`

负责：

- 路径归一化
- `userdata` / `userdata_mini` 解析
- 符号映射
- 时间格式转换

## 测试

运行全量测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

语法校验：

```powershell
.\.venv\Scripts\python.exe -c "import py_compile, pathlib; [py_compile.compile(str(p), doraise=True) for p in [pathlib.Path('app.py'), pathlib.Path('probe_rpc.py'), *pathlib.Path('xtquant_bridge').glob('*.py'), *pathlib.Path('tests').glob('*.py')]]; print('ok')"
```

## 常见问题

### 1. `xt user mode` / 市场权限错误

如果看到类似：

```text
未获取到市场权限
server only support xt user mode
```

说明你走到了远端权限模式。

当前实现已经改为本地 `xtdata.connect()` 模式，不再依赖 `xtdatacenter.init()` 的远端市场鉴权。如果你还看到这类错误，请确认你运行的是项目最新代码和当前仓库自己的 `.venv`。

### 2. `xttrader connect failed: -1`

这通常不是账户号本身错，而是交易路径落错了。

在部分 MiniQMT 安装里：

- 行情服务监听在 `bin.x64`
- 交易真正可用的数据目录在安装根目录同级的 `userdata_mini`

当前程序已经自动处理这个差异。

### 3. 服务启动了，但拿不到持仓

先确认：

1. MiniQMT 客户端已经手工登录成功
2. 本机运行的就是本项目 `.venv`
3. `probe_rpc.py` 能拿到账户和持仓

如果：

- `connect_result = -1`
- `account_infos = []`
- `account_status = []`

那说明交易会话本身还没 ready，不是 bridge 逻辑问题。

### 4. Windows 路径编码显示乱码

终端编码不同可能导致中文路径显示乱码，但只要程序实际能连接成功，通常不影响功能。

### 5. 配置里能不能写注释

可以。

当前支持 `//`、`/* ... */` 和 `#` 注释。

## 对外发布建议

如果你要把这个仓库对外开源，建议同时补充：

- `LICENSE`
- 更完整的 RPC 客户端示例
- 风险提示
- 不同券商 / MiniQMT 安装差异说明

## 风险提示

这是交易相关桥接工具。

- 任何下单、撤单、账户读取操作都可能影响真实账户
- 使用前请先在模拟环境或低风险环境自行验证
- 请自行确认券商、MiniQMT、xtquant 与本机 Python 环境兼容
- 作者不对由此产生的交易损失负责
