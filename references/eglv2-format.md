# Epic Games Launcher 安装数据格式与恢复原理

本文记录实际逆向/验证过的格式细节（在本机 eglv2 0.113.0 + 11 款游戏上验证通过），
供排查脚本解析异常、判断启动器版本、处理特殊游戏时使用。

## 1. 两代启动器，机制完全不同

| | 新版 eglv2 (0.113+) | 旧版 Launcher |
|---|---|---|
| 数据目录 | `%LOCALAPPDATA%\com.epicgames.eglv2` | `C:\ProgramData\Epic\EpicGamesLauncher\Data` |
| 已装游戏记录 | `installations.jsonl`（append-only JSONL 日志） | `Manifests\<GUID>.item`（单个 JSON 文件） |
| 缓存/日志 | `cache\`, `download\`, `logs\`, `settings\` | `Saved\Config`, `Saved\Logs` |
| 启动器 exe | `%LOCALAPPDATA%\Epic Games\Launcher\EpicGamesLauncher.exe`（常见） | `C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe` |

**先判断版本**：`installations.jsonl` 存在 → eglv2；只有 `ProgramData\Epic\...\Manifests` → 旧版。
本技能的脚本只支持 eglv2；旧版请走第 7 节的官方安装流程，或手工构造 `.item`。

eglv2 相关目录：
- `installations.jsonl` — 安装记录日志（唯一需要写的目标文件）
- `installations.jsonl.prev` — 上一次 compact 前的副本
- `cache\prod\account\<accountId>\library-cache.json` — 账号拥有的游戏库（含 title）
- `cache\prod\catalog-cache.json`、`sd-cache.json`、`product-mapping-cache.json` — 目录/版本元数据
- `download\queue.json` / `activity.json` — 下载队列（恢复前应为空闲）
- `logs\*.log` — 启动器运行日志（验证依据）

## 2. installations.jsonl 格式

第一行是 header，之后每行一条记录，**append-only**：同一 `installationId` 会出现多次
（partial → complete），**最后一条生效**。启动器启动时会回放日志并自行 compact、重写 header。

```
{"version":1,"writtenAt":"2026-11-08T10:50:48Z","entries":21}
{"installationId":"6DA8...","complete":true,"dir":"G:\\Game\\Epic Game\\Predecessor", ...}
```

字段与取值来源（已用已安装的 Borderlands3 / Predecessor 对照验证）：

| 字段 | 取值 | 注意 |
|---|---|---|
| `installationId` | `.egstore` 中**主** manifest 的文件名去扩展名 | 例如 `FCD8B9AD417A12FD3F746F9008F5460B` |
| `complete` | `true` | false 表示安装未完成 |
| `dir` | 游戏安装目录绝对路径 | **必须全部用反斜杠**，与已有记录风格一致；不要混用 `/` |
| `platform` | `"Windows"` | |
| `namespace` | `.mancpn` 的 `CatalogNamespace` | |
| `itemId` | `.mancpn` 的 `CatalogItemId` | |
| `artifactId` | `.mancpn` 的 `AppName` | **这是 catalog release appId，不是 manifest 里的 `AppNameString`**（后者常是构建内部名，两者可能不同） |
| `version` | manifest 的 `BuildVersionString` | 偏旧没关系，启动器会提示更新并复用文件 |
| `launchExecutable` | manifest 的 `LaunchExeString` | 相对路径，正斜杠或反斜杠均可；启动器会检查该文件是否存在 |
| `launchCommand` | manifest 的 `LaunchCommand` | 多数为空字符串 |
| `tags` | `[""]` | |
| `sidecar` | `{"revision":0}` | 已装记录可能带 `deploymentId`，新增时不必补 |
| `writtenAt` | UTC ISO8601，如 `2026-11-08T10:50:48Z` | |

可选字段（进行中的安装才有）：`pendingVersion`、`pendingTags`。
**不要手动修改 header 的 `entries`**；启动器回放时会自行更新。

写入方式：检查文件最后一个字节是否为 `\n`，不是则先补一个；随后以
`json.dumps(..., separators=(',',':'))` 追加，换行用 `\n`（`newline="\n"`，避免 CRLF）。

## 3. .egstore 内容

一个游戏安装目录内的隐藏文件夹，恢复的**关键**（缺它就必须重新下载）：

```
GameFolder\
  .egstore\
    <GUID>.manifest     主清单（文件哈希/chunk 信息 + 元数据）
    <GUID>.mancpn       与 manifest 同名的 JSON，记录 catalog 归属
    <GUID2>.manifest    可能是另一个 artifact（DLC、工具包、Assembly Kit）
    <GUID2>.mancpn
    Pending\            安装中断的临时数据
    <accountId>         0 字节占位文件
  <游戏文件...>
```

`.mancpn` 是纯 JSON（FormatVersion 0）：
```json
{ "FormatVersion": 0,
  "CatalogNamespace": "0c321ac6d5fe49139c153253b60702f4",
  "CatalogItemId": "5266dc333bb44e1f86a53fc11bc0f9e4",
  "AppName": "30d3a88d29e24716ac5a4720042289f7" }
