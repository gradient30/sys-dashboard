// 精确归因：hook addEventListener 记录调用来源，60s 后分组统计
const { chromium } = require('playwright');

(async () => {
  const exe = process.env.LOCALAPPDATA + '/ms-playwright/chromium-1234/chrome-win64/chrome.exe';
  const fs = require('fs');
  const browser = await chromium.launch(fs.existsSync(exe) ? { executablePath: exe } : {});
  const page = await browser.newPage();
  await page.addInitScript(() => {
    window.__ae = [];
    const o = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, ...rest) {
      const line = (new Error().stack.split('\n')[2] || '').trim().replace(/^at\s+/, '').slice(0, 150);
      window.__ae.push(type + ' ⇐ ' + line);
      return o.call(this, type, ...rest);
    };
  });
  await page.goto('http://localhost:61210/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
  const before = await page.evaluate(() => window.__ae.length);
  await page.waitForTimeout(60000);
  const after = await page.evaluate(() => window.__ae.length);
  const groups = await page.evaluate(() => {
    const m = {};
    for (const s of window.__ae) m[s] = (m[s] || 0) + 1;
    return Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, 15);
  });
  console.log(`60s 内新增 addEventListener: ${after - before} 次（总 ${after}）\n=== 来源分组 TOP ===`);
  groups.forEach(([k, v]) => console.log(`${String(v).padStart(4)} × ${k}`));
  await browser.close();
})();
