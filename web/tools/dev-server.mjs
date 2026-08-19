#!/usr/bin/env node
// Local dev server for web/: rebuilds scene3d.bundle.js on every src/
// change (esbuild's own watch API) and live-reloads the browser tab when
// that rebuild finishes, via a small SSE channel. No new dependencies --
// esbuild is already a devDependency and the server itself is plain
// node:http. The live-reload <script> is injected only into the response
// this server sends, never written to the committed web/index.html.
//
// Builds unminified to scene3d.dev.bundle.js (gitignored), NOT the
// committed scene3d.bundle.js: that file is esbuild's --minify output,
// checked in the same way web/index.html is, and is ~7x smaller than an
// unminified dev build. Overwriting it here used to dirty every dev
// session's git status with a ~20k-line diff for no real change. The HTML
// response instead gets its <script src="scene3d.bundle.js"> rewritten to
// point at the dev bundle in-flight, same "never written to disk" trick
// the live-reload snippet already uses below.
import esbuild from "esbuild";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..");
const PORT = process.env.PORT ? Number(process.env.PORT) : 8000;
const DEV_BUNDLE_NAME = "scene3d.dev.bundle.js";

let reloadClients = [];
function broadcastReload() {
  for (const res of reloadClients) res.write("data: reload\n\n");
  reloadClients = [];
}

const ctx = await esbuild.context({
  entryPoints: [path.join(root, "src/scene3d.js")],
  bundle: true,
  format: "iife",
  globalName: "Scene3D",
  outfile: path.join(root, DEV_BUNDLE_NAME),
  plugins: [
    {
      name: "reload-on-rebuild",
      setup(build) {
        let first = true;
        build.onEnd((result) => {
          // Skip the initial build -- nothing is listening for reload yet,
          // and the server hasn't started serving pages.
          if (first) {
            first = false;
            return;
          }
          if (result.errors.length === 0) broadcastReload();
        });
      },
    },
  ],
});
await ctx.watch();

const LIVE_RELOAD_SNIPPET = `
<script>
(function () {
  var es = new EventSource("/__reload");
  es.onmessage = function () { location.reload(); };
})();
</script>
`;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".css": "text/css; charset=utf-8",
};

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");

  if (url.pathname === "/__reload") {
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    res.write("\n");
    reloadClients.push(res);
    req.on("close", () => {
      reloadClients = reloadClients.filter((c) => c !== res);
    });
    return;
  }

  const reqPath = url.pathname === "/" ? "/index.html" : url.pathname;
  const filePath = path.join(root, path.normalize(decodeURIComponent(reqPath)));
  if (path.relative(root, filePath).startsWith("..")) {
    res.writeHead(403);
    res.end("forbidden");
    return;
  }

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end("not found");
      return;
    }
    const contentType = MIME[path.extname(filePath)] || "application/octet-stream";
    if (contentType.startsWith("text/html")) {
      const html = data.toString("utf8").replace("scene3d.bundle.js", DEV_BUNDLE_NAME);
      res.writeHead(200, { "Content-Type": contentType });
      res.end(html + LIVE_RELOAD_SNIPPET);
    } else {
      res.writeHead(200, { "Content-Type": contentType });
      res.end(data);
    }
  });
});

server.listen(PORT, () => {
  const url = `http://localhost:${PORT}`;
  console.log(`Pelagos dev server: ${url}`);
  console.log(`watching src/scene3d.js -> ${DEV_BUNDLE_NAME} (gitignored, dev-only), live-reloading on rebuild`);

  const opener = process.platform === "darwin" ? "open" : process.platform === "win32" ? "start" : "xdg-open";
  spawn(opener, [url], { stdio: "ignore", detached: true, shell: process.platform === "win32" }).unref();
});
