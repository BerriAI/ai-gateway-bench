// Renders the SVGs make_charts.py wrote to PNG at 2x, with IBM Plex Sans and JetBrains Mono.
//   node analyze/better_benchmarks/render.js
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

(async () => {
  const browser = await chromium.launch();
  for (const file of fs.readdirSync(__dirname).filter((f) => f.endsWith(".svg"))) {
    const svg = fs.readFileSync(path.join(__dirname, file), "utf8");
    const [, w, h] = svg.match(/width="(\d+)" height="(\d+)"/);
    const page = await browser.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 2 });
    await page.setContent(
      `<html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap"><style>body{margin:0}</style></head><body>${svg}</body></html>`,
    );
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(__dirname, file.replace(/\.svg$/, ".png")) });
    await page.close();
  }
  await browser.close();
})();
