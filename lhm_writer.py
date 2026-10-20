#!/usr/bin/env python3
"""LHM 温度 writer（管理员常驻）— Python 版替代 cmd 循环。

改进（稳定/性能）：
  - beszel_lhm.exe 常驻（stdin 循环协议），每 QUERY_INTERVAL 秒查询一次，
    不再每轮重新 Open() 硬件枚举（原 cmd 版每 5s spawn 一次，CPU 波峰大）
  - 查询带超时：卡死自动杀子进程并重启（自愈，根治管道死锁）
  - 原子写：先写 .tmp 再 os.replace，读方永远不会看到半行
用法（管理员）：pythonw lhm_writer.py
"""
import os
import subprocess
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LHM_EXE = os.path.join(BASE, 'lhm', 'glances_lhm.exe')     # 大屏自有版（温度+风扇+功耗）
OUT = os.path.join(os.environ.get('TEMP', r'C:\Users\admin\AppData\Local\Temp'), 'lhm_temps.txt')
QUERY_INTERVAL = 10          # 秒；温度变化慢，10s 足够
STALE_LIMIT = 30             # 数据文件超过该秒数视为 writer 失联


def query(proc):
    proc.stdin.write('getSensors\n')                        # 扩展协议（T|F|P|name|value）
    proc.stdin.flush()
    lines = []
    while True:
        line = proc.stdout.readline()
        if not line or line.strip() == '':
            break
        lines.append(line.strip())
    return lines


def spawn():
    p = subprocess.Popen(
        [LHM_EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
        creationflags=0x08000000)   # CREATE_NO_WINDOW：管理员 pythonw 下不弹控制台
    time.sleep(1.0)                       # 等 Computer.Open()
    return p


def main():
    proc = None
    while True:
        try:
            if proc is None or proc.poll() is not None:
                proc = spawn()
            # 查询超时保护：10 秒无响应视为卡死 → 重建子进程
            import threading
            box, done = [], threading.Event()

            def _q():
                try:
                    box.extend(query(proc))
                finally:
                    done.set()

            threading.Thread(target=_q, daemon=True).start()
            if not done.wait(10):
                proc.kill()
                proc = None
                continue
            data = '\n'.join(box) + '\n'
            tmp = OUT + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(data)
            os.replace(tmp, OUT)          # 原子替换
        except Exception:
            try:
                proc and proc.kill()
            except Exception:
                pass
            proc = None
        time.sleep(QUERY_INTERVAL)


if __name__ == '__main__':
    main()
