# qmt_srv

这个仓库现在只保留一个对外服务入口：`qmt_srv` 统一管理多个正在运行的 QMT 客户端策略导出目录，并对外提供兼容旧 REP/PUB 的固定接口。

旧 MiniQMT / xtquant 直连桥接实现已经移除；新的方案不在仓库进程里直连 QMT，而是在每个 QMT 客户端内运行 `qmt_data_export_bridge_strategy.py` 采集数据、读取命令文件，再由唯一的 `qmt_srv` 对外聚合服务。

## 架构边界

- 外部只启动一个 `qmt_srv`，默认 REP `tcp://*:20140`、PUB `tcp://*:20141`
- 多个 QMT 客户端可以同时运行同一个策略文件
- QMT 内策略默认不绑定 REP/PUB 端口，避免多开端口冲突
- qmt_srv 通过每个 QMT 的 `qmt_data_export/commands/inbox.jsonl` 下发订阅、账号等命令
- QMT 策略把 tick、bar、历史、合约、财务、账号、持仓、委托、成交等数据导出到本地目录
- qmt_srv 聚合所有 QMT 导出目录后，兼容旧的 `send_pyobj([method, args, kwargs]) -> [ok, payload]` 调用方式

## 配置

复制模板为本地配置：

```powershell
Copy-Item config.template.json config.user.json
```

多账号示例可以直接看 `config.multi_account.example.json`。如果要从示例开始改：

```powershell
Copy-Item config.multi_account.example.json config.user.json
```

在 `config.user.json` 里维护多个 QMT 目录和账号：

```json
{
  "rpc": {
    "rep_address": "tcp://*:20140",
    "pub_address": "tcp://*:20141"
  },
  "qmt_instances": [
    {
      "instance_id": "ctsec_01",
      "qmt_path": "D:\\迅投QMT交易终端财通证券版\\bin.x64",
      "stock_active": true,
      "futures_active": false,
      "option_active": false,
      "simulation": false,
      "account_type": "STOCK",
      "account_id": "",
      "session_id": 1,
      "connect_retries": 5,
      "connect_retry_interval": 1.0,
      "accounts": [
        { "account_id": "你的账号1", "account_type": "STOCK", "name": "main" },
        { "account_id": "你的账号2", "account_type": "STOCK", "name": "backup" }
      ]
    }
  ]
}
```

QMT 路径只写在 `qmt_instances[].qmt_path`。可以填 QMT 根目录、`bin.x64`、`userdata_mini` 或 `python` 目录，qmt_srv 和同步脚本会自动找到对应的 `python` 目录。

`instance_id` 是这条 QMT 实例的唯一别名，`qmt_path` 是这个别名绑定的实际 QMT 目录。多个 QMT 客户端目录就写多条 `qmt_instances`，每条都要有不同的 `instance_id` 和自己的 `qmt_path`；同一个 QMT 客户端里的多个账号不要再拆实例，放在该实例的 `accounts` 数组里。

旧 REP/PUB 客户端如果不传实例字段，命令会广播给所有 QMT；如果只想发给某一个 QMT，可以在旧的 `kwargs` 或第一个参数字典里传下面任一字段：

```python
sock.send_pyobj(["subscribe", [{"vt_symbol": "600460.SH"}], {"instance_id": "ctsec_main"}])
sock.send_pyobj(["subscribe", [{"vt_symbol": "600460.SH"}], {"qmt_path": "D:\\迅投QMT交易终端财通证券版\\bin.x64"}])
```

`qmt_path` 路由会自动兼容 QMT 根目录、`bin.x64`、`userdata_mini` 和 `python` 目录几种写法。

两个 QMT 同时管理时，第二个 QMT 继续加在 `qmt_instances` 数组里：

```json
{
  "snapshot_publish_seconds": 1.0,
  "rpc": {
    "rep_address": "tcp://*:20140",
    "pub_address": "tcp://*:20141"
  },
  "qmt_instances": [
    {
      "instance_id": "ctsec_main",
      "qmt_path": "D:\\迅投QMT交易终端财通证券版\\bin.x64",
      "stock_active": true,
      "futures_active": false,
      "option_active": false,
      "simulation": false,
      "account_type": "STOCK",
      "account_id": "",
      "session_id": 1,
      "connect_retries": 5,
      "connect_retry_interval": 1.0,
      "accounts": [
        { "account_id": "10000001", "account_type": "STOCK", "name": "main" },
        { "account_id": "10000002", "account_type": "STOCK", "name": "backup" }
      ]
    },
    {
      "instance_id": "ctsec_second",
      "qmt_path": "D:\\第二个QMT交易终端\\bin.x64",
      "stock_active": true,
      "futures_active": false,
      "option_active": false,
      "simulation": false,
      "account_type": "STOCK",
      "account_id": "",
      "session_id": 2,
      "connect_retries": 5,
      "connect_retry_interval": 1.0,
      "accounts": [
        { "account_id": "20000001", "account_type": "STOCK", "name": "second-main" }
      ]
    }
  ]
}
```

## 旧参数兼容

`config.template.json` 里保留了旧版配置段：

- `rpc.trade_workers` / `fast_workers` / `slow_workers` 等队列参数
- `csv_data_source`
- `data_download`
- `logging.categories`

