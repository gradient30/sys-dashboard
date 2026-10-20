// 长窗稳定性复核：3 分钟，每 30s 采 heap（CDP）+ addEventListener 增量
const { chromium } = require('./node_modules/playwright');

(async () => {
  const exe = process.env.LOCALAPPDATA + '/ms-playwright/chromium-1234/chrome-win64/chrome.exe';
  const fs = require('fs');
  const browser = await chromium.launch(fs.existsSync(exe) ? { executablePath: exe } : {});
  const page = await browser.newPage();
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  await page.addInitScript(() => {
    window.__t0 = Date.now();
    window.__ae = [];
    const o = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, ...rest) {
      const line = (new Error().stack.split('\n')[2] || '').trim().replace(/^at\s+/, '').slice(0, 120);
      window.__ae.push({ t: Date.now() - window.__t0, type, line });
      return o.call(this, type, ...rest);
    };
  });
  await page.goto('http://localhost:61210/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000);
  const samples = [];
  let lastAe = 0;
  for (let i = 0; i <= 6; i++) {
    const m = await cdp.send('Performance.getMetrics');
    const heap = m.metrics.find(x => x.name === 'JSHeapUsedSize').value / 1048576;
    const nodes = m.metrics.find(x => x.name === 'Nodes').value;
    const docs = m.metrics.find(x => x.name === 'Documents').value;
    const ae = await page.evaluate(() => window.__ae.length);
    samples.push({ t: i * 30, heap: +heap.toFixed(1), nodes, docs, aeDelta: ae - lastAe });
    lastAe = ae;
    if (i < 6) await page.waitForTimeout(30000);
  }
  console.log('时刻(s) | heap(MB) | Nodes | Docs | 本窗新增绑定');
  samples.forEach(s => console.log(
    String(s.t).padStart(5) + 's | ' + String(s.heap).padStart(6) + ' | ' +
    String(s.nodes).padStart(5) + ' | ' + String(s.docs).padStart(4) + ' | ' + s.aeDelta));
  await browser.close();
})();
