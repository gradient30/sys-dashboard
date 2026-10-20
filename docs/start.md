# 启动说明

日常：开机自启（Glances + 本服务），无需操作。

重启机器后恢复 CPU/存储温度：
```powershell
cd D:\workspace_test\github_desk\test\tools\memclean-svc
.\dev.ps1        # 选 [5]（或 [2] 一键补齐全部）
```

手动起服务：`pythonw memclean_svc.py`（已在 HKCU Run 注册自启）
温度 writer：`Start-Process lhm\restart_writer.ps1`（提权，UAC 一次）
清理桥：计划任务 MemReduct-Clean（持久，由服务经 schtasks 触发，勿删）
