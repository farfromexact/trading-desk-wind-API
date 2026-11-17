# 给后续 Codex / 开发者

## 项目与平台约定

- 这是同一个 Windows/macOS 产品，使用同一个 repo 和共同业务主线 `main`；按用户 2026-11-17 的新决定，Windows 使用 `codex/windows-stable`，Mac 使用 `codex/mac-stable`。两端分别发布，不复制业务实现到平台专用目录。
- `app/` 和 `frontend/` 是共用业务代码；策略规则、交易日归属、风控静默和期权模型不得因为平台适配而改变。
- Windows 安装/启动/停止入口：`install.ps1`、`start.ps1`、`stop.ps1` 和现有 CMD 包装。
- macOS 安装/启动入口：`install-mac.command`、`Start Dashboard Mac.command`；Mac 以前台服务运行，Control+C 停止。
- 两端安装器共用 `scripts/configure_wind.py`。SDK 目录优先级：显式参数/环境变量 `WIND_PYTHON_DIR` → 已有有效 `.pth` → 系统默认路径。只写当前 repo 的 `.venv`，不修改系统 Python。
- Wind SDK 来自官方客户端，不从 PyPI 安装同名包，不把 SDK、账号、许可证、密码或本机路径配置提交到仓库。
- `.venv`、`node_modules`、`frontend/dist`、数据库、缓存、日志各机独立生成；不要从另一操作系统复制过来。
- `config.json`、`options-config.json` 是共用产品参数，会影响两端；不是放本机安装路径的地方。`.env` 不会自动加载，本项目没有引入 dotenv。

## 平台分支与操作权限（2026-11-17，覆盖旧的两端跟随 main 约定）

- 开始前确认操作系统、`git branch --show-current`、上游分支和 `git status`，并读 `docs/PLATFORM_BRANCHES.md`。日常拉取、提交和推送只针对本平台稳定分支；`main` 汇集共享工作，不代表另一个平台已验收或已发布。
- Windows 不推进、合并、重置或推送 `codex/mac-stable`；Mac 不推进、合并、重置或推送 `codex/windows-stable`。不要为了猜测另一平台的兼容性而修改其专用启动器、SDK 调用方式或平台路径；跨平台适配由用户在目标机器要求同步后，在目标机器完成。共享业务需求仍只实现一次。
- 本次分支初始化是明确授权的例外：Mac 分支以 `796962b67a9fa5cd99fdb21691e1e9a78901c23d` 为运行代码基线，只增加分支操作文档；Windows 以 `a73b5d927f37ac802fdc24386bb47d783271ce9f` 为代码基线。前者为用户确认正常的 Mac 版本，不得把后续 Windows 修复打包送入 Mac。初始化不代表本轮重新做过 Mac 实机验证。
- 新功能在本机临时功能分支或本机稳定分支开发；获提交/推送授权后将共享功能归回 `main`，只发布本机验证过的内容到本机稳定分支。不得自动合并、挑选提交或强推到另一平台稳定分支。
- 用户只说“同步远端”时，默认更新本平台分支；用户在 Mac 明确要求接收 Windows 新功能时，先在 Mac 临时分支检查、选择提交并验证，再推进 Mac 稳定分支。反方向同理。不要为了两边提交号一致而同步所有差异。
- 本机未提交文件、构建产物、Wind 配置和行情库必须保留。切换前有改动时先检查归属，不自动丢弃、强制检出或把它们带到另一平台；只改文档无需重启、重建或调用 Wind。

## 修改前和验证

1. 先读 `README.md` 的跨平台维护段、`docs/PLATFORM_BRANCHES.md`、`docs/CROSS_PLATFORM.md`，检查当前分支、`git status` 和已有改动，不覆盖其他机器/用户未提交的修改。
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

## 2026-11-16 盘面背景统一 WindPy

