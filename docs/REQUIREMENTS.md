# 需求与特性全景 · sys-dashboard

> 按三大核心能力组织；每节含功能清单与**当前真实运行状态**（与部署形态对齐，勿凭旧文档）。
> 版本演进细节见 [ROADMAP.md](ROADMAP.md)；实现原理见 [TECH.md](TECH.md)。

## ① 监控显示（大屏）

| 特性 | 状态 |
| --- | --- |
| CPU/内存/网络/磁盘实时面板（2s/5s 分级轮询） | ✅ |
| 趋势大图 + 对比昨日（SQLite 持久历史，60s 采样，GET /history） | ✅ |
| 告警知识库（按峰值分档一句结论 + 数据参照行；清理后 10 分钟换页降级"属预期"） | ✅ |
| 传感器 tab（温度 15 / 风扇 2 / 功耗 5；GPU 用户态 + 提权 writer 合并 + ACPI） | ✅（CPU/主板风扇硬件不可读为已知边界） |
| 标题栏轮播 CPU/内存占比；断线 banner 保旧值 | ✅ |
| ECharts 三层加载链（本机 → CDN 兜底固化 → SVG 降级），零白屏 | ✅ |
| 界面风格三主题（黑/白/彩循环切换，localStorage `hud-theme` 两页共享，图表色 CSS 变量单一真源，切换不刷新） | ✅（verify 组14 断言覆盖） |
| 移动端适配（375px 无横向溢出） | ✅（verify 断言覆盖） |
| CPU 0% 坏帧净化（上游并发竞态，客户端三处守卫） | ✅ |
| 数据源 | Glances :61208（HKCU Run 自启，禁 processlist/processcount 两插件） |

## ② 内存清理

| 特性 | 状态 |
| --- |---|
| 大屏 header「🧹 清理内存」按钮（token 鉴权） | ✅ |
| MemReduct 计划任务桥（NT 层 6/8 区域，释放 1~5GB） | ⚠️ **任务 MemReduct-Clean 当前未注册**——按钮实际走 EmptyWorkingSet fallback（本地方案，释放量较小）。**2026-10-21 审计发现 fallback 路径曾因 `except as e` 块外引用 100% 崩溃**（清理已执行但响应/日志全丢，当日已修复并加回归 `verify_cleanup_api.js`）。补装：memreduct 仓 install_clean_task.ps1（见 RUNBOOK §3） |
| 清理结果 toast（引擎/释放量/区域数）+ 断连 ping 复核 | ✅ |
| 服务开机自启 | ⚠️ 未注册（dev.ps1 [2] 或手动 pythonw 起动）。候选改进见 ROADMAP |

## ③ 磁盘清理（/clean 五步向导）

| 特性 | 状态 |
| --- | --- |
| ①分析：五阶段增量直播（fs/du/b_find/docker/c_info steps + 发现 feed + 盘符水位先行） | ✅ |
| ②审阅：A/B/C 分组卡、占比条、组内排序、B 勾选实时汇总 | ✅ |
| ③计划：级别卡对比（⌂建议级）、dry-run 明细表（零写盘） | ✅ |
| ④执行：逐项状态 + C 级时间线 + sessionStorage 任务续接（刷新不丢） | ✅ |
| ⑤报告：释放大数字 + 盘符水位前后对比（live_fs 实测）+ 审计提示 | ✅ 端到端三方一致实测（报告=水位差=审计行） |
| A 级（纯缓存静默清，>3 天判定） | ✅ |
| B 级（30 天未动 robocopy /MOVE 入 `E:\.trash\<yyyyMMdd>\`，可回滚） | ✅（E 盘预校验） |
| C 级（VHDX 压缩 + pagefile 迁移，模态中断声明门禁） | ⚠️ **桥任务 Glc-DiskClean-C 当前未注册**——安装走 `elevated\install_disk_task.ps1`（-Test 冒烟） |
| 安全：默认 dry-run、保护名单扫描+执行双查、Docker 绝不 --volumes、token 鉴权、任务服务端保留 30 分钟 | ✅（verify 13 组断言覆盖） |
| 大屏入口 ×2：header「💾 磁盘清理」按钮 + 磁盘页链接（非 61210 端口自动切绝对地址） | ✅ |

## 非功能需求

- 停机恢复：Glances 自启；本服务/writer 手动（dev.ps1）；SQLite WAL 随 data/ 整体迁移
- 审计：磁盘清理全程 clean-audit.jsonl（只追加）
- 占用：常驻子进程复用、轮询分级、图表实例复用（无泄漏，v3.6 审计）
- 验证防线：verify/ 六脚本（Playwright），改前端必跑 verify_disk_clean.js

## 2026-10-20 迁仓基线

本仓自此为唯一真源（原 `test/tools/memclean-svc/` + `glances/.../dashboard/` 已迁并删除）。
端口不变（61210 服务 / 61208 数据源）；跨仓耦合与 make webui 清空风险消除。
