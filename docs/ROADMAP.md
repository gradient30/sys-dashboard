# 演进与路径 · Glances 大屏监控系统

> 配套：[ARCHITECTURE.md](ARCHITECTURE.md) ｜ 记录版本史与已排期的方向，供后续迭代者接续。

## 1. 版本史（2026-10-02 → 10-16）

| 版本 | 日期 | 内容 |
| --- | --- | --- |
| v1.0 | 10-02 | 初版单文件大屏（深蓝 Grafana 风，自绘 SVG sparkline）；设计/实施文档 docs/plans |
| v1.1 | 10-03 | 修数据不显示：根因 EDR 下进程扫描 19.5s/轮（服务端禁两插件 + 前端每端点独立降级） |
| v2.0 | 10-03 | ECharts 三层加载链 + 趋势大图（服务端 history）+ 告警面板 + 连接/磁盘面板；一致性验证 32/32 |
| v2.1 | 10-06 | ？帮助提示（8 卡+6 tab）+ 真实清理按钮（EmptyWorkingSet 方案 + HKCU Run 自启） |
| v2.2 | 10-07 | LHM 温度接入（beszel 壳；GPU 用户态 + 管理员 writer 并入存储温度） |
| v2.3 | 10-07 | **CPU 温度打通**：PawnIO 驱动修复（解包/入库/SetupAPI 重建 Root 设备）；15 传感器 |
| v2.4 | 10-07 | 质量加固：writer 常驻化+原子写+自愈、/temps 恒缓存、SQLite 持久历史、对比昨日 |
| v3.0 | 10-08 | 清理引擎切换 **MEM.REDUCT 同源**（计划任务桥→mem-clean.ps1，8 区域 NT 层）；HUD 换肤 |
| v3.1 | 10-08 | 短路径 `:61210/`（双模式 API）；磁盘表改剩余/总量；清理 ？ 说明（6/8 vs 8/8 风险） |
| v3.2 | 10-08 | 传感器扩展：自有 glances_lhm.exe（温度+风扇+功耗）、传感器 tab、历史库 p_cpu/p_gpu/fan |
| v3.3 | 10-08 | 修复 CPU 间歇 0.0%：Glances Webserver 并发竞态坏帧，客户端三处净化（判据 total=0 且 idle=0）+ 历史库清洗 137 行 |
| v3.4 | 10-08 | 告警面板知识库：按峰值分档一句结论（色区分）+ 一行参照（阈值/口径换算）；规避 global_msg 串位 |
| v3.5 | 10-09 | 标题栏轮播 CPU/内存 占比（4s 切换、≤9 字符；断线覆盖为警示） |
| v3.6 | 10-09 | 泄漏审计 + 图表实例复用全面化（iohist/hbar）：60s 绑定 198→9、3 分钟 heap 锯齿无增长；验证环境迁入 `verify/` |
| v3.7 | 10-10 | 清理联动：MEMSWAP 告警清理后 10 分钟降级"属预期"；fetch 断连 ping 复核修"服务不可达"误报 |
| v3.8 | 10-16 | **磁盘一键清理（三级安全分级）**：diskclean.py 引擎（600s 缓存扫描+异步任务+审计）+ C 级提权桥 Glc-DiskClean-C（VHDX 压缩+pagefile 迁移）；B 级 robocopy 入 `E:\.trash\`；Playwright 10/10 |
| v3.9 | 10-16 | 磁盘管理拆分：清理面板独立「清理」页签（磁盘页恢复纯监控），懒加载扫描（进页才取，替代首载无条件 4s） |
| v4.0 | 10-19 | **磁盘清理控制台 `/clean`（五步向导）**：Scanner 增量发布（五阶段 steps + partial append-only，fresh 即时唤醒；`live_fs` 实时水位）；clean.html 独立页（分析直播→审阅分组→级别计划→执行时间线→报告水位差；sessionStorage 任务续接）；大屏收口为入口链接；verify_disk_clean 13 组全绿；端到端 A 级实测：报告释放 512MB = 前后水位差 +0.5G = 审计 jsonl 新增 7 行一致 |

同期的旁支交付（同仓库生态）：dev.ps1 统一管理、值班柜登记（test 项目 C-02 等）、
codex 启动问题修复文档（test/docs/）。8765 webui 曾并入后按用户决策摒弃（零依赖）。

## 2. 设计原则（迭代请遵守）

1. **上游零改动**：glances / LibreHardwareMonitor / memreduct / beszel 仓库只读引用。
   参考其实现时拷贝到本项目改造（先例：glances_lhm.cs、pawnio-x64）。
2. **单文件前端**：零构建零 CDN 依赖；图表库三层链兜底。
3. **低占用**：常驻子进程复用（不做重复硬件枚举）、慢操作后台化、轮询分级（2/5/60s）。
4. **降级优先**：任何依赖缺失不得白屏——每端点/每数据源独立降级 + 可见提示。
5. **数据一致性**：数值展示与入史同口径（先例：温度卡 CPU Package 对齐）；改动后
   重跑断言（verify 脚本方法论见 TECH.md §5）。
6. **色板合规**：数据图形色须经 dataviz validator（表面 #10161d）；品牌琥珀仅装饰。

## 3. 已知边界（现状如实）

- CPU/主板风扇传感器本机硬件不可读（仅 GPU Fan）。
- LibreHardwareMonitorLib 无法从本仓库源码重编（master 仅维护 net8+；重编需 net9
  SDK + 壳同步升级 net8——排期见下）。
- EDR 环境制约：进程扫描禁用（无进程 Top）、schtasks 手动建被拒（官方脚本可过）、
  首次装包偶发 os error 5（重试/复用已装包）。

## 4. 候选方向（按价值粗排，未排期）

1. **功耗趋势可视化**：历史库已有 p_cpu/p_gpu/fan 列——趋势 modal 增加"功耗"维度
   与对比（数据已在积累，纯前端工作）。
2. **告警通道**：温度/内存阈值推送（Tailscale 侧无推送服务；可经 ntfy/自建 webhook）。
3. **verify.js 工程化**：一致性断言脚本从临时目录迁入仓库 + 定时跑（回归防线）。
4. **LHM 工具链升级**：net9 SDK + 壳升 net8 + 从源码构建库（解锁最新传感器支持；
  现用官方 0.9.6 dll 已稳定，非必需）。
5. **移动 PWA**：manifest + service worker（可安装到主屏、离线壳）。
6. **多机扩展**：memclean_svc 参数化目标机（agent 化），面板多机切换。
7. **上游反馈**：Glances Webserver 并发请求致 CPU 0% 坏帧（`__update_stats` 无锁 + psutil 非阻塞
   差值竞态，见 TECH.md §6.8）——可提 issue/PR（stats.update 串行化或插件级锁）。

## 5. 交接备忘

- 所有运维细节以 [RUNBOOK.md](RUNBOOK.md) 为准；修改组件行为后同步更新
  ARCHITECTURE §2 表与 RUNBOOK §4 路径表。
- 会话记忆（Claude Code auto-memory）存有等价要点，文档为准。
