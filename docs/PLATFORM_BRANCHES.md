# Windows / Mac 稳定分支操作说明

用户于 2026-11-17 确认：两台机器分别使用自己已验证的版本；Windows 增加功能后，等用户到 Mac 上要求同步，再由 Mac 接收和验证。两端不得主动修改、推进或发布对方的稳定分支。本说明替代旧的“两台都直接跟随 main、不要长期平台分支”约定。

## 分支职责与初始版本

| 分支 | 用途 | 初始运行代码 |
| --- | --- | --- |
| `main` | 汇集共享功能与修复，不自动部署到任一平台 | 已包含 Windows 修复；不作为 Mac 更新入口 |
| `codex/windows-stable` | Windows 日常运行、发布和接手 | `a73b5d927f37ac802fdc24386bb47d783271ce9f` |
| `codex/mac-stable` | Mac 日常运行、发布和接手 | `796962b67a9fa5cd99fdb21691e1e9a78901c23d` |

Mac 基线是 11月17日09:25 在 Windows 拉取的远端版本，用户明确确认它在 Mac 正常运行。初始化 Mac 分支时只添加 README、AGENTS 和本分支说明等维护文档，应用、前端、配置、依赖清单与安装/启动脚本保持该基线不变；**不包含 `a73b5d9` 的 Windows 修复**。这是用户确认的既有状态，不是本次在 Windows 上重新完成的 Mac 验证。

Windows 基线已完成当轮 Windows 短时行情观察和离线检查，不代表全天或 Mac 实机验证。初始化只发布分支说明，工作区其他未提交修改不随本次发布。

两个稳定分支可以指向不同提交。共享功能仍归回 `main`，平台专用差异集中在原有适配位置；不得复制两套指标、策略或业务实现。`main` 的提交通过离线检查，也不等于可以自动部署到另一平台。

## 首次从旧 main 切换

先检查 `git status`。若有未提交改动，检查归属并妥善保存；不要使用 `reset --hard`、强制检出或自动 stash 来掩盖冲突。以下命令适用于**尚未存在本地稳定分支**的工作区，每步成功后再继续；若本地分支已存在，直接 `git switch` 到该分支并检查其上游，不使用 `-C` 重建。

先 fetch，不要先把最新 main 拉进旧运行版本。可以不切换分支，直接查看远端新的接手说明：

```bash
git status
git fetch origin
git show origin/codex/mac-stable:AGENTS.md
```

Mac 首次切换：

```bash
git switch --track -c codex/mac-stable origin/codex/mac-stable
git status --short --branch
```

Windows 首次切换：

```powershell
git switch --track -c codex/windows-stable origin/codex/windows-stable
git status --short --branch
```

切换前比较当前版本与目标分支。若只差维护文档，不必重启或构建；若涉及运行代码，先停止本机服务，切换后按 `CROSS_PLATFORM.md` 安装/构建、验证和启动。不要在运行中的目录试切其他平台的代码。Mac 已经位于 `796962b` 且没有额外改动时，本次首次切换只有文档差异。

## 日常同步与推送

用户只说“同步远端”，默认含义是更新**当前平台**的稳定分支，不是把另一平台的新功能整包合并进来。

| 当前机器 | 运行分支 / 上游 | 日常拉取 | 获授权后的推送目标 |
| --- | --- | --- | --- |
| Windows | `codex/windows-stable` / `origin/codex/windows-stable` | `git pull --ff-only origin codex/windows-stable` | `git push origin codex/windows-stable` |
| Mac | `codex/mac-stable` / `origin/codex/mac-stable` | `git pull --ff-only origin codex/mac-stable` | `git push origin codex/mac-stable` |

执行前必须确认当前分支、上游和未提交内容；表内 pull 命令只在对应分支执行。若历史分叉，先检查双方提交，不强制拉取、重置或覆盖。不要使用 `git push --all`、`--mirror` 或强推来使两端一致。

Windows 不推 Mac 分支，Mac 不推 Windows 分支；不改对方专用启动器、SDK 路径和已验证的采集调用方式。此次由 Windows 建立 Mac 初始分支并加入维护文档，是用户明确要求的一次性初始化，不构成今后从 Windows 发布 Mac 代码的授权。

## 在 Windows 新增功能，之后由 Mac 接收

1. Windows 从自己的稳定版本开始，用 `codex/windows-...` 临时分支开发，或在已授权的本机稳定分支内完成小改动。保留 Mac 专用路径与原有业务口径。
2. 完成 Windows 所需验证、记录实际验证平台和未完成项，获提交/推送授权后把共享功能归回 `main`，并发布到 Windows 稳定分支。若 main 含其他尚未在本机验证的改动，先选择并验证所需提交，不为追平提交号而整包升级。
3. **不推进 Mac 稳定分支**。Mac 继续使用自己的正常版本。Windows 端可以记录待接收的提交号、功能说明和依赖变化，但不替 Mac 做适配或声称实机通过。
4. 用户到 Mac 上要求“接收 Windows 的新功能”后，在 Mac fetch，先查看 `main` / Windows 分支与 Mac 稳定分支的差异，确认本次要接收的功能和依赖提交。建立 `codex/mac-sync-...` 临时分支；原 Mac 稳定分支保持不动。
5. 在 Mac 临时分支选择所需提交或审查后合并共享主线，处理本机适配并完成相应测试、构建和真实 Wind 验证。禁止直接覆盖 Mac 已正常工作的 SDK 回调/启动逻辑；若某提交同时包含 Windows 专用改动，应拆清所需部分，不整包接收。失败时保持 Mac 稳定分支原样，不去改 Windows 稳定分支。
6. 验证通过、记录结果并在用户授权范围内推进和推送 Mac 稳定分支；共享修复归回 main，Windows 是否接收由之后在 Windows 的工作决定。

从 Mac 向 Windows 接收功能时按同样规则反向进行。整个流程复用本机唯一 WindPy 采集器，不同时启动第二个采集进程；需要停服或真实请求时遵守 `AGENTS.md` 的现有约定。平台分支隔离发布节奏，不降低行情完整性、新鲜度、同步或交易规则门槛。

## 文档与本机文件

- README、AGENTS 和本文件都说明分支规则，后续接手必须先读当前分支中的版本。
- 维护说明是约定，不是服务器端分支保护；不要把“有两个分支”解释为所有跨平台回归都被自动阻止。
- `.venv`、`node_modules`、`frontend/dist`、Wind 登录/SDK、本机设置、缓存和行情数据各自保留，不通过分支搬运。
- 只调整分支和文档时，核对提交范围、远端引用和旧文件是否保持；不为此启动采集、重启桌面或重复跑业务测试。
