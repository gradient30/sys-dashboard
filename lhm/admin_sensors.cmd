@echo off
(echo getSensors & ping -n 4 127.0.0.1 >nul) | "%~dp0glances_lhm.exe" > "%TEMP%\lhm_sensors_test.txt" 2>&1
