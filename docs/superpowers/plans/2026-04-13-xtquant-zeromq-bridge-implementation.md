# Xtquant ZeroMQ Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-04-12-xtquant-zeromq-bridge-design.md` 实现一个直连 `xtquant` 的 ZeroMQ bridge，同时保持现有 `vnpy_rpcservice` 客户端可直接连接。

**Architecture:** 保留 `vnpy.rpc.RpcServer` 的协议层来确保 REQ/REP + PUB/SUB 兼容，但移除 `MainEngine/EventEngine/BaseGateway/RpcServiceApp` 依赖。新增 `xtquant_bridge` 模块负责 xtquant 生命周期、回调路由、数据翻译、事件发布和 RPC 命令处理；`app.py` 只负责配置加载和桥接服务启动。

**Tech Stack:** Python 3.13, `xtquant`, `vnpy.rpc`, `vnpy.trader.object`, `vnpy.event.Event`, `zmq`, `unittest`

---

### Task 1: 锁定协议与翻译层测试

**Files:**
- Create: `D:\gupiao\cc\qmt_srv\tests\test_utils.py`
- Create: `D:\gupiao\cc\qmt_srv\tests\test_translator.py`
- Create: `D:\gupiao\cc\qmt_srv\tests\test_event_publisher.py`
- Create: `D:\gupiao\cc\qmt_srv\tests\test_rpc_handler.py`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_app.py`

- [ ] **Step 1: 先写失败测试**
- [ ] **Step 2: 运行测试并确认失败**
- [ ] **Step 3: 实现最小翻译和协议代码**
- [ ] **Step 4: 再跑测试确认通过**

### Task 2: 实现 xtquant_bridge 核心模块

**Files:**
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\__init__.py`
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\utils.py`
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\translator.py`
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\event_publisher.py`
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\callback_router.py`
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\rpc_handler.py`
- Create: `D:\gupiao\cc\qmt_srv\xtquant_bridge\bridge.py`

- [ ] **Step 1: 实现工具和符号映射**
- [ ] **Step 2: 实现 xtquant -> vnpy 数据翻译**
- [ ] **Step 3: 实现事件发布与状态缓存**
- [ ] **Step 4: 实现 RPC 命令处理**
- [ ] **Step 5: 实现桥接主类与生命周期管理**

### Task 3: 替换入口并接入配置

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\app.py`
- Modify: `D:\gupiao\cc\qmt_srv\config.template.json`
- Modify: `D:\gupiao\cc\qmt_srv\config.user.json`

- [ ] **Step 1: 重写 app.py，仅保留配置、启动和关闭逻辑**
- [ ] **Step 2: 扩展配置字段，兼容 spec 要求的 session_id/event_queue_size**
- [ ] **Step 3: 确保现有注释配置支持仍然有效**

### Task 4: 验证与最小联调

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\tests\*.py`

- [ ] **Step 1: 运行 `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest discover -s tests -v`**
- [ ] **Step 2: 运行 `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m py_compile app.py xtquant_bridge\\*.py tests\\*.py`**
- [ ] **Step 3: 做最小 smoke check，验证 bridge 能读取配置并实例化**
