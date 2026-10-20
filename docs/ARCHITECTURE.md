# 架构总览 · sys-dashboard（系统监控大屏）

> 配套文档：[TECH.md](TECH.md) 技术细节 ｜ [RUNBOOK.md](RUNBOOK.md) 运行手册 ｜ [ROADMAP.md](ROADMAP.md) 演进与路径 ｜ 本文档维护组件与数据流。

## 1. 系统组成（数据流图）

```
                        ┌────────────────────────── 浏览器（手机/PC，Tailscale 网内） ──────────────────────────┐
                        │  http://100.81.213.23:61210/   （唯一入口；原 61208 路径已随迁仓移除）                │
                        └───────┬───────────────────────┬──────────────────────────────┬──────────────────────┘
                                │ 页面+静态资源           │ POST /cleanup/<token>        │ GET /temps /history
                                │                        │ POST /disk/clean/<token>     │ GET /disk/scan /disk/task
                                ▼                       ▼                              ▼
┌─ memclean_svc.py（:61210，用户态常驻；dev.ps1 [2]/手动起动，未注册自启）────────────────────────────────┐
│  ① 静态入口 GET /  /clean    → web\index.html + echarts.min.js；/clean=清理控制台 web\clean.html        │
│  ② 清理   POST /cleanup/…    → schtasks /run MemReduct-Clean（提权桥）→ 轮询结果 JSON；失败回退        │
│                                    EmptyWorkingSet（psutil+ctypes 本地方案）                          │
│  ③ 温度   GET /temps         → LhmReader（用户态子进程 GPU 传感器）∪ 管理员 writer 共享文件（去重）     │
│                                    ∪ ACPI 热区（perf counter 后台预刷）                                │
│  ④ 历史   GET /history?h=N   → data/history.db（SQLite WAL，recorder 线程 60s 采样）                   │
│  ⑤ 磁盘   /disk/*            → diskclean.py：Scanner 线程（600s 缓存扫描 A/B/C 分级清单+建议级别）；  │
│                                    POST commit 起异步任务（A 缓存删除/B robocopy 入 E:\.trash\C 桥）； │
│                                    C 级 → schtasks /run Glc-DiskClean-C（提权桥，三文件协议）          │
└──────┬───────────────────────────────────────────────────────────────────────────────────────────────┘
       │ (页面数据主源)                              │ (CPU/存储/功耗传感器)              │ (清理结果)
       ▼                                             ▼                                    ▼
┌─ Glances（:61208）┐              ┌─ lhm_writer.py（管理员 pythonw 常驻）┐   ┌─ 计划任务 MemReduct-Clean ┐
│ python -m glances │              │  常驻 glances_lhm.exe 子进程          │   │ → memreduct/scripts/      │
│ -w --disable-     │              │  10s 轮询 getSensors（T/F/P 三类）   │   │   mem-clean.ps1           │
│ plugin processlist,│             │  原子写 %TEMP%\lhm_temps.txt         │   │   （NT 层 8 区域清理）    │
│ processcount ★    │              └──────────────┬───────────────────────┘   └───────────────────────────┘
└───────────────────┘                             ▼
                                    ┌─ glances_lhm.exe（自有封装，调 LHM 库）─ PawnIO 内核驱动 → CPU MSR/
                                    │  LibreHardwareMonitorLib.dll 0.9.6     SuperIO/NVAPI/SMART 传感器
                                    └────────────────────────────────────────
```

★ `--disable-plugin processlist,processcount` 为本机必带（EDR 环境全进程扫描 ~19.5s/轮会拖垮一切端点）。

## 2. 组件与端口

