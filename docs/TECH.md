# 技术细节 · Glances 大屏监控系统

> 配套：[ARCHITECTURE.md](ARCHITECTURE.md) ｜ 本文面向迭代开发者，记录每个技术决策的"为什么"与已知坑。

## 1. 前端（index.html，单文件 ~1800 行，零构建）

- **单文件约束**：Tailscale 内网 + 离线可用，无 CDN 依赖（手机弱网/断网也能开）。
- **ECharts 三层加载链**：本机 `./echarts.min.js`（主）→ CDN 兜底（bootcdn→cdnjs→jsdelivr，
  成功后源码固化 localStorage）→ SVG 自绘降级（数据不丢，图表简化）。渲染器抽象
  `Charts.{spark,bigLine,hbar,pulse}`，实例复用（setOption，勿退回每轮 init/dispose）。
  **v3.6 全面复用**：iohist/hbar 曾每轮 init/dispose（iohist 每 3s、60s 28 次 init/198 次
  DOM 绑定）→ 与 spark 同款"实例在 `Charts.live`、容器仅在缺失时重建"模式；实测修复后
  60s 绑定新增 198→9（仅剩 io 历史偶发一次超时后自愈重建一次，init/dispose 成对，非泄漏）。
  唯一保留每次重建的是 trend modal 的 bigLine（打开/关闭配 dispose，低频合理）。
- **数据层设计**：`pollMain`（2s，9 轻端点）+ `pollTab`（激活才拉，3s/进程 5s）+
  `pollLhmTemps`（5s）。历史入缓冲与渲染分离：`HIST_RULES.<ep>.pick` 在**数据到达时**
  入环形缓冲（120 点），`renderMain` 只读——不要在渲染层 push（会双写）。
- **每端点独立降级**：fetch 带 `AbortSignal.timeout`（API 6s / temps 12s——冷调用含
  子进程+热区查询需 5-10s，勿调小）。端点 400/404 自动移出轮询（插件被禁用场景）。
- **双模式 API**：`API_BASE` 按端口切换（见 ARCHITECTURE §4）。新增 fetch 一律走 `fetchJSON`。
- **标题栏轮播**（v3.5）：`document.title` 每 4s 切换 `CPU xx.x%` / `内存 xx.x%`（≤9 字符
  不截断，多标签页可瞄读）；断线时被 setConn 覆盖为「⚠ 连接中断」。
- **HUD 设计 token**：`#0a0e12` 底+网格纸+扫描线+暗角、琥珀 `#ffb000` 品牌色（仅装饰：
  标题/LED/按钮/角标，**不作数据系列色**）。图表系列色是经 dataviz validator 验证的
  暗色组 `#c2850a/#2a9db8/#c94f4f/#9b7fd4/#c96f10`（表面 `#10161d`），每核 3 档青阶
  `#1a5f76/#2a9db8/#8fd7e6`。改配色须重新跑 validator（bundled-skills dataviz）。
- **温度卡口径**：主值 = CPU Package（回退 Core Max → 最热 CPU 核）；sparkline 入史
  同口径（HIST_RULES.sensors.pick 已对齐）。副标题按 `RANK` 排序（未列入者排最后）。
- **`?` 帮助**：hover + click/tap 双通道（触屏无 hover）；`.help` 点击须 `stopPropagation`
  （否则触发卡片趋势 modal）。
- **告警面板知识库**（v3.4）：事件数值口径随类型不同（LOAD=每核 load 比 min5/cpucore，
  MEM/SWAP/CPU=%）。`ALERT_KB` 按类型给**一句结论**（按峰值分档：轻微可忽略/中等 med/
  重度 bad，颜色区分）+ 一行数据参照（阈值经 /api/4/<p>/limits 核实：load 0.7/1.0/5.0
  每核、mem/swap/cpu 50/70/90%）。**勿退回长段落**（用户明确要求简洁结论优先）。上游
  `global_msg` 是全局决策树快照会与条目类型**串位**，已弃用不展示。
- **清理与 MEMSWAP 的联动**（v3.7）：清理（工作集修剪）会把进程私有冷页搬入 pagefile →
  swap 占用骤升是**必然副作用非故障**（实测 mem 83→44%、swap 3→85% 同分钟发生）。
  两处防护：① `ALERT_KB.MEMSWAP.concl` 在 `cleanLast` 后 10 分钟内降级显示
  "清理后换页 · 属预期，回落中"；② `runCleanup` fetch 断连（清理 pagefile 写入风暴会掐
  断连接）先探测 `/ping`，服务在线则判"清理已在后台执行"（勿报"服务不可达"误报），
  并同样计入 `cleanLast`（驱动降级窗口 + 30s 冷却）。

## 2. 传感器链（核心难点）

