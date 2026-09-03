import { createServer } from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../dist/", import.meta.url));
const contentTypes = {
  ".css": "text/css",
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".txt": "text/plain",
  ".webmanifest": "application/manifest+json",
  ".woff2": "font/woff2",
};

const server = createServer((request, response) => {
  if (request.url === "/api/auth/me/") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ user: null, authenticated: false }));
    return;
  }

  const requested = normalize(decodeURIComponent((request.url ?? "/").split("?")[0]));
  const relative = requested === "/" ? "/index.html" : requested;
  const file = join(root, relative);
  if (!file.startsWith(root) || !existsSync(file) || !statSync(file).isFile()) {
    response.writeHead(404);
    response.end("not found");
    return;
  }

  response.writeHead(200, {
    "content-type": contentTypes[extname(file)] ?? "application/octet-stream",
  });
  createReadStream(file).pipe(response);
});

server.listen(4173, "127.0.0.1", () => {
  console.log("lighthouse server listening on 4173");
});
