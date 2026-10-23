# ============================================================
#  Glances 大屏 · 统一管理脚本（选项式菜单，彩色状态 + 选项说明）
#  位置: sys-dashboard\dev.ps1    用法: .\dev.ps1
# ============================================================
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$PY      = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $PY -or -not (& $PY -c 'print(1)' 2>$null)) { $PY = 'C:\Python\Python311\python.exe' }   # 本机回退
$PYW     = Join-Path (Split-Path $PY -Parent) 'pythonw.exe'
if (-not (Test-Path $PYW)) { $PYW = $PY }                        # 无 pythonw 时退控制台版
$SVC     = $PSScriptRoot                       # 仓库根（与服务端脚本平铺同级）
$GLANCES = Join-Path (Split-Path $PSScriptRoot -Parent) 'glances'   # 兄弟目录推导（外部依赖，缺失时仅 Glances 启动不可用）
$TSIP    = 'localhost'                         # 本机打开大屏；手机访问用 Tailscale IP（RUNBOOK §1）

# ---------- 输出辅助：Seg 追加一段着色文本，Line 输出整行（颜色缺省时退默认色） ----------
function Seg($text, $color) { if ($color) { Write-Host $text -ForegroundColor $color -NoNewline } else { Write-Host $text -NoNewline } }
function Line($text, $color) { if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text } }

# ---------- 状态检测 ----------
function Test-Port($port) {
    (curl.exe -s -o NUL -m 3 -w "%{http_code}" "http://127.0.0.1:$port/") -eq '200'
}
function Get-AutoCount {
    (reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" | Select-String 'GlancesWeb|MemCleanSvc' | Measure-Object).Count
}
function Get-Status {
    $gl = Test-Port 61208; $mc = Test-Port 61210
    $temps = & $PY -c "import json,urllib.request;d=json.load(urllib.request.urlopen('http://127.0.0.1:61210/temps',timeout=4));print(len(d['temps']),d.get('writer_fresh'))" 2>$null
    $auto = Get-AutoCount
    Write-Host ""
    Line "  ┌─ 服务状态 ────────────────────────────────" Yellow
    Seg  "  │ Glances 数据源   :61208  " White
    if ($gl) { Line '● 运行' Green } else { Line '○ 停止' Red }
    Seg  "  │ 大屏/清理/历史   :61210  " White
    if ($mc) { Seg '● 运行' Green } else { Seg '○ 停止' Red }
    Line "   ← 大屏短地址 http://${TSIP}:61210/" DarkGray
    if ($temps -match '^(\d+) (True|False)$') {
        $n = $Matches[1]; $fresh = $Matches[2] -eq 'True'
        Seg  "  │ 温度传感器       :$n 个  " White
        if ($fresh) { Line '● 完整(CPU+存储)' Green }
        else { Line '△ 仅GPU —— writer 需重启[5]' Yellow }
    } else { Line '  │ 温度传感器       :? (61210 未响应)' Red }
    Seg  '  │ 开机自启注册     ' White
    Seg  "$auto/2" $(if ($auto -eq 2) { 'Green' } elseif ($auto -ge 1) { 'Yellow' } else { 'Red' })
    Line '  (Glances/memclean)' DarkGray
    Line "  └───────────────────────────────────────────" Yellow
}

# ---------- 启停 ----------
function Start-All {
    if (-not (Test-Port 61208)) { Start-Process $PY -ArgumentList '-m','glances','-w','--disable-plugin','processlist,processcount' -WorkingDirectory $GLANCES -WindowStyle Hidden; Line '  → Glances 启动中…' Cyan }
    if (-not (Test-Port 61210)) { Start-Process $PYW -ArgumentList "$SVC\memclean_svc.py" -WindowStyle Hidden; Line '  → memclean 启动中…' Cyan }
    if (-not (Get-TempsFresh))  { Restart-Writer }
    Line '  完成。' Green
}
function Get-TempsFresh {
    (& $PY -c "import json,urllib.request;d=json.load(urllib.request.urlopen('http://127.0.0.1:61210/temps',timeout=4));print(d.get('writer_fresh'))" 2>$null) -eq 'True'
}
function Restart-Writer {
    Line '  → 提权重启温度 writer（UAC 确认一次）…' Cyan
    Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"$SVC\lhm\restart_writer.ps1" -Wait
    Line '  完成（10 秒后生效）。' Green
}
function Stop-All {
    foreach ($port in 61208, 61210) {
        # 端口未监听时 Get-NetTCPConnection 无匹配会抛 CIM 错误（SilentlyContinue 不总抑制）——
        # -ErrorAction Stop + try/catch 兜底，未运行给友好提示
        $conns = @(try { Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop } catch { @() })
        if ($conns.Count) {
            $conns | ForEach-Object {
                Stop-Process -Id $_.OwningProcess -Force
                Line "  → :$port 已停止" Cyan
            }
        } else { Line "  → :$port 本就未运行，跳过" DarkGray }
    }
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*lhm_writer*' -or $_.CommandLine -like '*memclean_svc*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Line "  → $($_.ProcessId) 已停止" Cyan }
    Get-Process beszel_lhm -ErrorAction SilentlyContinue | Stop-Process -Force
    Line '  全部停止（注：writer 为提权进程可能需[5]的脚本清理）。' Yellow
}