旧服务入口仍由 `qmt_srv` 对外提供，REP/PUB 地址和旧 RPC 方法继续保留。当前只移除了 qmt_srv 进程内的 xtdata/xtquant 直连下载能力；QMT 客户端内策略负责采集并导出实时数据，qmt_srv 继续聚合导出文件并读取旧 CSV 存储目录。

参数状态：

- `rpc.rep_address` / `rpc.pub_address`：仍生效，是唯一对外 REP/PUB 端口
- `rpc.trade_workers` / `fast_workers` / `slow_workers`：配置保留；当前聚合服务没有恢复旧 `ConcurrentRpcServer` 多队列执行模型
- `csv_data_source.path` / `default_adjust`：仍生效，作为 `query_history` 和 `xtdata.get_market_data_ex` 的历史 CSV 回退来源
- `data_download.daily_1min_download` / `boot_data_download` / `batch_size` / `stock_sectors`：配置保留；这些原本依赖 xtdata 直连下载，当前不再由 qmt_srv 执行
- `data_download.tick_history_enabled`：配置保留；tick 历史直连下载已随 xtdata 去除，实时 tick 由 QMT 策略导出
- `logging.enabled` / `level` / `categories`：配置保留；当前只保留基础控制台输出和 PUB 快照，旧分类日志事件尚未完全恢复
- `qmt_instances[].account_id` / `account_type`：仍用于实例账号配置；多账号优先使用 `accounts`
- `qmt_instances[].stock_active` / `futures_active` / `option_active` / `simulation` / `session_id` / `connect_retries`：配置保留在实例上；这些原本控制 xtquant 交易连接，当前不再由 qmt_srv 直连使用

如果旧配置里还没有 `qmt_instances`，需要把原来的 QMT 连接字段迁移到 `qmt_instances[]` 中。

## 同步策略到 QMT

```powershell
.\sync_qmt_strategy.ps1 -ConfigPath .\config.user.json
```

脚本会把 GBK 编码的 `qmt_data_export_bridge_strategy.py` 复制到每个 QMT `python` 目录，并生成该 QMT 专用的 `qmt_data_export_bridge_config.json`。这个配置只包含实例名、账号和命令文件设置，不会给每个 QMT 单独设置对外端口。

如果 QMT 日志里出现 `instance=bin.x64` 或导出目录落在 `bin.x64\qmt_data_export`，说明 QMT 正在运行旧策略或没有读到同步脚本生成的运行配置。重新执行上面的同步脚本，并在 QMT 里重新加载策略。新版策略即使从 `bin.x64` 启动，也会优先把数据导出到 QMT 根目录下的 `python\qmt_data_export`，和 qmt_srv 读取目录保持一致。

## 启动

先在每个 QMT 客户端里运行策略 `qmt_data_export_bridge_strategy.py`，再启动唯一的服务：

```powershell
python app.py --config .\config.user.json
```

旧客户端继续连同一组地址即可：

- REP: `tcp://127.0.0.1:20140`
- PUB: `tcp://127.0.0.1:20141`

### Python 启动错误排查

如果启动时出现下面错误，不要执行 `uv pip install encodings`：

```text
Fatal Python error: Failed to import encodings module
ModuleNotFoundError: No module named 'encodings'
```

`encodings` 是 Python 标准库，不是 pip 包。这个错误一般表示当前命令行拿到的 Python 运行时坏了，或者 `PYTHONHOME` / `PYTHONPATH` 指到了 QMT、MiniQMT 或其他不完整 Python 目录。

优先用仓库里的虚拟环境启动：

```powershell
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -c "import encodings; import sys; print(sys.executable)"
.\.venv\Scripts\python.exe app.py --config .\config.user.json
```

如果 `.venv` 本身也报同样错误，重建虚拟环境：

```powershell
deactivate
Remove-Item -Recurse -Force .\.venv
uv venv --python 3.13 .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

新版 `start.bat` 会自动做这个重建步骤。注意 `uv run app.py` 会复用当前坏掉的 `.venv`，不会自动修复；如果要继续用 `uv run`，先删除并重建 `.venv`。

## 旧接口兼容

已支持的常用方法包括：

- `register_client`
- `subscribe`
- `set_account`
- `query_history`
- `get_tick` / `get_l1_tick` / `get_all_ticks`
- `get_account` / `get_position` / `get_order` / `get_trade`
- `get_all_accounts` / `get_all_positions` / `get_all_orders` / `get_all_trades`
- `get_contract` / `get_all_contracts`
- `xtdata.get_full_tick`
- `xtdata.get_market_data` / `xtdata.get_market_data_ex` / `xtdata.get_local_data`
- `xtdata.get_instrument_detail`
- `xtdata.get_financial_data`
- `xtdata.get_trading_calendar`
- `xtdata.get_stock_list_in_sector`
- `xtdata.subscribe_quote` / `xtdata.subscribe_whole_quote`
- `xtdata.download_history_data` / `xtdata.download_history_data2`：兼容旧调用并返回成功；qmt_srv 不再执行 xtdata 直连下载，历史读取继续走 `csv_data_source.path` 和 QMT 策略导出

交易下单接口仍然禁用：`send_order` 和 `cancel_order` 会返回只读错误，避免误连真实账户。