- 用户明确要求所有正式数据走WindPy，禁止恢复MCP/CLI/API Key或失败后回退MCP。旧历史预览导入器只读存档，不是联网取数入口。
- `app/context_wind.py` 与 `app/wind_context_client.py` 复用唯一采集worker。背景请求低于期货/期权优先级、单在途，排队过期让行，WSQ等待10秒不占用原生工作线程；两端共用，原Windows期权路径保持不变。
- ETF资金流使用WSD MFD_INFLOW_M；净申赎使用WSD UNIT_TOTAL/NAV推算并明确标记估算，不冒充MCP字段。校验实际日期和元单位；资金流无源更新时间，标记日频截面，不冒充盘中实时。多基金单日矩阵支持两种官方SDK布局，须按明确维度核验。
- 新旧缓存分离，不能把旧MCP资金流当成WindPy结果。本轮Mac收盘后验证与Windows待验证边界见 `reports/market-context-windpy-2026-11-16.md`；更新后必须重建前端并重启，不能仅pull刷新旧页面。
- 用户随后提供指数聚合字段 `rt_up_total/rt_down_total/rt_same_total`，已与 `rt_date/rt_time` 接入原三指数批次。保持1000家覆盖与源时间校验，不新增成分股订阅；Mac原生实测中证1000家数295/686/19、参与度30.07%，页面已显示30.1%；15分钟变化仍需盘中历史快照。ETF WSD/WSS份额净值对照通过，资金流unit参数缩放核验失败，后续不得把全部ETF审计称为通过。详见本轮报告。

## 2026-11-16 五组广度 / 近5分钟补充

- `INDICES`仍仅IM/IF/IH，供原分钟线与历史调度使用；广度用`COUNTS`的五个查询代码（000016/000300/000852/000688.SH、399006.SZ；后两者是板块口径），禁止直接扩充INDICES而意外增加后两者WSI及历史负载。
- `breadths`含五卡；`rotation`含5分钟strength、同刻配对历史归一化turnover、同步breadth_gap。旧`breadth`保留中证1000别名，旧`style`现在是1000/300的5分钟strength，历史预览使用共用新算法，不再代表旧15分钟1000/50口径。
- 广度5分钟对照只取目标前90秒，同交易日同半日；两指数广度差要求当前及历史对照时间差均≤10秒。重复收盘源快照不产生趋势；成交偏向不是资金净流入。原15分钟成交活跃度和正式信号完全不变。
- 最新验证见 `reports/five-index-context-2026-11-16.md`；本轮不改ETF实现，上一轮资金流unit缩放差异仍未解决，不能写成已全部通过。未获推送指令前不commit/push。

- 用户最终明确接受Wind原始聚合家数、不再核验样本。后两卡标科创板/创业板·Wind聚合，`COUNTS=None`，范围动态，`coverage=vendor_reported`且`scope_ok=null`；不得误标成已核验50/100指数样本。前三指数数量校验保留。禁止恢复名单查询或新增个股订阅：本轮全A名单WSET曾触发Mac45秒超时及一次重连，最终路径已移除，完整失败与修复后观察见报告。

## 2026-11-16 发布补充

- 指标旁新增前端说明按钮，悬停/聚焦查看，点击固定、Esc/点空白关闭，不新增API请求。
- `.github/workflows/checks.yml` 对PR和main执行Windows/Linux离线pytest、Node测试与构建；CI不带Wind SDK，不得称为Windows行情实测。Windows更新涉及运行代码时，停止旧服务、拉取 `codex/windows-stable`、运行install.ps1重建前端、再启动；2026-11-17 起不再直接跟随 main，详见docs/CROSS_PLATFORM.md。

## 2026-11-16 刷新间隔调整

- 盘面底色刷新冷却由60秒缩短为30秒；页面每3秒读本机缓存，显示后端返回的interval_seconds。ETF日频资金流独立保持至少60秒，估算申赎缓存、分钟线完整标签、主行情优先级不变。
- 本机226项pytest、28项Node测试与构建通过。21:53:45重启后通过唯一采集器串行执行相隔31秒的两次刷新，均完成且无背景错误，重连计数0；这是收盘后短时验证，不是Windows或现货盘中稳定性验收。发布状态以主线提交和对应PR为准。

## 2026-11-17 Windows背景快照修复

- Windows实盘复现临时WSQ回调10秒内不能完整返回，补读注册返回值仍未解决；Windows背景WSQ改为与现有主行情一致的同步快照。按用户要求，提交前撤掉初始返回值处理及回调重构，Mac原有注册、回调处理、取消逻辑保持原样，不合并零散增量。平台选择集中在`wind_worker.py`，仅Windows采用同步快照，仍复用唯一worker、低优先级、单在途、15秒排队让行、2秒慢查询退避及45秒原生保护，不回退MCP或API Key。
- “盘中更新”开关保存到本机忽略文件`data/market-context-settings.json`，重启恢复上次选择，首次默认按需，离线/禁用始终不采集。ETF当日WSD返回空值时保留查询数据日和缺失原因，不替换有效旧值、不补零。
- 最终243项Python、29项Node测试及构建通过；Windows五组广度和五个跨市场合约快照已实盘恢复，Mac本轮未实机复验。结果及未完成边界见`reports/context-windows-display-fix-2026-11-17.md`，不把短时观察写成全天验收。
