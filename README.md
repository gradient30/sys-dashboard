# sys-dashboard · 系统监控大屏

单机 Windows 系统监控与清理平台：**监控显示 + 内存清理 + 磁盘三级清理** 三大能力，
由一个用户态 Python 服务（:61210）直出两个零构建单文件 Web 应用（大屏 + 磁盘清理向导）。
数据源为本机 Glances webserver（:61208，上游只读依赖）。

## 三大核心能力

| 能力 | 入口 | 说明 |
| --- | --- | --- |
| ① 监控显示 | `http://<host>:61210/` | HUD 暗色琥珀风大屏：CPU/内存/网络趋势（持久历史 SQLite）、告警知识库、传感器（温度/风扇/功耗）、连接与磁盘面板；移动端适配 |
| ② 内存清理 | 大屏 header「🧹 清理内存」 | 优先 MemReduct 计划任务桥（NT 层 6/8 区域），任务未注册时回退 EmptyWorkingSet 本地方案 |
| ③ 磁盘清理 | `http://<host>:61210/clean` | 五步向导（分析直播→审阅→计划→执行→报告）；三级安全分级：A 纯缓存 / B 30 天未动移入 `E:\.trash\` / C 系统级提权（VHDX 压缩+pagefile 迁移）；默认 dry-run、释放量只认 disk_usage 前后差值 |

需求与特性全景见 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)。

## 快速上手

```powershell
cd <本仓库 clone 位置>          # 本机为 D:\workspace_test\github_desk\sys-dashboard
.\dev.ps1          # 选项式菜单：[1] 状态总览 [2] 启动缺失 [3] 重启 [5] 温度 writer 提权重启 …
# 或手动起服务：pythonw memclean_svc.py（需 Python ≥3.10 + psutil）
```

重启机器后：Glances 数据源自启（HKCU Run）；本服务与温度 writer 需 `dev.ps1 [2]`（后者 `[5]` 需一次 UAC）。

**部署到新机器**：① Python ≥3.10 + `pip install psutil`；② 数据源：本机跑 Glances webserver
（:61208，见下表——页面从 :61210 打开时请求 `同一主机:61208`；无本机实例时改
`web/index.html` 的 `API_BASE`（搜索 `61208`）指向远端）；③ 建议修改清理 TOKEN
（`memclean_svc.py` 与两页面 JS 三处同步，见 CLAUDE.md）；④ CPU/存储温度需 PawnIO 驱动
（`lhm/install_pawnio.ps1`，TECH §2）。

## 目录结构

```
sys-dashboard/
├─ memclean_svc.py   HTTP 服务（:61210）：静态直出 web/ + 清理/温度/历史/磁盘 API
├─ diskclean.py      磁盘三级清理引擎（扫描/任务/审计/C 级桥客户端）
├─ lhm_writer.py     提权传感器 writer（管理员 pythonw，写 %TEMP%\lhm_temps.txt）
├─ dev.ps1           统一管理菜单
├─ web/              前端三件：index.html（大屏）/ clean.html（清理向导）/ echarts.min.js（本机托管）
├─ docs/             ARCHITECTURE / TECH / RUNBOOK / ROADMAP / REQUIREMENTS
├─ elevated/         C 级提权：install_disk_task.ps1 + disk-clean-c.ps1（$PSScriptRoot 自洽）
├─ lhm/              传感器资产：glances_lhm.exe/.cs、beszel_lhm.exe、pawnio-x64 驱动三件套
├─ verify/           Playwright 页面验证（node verify_disk_clean.js 等，浏览器在 %LOCALAPPDATA%\ms-playwright）
└─ data/             运行数据（gitignore）：history.db 持久历史 + clean-audit.jsonl 审计
```

## 外部依赖（不在本仓、勿改）

| 依赖 | 位置 | 用途 |
| --- | --- | --- |
| Glances | `D:\workspace_test\github_desk\glances`（:61208，HKCU Run 自启） | 全部监控数据源；启动必带 `--disable-plugin processlist,processcount`（EDR 环境） |
| memreduct 方案 | `D:\workspace_test\github_desk\memreduct`（只读引用） | mem-clean.ps1 + install_clean_task.ps1（内存清理桥方案；任务当前未注册） |
| LibreHardwareMonitor | `D:\workspace_test\github_repo\LibreHardwareMonitor`（只读） | LHM 库与 PawnIO 驱动来源（编译产物已入本仓 lhm/） |
| beszel | `D:\workspace_test\github_repo\beszel`（只读） | lhm 壳最初来源（beszel_lhm.exe 备用） |
| 机器绑定路径 | `E:\.trash\`、`C:\ProgramData\glances-diskclean\`、`C:\ProgramData\memreduct\` | B 级回收区 / C 级三文件协议 / 清理结果 |

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | 需求/特性全景（按三大核心组织，含真实运行状态） |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 组件与数据流图、端口、路径、协议速查 |
| [docs/TECH.md](docs/TECH.md) | 技术决策与实现细节、已知坑清单 |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | 运行手册：日常/恢复/故障分流/发布 checklist |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 版本史、设计原则、候选方向 |

## 沿革

2026-10-20 自两处独立仓库迁并为本仓（git 化统一管理）：
服务端原 `test/tools/memclean-svc/`、前端原 `glances/outputs/static/public/dashboard/`。
迁出后 Glances `make webui` 清空 public/ 的连带风险、跨仓目录耦合均告消除；页面行为与端口（61210/61208）不变。
