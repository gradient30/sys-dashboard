// hbar 复用修复验证：切到「连接」tab，观察 35s 绑定增量 + 图表渲染 + 页面报错
const { chromium } = require('./node_modules/playwright');

(async () => {
  const exe = process.env.LOCALAPPDATA + '/ms-playwright/chromium-1234/chrome-win64/chrome.exe';
  const fs = require('fs');
  const browser = await chromium.launch(fs.existsSync(exe) ? { executablePath: exe } : {});
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.addInitScript(() => {
    window.__ae = [];
    const o = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, ...rest) {
      const line = (new Error().stack.split('\n')[2] || '').trim().slice(0, 100);
      window.__ae.push(type + ' ⇐ ' + line);
      return o.call(this, type, ...rest);
    };
  });
  await page.goto('http://localhost:61210/', { waitUntil: 'domcontentloaded' });
  await page.click('button[data-tab="conns"]');
  await page.waitForTimeout(5000);            // 首帧 + 稳定
  const before = await page.evaluate(() => window.__ae.length);
  await page.waitForTimeout(35000);           // 35s：修复前 hbar 每 3s init ≈ +105
  const after = await page.evaluate(() => window.__ae.length);
  const state = await page.evaluate(() => {
    const el = document.querySelector('#conns-chart');
    return {
      hasEchart: !!el.querySelector('.echart'),
      bars: el.querySelectorAll('rect').length,
      panelOn: document.querySelector('#p-conns').classList.contains('on'),
    };
  });
  const delta = await page.evaluate(n => window.__ae.slice(n).reduce((m, s) => (m[s] = (m[s] || 0) + 1, m), {}), before);
  console.log(`conns tab 35s 新增 addEventListener: ${after - before} 次（修复前预期 ~105）`);
  console.log('图表状态:', JSON.stringify(state));
  console.log('页面错误:', errs.length ? errs.join('\n') : '无');
  await browser.close();
})();
