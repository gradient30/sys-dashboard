# 重启 LHM writer（管理员）：清旧进程（cmd 版/py 版/卡死 LHM）→ 起新版 python writer
Get-Process beszel_lhm -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
  Where-Object { $_.CommandLine -like '*lhm_writer*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*lhm_writer.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
Start-Process -FilePath 'C:\Python\Python311\pythonw.exe' `
  -ArgumentList (Join-Path $PSScriptRoot '..\lhm_writer.py') -WindowStyle Hidden
Write-Output 'py-writer restarted'
