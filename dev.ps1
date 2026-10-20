# ============================================================
#  Glances 大屏 · 统一管理脚本（选项式菜单）
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

# ---------- 状态检测 ----------
function Test-Port($port) {
    (curl.exe -s -o NUL -m 3 -w "%{http_code}" "http://127.0.0.1:$port/") -eq '200'
}
function Get-Status {
    $gl = Test-Port 61208; $mc = Test-Port 61210
    $temps = & $PY -c "import json,urllib.request;d=json.load(urllib.request.urlopen('http://127.0.0.1:61210/temps',timeout=4));print(len(d['temps']),d.get('writer_fresh'))" 2>$null
    $auto = (reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" | Select-String 'GlancesWeb|MemCleanSvc' | Measure-Object).Count
    Write-Host ""
    Write-Host "  ┌─ 服务状态 ────────────────────────────────"
    Write-Host "  │ Glances 数据源   :61208  $(if($gl){'● 运行'}else{'○ 停止'})"
    Write-Host "  │ 大屏/清理/历史   :61210  $(if($mc){'● 运行'}else{'○ 停止'})   ← 大屏短地址 http://${TSIP}:61210/"
    if ($temps -match '^(\d+) (True|False)$') {
        $n = $Matches[1]; $fresh = $Matches[2] -eq 'True'
        Write-Host "  │ 温度传感器       :$n 个  $(if($fresh){'● 完整(CPU+存储)'}else{'△ 仅GPU——writer 需重启[5]'})"
    } else { Write-Host "  │ 温度传感器       :? (61210 未响应)" }
    Write-Host "  │ 开机自启注册     :$auto/2  (Glances/memclean)"
    Write-Host "  └───────────────────────────────────────────"
}

# ---------- 启停 ----------
function Start-All {
    if (-not (Test-Port 61208)) { Start-Process $PY -ArgumentList '-m','glances','-w','--disable-plugin','processlist,processcount' -WorkingDirectory $GLANCES -WindowStyle Hidden; Write-Host '  → Glances 启动中…' }
    if (-not (Test-Port 61210)) { Start-Process $PYW -ArgumentList "$SVC\memclean_svc.py" -WindowStyle Hidden; Write-Host '  → memclean 启动中…' }
    if (-not (Get-TempsFresh))  { Restart-Writer }
    Write-Host '  完成。' -ForegroundColor Green
}
function Get-TempsFresh {
    (& $PY -c "import json,urllib.request;d=json.load(urllib.request.urlopen('http://127.0.0.1:61210/temps',timeout=4));print(d.get('writer_fresh'))" 2>$null) -eq 'True'
}
function Restart-Writer {
    Write-Host '  → 提权重启温度 writer（UAC 确认一次）…'
    Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"$SVC\lhm\restart_writer.ps1" -Wait
    Write-Host '  完成（10 秒后生效）。' -ForegroundColor Green
}
function Stop-All {
    foreach ($port in 61208, 61210) {
        Get-NetTCPConnection -LocalPort $port -State Listen | ForEach-Object {
            Stop-Process -Id $_.OwningProcess -Force
            Write-Host "  → :$port 已停止"
        }
    }
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*lhm_writer*' -or $_.CommandLine -like '*memclean_svc*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "  → $($_.ProcessId) 已停止" }
    Get-Process beszel_lhm -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host '  全部停止（注：writer 为提权进程可能需[5]的脚本清理）。' -ForegroundColor Yellow
}

# ---------- 其他 ----------
function Invoke-Clean {
    Write-Host '  触发 MEM.REDUCT 同源清理（6/8 区域）…'
    & $PY -c "import json,subprocess,time,os;subprocess.run(['schtasks','/run','/tn','MemReduct-Clean']);t0=os.path.getmtime(r'C:\ProgramData\memreduct\clean-result.json') if os.path.exists(r'C:\ProgramData\memreduct\clean-result.json') else 0
import time as _t
for _ in range(100):
    _t.sleep(1.5)
    try:
        if os.path.getmtime(r'C:\ProgramData\memreduct\clean-result.json')>t0:
            d=json.load(open(r'C:\ProgramData\memreduct\clean-result.json',encoding='utf-8-sig'));print('释放 %s GB（%s→%s GB，%.1fs）'%(d['freedGB'],d['beforeGB'],d['afterGB'],d['durationMs']/1000));break
    except: pass" 2>$null
}
function Show-History {
    & $PY -c "import sqlite3,time;d=sqlite3.connect(r'$SVC\data\history.db');n,lo,hi=d.execute('select count(*),min(ts),max(ts) from metrics').fetchone();print('  历史库: %d 点, 覆盖 %.1f 小时（%s ~ %s）'%(n,(hi-lo)/3600,time.strftime('%m-%d %H:%M',time.localtime(lo)),time.strftime('%m-%d %H:%M',time.localtime(hi))))" 2>$null
    if (-not $?) { Write-Host '  历史库为空/不可读' }
}
function Show-Log {
    Write-Host "`n  == memclean 清理日志（最近 8 条）=="; Get-Content "$env:TEMP\memclean_svc.log" -Tail 8 -ErrorAction SilentlyContinue
    Write-Host "`n  == Glances 日志（最近 5 条）=="; Get-Content "$env:TEMP\glances_webserver.log" -Tail 5 -ErrorAction SilentlyContinue
}

# ---------- 主循环 ----------
while ($true) {
    Write-Host ""
    Write-Host "╔══ Glances 大屏 · 管理 ════════════════════" -ForegroundColor Yellow
    Write-Host "║  [1] 状态总览        [2] 启动缺失组件"
    Write-Host "║  [3] 重启全部        [4] 停止全部"
    Write-Host "║  [5] 温度writer重启  [6] 立即清理内存"
    Write-Host "║  [7] 历史库统计      [8] 打开大屏"
    Write-Host "║  [9] 查看日志        [0] 退出"
    Write-Host "╚═══════════════════════════════════════════" -ForegroundColor Yellow
    $c = Read-Host '  选择'
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
        default { Write-Host '  无效选项' -ForegroundColor Red }
    }
}
