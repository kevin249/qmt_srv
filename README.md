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

在 `config.user.json` 里维护多个 QMT 目录和账号：

```json
{
  "rep_address": "tcp://*:20140",
  "pub_address": "tcp://*:20141",
  "qmt_instances": [
    {
      "instance_id": "ctsec_01",
      "python_dir": "D:\\迅投QMT交易终端财通证券版\\python",
      "accounts": [
        { "account_id": "你的账号1", "account_type": "STOCK", "name": "main" },
        { "account_id": "你的账号2", "account_type": "STOCK", "name": "backup" }
      ]
    }
  ]
}
```

## 同步策略到 QMT

```powershell
.\sync_qmt_strategy.ps1 -ConfigPath .\config.user.json
```

脚本会把 GBK 编码的 `qmt_data_export_bridge_strategy.py` 复制到每个 QMT `python` 目录，并生成该 QMT 专用的 `qmt_data_export_bridge_config.json`。这个配置只包含实例名、账号和命令文件设置，不会给每个 QMT 单独设置对外端口。

## 启动

先在每个 QMT 客户端里运行策略 `qmt_data_export_bridge_strategy.py`，再启动唯一的服务：

```powershell
python app.py --config .\config.user.json
```

旧客户端继续连同一组地址即可：

- REP: `tcp://127.0.0.1:20140`
- PUB: `tcp://127.0.0.1:20141`

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

交易下单接口仍然禁用：`send_order` 和 `cancel_order` 会返回只读错误，避免误连真实账户。
