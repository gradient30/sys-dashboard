#!/usr/bin/env python3
"""磁盘三级清理引擎 — memclean_svc 的磁盘清理模块（大屏「磁盘清理」面板后端）。

分级依据 playbook（test/docs/windows-disk-cleanup-playbook.md）：
  A 纯缓存     可静默清理（重下即可，零数据丢失）
  B 疑似废弃   30 天文件级 mtime 未动 → 勾选后移动到 E:\\.trash\\<日期>\\（可回滚）
  C 系统级     VHDX 压缩 / pagefile 迁移，经计划任务 Glc-DiskClean-C 提权执行
               （安装：管理员运行 elevated\\install_disk_task.ps1）

安全军规（playbook §9.4，全部落实）：
  默认 dry-run（commit=false 零写盘）；B 级删除即回收站；保护名单扫描标 protected +
  执行器二次校验（防 TOCTOU）；Docker 只 image/builder prune——【绝不 --volumes /
  system prune】；释放量以 disk_usage 前后差值为准，不信估算；审计落
  data\\clean-audit.jsonl。

红线（永不触碰，代码与文档双写）：注册表（除 pagefile 的 PagingFiles 指定键）、
  .pnpm-store（硬链接 store）、运行中容器数据卷、pagefile.sys/hiberfil.sys 本体、
  D:\\workspace_test 等研发资产（见 PROTECTED）。
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

import psutil

LP = os.environ.get('LOCALAPPDATA', '')       # C:\Users\<u>\AppData\Local
UP = os.environ.get('USERPROFILE', '')        # C:\Users\<u>
BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, 'data')
AUDIT_PATH = os.path.join(DATA_DIR, 'clean-audit.jsonl')

NO_WINDOW = 0x08000000                        # CREATE_NO_WINDOW：服务既定纪律

# ---------- A 级规则（kind: whole=整目录删 | age_tree=只删>age_days 子项 | glob_age=通配+龄 | docker=CLI） ----------
# 路径缺失（如已迁 E 盘的 NuGet/gradle）= 0 字节优雅跳过，不报错。
A_RULES = [
    dict(key='user_temp', label='用户 Temp（>3 天）', kind='age_tree',
         root=os.path.join(LP, 'Temp'), age_days=3,
         note='Windows/软件运行临时文件；只删 3 天前的，正在运行程序的锁文件自动跳过'),
    dict(key='npm_cache', label='npm 缓存', kind='whole', root=os.path.join(LP, 'npm-cache'),
         note='npm 下载缓存包；删除后下次 install 重新联网下载，不影响已装项目'),
    dict(key='uv_cache', label='uv 缓存', kind='whole', root=os.path.join(LP, 'uv', 'cache'),
         note='uv（Python 包管理器）下载缓存；删除后重新下载，不影响已建环境'),
    dict(key='pip_cache', label='pip 缓存', kind='whole', root=os.path.join(LP, 'pip', 'cache'),
         note='wheel 缓存，重下即可（等同 pip cache purge）'),
    dict(key='puppeteer', label='Puppeteer 浏览器', kind='whole', root=os.path.join(UP, '.cache', 'puppeteer'),
         note='测试用浏览器二进制；删除后首次运行自动重下'),
    dict(key='nuget_v3', label='NuGet v3-cache', kind='whole', root=os.path.join(LP, 'NuGet', 'v3-cache'),
         note='.NET 包下载缓存；只删 v3-cache，不动 .nuget\\packages（全局包本体）'),
    dict(key='codex_tmp', label='codex 安装临时目录（>30 天）', kind='glob_age',
         root=os.path.join(UP, '.cache', 'codex-runtimes'), pattern='codex-runtime-install-*', age_days=30,
         note='codex 升级残留的旧版安装包；保留 codex-primary-runtime 主目录'),
    # 红线：docker 只允许这两条字面量命令，绝无 --volumes（playbook §6.1：
    # 卷里是数据库数据，stopped 容器引用的卷会被连带删除）
    dict(key='docker_img', label='Docker 无引用镜像', kind='docker', cmds=[['docker', 'image', 'prune', '-a', '-f']],
         note='删未被任何容器引用的镜像层；运行中容器与其镜像不受影响'),
    dict(key='docker_bld', label='Docker 构建缓存', kind='docker', cmds=[['docker', 'builder', 'prune', '-a', '-f']],
         note='镜像构建中间层；下次 build 重新生成（变慢一次，不影响产物）'),
]

# ---------- B 级候选（种子大件 + D:\ 顶层自动发现） ----------
B_SEEDS = [
    dict(path=r'D:\Program Files (x86)\Tencent\qqhistory', note='QQ 聊天记录，删了不可恢复'),
    dict(path=os.path.join(LP, 'Feishu'), note='云端消息还在，重装需重新登录'),
    dict(path=r'D:\Microsoft Visual Studio', note='建议走正规卸载而非删目录'),
    # AI 环境大件（种子同样过 ≥300MB 阈值 + 30 天在用检测；B 级=勾选后移入回收站可回滚）
    dict(path=os.path.join(UP, '.cache', 'huggingface'), note='AI 模型缓存（HuggingFace）：删除即丢，重下耗时长'),
    dict(path=os.path.join(UP, '.ollama'), note='本地大模型库（Ollama）：删除即丢，重新 pull 耗时长'),
]
B_DISCOVER = dict(root='D:\\', min_bytes=300 * 2**20, stale_days=30)   # 顶层发现阈值：≥300MB 且 30 天未动
TRASH_ROOT = r'E:\.trash'                    # B 级回收区：E:\.trash\<yyyyMMdd>\<原名>（人工保留 30 天）
TRASH_DRIVE = 'e:'

# ---------- 保护名单（playbook §4 + D:\Docker=VHDX 宿主；normcase 比较） ----------
PROTECTED = [
    r'D:\workspace_test', r'D:\ProgramData', r'D:\flutter', r'D:\dotnet-sdk10',
    r'D:\SonicAgent_Env', r'D:\Programs', r'D:\Docker', r'D:\.pnpm-store',
    os.path.join(UP, '.gradle', 'caches'),                    # 网络差环境：删了构建要重下
    os.path.join(LP, 'wsl'),                                  # WSL 发行版
] + [os.path.join(UP, d) for d in (
    '.codex', '.codex-g', '.grok', '.claude', '.vscode', '.cursor',
    'orca', 'agent-tools', 'KerWork', 'WorkBuddy', 'Projects', 'bin')]
SRC_DRIVES = {'c:', 'd:'}                     # 删除源盘符白名单
ALLOWED_TRASH = r'e:\.trash'                  # 唯一合法回收区前缀

# ---------- C 级 ----------
VHDX_PATH = r'D:\Docker\wsl\disk\docker_data.vhdx'
DOCKER_EXE = r'C:\Program Files\Docker\Docker\Docker Desktop.exe'
C_TASK = 'Glc-DiskClean-C'
C_DIR = r'C:\ProgramData\glances-diskclean'   # 服务（用户态）写 params / 提权任务写 progress+result
C_PARAMS = os.path.join(C_DIR, 'c-params.json')
C_PROGRESS = os.path.join(C_DIR, 'c-progress.json')
C_RESULT = os.path.join(C_DIR, 'c-result.json')
C_BRIDGE_TIMEOUT = 900                        # > 全链 5-10 分钟；任务侧限 20 分钟

_PS = ['powershell', '-NoProfile', '-Command']
_PAGEFILE_CMD = _PS + ["Get-CimInstance Win32_PageFileUsage | ForEach-Object"
                       " { '{0}|{1}|{2}' -f $_.Name,$_.CurrentUsage,$_.AllocatedBaseSize }"]
_VOL_E_CMD = _PS + ["(Get-Volume -DriveLetter E).FileSystem"]


class BusyError(Exception):
    pass


class RefusedError(Exception):
    """安全校验拒绝（保护名单/盘符/回收区前缀），必须原样透出给前端。"""


def _run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          creationflags=NO_WINDOW, errors='replace')


def _human(n):
    for u in ('B', 'K', 'M', 'G', 'T'):
        if n < 1024 or u == 'T':
            return f'{n:.1f}{u}' if u != 'B' else f'{n}B'
        n /= 1024


# ================= 扫描器（后台线程 + (ts, data) 缓存，端点恒缓存读） =================
def _record_disk_hist(fs, vhdx):
    """每轮扫描完成记录磁盘水位 + Docker vhdx 体积到 data/history.db.disk_hist。
    独立小表（不动 metrics），WAL 并发安全 + busy_timeout 兜底；任何失败静默（不拖累扫描）。"""
    try:
        import sqlite3
        row = {f['drive']: f['free_gib'] for f in fs}
        vhdx_g = vhdx.get('size_gib') or 0
        db = os.path.join(DATA_DIR, 'history.db')
        con = sqlite3.connect(db, timeout=10)
        try:
            con.execute('PRAGMA busy_timeout=10000')
            con.execute('CREATE TABLE IF NOT EXISTS disk_hist('
                        'ts REAL, c_free REAL, d_free REAL, e_free REAL, vhdx_g REAL)')
            con.execute('INSERT INTO disk_hist VALUES (?,?,?,?,?)',
                        (time.time(), row.get('C', 0), row.get('D', 0), row.get('E', 0), vhdx_g))
            # 只留 60 天（600s 一轮 ≈ 8600 行/月，封顶防自身膨胀）
            con.execute('DELETE FROM disk_hist WHERE ts < ?', (time.time() - 60 * 86400,))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


class Scanner:
    _lock = threading.Lock()
    _cache = (0.0, None)                       # (ts, data)
    _dirty = True
    _wake = threading.Event()                  # fresh=1 立即唤醒扫描线程（免 30s 盲等）
    _partial = None                            # scanning 期间 {'fs': [...], 'items': [...]}，其余态恒 None
    _state = {'phase': 'idle', 'pct': 0, 'note': '', 'steps': []}   # 扫描进度（/disk/scan 恒可读）
    MAX_AGE = 600.0

    STEPS = [('fs', '盘符水位'), ('du', 'A 级缓存测量'), ('b_find', 'B 级废弃发现'),
             ('docker', 'Docker 可回收'), ('c_info', 'C 级现状')]

    @classmethod
    def refresh_loop(cls):
        """daemon 线程：缓存过期（或被标记脏）即重扫，空闲 wait(30) 可被 fresh 唤醒。"""
        while True:
            if cls._dirty or time.time() - cls._cache[0] > cls.MAX_AGE:
                try:
                    cls._scan_once()
                except Exception:
                    cls._fail_scan()           # 失败也要复位状态（否则 phase 卡死在半途），保住旧缓存
            cls._wake.wait(30)
            cls._wake.clear()

    @classmethod
    def snapshot(cls, force=False):
        if force:
            cls._dirty = True
            cls._wake.set()
        with cls._lock:                        # state/partial 同锁一致性快照（临界区纯赋值，无 IO）
            progress = {**cls._state, 'steps': [dict(s) for s in cls._state['steps']]}
            partial = dict(cls._partial) if cls._partial else None
        ts, data = cls._cache
        return {'state': 'ready' if data else ('scanning' if cls._state['phase'] != 'idle' else 'idle'),
                'progress': progress, 'ts': ts,
                'stale': data is None or time.time() - ts > cls.MAX_AGE,
                'data': data, 'partial': partial}

    # ---- 内部：五个阶段 fs → du → b_find → docker → c_info ----
    @classmethod
    def _phase(cls, name, pct):
        cls._step_state(name, 'run')
        with cls._lock:
            cls._state['pct'] = pct

    @classmethod
    def _begin_scan(cls):
        with cls._lock:
            cls._partial = {'fs': [], 'items': []}
            cls._state.update(phase='fs', pct=0, note='', steps=[
                {'name': n, 'label': lb, 'state': 'wait', 'note': ''} for n, lb in cls.STEPS])
        cls._step_state('fs', 'run')

    @classmethod
    def _step_state(cls, name, st, note=''):
        with cls._lock:
            for s in cls._state['steps']:
                if s['name'] == name:
                    s['state'] = st
                    if note:
                        s['note'] = note
                    cls._state['phase'] = name
                    return

    @classmethod
    def _tick(cls, pct, note=''):
        """阶段内推进（b_find 逐候选测量时 note 即前端 feed 文案）。"""
        with cls._lock:
            cls._state.update(pct=pct, note=note)

    @classmethod
    def _publish(cls, data):
        """发布中间结果（append-only：单项 bytes 为 du 终值，前端只需处理新增）。"""
        with cls._lock:
            cls._partial = {'fs': list(data['fs']), 'items': list(data['items'])}

    @classmethod
    def _fail_scan(cls):
        with cls._lock:
            for s in cls._state['steps']:
                if s['state'] == 'run':
                    s['state'] = 'fail'
            cls._state.update(phase='idle', note='扫描失败，保留旧缓存')
            cls._partial = None

    @classmethod
    def _scan_once(cls):
        cls._begin_scan()
        fs = []
        for d in ('C:', 'D:', 'E:'):
            try:
                u = psutil.disk_usage(d + '\\')
                fs.append({'drive': d[0], 'total_gib': round(u.total / 2**30, 1),
                           'free_gib': round(u.free / 2**30, 1), 'used_pct': round(u.percent, 1)})
            except OSError:
                pass
        data = {'fs': fs, 'items': [], 'ts': time.time()}
        cls._publish(data)                      # 水位先行：扫描开始 ~1s 内前端即有盘符条
        cls._step_state('fs', 'done')
        cls._phase('du', 10)

        # A 级 du
        for i, r in enumerate(A_RULES):
            cls._phase('du', 10 + int(25 * (i + 1) / len(A_RULES)))
            cls._tick(10 + int(25 * (i + 1) / len(A_RULES)), r['label'])
            if r['kind'] == 'docker':
                continue                        # docker 项在 docker 阶段测
            item = {'key': r['key'], 'level': 'A', 'label': r['label'],
                    'note': r.get('note', ''), 'exists': False, 'bytes': 0}
            if r['kind'] == 'glob_age':
                item['path'] = r['root']
                if os.path.isdir(r['root']):
                    item['exists'] = True
                    for name in os.listdir(r['root']):
                        if r.get('pattern') and not re.fullmatch(r['pattern'].replace('*', '.*'), name):
                            continue            # 只统计匹配项（保留 codex-primary-runtime）
                        p = os.path.join(r['root'], name)
                        b, _f, _pt = cls._du(p, 10)
                        item['bytes'] += b
            elif os.path.isdir(r['root']):
                item.update(exists=True, path=r['root'])
                b, _f, partial = cls._du(r['root'], 20)
                item['bytes'], item['partial'] = b, partial
            else:
                item['path'] = r['root']
            item['bytes_human'] = _human(item['bytes'])
            data['items'].append(item)
            cls._publish(data)

        # B 级：种子 + D:\ 顶层发现（≥300MB、非保护、30 天文件级未动）
        cls._step_state('du', 'done')
        cls._phase('b_find', 40)
        cands = []
        seen = set()
        for s in B_SEEDS:
            if os.path.exists(s['path']) and os.path.normcase(s['path']) not in seen:
                cands.append(s)
                seen.add(os.path.normcase(s['path']))
        try:
            for e in os.scandir(B_DISCOVER['root']):
                if e.is_dir(follow_symlinks=False):
                    p = e.path
                    nc = os.path.normcase(p)
                    if nc in seen or _is_protected(p):
                        continue
                    cands.append(dict(path=p, note='30 天未动的顶层目录'))
                    seen.add(nc)
        except OSError:
            pass
        cls._tick(42, f'候选 {len(cands)} 个，逐个测量…')
        for ci, c in enumerate(cands):
            # b_find 是 30~60s 大头：per-candidate 进度让黑盒变白盒
            cls._tick(42 + int(15 * (ci + 1) / max(len(cands), 1)),
                      f"测量 {os.path.basename(c['path'].rstrip(chr(92)))}")
            b, _f, partial = cls._du(c['path'], 15)
            if b < B_DISCOVER['min_bytes']:
                continue
            stale = cls._is_stale(c['path'], B_DISCOVER['stale_days'])
            if stale is not True:               # None(不可读)/False(在用) 都不进清单
                continue
            data['items'].append({'key': 'b_' + os.path.basename(c['path'].rstrip('\\')).lower()[:40],
                                  'level': 'B', 'label': os.path.basename(c['path'].rstrip('\\')),
                                  'path': c['path'], 'bytes': b, 'bytes_human': _human(b),
                                  'stale': True, 'protected': False, 'partial': partial,
                                  'note': c.get('note', '')})
            cls._publish(data)
        # 顶层发现逐个 du 慢：阶段性推进
        cls._phase('b_find', 60)

        # docker 可回收量
        cls._step_state('b_find', 'done')
        cls._phase('docker', 70)
        docker_ok = True
        try:
            r = _run(['docker', 'system', 'df', '--format', '{{json .}}'], timeout=15)
            docker_ok = r.returncode == 0
        except Exception:
            docker_ok = False
        for key, label in (('docker_img', 'Docker 无引用镜像'), ('docker_bld', 'Docker 构建缓存')):
            data['items'].append({'key': key, 'level': 'A', 'label': label, 'exists': docker_ok,
                                  'bytes': 0, 'bytes_human': '—' if docker_ok else '不可用',
                                  'note': '' if docker_ok else 'Docker 未运行或不可达，跳过'})

        # C 级信息（只读现状）
        cls._publish(data)
        cls._step_state('docker', 'done')
        cls._phase('c_info', 85)
        vhdx = {'available': os.path.isfile(VHDX_PATH)}
        if vhdx['available']:
            vhdx['path'], vhdx['size_gib'] = VHDX_PATH, round(os.path.getsize(VHDX_PATH) / 2**30, 2)
        data['items'].append({'key': 'c_vhdx', 'level': 'C', 'label': 'Docker VHDX 压缩',
                              'bytes': 0, 'bytes_human': f"{vhdx.get('size_gib', '—')}G",
                              'info': vhdx, 'note': '中断 Docker/WSL 5~10 分钟，容器自动恢复'})
        pf = cls._pagefile_info()
        data['items'].append({'key': 'c_pagefile', 'level': 'C', 'label': '页面文件迁移 E:',
                              'bytes': 0,
                              'bytes_human': f"{sum(c['allocated_mb'] for c in pf.get('current', [])) // 1024}G"
                                             if pf.get('available') else '—',
                              'info': pf, 'note': '注册表一次到位，重启后生效'})
        vol = _run(_VOL_E_CMD, timeout=15)
        e_ntfs = 'NTFS' in (vol.stdout or '').upper() and vol.returncode == 0
        e_free = next((f['free_gib'] for f in fs if f['drive'] == 'E'), 0)
        data['c_env'] = {'e_ntfs': e_ntfs, 'e_free_gib': e_free}

        data['suggest'] = suggest_level(fs, vhdx, pf, e_ntfs)
        cls._publish(data)
        with cls._lock:
            cls._cache = (time.time(), data)
            cls._dirty = False
            cls._partial = None                 # ready 后一切以 data 为准
            for s in cls._state['steps']:
                if s['name'] == 'c_info':
                    s['state'] = 'done'
            cls._state.update(phase='ready', pct=100, note='')   # steps 保留全 done 供前端回看
        _record_disk_hist(fs, vhdx)             # 水位/vhdx 入史（增长可观测，失败不影响扫描）

    @staticmethod
    def _du(root, budget_s):
        """目录大小统计（带预算超时：EDR 慢盘防线）。返回 (bytes, files, partial)。"""
        t0, total, files = time.time(), 0, 0
        partial = False
        for _dp, _dn, fns in os.walk(root, onerror=lambda e: None):
            for fn in fns:
                try:
                    total += os.lstat(os.path.join(_dp, fn)).st_size
                    files += 1
                except OSError:
                    pass
            if time.time() - t0 > budget_s:
                partial = True
                break
        return total, files, partial

    @staticmethod
    def _is_stale(root, days):
        """30 天判定：文件级 mtime walk（早退）。True=未动 False=在用 None=不可读。

        红线：绝不用顶层目录 mtime 判定（playbook §2.2 实战案例：Codex 目录顶层
        显示 3 个月旧、内部当周文件——目录 mtime 只反映直接子项增删）。
        """
        cutoff = time.time() - days * 86400
        try:
            for _dp, _dn, fns in os.walk(root, onerror=lambda e: None):
                for fn in fns:
                    try:
                        if os.lstat(os.path.join(_dp, fn)).st_mtime > cutoff:
                            return False        # 有新文件 → 在用，立即返回
                    except OSError:
                        pass
        except Exception:
            return None
        return True

    @staticmethod
    def _pagefile_info():
        info = {'available': False}
        try:
            r = _run(_PAGEFILE_CMD, timeout=15)
            if r.returncode == 0:
                cur = []
                for line in r.stdout.splitlines():
                    parts = line.strip().split('|')
                    if len(parts) == 3:
                        cur.append({'path': parts[0], 'current_mb': int(parts[1] or 0),
                                    'allocated_mb': int(parts[2] or 0)})
                if cur:
                    info = {'available': True, 'current': cur,
                            'usage_ratio': max(c['current_mb'] / max(c['allocated_mb'], 1) for c in cur)}
        except Exception:
            pass
        return info


def _is_protected(path):
    nc = os.path.normcase(os.path.abspath(path))
    return any(nc.startswith(os.path.normcase(p) + os.sep) or nc == os.path.normcase(p) for p in PROTECTED)


def suggest_level(fs, vhdx, pagefile, e_ntfs):
    """按 C/D 盘最差水位给建议；紧张盘上有 VHDX/pagefile 可 C 级处置时升 ABC。"""
    cd = [f for f in fs if f['drive'] in 'CD']
    if not cd:
        return {'level': 'A', 'reason': '未检出 C/D 盘'}
    worst = max(cd, key=lambda f: f['used_pct'])
    base = 'A' if worst['used_pct'] < 75 else 'AB'
    why = f"{worst['drive']} 盘 {worst['used_pct']:.0f}%，建议 {'A+B' if base == 'AB' else '日常 A 级'}"
    if base == 'AB' and e_ntfs:
        tight = {f['drive'] + ':' for f in cd if f['used_pct'] >= 75}   # 全部紧张盘（非仅 worst）
        vhdx_hit = vhdx.get('available') and VHDX_PATH[:2].upper() in tight
        pf_hit = any(c['path'][:2].upper() in tight for c in pagefile.get('current', []))
        if vhdx_hit or pf_hit:
            return {'level': 'ABC', 'reason': why + '；检测到 VHDX/页面文件可 C 级处置'}
    return {'level': base, 'reason': why}


# ================= 执行器 =================
_tasks = {}
_tasks_lock = threading.Lock()
_exec_lock = threading.Lock()
TASK_TTL = 1800                                # 终态任务保留 30 分钟


def _prune_tasks():
    now = time.time()
    with _tasks_lock:
        for tid in [k for k, t in _tasks.items() if t['done'] and now - t.get('ended', 0) > TASK_TTL]:
            del _tasks[tid]


def _new_task(kind, b_paths, c_opts, dry_run):
    t = {'id': f'{int(time.time()*1000):x}{threading.get_ident() & 0xff:02x}',
         'kind': kind, 'phase': 'validate', 'started': time.time(), 'ended': 0,
         'items': [], 'c_progress': None, 'freed_bytes': 0,
         'done': False, 'ok': None, 'error': None,
         'b_paths': list(b_paths or []), 'c_opts': dict(c_opts or {}), 'dry_run': dry_run}
    with _tasks_lock:
        _tasks[t['id']] = t
        if len(_tasks) > 8:                    # 注册表上限：保留最近 8 个
            for tid in sorted(_tasks, key=lambda k: _tasks[k]['started'])[:-8]:
                if tid != t['id']:
                    del _tasks[tid]
    return t


def _titem(task, key, label, **kw):
    it = {'key': key, 'label': label, 'status': 'pending', 'freed_bytes': 0, 'err': ''}
    it.update(kw)
    task['items'].append(it)
    return it


def audit_append(level, key, path, action, freed, ok, err=''):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AUDIT_PATH, 'a', encoding='utf-8') as f:      # jsonl 不可篡改式追加
        f.write(json.dumps({'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'actor': 'dashboard',
                            'level': level, 'key': key, 'path': path, 'action': action,
                            'bytes_freed': freed, 'ok': ok, 'err': str(err)[:200]},
                           ensure_ascii=False) + '\n')


def _validate_src(path):
    """删除/移动源路径最终校验（TOCTOU 防线：扫描后路径可能已变化）。"""
    p = os.path.abspath(path)
    nc = os.path.normcase(p)
    if nc.startswith('\\\\'):
        raise RefusedError(f'拒绝 UNC 路径: {p}')
    if p[:2].lower() not in SRC_DRIVES:
        raise RefusedError(f'拒绝非 C/D 盘源路径: {p}')
    if _is_protected(p):
        raise RefusedError(f'保护名单路径，拒绝: {p}')
    if not os.path.exists(p):
        raise RefusedError(f'路径不存在: {p}')
    return p


def _drive_of(path):
    return os.path.abspath(path)[:2].lower()


def _free_bytes(drive):
    return psutil.disk_usage(drive + '\\').free


class Executor:
    @classmethod
    def run(cls, task):
        try:
            task['phase'] = 'a'
            if 'A' in task['kind']:
                cls._clean_a(task)
            task['phase'] = 'b'
            if 'B' in task['kind']:
                cls._clean_b(task)
            task['phase'] = 'c_bridge'
            if 'C' in task['kind']:
                cls._run_c_bridge(task)
            task['ok'] = not any(i['status'] == 'fail' for i in task['items'])
            if task['error'] and task['ok']:
                task['ok'] = False
        except Exception as e:
            task['ok'] = False
            task['error'] = str(e)[:300]
        finally:
            task['phase'] = 'done'
            task['done'] = True
            task['ended'] = time.time()
            task['freed_bytes'] = sum(i['freed_bytes'] for i in task['items'])
            _exec_lock.release()               # 与 start_clean 的 acquire 配对

    # ---------- A 级 ----------
    @classmethod
    def _clean_a(cls, task):
        snap = Scanner.snapshot()['data'] or {}
        by_key = {i['key']: i for i in snap.get('items', [])}
        for r in A_RULES:
            it = _titem(task, r['key'], r['label'])
            it['status'] = 'running'
            try:
                if r['kind'] == 'docker':
                    if not by_key.get(r['key'], {}).get('exists', False):
                        it.update(status='skip', err='Docker 不可用')
                        continue
                    freed = 0
                    for cmd in r['cmds']:
                        res = _run(cmd, timeout=300)
                        if res.returncode != 0:
                            raise RuntimeError(res.stderr.strip()[:120] or 'docker 命令失败')
                        m = re.search(r'Total reclaimed space:\s*([\d.]+)\s*([KMGT]?)B', res.stdout or '')
                        if m:
                            mult = {'': 1, 'K': 2**10, 'M': 2**20, 'G': 2**30, 'T': 2**40}[m.group(2)]
                            freed += int(float(m.group(1)) * mult)
                    it.update(status='done', freed_bytes=freed)
                    audit_append('A', r['key'], '', 'docker_prune', freed, True)
                    continue
                if not os.path.isdir(r['root']):
                    it.update(status='skip', err='目录不存在（可能已迁数据盘）')
                    continue
                d = _drive_of(r['root'])
                before = _free_bytes(d)
                if r['kind'] == 'whole':
                    shutil.rmtree(r['root'], onerror=lambda *a: None)   # 锁文件自动跳过
                    os.makedirs(r['root'], exist_ok=True)               # 缓存目录本体保留
                elif r['kind'] == 'age_tree':
                    cls._purge_old_children(r['root'], r['age_days'])
                elif r['kind'] == 'glob_age':
                    for name in os.listdir(r['root']):
                        if not re.fullmatch(r['pattern'].replace('*', '.*'), name):
                            continue
                        p = os.path.join(r['root'], name)
                        if Scanner._is_stale(p, r['age_days']) is True:
                            shutil.rmtree(p, onerror=lambda *a: None)
                freed = max(_free_bytes(d) - before, 0)
                it.update(status='done', freed_bytes=freed)
                audit_append('A', r['key'], r['root'], 'delete', freed, True)
            except Exception as e:
                it.update(status='fail', err=str(e)[:150])
                audit_append('A', r['key'], r.get('root', ''), 'delete', 0, False, e)

    @staticmethod
    def _purge_old_children(root, age_days):
        """age_tree：只删 root 下超龄子项。文件按自身 mtime；目录按 _is_stale 精筛。

        _is_stale 对目录做文件级 walk（防顶层 mtime 陷阱），在用目录（如正在写的
        会话输出）会被跳过；被锁文件由 rmtree onerror 吞掉。
        """
        cutoff = time.time() - age_days * 86400
        for name in os.listdir(root):
            p = os.path.join(root, name)
            try:
                if os.path.isdir(p):
                    if Scanner._is_stale(p, age_days) is True:
                        shutil.rmtree(p, onerror=lambda *a: None)
                elif os.lstat(p).st_mtime < cutoff:
                    os.remove(p)
            except OSError:
                pass

    # ---------- B 级 ----------
    @classmethod
    def _clean_b(cls, task):
        snap = Scanner.snapshot()['data'] or {}
        scanned = {os.path.normcase(i.get('path', '')) for i in snap.get('items', []) if i.get('level') == 'B'}
        # 前置：E 盘余量 ≥ Σ选中 + 5G
        total = 0
        for p in task['b_paths']:
            b, _f, _pt = Scanner._du(p, 15)
            total += b
        e_free = _free_bytes('e:')
        if e_free < total + 5 * 2**30:
            raise RefusedError(f'E 盘剩余 {e_free/2**30:.0f}G 不足（需 {total/2**30:.0f}G+5G），B 级整批拒绝')
        day = time.strftime('%Y%m%d')
        for path in task['b_paths']:
            name = os.path.basename(os.path.abspath(path).rstrip('\\'))
            it = _titem(task, 'b_' + name.lower()[:40], name, path=path)
            it['status'] = 'running'
            try:
                src = _validate_src(path)                   # 二次校验（TOCTOU 防线）
                if os.path.normcase(src) not in scanned:
                    raise RefusedError('不在本次扫描的 B 级清单内，拒绝')
                if Scanner._is_stale(src, B_DISCOVER['stale_days']) is not True:
                    raise RefusedError('执行前复检发现 30 天内有新文件，已跳过')
                b, _f, _pt = Scanner._du(src, 15)
                dst = os.path.join(TRASH_ROOT, day, name)
                i = 1
                while os.path.exists(dst):                  # 同日同名冲突加后缀
                    dst = os.path.join(TRASH_ROOT, day, f'{name}-{i}')
                    i += 1
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                # robocopy /MOVE：exit ≤7 为成功（robocopy 语义）；R:0 W:0 锁文件不重试
                res = _run(['robocopy', src, dst, '/E', '/MOVE', '/R:0', '/W:0', '/NFL', '/NDL', '/NP'], timeout=1800)
                if res.returncode > 7:
                    raise RuntimeError(f'robocopy exit={res.returncode}，源未清完（目标已有部分文件可手动处理）')
                try:
                    os.rmdir(src)                            # 源空了才删得掉，残留则保留
                except OSError:
                    pass
                it.update(status='done', freed_bytes=b)
                audit_append('B', it['key'], src, f'trash→{dst}', b, True)
            except Exception as e:
                it.update(status='fail', err=str(e)[:150])
                audit_append('B', it.get('key', ''), path, 'trash', 0, False, e)

    # ---------- C 级提权桥 ----------
    @classmethod
    def _run_c_bridge(cls, task):
        opts = task['c_opts']
        actions = [a for a in ('vhdx', 'pagefile') if opts.get(a)]
        if not actions:
            return
        it = _titem(task, 'c_bridge', 'C 级提权链（' + '+'.join(actions) + '）')
        it['status'] = 'running'
        try:
            os.makedirs(C_DIR, exist_ok=True)
            t0 = _mtime(C_RESULT)
            tmp = C_PARAMS + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:     # 原子写 params
                json.dump({'ts': time.time(), 'id': task['id'], 'dry_run': task['dry_run'],
                           'actions': actions,
                           'pagefile': {'target': opts.get('pagefile_target', 'E:'),
                                        'size_mb': int(opts.get('pagefile_size_mb') or 32768)}}, f)
            os.replace(tmp, C_PARAMS)
            r = _run(['schtasks', '/run', '/tn', C_TASK], timeout=15)
            if r.returncode != 0:
                raise RuntimeError(f'C 级桥未安装或触发失败: {r.stderr.strip()[:80]}'
                                   '（管理员运行 elevated\\install_disk_task.ps1 安装）')
            deadline = time.time() + C_BRIDGE_TIMEOUT
            while time.time() < deadline:
                time.sleep(2)
                if _mtime(C_PROGRESS) > t0:                 # 步骤级进度透传
                    try:
                        task['c_progress'] = _read_json(C_PROGRESS)
                    except Exception:
                        pass
                if _mtime(C_RESULT) > t0:
                    time.sleep(0.5)                          # 等写完
                    result = _read_json(C_RESULT)
                    task['c_progress'] = result
                    if not result.get('ok'):
                        raise RuntimeError(';'.join(result.get('errors') or ['C 级链失败'])[:200])
                    freed = 0
                    v = result.get('vhdx') or {}
                    if v.get('before_gib') and v.get('after_gib') is not None:
                        freed = int((v['before_gib'] - v['after_gib']) * 2**30)
                    it.update(status='done', freed_bytes=freed,
                              err=f"VHDX {v.get('before_gib','—')}→{v.get('after_gib','—')}GiB"
                                  f" · 容器恢复 {len(result.get('docker_recovered') or [])} 个"
                                  f" · pagefile {(result.get('pagefile') or {}).get('note') or '未执行'}")
                    audit_append('C', 'bridge', '', 'vhdx_compact+pagefile', freed, True)
                    return
            raise TimeoutError('C 级链超时（900s）——请人工检查 Docker 状态')
        except Exception as e:
            it.update(status='fail', err=str(e)[:200])
            audit_append('C', 'bridge', '', 'c_bridge', 0, False, e)


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def _read_json(p):
    with open(p, encoding='utf-8-sig') as f:
        return json.load(f)


# ================= 路由面（memclean_svc 调用） =================
def scan_snapshot(force=False):
    return Scanner.snapshot(force)


def task_status(task_id):
    _prune_tasks()
    with _tasks_lock:
        t = _tasks.get(task_id)
        if not t:
            return None
        out = {k: t[k] for k in ('id', 'kind', 'phase', 'started', 'done', 'ok', 'error',
                                 'items', 'c_progress', 'freed_bytes')}
        out['elapsed_ms'] = int(((t.get('ended') or time.time()) - t['started']) * 1000)
        return out


def start_clean(level, commit, b_paths, c_opts, actor='dashboard'):
    """dry-run（commit=false）同步返回计划；commit=true 起后台任务返回 task_id。

    level ∈ A | AB | ABC；含 B 必须 b_paths 非空且全部在扫描 B 清单内。
    """
    if level not in ('A', 'AB', 'ABC'):
        raise ValueError('level 必须为 A/AB/ABC')
    snap = Scanner.snapshot()['data']
    if not snap:
        raise RuntimeError('扫描数据未就绪，请先扫描')
    scanned_b = {os.path.normcase(i.get('path', '')) for i in snap.get('items', []) if i.get('level') == 'B'}
    if 'B' in level:
        for p in b_paths:                        # B 零勾选合法（只做 A+C 场景）；勾了必须在清单内
            if os.path.normcase(os.path.abspath(p)) not in scanned_b:
                raise ValueError(f'路径不在扫描 B 级清单内: {p}')
    plan, est = [], 0
    if 'A' in level:
        for i in snap['items']:
            if i.get('level') == 'A' and i.get('exists') and i.get('bytes', 0) >= 0 and i.get('bytes_human') != '不可用':
                plan.append({'key': i['key'], 'action': 'delete', 'bytes': i.get('bytes', 0),
                             'label': i['label']})
                est += i.get('bytes', 0)
    if 'B' in level:
        for p in b_paths:
            m = next((i for i in snap['items'] if i.get('level') == 'B'
                      and os.path.normcase(i.get('path', '')) == os.path.normcase(os.path.abspath(p))), None)
            if m:
                plan.append({'key': m['key'], 'action': f'trash→{TRASH_ROOT}\\<date>\\{m["label"]}',
                             'bytes': m['bytes'], 'label': m['label'], 'note': m.get('note', '')})
                est += m['bytes']
    warnings = [p['note'] for p in plan if p.get('note')]
    if 'C' in level:
        plan.append({'key': 'c_vhdx', 'action': 'VHDX 压缩（中断 Docker/WSL 5~10 分钟）', 'bytes': 0, 'label': 'C 级·VHDX'})
        plan.append({'key': 'c_pagefile', 'action': 'pagefile 迁移（注册表，重启生效）', 'bytes': 0, 'label': 'C 级·pagefile'})
    if not commit:
        return {'ok': True, 'dry_run': True, 'level': level, 'plan': plan,
                'est_freed_bytes': est, 'est_freed_human': _human(est), 'warnings': warnings}
    if not _exec_lock.acquire(blocking=False):
        raise BusyError('磁盘清理进行中，请稍候')
    try:
        task = _new_task(level, b_paths, c_opts, False)
        threading.Thread(target=Executor.run, args=(task,), daemon=True).start()
        return {'ok': True, 'task_id': task['id']}
    except Exception:
        _exec_lock.release()
        raise
