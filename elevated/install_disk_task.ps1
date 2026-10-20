#Requires -Version 5.1
<#
.SYNOPSIS
	安装/卸载 Glances 磁盘清理 C 级提权桥计划任务 Glc-DiskClean-C。

.DESCRIPTION
	disk-clean-c.ps1（VHDX 压缩 + pagefile 迁移）需要管理员令牌。本脚本一次性注册：

	  Glc-DiskClean-C   按需任务：用户态 memclean_svc 执行 `schtasks /run /tn Glc-DiskClean-C`
	                    触发；三文件协议位于 %ProgramData%\glances-diskclean\
	                    （c-params.json / c-progress.json / c-result.json）

	以当前登录用户注册 (Interactive + Highest)，无需存储密码。照 MemReduct-Clean 桥先例。

.EXAMPLE
	# 管理员 PowerShell 安装（powershell 5.1 坏则换 pwsh）:
	powershell -ExecutionPolicy Bypass -File install_disk_task.ps1

	# 安装并做 dry-run 全链冒烟（零写盘：不 kill docker、不 diskpart、不动注册表）:
	powershell -ExecutionPolicy Bypass -File install_disk_task.ps1 -Test

	# 卸载:
	powershell -ExecutionPolicy Bypass -File install_disk_task.ps1 -Uninstall
#>
[CmdletBinding()]
param (
	[switch]$Test,
	[switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$TaskName = 'Glc-DiskClean-C'
$ScriptPath = Join-Path $PSScriptRoot 'disk-clean-c.ps1'
$CDir = Join-Path $env:ProgramData 'glances-diskclean'
$ParamsF = Join-Path $CDir 'c-params.json'
$ResultF = Join-Path $CDir 'c-result.json'

# ---------- 前置检查 ----------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ( -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) )
{
	Write-Host '错误: 本脚本需要管理员权限运行（右键"使用管理员身份运行"PowerShell）' -ForegroundColor Red
	exit 2
}
if ( -not $Uninstall -and -not (Test-Path $ScriptPath) )
{
	Write-Host "错误: 找不到执行体 $ScriptPath" -ForegroundColor Red
	exit 2
}

# ---------- 卸载模式 ----------
if ( $Uninstall )
{
	if ( Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue )
	{
		Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
		Write-Host "已移除任务: $TaskName" -ForegroundColor DarkGray
	}
	Write-Host '卸载完成。' -ForegroundColor Green
	exit 0
}

# ---------- 目录 + 任务注册 ----------
if ( -not (Test-Path $CDir) ) { New-Item -ItemType Directory -Path $CDir -Force | Out-Null }

$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
	-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
	-MultipleInstances IgnoreNew
# 占位触发器（过去时间，永不自动触发；按需任务必须至少有一个触发器才能注册）
$dummyTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date '1990-01-01 04:00')
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
	"-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -Quiet"
)
$principalTask = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest

if ( Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue )
{
	Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
	Write-Host '已移除旧版任务，重建…' -ForegroundColor DarkGray
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $dummyTrigger -Principal $principalTask -Settings $settings -Description 'Glances dashboard disk-clean C-level bridge (VHDX compact + pagefile migrate). Protocol: %ProgramData%\glances-diskclean\' | Out-Null
Write-Host "已注册按需任务: $TaskName (RunLevel=Highest, TimeLimit 20min)" -ForegroundColor Green
Write-Host "  执行体:   $ScriptPath"
Write-Host "  协议目录: $CDir"

# ---------- 冒烟测试（dry_run 全链，零副作用） ----------
if ( -not $Test ) { exit 0 }

Write-Host ''
Write-Host '触发 dry-run 冒烟（不 kill Docker / 不 diskpart / 不写注册表）...' -ForegroundColor Cyan

# 记录 docker 容器数（前后应一致——dry 模式零动作的铁证）
$cntBefore = 0
try { $cntBefore = @(& docker ps -a --format '{{.Names}}' 2>$null).Count } catch {}

$oldTs = 0
if ( Test-Path $ResultF )
{ try { $oldTs = [double](Get-Content $ResultF -Raw -Encoding UTF8 | ConvertFrom-Json).ts } catch {} }

# 写 dry_run 参数并触发
@{ ts = [double](Get-Date -UFormat %s); id = 'install-test'; dry_run = $true; actions = @('vhdx', 'pagefile');
   pagefile = @{ target = 'E:'; size_mb = 32768 } } | ConvertTo-Json | Out-File $ParamsF -Encoding UTF8
Start-ScheduledTask -TaskName $TaskName

$deadline = (Get-Date).AddSeconds(180)
while ( (Get-Date) -lt $deadline )
{
	Start-Sleep 2
	if ( Test-Path $ResultF )
	{
		try
		{
			$r = Get-Content $ResultF -Raw -Encoding UTF8 | ConvertFrom-Json
			if ( [double]$r.ts -gt $oldTs ) { break }
		} catch {}
	}
}

if ( -not (Test-Path $ResultF) ) { Write-Host '冒烟失败: 180s 内无 c-result.json' -ForegroundColor Red; exit 5 }
$r = Get-Content $ResultF -Raw -Encoding UTF8 | ConvertFrom-Json
if ( [double]$r.ts -le $oldTs ) { Write-Host '冒烟失败: 结果文件未更新（任务可能未跑起来，查 c-run.log）' -ForegroundColor Red; exit 5 }

$cntAfter = 0
try { $cntAfter = @(& docker ps -a --format '{{.Names}}' 2>$null).Count } catch {}

Write-Host ("冒烟结果: ok={0}  dry_run={1}  VHDX 现状 {2}G  steps {3} 个  errors: {4}" -f `
	$r.ok, $r.dry_run, $r.vhdx.before_gib, @($r.steps).Count, ($r.errors -join '; ')) $(if ($r.ok) { 'Green' } else { 'Red' })
Write-Host ("容器数前后一致: {0} = {1} {2}" -f $cntBefore, $cntAfter, $(if ($cntBefore -eq $cntAfter) { '✓' } else { '✗ 不一致！' })) `
	$(if ($cntBefore -eq $cntAfter) { 'Green' } else { 'Red' })
if ( $r.ok -and $cntBefore -eq $cntAfter ) { Write-Host '冒烟测试通过。' -ForegroundColor Green }
else { exit 6 }
