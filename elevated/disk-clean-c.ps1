#Requires -Version 5.1
<#
.SYNOPSIS
	Glances 大屏「磁盘清理」C 级提权执行体 — VHDX 压缩 + pagefile 迁移。

.DESCRIPTION
	由计划任务 Glc-DiskClean-C (RunLevel=Highest) 调用；memclean_svc/diskclean.py 经
	schtasks /run 触发本脚本。三文件协议（目录 %ProgramData%\glances-diskclean）：

	  c-params.json    服务(用户态)写入：{ts, id, dry_run, actions:[vhdx|pagefile],
	                   pagefile:{target:'E:', size_mb:32768}}
	  c-progress.json  本脚本写：{steps:[{name,state(run|done|fail)}]}（步骤级，2s 轮询粒度）
	  c-result.json    本脚本写：{ok, errors[], vhdx:{before_gib,after_gib,applied},
	                   pagefile:{applied}, docker_recovered[], dry_run}

	安全（playbook test/docs/windows-disk-cleanup-playbook.md §6/§9.4）：
	  - dry_run=true 全链只读：不 kill、不 diskpart、不写注册表；
	  - VHDX 链前后 docker ps -a 快照比对，曾 running 的容器自动 docker start 补救
	    （实战先例：eureka 4 小时前已 Exit 137 未被注意）；
	  - pagefile 迁移前校验换页活跃度(>85% 拒)与目标盘 NTFS/余量；
	  - 注册表仅动 PagingFiles 指定键（红线：其余注册表一律不碰）；
	  - 绝不 docker system prune --volumes / volume prune（本脚本根本不跑 prune）。

	依赖：系统 powershell.exe（与 MemReduct-Clean 桥同款，已验证可用）。

.EXAMPLE
	powershell -NoProfile -ExecutionPolicy Bypass -File disk-clean-c.ps1 -Quiet
#>
[CmdletBinding()]
param (
	[switch]$Quiet   # 占位（与 memreduct 桥参数形状一致；本脚本本就非交互）
)

$ErrorActionPreference = 'Continue'     # 单步失败继续跑完，错误汇总进 result

$CDir      = Join-Path $env:ProgramData 'glances-diskclean'
$ParamsF   = Join-Path $CDir 'c-params.json'
$ProgressF = Join-Path $CDir 'c-progress.json'
$ResultF   = Join-Path $CDir 'c-result.json'
$LogF      = Join-Path $CDir 'c-run.log'

$VhdxPath = 'D:\Docker\wsl\disk\docker_data.vhdx'
$DockerExe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
$PagefileKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management'

# ---------- 前置 ----------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ( -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) )
{
	Write-Host '错误: 需要管理员权限（应由 RunLevel=Highest 的计划任务调用）' -ForegroundColor Red
	exit 2
}
if ( -not (Test-Path $CDir) ) { New-Item -ItemType Directory -Path $CDir -Force | Out-Null }

# 日志走纯文件追加（§8 先例：提权/无 console 场景禁用 Start-Transcript——它依赖
# console host 初始化，从 pythonw 无控制台进程链触发的任务会挂在 conhost 分配上）
function Log ([string]$m)
{ Add-Content -Path $LogF -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 }
Log '=== C 级链脚本进入 ==='

# ---------- 协议工具 ----------
$script:Steps = New-Object System.Collections.ArrayList
function Write-ProgressJson
{
	$tmp = "$ProgressF.tmp"
	@{ ts = [double](Get-Date -UFormat %s); steps = $script:Steps } | ConvertTo-Json -Depth 4 | Out-File $tmp -Encoding UTF8
	Move-Item $tmp $ProgressF -Force
}
function Step-Begin ([string]$name) { [void]$script:Steps.Add(@{ name = $name; state = 'run' }); Write-ProgressJson }
function Step-End   { $script:Steps[$script:Steps.Count - 1].state = 'done'; Write-ProgressJson }
function Step-Bad   { $script:Steps[$script:Steps.Count - 1].state = 'fail'; Write-ProgressJson }
function Write-ResultJson ([hashtable]$r)
{
	$tmp = "$ResultF.tmp"
	$r | ConvertTo-Json -Depth 5 | Out-File $tmp -Encoding UTF8
	Move-Item $tmp $ResultF -Force
}
function Fail-All ([string]$msg)
{
	Write-ResultJson @{ ts = [double](Get-Date -UFormat %s); ok = $false; errors = @($msg); dry_run = $false; vhdx = @{}; pagefile = @{}; docker_recovered = @(); steps = @() }
	Log "失败: $msg"
	exit 3
}

