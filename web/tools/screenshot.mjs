#!/usr/bin/env node
// General-purpose visual-check harness for web/index.html: loads the built
// page in a real headless Chromium (via Playwright), optionally runs a
// snippet of page JS first (click a button, wait on scene3dReady, orbit the
// camera, ...), then saves a screenshot. Replaces the old "ask a human to
// look in a real browser" step for anything that just needs a rendered
// pixel check -- see NOTES.md for why that used to be the fallback.
//
// Usage: node tools/screenshot.mjs [outPath] [--eval "page JS to run before capture"]
import { chromium } from "playwright";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const evalIdx = args.indexOf("--eval");
const evalScript = evalIdx >= 0 ? args[evalIdx + 1] : null;
const outPath = path.resolve(args[0] && args[0] !== "--eval" ? args[0] : "screenshot.png");

const indexPath = path.join(__dirname, "..", "index.html");

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("console", (msg) => { if (msg.type() === "error") console.error("[page error]", msg.text()); });
  page.on("pageerror", (err) => console.error("[page exception]", err.message));

  await page.goto("file://" + indexPath);
  // Force layout is warmed up synchronously before first paint (see
  // template.html's `for (var warm = 0; warm < 160; warm++) tick();`), so
  // there's no async "ready" event to wait on beyond the WebGL canvas
  // existing and the app's own init running to completion.
  await page.waitForFunction(() => document.getElementById("graph") != null);
  await page.waitForTimeout(300); // let the first real rAF frame(s) actually paint

  if (evalScript) await page.evaluate(evalScript);

  await page.screenshot({ path: outPath });
  console.log("wrote " + outPath);
} finally {
  await browser.close();
}
