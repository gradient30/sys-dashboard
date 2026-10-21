// /cleanup 端点回归（审计 AUDIT-2026-10-21 P0-1 的回归防线）
// 断言：HTTP 200 + JSON ok/engine/释放量字段 + 日志落盘——修复前该路径 100% 断连（curl 52）
// 注意：会真实触发一次内存清理（EmptyWorkingSet 修剪 / memreduct 桥），与页面按钮行为一致
// 前置：memclean_svc 已在 61210 在线
const http = require('http');

const ok = (cond, name) => { console.log(`${cond ? '✓' : '✗ FAIL'}  ${name}`); if (!cond) process.exitCode = 1; };

const TOKEN = 'Glc-2026-mc-7f3a91';   // 与 web/index.html、web/clean.html 三处同步（CLAUDE.md）

const post = (path) => new Promise((res, rej) => {
  const r = http.request({ host: '127.0.0.1', port: 61210, path, method: 'POST', timeout: 180000 },
    resp => { let b = ''; resp.on('data', c => b += c); resp.on('end', () => res({ code: resp.statusCode, body: b })); });
  r.on('timeout', () => { r.destroy(); rej(new Error('timeout')); });
  r.on('error', rej);
  r.end();
});
const get = (path) => new Promise((res, rej) => {
  http.get({ host: '127.0.0.1', port: 61210, path, timeout: 10000 },
    resp => { let b = ''; resp.on('data', c => b += c); resp.on('end', () => res({ code: resp.statusCode, body: b })); })
    .on('error', rej);
});

(async () => {
  const ping = await get('/ping');
  ok(ping.code === 200, `前置：服务在线 /ping 200`);

  const sizeBefore = Number(require('child_process').execSync(
    `powershell -NoProfile -Command "(Get-Item $env:TEMP\\memclean_svc.log -EA SilentlyContinue).Length"`).toString().trim() || 0);

  let resp;
  try { resp = await post(`/cleanup/${TOKEN}`); }
  catch (e) { ok(false, `POST /cleanup 连接层失败（${e.message}）——审计 P0-1 复现形态`); process.exit(1); }

  ok(resp.code === 200, `组1 HTTP 200（修复前：断连 curl 52，实际=${resp.code}）`);
  let d = null;
  try { d = JSON.parse(resp.body); } catch { ok(false, `组2 响应为 JSON（实际前 60 字符：${resp.body.slice(0, 60)}）`); }
  if (d) {
    ok(d.ok === true, '组2 JSON ok=true');
    ok(d.engine === 'fallback' || d.engine === 'memreduct', `组3 engine 字段（=${d.engine}，可区分降级）`);
    if (d.engine === 'fallback') {
      ok(typeof d.degraded_reason === 'string', '组4 降级原因透传 degraded_reason 存在');
      ok([d.before_gib, d.after_gib, d.freed_gib].every(v => typeof v === 'number'), `组5 释放量数值（${d.before_gib}→${d.after_gib} GiB，Δ${d.freed_gib}）`);
    } else {
      ok(d.regions != null, '组5 memreduct 引擎 regions 字段存在');
    }
  }

  const sizeAfter = Number(require('child_process').execSync(
    `powershell -NoProfile -Command "(Get-Item $env:TEMP\\memclean_svc.log -EA SilentlyContinue).Length"`).toString().trim() || 0);
  ok(sizeAfter > sizeBefore, `组6 日志落盘（${sizeBefore} → ${sizeAfter} 字节，修复前恒 0 增长）`);

  console.log(process.exitCode ? '存在失败项' : '/cleanup 端点 6 组验证全部通过');
  process.exit(process.exitCode || 0);
})();
