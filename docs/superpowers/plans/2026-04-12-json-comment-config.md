# JSON Comment Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `config.user.json` 和 `config.template.json` 支持注释，同时保持现有 JSON 配置结构不变。

**Architecture:** 只修改配置读取层，在 `load_config()` 前增加去注释预处理函数，再交给 `json.loads()` 解析。注释支持 `//`、`/* ... */` 以及整行 `#`，并保证字符串里的 `http://`、Windows 路径等内容不被误删。

**Tech Stack:** Python 3.13, `json`, `unittest`

---

### Task 1: 用测试锁定注释支持

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\tests\test_app.py`
- Test: `D:\gupiao\cc\qmt_srv\tests\test_app.py`

- [ ] **Step 1: 写失败测试**
- [ ] **Step 2: 运行测试并确认失败**
- [ ] **Step 3: 实现最小代码让测试通过**
- [ ] **Step 4: 再次运行测试确认通过**

### Task 2: 修改配置读取逻辑

**Files:**
- Modify: `D:\gupiao\cc\qmt_srv\app.py`
- Test: `D:\gupiao\cc\qmt_srv\tests\test_app.py`

- [ ] **Step 1: 增加去注释预处理函数**
- [ ] **Step 2: 接入 `load_config()`**
- [ ] **Step 3: 保持现有主链路不变**
- [ ] **Step 4: 跑回归测试和语法校验**
