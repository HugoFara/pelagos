#!/usr/bin/env node
// V8 CPU sampling profile of several loop() calls, to find which function
// actually eats the ~450-1000ms/call measured by profile.mjs -- much more
// precise than guessing from reading the source.
import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const indexPath = path.join(__dirname, "..", "index.html");

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (err) => console.error("[page exception]", err.message));

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

  const client = await page.context().newCDPSession(page);
  await client.send("Profiler.enable");
  await client.send("Profiler.setSamplingInterval", { interval: 100 }); // microseconds
  await client.send("Profiler.start");

  await page.evaluate(() => {
    const loop = window.__capturedLoop;
    for (let i = 0; i < 6; i++) loop();
  });

  const { profile } = await client.send("Profiler.stop");

  // Aggregate self-time per function (nodeId -> hitCount * samplingInterval)
  const nodeById = new Map(profile.nodes.map((n) => [n.id, n]));
  const selfTime = new Map();
  for (const n of profile.nodes) {
    const key = `${n.callFrame.functionName || "(anonymous)"} @ ${n.callFrame.url.split("/").pop()}:${n.callFrame.lineNumber + 1}`;
    selfTime.set(key, (selfTime.get(key) || 0) + (n.hitCount || 0));
  }
  const totalHits = [...selfTime.values()].reduce((a, b) => a + b, 0);
  const ranked = [...selfTime.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25);
  const intervalMs = 100 / 1000;
  console.log("total sampled time (ms):", (totalHits * intervalMs).toFixed(1));
  ranked.forEach(([key, hits]) => {
    console.log(`${(hits * intervalMs).toFixed(1).padStart(8)}ms  ${(100 * hits / totalHits).toFixed(1).padStart(5)}%  ${key}`);
  });
} finally {
  await browser.close();
}
