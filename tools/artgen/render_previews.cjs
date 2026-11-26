// SVG 도안을 PNG 미리보기로 변환: node tools/artgen/render_previews.cjs <artwork_dir> [scale]
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
(async () => {
  const dir = path.resolve(process.argv[2]);
  const scale = Number(process.argv[3] || 1);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1000, height: 1000 }, deviceScaleFactor: scale });
  for (const name of ['color', 'numbered', 'progress', 'lineart']) {
    const f = path.join(dir, name + '.svg');
    if (!fs.existsSync(f)) continue;
    await page.goto('file://' + f);
    await page.screenshot({ path: path.join(dir, 'preview_' + name + '.png') });
  }
  await browser.close();
})();