| 组件 | 端口/形态 | 权限 | 重启机器后 | 代码位置 |
| --- | --- | --- | --- | --- |
| Glances 数据源 | :61208 | 用户 | 自启（HKCU Run `GlancesWeb`） | 上游 glances 仓库（零改动） |
| memclean 服务 | :61210（页面+API） | 用户 | 手动（dev.ps1 [2]） | `sys-dashboard/memclean_svc.py` |
| LHM writer | 提权常驻 pythonw | **管理员** | ⚠️ 手动：`dev.ps1 [5]` | `lhm_writer.py` |
| glances_lhm.exe | 被 writer/服务调用 | — | 随调用方 | `lhm/glances_lhm.cs/.exe` |
| 清理桥 | 计划任务 MemReduct-Clean | RunLevel=Highest | **未注册**（清理走 fallback） | memreduct 仓库 `scripts/mem-clean.ps1`（只读引用） |
| 磁盘清理引擎 | memclean 内线程（diskclean.py） | 用户 | 随 memclean | `sys-dashboard/diskclean.py` |
| 磁盘 C 级桥 | 计划任务 Glc-DiskClean-C | RunLevel=Highest | **未注册**（需要时安装） | `sys-dashboard/elevated/disk-clean-c.ps1`（VHDX 压缩+pagefile 迁移；安装 `elevated/install_disk_task.ps1`） |
| PawnIO 驱动 | Root\PawnIO 设备 | 内核 | 持久 | `lhm/pawnio-x64/`（源：LHM 仓库安装器） |
| 大屏页面 | 静态（61210 直出） | — | — | `sys-dashboard/web/index.html` |
| 磁盘清理控制台 | 静态（61210 直出 `/clean`） | — | — | `sys-dashboard/web/clean.html`（五步向导：分析→审阅→计划→执行→报告，零构建零依赖；大屏入口 ×2：header「💾 磁盘清理」按钮 + 「磁盘」页链接） |
| 管理入口 | dev.ps1 菜单 | 用户（[5][6]需 UAC） | — | `sys-dashboard/dev.ps1` |

## 3. 关键文件路径

```
D:\workspace_test\github_desk\sys-dashboard\     ← 独立仓库（本仓，服务大本营）
├─ memclean_svc.py        全部服务端逻辑（HTTP/清理/温度/历史/静态）
├─ diskclean.py           磁盘三级清理引擎（扫描/执行/审计/C 桥客户端）
├─ lhm_writer.py          提权温度/传感器 writer
├─ dev.ps1                选项式统一管理菜单
├─ web\                   前端三件（静态直出根：DASH_DIR）
│  ├─ index.html             大屏单页应用（HUD 风，零构建）
│  ├─ clean.html             磁盘清理控制台（/clean，五步向导，零构建零依赖）
│  └─ echarts.min.js         本机图表库（5.6.0）
├─ elevated\disk-clean-c.ps1      C 级提权执行体（VHDX 压缩+pagefile 迁移）
├─ elevated\install_disk_task.ps1 C 级桥任务安装/卸载/冒烟（-Test 为 dry-run 全链）
├─ data\history.db        持久历史库（SQLite WAL；另有 -wal/-shm 随迁移）
├─ data\clean-audit.jsonl 磁盘清理审计日志（只追加）
├─ verify\                页面验证（Playwright；node_modules 不入库）
├─ docs\                  本文档集
│
│ 磁盘清理外部路径（跨盘/系统区）：
├─ E:\.trash\<yyyyMMdd>\     B 级回收区（robocopy /MOVE 移入；人工保留 ~30 天后可清）
└─ C:\ProgramData\glances-diskclean\   C 级桥三文件协议
   ├─ c-params.json    服务(用户态)原子写：任务参数（actions/dry_run/pagefile 配置）
   ├─ c-progress.json  提权任务写：步骤级进度（steps[{name,state}]）
   ├─ c-result.json    提权任务写：终态（ok/errors/vhdx/pagefile/docker_recovered）
   └─ c-run.log        提权任务纯文件日志（禁 Start-Transcript，见 RUNBOOK 故障分流）
└─ lhm\
   ├─ glances_lhm.cs/.exe 自有 LHM 封装（T/F/P 协议）
   ├─ beszel_lhm.exe      原版壳（备用兼容）
   ├─ pawnio-x64\         x64 驱动三件套（sys/inf/cat）
   ├─ install_pawnio.ps1  SetupAPI 重建 Root 设备（修复用）
   └─ restart_writer.ps1  提权重启 writer

参考项目（独立，只读引用，勿改）：
D:\workspace_test\github_repo\LibreHardwareMonitor\   采集引擎源+指南（docs/temperature-sensing-guide.md）
D:\workspace_test\github_desk\memreduct\              清理方案（scripts/mem-clean.ps1 + 计划任务桥）
D:\workspace_test\github_repo\beszel\                 LHM 壳的最初来源（agent/lhm/beszel_lhm.cs）
```

