# PawnIO root 设备安装（等效官方安装器的 SetupAPI 调用序列）
# 前置：驱动包已由 pnputil /add-driver 入库
$ErrorActionPreference = 'Stop'

# ① 清理问题设备节点残留
pnputil /remove-device "ROOT\PAWNIO\0000" 2>$null | Out-Null

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class RootDev {
  [StructLayout(LayoutKind.Sequential)]
  public struct SP_DEVINFO_DATA { public int cbSize; public Guid ClassGuid; public int DevInst; public IntPtr Reserved; }
  [DllImport("setupapi.dll", CharSet = CharSet.Unicode)]
  public static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid ClassGuid, IntPtr hwndParent);
  [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool SetupDiCreateDeviceInfo(IntPtr DeviceInfoSet, string DeviceDescription,
      ref Guid ClassGuid, string hwndParentName, IntPtr hwndParent, int CreationFlags, ref SP_DEVINFO_DATA DeviceInfoData);
  [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool SetupDiSetDeviceRegistryProperty(IntPtr DeviceInfoSet, ref SP_DEVINFO_DATA DeviceInfoData, int Property, [MarshalAs(UnmanagedType.LPWStr)] string PropertyBuffer, int PropertyBufferSize);
  [DllImport("setupapi.dll")]
  public static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);
  [DllImport("setupapi.dll")]
  public static extern bool SetupDiCallClassInstaller(int InstallFunction, IntPtr DeviceInfoSet, ref SP_DEVINFO_DATA DeviceInfoData);
  [DllImport("newdev.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool UpdateDriverForPlugAndPlayDevices(IntPtr hwndParent, string HardwareId,
      string FullInfPath, int InstallFlags, out bool bRebootRequired);
}
"@

$clsGuid = [Guid]'{62f9c741-b25a-46ce-b54c-9bccce08b6f2}'   # SoftwareDevice
$g = $clsGuid
$h = [RootDev]::SetupDiCreateDeviceInfoList([ref]$g, [IntPtr]::Zero)
if ($h -eq [IntPtr]::Zero) { throw "CreateDeviceInfoList 失败" }

$data = New-Object RootDev+SP_DEVINFO_DATA
$data.cbSize = [Runtime.InteropServices.Marshal]::SizeOf($data)
$DICD_GENERATE_ID = 1
$ok = [RootDev]::SetupDiCreateDeviceInfo($h, 'PawnIO', [ref]$g, $null, [IntPtr]::Zero, $DICD_GENERATE_ID, [ref]$data)
if (-not $ok) { throw "CreateDeviceInfo 失败: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }

$SPDRP_HARDWAREID = 1
$hwid = "Root\PawnIO"
[void][RootDev]::SetupDiSetDeviceRegistryProperty($h, [ref]$data, $SPDRP_HARDWAREID, $hwid, ($hwid.Length + 1) * 2)

$DIF_REGISTERDEVICE = 0x19
$ok = [RootDev]::SetupDiCallClassInstaller($DIF_REGISTERDEVICE, $h, [ref]$data)
if (-not $ok) { throw "RegisterDevice 失败: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }

[void][RootDev]::SetupDiDestroyDeviceInfoList($h)

# ③ 绑定驱动（从 DriverStore 用已入库的 pawnio.inf）
$inf = (Get-WindowsDriver -Online | Where-Object OriginalFileName -like '*pawnio*' | Select-Object -First 1).OriginalFileName
if (-not $inf) { $inf = Join-Path $PSScriptRoot 'pawnio-x64\PawnIO.inf' }
Write-Host "使用 INF: $inf"
$reboot = $false
$ok = [RootDev]::UpdateDriverForPlugAndPlayDevices([IntPtr]::Zero, 'Root\PawnIO', $inf, 0, [ref]$reboot)
Write-Host "UpdateDriver: $ok (需重启=$reboot)"

# ④ 启动设备
Start-Sleep -Seconds 2
pnputil /restart-device "ROOT\PAWNIO\0000" 2>$null | Out-Null
$d = Get-PnpDevice -FriendlyName 'PawnIO' -ErrorAction SilentlyContinue
Write-Host ("设备状态: " + ($d.Status -join ','))