- **引擎**：LibreHardwareMonitorLib.dll 0.9.6（官方发布产物；本仓库 master 源码仅
  维护 net8+，net472/netstandard2.0 目标上游已编不过——重编译需 net9 SDK+全链升级，
  无功能增益，见 ROADMAP）。
- **自有壳** `lhm/glances_lhm.cs`：改自 beszel_lhm.cs（85 行），新增 getSensors 三类
  输出。**编译**：系统 csc（`Framework64\v4.0.30319`）——**只支持 C#5，禁用 `$""`
  插值字符串**（用拼接/string.Format）：
  `csc -target:exe -platform:x64 -out:glances_lhm.exe -r:LibreHardwareMonitorLib.dll glances_lhm.cs`
- **权限矩阵**（实测本机）：GPU 温度/风扇/功耗=用户态可读；CPU 温度、CPU 功耗、存储
  温度=需管理员。故双通道：服务内用户态子进程（GPU 兜底）+ 提权 writer（全量）。
- **PawnIO 驱动**：CPU 传感器必需；**缺失时 LHM 静默失败（值恒 null 无报错）**——
  排障第一嫌疑。修复件：`lhm/pawnio-x64/`（从 LHM 仓库安装器解包：7z→内嵌 CAB→
  `-aou` 分同名→PE Machine 判 x64）+ `install_pawnio.ps1`（SetupAPI 重建 Root\PawnIO）。
- **本机硬件边界**（如实）：CPU/主板风扇不可读（SuperIO 未暴露）；GPU 风扇低载 0 转
  正常；主板 ACPI 热区 tz00 ≈ 27°C 是机箱温度非核温。
- **writer 健壮性**：常驻子进程（避免每轮 Computer.Open 硬件枚举）、查询 10s 超时看护
  （卡死自动杀+重建）、**原子写**（tmp+os.replace，杜绝读方见半行）。

## 3. 清理链（MEM.REDUCT 同源）

- 路径：页面按钮 → 61210 → `schtasks /run /tn MemReduct-Clean`（RunLevel=Highest，
  免 UAC）→ `memreduct/scripts/mem-clean.ps1`（NT 层 8 区域：NtSetSystemInformation
  全套）→ 结果文件 `C:\ProgramData\memreduct\clean-result.json`（轮询 mtime 变化读取）。
- 默认 6/8 区域（memreduct 官方默认 mask 0xE7）；8/8 的两个激进项（备用列表全清/
  修改列表强制刷盘）副作用见页面 `?` 说明。切换 8/8：改桥任务参数加 `-All`。
- 兜底：桥不可用时回退 `do_cleanup()`（EmptyWorkingSet 全进程 + 孤儿 cmd/pwsh 终止）。
- **互斥**：服务端 `_lock` 非阻塞（并发 429）；页面 30s 冷却。

## 4. 历史库（SQLite）

- `data/history.db`，WAL 模式，recorder 线程 60s 采样（Glances API + 本地 temps 缓存）。
- 列：ts(PK 秒) cpu mem swap t_cpu t_gpu net_rx net_tx p_cpu p_gpu fan（新列经
  ALTER TABLE 迁移，勿依赖列序——SELECT 显式列名）。
- 体量：~1.4k 行/天 ≈ 50MB/年。趋势 modal "对比昨日"取 `hours=50`（今日+昨日窗口）。
- 多线程注意：服务主连接 `_db`（/history 持 `_rec_lock`）与 recorder 自建连接分离。

## 5. 服务端骨架（memclean_svc.py，纯标准库+psutil）

- `ThreadingHTTPServer`（并发不互相阻塞）；`_json` 统一 CORS `*`（页面跨 61208/61210）。
- 慢操作全部后台化：热区预刷线程 30s、recorder 60s；`/temps` 恒缓存读（3s）。
- 安全：cleanup 需路径 token（与页面 JS 同步改）；仅绑定 Tailscale 可达端口。
- **磁盘扫描增量发布（v4.0）**：Scanner 五阶段（fs/du/b_find/docker/c_info）steps 实时推进；
  `_partial` 仅扫描期间非 None 且 items **append-only**（单项发布即终值，前端 feed 只增不改）；
  读写均经 `_lock` 内整表赋值交换（du 等慢 IO 全在锁外，临界区纯赋值）；`fresh=1` 走
  `_wake.set()` 即时唤醒（免 30s 盲等）；扫描异常 `_fail_scan()` 复位状态保住旧缓存。
  有旧缓存时 `state` 恒 `ready`（重扫期间响应旧终值），页面判"正在重扫" =
  `progress.phase ∉ {ready,idle}`。`/clean` 控制台为单文件五步向导（零构建零依赖），
  任务经 `sessionStorage['cc-task']` 续接，报告页后水位 = 任务完成后 `fresh=1` 首个
  `partial.fs`（fs 阶段 <1s 即出，disk_usage 实测，不信估算）。

