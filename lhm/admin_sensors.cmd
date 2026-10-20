@echo off
(echo getSensors & ping -n 4 127.0.0.1 >nul) | "D:\workspace_test\github_desk\test\tools\memclean-svc\lhm\glances_lhm.exe" > "C:\Users\admin\AppData\Local\Temp\lhm_sensors_test.txt" 2>&1