```

**多 manifest 时如何选主游戏**：一个文件夹可能含多个 artifact。
例：Total War: WARHAMMER 的 `.egstore` 同时有
`FCD8...manifest`（2.5 MB，主游戏，库中 title = "Total War: WARHAMMER"）和
`3C0B...manifest`（164 KB，库 title = "Total War: WARHAMMER - Assembly Kit"）。
判定优先级：
1. 有同名 `.mancpn`；
2. `LaunchExeString` 非空且该 exe 在文件夹内实际存在；
3. `(namespace, itemId, artifactId)` 能在 `library-cache.json` 的 `items[].appId` 中找到；
4. 库 title 不含 `Assembly Kit / DLC / Editor / Tools / SDK / Soundtrack / Bonus / Dedicated Server / Beta / Demo` 等附加内容关键词；
5. manifest 体积最大（主游戏清单通常远大于附加包）；
6. 修改时间最新。

`library-cache.json` 索引键为 `(appId.namespaceId, appId.itemId, appId.appName)`，值取 `title`。
用它能同时验证"账号确实拥有这个游戏"和"选的是主游戏而非附加包"。

## 4. .manifest 二进制格式

少数老游戏的 manifest 是 JSON 文本（以 `{` 开头），键为
`AppNameString` / `BuildVersionString` / `LaunchExeString` / `LaunchCommand` / `FileManifestList`。
新版均为二进制：

文件头（小端）：
```
offset 0   u32  magic (0x44BEC00C 附近)
offset 4   u32  headerSize      <- body 起始位置
offset 8   u32  uncompressedSize
offset 12  u32  compressedSize
offset 16  20B  SHA1
offset 36  u8   storedAs       (bit0 = 1 表示 body 为 zlib 压缩)
offset 37  u32  version
...        padding to headerSize
```
`body = data[headerSize:]`，若 `storedAs & 1` 则 `zlib.decompress(body)`。

解压后元数据区：
```
u32  metaSize
u8   dataVersion
u32  featureLevel
u8   isFile
u32  appId
FString appName
FString buildVersion
FString launchExe
FString launchCommand
FString prereqName / prereqPath / prereqArgs / buildId ...
```

`FString` 编码：`i32 length`；
- `> 0`：length 个 UTF-8/ANSI 字节（含末尾 `\0`）；
- `< 0`：`-length` 个 UTF-16LE 字符（含 `\0`）；
- `= 0`：空串。

脚本 `scripts/epic_meta.py` 的 `parse_manifest()` 实现了以上解析。

## 5. 让启动器识别的两种方式

**A. 追加 installations.jsonl 记录（本技能默认）**
- 前置：完全关闭 `EpicGamesLauncher.exe`（用 `CloseMainWindow()`，失败再 `Stop-Process -Force`）；下载队列空闲。
- 备份 journal → 放好文件 → 追加记录 → 重启启动器。
- 启动器回放日志、检查可执行文件、计算磁盘占用，库中立即显示"已安装/可启动"。
- 无需重新下载；版本偏旧时启动器可能提示更新，更新过程会**验证并复用**已有文件。

**B. 官方安装流程（零配置风险，需手动点 N 次）**
- 只把文件放到目标目录，不改任何启动器数据。
- 在库中对每个游戏点「安装」，安装位置选同一个目标目录。
- 启动器扫描到已有文件后进入验证，仅补齐缺失/变更文件（实测 102 GB 的 Borderlands3 约 22 分钟完成，未全量重下）。
- 适用场景：旧版启动器、用户不同意修改启动器数据、或 A 方案出现异常时的兜底。

## 6. 验证成功的日志标志

重启后看 `logs\` 下最新 `.log`：

```
Replayed the installations log ... live=11 removed=0
ExecutableCheckCompleted ... count=11 suspect=0
Dropped 0 unplayable installation record(s)
ProductStateChanged ... new_state=CanLaunch     (每个游戏一条)
```

`live` 是唯一已完成安装数；`suspect=0` 表示启动程序全部检查通过；
`removed`/`Dropped` 非 0 说明有记录被丢弃，需检查 `dir`、`launchExecutable`、`installationId`。
启动器还会自动回填各游戏的磁盘占用大小。

## 7. 常见问题

- **提示"有更新"**：备份的 manifest 版本旧（例：Fall Guys `EGS_11640` vs 当前 `EGS_11958`）。直接更新即可；Epic 会做文件校验并复用未变化的文件，只下载差异。若想跳过，保持现状也能启动，但联机游戏可能需要更新。
- **某个游戏无法启动**：库中该游戏 → 「管理 → 验证/修复」；或检查 `launchExecutable` 路径大小写、是否为 32/64 位子目录。
- **游戏仍显示"未安装"**：确认启动器已完全退出才改的 journal、目录路径全反斜杠、`artifactId` 取自 `.mancpn` 而非 manifest 内部名、账号拥有该游戏。
- **写入后启动器 compact 覆盖**：正常行为，它只是把 append-only 日志压缩并重写 header，记录内容不变。
- **回滚**：关闭启动器，把 `installations.jsonl.bak-<时间戳>` 复制回 `installations.jsonl`。注意 `move` 模式的文件位置变化不会自动回滚，需手动移回。
- **中文路径/输出编码**：Python 脚本在 Windows 下先 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；在 Git Bash 中调用时避免用管道拼接中文输出。
- **跨盘移动**：`os.rename` 会失败，脚本自动回退 `shutil.move`（此时不再是瞬时操作）。同盘移动最快且不占额外空间。
- **目录联接（junction）**：`mklink /J` 让目标目录指向备份目录，两边是同一份文件；之后不能单独删除任一边，且备份目录不能清空。