## 6. 已知坑清单（历史教训，勿重蹈）

1. csc C#5 无 `$""`；PowerShell 内联命令经 bash 转义必坏——**一律写 .ps1 文件提权执行**。
2. Glances 必带 `--disable-plugin processlist,processcount`（本机 EDR 下 19.5s/轮）。
3. LHM 传感器缺失先查 PawnIO 服务/设备（静默失败）；junction 别名无效（路径会被规范化）。
4. schtasks 手动建被 EDR 拒，但 memreduct 官方 install_clean_task.ps1 可过（姿势正确）。
5. 页面 fetch 超时：API 6s/temps 12s——低于此会在冷调用期误判失败。
6. Node/Playwright 在本机偶发 OOM（系统 commit 高）——先触发一次清理再跑验证。
7. ~~`make webui` 会清空 `public/`（连带删 dashboard）~~ **已随 2026-10-20 迁仓消除**（前端
   入本仓 `web/` 并入库，`git checkout -- web/` 即恢复；坑 7 保留作历史教训）。
8. **CPU 间歇 0.0%（v3.3 已修）**：Glances Webserver 每个插件 API 请求都**无条件触发**该插件
   update（`glances_restful_api.py __update_stats` 带 plugins_list 时绕过节流）且全程**无锁**；
   多客户端并发时 psutil 非阻塞差值采样（`cpu_percent`/`cpu_times_percent` interval=0）被并发/
   贴身调用，输家线程差值窗口≈0ms → `total/user/sys/idle` **全 0 坏帧**写入缓存（实测 3 并发
   ≈20% 坏帧率；贴身 2ms 调用 `cpu_percent` 82% 返回 0；历史库曾 9.1% 行污染）。上游缺陷、
   仓库只读 → **客户端净化**，判据 `total==0 && idle==0`（数学上 total=0 ⇒ idle=100，零误杀）。
   三处守卫：dashboard `pollMain`（cpu 丢帧保持旧值）、`pollTab`（percpu 整表全 0 丢帧）、
   recorder（写 NULL 勿记 0）。历史库 137 行污染已清洗为 NULL。验证：压测 60s API 层
   32 坏帧下页面主卡 0 次闪零。
9. **前端泄漏审计方法论（v3.6 实战）**：CDP `Performance.getMetrics` 的 `JSEventListeners`
   计数**不可靠**（与真实挂载数不相关）；可靠组合 = ① `addInitScript` hook
   `EventTarget.prototype.addEventListener` 记来源/时刻（ground truth）② CDP
   `JSHeapUsedSize`/`Nodes`/`Documents` 长窗采样看**单调增长**（GC 锯齿波动正常）。
   本页 2026-10 基线：3 分钟窗 heap 14→37→15→18MB 锯齿、Nodes 波动回落、Documents 恒 1、
   稳定期绑定 0 新增/30s → 无泄漏。注意归因时行号会随后续编辑漂移，且页面加载尾巴会
   跨入基线（先等 ≥5s 再取基线）。脚本：`verify/verify_ae_t.js`、`verify_long_stability.js`。
10. **Windows shell 双坑（v4.0 复踩）**：① bash sed 替换文本里的 `\\` 会被解释成控制符
   （如 `\b`=0x08，文件里肉眼不可见、grep 匹配不上）——多行/含反斜杠的改写一律用 Edit
   工具或 python 字节级替换；② Playwright 版本与 ms-playwright 浏览器目录错位
   （node_modules 要 chromium_headless_shell-1243，本机只装 chromium-1234）——launch 处
   统一回退 `executablePath = LOCALAPPDATA + '/ms-playwright/chromium-1234/chrome-win64/chrome.exe'`
   （正斜杠），verify/ 六脚本已全部带上。
11. **flex 定宽 nowrap 列溢出压叠 + grid item 撑破（v4.0 报告页实录）**：① `.fsrow.dual`
   两段数值列固定 96px + `white-space:nowrap`，内容 `94.4% · 余 21.7G / 389.9G` 实测 174px
   → 溢出盒子 68px 压到相邻后值 span（**flex 不换行不收缩，溢出内容直接叠在邻居上**，
   容器级 scrollWidth 断言抓不到 span 级重叠）；② 窄屏 `.track` 有 min-width（60px），
   dual 行 min-content 369 撑破 grid item（repbox 默认 `min-width:auto`）→ 文档横向滚动。
   修复：dual 前值去总量 + 列宽 126px（内容实测要量字体渲染，估算 98px 实测 125px）；
   窄屏媒体查询列改 `width:auto; flex:none` 缩一号 + track 隐藏。教训：**定宽 nowrap 列
   必须按最长实测内容定宽**；span 级重叠断言要比较相邻元素 rect 相交而非容器 scrollWidth。
   （端到端截图经视觉模型发现——该渠道在本控制台曾误报"阶梯缩进"，量测核实后才可信。）
