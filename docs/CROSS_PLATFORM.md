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

## Windows 接手本次更新（2026-11-12）

同一仓库、同一条`main`主线。先在Windows仓库目录检查工作区；若有未提交修改，先保存或提交，遇到冲突检查双方内容，不使用`reset --hard`覆盖。确认本机Wind已安装并登录。

在PowerShell中依次执行，每步成功后再继续：

```powershell
git status
.\stop.ps1
git switch main
git pull --ff-only
.\install.ps1
.\.venv\Scripts\python.exe scripts/configure_wind.py --check
.\.venv\Scripts\python.exe -m pytest -q
node --test (Get-ChildItem frontend/src/*.test.js).FullName
.\start.ps1
```

`install.ps1`会复用本机有效的官方SDK路径、按锁文件安装依赖，并执行`npm ci`和前端构建。这次没有更换依赖或Windows入口；重新执行安装器是为了确保拉取后的前后端版本一致。**仅pull、或仅刷新旧页面，不会自动生成新版`frontend/dist`。** 不复制Mac的虚拟环境、SDK或行情库。

本次Windows仍使用原同步期权快照（每批最多60），Mac回调补数只在Darwin启用。共享降频、完整盘口判断、到期停止补数和橙红灯效会一起更新；5/15分钟正式信号频率及门槛不变。已有170项Python与14项前端测试、Mac夜盘实测记录，**本轮Windows原生SDK还需实机盘中复验**。

若要独立诊断SDK，保持看板停止后运行`.\.venv\Scripts\python.exe wind_check.py`，完成后再启动看板；不要与采集器并行运行。启动后刷新浏览器，检查当前交易中的期货、适用且未到期的期权、查看1分钟图的补数及重连计数。完整记录见[本次验证报告](../reports/refresh-pressure-and-atmosphere-2026-11-12.md)。

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

早期Mac版本曾因查询超时反复重连。后续已增加请求操作/品种/执行时长及子进程退出码日志，并修复商品标的报价来源、增量盘口更新语义和采集队列/订阅压力。25秒连接与45秒请求超时不变。
最新结果见 [刷新与回调快照报告](../reports/refresh-pressure-and-atmosphere-2026-11-12.md)，此前对照见 [期权采集修复报告](../reports/options-commodity-feed-fix-2026-11-10.md)。原始安装报告保留当时的失败记录；不能只从“Wind已连接”判断验收。

Windows接手时还需要盘中验证期货持续更新、各交易中期权品种有效估值、`metrics.restarts`、`acquisition.native_requests` 和 `quote_processing_lag_ms`。真实Windows SDK尚未复测，日盘指数期权也不能用Mac商品夜盘替代。

若需要比较停用期权后的表现，在同一个PowerShell窗口中停止看板、设置变量后启动：

```powershell
.\stop.ps1
$env:WIND_DESK_OPTIONS_DISABLED='1'
.\start.ps1
.\.venv\Scripts\python.exe scripts/observe_stability.py --mode futures-only --seconds 600 --output reports/windows-futures-only.json
.\stop.ps1
Remove-Item Env:WIND_DESK_OPTIONS_DISABLED
.\start.ps1
.\.venv\Scripts\python.exe scripts/observe_stability.py --mode options-on --seconds 900 --output reports/windows-options-on.json
```

观察脚本只读取本机HTTP，不启动Wind进程；每次使用新的输出文件名。环境变量只是临时排障开关，不提交本机设置。`zero_qualified`记录整组估值缺失次数，不代表每一个合约都应该可估值；深度虚值、宽价差与旧报价应继续显示缺失。

## 2026-11-12 刷新降压与页面提示

- 正式5/15分钟线继续每分钟查询近45分钟重叠区间；信号规则、修订检查、交易日和门槛不变。
- 辅助1分钟缓存无人查看时至少间隔300秒补查，下一轮分钟调度执行；查看时每分钟刷新。页面通过本机 `/api/chart-interest` 登记90秒有效的查看需求，每30秒续期；首次打开会去重补查，离线模式不请求Wind。多个页面共用缓存与原生请求。
- 期权30秒检查修复需求，健康且完整初始化的连续订阅合约改为120秒完整快照审计；异常/过期报价仍30秒补查。重连清空快照审计状态，Mac8/Windows60的批量上限不变。
- 全页橙色覆盖当前交易日仍有效的初始化关注，保留静默；红色仍仅表示实时确认/触价。增加边缘光、雷达脉冲和对应品种高亮，支持减少动态效果。
- 拉取后需要重新构建前端并重启Python服务。Windows须实测一分钟图切换、连续期权订阅、新收盘线及时性和稳定性；不能把Mac结果当成Windows验收。


本轮盘中复现过一次Mac同步 `option_snapshot AG` 45秒超时，随后增加Mac临时期权回调快照路径（`app/wind_snapshots.py`）。临时请求与常驻订阅分开管理，回调只入队，注册/取消仍在唯一Wind工作线程执行；缺少完整字段达到10秒等待期限后在下一轮调度结束本轮并记录缺失，不重置整条连接。Windows继续原同步路径；两端同一套完整性和新鲜度门槛，不因平台不同改变估值或信号规则。后续验证须区分 `waiting_callback` 与原生调用执行时间。
