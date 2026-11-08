---
name: epic-game-restore
description: Restore or migrate backed-up Epic Games Store game folders that still contain their .egstore metadata into a chosen install directory, and register them as installed in the modern Epic Games Launcher (eglv2) so they appear in the library and launch without re-downloading. This skill should be used when the user asks to restore, recover, import, move, or re-register Epic games from a backup folder, or when Epic game files exist on disk but the launcher does not show them as installed.
agent_created: true
---

# Epic 游戏备份恢复

## 目的

把备份目录里带 `.egstore` 元数据的 Epic 游戏文件夹放到目标安装目录，并让 Epic 启动器把它们识别为"已安装、可启动"，避免重新下载几十上百 GB。
核心原理：新版启动器用 `%LOCALAPPDATA%\com.epicgames.eglv2\installations.jsonl` 记录已安装游戏；只要按规则补写记录并重启启动器，它就会回放日志、检查启动程序、复用已有文件。

## 适用与不适用

- 适用：新版 eglv2 启动器；备份保留了每个游戏的 `.egstore`（`.manifest` + `.mancpn`）；游戏文件在同一块磁盘上迁移/恢复。
- 不适用：备份缺 `.egstore`（此时只能走官方重装流程，会重新下载）；旧版启动器（用 `ProgramData\Epic\...\Manifests\*.item`，改用第 5 节的官方流程）。

## 工作流

### 1. 只读侦察，生成恢复计划

先确认启动器版本、备份结构、目标目录现状、磁盘剩余空间、启动器进程与下载队列是否空闲。
随后运行扫描脚本（只读，不改任何文件）：

```bash
python <skill>/scripts/inspect_backups.py \
  --backup "G:\Game\epic备份" \
  --dest "G:\Game\Epic Game" \
  --out "C:\Users\abel\epic-restore-plan.json"
```

脚本会解析每个 `.egstore` 的 manifest（二进制与 JSON 两种格式）和 `.mancpn`，结合
`library-cache.json` 自动选出主游戏 artifact（排除 Assembly Kit / DLC 等附加包），输出：
游戏文件夹、体积、`installationId`、`namespace` / `itemId` / `artifactId`、版本、启动程序、库内标题，
以及冲突与告警。计划 JSON 交给下一步使用。

**必须人工复核扫描结果**：确认每个 `libraryTitle` 对得上、`launchExecutable` 存在、目标目录无同名文件夹、`[WARN]` 项都已解释清楚。
遇到 `--all-candidates` 显示多个候选时，以 `references/eglv2-format.md` 第 3 节的规则判断主游戏。

### 2. 向用户确认两个决策后再动手

用提问确认，不要替用户默认：

1. **文件放置方式**：`move`（同盘瞬时、备份目录会被清空）、`copy`（保留备份、需等量空间）、`junction`（瞬时、备份保留但不能单独删除任一边）。
2. **识别方式**：`写记录 + 重启启动器`（推荐，自动、需短暂关闭启动器）或 `官方安装流程`（只放文件，用户在启动器里逐个点安装到同一目录，零配置风险）。

同时告知总体积、目标盘剩余空间、原备份会被如何处理。

### 3. 关闭启动器

选择"写记录"方案时，先优雅关闭 `EpicGamesLauncher.exe`（`CloseMainWindow()`，数秒后若仍在则 `Stop-Process -Force`），并确认进程已退出、下载队列空闲。
**启动器运行时绝不修改 `installations.jsonl`。**

### 4. 先 dry-run，再执行

```bash
python <skill>/scripts/apply_restore.py --plan "C:\Users\abel\epic-restore-plan.json"          # 预演
python <skill>/scripts/apply_restore.py --plan "C:\Users\abel\epic-restore-plan.json" \
  --execute --mode move --launch-after
```

脚本自动完成：前置校验（源/目标/元数据/空间/重复记录）→ 备份 journal 为
`installations.jsonl.bak-<时间戳>` → 按 mode 放置文件夹并逐个校验 `.egstore` →
按第 2 节字段规则追加 `complete:true` 记录 → 可选启动启动器。
任何一步失败立即中止，并提示用备份恢复。

### 5. 重启并验证

```bash
python <skill>/scripts/verify_restore.py --plan "C:\Users\abel\epic-restore-plan.json"
```

检查 journal 中完成记录数、每个 `dir` / 启动程序是否真实存在，并抓取最新启动器日志中的
`Replayed the installations log ... live=N removed=0`、`ExecutableCheckCompleted ... suspect=0`、
`Dropped 0 unplayable installation record(s)` 与每款游戏的 `new_state=CanLaunch`。
把结果汇总给用户。

### 6. 官方安装流程（备选）

不改任何启动器数据，只把文件放到目标目录；让用户在库中对每个游戏点「安装」并选同一目录，
启动器扫描已有文件后进入验证、只补差异文件。旧版启动器一律走这条路。

## 不可违反的规则

- `artifactId` 取 `.mancpn` 的 `AppName`（catalog release appId），**不是** manifest 里的 `AppNameString`。
- `installationId` 取主 manifest 文件名去扩展名；`version` / `launchExecutable` 取 manifest 的 `BuildVersionString` / `LaunchExeString`。
- `dir` 与已有记录保持一致的全反斜杠 Windows 路径；不要混用 `/`。
- 不要手动改 journal 第一行 header 的 `entries`；启动器启动时自行 compact。
- 追加记录用 `newline="\n"`，写前检查文件末尾是否有换行。
- 改 journal 前后必须确认启动器完全退出；改完必须重启才生效。
- 只追加、不重写已有记录；已注册的游戏直接跳过。
- 提醒用户：备份 manifest 版本通常偏旧，启动器可能提示更新，更新会验证并复用本地文件，不会全量重下；个别游戏异常时用「管理 → 验证/修复」。

## 回滚

```bash
python <skill>/scripts/apply_restore.py --rollback "C:\...\installations.jsonl.bak-20261108-183000" \
  --journal "C:\...\installations.jsonl"
```

回滚只恢复 journal；`move` 模式下的文件位置需手动移回。回滚前同样要关闭启动器。

## 随附资源

| 文件 | 用途 |
|---|---|
| `scripts/epic_meta.py` | 共享解析库：启动器探测、journal 读写、library 索引、manifest/mancpn 二进制解析、候选打分 |
| `scripts/inspect_backups.py` | 只读扫描 → 生成恢复计划 JSON |
| `scripts/apply_restore.py` | 按计划放置文件 + 写 journal（默认 dry-run；支持 `--rollback`） |
| `scripts/verify_restore.py` | 重启后核对 journal 与启动器日志 |
| `references/eglv2-format.md` | 完整格式说明：两代启动器差异、journal 字段表、manifest 二进制布局、主 manifest 判定、验证标志、故障排查 |

脚本仅依赖 Python 标准库，面向 Windows。遇到解析异常或启动器版本差异时，先读
`references/eglv2-format.md`，必要时用脚本里的函数做交互式诊断，再决定是否调整计划。
