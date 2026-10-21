#!/usr/bin/env python3
"""内存清理触发服务 — 供 Glances 大屏面板按钮调用。

监听 0.0.0.0:61210（Tailscale 网内可达）：
  GET  /ping            健康检查
  POST /cleanup/<token> 执行清理（孤儿 cmd/pwsh 终止 + 全进程工作集修剪），
                        返回 JSON：{"ok":true,"before_gib":..,"after_gib":..,"freed_gib":..,...}

用法：  python memclean_svc.py            （前台运行；管理菜单见 dev.ps1）
依赖：  psutil（已随系统 Python 安装）
安全：  cleanup 需要路径 token；只绑 Tailscale/本机可达端口，未鉴权者仅能 ping。
"""
import ctypes
import json
import os
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

import diskclean                              # 磁盘三级清理引擎（A 缓存/B 回收区/C 提权桥）

PORT = 61210
TOKEN = 'Glc-2026-mc-7f3a91'   # 与大屏页面 JS 保持一致（内网防护级）
LHM_EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lhm', 'glances_lhm.exe')
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
DB_PATH = os.path.join(DATA_DIR, 'history.db')
GLANCES = 'http://localhost:61208/api/4'

LOG = r'C:\Users\admin\AppData\Local\Temp\memclean_svc.log'
psapi = ctypes.windll.psapi
_lock = threading.Lock()       # 清理互斥（按钮防抖）


def free_gib():
    m = psutil.virtual_memory()
    return m.available / 2**30


# ---------- memreduct 桥（严格方案：schtasks 触发 NT 层 8 区域清理） ----------
MEMREDUCT_TASK = 'MemReduct-Clean'
MEMREDUCT_RESULT = r'C:\ProgramData\memreduct\clean-result.json'


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def do_memreduct_clean(timeout=150):
    """经 MemReduct-Clean 计划任务桥触发 memreduct 同源清理，轮询结果文件。"""
    t0 = _mtime(MEMREDUCT_RESULT)
    r = subprocess.run(['schtasks', '/run', '/tn', MEMREDUCT_TASK],
                       capture_output=True, text=True, timeout=15,
                       creationflags=0x08000000)      # CREATE_NO_WINDOW：清理触发时同样不弹窗
    if r.returncode != 0:
        raise RuntimeError(f'schtasks 失败: {r.stderr.strip()[:100]}')
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.5)
        if _mtime(MEMREDUCT_RESULT) > t0:
            time.sleep(0.5)                       # 等写完
            with open(MEMREDUCT_RESULT, encoding='utf-8-sig') as f:
                return json.load(f)
    raise TimeoutError('memreduct 结果超时')


def do_cleanup():
    """孤儿 cmd/pwsh 终止 + 工作集修剪，返回统计。"""
    killed = 0
    procs = {p.pid: p for p in psutil.process_iter(['name'])}
    for pid, p in list(procs.items()):
        try:
            if (p.info['name'] or '').lower() in ('cmd.exe', 'pwsh.exe'):
                if p.ppid() not in procs:          # 父进程已死 → 孤儿
                    p.terminate()
                    killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    trimmed = 0
    for p in psutil.process_iter():
        try:
            h = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, p.pid)  # PROCESS_ALL_ACCESS
            if h:
                psapi.EmptyWorkingSet(h)
                ctypes.windll.kernel32.CloseHandle(h)
                trimmed += 1
        except Exception:
            pass
    return {'killed_orphans': killed, 'trimmed': trimmed}