12. **`except as e` 块外引用 e 必 UnboundLocalError（2026-10-21 审计 P0 实录）**：
   CPython 在 except 块结束时自动 `del e`，块外再引用即崩。`memclean_svc.py` 兜底清理路径
   曾因此 **100% 崩溃**（修剪已执行、响应永远发不出、日志不落盘），且被前端"断连 ping
   复核通过→清理已在后台执行"的兜底进一步掩盖——**`memclean_svc.log` 恒 0 字节是唯一
   线索**。三教训：① 块内先 `err = str(e)` 再块外用；② `$ErrorActionPreference
   'SilentlyContinue'`/裸 `except: pass` 会把参数异常静默吞成"语句消失"（曾致 dev.ps1
   两行从不输出，同模式）；③ 写日志必须独立 try/except 包裹——日志失败不得影响主响应。
   回归防线：`verify/verify_cleanup_api.js`（断言 200+JSON+日志落盘）。另：坑 8 判据经受住
   2026-10-21 审计复核——历史库残留 21 行 `cpu=0.0` 经同期 mem/swap/net 上下文比对均为
   真实极低占用（12 核轻载 round 到 0.00），非坏帧漏网，判据零误杀结论维持。
13. **传感器数据完整性边界（2026-10-21 审计复核）**：`fan` 列 61% NULL（CPU/主板风扇
   硬件不可读属已知边界，但意味着趋势图风扇曲线大部分时段断线）；`p_cpu` 恒 0——LHM 的
   CPU Package 功耗本机读出即 0.0W（硬件/驱动不报，非链路丢失），功耗维度**仅 p_gpu 有效**
   （其 24W↔46W 跳变为负载波动，非双口径）。ROADMAP 功耗方向已据此修正。
14. **顶格 def 插进 class 体 = 静默截断类（2026-10-28 实录）**：向 Scanner 类方法之间插入
   模块级函数（顶格 `def`）后，**class 体在该处提前结束**，其后的缩进方法（`_du` 等）沦为
   该函数的嵌套 def——`py_compile` 与 `import` 均不报错，运行时 `cls._du` 才
   AttributeError（扫描必失败"保留旧缓存"）。教训：往类文件插模块级函数要么放 class 定义
   之前，要么放文件尾；插入后用 `hasattr(cls, '_du')` 一类的结构自检再部署。
   同日伴生坑：audit jsonl 的 `ts` 是 **ISO 字符串**、释放量字段名是 **bytes_freed**——
   按 float 比较时间戳（`r.get('ts',0) >= since`）会 TypeError 逃逸 handler 断连
   （curl 空响应），需 `datetime.fromisoformat()` 转换；汇总历史曾用错字段名 `bytes`
   读出恒 0（实际累计释放 11.1G）。

## 6. 空间去向追踪方案（2026-10-28，用户"两周 C/D 各 +10G"核实结论）

**核实数据**（mtime 分桶实测，去 junction 双计——`LOCALAPPDATA\Docker` 是指向
`D:\Docker` 的符号链接，11.96G 物理在 D）：近 14 天真实增长 = **Docker vhdx +11.9G**
（docker_data.vhdx 只涨不缩，内含镜像 3.6G/卷 5.3G + 水分）、**微信（PF x86\Tencent）
+6.7G**、**workspace_test +8.0G**（研发产物，正常）；C 盘另 wsl vhdx +5.0G、codex +1.0G。
结论：研发缓存类（npm/uv/pip/Temp）非主凶且 A 级已覆盖（历史累计清出 11.1G），
大头是 **Docker vhdx 结构性膨胀 + 应用数据（微信）+ 正常研发增长**。

**机制**（借鉴 360 清理的分类清晰/描述质量/清理史）：
- `disk_hist` 小表（history.db，独立于 metrics）：每轮扫描（600s）记录
  ts/c_free/d_free/e_free/vhdx_g，60 天滚动保留——水位与 vhdx 趋势可观测；
- `GET /disk/growth?days=N`：首尾水位 Δ + vhdx Δ + audit 清理史汇总（含 bytes_freed）；
- /clean 分析页"空间去向"卡：近 14 天占用变化（free 降=红）+ 当前水位 +
  历史清理释放总量/次数，10 分钟自刷；
- A 级 9 条规则 note 全量补齐"这是什么 + 删了会怎样"式描述。

**行动项**（人工决策）：Docker 卷 5.3G reclaimable（99% 未引用）须逐卷确认价值后
`docker volume prune`；C 级 VHDX 压缩（装 Glc-DiskClean-C）可回收 vhdx 水分 ~3G；
微信缓存走客户端内置清理；wsl vhdx 可迁移 E 盘（opt-in）。
