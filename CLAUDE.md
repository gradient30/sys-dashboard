# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

单机 Windows 监控与清理平台：`memclean_svc.py`（:61210）直出 `web/` 下两个零构建单文件应用，三大能力 = 监控显示 + 内存清理 + 磁盘三级清理。数据源是本机 Glances webserver（:61208，外部只读依赖）。中文文档齐全（docs/），改组件行为后同步更新 ARCHITECTURE §2 表与 RUNBOOK §4 路径表。

## 常用命令

```powershell
.\dev.ps1                      # 选项式管理菜单（[1] 状态 [2] 启动缺失 [3] 重启 [5] writer 提权 [8] 开大屏）
C:\Python\Python311\pythonw.exe memclean_svc.py    # 手动起服务
```

```bash
cd verify && node verify_disk_clean.js        # /clean 向导 13 组断言（--no-dash 跳大屏组）
cd verify && node verify_long_stability.js    # 长稳/泄漏
```

验证环境注意：Playwright node_modules 在本仓 verify/（不入库）；浏览器用 %LOCALAPPDATA%\ms-playwright\chromium-1234 回退（脚本内已带 executablePath）。跑前若系统 commit 高先触发一次内存清理（Node OOM 风险）。

## 文件地图

- `memclean_svc.py` — 全部服务端逻辑：静态直出（DASH_DIR=`<repo>/web`）、/cleanup /disk/* /temps /history API、CLEAN_TOKEN（token 在 URL path）
- `diskclean.py` — 磁盘三级清理引擎：Scanner（增量发布五阶段 steps + partial + live_fs）、异步任务、审计 jsonl、C 级桥客户端
- `web/index.html` — 大屏（HUD 风，ECharts 三层加载链）；`web/clean.html` — 磁盘清理五步向导
- `lhm/` — 传感器封装（glances_lhm.exe、PawnIO 驱动）；`lhm_writer.py` — 提权 writer（共享文件 %TEMP%\lhm_temps.txt）
- `elevated/` — C 级提权执行体与任务安装（$PSScriptRoot 自定位）
- `data/` — 运行数据（gitignore；history.db SQLite WAL + clean-audit.jsonl 只追加）

## 硬约束

- **前端单文件零构建零 CDN 依赖**：web/ 下不引构建工具；echarts.min.js 本机托管，全链降级不白屏。
- **TOKEN 双处同步**：memclean_svc.py 内 `TOKEN` 与 web/index.html、web/clean.html 的 `CLEAN_TOKEN` 必须一起改。
- **安全红线**：磁盘清理默认 dry-run；Docker 仅 image/builder prune（绝不 --volumes）；B 级只移 `E:\.trash\`；PROTECTED 名单含本仓父目录 `D:\workspace_test`（扫描+执行双查防 TOCTOU）；释放量只认 disk_usage 前后差值。
- **机器绑定路径非 bug**：E:\.trash、C:\ProgramData\glances-diskclean、VHDX/Docker 路径等是本机部署形态，勿"修复"。
- **系统侧现状**（2026-10）：HKCU Run 仅 GlancesWeb 自启；计划任务 MemReduct-Clean 与 Glc-DiskClean-C 均未注册（内存清理走 EmptyWorkingSet fallback、C 级桥待装）——这是现状不是故障，补装走 RUNBOOK §3。
- **data/ 勿手编**；历史库改表结构用 ALTER 迁移（先例见 v3.2）。
- 提权脚本禁 Start-Transcript（conhost 挂死，TECH.md 坑清单）。