## 4. 协议速查

- **glances_lhm.exe**：stdin 行命令 → stdout 行结果，空行结束。`getTemps`=旧协议 `name|value`（仅温度）；`getSensors`=新协议 `T|name|v` / `F|name|rpm` / `P|name|watt`。
- **共享文件** `%TEMP%\lhm_temps.txt`：writer 原子写（tmp+replace） getSensors 原始行；服务 30s 新鲜度窗口读入。
- **/cleanup 响应**：`{engine: "memreduct"|"fallback", freed_gib, before_gib, after_gib, duration_ms, regions{区域:状态}}`（memreduct 路径）；fallback 路径含 `trimmed/killed_orphans`。
- **/temps 响应**：`{ok, temps:[{name,value,kind:"T|F|P"}], writer_fresh:bool}`。
- **/history 响应**：`{points:[{ts(ms),cpu,mem,swap,t_cpu,t_gpu,net_rx,net_tx,p_cpu,p_gpu,fan}]}`。
- **磁盘清理三级协议**（token 同 `/cleanup`）：
  - `GET /disk/scan[?fresh=1]` → `{state, progress:{phase,pct,steps:[{name,label,state:wait|run|done|fail,note}]×5}, fs:[…], suggest:{…}, items:[…], c_env:{…}}`（600s 缓存，fresh 置脏并唤醒扫描线程）。**增量发布**：扫描期间 `progress.steps` 五阶段（fs/du/b_find/docker/c_info）实时推进；无缓存的首扫为 `state:"scanning"` 并透传 `partial:true` + 中间 `items`（append-only，单项发布即终值）；有旧缓存时 `state` 恒 `ready`、`items` 恒旧终值，"正在重扫"信号 = `progress.phase ∉ {ready,idle}`。重扫进行中额外透传 `live_fs`（fs 阶段实测水位）——清理报告页"后水位"专用（fs 键此时仍是旧值，勿混用）。
  - `POST /disk/clean/<token>` body `{level, commit, b_paths[], c_opts{vhdx,pagefile}}`：commit=false 同步返回 dry-run 计划（零写盘）；commit=true → `202 {task_id}`（异步；429=互斥占用，400=参数/安全拒绝）。B 勾选可为空（只做 A+C）。
  - `GET /disk/task/<token>/<id>` → `{task:{phase:validate→a→b→c_bridge→done, items:[{key,status,freed_bytes}], c_progress:{steps,docker_recovered}, freed_bytes, ok}}`（终态保留 30 分钟）。
  - 红线：Docker 仅 `image/builder prune`（绝不 `--volumes`）；B 级只移 `E:\.trash\`；保护名单扫描+执行双查（防 TOCTOU）；释放量以 `disk_usage` 前后差值为准。
- **页面 API 双模式**：`location.port==="61210"` 时 API 绝对指向 `:61208`（Glances CORS 默认全开），否则同源相对路径（为经其他静态服务打开的场合保留）。页面入口唯一：61210 直出（`/` 与 `/clean`）；大屏两处清理入口（header `#btn-disk` 按钮、磁盘页 `#dc-console` 链接）在非 61210 端口下自动切绝对地址。
