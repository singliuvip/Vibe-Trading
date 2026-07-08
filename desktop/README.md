# Vibe-Trading Desktop

基于 [Tauri 2](https://v2.tauri.app/) 的跨平台桌面客户端。

## 前置条件

| 依赖 | 版本 | 说明 |
|------|------|------|
| Rust | stable | [rustup](https://rustup.rs/) 安装 |
| Node.js | ≥ 20 | [nvm](https://github.com/nvm-sh/nvm) 或官方安装 |
| npm | ≥ 9 | 随 Node.js 分发 |
| Linux 系统库 | — | 见下方各平台说明 |

### Linux 系统依赖

```bash
sudo apt-get install -y \
  libwebkit2gtk-4.1-dev \
  build-essential \
  curl \
  wget \
  file \
  libxdo-dev \
  libssl-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev
```

### macOS

需要 Xcode Command Line Tools：

```bash
xcode-select --install
```

### Windows

需要 [Microsoft Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)。

## 开发

```bash
# 1. 安装前端依赖
cd frontend
npm ci

# 2. 启动 Tauri 开发模式（热重载）
cd ../desktop/src-tauri
cargo tauri dev
```

## 构建

```bash
cd desktop/src-tauri

# 本地平台构建
cargo tauri build

# 指定目标平台（需安装对应 Rust target）
cargo tauri build --target x86_64-unknown-linux-gnu
cargo tauri build --target x86_64-pc-windows-msvc
cargo tauri build --target aarch64-apple-darwin
```

构建产物路径（相对于 `desktop/src-tauri/target/<target>/release/bundle/`）：

| 平台 | 格式 |
|------|------|
| Linux | `.deb` / `.rpm` / `.AppImage` |
| macOS | `.app` / `.dmg` |
| Windows | `.exe` (NSIS) / `.msi` |

## CI/CD

推送至 `main` 分支或创建 PR 时，GitHub Actions 自动构建所有平台。详见 `.github/workflows/build-desktop.yml`。

- **PR**：仅构建并上传 artifacts（保留 14 天）
- **main**：构建 → 创建 draft GitHub Release

## 代码签名（可选）

当前签名配置为空，用户如需签名请自行配置：

### Windows（Authenticode）

在 `tauri.conf.json` 中设置：
```json
"windows": {
  "certificateThumbprint": "<your-thumbprint>",
  "timestampUrl": "http://timestamp.digicert.com"
}
```

### macOS（Notarization）

需 Apple Developer 账户，在 `tauri.conf.json` 中设置：
```json
"macOS": {
  "signingIdentity": "Developer ID Application: <Your Name> (<Team ID>)",
  "provisioningProfile": "embedded.provisionprofile"
}
```

并通过环境变量提供凭据：
```bash
export APPLE_ID="your@email.com"
export APPLE_ID_PASSWORD="xxxx-xxxx-xxxx-xxxx"
export APPLE_TEAM_ID="XXXXXXXXXX"
```

## 图标

`src-tauri/icons/icon.svg` 为占位符向量图标。Tauri CLI 会自动从此 SVG 生成各平台所需尺寸（`.png`、`.ico`、`.icns`）。

替换步骤：
1. 替换 `icons/icon.svg`（推荐 1024×1024 以上）
2. 删除 `icons/` 下自动生成的文件
3. 重新运行 `cargo tauri build` 即可自动重新生成

---

## 版本更新清单

发版前请确认以下事项：

- [ ] `tauri.conf.json` 中 `version` 与 `frontend/package.json` 中 `version` 保持一致
- [ ] `tauri.conf.json` 中 `identifier` 正确（`com.vibetrading.desktop`）
- [ ] CI 构建通过（`.github/workflows/build-desktop.yml`）
- [ ] 各平台构建产物可正常启动
- [ ] Shell 权限范围仅限 `vibe-trading` 命令（`capabilities/default.json`）
- [ ] CSP 已启用（`"csp": "default-src 'self'"`，非 `null`）

---

## Tauri Updater 配置

Tauri 内置自动更新支持，通过以下步骤启用：

### 1. 生成签名密钥

```bash
cd desktop/src-tauri
cargo tauri signer generate --password <your-password>
```

私钥保存至 `~/.vibe-trading/tauri-updater-key`（**禁止提交仓库**），公钥写入 `tauri.conf.json`。

### 2. 配置 `tauri.conf.json`

```json
{
  "plugins": {
    "updater": {
      "pubkey": "<公钥>",
      "endpoints": [
        "https://updates.vibetrading.com/updates/{{target}}/{{arch}}/{{current_version}}"
      ],
      "windows": {
        "installMode": "passive"
      }
    }
  }
}
```

### 3. 发布更新

1. 构建新版本 desktop app（CI 自动完成）
2. 将构建产物上传至更新端点
3. 更新服务端 manifest（JSON 格式），包含新版本号、下载 URL 和签名

```json
{
  "version": "0.2.0",
  "notes": "新功能：支持自定义指标面板",
  "pub_date": "2026-07-07T00:00:00Z",
  "platforms": {
    "linux-x86_64": { "signature": "<签名>", "url": "https://..." },
    "darwin-aarch64": { "signature": "<签名>", "url": "https://..." },
    "windows-x86_64": { "signature": "<签名>", "url": "https://..." }
  }
}
```

> ⚠️ 签名私钥必须离线保管，不可纳入 CI secret 或仓库。更新端点应启用 HTTPS 并验证签名。
