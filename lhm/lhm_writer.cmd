@echo off
rem LHM 温度 writer（管理员常驻）：每 5 秒采集一次传感器写入共享文件
rem 由 memclean_svc 的 /temps 读取合并；UAC 确认一次后常驻，重启机器后需重新提权启动
:loop
(echo getTemps & ping -n 4 127.0.0.1 >nul) | "D:\workspace_test\github_desk\test\tools\memclean-svc\lhm\beszel_lhm.exe" > "C:\Users\admin\AppData\Local\Temp\lhm_temps.txt" 2>&1
ping -n 6 127.0.0.1 >nul
goto loop
