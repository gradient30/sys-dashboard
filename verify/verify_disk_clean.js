// /clean 磁盘清理控制台端到端验证（13 组断言，全程零写盘：dry-run 允许、commit=true 计数必须为 0）
// 用法：node verify_disk_clean.js           全量（含第 12 组大屏收口，须在 index.html 收口后跑）
//       node verify_disk_clean.js --no-dash 跳过第 12 组（大屏收口完成前的过渡期）
// 前置：memclean_svc 已在 61210 在线；触发一次真实重扫（fresh=1），组 1/3 依赖直播窗口
const { chromium } = require('./node_modules/playwright');
const fs = require('fs');
const http = require('http');
const NO_DASH = process.argv.includes('--no-dash');

const ok = (cond, name) => { console.log(`${cond ? '✓' : '✗ FAIL'}  ${name}`); if (!cond) process.exitCode = 1; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

const getScan = qs => new Promise((res, rej) => {
  http.get(`http://localhost:61210/disk/scan${qs || ''}`, r => {
    let b = ''; r.on('data', c => b += c);
    r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
  }).on('error', rej);
});

// 组1：fresh 触发重扫 → 观察 progress.phase 推进 → 终态 ready（partial 态存在时顺带验单调）
async function watchRescan() {
  await new Promise((res, rej) => http.get(
    'http://localhost:61210/disk/scan?fresh=1',
    r => { r.resume(); r.on('end', res); }
  ).on('error', rej));
  const phases = [];
  let partialSeen = false, monotonic = true, lastPartial = -1, final = null;
  for (let i = 0; i < 100; i++) {                 // 100×2s = 200s 上限（B 级发现偶尔超 90s）
    await sleep(2000);
    const d = await getScan();
    phases.push(d.progress && d.progress.phase);
    if (d.partial) {
      partialSeen = true;
      const n = (d.items || []).length;
      if (lastPartial >= 0 && n < lastPartial) monotonic = false;   // append-only：只增不减
      lastPartial = n;
    }
    if (d.state === 'ready' && d.progress && d.progress.phase === 'ready') { final = d; break; }
  }
  return { phases, partialSeen, monotonic, final };
}

(async () => {
  const local1234 = `${process.env.LOCALAPPDATA}\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe`;
  const browser = await chromium.launch(fs.existsSync(local1234) ? { executablePath: local1234 } : {});
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  let commits = 0, dryruns = 0;
  page.on('request', r => {
    if (r.url().includes('/disk/clean')) (r.postData() || '').includes('"commit":true') ? commits++ : dryruns++;
  });

  // fresh 触发重扫（先于开页，保证页面 init 即见直播窗口）
  const watch = watchRescan();
  await page.goto('http://localhost:61210/clean', { waitUntil: 'domcontentloaded' });

  // ---- 组3：s1 直播（步骤清单 + 水位先行 + feed） ----
  await page.waitForTimeout(2500);
  const live = (await page.locator('#s1.on').count()) === 1;
  ok(live, '组3 重扫直播：页面停留 s1');
  if (live) {
    const nSteps = await page.locator('#sc-steps .s').count();
    ok(nSteps >= 5, `组3 五阶段步骤清单渲染（${nSteps} 步）`);
    const nFs = await page.locator('#sc-fs .fsrow').count();
    ok(nFs >= 3, `组3 盘符水位先行渲染（${nFs} 行）`);
    const nFeed = await page.locator('#sc-feed .f').count();
    ok(nFeed >= 1, `组3 发现 feed 渲染（${nFeed} 行）`);
    ok(await page.locator('button[data-act="rescan"]').isDisabled(), '组3 分析中 rescan 按钮 disabled');
    ok((await page.locator('button[data-act="to2"]').count()) === 1, '组3 重扫期间「使用现有结果」可直达');
  }

  // ---- 组2：ready 自动落 step2 + stepper ----
  await page.waitForSelector('#s2.on', { timeout: 150000 });
  ok(true, '组2 重扫完成后自动落 step2（审阅）');
  ok((await page.locator('#stepper li').count()) === 5, '组2 stepper 五节点');
  await page.waitForTimeout(600);

  // ---- 组1：协议增量断言（watch 与页面并行观察同一轮重扫） ----
  const seen = await watch;
  ok(seen.phases.some(p => p && p !== 'ready' && p !== 'idle'), `组1 重扫 phase 推进可观察（${[...new Set(seen.phases)].join('→')}）`);
  ok(!!seen.final, '组1 终态回到 ready');
  if (seen.partialSeen) {
    ok(seen.monotonic, '组1 partial.items append-only（单调不减）');
    console.log('  （本轮观察到无缓存 scanning 态：partial:true 已透传）');
  } else {
    console.log('  （有旧缓存：state 恒 ready，partial 不透传——符合协议，单调性由服务端 _publish 保证）');
  }
  if (seen.final) {
    const steps = (seen.final.progress || {}).steps || [];
    ok(seen.final.partial === undefined, '组1 终态无 partial 键');
    ok((seen.final.progress || {}).pct === 100, '组1 终态 pct=100');
    ok(steps.length === 5 && steps.every(s => s.state === 'done'), '组1 终态五阶段全 done');
    ok((seen.final.items || []).length >= 10, `组1 items ≥10（实际 ${(seen.final.items || []).length}）`);
  }

  // ---- 组4：审阅分组（A/B/C 三卡 + 占比条） ----
  const cards = await page.locator('#rv-body .stcard').count();
  ok(cards === 3, `组4 A/B/C 三张分组卡（实际 ${cards}）`);
  const tracks = await page.locator('#rv-body .track').count();
  ok(tracks >= 3, `组4 占比条渲染（${tracks} 条）`);
  const aRows = await page.locator('#rv-body .stcard').first().locator('.row').count();
  ok(aRows >= 5, `组4 A 组行数 ≥5（实际 ${aRows}）`);

  // ---- 组5：B 勾选实时汇总 ----
  const bRow = page.locator('[data-act="btoggle"]').first();
  if ((await bRow.count()) && (await bRow.isVisible())) {
    const head = page.locator('#rv-body .stcard').nth(1).locator('.cardhead .sum');
    const before = await head.innerText();
    await bRow.click();
    await page.waitForTimeout(250);
    const after = await head.innerText();
    ok(before !== after && /已选 [1-9]/.test(after), `组5 B 勾选组头汇总变化（${before.trim()} → ${after.trim()}）`);
    await bRow.click();
    await page.waitForTimeout(250);
  } else ok(false, '组5 B 行可点选（未找到可见 btoggle 行）');

  // ---- 组6：排序切换（整组行序签名变化；首行在 zh locale 下可能巧合不变） ----
  const orderSig = () => page.locator('#rv-body .stcard').first().locator('.row .nm').allInnerTexts().then(a => a.map(s => s.trim()).join('|'));
  const sigBytes = await orderSig();
  await page.click('button[data-act="sort"][data-v="label"]');
  await page.waitForTimeout(300);
  const sigLabel = await orderSig();
  ok(sigBytes !== sigLabel, `组6 排序切换行序变化（${sigBytes.split('|')[0].trim()} → ${sigLabel.split('|')[0].trim()} 为首）`);
  await page.click('button[data-act="sort"][data-v="bytes"]');
  await page.waitForTimeout(300);

  // ---- 组7：级别卡（默认选中=建议级 + ⌂ 标恰 1） ----
  await page.click('[data-act="to3"]');
  await page.waitForSelector('#s3.on');
  ok((await page.locator('#lv-grid .lvcard').count()) === 3, '组7 三张级别卡');
  const nRec = await page.locator('.lvcard .rec').count();
  ok(nRec === 1, `组7 ⌂ 建议标恰好 1 张（实际 ${nRec}）`);
  const onV = await page.locator('.lvcard.on').getAttribute('data-v');
  const recV = await page.locator('.lvcard:has(.rec)').getAttribute('data-v');
  ok(onV === recV && !!onV, `组7 默认选中=建议级（${onV}）`);

  // ---- 组8：C 子项（ABC 显示 + 可切换） ----
  await page.click('.lvcard[data-v="ABC"]');
  await page.waitForTimeout(300);
  ok(await page.locator('#c-box').isVisible(), '组8 ABC 时 C 子项卡可见');
  const cRows = await page.locator('#c-items [data-act="ctoggle"]').count();
  ok(cRows >= 2, `组8 C 子项行 ≥2（vhdx/pagefile，实际 ${cRows}）`);

  // ---- 组9：dry-run 计划 ----
  await page.click('[data-act="preview"]');
  await page.waitForSelector('#plan-slot .planbox', { timeout: 20000 });
  const est = await page.locator('.planbox .est').innerText();
  ok(/预计释放/.test(est), `组9 dry-run 计划渲染（${est.trim().slice(0, 36)}）`);
  const prows = await page.locator('.planbox .prow').count();
  ok(prows >= 5, `组9 计划明细 ≥5 行（实际 ${prows}）`);

  // ---- 组10：C 级模态门禁（不真执行） ----
  await page.click('[data-act="commit"]');
  await page.waitForSelector('#modal.open', { timeout: 5000 });
  ok(true, '组10 ABC 提交弹出中断声明模态');
  ok(await page.locator('#ack-go').isDisabled(), '组10 未勾声明确认键 disabled');
  await page.check('#ack-cb');
  ok(!(await page.locator('#ack-go').isDisabled()), '组10 勾选后确认键 enabled');
  await page.click('#ack-back');
  await page.waitForTimeout(300);
  ok((await page.locator('#modal.open').count()) === 0, '组10 返回关闭模态不执行');

  // ---- 组11：零写盘 ----
  ok(commits === 0 && dryruns === 1, `组11 零写盘（commit:true=${commits} / dry-run=${dryruns}）`);

  // ---- 组12：大屏收口（--no-dash 跳过） ----
  if (!NO_DASH) {
    const dp = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const derrs = [];
    dp.on('pageerror', e => derrs.push(e.message));
    await dp.goto('http://localhost:61210/', { waitUntil: 'domcontentloaded' });
    await dp.waitForTimeout(1500);
    ok((await dp.locator('button[data-tab="clean"]').count()) === 0, '组12 大屏 clean 页签已移除');
    ok((await dp.locator('#dc-console').count()) === 1, '组12 清理控制台入口链接存在');
    ok((await dp.locator('#btn-disk').count()) === 1 && /\/clean$/.test(await dp.locator('#btn-disk').getAttribute('href')), '组12 header 磁盘清理按钮存在且指向 /clean');
    ok((await dp.locator('#p-clean, #dc-main').count()) === 0, '组12 旧清理面板 DOM 不存在');
    ok(derrs.length === 0, '组12 大屏零 JS 错误' + (derrs.length ? '：' + derrs.join(';;') : ''));
    await dp.close();
  } else console.log('  （--no-dash：跳过组12 大屏收口断言）');

  // ---- 组13：移动端 375px ----
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('http://localhost:61210/clean', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  ok(overflow <= 1, `组13 375px 无横向溢出（差 ${overflow}px）`);
  const btnH = await page.locator('#actbar .btn').first().evaluate(el => el.getBoundingClientRect().height);
  ok(btnH >= 40, `组13 移动端按钮高 ≥40px（实际 ${btnH.toFixed(0)}）`);

  ok(errs.length === 0, '全程零 JS 错误' + (errs.length ? '：' + errs.join(' ;; ') : ''));
  await browser.close();
  console.log(process.exitCode ? '存在失败项' : '磁盘清理控制台（/clean）13 组验证全部通过');
  process.exit(process.exitCode || 0);
})();