class LhmReader:
    """常驻 glances_lhm.exe 子进程（大屏自有 LHM 封装，协议：stdin 'getSensors' →
    stdout 多行 'K|name|value'（K=T 温度/F 风扇 RPM/P 功耗 W），空行结束）。
    GPU 类传感器用户态可读；CPU/存储/功耗需管理员 writer。"""
    _lock = threading.Lock()
    _proc = None
    _cache = (0, [])

    @staticmethod
    def _parse(line):
        """解析一行传感器数据 → dict 或 None。兼容旧两段式（按温度）。"""
        parts = [p.strip() for p in line.strip().split('|')]
        if len(parts) == 3 and parts[0] in ('T', 'F', 'P'):
            kind, name, val = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            kind, name, val = 'T', parts[0], parts[1]
        else:
            return None
        try:
            v = float(val)
        except ValueError:
            return None
        limit = {'T': 150, 'F': 30000, 'P': 1000}[kind]
        if name and 0 <= v < limit:
            return {'name': name, 'value': v, 'kind': kind}
        return None

    @classmethod
    def _get(cls):
        if cls._proc is None or cls._proc.poll() is not None:
            cls._proc = subprocess.Popen(
                [LHM_EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                creationflags=0x08000000)   # CREATE_NO_WINDOW：防止常驻子进程弹控制台
            time.sleep(0.8)                       # 等 Computer.Open()
        cls._proc.stdin.write('getSensors\n')
        cls._proc.stdin.flush()
        out = []
        while True:
            line = cls._proc.stdout.readline()
            if not line or line.strip() == '':
                break
            d = cls._parse(line)
            if d:
                out.append(d)
        return out

    @classmethod
    def temps(cls, max_age=3.0):
        with cls._lock:
            ts, cached = cls._cache
            if time.time() - ts < max_age:
                return cached
            out = cls._get()                   # 用户态子进程（GPU 温度/风扇/功耗）
            # 并入管理员 writer 的共享文件（全量传感器，30s 新鲜度），按 名称+类型 去重
            try:
                f = os.path.join(os.environ.get('TEMP', '/tmp'), 'lhm_temps.txt')
                if time.time() - os.path.getmtime(f) < 30:
                    seen = {(t['name'], t.get('kind', 'T')) for t in out}
                    for line in open(f, encoding='utf-8', errors='replace'):
                        d = cls._parse(line)
                        if d and (d['name'], d['kind']) not in seen:
                            out.append(d)
                            seen.add((d['name'], d['kind']))
            except OSError:
                pass                          # writer 未运行（重启后未提权）——仅 GPU 传感器
            for z in thermal_zones():
                if z['name'] not in {t['name'] for t in out}:
                    out.append(z)
            cls._cache = (time.time(), out)
            return out


_tz_cache = (0, [])
_TZ_CMD = ['powershell', '-NoProfile', '-Command',
           "(Get-Counter '\\Thermal Zone Information(*)\\Temperature').CounterSamples"
           " | ForEach-Object { '{0}|{1:N2}' -f $_.InstanceName, ($_.CookedValue - 273.15) }"]


def thermal_zones(max_age=30.0):
    """ACPI 热区（perf counter，后台线程预刷，本函数只读缓存）。"""
    global _tz_cache
    ts, cached = _tz_cache
    if time.time() - ts < max_age:
        return cached
    try:
        r = subprocess.run(_TZ_CMD, capture_output=True, text=True, timeout=20,
                           creationflags=0x08000000)   # CREATE_NO_WINDOW：pythonw 下不弹控制台窗口
        out = []
        for line in r.stdout.splitlines():
            name, _, val = line.strip().partition('|')
            try:
                v = float(val)
                if 0 < v < 150:
                    out.append({'name': f'主板热区 {name}', 'value': v})
            except ValueError:
                pass
        _tz_cache = (time.time(), out)
    except Exception:
        pass
    return _tz_cache[1]


def _bg_refresh():
    """后台预刷：热区 30s / writer 文件健康检查，让 /temps 恒为纯缓存读。"""
    while True:
        thermal_zones(0)          # 强制刷新（max_age=0）
        time.sleep(30)


def _glance(ep):
    try:
        with urllib.request.urlopen(f'{GLANCES}/{ep}', timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


_rec_lock = threading.Lock()


def _bg_record():
    """历史记录器：60s 采样关键指标入 SQLite（WAL）。数据用于持久历史与跨日对比。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS metrics(
        ts INTEGER PRIMARY KEY, cpu REAL, mem REAL, swap REAL,
        t_cpu REAL, t_gpu REAL, net_rx REAL, net_tx REAL,
        p_cpu REAL, p_gpu REAL, fan REAL)''')
    db.commit()
    while True:
        try:
            cpu = _glance('cpu'); mem = _glance('mem'); swap = _glance('memswap'); net = _glance('network')
            if cpu and cpu.get('total') == 0 and cpu.get('idle') == 0:
                cpu = None  # Glances Webserver 并发竞态坏帧（total=0 时 idle 应为 100）：写 NULL，勿以 0 污染历史
            tp = _temps_snapshot()
            db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
                int(time.time()),
                cpu and round(cpu.get('total', 0), 2),
                mem and round(mem.get('percent', 0), 2),
                swap and round(swap.get('percent', 0), 2),
                tp['t_cpu'], tp['t_gpu'],
                net and round(sum(n.get('bytes_recv_rate_per_sec') or 0 for n in net), 1),
                net and round(sum(n.get('bytes_sent_rate_per_sec') or 0 for n in net), 1),
                tp['p_cpu'], tp['p_gpu'], tp['fan'],
            ))
            db.commit()
        except Exception:
            pass
        time.sleep(60)


def _temps_snapshot():
    """从当前传感器缓存提取摘要（温度/功耗/风扇）。"""
    temps = LhmReader.temps(max_age=15)
    t_cpu = t_gpu = p_cpu = p_gpu = fan = None
    for t in temps:
        n, v = t.get('name', ''), t.get('value')
        k = t.get('kind', 'T')
        if v is None:
            continue
        if k == 'T':
            if n in ('CPU Package', 'Core Max') and (t_cpu is None or v > t_cpu):
                t_cpu = v
            elif n == 'GPU Core':
                t_gpu = v
        elif k == 'P':
            if n == 'CPU Package':
                p_cpu = v
            elif n == 'GPU Package':
                p_gpu = v
        elif k == 'F' and v > 0:
            fan = max(fan or 0, v)
    return {'t_cpu': t_cpu, 't_gpu': t_gpu, 'p_cpu': p_cpu, 'p_gpu': p_gpu, 'fan': fan}


DASH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
_STATIC = {   # 短路径静态入口：http://<host>:61210/ 直达大屏
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/clean': ('clean.html', 'text/html; charset=utf-8'),          # 磁盘清理控制台（五步向导）
    '/clean.html': ('clean.html', 'text/html; charset=utf-8'),
    '/echarts.min.js': ('echarts.min.js', 'application/javascript; charset=utf-8'),
    '/favicon.ico': ('echarts.min.js', 'application/javascript'),   # 占位防 404 噪音
}


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')   # 大屏同源部署，可留
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in _STATIC:
            fname, ctype = _STATIC[self.path]
            try:
                with open(os.path.join(DASH_DIR, fname), 'rb') as f:
                    body = f.read()
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                self._json(404, {'ok': False})
            return
        if self.path == '/ping':
            self._json(200, {'ok': True, 'svc': 'memclean', 'ts': time.time()})
        elif self.path == '/temps':
            t = LhmReader.temps(max_age=3.0)
            f = os.path.join(os.environ.get('TEMP', '/tmp'), 'lhm_temps.txt')
            try:
                fresh = time.time() - os.path.getmtime(f) < 60
            except OSError:
                fresh = False
            self._json(200, {'ok': not any('error' in x for x in t), 'temps': t, 'writer_fresh': fresh})
        elif self.path.startswith('/history'):
            q = urllib.parse.urlparse(self.path).query
            hours = int(urllib.parse.parse_qs(q).get('hours', ['48'])[0])
            since = int((time.time() - hours * 3600) * 1000)
            with _rec_lock:
                rows = _db.execute('SELECT ts*1000,cpu,mem,swap,t_cpu,t_gpu,net_rx,net_tx,p_cpu,p_gpu,fan FROM metrics WHERE ts*1000>=? ORDER BY ts', (since,)).fetchall()
            self._json(200, {'ok': True, 'points': [
                {'ts': r[0], 'cpu': r[1], 'mem': r[2], 'swap': r[3], 't_cpu': r[4], 't_gpu': r[5], 'net_rx': r[6], 'net_tx': r[7], 'p_cpu': r[8], 'p_gpu': r[9], 'fan': r[10]}
                for r in rows]})
        elif self.path.split('?')[0] == '/disk/scan':
            fresh = 'fresh=1' in (urllib.parse.urlparse(self.path).query or '')
            s = diskclean.scan_snapshot(force=fresh)
            body = dict(state=s['state'], progress=s['progress'], ts=s['ts'], stale=s['stale'])
            # scanning 态透传 partial（append-only 中间结果），前端可边扫边渲染
            d = s['data'] or (s.get('partial') if s['state'] == 'scanning' else None)
            if d:
                body.update(ok=True, fs=d.get('fs') or [], items=d.get('items') or [],
                            suggest=d.get('suggest'), c_env=d.get('c_env') or {})
                if s['data'] is None:
                    body['partial'] = True      # 清单尚未定稿，前端按增量 feed 处理
            else:
                body.update(ok=True, fs=[], items=[], suggest=None)
            # 重扫进行中（有旧缓存时 state 恒 ready）透传实时水位：报告页"后水位"取 fs 阶段实测
            p = s.get('partial')
            if p and p.get('fs') and (s['progress'] or {}).get('phase') not in ('ready', 'idle'):
                body['live_fs'] = p['fs']
            self._json(200, body)
        elif self.path.startswith(f'/disk/task/{TOKEN}/'):
            tid = self.path.rstrip('/').rsplit('/', 1)[-1]
            t = diskclean.task_status(tid)
            self._json(200 if t else 404, {'ok': t is not None, 'task': t})
        elif self.path.startswith('/disk/task/'):
            self._json(403, {'ok': False, 'err': 'bad token'})
        else:
            self._json(404, {'ok': False})

    def do_POST(self):
        if self.path.startswith(f'/disk/clean/{TOKEN}'):
            # 磁盘清理：dry-run 同步返回计划；commit 起异步任务（diskclean 内部自持 _exec_lock 互斥，
            # 勿再套全局 _lock——否则 30 分钟 C 级任务会把内存清理按钮一并锁死）
            try:
                n = int(self.headers.get('Content-Length') or 0)
                req = json.loads(self.rfile.read(n).decode()) if n else {}
            except Exception:
                self._json(400, {'ok': False, 'err': 'bad json body'})
                return
            try:
                r = diskclean.start_clean(level=req.get('level'), commit=bool(req.get('commit')),
                                          b_paths=req.get('b_paths') or [], c_opts=req.get('c_opts') or {})
                self._json(202 if r.get('task_id') else 200, r)
            except diskclean.BusyError:
                self._json(429, {'ok': False, 'err': '磁盘清理进行中，请稍候'})
            except (ValueError, RuntimeError, diskclean.RefusedError) as e:
                self._json(400, {'ok': False, 'err': str(e)[:200]})
            return
        if not self.path.startswith(f'/cleanup/{TOKEN}'):
            self._json(403, {'ok': False, 'err': 'bad token'})
            return
        if not _lock.acquire(blocking=False):
            self._json(429, {'ok': False, 'err': '清理进行中，请稍候'})
            return
        before = free_gib()
        try:
            # ① 严格方案：memreduct 桥（NT 层 8 区域清理）
            try:
                mr = do_memreduct_clean()
                out = {'ok': bool(mr.get('ok')), 'engine': 'memreduct',
                       'freed_gib': mr.get('freedGB'), 'before_gib': mr.get('beforeGB'), 'after_gib': mr.get('afterGB'),
                       'duration_ms': mr.get('durationMs'),
                       'regions': {k: v.get('state') for k, v in (mr.get('regions') or {}).items()}}
                with open(LOG, 'a', encoding='utf-8') as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [memreduct] freed={out['freed_gib']}GB dur={out['duration_ms']}ms\n")
                self._json(200, out)
                return
            except Exception as e:
                # ② 兜底：本地 EmptyWorkingSet 方案（except as e 的 e 在块尾被 del，
                #    必须块内取出；str(e) 供日志与降级原因透传）
                err = str(e)[:120]
            stats = do_cleanup()
            time.sleep(2)                       # 等待管理器回收
            after = free_gib()
            out = {'ok': True, 'engine': 'fallback',
                   'before_gib': round(before, 1), 'after_gib': round(after, 1),
                   'freed_gib': round(after - before, 1), 'degraded_reason': err, **stats}
            try:
                with open(LOG, 'a', encoding='utf-8') as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [fallback:{err[:40]}] freed={out['freed_gib']}GiB trimmed={stats['trimmed']}\n")
            except OSError:
                pass                            # 日志失败不得影响清理响应（审计 P0-2）
            self._json(200, out)
        finally:
            _lock.release()

    def log_message(self, *a):                  # 静默访问日志
        pass


if __name__ == '__main__':
    print(f'memclean service on 0.0.0.0:{PORT} (token in path)')
    os.makedirs(DATA_DIR, exist_ok=True)
    _db = sqlite3.connect(DB_PATH, check_same_thread=False)
    _db.execute('PRAGMA journal_mode=WAL')
    _db.execute('''CREATE TABLE IF NOT EXISTS metrics(
        ts INTEGER PRIMARY KEY, cpu REAL, mem REAL, swap REAL,
        t_cpu REAL, t_gpu REAL, net_rx REAL, net_tx REAL,
        p_cpu REAL, p_gpu REAL, fan REAL)''')
    for col in ('p_cpu', 'p_gpu', 'fan'):
        try: _db.execute(f'ALTER TABLE metrics ADD COLUMN {col} REAL')
        except Exception: pass
    _db.commit()
    threading.Thread(target=_bg_refresh, daemon=True).start()    # 热区预刷 30s
    threading.Thread(target=_bg_record, daemon=True).start()     # 历史记录 60s
    threading.Thread(target=diskclean.Scanner.refresh_loop, daemon=True).start()   # 磁盘扫描（600s 缓存）
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
