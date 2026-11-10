# Windows / macOS 同仓库维护

## 文件职责

| 层 | 位置 | 约定 |
| --- | --- | --- |
| 共用业务 | `app/`、`frontend/`、两个配置 JSON | 功能和规则只实现一次 |
| Wind SDK 路径配置 | `scripts/configure_wind.py` | 安装时注册官方 SDK；不加载原生库，不验证权限 |
| Wind 调用与进程 | `app/wind_worker.py`、`app/service.py` | 共用调用逻辑；新增平台差异时集中处理并分别验证 |
| Windows 入口 | `.ps1`、`.cmd` | 保留原有后台启停方式 |
| Mac 入口 | `.command` | 本机前台运行，Control+C 停止 |
| 本机产物 | `.venv/`、`node_modules/`、`data/`、`logs/`、构建目录 | 已忽略，不通过 Git 同步 |

`.gitattributes` 固定 Shell/Python 等为 LF、PowerShell/CMD 为 CRLF，减少跨系统换行差异。
本次不进行整个仓库的换行重写；已有工作树可能在后续检出时才按规则规范化。

## 明天在 Windows 上接手

先确认这次 Mac 改动已经提交并推送。Windows 本地有未提交工作时先保存/提交，遇到拉取冲突检查双方修改，不使用 `reset --hard` 覆盖。

```powershell
git status
git pull --ff-only
```

本次更改了安装器：**先停止 Windows 正在运行的看板，再运行一次安装脚本**。已有有效 Wind SDK 路径会被保留；旧路径不存在时尝试当前用户默认目录。

```powershell
.\stop.ps1
.\install.ps1
.\.venv\Scripts\python.exe scripts/configure_wind.py --check
.\.venv\Scripts\python.exe wind_check.py
.\.venv\Scripts\python.exe -m pytest -q
.\start.ps1
```

`--check` 仅检查路径；`wind_check.py` 才会加载 SDK、登录并执行一个 WSS 查询。
SDK 安装目录非默认时，先指定包含 `WindPy.py` 的目录，例如：

```powershell
$env:WIND_PYTHON_DIR='D:\Wind\Wind.NET.Client\WindNET\x64'
.\install.ps1
```

这会将路径保存到本机 `.venv` 的 `WindPy.pth`，日常启动不必继续设置该环境变量。
也可直接运行 `.\.venv\Scripts\python.exe scripts/configure_wind.py --wind-python-dir 'D:\Wind\Wind.NET.Client\WindNET\x64'`。
不要把 Mac 的 `.venv` 复制到 Windows；若误复制，另建本机虚拟环境。

启动后检查页面/API 的 `feed_state`、`last_error`、剩余同步任务和各品种状态。盘后正常的“已收盘”不等于实时盘中验证。
本次共享配置工具的 Windows 路径分支已做离线测试，PowerShell 脚本和 SDK 仍需 Windows 实机运行，不能用 Mac 测试替代。

## Mac 安装与启动

从 Mac App Store 安装官方 Wind API。保留 `/Applications/Wind API.app` 原始位置：当前官方 Python 模块内部使用该位置加载动态库，移动应用后仅修改 Python 搜索路径未必够用。

```bash
./install-mac.command
.venv/bin/python scripts/configure_wind.py --check
.venv/bin/python wind_check.py
./'Start Dashboard Mac.command'
```

默认使用 `python3.12`，必要时用 `PYTHON_BIN=/完整路径/python3.12 ./install-mac.command` 指定。
访问 `http://127.0.0.1:8765`，终端 Control+C 停止。不要与已有8765服务同时启动。
首次 API 调用可能出现独立 Wind 登录窗口，保存凭据/自动登录由用户自行决定。

## 每次拉取后做什么

| 改动类型 | 操作 |
| --- | --- |
| 只有文档 | 不必重启 |
| Python 业务代码或配置 JSON | 停服后拉取，运行相关测试，重启 |
| 前端源码 | 停服后拉取，执行 `npm run build --prefix frontend`，重启/刷新 |
| Python 依赖或前端锁文件 | 停服后拉取，重跑本机安装器，再验证启动 |
| Wind 或进程生命周期 | 两个系统分别做真实连接验证；记录未验证项 |

每次开始工作先拉取；完成后检查 diff、测试、提交并推送；换电脑再拉取。并行开发功能时用临时功能分支，合回共同主线，不维护永久 Windows/Mac 两条主线。
Git 不同步行情库、历史事件、登录状态或服务器进程。需要迁移数据时另行设计备份和导入流程。

## 已知问题与证据边界

Mac 独立 API 测试成功，前端可构建、HTTP 服务可启动，也观察到过行情同步页面，但连接会再次回到重连状态。
持续采集尚不稳定，不作为已完成验收。SDK 首次认证、子进程启动和后续查询的超时需要逐项定位，不能仅从“已连接”文本判断完成。
配置整理未改变服务25秒连接超时、45秒请求超时或行情质量门槛。实测记录见 `reports/mac-setup-2026-11-10.md`。
