# epic-game-restore

把带 `.egstore` 元数据的 Epic 游戏备份恢复到指定目录，并让新版 Epic 启动器（eglv2）认成「已安装、可启动」，**不用重新下载几十上百 GB**。

这是一个 WorkBuddy 技能。直接对助手说一句话即可：

> 把 `X:\备份目录` 里的 Epic 游戏恢复到 `Y:\目标目录`，能在 Epic 里打开

技能会先只读扫描、列出识别到的游戏和体积，问你两个决策，确认后才动手。

## 适用 / 不适用

| | 说明 |
|---|---|
| ✅ 适用 | 新版 eglv2 启动器；备份保留了每个游戏的 `.egstore`；同一块磁盘内迁移或恢复 |
| ❌ 不适用 | 备份缺 `.egstore`（只能走官方重装流程，会重新下载）；旧版启动器（使用 `ProgramData\Epic\...\Manifests\*.item`） |

## 会先问你的两件事

1. **文件怎么放**：`move`（同盘瞬时完成、备份目录清空）／`copy`（备份保留，但需要等量空间）／`junction`（瞬时、备份保留，但两边是同一份文件，之后不能单独删除任一边）。
2. **怎么让启动器认出来**：`写记录 + 重启启动器`（自动，推荐）／`官方安装流程`（只放文件，你在库里逐个点「安装」到同一目录，零配置风险）。

如果启动器正在下载更新，还会额外问你要不要中断——Epic 的下载队列是持久化的，重启后会自动继续。

## 工作原理

新版启动器把已安装游戏记录在 `%LOCALAPPDATA%\com.epicgames.eglv2\installations.jsonl`（append-only 的 JSONL 日志），不再是旧版的 `*.item` 清单。

技能从每个游戏 `.egstore` 里的 `.manifest` 与 `.mancpn` 解析出 `installationId`、`namespace` / `itemId` / `artifactId`、版本号和启动程序，再按已有记录同样的字段格式追加 `complete:true` 记录，然后重启启动器。启动器回放日志、检查启动程序、复用已有文件，游戏就出现在库中了。

`artifactId` 取自 `.mancpn` 而不是 manifest；同一游戏若已有有效安装，会先询问而不是直接追加第二份。

## 目录结构

| 文件 | 用途 |
|---|---|
| `SKILL.md` | 主流程、必问决策、不可违反的字段规则 |
| `scripts/epic_meta.py` | 解析共享库：二进制/JSON manifest、`.mancpn`、journal、游戏库缓存 |
| `scripts/inspect_backups.py` | 只读扫描 → 生成恢复计划 JSON |
| `scripts/apply_restore.py` | 放置文件 + 写 `installations.jsonl`，**默认 dry-run**，支持 `--rollback` |
| `scripts/verify_restore.py` | 重启后逐游戏核对记录与启动器日志 |
| `references/eglv2-format.md` | 完整格式说明、两代启动器差异、主游戏判定、故障排查 |

脚本仅依赖 Python 标准库，面向 Windows。

## 安全设计

- 扫描阶段完全只读；`apply_restore.py` 不加 `--execute` 只会预演，不改动任何文件。
- 修改 journal 前先备份为 `installations.jsonl.bak-<时间戳>`，并确认启动器进程已完全退出。
- 只追加记录、不重写已有记录；前置校验（源/目标/元数据/空间/重复）任一失败立即中止。
- 执行后用启动器日志验证 `suspect=0`、`Dropped 0 unplayable` 以及每个游戏的 `CanLaunch`。

## 注意事项

- 备份里的 manifest 版本通常偏旧，启动器可能提示更新。更新会验证并复用本地文件，不会全量重下；但在线游戏（Fortnite、Fall Guys 等）一般要更到当前版本才能进入。
- 个别游戏启动异常时，在库中点「管理 → 验证/修复」。
- 回滚只恢复 journal；`move` 模式下的文件位置需要手动移回。

## 回滚

```bash
python scripts/apply_restore.py --rollback "<journal>.bak-<时间戳>" --journal "<journal>"
```

回滚前同样要先关闭启动器。
