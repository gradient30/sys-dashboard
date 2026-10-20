@echo off
(echo getTemps & ping -n 4 127.0.0.1 >nul) | "%~dp0beszel_lhm.exe" > "%TEMP%\lhm_admin_test.txt" 2>&1