# ---------- 读参数 ----------
if ( -not (Test-Path $ParamsF) ) { Fail-All 'c-params.json 缺失（应由 memclean_svc 写入）' }
try { $params = Get-Content $ParamsF -Raw -Encoding UTF8 | ConvertFrom-Json }
catch { Fail-All "c-params.json 解析失败: $($_.Exception.Message)" }
$dry = [bool]$params.dry_run
$actions = @($params.actions)
Log ("C 级链启动: actions=[{0}] dry_run={1} id={2}" -f ($actions -join ','), $dry, $params.id)

$errors   = @()
$recovered = @()
$vhdxOut  = @{ before_gib = $null; after_gib = $null; applied = $false }
$pfOut    = @{ applied = $false }

# ---------- Docker 现状快照（docker ps -a；返回 @{名字=state} 哈希） ----------
function Get-DockerState
{
	$h = @{}
	try
	{
		$lines = & docker ps -a --format '{{.Names}}|{{.State}}' 2>$null
		if ( $LASTEXITCODE -eq 0 )
		{
			foreach ( $l in $lines )
			{
				$p = "$l".Split('|')
				if ( $p.Count -ge 2 ) { $h[$p[0]] = $p[1] }
			}
		}
	}
	catch {}
	return $h
}

# ---------- VHDX 压缩链（playbook §6.2） ----------
function Invoke-VhdxCompact
{
	if ( -not (Test-Path $VhdxPath) ) { $script:errors += "VHDX 不存在: $VhdxPath"; return }

	Step-Begin 'docker ps -a 快照'
	$before = Get-DockerState
	if ( $before.Count -eq 0 ) { Write-Host '警告: docker 不可达，快照为空（比对补救将不可用）' -ForegroundColor Yellow }
	$runningBefore = @($before.Keys | Where-Object { $before[$_] -eq 'running' })
	$vhdxOut.before_gib = [math]::Round((Get-Item $VhdxPath).Length / 1GB, 2)
	Step-End

	if ( $dry )
	{
		Step-Begin 'dry-run：将执行 停 Docker → wsl --shutdown → compact → 重启 → 比对恢复'
		$vhdxOut.after_gib = $vhdxOut.before_gib
		Step-End
		return
	}

	Step-Begin '停止 Docker Desktop'
	foreach ( $proc in 'Docker Desktop', 'com.docker.backend', 'com.docker.build', 'com.docker.dev-envs' )
	{ & taskkill /F /IM "$proc.exe" 2>$null | Out-Null }
	Start-Sleep 3
	Step-End

	Step-Begin 'wsl --shutdown（含全部发行版）'
	& wsl --shutdown 2>$null | Out-Null
	Start-Sleep 8            # 等 VHDX 句柄彻底释放
	Step-End

	Step-Begin 'diskpart compact vdisk'
	$dpScript = Join-Path $env:TEMP 'glances-compact.txt'
	@"
select vdisk file="$VhdxPath"
attach vdisk readonly
compact vdisk
detach vdisk
"@ | Out-File $dpScript -Encoding ascii
	$dp = & diskpart /s $dpScript 2>&1
	$dpText = "$dp"
	if ( $LASTEXITCODE -ne 0 -and $dpText -notmatch 'successfully compacted' )
	{ Step-Bad; $script:errors += "diskpart 失败: " + ($dpText -replace '\s+', ' ').Substring(0, [Math]::Min(160, $dpText.Length)) }
	else { Step-End }
	$vhdxOut.after_gib = [math]::Round((Get-Item $VhdxPath).Length / 1GB, 2)
	$vhdxOut.applied = $true
	Write-Host ("VHDX: {0}G -> {1}G" -f $vhdxOut.before_gib, $vhdxOut.after_gib)

	Step-Begin '重启 Docker Desktop'
	if ( Test-Path $DockerExe ) { Start-Process $DockerExe; Step-End }
	else { Step-Bad; $script:errors += "Docker Desktop.exe 不存在: $DockerExe" }

	Step-Begin '等待 Docker 引擎就绪（≤120s）'
	$ready = $false
	$deadline = (Get-Date).AddSeconds(120)
	while ( (Get-Date) -lt $deadline )
	{
		& docker version --format '{{.Server.Version}}' 2>$null | Out-Null
		if ( $LASTEXITCODE -eq 0 ) { $ready = $true; break }
		Start-Sleep 4
	}
	if ( $ready ) { Step-End } else { Step-Bad; $script:errors += 'Docker 引擎 120s 未就绪（跳过容器比对，请人工 docker ps 核对）' }

	if ( $ready )
	{
		Step-Begin '容器状态比对与自动恢复'
		Start-Sleep 15        # 等 restart=always/on-failure 策略先自行拉起
		$after = Get-DockerState
		foreach ( $name in $runningBefore )
		{
			if ( $after.ContainsKey($name) -and $after[$name] -ne 'running' )
			{
				& docker start $name 2>$null | Out-Null
				if ( $LASTEXITCODE -eq 0 )
				{ $script:recovered += $name; Write-Host "  已恢复容器: $name" }
				else { $script:errors += "容器 $name 恢复失败（原为 running），请人工处理" }
			}
		}
		Write-Host ("比对完成: 快照 running {0} 个, 本次恢复 {1} 个" -f $runningBefore.Count, $script:recovered.Count)
		Step-End
	}
}

