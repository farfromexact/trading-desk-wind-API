# 给后续 Codex / 开发者

## 项目与平台约定

- 这是同一个 Windows/macOS 产品，使用同一个 repo 和共同主线。不要复制业务实现到 Mac 专用目录，不要建立长期平台分支。
- `app/` 和 `frontend/` 是共用业务代码；策略规则、交易日归属、风控静默和期权模型不得因为平台适配而改变。
- Windows 安装/启动/停止入口：`install.ps1`、`start.ps1`、`stop.ps1` 和现有 CMD 包装。
- macOS 安装/启动入口：`install-mac.command`、`Start Dashboard Mac.command`；Mac 以前台服务运行，Control+C 停止。
- 两端安装器共用 `scripts/configure_wind.py`。SDK 目录优先级：显式参数/环境变量 `WIND_PYTHON_DIR` → 已有有效 `.pth` → 系统默认路径。只写当前 repo 的 `.venv`，不修改系统 Python。
- Wind SDK 来自官方客户端，不从 PyPI 安装同名包，不把 SDK、账号、许可证、密码或本机路径配置提交到仓库。
- `.venv`、`node_modules`、`frontend/dist`、数据库、缓存、日志各机独立生成；不要从另一操作系统复制过来。
- `config.json`、`options-config.json` 是共用产品参数，会影响两端；不是放本机安装路径的地方。`.env` 不会自动加载，本项目没有引入 dotenv。

## 修改前和验证

1. 先读 `README.md` 的跨平台维护段、`docs/CROSS_PLATFORM.md`，检查 `git status` 和已有改动，不覆盖其他机器/用户未提交的修改。
2. 改动平台脚本时保留另一平台入口；优先使用 `pathlib` 和平台检测，禁止写死某台电脑的用户名。
3. Python 依赖使用 `requirements-lock.txt`，前端使用 `frontend/package-lock.json`。依赖变更同步维护清单和锁文件，并说明另一平台尚未执行的验证。
4. 业务变化运行项目 pytest；前端变化执行现有 Node 测试和构建；安装器变化验证路径解析、错误提示和 shell 语法。不得把模拟的平台路径测试称为真实 Windows 执行。
5. Wind 真实请求应少量、只读且串行。不要为验证另启多个采集进程，也不要停止用户正在运行的服务，除非获得授权。服务运行时优先读取 `/api/state` 和观察脚本。
6. 保持仅本机监听，不加入交易下单功能，不为了让状态变绿而降低行情新鲜度、完整性或同步门槛。

## 2026-11-10 交接（Windows 上接手前必读）

- 原 Windows 版本有真实验证记录，见 `reports/validation-2026-11-10.md` 及扩展报告；这些是当时的结果，不代表后续改动已在 Windows 重测。
- Mac 已安装官方 Wind API 26.1.7，Python 3.12 arm64；独立进程的登录、WSS、交易日历、主力映射、WSI、WSQ 快照/回调、WSET 期权链均成功。
- 后续已定位商品期权标的报价被旧快照覆盖、增量盘口字段误判及采集压力问题。共用 worker 现在把控制响应与行情分队列，记录原生请求耗时；商品期权复用同月期货报价，实时订阅交易中的完整监测池，变更时保留未受影响的订阅组。
- 完整快照初始化后，连续增量回调可保留未变化的盘口；不能将字段最后变化时间当作最后确认时间。重连清空初始化状态。仍保持15秒行情新鲜度、10秒同步、20%价差与挂单量门槛。
- Mac快照批量上限8，Windows仍使用60；25秒连接与45秒请求超时不变。Windows SDK、队列分离和完整池订阅须在Windows实机复验，不能将Mac夜盘结果当作日盘/Windows验收。
- 排障可临时设置 `WIND_DESK_OPTIONS_DISABLED=1` 后启动作期货对照；恢复时删除变量再重启。单个Wind采集进程，观察使用 `scripts/observe_stability.py`，不并发运行 `wind_check.py`。
- 最新实测见下方2026-11-12补充及其报告；`reports/options-commodity-feed-fix-2026-11-10.md`保留此前对照，不代表当前版本。
- 新增文件若尚未提交，其他电脑不会自动得到。用户要求推送前不要自行 commit/push。

明天 Windows 验证与日常同步步骤见 `docs/CROSS_PLATFORM.md`。

## 2026-11-12 刷新与回调快照补充

- 5/15分钟原生线仍每分钟查45分钟重叠区间，正式信号规则不变。1分钟仅作辅助图：无查看需求时至少间隔300秒补缓存，查看时每分钟刷新；本机`/api/chart-interest`租期90秒，多页面去重。
- 期权健康、完整初始化且持续更新的合约跳过30秒重复补数，120秒完整审计；一个回调内的完整盘口也可初始化，禁止拼接多个零散包冒充完整初始化。到期合约保留展示和到期原因，停止实时补数/监测池订阅。
- 现场再次复现Mac同步期权快照45秒超时。Mac改用`app/wind_snapshots.py`临时回调快照：回调只入队，唯一工作线程注册/取消；10秒等待期限后在下一轮调度清理并记录缺失，不阻塞期货查询。Windows保留同步路径，SDK实机复验仍待执行。原生45秒/连接25秒保护不变。
- `acquisition.pending.waiting_callback`区别回调等待；Mac快照原生耗时只含注册/取消。期权指标`snapshot_timeouts`记录回调缺失，不能当成行情已完整。
- 本轮完整结果与失败记录见`reports/refresh-pressure-and-atmosphere-2026-11-12.md`，不要把旧15分钟稳定报告当作本轮验收。
