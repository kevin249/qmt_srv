# MiniQMT JSON Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MiniQMT RPC server 的配置来源从环境变量切换为项目根目录固定的 `config.json`。

**Architecture:** 保持现有 RPC server 启动链路不变，只替换配置加载层。`app.py` 负责读取并校验 `config.json`，再把 JSON 字段映射成 `vnpy_xt.XtGateway` 所需配置和 RPC 地址；测试覆盖 JSON 解析、字段映射与启动编排。

**Tech Stack:** Python 3.13, `unittest`, `json`, `vnpy`, `vnpy_xt`, `vnpy_rpcservice`

---

### Task 1: 用测试锁定 JSON 配置行为

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_app.py`
- Test: `D:\gupiao\cc\qmt_srv\tests\test_app.py`

- [ ] **Step 1: 写失败测试**

为 `config.json` 解析、XT 配置映射、RPC 地址读取补测试，断言不再依赖环境变量。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m unittest tests.test_app -v`
Expected: FAIL，提示旧接口仍读取环境变量或缺少新的 JSON 配置函数。

- [ ] **Step 3: 实现最小代码让测试通过**

在 `app.py` 中新增 JSON 读取与字段映射逻辑，删除环境变量入口。

- [ ] **Step 4: 再次运行测试确认通过**

Run: `python -m unittest tests.test_app -v`
Expected: PASS

### Task 2: 切换服务端入口到固定 config.json

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\app.py`
- Create: `D:\gupiao\cc\qmt_srv\config.json`
- Test: `D:\gupiao\cc\qmt_srv\tests\test_app.py`

- [ ] **Step 1: 写失败测试**

增加缺少 `config.json`、缺少 `qmt_path` 或 `account_id` 时的失败用例。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m unittest tests.test_app -v`
Expected: FAIL，报缺少配置文件或缺少 JSON 字段。

- [ ] **Step 3: 实现最小代码让测试通过**

在 `app.py` 中固定读取项目根目录 `config.json`，补校验和启动摘要；提供仓库内默认 `config.json` 模板。

- [ ] **Step 4: 再次运行测试确认通过**

Run: `python -m unittest tests.test_app -v`
Expected: PASS

### Task 3: 做最终校验

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\app.py`
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_app.py`
- Modify: `D:\gupiao\cc\qmt_srv\config.json`

- [ ] **Step 1: 运行语法校验**

Run: `python -m py_compile app.py tests\test_app.py`
Expected: PASS

- [ ] **Step 2: 运行配置级 smoke check**

Run: `python -c "import app; print(app.load_config()); print(app.build_xt_setting(app.load_config())); print(app.build_rpc_setting(app.load_config()))"`
Expected: 输出 JSON 配置、XT 映射结果与 RPC 地址

