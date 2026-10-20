// 验证：① 清理后 10 分钟内 MEMSWAP 告警降级为"属预期" ② 窗口外恢复正常分档
const { chromium } = require('./node_modules/playwright');

(async () => {
  const exe = process.env.LOCALAPPDATA + '/ms-playwright/chromium-1234/chrome-win64/chrome.exe';
  const fs = require('fs');
  const browser = await chromium.launch(fs.existsSync(exe) ? { executablePath: exe } : {});
  const page = await browser.newPage();
  await page.goto('http://localhost:61210/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  const r = await page.evaluate(async () => {
    const fakeAlert = { type: 'MEMSWAP', state: 'CRITICAL', avg: 88.2, max: 93.0, begin: Math.floor(Date.now() / 1000) - 120, end: null };
    LAST.alert = [fakeAlert];
    const read = () => document.querySelector('#alert-panel .concl')?.textContent || '(无)';
    // ① 窗口内：设 cleanLast = 刚刚
    cleanLast = Date.now();
    renderAlerts();
    document.querySelector('#alert-panel').classList.add('open');
    const inWindow = read();
    // ② 窗口外：cleanLast = 11 分钟前
    cleanLast = Date.now() - 11 * 60000;
    renderAlerts();
    const outWindow = read();
    LAST.alert = [];                                    // 复原
    return { inWindow, outWindow };
  });
  console.log('清理后 10 分钟内 :', JSON.stringify(r.inWindow));
  console.log('窗口外（正常分档）:', JSON.stringify(r.outWindow));
  const pass = /清理后换页/.test(r.inWindow) && /换页严重/.test(r.outWindow);
  console.log(pass ? 'PASS' : 'FAIL');
  await browser.close();
  process.exit(pass ? 0 : 1);
})();
