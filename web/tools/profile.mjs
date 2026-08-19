#!/usr/bin/env node
// Measures real rAF frame pacing of web/index.html's live render loop (not a
// synthetic microbenchmark) -- registers our own rAF callback alongside the
// app's, which reveals the app loop's real per-frame cost via how late our
// own callback fires each frame.
import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const indexPath = path.join(__dirname, "..", "index.html");

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (err) => console.error("[page exception]", err.message));

  // Headless tabs throttle real rAF hard (a known confound already
  // documented for this project's rotating-camera export work), so
  // measuring wall-clock rAF cadence in headless Chromium doesn't reflect
  // the loop's actual CPU cost. Instead: intercept the app's very first
  // requestAnimationFrame registration (its `loop` callback) before the
  // page's own script runs, then drive it manually in a tight synchronous
  // loop with performance.now() around each call -- a true CPU-bound
  // measurement independent of display-refresh scheduling.
  await page.addInitScript(() => {
    window.__capturedLoop = null;
    const realRAF = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = function (cb) {
      if (!window.__capturedLoop) { window.__capturedLoop = cb; return 1; }
      return realRAF(cb);
    };
  });

  await page.goto("file://" + indexPath);
  await page.waitForFunction(() => document.getElementById("graph") != null);
  await page.waitForFunction(() => window.__capturedLoop != null);
  await page.waitForTimeout(200);

  const result = await page.evaluate(() => {
    const loop = window.__capturedLoop;
    const times = [];
    for (let i = 0; i < 20; i++) {
      const t0 = performance.now();
      loop();
      times.push(performance.now() - t0);
    }
    const avg = times.reduce((a, b) => a + b, 0) / times.length;
    const max = Math.max(...times);
    const sorted = [...times].sort((a, b) => a - b);
    return { times, avg, max, p50: sorted[Math.floor(sorted.length * 0.5)] };
  });
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
}
