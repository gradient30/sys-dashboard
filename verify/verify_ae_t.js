// 精确归因 v2：记录 addEventListener 的时刻/类型/来源，判断剩余绑定是周期性还是一次性
const { chromium } = require('./node_modules/playwright');

(async () => {
  const exe = process.env.LOCALAPPDATA + '/ms-playwright/chromium-1234/chrome-win64/chrome.exe';
  const fs = require('fs');
  const browser = await chromium.launch(fs.existsSync(exe) ? { executablePath: exe } : {});
  const page = await browser.newPage();
  await page.addInitScript(() => {
    window.__t0 = Date.now();
    window.__ae = [];
    const o = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, ...rest) {
      const line = (new Error().stack.split('\n')[2] || '').trim().replace(/^at\s+/, '').slice(0, 160);
      const tgt = (this && (this.id || this.className || this.tagName) || '?').toString().slice(0, 40);
      window.__ae.push({ t: Date.now() - window.__t0, type, line, tgt });
      return o.call(this, type, ...rest);
    };
  });
  await page.goto('http://localhost:61210/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000);          // 页面稳定基线
  const before = await page.evaluate(() => window.__ae.length);
  await page.waitForTimeout(45000);         // 观察 45s
  const evs = await page.evaluate((n) => window.__ae.slice(n), before);
  console.log(`45s 窗口新增 addEventListener: ${evs.length} 次`);
  if (evs.length) {
    const m = {};
    for (const e of evs) { const k = `${e.type} ⇐ ${e.line}`; (m[k] = m[k] || []).push(e.t / 1000); }
    Object.entries(m).sort((a, b) => b[1].length - a[1].length).forEach(([k, ts]) =>
      console.log(`${String(ts.length).padStart(3)} × [${ts.map(t => t.toFixed(0) + 's').join(', ')}] ${k}`));
  }
  // 顺带：heap 快照对照（起点/中点/终点，看是否单调增长）
  const heap = await page.evaluate(() => performance.memory ? {
    used: performance.memory.usedJSHeapSize, mb: (performance.memory.usedJSHeapSize / 1048576).toFixed(1),
  } : null);
  console.log('窗口末 heap:', heap ? heap.mb + ' MB' : 'performance.memory 不可用');
  await browser.close();
})();