# ---------- pagefile 迁移链（playbook §5） ----------
function Invoke-PagefileMigrate ([string]$Target, [int]$SizeMb)
{
	if ( -not $Target ) { $Target = 'E:' }
	$size = if ( $SizeMb -gt 0 ) { $SizeMb } else { 32768 }

	Step-Begin '读取换页压力（Win32_PageFileUsage）'
	$pf = @(Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue)
	if ( -not $pf ) { Step-Bad; $script:errors += '读取不到页面文件信息'; return }
	$ratio = 0
	foreach ( $p in $pf ) { $r = $p.CurrentUsage / [Math]::Max($p.AllocatedBaseSize, 1); if ( $r -gt $ratio ) { $ratio = $r } }
	if ( $ratio -gt 0.85 )
	{ Step-Bad; $script:errors += ("换页活跃 {0}%（>85%），顶格使用期不可迁移" -f [math]::Round($ratio * 100)); return }
	Step-End

	Step-Begin "校验目标盘 $Target（NTFS + 余量）"
	$vol = Get-Volume -DriveLetter $Target.TrimEnd(':')[0] -ErrorAction SilentlyContinue
	if ( -not $vol -or $vol.FileSystem -ne 'NTFS' )
	{ Step-Bad; $script:errors += "目标盘 $Target 非 NTFS（页面文件不支持）"; return }
	if ( $vol.SizeRemaining -lt $size * 1MB + 5GB )
	{ Step-Bad; $script:errors += ("{0} 剩余 {1:N0}G 不足（需 {2}G+5G）" -f $Target, ($vol.SizeRemaining / 1GB), ($size / 1024)); return }
	Step-End

	Step-Begin '检查是否已在目标盘（幂等）'
	$already = @($pf | Where-Object { $_.Name -like "$Target\*" })
	if ( $already )
	{
		Write-Host "页面文件已在 $Target，无需迁移"
		$pfOut.applied = $true
		$pfOut.note = '已在目标盘（幂等，未改动）'
		Step-End
		return
	}
	Step-End

	if ( $dry ) { Step-Begin 'dry-run：将写 PagingFiles 注册表（重启生效）'; Step-End; return }

	Step-Begin '写注册表（AutomaticManagedPagefile=false + PagingFiles）'
	try
	{
		$cs = Get-CimInstance Win32_ComputerSystem
		Set-CimInstance -InputObject $cs -Property @{ AutomaticManagedPagefile = $false }
		Set-ItemProperty -Path $PagefileKey -Name PagingFiles -Value "$Target\pagefile.sys $size $size" -Type MultiString
		$pfOut.applied = $true
		$pfOut.note = '已写注册表，重启后生效'
		Write-Host "已设置 $Target\pagefile.sys $size MB（重启后生效，旧盘文件自动消失）"
		Step-End
	}
	catch { Step-Bad; $script:errors += "注册表写入失败: $($_.Exception.Message)" }
}

# ---------- 主流程 ----------
if ( $actions -contains 'vhdx' )    { Invoke-VhdxCompact }
if ( $actions -contains 'pagefile' )
{
	$t = 'E:'; $s = 32768
	if ( $params.pagefile ) { $t = $params.pagefile.target; $s = [int]$params.pagefile.size_mb }
	Invoke-PagefileMigrate $t $s
}

Write-ResultJson @{
	ts = [double](Get-Date -UFormat %s)
	id = $params.id
	ok = ( $errors.Count -eq 0 )
	errors = $errors
	dry_run = $dry
	vhdx = $vhdxOut
	pagefile = $pfOut
	docker_recovered = $recovered
	steps = $script:Steps
}
Log ("C 级链结束: ok={0} errors={1} 恢复容器={2}" -f ( $errors.Count -eq 0 ), $errors.Count, $recovered.Count)
