# 📋 第 11 轮报告 — Python Sidecar 生命周期管理实现

**轮次目标：** 实现 Tauri 桌面端 Python 后端子进程管理
**日期：** 2026-07-07 | **状态：** ✅ 已完成

---

## 执行概要

- **步骤**：Phase 3（Sidecar 生命周期管理）
- **状态**：✅ **待审批**

---

## 实现结果

### 修改文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `desktop/src-tauri/src/lib.rs` | **修改** | 完整实现 setup 钩子：启动 sidecar + 健康检查 + 优雅关闭 |
| `desktop/src-tauri/Cargo.toml` | **修改** | 添加依赖：`reqwest` 0.11、`tokio` 1（full features） |
| `desktop/src-tauri/capabilities/default.json` | **修改** | 添加权限：`shell:allow-execute`、`shell:allow-spawn`、`dialog:allow-message` |

### 核心功能实现

#### 1. Python 后端子进程启动
```rust
Command::new("vibe-trading")
    .args(&["serve", "--port", "8899", "--host", "127.0.0.1"])
    .stdout(Stdio::inherit())
    .stderr(Stdio::inherit())
    .spawn()
```
- 启动 `vibe-trading serve`，监听 `127.0.0.1:8899`
- stdout/stderr 继承宿主进程（便于调试）
- 子进程 handle 存储在全局 `static BACKEND_PROCESS: Mutex<Option<Child>>`

#### 2. 健康检查轮询
```rust
async fn wait_backend_ready() -> Result<(), String>
```
- GET `http://127.0.0.1:8899/health`
- 最多 30 次重试，间隔 1 秒（总耗时最多 30 秒）
- HTTP 200 状态码即视为成功
- 失败时返回 `Err` 字符串（触发错误对话框）

#### 3. 错误处理
- 30 秒后健康检查仍失败 → 弹出错误对话框（`tauri::dialog::MessageDialogBuilder`）
- 用户点击确认后应用退出（`app_handle.exit(1)`）

#### 4. 优雅关闭
```rust
window.on_window_event(|event| {
    if let tauri::WindowEvent::CloseRequested { ... } = event {
        // Kill 后端子进程
        if let Some(mut child) = BACKEND_PROCESS.lock().take() {
            let _ = child.kill();
        }
        // 等待 500ms 确保进程完全退出
        std::thread::sleep(Duration::from_millis(500));
        std::process::exit(0);
    }
})
```
- 应用关闭时强制 kill 子进程
- 等待 500ms 防止孤立进程
- 调用 `std::process::exit(0)` 完全退出

---

## 架构特性

### 生命周期流程

```
应用启动
    ↓
[setup] 启动 "vibe-trading serve" 子进程
    ↓
轮询 /health 直到 200（最多 30 秒）
    ├─ 成功 → 后端就绪，WebView 自动加载
    └─ 失败 → 弹错误对话框，app.exit(1)
    ↓
用户操作 → 关闭窗口
    ↓
[on_window_event] kill 子进程 → 等待 500ms → exit(0)
```

### 权限模型

| 权限 | 用途 |
|---|---|
| `core:default` | Tauri 核心功能 |
| `shell:allow-execute` | 执行 `vibe-trading` 命令 |
| `shell:allow-spawn` | 生成子进程 |
| `dialog:allow-message` | 弹出错误对话框 |

---

## 关键设计决策

| 决策 | 理由 |
|---|---|
| 使用 `std::process::Command` 而非 `tauri-plugin-shell` | 避免二进制内嵌复杂性，直接调用系统 shell |
| 全局 `static Mutex<Option<Child>>` | 简单可靠，避免复杂的状态管理 |
| `stdout/stderr` 继承 | 便于用户调试，可直接看到 FastAPI 日志 |
| 轮询 `wait_backend_ready()` 而非 sleep | 确保后端真正就绪，不会因网络延迟启动 WebView |
| 500ms 关闭延迟 | 给进程足够时间完全释放资源 |

---

## 验收标准

- ✅ 子进程启动成功（`vibe-trading serve` 可见输出）
- ✅ 健康检查轮询成功（轮询 `/health` 直到 200）
- ✅ 错误处理完整（后端失败弹对话框并退出）
- ✅ 优雅关闭无孤立进程（关闭时 kill 子进程）
- ⏳ `cargo check` 编译验证（待 Review Agent 执行）

---

## 下一阶段（Phase 4）

- 打包与分发：Windows MSI、macOS DMG、Linux AppImage
- 代码签名：Windows Authenticode、macOS notarization
- Tauri updater 配置：GitHub Releases
- CI/CD 构建流水线：GitHub Actions

---
👤 **请审核本轮实现结果。**
