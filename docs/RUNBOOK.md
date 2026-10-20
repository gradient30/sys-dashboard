# 运行手册 · Glances 大屏监控系统

> 配套：[ARCHITECTURE.md](ARCHITECTURE.md) ｜ 日常入口：`dev.ps1`（选项式菜单）。

## 1. 日常使用

- **大屏地址**（手机收藏）：`http://100.81.213.23:61210/`（短路径；原路径
  `:61208/static/dashboard/index.html` 仍可用，行为一致）
- **磁盘清理控制台**：`http://100.81.213.23:61210/clean`（五步向导：分析→审阅→
  计划→执行→报告；大屏 header「💾 磁盘清理」按钮与「磁盘」页右上角链接均可达。仅 61210 直出）
- **统一管理**：`cd D:\workspace_test\github_desk\test\tools\memclean-svc; .\dev.ps1`
  - `[1]` 状态总览（体检） `[2]` 启动缺失 `[3]` 重启全部 `[4]` 停止
  - `[5]` 温度 writer 重启（提权） `[6]` 立即清理 `[7]` 历史库统计
  - `[8]` 打开大屏 `[9]` 日志 `[0]` 退出

## 2. 重启机器后（唯一例行操作）

两件套（Glances + memclean）自启恢复，**仅需一步**：

```
dev.ps1 → [5] 温度writer重启（UAC 确认一次）
```

不做的后果：温度卡只剩 GPU 类传感器（CPU/存储温度与 CPU 功耗消失）。
一键补齐全部：`[2]`（内含 writer 检测）。

## 3. 故障分流（按现象查）

### 大屏打不开
```
dev.ps1 [1] → 看 61210 状态；○ 停止 → [2] 启动
```

### 报"连接中断"横幅（数据不动）
Glances 挂了或网络断：`[1]` 看 61208；`○` → `[2]`。

### 温度只剩 GPU / "⚠ writer 待重启"
→ `[5]`。仍不行：查 PawnIO（`sc query PawnIO` 应 RUNNING；否则跑
`lhm\install_pawnio.ps1` 提权——详见 TECH.md §2）。

### 清理按钮报"服务不可达"
61210 死：`[2]`。报"清理失败"但服务在：查计划任务 `schtasks /query /tn MemReduct-Clean`
不存在则重装：管理员 `powershell -File D:\workspace_test\github_desk\memreduct\scripts\install_clean_task.ps1 -Test`。

### 图表变简单折线（无缩放）
ECharts 本机文件缺失（`make webui` 清空过 public/）：恢复
`dashboard\echarts.min.js`（备份：CDN 任意源 5.6.0）。
同场景若 index.html / clean.html 一并被清：dashboard/ 目录**未入 git**（untracked），
恢复依赖备份——建议在 `test\tools\memclean-svc\backup\` 留三文件副本
（index.html / clean.html / echarts.min.js），改版后随手更新。

### codex/CLI 等其它软件报 `workspace routing discovery timed out`
与本系统无关时的两步分流：① `tasklist | findstr codex`（daemon）；
② `curl -m 6 https://chatgpt.com`（代理）。详见 test/docs/codex-tui-startup-sunlen-fix.md。

### 磁盘清理 C 级报「桥未安装或触发失败」
计划任务 Glc-DiskClean-C 缺失/损坏，重装（管理员，-Test 为 dry-run 全链冒烟）：
`powershell -ExecutionPolicy Bypass -File test\tools\memclean-svc\elevated\install_disk_task.ps1 -Test`。

### 磁盘清理 C 级任务长时间 running / 900s 超时
看 `C:\ProgramData\glances-diskclean\c-run.log` 尾部定位步骤。提权 powershell 卡死且
无日志推进时：任务管理器结束 `powershell.exe`（最高权限那个），再重试。**教训（10-16 实录）：
C 级脚本禁用 Start-Transcript**——从 pythonw 无控制台服务链触发的任务会挂在 conhost 分配上
（进程活着但零输出）；日志一律走 `Add-Content` 纯文件追加（现行实现已是）。

### B 级误清恢复（E:\.trash 回收区）
`E:\.trash\<yyyyMMdd>\<原名>\` 内为完整原数据（robocopy /MOVE）。恢复：
`robocopy E:\.trash\<日期>\<名> <原路径> /E /MOVE /R:1 /W:1`，移完删空日期目录。
回收区保留 ~30 天，确认无虞后可整目录删除。

## 4. 日志与数据位置

| 内容 | 路径 |
| --- | --- |
| 清理日志（每次一行） | `%TEMP%\memclean_svc.log` |
| Glances 日志 | `%TEMP%\glances_webserver.log` |
| writer 共享文件（原子写） | `%TEMP%\lhm_temps.txt` |
| memreduct 清理结果 | `C:\ProgramData\memreduct\clean-result.json` |
| 磁盘清理审计（只追加 jsonl） | `memclean-svc\data\clean-audit.jsonl` |
| 磁盘 C 级桥三文件协议 | `C:\ProgramData\glances-diskclean\{c-params,c-progress,c-result}.json` + `c-run.log` |
| 磁盘 B 级回收区（恢复入口） | `E:\.trash\<yyyyMMdd>\<原名>\`（robocopy /MOVE 移回即恢复） |
| 持久历史库 | `memclean-svc\data\history.db`（SQLite；备份即拷文件，WAL 同拷 `-wal/-shm`） |
| 自启注册（3 处） | HKCU Run：`GlancesWeb` / `MemCleanSvc`（+ 曾有 MemReductWebUI 已弃） |

## 5. 变更发布（改动后 checklist）

1. 改 `index.html` → 刷新即生效（服务直出，无构建）；改 py → 重启 memclean
   （`dev.ps1 [3]` 或杀 PID 后 `[2]`）。
2. JS 改动跑语法检查：提取 `<script>` 段 `node --check`。
3. 页面验证（Playwright）：环境与脚本在 `test/tools/memclean-svc/verify/`
   （playwright@1.63.0 + verify_*.js 系列脚本，node 直跑；Temp 下的旧环境已弃用）。
4. 全链体检：`dev.ps1 [1]` 全 ● + 温度 15 个传感器 fresh。
