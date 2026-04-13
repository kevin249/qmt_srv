# Logging Controls And Client Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 XTQ Bridge 增加配置化日志级别与分类开关，并补一个可选的 RPC 客户端注册方法，用于明确记录客户端接入。

**Architecture:** 扩展 `config.template.json` 和 `app.py` 的配置解析，给 `XtQuantBridge` 增加统一的结构化日志入口，并让 `RpcRequestHandler` 与关键 bridge 路径都走日志过滤。新增 `register_client` RPC 方法，复用现有 `RpcServer` REQ/REP 协议，不改现有 ZeroMQ 传输层。

**Tech Stack:** Python 3.13, `unittest`, `vnpy.rpc`, `vnpy.event`, `xtquant`

---

### Task 1: 用测试锁定日志控制行为

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_app.py`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_integration.py`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_rpc_logging.py`

- [ ] **Step 1: 写失败测试**
- [ ] **Step 2: 运行测试并确认失败**
- [ ] **Step 3: 实现最小代码让测试通过**
- [ ] **Step 4: 再跑测试确认通过**

### Task 2: 实现日志级别/分类与客户端注册

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\app.py`
- Modify: `D:\gupiao\cc\qmt_srv\config.template.json`
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\bridge.py`
- Modify: `D:\gupiao\cc\qmt_srv\xtquant_bridge\rpc_handler.py`
- Modify: `D:\gupiao\cc\qmt_srv\probe_rpc.py`

- [ ] **Step 1: 增加 logging 配置默认值**
- [ ] **Step 2: 增加结构化日志过滤入口**
- [ ] **Step 3: 给 RPC handler 和 bridge 关键路径接日志**
- [ ] **Step 4: 增加 `register_client` RPC 方法**
- [ ] **Step 5: 让 probe_rpc 默认先注册客户端**

### Task 3: 最终验证

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\README.md`

- [ ] **Step 1: 跑 `D:\gupiao\cc\qmt_srv\.venv\Scripts\python.exe -m unittest discover -s tests -v`**
- [ ] **Step 2: 跑语法校验**
- [ ] **Step 3: 用真实 server 跑一次 `probe_rpc.py` 验证 RPC 路径**