# ---------- 其他 ----------
function Invoke-Clean {
    Line '  触发 MEM.REDUCT 同源清理（6/8 区域）…' Cyan
    $r = & $PY -c "import json,subprocess,time,os;subprocess.run(['schtasks','/run','/tn','MemReduct-Clean']);t0=os.path.getmtime(r'C:\ProgramData\memreduct\clean-result.json') if os.path.exists(r'C:\ProgramData\memreduct\clean-result.json') else 0
import time as _t
for _ in range(100):
    _t.sleep(1.5)
    try:
        if os.path.getmtime(r'C:\ProgramData\memreduct\clean-result.json')>t0:
            d=json.load(open(r'C:\ProgramData\memreduct\clean-result.json',encoding='utf-8-sig'));print('释放 %s GB（%s→%s GB，%.1fs）'%(d['freedGB'],d['beforeGB'],d['afterGB'],d['durationMs']/1000));break
    except: pass" 2>$null
    if ($r) { Line "  $r" Green } else { Line '  150s 内未返回结果：任务 MemReduct-Clean 未注册或执行超时（补装见 RUNBOOK §3）' Red }
}
function Show-History {
    & $PY -c "import sqlite3,time;d=sqlite3.connect(r'$SVC\data\history.db');n,lo,hi=d.execute('select count(*),min(ts),max(ts) from metrics').fetchone();print('  历史库: %d 点, 覆盖 %.1f 小时（%s ~ %s）'%(n,(hi-lo)/3600,time.strftime('%m-%d %H:%M',time.localtime(lo)),time.strftime('%m-%d %H:%M',time.localtime(hi))))" 2>$null
    if (-not $?) { Line '  历史库为空/不可读' Red }
}
function Show-Log {
    Line "`n  == memclean 清理日志（最近 8 条）==" Cyan; Get-Content "$env:TEMP\memclean_svc.log" -Tail 8 -ErrorAction SilentlyContinue
    Line "`n  == Glances 日志（最近 5 条）==" Cyan; Get-Content "$env:TEMP\glances_webserver.log" -Tail 5 -ErrorAction SilentlyContinue
}

# ---------- 主菜单 ----------
function Opt($key, $name, $desc) {
    Seg '║  ' Yellow
    Seg "[$key]" Cyan
    Seg " $name" White
    if ($desc) { Seg "  —— $desc" DarkGray }
    Write-Host ''
}
function Show-Menu {
    $gl = Test-Port 61208; $mc = Test-Port 61210; $auto = Get-AutoCount
    Write-Host ''
    Line '╔══ Glances 大屏 · 管理台 ═══════════════════════════════' Yellow
    Seg  '║  状态  ' Yellow
    Seg  'Glances :61208 ' White
    if ($gl) { Seg '●运行' Green } else { Seg '○停止' Red }
    Seg  '    ' Yellow
    Seg  '大屏 :61210 ' White
    if ($mc) { Seg '●运行' Green } else { Seg '○停止' Red }
    Seg  '    ' Yellow
    Seg  '开机自启 ' White
    Seg  "$auto/2" $(if ($auto -eq 2) { 'Green' } elseif ($auto -ge 1) { 'Yellow' } else { 'Red' })
    Write-Host ''
    Line '╠════════════════════════════════════════════════════════' Yellow
    Opt 1 '状态总览'       '端口/温度传感器/开机自启四项详情'
    Opt 2 '启动缺失组件'   '缺谁起谁（Glances/大屏/温度writer），已运行的不动'
    Opt 3 '重启全部'       '先停后起；端口卡死或改过配置后用'
    Opt 4 '停止全部'       '停两个端口与 writer；不动开机自启注册'
    Opt 5 '重启温度writer' '需 UAC 管理员确认；温度卡只剩 GPU 数据时用'
    Opt 6 '立即清理内存'   'MemReduct 计划任务 NT 层清理（约 1~3 分钟）'
    Opt 7 '历史库统计'     '持久历史点数与覆盖时段（趋势大图数据来源）'
    Opt 8 '打开大屏'       "浏览器打开 http://${TSIP}:61210/"
    Opt 9 '查看日志'       'memclean 与 Glances 最近日志（排障入口）'
    Opt 0 '退出'           '（或输入 q）'
    Line '╚════════════════════════════════════════════════════════' Yellow
}

# ---------- 主循环 ----------
while ($true) {
    Show-Menu
    $in = Read-Host '  选择（回车刷新状态）'
    $c = if ($null -ne $in) { $in.Trim() } else { '' }     # EOF 返回 $null：归一为空串走退出防护
    switch ($c) {
        '1' { Get-Status }
        '2' { Start-All }
        '3' { Stop-All; Start-Sleep 2; Start-All }
        '4' { Stop-All }
        '5' { Restart-Writer }
        '6' { Invoke-Clean }
        '7' { Show-History }
        '8' { Start-Process "http://${TSIP}:61210/" }
        '9' { Show-Log }
        '0' { return }
        'q' { return }
        ''  {                                  # 空输入：交互下=空回车刷新；stdin 重定向下=EOF，退出防死循环
            if ([Console]::IsInputRedirected) { Line '  输入结束（非交互模式），自动退出。' DarkGray; return }
        }
        default { Line '  无效选项（0-9 或 q 退出）' Red }
    }
}
